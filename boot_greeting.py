"""
Boot greeting: prints the "server started" receipt with hostname and IP.

Runs as its own short-lived process, started once per service start
from gunicorn.conf.py's on_starting() hook (never by the workers, so a
worker restart doesn't print it again). It is a separate interpreter
rather than a function in the Gunicorn master because the master must
never load pyusb or python-escpos - see gunicorn.conf.py.

The greeting bypasses the print queue: it runs before any worker (and
therefore before print_queue's worker thread) exists, so nothing else
can be using the printer. For the same reason the print history entry
is written here directly instead of by print_queue._print_worker.

Exit status: 0 printed or skipped during an update, 1 the printer could
not be used (logged to the print history and stderr).
"""
import os
import socket
import sys

import history_store
import i18n
from modules.message.routes import _raw_print_message
from printer import get_local_ip

# Present while the updater stops and starts the server: the update ends
# with a receipt of its own, so the greeting would only add paper.
UPDATE_MARKER = "/run/receiptpi-update/active"


def main():
    if os.path.exists(UPDATE_MARKER):
        print("Boot greeting skipped: an update is running")
        return 0
    # app.py, which normally loads the translations, is never imported
    # in this process.
    i18n.load_translations()
    hostname = socket.gethostname()
    try:
        ip = get_local_ip()
        text = i18n.tr("receipt.boot.body", hostname=hostname, ip=ip)
        _raw_print_message(i18n.tr("receipt.boot.title"), text, use_text_scale=False)
    except Exception as e:
        # "Nice to have": the caller only logs a non-zero exit status, the
        # server starts either way.
        print(f"Boot greeting could not be printed: {e}", file=sys.stderr)
        history_store.log_job("boot", hostname, "system", "error", str(e))
        return 1
    history_store.log_job("boot", f"{hostname} ({ip})", "system", "ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
