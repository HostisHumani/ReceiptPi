"""
Installs a newer, signed ReceiptPi release next to the running one,
switches over, checks that the server starts, and goes back to the
previous release and its data if it doesn't.

Runs as root from systemd, with the system Python and only the standard
library, always as `python3 -I -E -s updater.py <command>`:

  run manual       one request from the web UI (receiptpi-update.path)
  run auto         the nightly run (receiptpi-update-auto.timer)
  recover          on boot (receiptpi-update-recover.service): finishes or
                   undoes an update that was interrupted, e.g. by a power loss
  rollback --yes   emergency route over SSH: back to the previous release
                   and to the data of the last backup
  status           prints the last result and an unfinished update, if any
  receipt          prints a result receipt that is still owed
  venv-key LOCK    name of the Python environment for a lock file; install.sh
                   uses it, so both name environments the same way

Layout:
  /opt/receiptpi                    relative symlink to the active release
  /opt/receiptpi-releases/<version> code of one release, owned by root
  /opt/receiptpi-venvs/<key>        Python environment, shared by every
                                    release with the same lock file; each
                                    release links to its own as "venv"
  /var/lib/receiptpi-update         status.json (readable for the service
                                    group), journal.json, backups/, download/

What is trusted, and why:
  - From the request file the web UI writes, only the action and the name
    of the account that asked are taken. The version and every path come
    from the release itself, so a compromised web application can at most
    ask for the newest signed release to be installed.
  - The release is GitHub's "latest" release of the project, which never
    is a prerelease or a draft. Files are only downloaded from the release
    download address of that tag.
  - The manifest is signed with Ed25519 and checked with the keys of the
    INSTALLED release (deploy/update/keys/*.pub), so a release can only be
    replaced by one signed with a key the device already trusts. The
    manifest names the package's size and checksum.
  - Only a version higher than the running one is installed.
  - A local test source, the test channel and a test key are only possible
    through /etc/receiptpi/update.conf, which must be owned by root and
    writable by nobody else. The web application cannot write there.

The update itself:
  1. Download, verify and unpack the new release, build its Python
     environment if the lock file changed. The device keeps running.
  2. Stop the server and its watchers, back up the data, the configuration
     and the units.
  3. Install the new units, switch the symlink, start the server and
     check it.
  4. On failure: the old units, the old symlink and the backed-up data
     come back. The data comes back too because the new version may
     already have migrated settings or databases on its first start, and
     the old version can't be expected to read them.
Every step that changes the system is recorded in journal.json first, so
an update interrupted at any point is finished or undone on the next boot.
"""
import ast
import datetime
import fcntl
import grp
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request

OWNER = "HostisHumani"
REPO = "ReceiptPi"
API_LATEST = f"https://api.github.com/repos/{OWNER}/{REPO}/releases/latest"
DOWNLOAD_PREFIX = f"https://github.com/{OWNER}/{REPO}/releases/download/"
USER_AGENT = "receiptpi-updater"

APP_LINK = "/opt/receiptpi"
RELEASES_DIR = "/opt/receiptpi-releases"
VENVS_DIR = "/opt/receiptpi-venvs"
CONF_DIR = "/etc/receiptpi"
UPDATE_CONF = "/etc/receiptpi/update.conf"
STATE_DIR = "/var/lib/receiptpi"
WORK_DIR = "/var/lib/receiptpi-update"
RUN_DIR = "/run/receiptpi-update"
UNIT_DIR = "/etc/systemd/system"
UDEV_DIR = "/etc/udev/rules.d"
UDEV_RULE = "99-receiptpi-printer.rules"
SYSTEM_PYTHON = "/usr/bin/python3"
OPENSSL = "/usr/bin/openssl"
SERVER_URL = "http://127.0.0.1:5000"
SERVICE = "receiptpi.service"
SERVICE_GROUP = "receiptpi"
# Files that decide what is installed must belong to this user.
ROOT_UID = 0
# Never stopped by an update: the nightly timer has to stay armed, and
# stopping it would not stop the run it started anyway.
KEEP_RUNNING_TIMERS = ("receiptpi-update-auto.timer",)

# The manifest format and the steps this updater knows. A release that
# needs a newer updater says so with "min_updater" and is refused with a
# reason the web UI can show, instead of being half installed.
MANIFEST_FORMAT = 1
UPDATER_LEVEL = 1

MAX_REQUEST_BYTES = 4096
MAX_SETTINGS_BYTES = 1024 * 1024
MAX_API_BYTES = 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
SIGNATURE_BYTES = 64
MAX_PACKAGE_BYTES = 200 * 1024 * 1024
MAX_BY_CHARS = 64
HTTP_TIMEOUT = 30
VENV_TIMEOUT = 30 * 60
COMMAND_TIMEOUT = 10 * 60
# The server start includes the boot greeting (up to 30 s, see
# gunicorn.conf.py) and the imports on a slow device.
HEALTH_TIMEOUT = 180
STABLE_SECONDS = 30
POLL_SECONDS = 2
RECEIPT_DELAY = "120s"
# The nightly window; it ends well before the automatic reboot of the
# system's own security upgrades (04:00, deploy/image/apt/).
AUTO_WINDOW = ((2, 0), (3, 30))
KEEP_BACKUPS = 2
# Free space kept on top of what an update is expected to need, and the
# expected size of a new Python environment.
SPACE_MARGIN = 200 * 1024 * 1024
VENV_ESTIMATE = 400 * 1024 * 1024
VENV_MARKER = ".receiptpi-complete"

VERSION_RE = re.compile(r"^(\d{1,4})\.(\d{1,4})\.(\d{1,4})$")
TAG_RE = re.compile(r"^v(\d{1,4}\.\d{1,4}\.\d{1,4})$")
APT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]{0,63}$")
PYTHON_RE = re.compile(r"^\d{1,2}\.\d{1,3}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MANIFEST_NAME_RE = re.compile(r"^receiptpi-(\d{1,4}\.\d{1,4}\.\d{1,4})\.manifest\.json$")

# Journal phases, in order. Up to "backup_done" the running system is
# unchanged apart from stopped services; from "switched" on, undoing
# means restoring the backup.
PHASE_STAGING = "staging"
PHASE_STOPPED = "stopped"
PHASE_BACKUP_DONE = "backup_done"
PHASE_SWITCHED = "switched"
PHASE_ROLLING_BACK = "rolling_back"
PHASE_DONE = "done"
RESTORE_PHASES = (PHASE_SWITCHED, PHASE_ROLLING_BACK)

# Results that get a receipt. "up_to_date" and "not_available" are the
# normal outcome of a check and print nothing.
RECEIPT_RESULTS = ("updated", "failed", "rolled_back", "rollback_failed")
QUIET_REASONS = ("no_admin", "busy", "request")


class UpdateError(Exception):
    """reason is a short key the web UI translates; detail is for the
    journal only."""

    def __init__(self, reason, detail=""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


class NoUpdate(Exception):
    """The check found nothing to install: result is "up_to_date" or
    "not_available"."""

    def __init__(self, result, version=""):
        super().__init__(result)
        self.result = result
        self.version = version


# ---------------------------------------------------------------- helpers

def log(message):
    print(message, flush=True)


def _now():
    return datetime.datetime.now().astimezone()


def _stamp():
    return _now().isoformat(timespec="seconds")


def _sleep(seconds):
    time.sleep(seconds)


def _monotonic():
    return time.monotonic()


def _run(cmd, timeout=COMMAND_TIMEOUT, env=None):
    """Runs a command, raises CalledProcessError/TimeoutExpired. Output
    goes to the journal through the inherited stdout/stderr."""
    return subprocess.run(cmd, check=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL)


def _capture(cmd, timeout=COMMAND_TIMEOUT):
    result = subprocess.run(cmd, check=True, timeout=timeout, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True)
    return result.stdout


def _systemctl(*args):
    _run(["systemctl", *args], timeout=COMMAND_TIMEOUT)


def _systemctl_quiet(*args):
    """For steps whose failure must not stop a rollback."""
    try:
        _systemctl(*args)
        return True
    except (subprocess.SubprocessError, OSError) as e:
        log(f"systemctl {' '.join(args)} failed: {e}")
        return False


def _service_props():
    out = _capture(["systemctl", "show", SERVICE,
                    "--property=ActiveState,SubState,MainPID,NRestarts"])
    props = {}
    for line in out.splitlines():
        key, _, value = line.partition("=")
        props[key] = value
    return props


def _active_timers():
    out = _capture(["systemctl", "list-units", "--type=timer", "--state=active",
                    "--plain", "--no-legend", "receiptpi-*"])
    names = []
    for line in out.splitlines():
        name = line.split(None, 1)[0] if line.strip() else ""
        if name.endswith(".timer") and name not in KEEP_RUNNING_TIMERS:
            names.append(name)
    return sorted(names)


def _ids():
    """(uid of root, gid of the service group): the status file is
    readable for the web application, everything else stays root's."""
    return 0, grp.getgrnam(SERVICE_GROUP).gr_gid


def _lchown(path, uid, gid):
    os.chown(path, uid, gid, follow_symlinks=False)


def _mkdir(path, mode):
    os.makedirs(path, exist_ok=True)
    os.chmod(path, mode)


def _remove(path):
    """Removes a file, a symlink or a directory tree without following
    symlinks; a missing path is fine."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISDIR(st.st_mode):
        shutil.rmtree(path)
    else:
        os.unlink(path)


def _write_atomic(path, data, mode, group_readable=False):
    tmp = f"{path}.tmp"
    _remove(tmp)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    if group_readable:
        uid, gid = _ids()
        _lchown(tmp, uid, gid)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _read_limited(path, limit):
    """Reads a regular file without following a symlink at its place and
    refuses anything larger than limit. For files the service user can
    replace, so a planted symlink or a huge file is never read as root."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
            raise ValueError(f"{path}: not a regular file of at most {limit} bytes")
        data = os.read(fd, limit + 1)
    finally:
        os.close(fd)
    if len(data) > limit:
        raise ValueError(f"{path}: larger than {limit} bytes")
    return data


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_version(text):
    m = VERSION_RE.match(text.strip()) if isinstance(text, str) else None
    return tuple(int(x) for x in m.groups()) if m else None


def python_version():
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def venv_key(lock_sha256, python=None):
    """Name of the Python environment for a lock file and a Python
    version. Packages are built for one Python version, so a new Python
    gets a new environment even with the same lock file."""
    python = python or python_version()
    text = f"receiptpi-venv\npython {python}\nlock {lock_sha256}\n"
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _work(*parts):
    return os.path.join(WORK_DIR, *parts)


def prepare_work_dir():
    """root:<service group> 0750: the web application may read the status
    file in it, nothing else (backups and downloads are 0700 below it)."""
    _mkdir(WORK_DIR, 0o750)
    uid, gid = _ids()
    _lchown(WORK_DIR, uid, gid)


# ---------------------------------------------------------------- status and journal

def read_json_file(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_status(**fields):
    """status.json: what the web UI shows. Readable for the service
    group, written atomically, only a few times per update."""
    data = {"updated_at": _stamp(), **fields}
    _write_atomic(_work("status.json"), json.dumps(data, sort_keys=True).encode(), 0o640,
                  group_readable=True)
    return data


def read_journal():
    return read_json_file(_work("journal.json"))


def write_journal(journal):
    journal["updated_at"] = _stamp()
    _write_atomic(_work("journal.json"), json.dumps(journal, sort_keys=True).encode(), 0o600)


def clear_journal():
    _remove(_work("journal.json"))


def _set_marker(active):
    """While an update runs, a file in the runtime directory tells the
    boot greeting not to print: only the result receipt should."""
    path = os.path.join(RUN_DIR, "active")
    if active:
        _mkdir(RUN_DIR, 0o755)
        with open(path, "w") as f:
            f.write("update\n")
        os.chmod(path, 0o644)
    else:
        _remove(path)


class Lock:
    """One update at a time: the button, the timer, the boot recovery and
    the SSH route all take this lock."""

    def __init__(self):
        self.fd = None

    def acquire(self):
        _mkdir(RUN_DIR, 0o755)
        self.fd = os.open(os.path.join(RUN_DIR, "lock"),
                          os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.fd)
            self.fd = None
            return False
        return True

    def release(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


# ---------------------------------------------------------------- inputs from the service user

def request_file():
    return os.path.join(STATE_DIR, "update-request.json")


def take_request():
    """Reads and removes the request file. Returns None without a file,
    {"valid": False} for anything unusable, otherwise
    {"valid": True, "by": <account name for the receipt>}. The file is
    removed in every case: the path unit starts this service as long as
    it exists."""
    try:
        data = _read_limited(request_file(), MAX_REQUEST_BYTES)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        log(f"request file unusable: {e}")
        data = None
    try:
        _remove(request_file())
    except OSError as e:
        log(f"request file could not be removed: {e}")
    if data is None:
        return {"valid": False}
    try:
        request = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {"valid": False}
    if not isinstance(request, dict) or request.get("action") != "install":
        return {"valid": False}
    by = request.get("by")
    by = by if isinstance(by, str) else ""
    by = "".join(c for c in by if c.isprintable())[:MAX_BY_CHARS]
    return {"valid": True, "by": by}


def admin_exists():
    """At least one admin account in auth.db, read-only. Updates are only
    offered once someone is responsible for the device: without an admin
    the web UI is open to everyone on the network."""
    path = os.path.join(STATE_DIR, "auth.db")
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return False
    except OSError:
        return False
    uri = "file:" + urllib.parse.quote(path) + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.Error:
        return False
    try:
        row = conn.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone()
    except sqlite3.Error:
        return False
    finally:
        conn.close()
    return row is not None


def auto_enabled():
    try:
        settings = json.loads(_read_limited(os.path.join(STATE_DIR, "settings.json"),
                                            MAX_SETTINGS_BYTES).decode("utf-8"))
    except (OSError, ValueError):
        return False
    updates = settings.get("updates") if isinstance(settings, dict) else None
    return isinstance(updates, dict) and updates.get("auto") is True


def in_auto_window(now=None):
    now = now or _now()
    (start_h, start_m), (end_h, end_m) = AUTO_WINDOW
    minutes = now.hour * 60 + now.minute
    return start_h * 60 + start_m <= minutes < end_h * 60 + end_m


def api_token():
    """API_TOKEN from the device configuration, "" if there is none. Read
    with ast instead of importing the file, so nothing in it runs here."""
    try:
        with open(os.path.join(CONF_DIR, "config.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except (OSError, SyntaxError, ValueError):
        return ""
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and getattr(node.targets[0], "id", None) == "API_TOKEN"):
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                return ""
            return value if isinstance(value, str) else ""
    return ""


# ---------------------------------------------------------------- configuration for tests

def load_update_conf():
    """Settings from /etc/receiptpi/update.conf, only for testing a
    release before it is published:

      source = /var/lib/receiptpi-update/test-source
      allow_test_channel = yes
      test_key = /etc/receiptpi/update-test.pub

    Without the file the updater only uses GitHub, the release channel
    and the keys of the installed release. A file that someone other
    than root could have written stops the update."""
    conf = {"source": "", "allow_test_channel": False, "test_key": ""}
    try:
        st = os.lstat(UPDATE_CONF)
    except FileNotFoundError:
        return conf
    if not stat.S_ISREG(st.st_mode) or st.st_uid != ROOT_UID or st.st_mode & 0o022:
        raise UpdateError("config", f"{UPDATE_CONF} must be a file owned by root, writable only by root")
    with open(UPDATE_CONF, encoding="utf-8") as f:
        for number, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, value = (part.strip() for part in line.partition("="))
            if not sep or key not in conf:
                raise UpdateError("config", f"{UPDATE_CONF}:{number}: unknown line")
            if key == "allow_test_channel":
                if value not in ("yes", "no"):
                    raise UpdateError("config", f"{UPDATE_CONF}:{number}: yes or no")
                conf[key] = value == "yes"
            else:
                if not os.path.isabs(value):
                    raise UpdateError("config", f"{UPDATE_CONF}:{number}: absolute path required")
                conf[key] = value
    for key in ("source", "test_key"):
        if conf[key]:
            _check_root_owned(conf[key])
    if conf["test_key"] and not conf["allow_test_channel"]:
        raise UpdateError("config", "test_key needs allow_test_channel = yes")
    return conf


def _check_root_owned(path):
    """The path and every directory above it owned by root and writable
    only by root, so nobody else can swap what is read there."""
    current = os.path.abspath(path)
    while True:
        try:
            st = os.lstat(current)
        except OSError as e:
            raise UpdateError("config", f"{current}: {e}") from e
        if stat.S_ISLNK(st.st_mode) or st.st_uid != ROOT_UID or st.st_mode & 0o022:
            raise UpdateError("config", f"{current} must be owned by root and writable only by root")
        if current == "/":
            return
        current = os.path.dirname(current)


# ---------------------------------------------------------------- layout

def current_release():
    """(version, release directory) of the active release. The symlink
    layout is required: replacing a plain code directory in place could
    not be undone."""
    if not os.path.islink(APP_LINK):
        raise UpdateError("layout", f"{APP_LINK} is not a symlink")
    target = os.path.realpath(APP_LINK)
    if os.path.dirname(target) != os.path.realpath(RELEASES_DIR) or not os.path.isdir(target):
        raise UpdateError("layout", f"{APP_LINK} does not point into {RELEASES_DIR}")
    try:
        with open(os.path.join(target, "VERSION"), encoding="utf-8") as f:
            version = f.read().strip()
    except OSError as e:
        raise UpdateError("layout", f"VERSION: {e}") from e
    if parse_version(version) is None:
        raise UpdateError("version", f"installed version {version!r} is not MAJOR.MINOR.PATCH")
    return version, target


def trusted_keys(release_dir):
    keys_dir = os.path.join(release_dir, "deploy", "update", "keys")
    try:
        names = sorted(n for n in os.listdir(keys_dir) if n.endswith(".pub"))
    except OSError:
        names = []
    return [os.path.join(keys_dir, n) for n in names]


def switch_link(release_dir):
    """Points APP_LINK at release_dir in one rename, so the link is never
    missing. Relative, so it also resolves in a mounted image."""
    rel = os.path.relpath(release_dir, os.path.dirname(APP_LINK))
    tmp = APP_LINK + ".new"
    _remove(tmp)
    os.symlink(rel, tmp)
    os.replace(tmp, APP_LINK)


# ---------------------------------------------------------------- finding and checking a release

class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """GitHub sends release downloads on to its file storage; a redirect
    may only ever lead to another https address."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise urllib.error.HTTPError(newurl, code, "redirect to a non-https address", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_url(url, accept):
    opener = urllib.request.build_opener(_HttpsOnlyRedirect())
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    return opener.open(req, timeout=HTTP_TIMEOUT)


def _download(url, limit, dest=None, accept="application/octet-stream"):
    """Bytes of url (or written to dest), at most limit bytes."""
    try:
        with _open_url(url, accept) as resp:
            if dest is None:
                data = resp.read(limit + 1)
                if len(data) > limit:
                    raise UpdateError("download", f"{url}: larger than {limit} bytes")
                return data
            size = 0
            fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            with os.fdopen(fd, "wb") as f:
                for chunk in iter(lambda: resp.read(1024 * 1024), b""):
                    size += len(chunk)
                    if size > limit:
                        raise UpdateError("download", f"{url}: larger than {limit} bytes")
                    f.write(chunk)
                f.flush()
                os.fsync(f.fileno())
            return None
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise NoUpdate("not_available") from e
        raise UpdateError("download", f"{url}: HTTP {e.code}") from e
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise UpdateError("download", f"{url}: {e}") from e


class GitHubSource:
    """The latest full release on GitHub. One API request per check."""

    def __init__(self):
        self.tag = ""
        self.assets = {}

    def latest(self):
        data = _download(API_LATEST, MAX_API_BYTES, accept="application/vnd.github+json")
        try:
            release = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            raise UpdateError("download", "the release list is not JSON") from e
        if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
            raise NoUpdate("not_available")
        tag = release.get("tag_name")
        m = TAG_RE.match(tag) if isinstance(tag, str) else None
        if not m:
            raise NoUpdate("not_available")
        self.tag = tag
        self.assets = {}
        for asset in release.get("assets") or []:
            if not isinstance(asset, dict):
                continue
            name, url = asset.get("name"), asset.get("browser_download_url")
            # Only the download address of exactly this tag and name.
            if isinstance(name, str) and url == f"{DOWNLOAD_PREFIX}{tag}/{name}":
                self.assets[name] = url
        return m.group(1)

    def fetch(self, name, limit, dest=None):
        url = self.assets.get(name)
        if url is None:
            raise NoUpdate("not_available")
        return _download(url, limit, dest)


class LocalSource:
    """A directory with release files, for tests before a release is
    published (update.conf "source"). The highest version in it counts."""

    def __init__(self, directory):
        self.directory = directory

    def latest(self):
        versions = []
        for name in os.listdir(self.directory):
            m = MANIFEST_NAME_RE.match(name)
            if m:
                versions.append((parse_version(m.group(1)), m.group(1)))
        if not versions:
            raise NoUpdate("not_available")
        return max(versions)[1]

    def fetch(self, name, limit, dest=None):
        path = os.path.join(self.directory, os.path.basename(name))
        try:
            if dest is None:
                return _read_limited(path, limit)
            # Copied in pieces: a package can be larger than what a small
            # device should hold in memory.
            src = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            with os.fdopen(src, "rb") as f:
                st = os.fstat(f.fileno())
                if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
                    raise UpdateError("download", f"{path}: not a file of at most {limit} bytes")
                fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
                             | os.O_CLOEXEC, 0o600)
                with os.fdopen(fd, "wb") as out:
                    shutil.copyfileobj(f, out, 1024 * 1024)
                    out.flush()
                    os.fsync(out.fileno())
            return None
        except FileNotFoundError as e:
            raise NoUpdate("not_available") from e
        except (OSError, ValueError) as e:
            raise UpdateError("download", str(e)) from e


def verify_signature(manifest_path, signature_path, keys):
    """The first key the signature verifies with, or None."""
    for key in keys:
        try:
            subprocess.run([OPENSSL, "pkeyutl", "-verify", "-rawin", "-pubin", "-inkey", key,
                            "-in", manifest_path, "-sigfile", signature_path],
                           check=True, timeout=60, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return key
        except (subprocess.SubprocessError, OSError):
            continue
    return None


def check_manifest(data, version, conf, test_key_used):
    try:
        manifest = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise UpdateError("manifest", "not JSON") from e
    if not isinstance(manifest, dict) or manifest.get("format") != MANIFEST_FORMAT:
        raise UpdateError("manifest", "unknown format")
    if manifest.get("name") != "receiptpi" or manifest.get("version") != version:
        raise UpdateError("manifest", f"names {manifest.get('version')!r}, expected {version!r}")
    channel = manifest.get("channel")
    if channel == "test":
        if not conf["allow_test_channel"]:
            raise UpdateError("channel", "a test package on a device without the test channel")
    elif channel != "release" or test_key_used:
        raise UpdateError("channel", f"channel {channel!r} with this key")
    package = manifest.get("package")
    if not isinstance(package, dict):
        raise UpdateError("manifest", "no package")
    if package.get("file") != f"receiptpi-{version}.tar.gz":
        raise UpdateError("manifest", "unexpected package file name")
    size = package.get("size")
    if not isinstance(size, int) or not 0 < size <= MAX_PACKAGE_BYTES:
        raise UpdateError("manifest", "package size")
    for value in (package.get("sha256"), manifest.get("lock_sha256")):
        if not isinstance(value, str) or not SHA256_RE.match(value):
            raise UpdateError("manifest", "checksum format")
    min_updater = manifest.get("min_updater", 1)
    if not isinstance(min_updater, int) or min_updater > UPDATER_LEVEL:
        raise UpdateError("updater", f"needs updater level {min_updater}")
    pythons = manifest.get("python")
    if pythons is not None:
        if not isinstance(pythons, list) or not all(isinstance(p, str) and PYTHON_RE.match(p)
                                                    for p in pythons):
            raise UpdateError("manifest", "python list")
        if python_version() not in pythons:
            raise UpdateError("python", f"needs Python {', '.join(pythons)}")
    apt = manifest.get("apt", [])
    if not isinstance(apt, list) or not all(isinstance(p, str) and APT_NAME_RE.match(p) for p in apt):
        raise UpdateError("manifest", "apt list")
    missing = [p for p in apt if not package_installed(p)]
    if missing:
        raise UpdateError("requirements", f"system packages missing: {', '.join(missing)}")
    return manifest


def package_installed(name):
    try:
        out = _capture(["dpkg-query", "-W", "-f=${Status}", name], timeout=60)
    except (subprocess.SubprocessError, OSError):
        return False
    return out.strip() == "install ok installed"


def _tree_size(path):
    total = 0
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            try:
                total += os.lstat(os.path.join(root, name)).st_blocks * 512
            except OSError:
                pass
    return total


def check_space(manifest):
    """Room for the download, the unpacked release, the backup and a new
    Python environment if one is needed, plus a margin. The sizes are
    estimates: a full card would break more than the update."""
    package = manifest["package"]["size"]
    need = 4 * package + _tree_size(STATE_DIR) + _tree_size(CONF_DIR) + SPACE_MARGIN
    if not os.path.isfile(os.path.join(VENVS_DIR, venv_key(manifest["lock_sha256"]), VENV_MARKER)):
        need += VENV_ESTIMATE
    for path in (WORK_DIR, RELEASES_DIR):
        free = shutil.disk_usage(path).free
        if free < need:
            raise UpdateError("space", f"{path}: {free} bytes free, {need} needed")


# ---------------------------------------------------------------- staging

REQUIRED_FILES = ("app.py", "VERSION", "deploy/requirements.lock", "deploy/update/updater.py",
                  "deploy/systemd/receiptpi.service")
FORBIDDEN_TOP = ("config.py", "secret.key", "settings.json", "venv")


def unpack(package_path, version):
    """Unpacks the package into RELEASES_DIR/.staging-<version> and
    returns that directory. The package holds exactly one directory,
    receiptpi-<version>/; the "data" filter refuses absolute paths, links
    leading outside, devices and special modes, and drops owners."""
    top = f"receiptpi-{version}"
    unpack_dir = os.path.join(RELEASES_DIR, f".unpack-{version}")
    staging = os.path.join(RELEASES_DIR, f".staging-{version}")
    _remove(unpack_dir)
    _remove(staging)
    os.mkdir(unpack_dir, 0o700)
    try:
        with tarfile.open(package_path, "r:gz") as tar:
            members = tar.getmembers()
            for member in members:
                if member.name != top and not member.name.startswith(top + "/"):
                    raise UpdateError("package", f"unexpected member {member.name!r}")
            tar.extractall(unpack_dir, members=members, filter="data")
    except (tarfile.TarError, OSError, EOFError) as e:
        _remove(unpack_dir)
        raise UpdateError("package", str(e)) from e
    except UpdateError:
        _remove(unpack_dir)
        raise
    if os.listdir(unpack_dir) != [top]:
        _remove(unpack_dir)
        raise UpdateError("package", "no single top directory")
    os.rename(os.path.join(unpack_dir, top), staging)
    _remove(unpack_dir)
    for rel in REQUIRED_FILES:
        if not os.path.isfile(os.path.join(staging, rel)):
            raise UpdateError("package", f"{rel} missing")
    for name in FORBIDDEN_TOP:
        if os.path.lexists(os.path.join(staging, name)):
            raise UpdateError("package", f"{name} must not be part of a release")
    with open(os.path.join(staging, "VERSION"), encoding="utf-8") as f:
        if f.read().strip() != version:
            raise UpdateError("package", "VERSION does not match the manifest")
    return staging


def build_venv(release_dir, lock_sha256):
    """The Python environment for the release's lock file: reused when one
    with the same lock file and Python version exists, otherwise built
    from the hashed lock file with prebuilt wheels only. Returns (key,
    built): built is True for a new environment."""
    lock_path = os.path.join(release_dir, "deploy", "requirements.lock")
    if _sha256_file(lock_path) != lock_sha256:
        raise UpdateError("package", "lock file does not match the manifest")
    key = venv_key(lock_sha256)
    target = os.path.join(VENVS_DIR, key)
    if os.path.isfile(os.path.join(target, VENV_MARKER)):
        return key, False
    _remove(target)
    # PIP_CONFIG_FILE=/dev/null ignores /etc/pip.conf, which can point pip
    # at an index whose files are not the ones the lock file's hashes name.
    env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "PIP_CONFIG_FILE": "/dev/null",
           "PIP_NO_INPUT": "1", "LANG": "C.UTF-8"}
    try:
        _run([SYSTEM_PYTHON, "-m", "venv", target], timeout=VENV_TIMEOUT, env=env)
        _run([os.path.join(target, "bin", "pip"), "install", "--no-input",
              "--disable-pip-version-check", "--no-cache-dir", "--require-hashes",
              "--only-binary=:all:", "--no-deps", "-r", lock_path],
             timeout=VENV_TIMEOUT, env=env)
    except (subprocess.SubprocessError, OSError) as e:
        _remove(target)
        raise UpdateError("venv", str(e)) from e
    with open(os.path.join(target, VENV_MARKER), "w") as f:
        f.write(f"python {python_version()}\nlock {lock_sha256}\n")
    own_tree(target)
    return key, True


def finish_release(staging, key):
    """Links the environment and the configuration, compiles the bytecode
    (the service can't write the code directory, so Python could not
    cache it) and makes everything root's and read-only for others."""
    os.symlink(os.path.relpath(os.path.join(VENVS_DIR, key), staging), os.path.join(staging, "venv"))
    os.symlink(os.path.join(CONF_DIR, "config.py"), os.path.join(staging, "config.py"))
    python = os.path.join(VENVS_DIR, key, "bin", "python3")
    try:
        _run([python, "-m", "compileall", "-q", "-x",
              "^" + re.escape(staging) + r"/(venv/|config\.py$)", staging])
    except (subprocess.SubprocessError, OSError) as e:
        raise UpdateError("package", f"compileall: {e}") from e
    own_tree(staging)


def own_tree(path):
    """root:root, directories and executables 0755, other files 0644;
    symlinks are left alone."""
    for root, dirs, files in os.walk(path):
        paths = [os.path.join(root, n) for n in dirs + files]
        if root == path:
            paths.insert(0, root)
        for p in paths:
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode):
                continue
            _lchown(p, 0, 0)
            executable = stat.S_ISDIR(st.st_mode) or st.st_mode & 0o100
            os.chmod(p, 0o755 if executable else 0o644)


# ---------------------------------------------------------------- backup and restore

def create_backup(journal):
    """Data, configuration, units and udev rule in one tar file, taken
    while the server is stopped, so the databases are consistent. Only
    root can read it: it holds config.py with the device's secret key."""
    backups = _work("backups")
    _mkdir(backups, 0o700)
    name = f"{_now().strftime('%Y%m%d-%H%M%S')}-{journal['from']}-to-{journal['to']}.tar"
    final = os.path.join(backups, name)
    partial = final + ".partial"
    _remove(partial)
    meta = json.dumps({"from": journal["from"], "to": journal["to"],
                       "release": journal["previous_release"], "created": _stamp()}).encode()
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "wb") as f:
        with tarfile.open(fileobj=f, mode="w", format=tarfile.PAX_FORMAT) as tar:
            info = tarfile.TarInfo("meta.json")
            info.size = len(meta)
            info.mode = 0o600
            tar.addfile(info, _BytesReader(meta))
            for prefix, directory, names in _backup_sources():
                for entry in names:
                    tar.add(os.path.join(directory, entry), arcname=f"{prefix}/{entry}",
                            recursive=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(partial, final)
    return final


class _BytesReader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def read(self, size=-1):
        if size is None or size < 0:
            size = len(self.data) - self.pos
        chunk = self.data[self.pos:self.pos + size]
        self.pos += len(chunk)
        return chunk


def _unit_files():
    try:
        names = os.listdir(UNIT_DIR)
    except FileNotFoundError:
        return []
    return sorted(n for n in names if n.startswith("receiptpi")
                  and stat.S_ISREG(os.lstat(os.path.join(UNIT_DIR, n)).st_mode))


def _backup_sources():
    def children(path):
        try:
            return sorted(os.listdir(path))
        except FileNotFoundError:
            return []
    udev = [UDEV_RULE] if os.path.lexists(os.path.join(UDEV_DIR, UDEV_RULE)) else []
    return [("state", STATE_DIR, children(STATE_DIR)),
            ("conf", CONF_DIR, children(CONF_DIR)),
            ("units", UNIT_DIR, _unit_files()),
            ("udev", UDEV_DIR, udev)]


def _restore_filter(member, dest):
    """The checks of the "data" filter (no absolute paths, no links that
    lead outside dest or to an absolute path, no device files) with the
    owners and modes of the "tar" filter, which the restore needs."""
    tarfile.data_filter(member, dest)
    return tarfile.tar_filter(member, dest)


def _extract_prefix(tar, members, prefix, dest):
    """Extracts the members below prefix/ into dest, keeping owners and
    modes. A hard link names another member, so its target loses the
    prefix too. A link leading outside dest can only have been planted
    in the data directory; it is left out instead of stopping the
    restore."""
    strip = len(prefix) + 1
    for member in members:
        if not member.name.startswith(prefix + "/") or len(member.name) <= strip:
            continue
        changes = {"name": member.name[strip:]}
        if member.islnk():
            if not member.linkname.startswith(prefix + "/"):
                continue
            changes["linkname"] = member.linkname[strip:]
        try:
            tar.extract(member.replace(**changes, deep=False), dest, filter=_restore_filter,
                        numeric_owner=True)
        except tarfile.FilterError as e:
            log(f"restore: left out {member.name}: {e}")


def _clear_dir(path):
    try:
        names = os.listdir(path)
    except FileNotFoundError:
        os.makedirs(path)
        return
    for name in names:
        _remove(os.path.join(path, name))


def restore_backup(backup_path):
    """Puts back the data directory exactly as backed up (anything the
    new version wrote or migrated is gone), the configuration files, the
    units and the udev rule. Unit files that were not there at backup
    time, i.e. added by the new version, are removed. Safe to repeat
    after an interruption."""
    with tarfile.open(backup_path, "r:") as tar:
        members = tar.getmembers()
        _clear_dir(STATE_DIR)
        _extract_prefix(tar, members, "state", STATE_DIR)
        _extract_prefix(tar, members, "conf", CONF_DIR)
        backed_up_units = {m.name.split("/", 1)[1] for m in members if m.name.startswith("units/")}
        for name in _unit_files():
            if name not in backed_up_units:
                _remove(os.path.join(UNIT_DIR, name))
        _extract_prefix(tar, members, "units", UNIT_DIR)
        _extract_prefix(tar, members, "udev", UDEV_DIR)


def backup_meta(backup_path):
    with tarfile.open(backup_path, "r:") as tar:
        f = tar.extractfile("meta.json")
        return json.loads(f.read().decode("utf-8"))


def list_backups():
    try:
        names = os.listdir(_work("backups"))
    except FileNotFoundError:
        return []
    return sorted(os.path.join(_work("backups"), n) for n in names if n.endswith(".tar"))


# ---------------------------------------------------------------- switching

def install_units(release_dir):
    """Copies the release's units and udev rule into place. Which units
    are enabled stays as it is."""
    src = os.path.join(release_dir, "deploy", "systemd")
    for name in sorted(os.listdir(src)):
        path = os.path.join(src, name)
        if name.startswith("receiptpi") and os.path.isfile(path):
            _copy_file(path, os.path.join(UNIT_DIR, name))
    rule = os.path.join(release_dir, "deploy", "udev", UDEV_RULE)
    if os.path.isfile(rule):
        _copy_file(rule, os.path.join(UDEV_DIR, UDEV_RULE))


def _copy_file(src, dest):
    tmp = dest + ".receiptpi-new"
    _remove(tmp)
    shutil.copyfile(src, tmp, follow_symlinks=False)
    _lchown(tmp, 0, 0)
    os.chmod(tmp, 0o644)
    os.replace(tmp, dest)


def reload_units():
    _systemctl("daemon-reload")
    try:
        _run(["udevadm", "control", "--reload-rules"], timeout=60)
    except (subprocess.SubprocessError, OSError) as e:
        log(f"udev rules not reloaded: {e}")


def stop_services():
    """Stops the server, the watcher timers and any watcher still running.
    Returns the timers that were active, to start exactly those again."""
    timers = _active_timers()
    _systemctl("stop", SERVICE, *timers)
    _systemctl_quiet("stop", "receiptpi-watcher@*.service")
    return timers


def start_timers(timers):
    if timers:
        _systemctl_quiet("start", *timers)


def _http_get(path):
    """(status, body) of a GET on the local server, without following
    redirects."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(NoRedirect())
    req = urllib.request.Request(SERVER_URL + path, headers={"User-Agent": USER_AGENT,
                                                             "Accept": "application/json"})
    try:
        with opener.open(req, timeout=10) as resp:
            return resp.status, resp.read(65536)
    except urllib.error.HTTPError as e:
        return e.code, e.read(65536)


def _answers():
    """The server answers as a running application: /health with its JSON
    (500 only means the printer is not attached) and /login rendered or
    redirected, which reads settings and accounts and so runs the
    migrations of the version."""
    try:
        status, body = _http_get("/health")
        if status not in (200, 500):
            return False
        health = json.loads(body.decode("utf-8"))
        if not isinstance(health, dict) or health.get("status") not in ("ok", "error"):
            return False
        status, _body = _http_get("/login")
        return status in (200, 302, 303)
    except (OSError, ValueError, urllib.error.URLError):
        return False


def start_and_check():
    """Starts the server and waits until it answers, then watches it for
    STABLE_SECONDS: same main process, no restart by systemd. Raises
    UpdateError("health") otherwise."""
    try:
        _systemctl("start", SERVICE)
    except (subprocess.SubprocessError, OSError) as e:
        raise UpdateError("health", f"start: {e}") from e
    deadline = _monotonic() + HEALTH_TIMEOUT
    baseline = None
    while True:
        props = _service_props()
        if baseline is None:
            baseline = props.get("NRestarts", "0")
        if props.get("ActiveState") == "failed":
            raise UpdateError("health", "the server failed to start")
        if (props.get("ActiveState") == "active" and props.get("SubState") == "running"
                and props.get("NRestarts", "0") == baseline and _answers()):
            break
        if _monotonic() >= deadline:
            raise UpdateError("health", f"no answer within {HEALTH_TIMEOUT} s")
        _sleep(POLL_SECONDS)
    pid = props.get("MainPID")
    _sleep(STABLE_SECONDS)
    props = _service_props()
    if (props.get("ActiveState") != "active" or props.get("MainPID") != pid
            or props.get("NRestarts", "0") != baseline or not _answers()):
        raise UpdateError("health", "the server did not keep running")


# ---------------------------------------------------------------- receipts

def _post_receipt(payload):
    """HTTP status of the receipt request; the server decides about quiet
    hours from "mode"."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(SERVER_URL + "/print/update-result", data=data, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "X-Api-Token": api_token(), "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def send_receipt(status):
    """Asks the running server to print the result. "printed", "held"
    (quiet hours: the job waits under pending prints) or "failed"."""
    payload = {k: status.get(k, "") for k in ("result", "reason", "from", "to", "mode", "by")}
    try:
        code = _post_receipt(payload)
    except (OSError, urllib.error.URLError, ValueError) as e:
        log(f"receipt not printed: {e}")
        return "failed"
    if code == 200:
        return "printed"
    if code == 429:
        return "held"
    log(f"receipt not printed: HTTP {code}")
    return "failed"


def schedule_receipt():
    """After a recovery during boot the server is not running yet; a
    one-time timer prints the receipt once it is."""
    try:
        _run(["systemd-run", "--unit=receiptpi-update-receipt", "--on-active=" + RECEIPT_DELAY,
              "--description=ReceiptPi: print the update result",
              SYSTEM_PYTHON, "-I", "-E", "-s", os.path.join(APP_LINK, "deploy", "update", "updater.py"),
              "receipt"], timeout=60)
    except (subprocess.SubprocessError, OSError) as e:
        log(f"receipt not scheduled: {e}")


def _same_night(status, previous):
    """The nightly run ending the same way for the same version as the
    last time. Most nights find nothing new; writing the same status
    every night would only wear the SD card, and a failure that repeats
    itself was already printed."""
    if status.get("mode") != "auto" or not previous:
        return False
    return all(previous.get(k) == status.get(k) for k in ("mode", "result", "reason", "to"))


def finish(status, print_receipt=True, previous=None, running_shown=True):
    """Writes the final status and prints its receipt when the result
    calls for one. previous is the status from before this run; while no
    "running" status was shown, a repeated nightly result is not written
    again."""
    same = _same_night(status, previous)
    if same and not running_shown:
        log("same result as the last nightly run, status left as it is")
        return {**status, "receipt": "none"}
    wants_receipt = (status.get("result") in RECEIPT_RESULTS
                     and status.get("reason") not in QUIET_REASONS
                     and not same)
    status["receipt"] = "none"
    if wants_receipt and print_receipt:
        status["receipt"] = send_receipt(status)
    elif wants_receipt:
        status["receipt"] = "pending"
    write_status(**status)
    return status


# ---------------------------------------------------------------- pruning

def prune(keep_releases):
    """Keeps the given release directories and removes the others,
    including unfinished ones; then removes every Python environment no
    kept release links to, and all but the newest KEEP_BACKUPS backups."""
    keep = {os.path.basename(os.path.realpath(p)) for p in keep_releases if p}
    for name in os.listdir(RELEASES_DIR):
        if name not in keep:
            _remove(os.path.join(RELEASES_DIR, name))
    prune_venvs()
    backups = list_backups()
    for path in backups[:-KEEP_BACKUPS] if KEEP_BACKUPS else backups:
        _remove(path)
    for name in os.listdir(_work("backups")) if os.path.isdir(_work("backups")) else []:
        if name.endswith(".partial"):
            _remove(os.path.join(_work("backups"), name))
    _remove(_work("download"))


# ---------------------------------------------------------------- the update

def prepare(conf, current_version, current_dir, on_found=None):
    """Finds, downloads, verifies and stages the new release while the
    device keeps running. Returns (version, staging dir, manifest).
    Raises NoUpdate or UpdateError. on_found(version) is called once a
    newer version is known."""
    source = LocalSource(conf["source"]) if conf["source"] else GitHubSource()
    version = source.latest()
    if parse_version(version) <= parse_version(current_version):
        raise NoUpdate("up_to_date", version)
    if on_found:
        on_found(version)
    keys = trusted_keys(current_dir)
    if conf["test_key"]:
        keys.append(conf["test_key"])
    if not keys:
        raise UpdateError("no_key", "no trusted key installed")
    download = _work("download")
    _remove(download)
    _mkdir(download, 0o700)
    manifest_path = os.path.join(download, f"receiptpi-{version}.manifest.json")
    signature_path = manifest_path + ".sig"
    try:
        manifest_data = source.fetch(os.path.basename(manifest_path), MAX_MANIFEST_BYTES)
        signature = source.fetch(os.path.basename(signature_path), SIGNATURE_BYTES)
    except NoUpdate as e:
        # Published, but the files are not attached yet: the release
        # workflow adds them a few minutes after the release appears.
        raise NoUpdate(e.result, version) from e
    if len(signature) != SIGNATURE_BYTES:
        raise UpdateError("signature", "signature has the wrong size")
    _write_atomic(manifest_path, manifest_data, 0o600)
    _write_atomic(signature_path, signature, 0o600)
    key = verify_signature(manifest_path, signature_path, keys)
    if key is None:
        raise UpdateError("signature", "no trusted key verifies the manifest")
    manifest = check_manifest(manifest_data, version, conf, key == conf["test_key"])
    check_space(manifest)
    package = manifest["package"]
    package_path = os.path.join(download, package["file"])
    source.fetch(package["file"], package["size"], dest=package_path)
    if os.path.getsize(package_path) != package["size"] or _sha256_file(package_path) != package["sha256"]:
        raise UpdateError("checksum", "the package does not match the manifest")
    staging = unpack(package_path, version)
    key_name, _built = build_venv(staging, manifest["lock_sha256"])
    finish_release(staging, key_name)
    return version, staging, manifest


def place_release(staging, version):
    """Moves the staged release to its final name. A leftover directory of
    the same version (an earlier attempt) is replaced; the active release
    never has this name, its version is lower."""
    final = os.path.join(RELEASES_DIR, version)
    _remove(final)
    os.rename(staging, final)
    return final


def switch_over(journal):
    """Stops, backs up, switches and checks. Each step is recorded before
    it starts. Raises on failure; the caller rolls back."""
    journal["timers"] = stop_services()
    journal["phase"] = PHASE_STOPPED
    write_journal(journal)
    journal["backup"] = create_backup(journal)
    journal["phase"] = PHASE_BACKUP_DONE
    write_journal(journal)
    # Recorded as switched before the first change, so an interruption
    # from here on restores the backup.
    journal["phase"] = PHASE_SWITCHED
    write_journal(journal)
    install_units(journal["new_release"])
    reload_units()
    switch_link(journal["new_release"])
    start_and_check()


def roll_back(journal, start):
    """Undoes an update according to the journal's phase. With start the
    server and the timers are started and checked (a run or the SSH
    route); without (during boot) systemd starts them afterwards.
    Returns True if the old version is back and, with start, healthy."""
    phase = journal.get("phase")
    restore = phase in RESTORE_PHASES
    journal["phase"] = PHASE_ROLLING_BACK if restore else journal.get("phase")
    write_journal(journal)
    if start:
        _systemctl_quiet("stop", SERVICE)
    ok = True
    if restore:
        try:
            backup = journal.get("backup")
            if not backup or not os.path.isfile(backup):
                raise UpdateError("backup", "backup missing")
            restore_backup(backup)
            switch_link(journal["previous_release"])
            reload_units()
        except (UpdateError, OSError, tarfile.TarError, KeyError) as e:
            log(f"rollback failed: {e}")
            ok = False
    if start and ok:
        try:
            start_and_check()
        except (UpdateError, subprocess.SubprocessError, OSError) as e:
            log(f"the previous version does not start either: {e}")
            ok = False
    if start:
        start_timers(journal.get("timers", []))
    new_release = journal.get("new_release")
    if ok and new_release and os.path.realpath(APP_LINK) != os.path.realpath(new_release):
        _remove(new_release)
        prune_venvs()
    _set_marker(False)
    return ok


def prune_venvs():
    used = set()
    for name in os.listdir(RELEASES_DIR):
        link = os.path.join(RELEASES_DIR, name, "venv")
        if os.path.islink(link):
            used.add(os.path.basename(os.readlink(link)))
    for name in os.listdir(VENVS_DIR):
        if name not in used:
            _remove(os.path.join(VENVS_DIR, name))


def run(mode):
    lock = Lock()
    request = take_request() if mode == "manual" else None
    if mode == "manual" and request is None:
        log("no request file, nothing to do")
        return 0
    if not lock.acquire():
        log("another update is running")
        return 0
    try:
        return _run_locked(mode, request)
    finally:
        lock.release()


def _run_locked(mode, request):
    base = {"mode": mode, "by": request["by"] if request and request.get("valid") else "",
            "started": _stamp(), "from": "", "to": ""}
    previous = read_json_file(_work("status.json"))
    shown = {"running": False}

    def show_running():
        write_status(**base, state="running", phase="preparing")
        shown["running"] = True

    def found(version):
        base["to"] = version
        if mode == "auto":
            show_running()

    if mode == "manual" and not request.get("valid"):
        finish({**base, "state": "done", "result": "failed", "reason": "request"})
        return 1
    if mode == "auto":
        if not auto_enabled():
            log("automatic updates are switched off")
            return 0
        if not in_auto_window():
            log("outside the window for automatic updates")
            return 0
    if not admin_exists():
        log("no admin account, updates are not available")
        if mode == "manual":
            finish({**base, "state": "done", "result": "failed", "reason": "no_admin"})
        return 0
    journal = read_journal()
    if journal:
        # An earlier run ended without finishing (killed or crashed while
        # the system was running): undo it before anything else.
        log(f"unfinished update in phase {journal.get('phase')}, undoing it first")
        recover(start=True)
    try:
        current_version, current_dir = current_release()
        base["from"] = current_version
        conf = load_update_conf()
        if mode == "manual":
            # Someone is waiting on the settings page; the nightly run
            # only shows itself once there is something to install.
            show_running()
        version, staging, _manifest = prepare(conf, current_version, current_dir, on_found=found)
        new_release = place_release(staging, version)
    except NoUpdate as e:
        log(f"no update: {e.result}")
        finish({**base, "to": e.version or base["to"], "state": "done", "result": e.result,
                "reason": ""},
               previous=previous, running_shown=shown["running"])
        _cleanup_staging()
        return 0
    except UpdateError as e:
        log(f"update not started: {e}")
        finish({**base, "state": "done", "result": "failed", "reason": e.reason},
               previous=previous, running_shown=shown["running"])
        _cleanup_staging()
        return 1
    except Exception as e:  # any bug here must leave the device as it was
        log(f"update not started, unexpected error: {e!r}")
        finish({**base, "state": "done", "result": "failed", "reason": "internal"},
               previous=previous, running_shown=shown["running"])
        _cleanup_staging()
        return 1

    journal = {**base, "phase": PHASE_STAGING, "previous_release": current_dir,
               "new_release": new_release, "timers": []}
    write_journal(journal)
    write_status(**base, state="running", phase="switching")
    _set_marker(True)
    try:
        switch_over(journal)
    except Exception as e:  # every failure after stopping is rolled back
        reason = e.reason if isinstance(e, UpdateError) else "switch"
        log(f"update failed ({e!r}), rolling back")
        ok = roll_back(journal, start=True)
        clear_journal()
        finish({**base, "state": "done", "result": "rolled_back" if ok else "rollback_failed",
                "reason": reason, "backup": os.path.basename(journal.get("backup") or "")})
        return 1
    journal["phase"] = PHASE_DONE
    write_journal(journal)
    start_timers(journal["timers"])
    _set_marker(False)
    status = finish({**base, "state": "done", "result": "updated", "reason": "",
                     "backup": os.path.basename(journal["backup"])})
    prune([new_release, current_dir])
    clear_journal()
    log(f"updated from {current_version} to {version}, receipt: {status['receipt']}")
    return 0


def _cleanup_staging():
    try:
        for name in os.listdir(RELEASES_DIR):
            if name.startswith((".staging-", ".unpack-")):
                _remove(os.path.join(RELEASES_DIR, name))
        prune_venvs()
    except OSError as e:
        log(f"cleanup: {e}")
    _remove(_work("download"))


def recover(start=False):
    """Finishes or undoes an unfinished update from its journal. During
    boot (start=False) nothing is started: systemd starts the server and
    the timers after this unit, and the receipt follows by a one-time
    timer."""
    journal = read_journal()
    if not journal:
        return 0
    phase = journal.get("phase")
    base = {k: journal.get(k, "") for k in ("mode", "by", "started", "from", "to")}
    log(f"unfinished update {base['from']} -> {base['to']} in phase {phase}")
    if phase == PHASE_DONE:
        clear_journal()
        prune([journal.get("new_release"), journal.get("previous_release")])
        _set_marker(False)
        return 0
    if phase in RESTORE_PHASES:
        ok = roll_back(journal, start=start)
        result = "rolled_back" if ok else "rollback_failed"
    else:
        # Nothing was switched yet: the old release and the data are
        # unchanged; only the services may still be stopped.
        if start:
            _systemctl_quiet("start", SERVICE)
            start_timers(journal.get("timers", []))
        new_release = journal.get("new_release")
        if new_release:
            _remove(new_release)
        backup = journal.get("backup")
        if phase == PHASE_STOPPED and backup:
            _remove(backup + ".partial")
        _set_marker(False)
        _cleanup_staging()
        result = "failed"
    clear_journal()
    status = finish({**base, "state": "done", "result": result, "reason": "interrupted",
                     "backup": os.path.basename(journal.get("backup") or "")},
                    print_receipt=start)
    if status["receipt"] == "pending":
        schedule_receipt()
    return 0


def rollback_cli():
    """The SSH emergency route: back to the release before the last update
    and to the data of the last backup, which was taken right before that
    update. Everything written since then is lost."""
    backups = list_backups()
    if not backups:
        log("no backup, nothing to roll back to")
        return 1
    backup = backups[-1]
    meta = backup_meta(backup)
    previous = meta.get("release")
    if not previous or not os.path.isdir(previous):
        log(f"the release of the backup ({previous}) is gone")
        return 1
    current_version, current_dir = current_release()
    if os.path.realpath(previous) == os.path.realpath(current_dir):
        log("the last update was already undone; the running release is the one of the backup")
        return 1
    journal = {"mode": "ssh", "by": "", "started": _stamp(), "from": current_version,
               "to": meta.get("from", ""), "phase": PHASE_SWITCHED, "backup": backup,
               "previous_release": previous, "new_release": current_dir,
               "timers": stop_services()}
    write_journal(journal)
    _set_marker(True)
    ok = roll_back(journal, start=True)
    clear_journal()
    finish({"mode": "ssh", "by": "", "started": journal["started"], "from": current_version,
            "to": journal["to"], "state": "done",
            "result": "rolled_back" if ok else "rollback_failed", "reason": "manual",
            "backup": os.path.basename(backup)})
    log("rolled back" if ok else "rollback failed, see the log above")
    return 0 if ok else 1


def receipt_cli():
    status = read_json_file(_work("status.json"))
    if not status or status.get("receipt") != "pending":
        return 0
    status["receipt"] = send_receipt(status)
    write_status(**{k: v for k, v in status.items() if k != "updated_at"})
    return 0


def main(argv):
    if len(argv) >= 2 and argv[0] == "venv-key":
        print(venv_key(_sha256_file(argv[1])))
        return 0
    if argv == ["status"]:
        for name in ("status.json", "journal.json"):
            data = read_json_file(_work(name))
            print(f"{name}: {json.dumps(data, indent=2, sort_keys=True) if data else 'none'}")
        return 0
    if os.geteuid() != 0:
        print("updater.py: must run as root", file=sys.stderr)
        return 2
    os.umask(0o022)
    prepare_work_dir()
    if len(argv) == 2 and argv[0] == "run" and argv[1] in ("manual", "auto"):
        return run(argv[1])
    lock = Lock()
    if argv == ["recover"]:
        if not lock.acquire():
            return 0
        try:
            return recover(start=False)
        finally:
            lock.release()
    if argv == ["receipt"]:
        return receipt_cli()
    if argv == ["rollback", "--yes"]:
        if not lock.acquire():
            print("an update is running", file=sys.stderr)
            return 1
        try:
            return rollback_cli()
        finally:
            lock.release()
    if argv == ["rollback"]:
        print("This restores the release before the last update and the data of the last\n"
              "backup; everything changed since that update is lost. Repeat with --yes.")
        return 1
    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
