"""
Module: system report - largely uses the same SSH commands as the
existing Termux Lab Commander (scripts/termux-lab-commander.sh in the
HomeLab repo): CPU temp/load, RAM, LXC/VM list, PBS backups, and update
check (the Commander's version doesn't cover PBS, this one does).
Deliberately no Zabbix, to avoid needing extra item/template
configuration. Only two structured roles exist (Proxmox, PBS) -
anything else (a NAS of any kind, a QNAP, ...) goes through role=
"custom" with a user-supplied command instead of a dedicated fetch_*
function, since no particular NAS software can be assumed for everyone.
"""
import json
import subprocess
from datetime import datetime

import paramiko
from flask import Blueprint, jsonify, render_template

import i18n
import secrets_crypto
import settings_store
from modules.message.routes import _raw_print_message
from print_queue import enqueue_print
from security import csrf_protect, get_csrf_token, require_api_token

system_bp = Blueprint("system", __name__)


def _ssh_run_key(user, host, remote_command, timeout=10):
    """Runs a command via the system `ssh` CLI, authenticating with the
    ReceiptPi Pi's own SSH key (passwordless login must already be set
    up on the target). Returns stdout as a string, raises on
    errors/timeout."""
    result = subprocess.run(
        [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=5",
            "-o", "StrictHostKeyChecking=accept-new",
            f"{user}@{host}",
            remote_command,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or i18n.tr("receipt.system.ssh_command_failed", code=result.returncode))
    return result.stdout.strip()


def _ssh_run_password(user, host, password, remote_command, timeout=10):
    """Runs a command via paramiko, authenticating with a password
    instead of a key. Used instead of the `ssh` CLI + sshpass for
    password hosts, since sshpass has to pass the password as a command-
    line argument or env var that ends up visible to anyone who can read
    /proc/<pid>/cmdline or /proc/<pid>/environ on the Pi - paramiko hands
    the password directly to libssh's auth exchange, never through a
    process argument. Mirrors _ssh_run_key()'s contract (stripped
    stdout, raises on non-zero exit/timeout) so both are interchangeable
    to callers via ssh_run() below."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(host, username=user, password=password, timeout=timeout)
        _stdin, stdout, stderr = client.exec_command(remote_command, timeout=timeout)
        exit_status = stdout.channel.recv_exit_status()
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")
    finally:
        client.close()
    if exit_status != 0:
        raise RuntimeError(err.strip() or i18n.tr("receipt.system.ssh_command_failed", code=exit_status))
    return out.strip()


def ssh_run(user, host, remote_command, password=None, timeout=10):
    """Single entry point every fetch_* function below calls - picks key
    auth (existing behavior, unchanged for every already-configured
    host) or password auth (paramiko) based on whether a password was
    supplied. See _entry_password() for where a host entry's (decrypted)
    password comes from."""
    if password:
        return _ssh_run_password(user, host, password, remote_command, timeout=timeout)
    return _ssh_run_key(user, host, remote_command, timeout=timeout)


def fetch_pve_status(user, host, password=None):
    """CPU temp, CPU load, RAM on a Proxmox-like host - identical
    commands to the Termux Commander (check_status())."""
    lines = []
    cpu_temp = ssh_run(user, host,
                        "cat /sys/class/thermal/thermal_zone0/temp | awk '{printf \"%.0f\", $1/1000}'",
                        password=password)
    cpu_load = ssh_run(user, host,
                        "top -bn1 | grep 'Cpu' | awk '{printf \"%.0f\", 100-$8}'",
                        password=password)
    ram = ssh_run(user, host,
                  "free -h | awk '/^Mem:/{print $3\"/\"$2}'",
                  password=password)
    lines.append(i18n.tr("receipt.system.cpu_temp", value=cpu_temp))
    lines.append(i18n.tr("receipt.system.cpu_load", value=cpu_load))
    lines.append(i18n.tr("receipt.system.ram", value=ram))
    return lines


def fetch_lxc_vm_status(user, host, password=None):
    """LXC and VM list from a Proxmox-like host (pct list / qm list)."""
    lines = []
    lxc_output = ssh_run(user, host, "pct list", password=password)
    for line in lxc_output.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 3:
            lines.append(f"LXC {parts[0]} ({parts[2]}): {parts[1]}")

    vm_output = ssh_run(user, host, "qm list", password=password)
    for line in vm_output.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 3:
            lines.append(f"VM {parts[0]} ({parts[1]}): {parts[2]}")

    return lines or [i18n.tr("receipt.system.no_lxc_vms")]


def fetch_pbs_recent_backups(user, host, password=None, limit=5):
    """Most recent PBS backup tasks (Backup/Sync/Prune/Verify/GC) from a
    PBS-like host, analogous to the Python evaluation in the Termux
    Commander, parsed here locally instead of remotely via an embedded
    Python call."""
    output = ssh_run(user, host,
                      "proxmox-backup-manager task list --all --output-format json-pretty",
                      password=password)
    tasks = json.loads(output)
    relevant_types = {"backup", "syncjob", "prune", "verify", "garbage_collection"}
    relevant = [t for t in tasks if t.get("worker_type") in relevant_types]
    lines = []
    for t in relevant[:limit]:
        ts = datetime.fromtimestamp(t["starttime"]).strftime("%d.%m %H:%M")
        status = t.get("status", "?")
        lines.append(f"{ts} {t['worker_type']}: {status}")
    return lines or [i18n.tr("receipt.system.no_backup_tasks")]


UPDATE_LIST_THRESHOLD = 10  # print package names individually up to this count, otherwise just the number


def fetch_updates_for_host(label, user, host, password=None):
    """Returns update lines for one host. With few open updates
    (<= UPDATE_LIST_THRESHOLD), the package names are listed
    individually; above that, just the count."""
    # "|| true" is necessary here: grep exits 1 when it finds NO matching
    # lines - which simply means "no updates available", not an actual
    # error. Without it, ssh_run() would treat that as a failed SSH
    # command (any non-zero exit code) and raise, even though nothing
    # went wrong.
    output = ssh_run(user, host, "apt list --upgradable 2>/dev/null | grep -v '^Listing' || true",
                      password=password)
    if not output:
        return [i18n.tr("receipt.system.host_current", label=label)]

    packages = [line.split("/")[0] for line in output.splitlines() if line.strip()]
    count = len(packages)
    if count <= UPDATE_LIST_THRESHOLD:
        lines = [i18n.tr("receipt.system.host_updates_count", label=label, count=count)]
        lines.extend(f"  - {p}" for p in packages)
        return lines
    return [i18n.tr("receipt.system.host_updates_available", label=label, count=count)]


def fetch_update_counts(hosts):
    """Update status for every configured host, like the Termux
    Commander's 'u) Update-Check' - PBS wasn't covered there, added
    here. Runs across ALL configured hosts regardless of role (not just
    a fixed PVE/OMV/PBS triple) since "apt list --upgradable" is the
    same command on every Debian-based host (Proxmox, OMV, PBS). Each
    host is wrapped in its own try/except (unlike the other fetch_*
    functions, which rely on the caller's single try/except around the
    whole section) - now that role="custom" allows genuinely arbitrary,
    possibly non-Debian hosts (e.g. a QNAP with no apt), one such host
    must not blank out the update counts for every other host too."""
    lines = []
    for entry in hosts:
        label = entry.get("name") or entry.get("role", "")
        try:
            lines.extend(fetch_updates_for_host(label, entry["user"], entry["host"],
                                                 password=_entry_password(entry)))
        except Exception as e:
            lines.append(i18n.tr("print.error_prefix") + f"{label}: {e}")
    return lines


def fetch_custom_command(user, host, command, password=None):
    """Runs a user-defined SSH command verbatim (role="custom") - for
    hosts that aren't Proxmox/OMV/PBS (e.g. a QNAP) and therefore have
    no structured fetch_* function of their own. Output is split into
    lines as-is, no parsing/formatting."""
    output = ssh_run(user, host, command, password=password)
    return output.splitlines() if output else [i18n.tr("receipt.system.no_output")]


def _entry_password(entry):
    """Decrypts a host entry's stored password (see secrets_crypto.py),
    or returns None for a key-auth host (empty/missing
    "password_encrypted") - None is also what ssh_run()'s password=
    parameter expects to mean "use key auth"."""
    return secrets_crypto.decrypt_password(entry.get("password_encrypted"))


# Maps a host entry's role to the i18n key used as its fallback display
# name (when the user leaves "name" blank) - same strings that already
# labelled the old fixed fields, now reused as the role dropdown's
# option text (see settings_system_report.html).
ROLE_NAME_KEYS = {
    "proxmox": "settings.system_report.proxmox",
    "pbs": "settings.system_report.pbs",
    "custom": "settings.system_report.custom",
}


def _report_sections_for_entry(entry):
    """Returns a list of (title, fetch_callable) pairs for one
    configured host, based on its role. This is the one place that maps
    role -> which sections get printed for that host - the underlying
    fetch_* functions stay role-specific (see their own docstrings)
    since e.g. "pct list"/"qm list" only make sense against a
    Proxmox-like host. There is deliberately no NAS-specific structured
    role (see settings_store.py's _migrate_system_report_omv_role_removed
    for the removed "omv" role) - any non-Proxmox/PBS host, NAS or
    otherwise, goes through role="custom" instead."""
    role = entry.get("role")
    name = entry.get("name") or i18n.tr(ROLE_NAME_KEYS.get(role, "settings.system_report.proxmox"))
    user, host = entry["user"], entry["host"]
    password = _entry_password(entry)

    if role == "proxmox":
        return [
            (name, lambda: fetch_pve_status(user, host, password=password)),
            (f"{name} – {i18n.tr('receipt.system.section.lxc_vms')}", lambda: fetch_lxc_vm_status(user, host, password=password)),
        ]
    if role == "pbs":
        return [(name, lambda: fetch_pbs_recent_backups(user, host, password=password))]
    if role == "custom":
        command = entry.get("command", "")
        return [(name, lambda: fetch_custom_command(user, host, command, password=password))]
    return []


def _raw_print_system_report():
    report_lines = []
    hosts = settings_store.get_settings()["system_report"]["hosts"]

    def add_section(title, fetch_func):
        report_lines.append("-" * 32)
        report_lines.append(title)
        try:
            report_lines.extend(fetch_func())
        except Exception as e:
            report_lines.append(i18n.tr("print.error_prefix") + str(e))

    for entry in hosts:
        for title, fetch_func in _report_sections_for_entry(entry):
            add_section(title, fetch_func)

    if hosts:
        add_section(i18n.tr("receipt.system.section.updates"), lambda: fetch_update_counts(hosts))

    text = "\n".join(report_lines)
    # Title goes through _raw_print_message's own title parameter now
    # (centered/bold, same as every other print type) instead of being
    # baked into text as a plain first line - also drops the timestamp
    # that used to be duplicated here, since the shared "-- dd.mm.yyyy
    # HH:MM --" footer already covers that.
    _raw_print_message(i18n.tr("receipt.system.title"), text, module="system")


@system_bp.route("/system", methods=["GET"])
def system_page():
    return render_template("system.html", message=None, success=None, csrf_token=get_csrf_token())


@system_bp.route("/print/system", methods=["POST"])
@require_api_token
def print_system():
    ok, detail, status_code = enqueue_print(
        _raw_print_system_report, job_type="system", source="api", retry_payload={},
    )
    if ok:
        return jsonify({"status": "printed"}), 200
    return jsonify({"status": "error", "detail": detail}), status_code


@system_bp.route("/ui/system", methods=["POST"])
@csrf_protect
def ui_print_system():
    ok, detail, _status_code = enqueue_print(
        _raw_print_system_report, job_type="system", source="ui", retry_payload={},
    )
    message = i18n.tr("print.success") if ok else i18n.tr("print.error_prefix") + detail
    return render_template("system.html", message=message, success=ok, csrf_token=get_csrf_token())
