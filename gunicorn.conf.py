"""
Gunicorn server hooks. on_starting() is guaranteed to run exactly ONCE
in the master process, before the workers are forked - regardless of
how many --workers are configured and not again when a worker is
restarted. The boot greeting is started here rather than from app.py,
because app.py gets imported separately by EVERY worker process and
would print again on every worker (re)start.

The master process itself never imports pyusb or python-escpos and never
opens the printer. pyusb creates one libusb context per process on first
use; fork() copies such a context into every worker, but only the
calling thread, so the copies lack libusb's own event thread. The
greeting therefore runs in a separate, short-lived interpreter
(boot_greeting.py), and the workers create their own libusb context
the first time they print.
"""
import os
import subprocess
import sys

# Enough for the interpreter start, the imports and one short receipt on
# a Pi Zero 2 W. on_starting() blocks the server start, so a hanging
# printer or USB call must not hold it up for longer than this.
_BOOT_GREETING_TIMEOUT_S = 30
_BOOT_GREETING_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "boot_greeting.py")


def _run_boot_greeting(server, cmd=None, timeout=_BOOT_GREETING_TIMEOUT_S):
    """Runs the greeting process and waits for it. Never raises: the
    greeting is "nice to have", so a missing printer, a failing or
    crashing child or a timeout only gets logged, and the server starts
    either way. On timeout subprocess.run() kills the child."""
    cmd = cmd or [sys.executable, _BOOT_GREETING_SCRIPT]
    try:
        result = subprocess.run(cmd, stdin=subprocess.DEVNULL, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        server.log.warning(f"Boot greeting: no result after {timeout}s, process killed")
        return
    except Exception as e:
        server.log.warning(f"Boot greeting could not be started: {e}")
        return
    if result.returncode != 0:
        server.log.warning(f"Boot greeting failed (exit status {result.returncode})")


def on_starting(server):
    _run_boot_greeting(server)
