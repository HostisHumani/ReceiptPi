"""Gunicorn configuration and lifecycle hooks.

on_starting() runs once in the Gunicorn master process before workers are
forked. Keeping the boot greeting here prevents duplicate prints if the worker
count is ever increased accidentally."""
import socket

from modules.message.routes import _raw_print_message
from printer import get_local_ip


def on_starting(server):
    try:
        hostname = socket.gethostname()
        ip = get_local_ip()
        text = f"Hostname: {hostname}\nIP: {ip}\nReceiptPi-Server gestartet"
        _raw_print_message("ONLINE", text)
    except Exception as e:
        # The boot greeting is optional; an unavailable printer must not block startup.
        #
        server.log.warning(f"Boot-Gruß konnte nicht gedruckt werden: {e}")
