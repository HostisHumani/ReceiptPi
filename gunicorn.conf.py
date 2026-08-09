"""
Gunicorn server hooks. on_starting() is guaranteed to run exactly ONCE
in the master process, before the workers are forked - regardless of
how many --workers are configured. The boot greeting belongs here
rather than in app.py, because app.py gets imported separately by EVERY
worker process and would print multiple times if --workers were ever
accidentally set to > 1 (e.g. through a later config change).
"""
import socket

import history_store
import i18n
from modules.message.routes import _raw_print_message
from printer import get_local_ip


def on_starting(server):
    """The boot greeting runs BEFORE the worker processes are forked,
    and therefore also before print_queue.start_worker() - so the print
    goes out directly via _raw_print_message() here instead of through
    enqueue_print()/the central queue. For the same reason,
    history_store.log_job() is called directly here too, instead of
    relying on the usual logging spot in the worker (_print_worker in
    print_queue.py).

    i18n.load_translations() must be called explicitly here too: this
    hook fires before Gunicorn even imports app.py (which is where
    load_translations() normally runs, once, at module level) - without
    this call, i18n.tr() below would find an empty translation table
    and print the raw keys ("receipt.boot.title") instead of real
    text."""
    i18n.load_translations()
    hostname = socket.gethostname()
    try:
        ip = get_local_ip()
        text = i18n.tr("receipt.boot.body", hostname=hostname, ip=ip)
        _raw_print_message(i18n.tr("receipt.boot.title"), text, use_text_scale=False)
    except Exception as e:
        # The boot greeting is "nice to have" - an unreachable printer
        # must not prevent the server from starting.
        server.log.warning(f"Boot greeting could not be printed: {e}")
        history_store.log_job("boot", hostname, "system", "error", str(e))
        return
    history_store.log_job("boot", f"{hostname} ({ip})", "system", "ok")
