"""
Module: system report - largely uses the same SSH commands as the
existing Termux Lab Commander (scripts/termux-lab-commander.sh in the
HomeLab repo): CPU temp/load, RAM, NVMe disk space, LXC/VM list, PBS
backups, and update check (the Commander's version doesn't cover PBS,
this one does). The general OMV Docker status (fetch_docker_status) is NOT taken from the
Commander - that only checks Frigate on a separate host - it's added
here independently for all OMV containers. Deliberately no Zabbix, to
avoid needing extra item/template configuration.
"""
import json
import subprocess
from datetime import datetime

from flask import Blueprint, jsonify, render_template

import i18n
import settings_store
from modules.message.routes import _raw_print_message
from print_queue import enqueue_print
from security import csrf_protect, get_csrf_token, require_api_token

system_bp = Blueprint("system", __name__)


def _ssh_target(role):
    """Returns (user, host) for one of the three fixed roles
    ("proxmox", "pinas", "pbs"), read from settings.json (see
    settings_store.py, migrated once from config.py on first run)."""
    entry = settings_store.get_settings()["system_report"]["ssh_hosts"][role]
    return entry["user"], entry["host"]


def ssh_run(user, host, remote_command, timeout=10):
    """Runs a command via SSH on another host. Requires the ReceiptPi
    Pi to be able to log in there passwordlessly via key. Returns
    stdout as a string, raises an exception on errors/timeout."""
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


def fetch_pve_status():
    """CPU temp, CPU load, RAM on the Proxmox host - identical commands
    to the Termux Commander (check_status())."""
    user, host = _ssh_target("proxmox")
    lines = []
    cpu_temp = ssh_run(user, host,
                        "cat /sys/class/thermal/thermal_zone0/temp | awk '{printf \"%.0f\", $1/1000}'")
    cpu_load = ssh_run(user, host,
                        "top -bn1 | grep 'Cpu' | awk '{printf \"%.0f\", 100-$8}'")
    ram = ssh_run(user, host,
                  "free -h | awk '/^Mem:/{print $3\"/\"$2}'")
    lines.append(i18n.tr("receipt.system.cpu_temp", value=cpu_temp))
    lines.append(i18n.tr("receipt.system.cpu_load", value=cpu_load))
    lines.append(i18n.tr("receipt.system.ram", value=ram))
    return lines


def fetch_lxc_vm_status():
    """LXC and VM list from the Proxmox host (pct list / qm list)."""
    user, host = _ssh_target("proxmox")
    lines = []
    lxc_output = ssh_run(user, host, "pct list")
    for line in lxc_output.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 3:
            lines.append(f"LXC {parts[0]} ({parts[2]}): {parts[1]}")

    vm_output = ssh_run(user, host, "qm list")
    for line in vm_output.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 3:
            lines.append(f"VM {parts[0]} ({parts[1]}): {parts[2]}")

    return lines or [i18n.tr("receipt.system.no_lxc_vms")]


def fetch_omv_status():
    """NVMe disk space on piNAS (OMV) - identical command to the Termux
    Commander."""
    user, host = _ssh_target("pinas")
    disk = ssh_run(user, host,
                    "df -h /dev/nvme0n1p2 | awk 'NR==2{print $3\"/\"$2\" (\"$5\")\"}'")
    return [i18n.tr("receipt.system.nvme", value=disk)]


def fetch_docker_status():
    """Docker container status via SSH directly from piNAS/OMV (docker
    ps). Unlike the other fetch_* functions, NOT taken 1:1 from the
    Termux Commander - that only checks Frigate on a separate host, not
    a general OMV Docker stack."""
    user, host = _ssh_target("pinas")
    output = ssh_run(
        user, host,
        "docker ps --format '{{.Names}}: {{.Status}}'",
    )
    return output.splitlines() if output else [i18n.tr("receipt.system.no_containers")]


def fetch_pbs_recent_backups(limit=5):
    """Most recent PBS backup tasks (Backup/Sync/Prune/Verify/GC),
    analogous to the Python evaluation in the Termux Commander, parsed
    here locally instead of remotely via an embedded Python call."""
    user, host = _ssh_target("pbs")
    output = ssh_run(user, host,
                      "proxmox-backup-manager task list --all --output-format json-pretty")
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


def fetch_updates_for_host(label, user, host):
    """Returns update lines for one host. With few open updates
    (<= UPDATE_LIST_THRESHOLD), the package names are listed
    individually; above that, just the count."""
    # "|| true" is necessary here: grep exits 1 when it finds NO matching
    # lines - which simply means "no updates available", not an actual
    # error. Without it, ssh_run() would treat that as a failed SSH
    # command (any non-zero exit code) and raise, even though nothing
    # went wrong.
    output = ssh_run(user, host, "apt list --upgradable 2>/dev/null | grep -v '^Listing' || true")
    if not output:
        return [i18n.tr("receipt.system.host_current", label=label)]

    packages = [line.split("/")[0] for line in output.splitlines() if line.strip()]
    count = len(packages)
    if count <= UPDATE_LIST_THRESHOLD:
        lines = [i18n.tr("receipt.system.host_updates_count", label=label, count=count)]
        lines.extend(f"  - {p}" for p in packages)
        return lines
    return [i18n.tr("receipt.system.host_updates_available", label=label, count=count)]


def fetch_update_counts():
    """Update status for PVE, OMV and PBS, like the Termux Commander's
    'u) Update-Check' - PBS wasn't covered there, added here."""
    lines = []
    pve_user, pve_host = _ssh_target("proxmox")
    pinas_user, pinas_host = _ssh_target("pinas")
    pbs_user, pbs_host = _ssh_target("pbs")
    lines.extend(fetch_updates_for_host("PVE", pve_user, pve_host))
    lines.extend(fetch_updates_for_host("OMV", pinas_user, pinas_host))
    lines.extend(fetch_updates_for_host("PBS", pbs_user, pbs_host))
    return lines


def _raw_print_system_report():
    report_lines = []

    sections = [
        (i18n.tr("receipt.system.section.proxmox"), fetch_pve_status),
        (i18n.tr("receipt.system.section.lxc_vms"), fetch_lxc_vm_status),
        (i18n.tr("receipt.system.section.pinas"), fetch_omv_status),
        (i18n.tr("receipt.system.section.docker"), fetch_docker_status),
        (i18n.tr("receipt.system.section.pbs_backups"), fetch_pbs_recent_backups),
        (i18n.tr("receipt.system.section.updates"), fetch_update_counts),
    ]

    for title, fetch_func in sections:
        report_lines.append("-" * 32)
        report_lines.append(title)
        try:
            report_lines.extend(fetch_func())
        except Exception as e:
            report_lines.append(i18n.tr("print.error_prefix") + str(e))

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
    ok, detail, status_code = enqueue_print(_raw_print_system_report, job_type="system", source="api")
    if ok:
        return jsonify({"status": "printed"}), 200
    return jsonify({"status": "error", "detail": detail}), status_code


@system_bp.route("/ui/system", methods=["POST"])
@csrf_protect
def ui_print_system():
    ok, detail, _status_code = enqueue_print(_raw_print_system_report, job_type="system", source="ui")
    message = i18n.tr("print.success") if ok else i18n.tr("print.error_prefix") + detail
    return render_template("system.html", message=message, success=ok, csrf_token=get_csrf_token())
