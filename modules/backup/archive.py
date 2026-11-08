"""
Settings backup archive: export, inspection and restore (the web UI
side is modules/backup/routes.py).

The archive is a plain .zip:
  settings.json      allowlisted copy of the settings (SETTINGS_SCHEMA)
  logos/<slot>.png   every stored logo file, one member per slot
  manifest.json      format + app version, creation time, size and
                     sha256 of every other member - written last, so the
                     hashes can be computed while the members stream out

SECRETS NEVER GO INTO AN ARCHIVE - not in plaintext, not encrypted, not
the key. settings.json is exported field by field from SETTINGS_SCHEMA:
anything not listed there is not copied, including any field added to
settings.json later. The encrypted Mealie token, SSH passwords and
MQTT password, secret.key, the MQTT CA file, config.py, the print
history, pending jobs and list drafts are therefore never part of an
archive, and neither is a host's SSH "command" (it is executed on that
host by the system report, so it is only ever set by hand in the
settings UI) or the MQTT user name. Plain booleans stand in for the
secrets, so a restore can tell the user what to enter again:
recipes.mealie.token_set, system_report.hosts[].uses_password and
mqtt.password_set.

A restore writes only fields from the same schema, validated like the
settings forms do, and never changes a secret on the device: the stored
Mealie token, host passwords, MQTT password and secret.key stay exactly
as they are.
"""
import hashlib
import io
import json
import os
import re
import secrets
import stat
import threading
import time
import uuid
import zipfile
from datetime import UTC, datetime

from PIL import Image

import i18n
import logos
import module_catalog
import mqtt_bridge
import settings_store
import themes
import version
from modules.recipes import mealie
from modules.settings.routes import (
    RECIPE_PROVIDERS,
    STORM_WARNING_PROVIDERS,
    SYSTEM_REPORT_ROLES,
    WEATHER_REPORT_PROVIDERS,
    build_quiet_hours_rule,
    validate_and_build_location,
    validate_github_repo,
    validate_print_rules_updates,
    validate_quiet_hours_rule,
    validate_ssh_host_entry,
)
from security import MAX_TITLE_LEN, is_valid_ssh_host, is_valid_ssh_user

FORMAT_NAME = "receiptpi-backup"
FORMAT_VERSION = 1

MANIFEST_NAME = "manifest.json"
SETTINGS_NAME = "settings.json"
LOGO_SLOTS = (*logos.MODULE_KEYS, "default")
LOGO_NAMES = {f"logos/{slot}.png": slot for slot in LOGO_SLOTS}
ALLOWED_NAMES = {MANIFEST_NAME, SETTINGS_NAME, *LOGO_NAMES}

# Upper bounds for an uploaded archive. Werkzeug spools uploads to /tmp,
# which is a RAM-backed tmpfs on the Pi, so the upload cap stays well
# below the app-wide MAX_CONTENT_LENGTH. Member sizes are enforced while
# reading, not taken from the (forgeable) zip headers alone.
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED = 32 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_SETTINGS_BYTES = 256 * 1024
MAX_LOGO_BYTES = 8 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
MAX_MEMBERS = len(ALLOWED_NAMES)
MAX_LIST_ENTRIES = 100
MAX_STORM_TEXT_LEN = 100
_CHUNK = 64 * 1024

# Local pre-restore snapshots (same archive format, so no secrets either)
# and the archive waiting for confirmation between the "check" and the
# "apply" step. Both directories are 0700, their files 0600.
SNAPSHOT_DIR = os.path.join(settings_store.STATE_DIR, "restore_snapshots")
STAGING_DIR = os.path.join(settings_store.STATE_DIR, "restore_staging")
SNAPSHOTS_KEPT = 5
STAGING_MAX_AGE_S = 3600
_STAGING_ID_RE = re.compile(r"^[0-9a-f]{32}$")

_restore_lock = threading.Lock()


class ArchiveError(Exception):
    """A refused archive or restore, with a translation key + params
    for the message shown to the user."""

    def __init__(self, key, **params):
        super().__init__(key)
        self.key = key
        self.params = params


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------

class _Map:
    """Schema node for an open, user-named collection (e.g. weather
    locations): any string key, every value checked against `item`."""

    def __init__(self, item):
        self.item = item


_NUMBER = "number"

SETTINGS_SCHEMA = {
    "print_rules": {
        "quiet_hours_rules": [{
            "id": str, "label": str, "enabled": bool, "days": [int], "start": str, "end": str,
        }],
        "max_jobs_per_hour": int,
        "duplicate_window_seconds": int,
        "pending_retention_days": int,
        "text_size": str,
    },
    "weather": {
        "locations": _Map({"lat": _NUMBER, "lon": _NUMBER}),
        "default_location": str,
        "report_provider": str,
        "storm_warning": {
            "enabled": bool, "provider": str, "dwd_state": str, "dwd_region_name": str,
            "meteoalarm_country": str, "meteoalarm_region": str, "ignore_quiet_hours": bool,
        },
    },
    "language": str,
    "theme": str,
    "glass": {"enabled": bool, "blur": bool},
    "updates": {"auto": bool},
    "enabled_modules": _Map(bool),
    "recipes": {
        "provider": str,
        "mealie": {"base_url": str, "token_set": bool},
    },
    "system_report": {
        "hosts": [{
            "id": str, "name": str, "role": str, "host": str, "user": str, "uses_password": bool,
        }],
    },
    "github_watch": {
        "repos": [{"owner": str, "repo": str}],
    },
    "logos": {
        "enabled": bool,
        "modules": _Map({"enabled": bool}),
    },
    # No username, password, CA file or discovery bookkeeping.
    "mqtt": {
        "enabled": bool, "host": str, "port": int, "tls": bool, "prefix": str, "discovery": bool,
        "password_set": bool,
    },
}


def _type_ok(value, expected):
    # bool is a subclass of int - checked explicitly so true/false never
    # passes as a number, same reasoning as validate_print_rules_updates().
    if expected is _NUMBER:
        return type(value) in (int, float)
    if expected is int:
        return type(value) is int
    return type(value) is expected


def _filter(value, schema, path, ignored, type_errors):
    """Returns the part of `value` described by `schema`, or _SKIP.
    Keys outside the schema are appended to `ignored` (as dotted paths
    with list indexes / "*" for map keys, never values); values of the
    wrong type are appended to `type_errors`."""
    if isinstance(schema, dict):
        if not isinstance(value, dict):
            type_errors.append(path)
            return _SKIP
        out = {}
        for key, sub_value in value.items():
            sub_path = f"{path}.{key}" if path else str(key)
            if key not in schema:
                ignored.append(sub_path)
                continue
            filtered = _filter(sub_value, schema[key], sub_path, ignored, type_errors)
            if filtered is not _SKIP:
                out[key] = filtered
        return out
    if isinstance(schema, list):
        if not isinstance(value, list):
            type_errors.append(path)
            return _SKIP
        out = []
        for index, item in enumerate(value):
            filtered = _filter(item, schema[0], f"{path}[{index}]", ignored, type_errors)
            if filtered is not _SKIP:
                out.append(filtered)
        return out
    if isinstance(schema, _Map):
        if not isinstance(value, dict):
            type_errors.append(path)
            return _SKIP
        out = {}
        for key, sub_value in value.items():
            filtered = _filter(sub_value, schema.item, f"{path}.*", ignored, type_errors)
            if filtered is not _SKIP:
                out[str(key)] = filtered
        return out
    if not _type_ok(value, schema):
        type_errors.append(path)
        return _SKIP
    return value


_SKIP = object()


def export_settings(settings):
    """The allowlisted, secret-free copy of `settings` that goes into an
    archive, plus the booleans that replace the secrets."""
    out = _filter(settings, SETTINGS_SCHEMA, "", [], [])
    # The schema allows these flags, so a stored value of the right type
    # would pass the filter - they're always recomputed from the secret
    # fields' presence instead.
    if "recipes" in out:
        mealie_out = out["recipes"].setdefault("mealie", {})
        mealie_out["token_set"] = bool(settings.get("recipes", {}).get("mealie", {}).get("token_encrypted"))
    if "mqtt" in out:
        out["mqtt"]["password_set"] = bool(settings.get("mqtt", {}).get("password_encrypted"))
    hosts_in = settings.get("system_report", {}).get("hosts", [])
    if "system_report" in out and isinstance(hosts_in, list):
        exported = []
        for host in hosts_in:
            if not isinstance(host, dict):
                continue
            entry = _filter(host, SETTINGS_SCHEMA["system_report"]["hosts"][0], "", [], [])
            entry["uses_password"] = bool(host.get("password_encrypted"))
            exported.append(entry)
        out["system_report"]["hosts"] = exported
    return out


# ---------------------------------------------------------------------------
# Export (streamed)
# ---------------------------------------------------------------------------

class _Sink(io.RawIOBase):
    """Write-only, non-seekable target for zipfile: collects the bytes
    zipfile produces so a generator can hand them out piece by piece.
    zipfile detects the missing tell()/seek() and switches to data
    descriptors, which is what makes writing without a seekable file
    possible."""

    def __init__(self):
        super().__init__()
        self._chunks = []

    def writable(self):
        return True

    def write(self, data):
        self._chunks.append(bytes(data))
        return len(data)

    def drain(self):
        data = b"".join(self._chunks)
        self._chunks = []
        return data


def _zip_info(name, compress):
    info = zipfile.ZipInfo(name, date_time=time.localtime()[:6])
    info.compress_type = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    return info


def iter_backup_chunks():
    """Yields the archive bytes. Never holds more than one read chunk
    of a logo in memory; settings.json is small and built in memory.
    Reads the stores without taking any print-queue lock, so a running
    backup never delays a print job."""
    sink = _Sink()
    files = []
    with zipfile.ZipFile(sink, "w") as zf:
        settings_bytes = json.dumps(
            export_settings(settings_store.get_settings()), indent=2, ensure_ascii=False,
        ).encode("utf-8")
        with zf.open(_zip_info(SETTINGS_NAME, compress=True), "w") as out:
            out.write(settings_bytes)
        files.append({"name": SETTINGS_NAME, "size": len(settings_bytes),
                      "sha256": hashlib.sha256(settings_bytes).hexdigest()})
        yield sink.drain()

        for slot in LOGO_SLOTS:
            path = logos.slot_path(slot)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            name = f"logos/{slot}.png"
            digest = hashlib.sha256()
            size = 0
            # PNG data is already compressed - storing it as-is is
            # cheaper on the Pi than deflating it again.
            with open(path, "rb") as src, zf.open(_zip_info(name, compress=False), "w") as out:
                for chunk in iter(lambda: src.read(_CHUNK), b""):
                    out.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                    yield sink.drain()
            files.append({"name": name, "size": size, "sha256": digest.hexdigest()})

        manifest = {
            "format": FORMAT_NAME,
            "format_version": FORMAT_VERSION,
            "app_version": version.CURRENT_VERSION,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "files": files,
        }
        with zf.open(_zip_info(MANIFEST_NAME, compress=True), "w") as out:
            out.write(json.dumps(manifest, indent=2).encode("utf-8"))
    yield sink.drain()


def download_filename(now=None):
    now = now or datetime.now()
    return f"receiptpi-backup-{now.strftime('%Y-%m-%d-%H%M')}.zip"


def estimated_size():
    """Rough archive size (logo files + a few KB), for warning before a
    download that would be too large to restore later."""
    total = 16 * 1024
    for slot in LOGO_SLOTS:
        path = logos.slot_path(slot)
        if os.path.isfile(path) and not os.path.islink(path):
            total += os.path.getsize(path)
    return total


def _private_dir(path):
    os.makedirs(path, mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)  # makedirs' mode is filtered by the umask


def _open_private(path):
    """New file, created with 0600 from the start (no window where it
    is readable by others), refusing to follow or reuse an existing
    path."""
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb")


def write_snapshot():
    """Writes a pre-restore snapshot (same secret-free archive format)
    and keeps only the newest SNAPSHOTS_KEPT. Returns the file name."""
    _private_dir(SNAPSHOT_DIR)
    name = f"receiptpi-backup-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.zip"
    path = os.path.join(SNAPSHOT_DIR, name)
    tmp_path = path + ".tmp"
    with _open_private(tmp_path) as f:
        for chunk in iter_backup_chunks():
            f.write(chunk)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)
    snapshots = sorted(n for n in os.listdir(SNAPSHOT_DIR) if n.startswith("receiptpi-backup-") and n.endswith(".zip"))
    for old in snapshots[:-SNAPSHOTS_KEPT]:
        os.remove(os.path.join(SNAPSHOT_DIR, old))
    return name


# ---------------------------------------------------------------------------
# Staging (upload waiting for confirmation)
# ---------------------------------------------------------------------------

def _clear_staging(keep=None):
    if not os.path.isdir(STAGING_DIR):
        return
    for name in os.listdir(STAGING_DIR):
        if keep and name == keep:
            continue
        try:
            os.remove(os.path.join(STAGING_DIR, name))
        except OSError:
            pass


def stage_upload(file_storage):
    """Stores an uploaded archive for the confirmation step and returns
    its id. Only one archive is staged at a time - a new upload replaces
    any previous one. Streams to disk in chunks, never reads it whole."""
    _private_dir(STAGING_DIR)
    _clear_staging()
    staging_id = secrets.token_hex(16)
    path = os.path.join(STAGING_DIR, f"{staging_id}.zip")
    size = 0
    try:
        with _open_private(path) as out:
            for chunk in iter(lambda: file_storage.stream.read(_CHUNK), b""):
                size += len(chunk)
                if size > MAX_ARCHIVE_BYTES:
                    raise ArchiveError("backup.error.too_large", max_mb=MAX_ARCHIVE_BYTES // (1024 * 1024))
                out.write(chunk)
    except BaseException:
        discard_staged(staging_id)
        raise
    return staging_id


def _staging_file(staging_id):
    """Path of the staging file for staging_id, or None if the id is not
    exactly 32 lowercase hex characters. fullmatch, because "$" with
    match() would also accept a trailing newline. The id comes from a
    form, so the normalized path is checked to lie inside STAGING_DIR as
    well: two independent checks, either of which keeps the id from
    naming a file anywhere else."""
    if not isinstance(staging_id, str) or not _STAGING_ID_RE.fullmatch(staging_id):
        return None
    base = os.path.normpath(STAGING_DIR)
    path = os.path.normpath(os.path.join(base, f"{staging_id}.zip"))
    if not path.startswith(base + os.sep):
        return None
    return path


def staged_path(staging_id):
    """Path of a staged archive, or None if the id is malformed,
    unknown or older than STAGING_MAX_AGE_S (then it's deleted)."""
    path = _staging_file(staging_id)
    if path is None or not os.path.isfile(path):
        return None
    if time.time() - os.path.getmtime(path) > STAGING_MAX_AGE_S:
        discard_staged(staging_id)
        return None
    return path


def discard_staged(staging_id):
    path = _staging_file(staging_id)
    if path is not None:
        try:
            os.remove(path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Inspection (nothing on the device is touched here)
# ---------------------------------------------------------------------------

def _member_limit(name):
    if name == MANIFEST_NAME:
        return MAX_MANIFEST_BYTES
    if name == SETTINGS_NAME:
        return MAX_SETTINGS_BYTES
    return MAX_LOGO_BYTES


def _read_member(zf, info, limit):
    """Reads one member, refusing to go past `limit` bytes regardless of
    what the header claims (zip bombs can lie about file_size)."""
    data = bytearray()
    try:
        with zf.open(info) as src:
            while True:
                chunk = src.read(_CHUNK)
                if not chunk:
                    break
                data += chunk
                if len(data) > limit:
                    raise ArchiveError("backup.error.member_too_large", name=info.filename)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, OSError, RuntimeError, NotImplementedError) as e:
        raise ArchiveError("backup.error.corrupt", name=info.filename) from e
    return bytes(data)


def _hash_member(zf, info, limit):
    digest = hashlib.sha256()
    size = 0
    try:
        with zf.open(info) as src:
            for chunk in iter(lambda: src.read(_CHUNK), b""):
                size += len(chunk)
                if size > limit:
                    raise ArchiveError("backup.error.member_too_large", name=info.filename)
                digest.update(chunk)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, OSError, RuntimeError, NotImplementedError) as e:
        raise ArchiveError("backup.error.corrupt", name=info.filename) from e
    return size, digest.hexdigest()


def _check_member_header(info):
    name = info.filename
    parts = name.split("/")
    if (name.startswith("/") or "\\" in name or ":" in name or "\x00" in name
            or ".." in parts or "" in parts):
        raise ArchiveError("backup.error.unsafe_path", name=name)
    if stat.S_ISLNK(info.external_attr >> 16):
        raise ArchiveError("backup.error.symlink", name=name)
    if info.is_dir() or name not in ALLOWED_NAMES:
        raise ArchiveError("backup.error.unexpected_file", name=name)
    if info.flag_bits & 0x1:
        raise ArchiveError("backup.error.encrypted", name=name)
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise ArchiveError("backup.error.corrupt", name=name)
    if info.file_size > _member_limit(name):
        raise ArchiveError("backup.error.member_too_large", name=name)
    if info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
        raise ArchiveError("backup.error.member_too_large", name=name)


def _check_manifest(manifest):
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT_NAME:
        raise ArchiveError("backup.error.not_backup")
    fmt = manifest.get("format_version")
    if type(fmt) is not int or fmt < 1:
        raise ArchiveError("backup.error.not_backup")
    if fmt > FORMAT_VERSION:
        raise ArchiveError("backup.error.newer_format", version=fmt)
    app_version = manifest.get("app_version")
    created_at = manifest.get("created_at")
    if not isinstance(app_version, str) or not isinstance(created_at, str) or len(created_at) > 40:
        raise ArchiveError("backup.error.not_backup")
    order = version.compare_versions(app_version, version.CURRENT_VERSION)
    if order is None:
        raise ArchiveError("backup.error.not_backup")
    if order > 0:
        raise ArchiveError("backup.error.newer_app", version=app_version, current=version.CURRENT_VERSION)
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) > MAX_MEMBERS:
        raise ArchiveError("backup.error.not_backup")
    listed = {}
    for entry in files:
        if (not isinstance(entry, dict) or not isinstance(entry.get("name"), str)
                or type(entry.get("size")) is not int
                or not isinstance(entry.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
                or entry["name"] in listed):
            raise ArchiveError("backup.error.not_backup")
        listed[entry["name"]] = entry
    return listed


def _check_png(zf, info):
    """The logo must really be a decodable PNG within the same
    dimension limit as a logo upload (logos.MAX_UPLOAD_DIMENSION). The
    size is checked from the header first, so an oversized image is
    refused before its pixels are decoded."""
    data = _read_member(zf, info, MAX_LOGO_BYTES)
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.format != "PNG":
                raise ArchiveError("backup.error.not_image", name=info.filename)
            if img.width > logos.MAX_UPLOAD_DIMENSION or img.height > logos.MAX_UPLOAD_DIMENSION:
                raise ArchiveError("backup.error.image_too_large", name=info.filename)
            img.verify()
        with Image.open(io.BytesIO(data)) as img:
            img.load()
    except ArchiveError:
        raise
    except Exception as e:
        raise ArchiveError("backup.error.not_image", name=info.filename) from e


def inspect_archive(path):
    """Fully checks an archive without touching anything on the device.
    Returns {"manifest", "settings" (raw dict or None), "logo_slots"}.
    Raises ArchiveError for anything that isn't a valid backup."""
    if os.path.getsize(path) > MAX_ARCHIVE_BYTES:
        raise ArchiveError("backup.error.too_large", max_mb=MAX_ARCHIVE_BYTES // (1024 * 1024))
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        raise ArchiveError("backup.error.not_zip") from e
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_MEMBERS:
            raise ArchiveError("backup.error.not_backup")
        names = [info.filename for info in infos]
        if len(set(names)) != len(names):
            raise ArchiveError("backup.error.duplicate_file")
        for info in infos:
            _check_member_header(info)
        if sum(info.file_size for info in infos) > MAX_TOTAL_UNCOMPRESSED:
            raise ArchiveError("backup.error.too_large", max_mb=MAX_ARCHIVE_BYTES // (1024 * 1024))
        by_name = {info.filename: info for info in infos}
        if MANIFEST_NAME not in by_name:
            raise ArchiveError("backup.error.not_backup")
        try:
            manifest = json.loads(_read_member(zf, by_name[MANIFEST_NAME], MAX_MANIFEST_BYTES))
        except (ValueError, UnicodeDecodeError) as e:
            raise ArchiveError("backup.error.not_backup") from e
        listed = _check_manifest(manifest)
        if set(listed) != set(names) - {MANIFEST_NAME}:
            raise ArchiveError("backup.error.manifest_mismatch")
        for name, entry in listed.items():
            size, digest = _hash_member(zf, by_name[name], _member_limit(name))
            if size != entry["size"] or digest != entry["sha256"]:
                raise ArchiveError("backup.error.manifest_mismatch")

        settings_raw = None
        if SETTINGS_NAME in by_name:
            try:
                settings_raw = json.loads(_read_member(zf, by_name[SETTINGS_NAME], MAX_SETTINGS_BYTES))
            except (ValueError, UnicodeDecodeError) as e:
                raise ArchiveError("backup.error.settings_invalid") from e
            if not isinstance(settings_raw, dict):
                raise ArchiveError("backup.error.settings_invalid")

        logo_slots = []
        for name, slot in LOGO_NAMES.items():
            if name in by_name:
                _check_png(zf, by_name[name])
                logo_slots.append(slot)
    return {"manifest": manifest, "settings": settings_raw, "logo_slots": logo_slots}


# ---------------------------------------------------------------------------
# Restore plan: validated backup values merged into the device settings
# ---------------------------------------------------------------------------

_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_GITHUB_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


def _item(key, **params):
    return {"key": key, "params": params}


def _new_id(used):
    while True:
        candidate = uuid.uuid4().hex[:12]
        if candidate not in used:
            used.add(candidate)
            return candidate


def _keep_or_new_id(raw_id, used):
    if isinstance(raw_id, str) and _ID_RE.match(raw_id) and raw_id not in used:
        used.add(raw_id)
        return raw_id
    return _new_id(used)


def _plan_print_rules(section, device, report, errors):
    pr = device.setdefault("print_rules", {})
    scalars = {k: section[k] for k in
               ("max_jobs_per_hour", "duplicate_window_seconds", "pending_retention_days", "text_size")
               if k in section}
    error = validate_print_rules_updates(scalars)
    if error:
        errors.append(_item(error[0], **error[1]))
        return
    pr.update(scalars)
    if "quiet_hours_rules" in section:
        rules = section["quiet_hours_rules"]
        if len(rules) > MAX_LIST_ENTRIES:
            errors.append(_item("backup.validation.too_many", field="print_rules.quiet_hours_rules"))
            return
        built, used = [], set()
        for raw in rules:
            error = validate_quiet_hours_rule(raw)
            if error:
                errors.append(_item(error[0], **error[1]))
                return
            rule = build_quiet_hours_rule(raw)
            rule["id"] = _keep_or_new_id(raw.get("id"), used)
            built.append(rule)
        pr["quiet_hours_rules"] = built
    report["sections"].append("print_rules")


def _plan_weather(section, device, report, errors):
    weather = device.setdefault("weather", {})
    locations = weather.get("locations", {})
    if "locations" in section:
        raw_locations = section["locations"]
        if not raw_locations or len(raw_locations) > MAX_LIST_ENTRIES:
            errors.append(_item("backup.validation.locations_count"))
            return
        locations = {}
        for raw_name, coords in raw_locations.items():
            if "lat" not in coords or "lon" not in coords:
                errors.append(_item("settings.validation.latlon_numbers"))
                return
            name, lat, lon, error_key, error_kwargs = validate_and_build_location(
                raw_name, coords["lat"], coords["lon"],
            )
            if error_key:
                errors.append(_item(error_key, **error_kwargs))
                return
            if name in locations:
                errors.append(_item("backup.validation.duplicate_location"))
                return
            locations[name] = {"lat": lat, "lon": lon}
    default = section.get("default_location", weather.get("default_location"))
    if default not in locations:
        if "default_location" in section:
            errors.append(_item("backup.validation.default_location"))
            return
        default = next(iter(locations), None)
    if "report_provider" in section and section["report_provider"] not in WEATHER_REPORT_PROVIDERS:
        errors.append(_item("settings.weather_provider.unsupported", provider=section["report_provider"]))
        return
    storm = section.get("storm_warning", {})
    if "provider" in storm and storm["provider"] not in STORM_WARNING_PROVIDERS:
        errors.append(_item("backup.validation.storm_provider"))
        return
    storm_clean = {}
    for key, value in storm.items():
        if isinstance(value, str):
            value = value.strip()
            if len(value) > MAX_STORM_TEXT_LEN:
                errors.append(_item("backup.validation.too_long", field=f"weather.storm_warning.{key}"))
                return
        storm_clean[key] = value

    weather["locations"] = locations
    if default is not None:
        weather["default_location"] = default
    if "report_provider" in section:
        weather["report_provider"] = section["report_provider"]
    weather.setdefault("storm_warning", {}).update(storm_clean)
    report["sections"].append("weather")


def _plan_simple(section_name, value, allowed, device, report, errors):
    if value not in allowed:
        errors.append(_item("backup.validation.value_invalid", field=section_name))
        return
    device[section_name] = value
    report["sections"].append(section_name)


def _plan_glass(section, device, report, errors):
    # Both fields are plain booleans, already type-checked by _filter();
    # a field missing from an older archive keeps the device's value.
    device.setdefault("glass", {}).update(section)
    report["sections"].append("glass")


def _plan_updates(section, device, report, errors):
    # A plain boolean, type-checked by _filter(); the updater still only
    # acts on it while an admin account exists.
    device.setdefault("updates", {}).update(section)
    report["sections"].append("updates")


def _plan_enabled_modules(section, device, report, errors):
    enabled = device.setdefault("enabled_modules", {})
    for key, value in section.items():
        if key not in module_catalog.MODULE_KEYS:
            report["ignored"].append(f"enabled_modules.{key}")
            continue
        enabled[key] = value
    report["sections"].append("enabled_modules")


def _plan_recipes(section, device, report, errors):
    recipes = device.setdefault("recipes", {})
    device_mealie = recipes.setdefault("mealie", {})
    has_token = bool(device_mealie.get("token_encrypted"))
    provider = section.get("provider", recipes.get("provider", "off"))
    if provider not in RECIPE_PROVIDERS:
        errors.append(_item("settings.recipes.provider_invalid"))
        return
    backup_mealie = section.get("mealie", {})
    url = device_mealie.get("base_url", "")
    if "base_url" in backup_mealie:
        raw_url = backup_mealie["base_url"].strip()
        new_url = mealie.normalize_base_url(raw_url) if raw_url else ""
        if new_url is None:
            errors.append(_item("settings.recipes.url_invalid"))
            return
        # Same rule as the settings form: the stored token only ever goes
        # to the URL it was entered for, so a token on the device pins
        # the device's URL.
        if has_token and new_url != url and not mealie.same_base_url(new_url, url):
            report["conflicts"].append(_item("backup.conflict.mealie_url"))
        else:
            url = new_url
    if provider == "mealie" and (not url or not has_token):
        # The settings form refuses "mealie" without URL + token, so the
        # restore doesn't leave that state behind either.
        provider = "off"
        report["reenter"].append(_item("backup.reenter.mealie_token"))
    elif backup_mealie.get("token_set") and not has_token:
        report["reenter"].append(_item("backup.reenter.mealie_token"))
    recipes["provider"] = provider
    device_mealie["base_url"] = url
    report["sections"].append("recipes")


def _host_label(entry):
    return entry.get("name") or entry.get("host") or entry.get("id", "")


def _plan_system_report(section, device, report, errors):
    sr = device.setdefault("system_report", {})
    if "hosts" not in section:
        return
    raw_hosts = section["hosts"]
    if len(raw_hosts) > MAX_LIST_ENTRIES:
        errors.append(_item("backup.validation.too_many", field="system_report.hosts"))
        return
    device_hosts = [h for h in sr.get("hosts", []) if isinstance(h, dict)]
    by_id = {h.get("id"): h for h in device_hosts}
    protected = {h.get("id"): h for h in device_hosts if h.get("password_encrypted")}

    new_hosts, used, kept_protected = [], set(), set()
    for raw in raw_hosts:
        role = raw.get("role", "")
        if role not in SYSTEM_REPORT_ROLES:
            errors.append(_item("settings.validation.system_report_role_invalid", role=role))
            return
        host, user, error_key, error_kwargs = validate_ssh_host_entry(raw.get("host"), raw.get("user"))
        if error_key:
            errors.append(_item(error_key, **error_kwargs))
            return
        if not host or not user:
            errors.append(_item("settings.validation.system_report_host_missing"))
            return
        # Same allowlist as the host form and API (see security.py).
        if not is_valid_ssh_host(host) or not is_valid_ssh_user(user):
            errors.append(_item("backup.validation.ssh_target"))
            return
        name = str(raw.get("name") or "").strip()[:MAX_TITLE_LEN]

        raw_id = raw.get("id")
        if raw_id in protected:
            # A host with a stored password stays exactly as it is on the
            # device - otherwise a changed host/user would send that
            # password to a different machine.
            device_entry = protected[raw_id]
            if raw_id not in kept_protected:
                new_hosts.append(device_entry)
                kept_protected.add(raw_id)
                used.add(raw_id)
            if (device_entry.get("host"), device_entry.get("user"), device_entry.get("role"), device_entry.get("name")) \
                    != (host, user, role, name):
                report["conflicts"].append(_item("backup.conflict.host_password", name=_host_label(device_entry)))
            continue

        entry = {
            "id": _keep_or_new_id(raw_id, used | set(protected)),
            "name": name,
            "role": role,
            "host": host,
            "user": user,
            "command": "",
            "password_encrypted": "",
        }
        used.add(entry["id"])
        # The command never comes from an archive. A device entry with the
        # same id and the same target keeps the command it already has.
        previous = by_id.get(raw_id)
        if (previous and role == "custom" and previous.get("role") == role
                and previous.get("host") == host and previous.get("user") == user):
            entry["command"] = previous.get("command", "")
        if role == "custom" and not entry["command"]:
            report["reenter"].append(_item("backup.reenter.host_command", name=_host_label(entry)))
        if raw.get("uses_password"):
            report["reenter"].append(_item("backup.reenter.host_password", name=_host_label(entry)))
        new_hosts.append(entry)

    for host_id, device_entry in protected.items():
        if host_id not in kept_protected:
            new_hosts.append(device_entry)
            report["conflicts"].append(_item("backup.conflict.host_kept", name=_host_label(device_entry)))
    sr["hosts"] = new_hosts
    report["sections"].append("system_report")


def _plan_github_watch(section, device, report, errors):
    gh = device.setdefault("github_watch", {})
    if "repos" not in section:
        return
    if len(section["repos"]) > MAX_LIST_ENTRIES:
        errors.append(_item("backup.validation.too_many", field="github_watch.repos"))
        return
    repos = []
    for raw in section["repos"]:
        owner, repo, error_key, error_kwargs = validate_github_repo(raw.get("owner"), raw.get("repo"))
        if error_key:
            errors.append(_item(error_key, **error_kwargs))
            return
        if not _GITHUB_NAME_RE.match(owner) or not _GITHUB_NAME_RE.match(repo):
            errors.append(_item("backup.validation.github_name"))
            return
        if {"owner": owner, "repo": repo} not in repos:
            repos.append({"owner": owner, "repo": repo})
    gh["repos"] = repos
    report["sections"].append("github_watch")


def _plan_logo_settings(section, device, report, errors):
    lg = device.setdefault("logos", {})
    if "enabled" in section:
        lg["enabled"] = section["enabled"]
    modules = lg.setdefault("modules", {})
    for key, value in section.get("modules", {}).items():
        if key not in logos.MODULE_KEYS:
            report["ignored"].append(f"logos.modules.{key}")
            continue
        if "enabled" in value:
            modules.setdefault(key, {})["enabled"] = value["enabled"]
    report["sections"].append("logos")


def _plan_mqtt(section, device, report, errors):
    mq = device.setdefault("mqtt", {})
    has_password = bool(mq.get("password_encrypted"))
    host = section.get("host", mq.get("host", "")).strip()
    port = section.get("port", mq.get("port", 1883))
    tls = section.get("tls", bool(mq.get("tls")))
    prefix = section.get("prefix", mq.get("prefix", ""))
    if host and not mqtt_bridge.is_valid_host(host):
        errors.append(_item("settings.mqtt.host_invalid"))
        return
    if not mqtt_bridge.is_valid_port(port):
        errors.append(_item("settings.mqtt.port_invalid"))
        return
    if not mqtt_bridge.is_valid_prefix(prefix):
        errors.append(_item("settings.mqtt.prefix_invalid"))
        return
    # Same rule as the settings form: a stored password only ever goes
    # to the broker it was entered for, and not without TLS if it was
    # entered with TLS - so it pins the device's broker settings.
    if has_password and (host != mq.get("host") or port != mq.get("port") or (mq.get("tls") and not tls)):
        report["conflicts"].append(_item("backup.conflict.mqtt_host"))
        host, port, tls = mq.get("host", ""), mq.get("port", 1883), bool(mq.get("tls"))
    enabled = section.get("enabled", bool(mq.get("enabled")))
    if enabled and not host:
        enabled = False
    if section.get("password_set") and not has_password:
        report["reenter"].append(_item("backup.reenter.mqtt_password"))
    mq.update({
        "enabled": enabled, "host": host, "port": port, "tls": tls, "prefix": prefix,
        "discovery": section.get("discovery", bool(mq.get("discovery"))),
    })
    report["sections"].append("mqtt")


def plan_restore(backup_settings, device_settings):
    """Merges the allowlisted, validated backup settings into a copy of
    `device_settings`. Returns (new_settings, report); report["errors"]
    non-empty means the archive must be refused as a whole - nothing of
    it is applied then.

    Never touches a secret: the Mealie token, host passwords, the MQTT
    password and every field outside SETTINGS_SCHEMA keep the device's
    value. The migrated_from_config flags are forced to true, so a
    restored file can never re-trigger the one-time copy from config.py."""
    device = json.loads(json.dumps(device_settings))
    report = {"sections": [], "ignored": [], "conflicts": [], "reenter": [], "errors": []}
    if backup_settings is None:
        return device, report

    type_errors = []
    filtered = _filter(backup_settings, SETTINGS_SCHEMA, "", report["ignored"], type_errors)
    for path in type_errors:
        report["errors"].append(_item("backup.validation.type", field=path))
    if report["errors"]:
        return device_settings, report

    errors = report["errors"]
    if "print_rules" in filtered:
        _plan_print_rules(filtered["print_rules"], device, report, errors)
    if "weather" in filtered:
        _plan_weather(filtered["weather"], device, report, errors)
    if "language" in filtered:
        _plan_simple("language", filtered["language"], i18n.SUPPORTED_LANGUAGES, device, report, errors)
    if "theme" in filtered:
        _plan_simple("theme", filtered["theme"], themes.SUPPORTED_THEMES, device, report, errors)
    if "glass" in filtered:
        _plan_glass(filtered["glass"], device, report, errors)
    if "updates" in filtered:
        _plan_updates(filtered["updates"], device, report, errors)
    if "enabled_modules" in filtered:
        _plan_enabled_modules(filtered["enabled_modules"], device, report, errors)
    if "recipes" in filtered:
        _plan_recipes(filtered["recipes"], device, report, errors)
    if "system_report" in filtered:
        _plan_system_report(filtered["system_report"], device, report, errors)
    if "github_watch" in filtered:
        _plan_github_watch(filtered["github_watch"], device, report, errors)
    if "logos" in filtered:
        _plan_logo_settings(filtered["logos"], device, report, errors)
    if "mqtt" in filtered:
        _plan_mqtt(filtered["mqtt"], device, report, errors)

    device.setdefault("system_report", {})["migrated_from_config"] = True
    device.setdefault("github_watch", {})["migrated_from_config"] = True
    if errors:
        return device_settings, report
    return device, report


def preview(path):
    """Inspection + plan against the current device settings, for the
    confirmation page. Raises ArchiveError for an invalid archive."""
    contents = inspect_archive(path)
    _, report = plan_restore(contents["settings"], settings_store.get_settings())
    return contents, report


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def _extract_logo(zf, slot, tmp_path):
    info = zf.getinfo(f"logos/{slot}.png")
    size = 0
    with zf.open(info) as src, open(tmp_path, "wb") as out:
        for chunk in iter(lambda: src.read(_CHUNK), b""):
            size += len(chunk)
            if size > MAX_LOGO_BYTES:
                raise ArchiveError("backup.error.member_too_large", name=info.filename)
            out.write(chunk)
        out.flush()
        os.fsync(out.fileno())


def apply_restore(path):
    """Applies a staged archive. Checks it again from scratch first (the
    staged file is never trusted on the strength of the earlier check),
    writes a secret-free snapshot of the current state, then swaps in
    the logos and finally the settings. Any failure on the way puts back
    the previous logo files and re-raises. settings.json needs no undo:
    it is written last, in one atomic replace (settings_store._write),
    so a failure either happens before it or leaves the old file intact.

    Not covered: a power loss between the individual file replacements
    can leave a mix of old and new files; the snapshot written here is
    the way back then.

    Returns (report, snapshot_name)."""
    if not _restore_lock.acquire(blocking=False):
        raise ArchiveError("backup.error.restore_running")
    try:
        contents = inspect_archive(path)
        _, report = plan_restore(contents["settings"], settings_store.get_settings())
        if report["errors"]:
            raise ArchiveError("backup.error.settings_invalid")

        snapshot_name = write_snapshot()

        staged, swapped = {}, []
        try:
            os.makedirs(logos.LOGOS_DIR, exist_ok=True)
            with zipfile.ZipFile(path) as zf:
                for slot in contents["logo_slots"]:
                    tmp_path = logos.slot_path(slot) + ".restore-tmp"
                    _extract_logo(zf, slot, tmp_path)
                    staged[slot] = tmp_path

            # Re-planned inside the settings transaction, against the
            # settings as they are at this moment - not as they were
            # when the confirmation page was rendered.
            final = {}

            def _mutate(data):
                new_settings, final_report = plan_restore(contents["settings"], data)
                if final_report["errors"]:
                    raise ArchiveError("backup.error.settings_invalid")
                data.clear()
                data.update(new_settings)
                final["report"] = final_report

            for slot, tmp_path in staged.items():
                target = logos.slot_path(slot)
                old_path = target + ".restore-old"
                had_old = os.path.isfile(target)
                if had_old:
                    os.replace(target, old_path)
                swapped.append((slot, had_old))
                os.replace(tmp_path, target)
            staged = {}

            settings_store.update_settings_transaction(_mutate)
        except BaseException:
            for tmp_path in staged.values():
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            for slot, had_old in reversed(swapped):
                target = logos.slot_path(slot)
                try:
                    if had_old:
                        os.replace(target + ".restore-old", target)
                    else:
                        os.remove(target)
                except OSError:
                    pass
            raise

        for slot, had_old in swapped:
            if had_old:
                try:
                    os.remove(logos.slot_path(slot) + ".restore-old")
                except OSError:
                    pass
        report = final["report"]
        report["logos"] = list(contents["logo_slots"])
        return report, snapshot_name
    finally:
        _restore_lock.release()
