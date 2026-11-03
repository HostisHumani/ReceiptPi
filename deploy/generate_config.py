"""
Creates the device configuration (/etc/receiptpi/config.py) on the first
start, from config.example.py with a fresh random SECRET_KEY.

Runs as root from receiptpi-firstboot.service, before the server starts.
Does nothing if the configuration already exists, so it never replaces
a key or a value someone set by hand. The file is written next to its
final path and only renamed into place once it is complete, so an
interrupted run (power loss on the first boot) leaves either no file or
a whole one, and the next boot simply tries again.

Every device gets its own SECRET_KEY: secrets.token_hex() reads from
getrandom(), which blocks until the kernel's random pool is initialised,
so even a very early first boot cannot produce a predictable key. The
key is never printed or logged.

Only the standard library is used: this runs with the system Python,
independent of the application's virtual environment.
"""
import argparse
import ast
import grp
import os
import secrets
import sys
import tempfile

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TEMPLATE = os.path.join(PROJECT_DIR, "config.example.py")
DEFAULT_TARGET = "/etc/receiptpi/config.py"
DEFAULT_GROUP = "receiptpi"

HEADER = (
    "# Device configuration, created on the first start from config.example.py.\n"
    "# SECRET_KEY was generated for this device only. Edit as root and restart\n"
    "# the service afterwards: sudo systemctl restart receiptpi\n"
)


def _overrides():
    """Values that differ from config.example.py. The example's SSH hosts
    and GitHub repos are sample entries: settings_store copies them into
    settings.json once on the first start, so a new device would start
    with system report targets and a watched repo that don't exist.
    Empty values leave both lists empty until they are set in the web
    UI. Each value is Python source text."""
    return {
        "SECRET_KEY": repr(secrets.token_hex(32)),
        "SSH_PROXMOX_HOST": '""',
        "SSH_PINAS_HOST": '""',
        "SSH_PBS_HOST": '""',
        "GITHUB_REPOS": "[]",
    }


def render(template_source, overrides):
    """Returns template_source with the top-level assignment of every
    name in overrides replaced by "NAME = value". Comments and all other
    lines stay as they are, so the device file keeps the explanations
    from the example. Raises ValueError if a name is missing or assigned
    more than once, so a changed config.example.py fails loudly here
    instead of silently producing a file without a secret key."""
    tree = ast.parse(template_source)
    spans = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in overrides:
                if target.id in spans:
                    raise ValueError(f"{target.id} is assigned more than once in the template")
                spans[target.id] = (node.lineno, node.end_lineno)
    missing = sorted(set(overrides) - set(spans))
    if missing:
        raise ValueError("missing in the template: " + ", ".join(missing))

    lines = template_source.splitlines(keepends=True)
    # Bottom-up, so the line numbers of the spans not yet replaced stay valid.
    for name, (start, end) in sorted(spans.items(), key=lambda item: item[1][0], reverse=True):
        lines[start - 1:end] = [f"{name} = {overrides[name]}\n"]
    return HEADER + "".join(lines)


def _check(source, overrides):
    """Executes the rendered file and compares the result with the
    overrides, so only a file that loads and holds the new values is
    ever put in place."""
    namespace = {}
    exec(compile(source, "config.py", "exec"), namespace)  # noqa: S102 - our own rendered template
    for name, value in overrides.items():
        if namespace.get(name) != ast.literal_eval(value):
            raise ValueError(f"{name} does not have the expected value after rendering")


def write_config(target, source, gid):
    """Writes source to target with mode 0640, owner root and group gid.
    The temporary file is created with mode 0600 by mkstemp(), so the
    secret is never readable by others, not even for a moment."""
    directory = os.path.dirname(target)
    os.makedirs(directory, exist_ok=True)
    # Explicitly, since makedirs() is subject to the umask: the service
    # user reaches the file only through this directory.
    os.chown(directory, 0, gid)
    os.chmod(directory, 0o750)
    fd, tmp_path = tempfile.mkstemp(prefix=".config.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(source)
            f.flush()
            os.fchown(f.fileno(), 0, gid)
            os.fchmod(f.fileno(), 0o640)
            os.fsync(f.fileno())
        # link() instead of rename(): it fails if target appeared in the
        # meantime, so an existing configuration is never replaced.
        os.link(tmp_path, target)
    finally:
        os.unlink(tmp_path)
    dir_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--template", default=DEFAULT_TEMPLATE)
    parser.add_argument("--group", default=DEFAULT_GROUP)
    args = parser.parse_args(argv)

    if os.path.lexists(args.target):
        print(f"{args.target} already exists, left unchanged")
        return 0

    with open(args.template, encoding="utf-8") as f:
        template_source = f.read()
    overrides = _overrides()
    source = render(template_source, overrides)
    _check(source, overrides)
    write_config(args.target, source, grp.getgrnam(args.group).gr_gid)
    print(f"{args.target} created with a new SECRET_KEY")
    return 0


if __name__ == "__main__":
    sys.exit(main())
