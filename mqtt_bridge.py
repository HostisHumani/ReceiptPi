"""
MQTT bridge: print commands in, printer status out, optional Home
Assistant discovery. Configured on /settings/mqtt (settings_store
section "mqtt").

Nothing runs unless MQTT is enabled and configured: start() then
returns without importing paho-mqtt or starting a thread. When enabled,
it runs inside the single Gunicorn worker (started from app.py, after
the fork - never in the master process), as three daemon threads:
paho's network loop, a command dispatcher and the status monitor.

Topics (<p> = configured prefix, default "receiptpi"):
  <p>/availability   "online"/"offline", retained; "offline" is also
                     the last will, so a lost connection shows up too
  <p>/state          JSON, retained, published only when it changes
  <p>/cmd/<command>  incoming print commands (COMMANDS below)
  <p>/result         one JSON result per command, not retained

Incoming messages are untrusted. Each one is checked in this order:
retained flag (ignored - a retained command would print again on every
reconnect), size, UTF-8 JSON object, optional "id", known command,
module enabled. Accepted commands go through the same parse functions
and do_print_* entry points as the HTTP API, and with them through the
central print rules (quiet hours, rate limit, duplicate suppression,
pending list) - nothing here bypasses print_queue.
"""
import atexit
import hashlib
import json
import logging
import os
import queue
import re
import ssl
import threading
import time
from collections import OrderedDict
from datetime import datetime

import history_store
import i18n
import module_catalog
import pending_store
import print_queue
import printer_status
import secrets_crypto
import settings_store
import version
from security import is_valid_ssh_host

logger = logging.getLogger(__name__)

KEEPALIVE_S = 30
RECONNECT_MIN_S = 1
RECONNECT_MAX_S = 300
MAX_PAYLOAD_BYTES = 64 * 1024
MAX_PREFIX_LEN = 64
MAX_USERNAME_LEN = 128
MAX_PASSWORD_LEN = 256
MAX_CA_BYTES = 64 * 1024
DISPATCH_QUEUE_SIZE = 10
MAX_QUEUED_OUTGOING = 50
RECENT_IDS_KEPT = 100
RECENT_ID_TTL_S = 600
STOP_TIMEOUT_S = 5

HA_DISCOVERY_PREFIX = "homeassistant"
HA_STATUS_TOPIC = "homeassistant/status"

CA_FILE = os.path.join(settings_store.STATE_DIR, "mqtt_ca.pem")

PREFIX_RE = re.compile(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*")
COMMAND_ID_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")

# command -> module_catalog key that must be active for it, mirroring
# app.py's before_request check for the matching /print/* route. None:
# always allowed - "message" behaves like /print/automation, whose
# blueprint isn't a toggleable module.
COMMANDS = {
    "message": None,
    "list": "lists",
    "todo": "lists",
    "task": "lists",
    "weather": "weather",
}

# Printer states that count as "problem" for Home Assistant. "offline"
# is not one: a switched-off printer is a normal state.
PROBLEM_STATES = printer_status.PROBLEM_STATES


# ---------------------------------------------------------------------------
# Validation shared with the settings form (modules/settings/routes.py)
# and the backup restore (modules/backup/archive.py).
# ---------------------------------------------------------------------------

def is_valid_host(host):
    """Same allowlist as the system report's SSH hosts: DNS name, IPv4
    or IPv6, nothing that starts with "-" or contains whitespace."""
    return is_valid_ssh_host(host)


def is_valid_port(port):
    return type(port) is int and 1 <= port <= 65535


def is_valid_prefix(prefix):
    """Topic levels of letters, digits, "_" and "-" only: no MQTT
    wildcards, no leading "$" (reserved for broker topics), no empty
    levels. The Home Assistant discovery prefix is refused so commands
    and discovery configs can never share a topic tree."""
    return (
        isinstance(prefix, str)
        and len(prefix) <= MAX_PREFIX_LEN
        and PREFIX_RE.fullmatch(prefix) is not None
        and prefix.split("/")[0] != HA_DISCOVERY_PREFIX
    )


def is_valid_credential(value, max_len):
    return (
        isinstance(value, str)
        and len(value) <= max_len
        and not any(ord(c) < 32 or ord(c) == 127 for c in value)
    )


def ca_file_present():
    return os.path.isfile(CA_FILE)


def _load_config():
    """The current settings as connection config, or (None, reason)
    when MQTT is off ("disabled") or the stored values are unusable
    ("config_invalid")."""
    s = settings_store.get_settings()["mqtt"]
    if not s.get("enabled"):
        return None, "disabled"
    cfg = {
        "host": s.get("host", ""),
        "port": s.get("port", 1883),
        "tls": bool(s.get("tls")),
        "username": s.get("username", ""),
        "password_encrypted": s.get("password_encrypted", ""),
        "prefix": s.get("prefix", ""),
        "discovery": bool(s.get("discovery")),
        # Part of the config only so a replaced CA file changes the
        # signature below and the bridge reconnects with it.
        "ca_mtime": os.path.getmtime(CA_FILE) if s.get("tls") and ca_file_present() else None,
    }
    if not (is_valid_host(cfg["host"]) and is_valid_port(cfg["port"]) and is_valid_prefix(cfg["prefix"])):
        return None, "config_invalid"
    return cfg, None


def _signature(cfg):
    return json.dumps(cfg, sort_keys=True) if cfg else ""


# ---------------------------------------------------------------------------
# Connection status for the settings page
# ---------------------------------------------------------------------------

_status_lock = threading.Lock()
_status = {"state": "disabled", "detail": ""}


def _transition(state, detail="", log_message=None):
    """Records the connection state for the settings page. The log line
    is only written when the state actually changes, so a broker that
    stays unreachable doesn't produce one line per reconnect attempt.
    WARNING level: the app configures no log handlers of its own, so
    only WARNING and above reach stderr and with it the service log."""
    global _status
    with _status_lock:
        if _status == {"state": state, "detail": detail}:
            return
        _status = {"state": state, "detail": detail}
    if log_message:
        logger.warning(log_message)


def get_status():
    with _status_lock:
        return dict(_status)


# ---------------------------------------------------------------------------
# The running bridge
# ---------------------------------------------------------------------------

def _last_job_from_history():
    try:
        rows = history_store.get_recent(limit=1)
    except Exception:
        return None
    if not rows:
        return None
    row = rows[0]
    return {"type": row["job_type"], "result": row["status"], "at": row["timestamp"]}


def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


class _Bridge:
    def __init__(self, cfg, monitor=None):
        self.cfg = cfg
        self.signature = _signature(cfg)
        prefix = cfg["prefix"]
        digest = hashlib.sha256(prefix.encode()).hexdigest()[:12]
        # Stable per prefix: a second connection with the same client id
        # makes the broker drop the older one (MQTT 3.1.1, 3.1.4), so a
        # restarting worker can never leave two bridges subscribed. Only
        # letters and digits, at most 23 characters - the client id every
        # broker must accept (MQTT 3.1.1, 3.1.3.1).
        self.client_id = "receiptpi" + digest
        self.node_id = "receiptpi_" + digest
        self.topic_availability = f"{prefix}/availability"
        self.topic_state = f"{prefix}/state"
        self.topic_result = f"{prefix}/result"
        self.topic_cmd = f"{prefix}/cmd/"
        self.discovery_topic = f"{HA_DISCOVERY_PREFIX}/device/{self.node_id}/config"

        self.monitor = monitor or printer_status.PrinterStatusMonitor()
        self.printer_since = None
        self.pending = 0
        self.last_job = _last_job_from_history()

        self.client = None
        self.connected = False
        self._refused = False
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._force_port = False
        self._reload_requested = False
        self._commands = queue.Queue(maxsize=DISPATCH_QUEUE_SIZE)
        self._threads = []
        self._publish_lock = threading.Lock()
        self._published_state = None
        self._recent_ids = OrderedDict()
        self._recent_lock = threading.Lock()
        self._retained_logged = False

    # -- lifecycle ---------------------------------------------------------

    def start(self, mqtt):
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.client_id,
            clean_session=True,
            protocol=mqtt.MQTTv311,
        )
        # paho re-raises exceptions from callbacks by default, which
        # would end its network thread; every callback here also catches
        # its own exceptions, this is the second line of defence.
        client.suppress_exceptions = True
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_connect_fail = self._on_connect_fail
        client.on_message = self._on_message
        client.will_set(self.topic_availability, "offline", qos=1, retain=True)
        if self.cfg["username"]:
            password = None
            if self.cfg["password_encrypted"]:
                password = secrets_crypto.decrypt_password(self.cfg["password_encrypted"])
                if password is None:
                    logger.warning("MQTT: the stored password could not be decrypted, connecting without it")
            client.username_pw_set(self.cfg["username"], password)
        if self.cfg["tls"]:
            context = ssl.create_default_context(cafile=CA_FILE if ca_file_present() else None)
            client.tls_set_context(context)
        client.reconnect_delay_set(RECONNECT_MIN_S, RECONNECT_MAX_S)
        client.max_queued_messages_set(MAX_QUEUED_OUTGOING)
        self.client = client

        for target, name in ((self._monitor_loop, "mqtt-status"), (self._dispatch_loop, "mqtt-commands")):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)

        _transition("connecting")
        # IPv6 literals may be stored bracketed (the shared host
        # allowlist accepts that form); the socket API wants them bare.
        client.connect_async(self.cfg["host"].strip("[]"), self.cfg["port"], keepalive=KEEPALIVE_S)
        client.loop_start()

    def stop(self, remove_discovery=False):
        """Publishes "offline" (and, if asked, removes the discovery
        config) while still connected, then disconnects cleanly - a
        clean DISCONNECT means the broker discards the last will, so the
        explicit "offline" is what subscribers see."""
        self._stop.set()
        self._wake.set()
        client = self.client
        if client is not None:
            if self.connected:
                try:
                    if remove_discovery:
                        self._remove_discovery(client)
                    info = client.publish(self.topic_availability, "offline", qos=1, retain=True)
                    info.wait_for_publish(timeout=2)
                except (RuntimeError, ValueError):
                    pass
            client.disconnect()
            client.loop_stop()
        for thread in self._threads:
            thread.join(timeout=STOP_TIMEOUT_S)

    # -- paho callbacks (network thread) -------------------------------------

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        try:
            if reason_code.is_failure:
                self.connected = False
                self._refused = True
                _transition("error", str(reason_code),
                            f"MQTT: connection refused by the broker ({reason_code})")
                return
            self.connected = True
            self._refused = False
            self._retained_logged = False
            _transition("connected", "", f"MQTT: connected to {self.cfg['host']}:{self.cfg['port']}")
            client.publish(self.topic_availability, "online", qos=1, retain=True)
            client.subscribe(self.topic_cmd + "+", qos=1)
            self._sync_discovery(client)
            self._publish_state(force=True)
        except Exception:
            logger.exception("MQTT: error after connecting")

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        self.connected = False
        # After a refused CONNACK the broker closes the connection; the
        # refusal reason (e.g. "Not authorized") is the useful status.
        if not self._stop.is_set() and not self._refused:
            _transition("disconnected", str(reason_code), f"MQTT: connection lost ({reason_code}), reconnecting")

    def _on_connect_fail(self, client, userdata):
        self.connected = False
        if not self._stop.is_set():
            _transition("error", "connect_failed",
                        f"MQTT: cannot reach {self.cfg['host']}:{self.cfg['port']}, retrying with backoff")

    def _on_message(self, client, userdata, message):
        try:
            self._handle_message(message)
        except Exception:
            logger.exception("MQTT: error while handling an incoming message")

    # -- incoming --------------------------------------------------------------

    def _handle_message(self, message):
        topic = message.topic
        if topic == HA_STATUS_TOPIC:
            if self.cfg["discovery"] and message.payload == b"online":
                self._publish_discovery(self.client)
                self._publish_state(force=True)
            return
        if not topic.startswith(self.topic_cmd):
            return
        if message.retain:
            if not self._retained_logged:
                self._retained_logged = True
                logger.warning("MQTT: ignoring retained message(s) on a command topic")
            return
        command = topic[len(self.topic_cmd):]

        if len(message.payload) > MAX_PAYLOAD_BYTES:
            self._publish_result(None, None, False, f"payload larger than {MAX_PAYLOAD_BYTES} bytes", 413)
            return
        try:
            data = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._publish_result(None, None, False, "payload is not valid UTF-8 JSON", 400)
            return
        if not isinstance(data, dict):
            self._publish_result(None, None, False, "payload must be a JSON object", 400)
            return
        command_id = data.get("id")
        if command_id is not None and not (isinstance(command_id, str) and COMMAND_ID_RE.fullmatch(command_id)):
            self._publish_result(None, None, False, "id must be 1-64 characters of A-Z a-z 0-9 _ . -", 400)
            return
        if command not in COMMANDS:
            self._publish_result(command_id, None, False, "unknown command", 404)
            return
        module_key = COMMANDS[command]
        if module_key and not module_catalog.active_modules(settings_store.get_settings()).get(module_key, True):
            self._publish_result(command_id, command, False, "module is disabled", 404)
            return
        if command_id is not None and not self._remember_id(command_id):
            self._publish_result(command_id, command, False, "duplicate id", 409)
            return
        try:
            self._commands.put_nowait((command, data, command_id))
        except queue.Full:
            self._publish_result(command_id, command, False, "too many commands waiting", 503)

    def _remember_id(self, command_id):
        """False if this id was already accepted within RECENT_ID_TTL_S -
        catches a QoS 1 redelivery of a command that already ran."""
        now = time.monotonic()
        with self._recent_lock:
            for old_id, seen in list(self._recent_ids.items()):
                if now - seen > RECENT_ID_TTL_S:
                    del self._recent_ids[old_id]
            if command_id in self._recent_ids:
                return False
            self._recent_ids[command_id] = now
            while len(self._recent_ids) > RECENT_IDS_KEPT:
                self._recent_ids.popitem(last=False)
            return True

    def _dispatch_loop(self):
        while not self._stop.is_set():
            try:
                command, data, command_id = self._commands.get(timeout=1)
            except queue.Empty:
                continue
            try:
                ok, detail, status_code = execute_command(command, data)
            except Exception:
                logger.exception("MQTT: command %s failed", command)
                ok, detail, status_code = False, "internal error", 500
            self._publish_result(command_id, command, ok, detail, status_code)

    def _publish_result(self, command_id, command, ok, detail, status_code):
        if self.client is None:
            return
        payload = {
            "id": command_id,
            "cmd": command,
            "status": "printed" if ok else "error",
            "code": status_code,
            "detail": detail,
        }
        self.client.publish(self.topic_result, json.dumps(payload, ensure_ascii=False), qos=1, retain=False)

    # -- status ------------------------------------------------------------------

    def on_job(self, event):
        """print_queue job listener: only records and wakes the monitor."""
        if event.get("logged"):
            self.last_job = {"type": event["job_type"], "result": event["status"], "at": event["at"]}
        self._force_port = True
        self._wake.set()

    def _monitor_loop(self):
        first = True
        while not self._stop.is_set():
            if not first:
                self._wake.wait(timeout=printer_status.PrinterStatusMonitor.PRESENCE_INTERVAL_S)
                self._wake.clear()
            if self._stop.is_set():
                break
            force = self._force_port or first
            self._force_port = False
            first = False
            try:
                if self.monitor.tick(time.monotonic(), force_port=force) or self.printer_since is None:
                    self.printer_since = _now_iso()
                self.pending = len(pending_store.get_all())
                self._publish_state()
                self._check_config_change()
            except Exception:
                logger.exception("MQTT: status update failed")

    def state_payload(self):
        last = self.last_job or {}
        return {
            "printer": self.monitor.state,
            "printer_since": self.printer_since,
            "problem": self.monitor.state in PROBLEM_STATES,
            "pending": self.pending,
            "last_job_type": last.get("type"),
            "last_job_result": last.get("result"),
            "last_job_at": last.get("at"),
            "version": version.CURRENT_VERSION,
        }

    def _publish_state(self, force=False):
        with self._publish_lock:
            if not self.connected or self.client is None:
                return
            payload = json.dumps(self.state_payload(), sort_keys=True)
            if not force and payload == self._published_state:
                return
            self.client.publish(self.topic_state, payload, qos=1, retain=True)
            self._published_state = payload

    def _check_config_change(self):
        """Picks up settings changes that didn't come through the MQTT
        settings form (e.g. a settings restore): the bridge restarts
        itself once the stored config differs from the running one."""
        if self._reload_requested:
            return
        cfg, _reason = _load_config()
        if _signature(cfg) != self.signature:
            self._reload_requested = True
            reload()

    # -- Home Assistant discovery ----------------------------------------------

    def _sync_discovery(self, client):
        stored = settings_store.get_settings()["mqtt"].get("discovery_topic", "")
        wanted = self.discovery_topic if self.cfg["discovery"] else ""
        if stored and stored != wanted:
            client.publish(stored, b"", qos=1, retain=True)
        if wanted:
            client.subscribe(HA_STATUS_TOPIC, qos=1)
            self._publish_discovery(client)
        if stored != wanted:
            settings_store.update_section("mqtt", {"discovery_topic": wanted})

    def _remove_discovery(self, client):
        stored = settings_store.get_settings()["mqtt"].get("discovery_topic", "")
        if stored:
            # An empty retained message deletes the retained config on
            # the broker (MQTT 3.1.1, 3.3.1.3) - Home Assistant removes
            # the device when it sees it.
            client.publish(stored, b"", qos=1, retain=True)
            settings_store.update_section("mqtt", {"discovery_topic": ""})

    def _publish_discovery(self, client):
        client.publish(self.discovery_topic, json.dumps(self.discovery_payload(), ensure_ascii=False),
                       qos=1, retain=True)

    def discovery_payload(self):
        """Home Assistant device-based discovery: one config message for
        the whole device, components under "cmps", each with its
        platform ("p") and a unique_id. Entity names follow the current
        UI language."""
        n = self.node_id
        return {
            "dev": {
                "identifiers": [n],
                "name": "ReceiptPi",
                "model": "ESC/POS",
                "sw_version": version.CURRENT_VERSION,
            },
            "o": {"name": "ReceiptPi", "sw_version": version.CURRENT_VERSION},
            "availability_topic": self.topic_availability,
            "state_topic": self.topic_state,
            "qos": 1,
            "cmps": {
                f"{n}_printer": {
                    "p": "sensor",
                    "unique_id": f"{n}_printer",
                    "name": i18n.tr("mqtt.discovery.printer"),
                    "device_class": "enum",
                    "options": list(printer_status.STATES),
                    "value_template": "{{ value_json.printer }}",
                },
                f"{n}_problem": {
                    "p": "binary_sensor",
                    "unique_id": f"{n}_problem",
                    "name": i18n.tr("mqtt.discovery.problem"),
                    "device_class": "problem",
                    "value_template": "{{ 'ON' if value_json.problem else 'OFF' }}",
                },
                f"{n}_pending": {
                    "p": "sensor",
                    "unique_id": f"{n}_pending",
                    "name": i18n.tr("mqtt.discovery.pending"),
                    "value_template": "{{ value_json.pending }}",
                },
                f"{n}_last_job": {
                    "p": "sensor",
                    "unique_id": f"{n}_last_job",
                    "name": i18n.tr("mqtt.discovery.last_job"),
                    "value_template": "{{ value_json.last_job_result or 'none' }}",
                    "json_attributes_topic": self.topic_state,
                    "json_attributes_template":
                        "{{ {'type': value_json.last_job_type, 'at': value_json.last_job_at} | tojson }}",
                },
                f"{n}_print_weather": {
                    "p": "button",
                    "unique_id": f"{n}_print_weather",
                    "name": i18n.tr("mqtt.discovery.print_weather"),
                    "command_topic": self.topic_cmd + "weather",
                    "payload_press": "{}",
                },
            },
        }


# ---------------------------------------------------------------------------
# Commands - the same parse functions and do_print_* entry points as the
# HTTP API. Imported lazily, so importing this module for its validation
# helpers (settings page, backup restore) doesn't also import the print
# modules and python-escpos with them.
# ---------------------------------------------------------------------------

def execute_command(command, data):
    """Returns (ok, detail, http_status) like enqueue_print()."""
    if command == "message":
        from modules.automation.routes import do_print_automation, parse_automation_body
        fields, error = parse_automation_body(data)
        if error:
            return False, *error
        return do_print_automation(*fields, source="mqtt")
    if command in ("list", "todo"):
        from modules.lists.routes import do_print_list, parse_list_body
        default_title = "List" if command == "list" else i18n.tr("lists.todo.title_default")
        fields, error = parse_list_body(data, default_title)
        if error:
            return False, *error
        return do_print_list(*fields, job_type="shopping" if command == "list" else "todo", source="mqtt")
    if command == "task":
        from modules.lists.routes import do_print_task, parse_task_fields
        fields, error = parse_task_fields(data)
        if error:
            return False, *error
        return do_print_task(fields, source="mqtt")
    if command == "weather":
        from modules.weather.routes import do_print_weather, parse_weather_location
        location_name, error = parse_weather_location(data.get("location"))
        if error:
            return False, *error
        return do_print_weather(location_name, source="mqtt")
    return False, "unknown command", 404


# ---------------------------------------------------------------------------
# Module-level control
# ---------------------------------------------------------------------------

_bridge = None
_bridge_lock = threading.Lock()
_hooks_installed = False


def _forward_job_event(event):
    bridge = _bridge
    if bridge is not None:
        bridge.on_job(event)


def _start_locked():
    global _bridge, _hooks_installed
    _bridge = None
    cfg, reason = _load_config()
    if cfg is None:
        _transition(reason, "", "MQTT: stored settings are invalid, not connecting"
                    if reason == "config_invalid" else None)
        return
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        _transition("library_missing", "", "MQTT: enabled, but paho-mqtt is not installed")
        return
    if not _hooks_installed:
        print_queue.add_job_listener(_forward_job_event)
        atexit.register(stop)
        _hooks_installed = True
    bridge = _Bridge(cfg)
    try:
        bridge.start(mqtt)
    except (OSError, ValueError, ssl.SSLError) as e:
        bridge.stop()
        _transition("error", type(e).__name__, f"MQTT: could not start ({type(e).__name__})")
        return
    _bridge = bridge


def start():
    """Called once from app.py inside the worker process."""
    with _bridge_lock:
        if _bridge is None:
            _start_locked()


def reload(wait=False):
    """Restarts the bridge from the current settings. Runs on its own
    thread by default: stopping waits for paho's network thread, which
    can take a few seconds while a connection attempt times out, and a
    request handler shouldn't sit through that."""
    def run():
        with _bridge_lock:
            old = _bridge
            new_cfg, _reason = _load_config()
            if old is not None:
                if _signature(new_cfg) == old.signature:
                    # Both the settings form and the bridge's own change
                    # check can ask for a reload of the same change.
                    old._reload_requested = False
                    return
                same_target = new_cfg is not None and all(
                    new_cfg[k] == old.cfg[k] for k in ("host", "port", "prefix"))
                old.stop(remove_discovery=not (same_target and new_cfg["discovery"]))
            _start_locked()

    if wait:
        run()
    else:
        threading.Thread(target=run, name="mqtt-reload", daemon=True).start()


def stop():
    """Clean shutdown at process exit: "offline" is published, the
    discovery config stays (a service restart shouldn't remove the
    device from Home Assistant)."""
    global _bridge
    with _bridge_lock:
        if _bridge is not None:
            _bridge.stop()
            _bridge = None
