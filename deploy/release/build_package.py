"""
Builds, signs and checks the update package of a ReceiptPi release, as
deploy/update/updater.py expects it. Used by .github/workflows/release.yml;
only the standard library, git and openssl.

  build_package.py keys   --keys deploy/update/keys
  build_package.py build  --ref HEAD --version 2.0.0 --channel release --out dist
  build_package.py sign   --manifest dist/receiptpi-2.0.0.manifest.json --key <private key>
  build_package.py verify --manifest dist/receiptpi-2.0.0.manifest.json --keys deploy/update/keys
  build_package.py sums   --out dist

build writes into --out:
  receiptpi-<version>.tar.gz          the files git tracks at --ref, below
                                      receiptpi-<version>/, with VERSION set
                                      to <version>
  receiptpi-<version>.manifest.json   what the updater checks (see below)
sign adds receiptpi-<version>.manifest.json.sig (Ed25519, 64 bytes, over the
manifest file), sums adds SHA256SUMS of every file in --out.

The package is reproducible: the same commit and version give the same
bytes. Every entry has the commit's time, owner root (0), mode 0755 for
directories and executables and 0644 otherwise, and gzip stores no name
and no time.

Manifest fields: format, name, version, channel ("release", or "test" for
packages that only devices with a test configuration accept), handoff
(the newest stage 1 / stage 2 handoff the package's own updater speaks),
package (file, size, sha256), lock_sha256, python (from the lock file's
header), apt (the system packages deploy/install.sh installs), commit and,
for a test package that fails to start on purpose, break_start.

Test packages with --break-start: the server writes the file
update-test-break-start into its data directory and then refuses to
start. A device updating to it has to fall back to the previous release
and its data, so the file is gone afterwards. Only on the test channel.
"""
import argparse
import ast
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile

MANIFEST_FORMAT = 1
VERSION_RE = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")
CHANNELS = ("release", "test")
# Tracked files that belong to the repository, not to an installation.
EXCLUDED_TOP = (".github", ".gitignore", ".gitattributes")
REQUIRED_FILES = ("app.py", "VERSION", "deploy/requirements.lock", "deploy/update/updater.py",
                  "deploy/systemd/receiptpi.service")
FORBIDDEN_TOP = ("config.py", "secret.key", "settings.json", "venv")
BREAK_MARKER = "update-test-break-start"
SIGNATURE_BYTES = 64
OPENSSL = "openssl"


def fail(message):
    print(f"build_package.py: {message}", file=sys.stderr)
    sys.exit(1)


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True).stdout


def sha256(data):
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- keys

def public_keys(keys_dir):
    """The *.pub files in keys_dir, each an Ed25519 public key in PEM form
    (the output of openssl pkey -pubout). Stops with a clear message when
    there is none: a release signed for no trusted key could never be
    installed."""
    try:
        names = sorted(n for n in os.listdir(keys_dir) if n.endswith(".pub"))
    except FileNotFoundError:
        names = []
    if not names:
        fail(f"no public signing key in {keys_dir}: add the public key(s) (*.pub, Ed25519, PEM) "
             "before building a release, see docs/developer-reference.md#updates")
    keys = []
    for name in names:
        path = os.path.join(keys_dir, name)
        text = subprocess.run([OPENSSL, "pkey", "-pubin", "-in", path, "-noout", "-text"],
                              capture_output=True, text=True, check=False)
        if text.returncode != 0 or not text.stdout.startswith("ED25519 Public-Key"):
            fail(f"{path} is not an Ed25519 public key in PEM form")
        der = subprocess.run([OPENSSL, "pkey", "-pubin", "-in", path, "-outform", "DER"],
                             check=True, capture_output=True).stdout
        keys.append((path, sha256(der)))
    return keys


def cmd_keys(args):
    for path, fingerprint in public_keys(args.keys):
        print(f"public key {os.path.basename(path)}: sha256 {fingerprint}")


# ---------------------------------------------------------------- build

def break_start(source):
    """app.py that writes the marker into the data directory and then
    fails, inserted right after the module docstring."""
    tree = ast.parse(source)
    first = tree.body[0]
    after = first.end_lineno if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                                 and isinstance(first.value.value, str)) else 0
    lines = source.splitlines(keepends=True)
    inject = (
        "# Test package (break_start): marks the data directory, then refuses to start.\n"
        "import os as _break_os\n"
        "import config as _break_config\n"
        "with open(_break_os.path.join(getattr(_break_config, 'STATE_DIR', '/var/lib/receiptpi'),\n"
        f"                              {BREAK_MARKER!r}), 'w') as _break_file:\n"
        "    _break_file.write('written by a test package that fails to start\\n')\n"
        "raise RuntimeError('test package: the server fails to start on purpose')\n"
    )
    return "".join(lines[:after]) + inject + "".join(lines[after:])


def read_members(repo, ref):
    """{path: (TarInfo, bytes or None)} of the files git tracks at ref."""
    data = git(repo, "archive", "--format=tar", ref)
    members = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
        for info in tar.getmembers():
            name = info.name.rstrip("/")
            if not name or name.split("/", 1)[0] in EXCLUDED_TOP:
                continue
            content = tar.extractfile(info).read() if info.isfile() else None
            members[name] = (info, content)
    return members


def manifest_facts(files):
    """handoff, python and apt, from the package's own files."""
    updater = ast.parse(files["deploy/update/updater.py"].decode("utf-8"))
    handoff = None
    for node in updater.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and getattr(node.targets[0], "id", None) == "SUPPORTED_HANDOFFS"):
            handoff = max(ast.literal_eval(node.value))
    if not isinstance(handoff, int):
        fail("deploy/update/updater.py names no SUPPORTED_HANDOFFS")
    facts = {"handoff": handoff}
    m = re.search(rb"with Python (\d+\.\d+)", files["deploy/requirements.lock"][:2000])
    if m:
        facts["python"] = [m.group(1).decode()]
    install = files.get("deploy/install.sh", b"").decode("utf-8")
    m = re.search(r"^APT_PACKAGES=\(([^)]*)\)", install, re.MULTILINE)
    facts["apt"] = m.group(1).split() if m else []
    return facts


def cmd_build(args):
    version, channel = args.version, args.channel
    if not VERSION_RE.match(version):
        fail(f"version {version!r} is not MAJOR.MINOR.PATCH, the only form the updater installs")
    if args.break_start and channel != "test":
        fail("--break-start is only allowed on the test channel")
    repo = os.path.abspath(args.repo)
    commit = git(repo, "rev-parse", f"{args.ref}^{{commit}}").decode().strip()
    mtime = int(git(repo, "log", "-1", "--format=%ct", commit).decode().strip())
    members = read_members(repo, commit)
    files = {name: content for name, (info, content) in members.items() if info.isfile()}
    for rel in REQUIRED_FILES:
        if rel not in files:
            fail(f"{rel} is not tracked at {args.ref}")
    for name in FORBIDDEN_TOP:
        if name in members:
            fail(f"{name} is tracked at {args.ref} and must not be part of a release")
    files["VERSION"] = f"{version}\n".encode()
    if args.break_start:
        files["app.py"] = break_start(files["app.py"].decode("utf-8")).encode("utf-8")

    top = f"receiptpi-{version}"
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:

        def add(name, info_type, mode, content=None, linkname=""):
            entry = tarfile.TarInfo(name)
            entry.type = info_type
            entry.mode = mode
            entry.mtime = mtime
            entry.uid = entry.gid = 0
            entry.uname = entry.gname = ""
            entry.linkname = linkname
            entry.size = len(content) if content is not None else 0
            tar.addfile(entry, io.BytesIO(content) if content is not None else None)

        add(top, tarfile.DIRTYPE, 0o755)
        for name in sorted(members):
            info, _content = members[name]
            path = f"{top}/{name}"
            if info.isdir():
                add(path, tarfile.DIRTYPE, 0o755)
            elif info.issym():
                add(path, tarfile.SYMTYPE, 0o777, linkname=info.linkname)
            elif info.isfile():
                add(path, tarfile.REGTYPE, 0o755 if info.mode & 0o100 else 0o644, files[name])
            else:
                fail(f"{name}: unsupported entry type in git archive")
    packed = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=packed, compresslevel=9, mtime=0) as gz:
        gz.write(raw.getvalue())
    package = packed.getvalue()

    os.makedirs(args.out, exist_ok=True)
    package_name = f"{top}.tar.gz"
    with open(os.path.join(args.out, package_name), "wb") as f:
        f.write(package)
    manifest = {
        "format": MANIFEST_FORMAT, "name": "receiptpi", "version": version, "channel": channel,
        "package": {"file": package_name, "size": len(package), "sha256": sha256(package)},
        "lock_sha256": sha256(files["deploy/requirements.lock"]), "commit": commit,
        **manifest_facts(files),
    }
    if args.break_start:
        manifest["break_start"] = True
    with open(os.path.join(args.out, f"{top}.manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"{package_name}: {len(package)} bytes, sha256 {manifest['package']['sha256']}, "
          f"channel {channel}, handoff {manifest['handoff']}, commit {commit}")


# ---------------------------------------------------------------- sign and verify

def cmd_sign(args):
    """Signs the manifest. Prints nothing about the key."""
    signature = args.manifest + ".sig"
    result = subprocess.run([OPENSSL, "pkeyutl", "-sign", "-rawin", "-inkey", args.key,
                             "-in", args.manifest, "-out", signature],
                            capture_output=True, stdin=subprocess.DEVNULL, check=False)
    if result.returncode != 0:
        fail("signing failed: the key must be an Ed25519 private key in PEM form")
    if os.path.getsize(signature) != SIGNATURE_BYTES:
        os.unlink(signature)
        fail("the signature is not 64 bytes: the key is not an Ed25519 key")
    print(f"signed {os.path.basename(args.manifest)}")


def cmd_verify(args):
    """Checks the signature against the public keys in --keys, the way the
    updater does on the device, and that the manifest matches its
    package."""
    signature = args.manifest + ".sig"
    if not os.path.isfile(signature) or os.path.getsize(signature) != SIGNATURE_BYTES:
        fail(f"{signature} is missing or not 64 bytes")
    verified = None
    for path, fingerprint in public_keys(args.keys):
        result = subprocess.run([OPENSSL, "pkeyutl", "-verify", "-rawin", "-pubin", "-inkey", path,
                                 "-in", args.manifest, "-sigfile", signature],
                                capture_output=True, stdin=subprocess.DEVNULL, check=False)
        if result.returncode == 0:
            verified = (path, fingerprint)
            break
    if verified is None:
        fail("the signature does not verify with any public key in "
             f"{args.keys}: the signing secret and the public keys don't belong together")
    with open(args.manifest, "rb") as f:
        manifest = json.loads(f.read().decode("utf-8"))
    package = os.path.join(os.path.dirname(args.manifest), manifest["package"]["file"])
    with open(package, "rb") as f:
        data = f.read()
    if len(data) != manifest["package"]["size"] or sha256(data) != manifest["package"]["sha256"]:
        fail(f"{package} does not match the manifest")
    if args.channel and manifest.get("channel") != args.channel:
        fail(f"channel {manifest.get('channel')!r}, expected {args.channel!r}")
    print(f"signature verified with {os.path.basename(verified[0])} (sha256 {verified[1]}), "
          f"package matches the manifest")


def cmd_sums(args):
    names = sorted(n for n in os.listdir(args.out) if n != "SHA256SUMS")
    lines = []
    for name in names:
        with open(os.path.join(args.out, name), "rb") as f:
            lines.append(f"{sha256(f.read())}  {name}\n")
    with open(os.path.join(args.out, "SHA256SUMS"), "w") as f:
        f.writelines(lines)
    print("".join(lines), end="")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build, sign and check a ReceiptPi update package.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("keys")
    p.add_argument("--keys", required=True)
    p = sub.add_parser("build")
    p.add_argument("--repo", default=".")
    p.add_argument("--ref", default="HEAD")
    p.add_argument("--version", required=True)
    p.add_argument("--channel", required=True, choices=CHANNELS)
    p.add_argument("--break-start", action="store_true")
    p.add_argument("--out", required=True)
    p = sub.add_parser("sign")
    p.add_argument("--manifest", required=True)
    p.add_argument("--key", required=True)
    p = sub.add_parser("verify")
    p.add_argument("--manifest", required=True)
    p.add_argument("--keys", required=True)
    p.add_argument("--channel", choices=CHANNELS)
    p = sub.add_parser("sums")
    p.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    {"keys": cmd_keys, "build": cmd_build, "sign": cmd_sign, "verify": cmd_verify,
     "sums": cmd_sums}[args.command](args)


if __name__ == "__main__":
    main()
