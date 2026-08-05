"""
Modul: Systembericht - nutzt größtenteils dieselben SSH-Befehle wie der
bestehende Termux Lab Commander (scripts/termux-lab-commander.sh im
HomeLab-Repo): CPU-Temp/-Last, RAM, NVMe-Speicherplatz, LXC/VM-Liste,
PBS-Backups und Update-Check (dort noch ohne PBS, hier ergänzt). Der
allgemeine OMV-Docker-Status (fetch_docker_status) ist NICHT aus dem
Commander übernommen, der checkt nur Frigate auf einem separaten Host -
hier eigenständig für alle OMV-Container ergänzt. Bewusst ohne Zabbix,
um keine zusätzliche Item-/Template-Konfiguration zu brauchen.
"""
import json
import subprocess
from datetime import datetime

import config
from flask import Blueprint, jsonify, render_template

import i18n
from modules.message.routes import _raw_print_message
from print_queue import enqueue_print
from security import csrf_protect, get_csrf_token, require_api_token

system_bp = Blueprint("system", __name__)


def ssh_run(user, host, remote_command, timeout=10):
    """Führt einen Befehl per SSH auf einem anderen Host aus. Setzt
    voraus, dass der ReceiptPi-Pi sich per Key ohne Passwort dort
    anmelden kann (siehe ANLEITUNG.md). Gibt stdout als String zurück,
    wirft eine Exception bei Fehlern/Timeout."""
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
    """CPU-Temp, CPU-Last, RAM auf dem Proxmox-Host - identische Befehle
    wie im Termux Commander (check_status())."""
    lines = []
    cpu_temp = ssh_run(config.SSH_PROXMOX_USER, config.SSH_PROXMOX_HOST,
                        "cat /sys/class/thermal/thermal_zone0/temp | awk '{printf \"%.0f\", $1/1000}'")
    cpu_load = ssh_run(config.SSH_PROXMOX_USER, config.SSH_PROXMOX_HOST,
                        "top -bn1 | grep 'Cpu' | awk '{printf \"%.0f\", 100-$8}'")
    ram = ssh_run(config.SSH_PROXMOX_USER, config.SSH_PROXMOX_HOST,
                  "free -h | awk '/^Mem:/{print $3\"/\"$2}'")
    lines.append(i18n.tr("receipt.system.cpu_temp", value=cpu_temp))
    lines.append(i18n.tr("receipt.system.cpu_load", value=cpu_load))
    lines.append(i18n.tr("receipt.system.ram", value=ram))
    return lines


def fetch_lxc_vm_status():
    """LXC- und VM-Liste vom Proxmox-Host (pct list / qm list)."""
    lines = []
    lxc_output = ssh_run(config.SSH_PROXMOX_USER, config.SSH_PROXMOX_HOST, "pct list")
    for line in lxc_output.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 3:
            lines.append(f"LXC {parts[0]} ({parts[2]}): {parts[1]}")

    vm_output = ssh_run(config.SSH_PROXMOX_USER, config.SSH_PROXMOX_HOST, "qm list")
    for line in vm_output.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 3:
            lines.append(f"VM {parts[0]} ({parts[1]}): {parts[2]}")

    return lines or [i18n.tr("receipt.system.no_lxc_vms")]


def fetch_omv_status():
    """NVMe-Speicherplatz auf piNAS (OMV) - identischer Befehl wie im
    Termux Commander."""
    disk = ssh_run(config.SSH_PINAS_USER, config.SSH_PINAS_HOST,
                    "df -h /dev/nvme0n1p2 | awk 'NR==2{print $3\"/\"$2\" (\"$5\")\"}'")
    return [i18n.tr("receipt.system.nvme", value=disk)]


def fetch_docker_status():
    """Docker-Container-Status per SSH direkt von piNAS/OMV (docker ps).
    Anders als die anderen fetch_*-Funktionen NICHT 1:1 aus dem Termux
    Commander übernommen - der checkt nur Frigate auf einem separaten
    Host, keinen allgemeinen OMV-Docker-Stack."""
    output = ssh_run(
        config.SSH_PINAS_USER, config.SSH_PINAS_HOST,
        "docker ps --format '{{.Names}}: {{.Status}}'",
    )
    return output.splitlines() if output else [i18n.tr("receipt.system.no_containers")]


def fetch_pbs_recent_backups(limit=5):
    """Letzte PBS-Backup-Tasks (Backup/Sync/Prune/Verify/GC), analog zur
    Python-Auswertung im Termux Commander, hier lokal statt remote per
    eingebettetem Python-Aufruf geparst."""
    output = ssh_run(config.SSH_PBS_USER, config.SSH_PBS_HOST,
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


UPDATE_LIST_THRESHOLD = 10  # bis zu dieser Anzahl Paketnamen ausdrucken, sonst nur die Zahl


def fetch_updates_for_host(label, user, host):
    """Liefert Update-Zeilen für einen Host. Bei wenigen offenen Updates
    (<= UPDATE_LIST_THRESHOLD) werden die Paketnamen einzeln aufgelistet,
    darüber nur die Anzahl."""
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
    """Update-Status für PVE, OMV und PBS, wie im Termux Commander unter
    'u) Update-Check' - dort bisher ohne PBS, hier ergänzt."""
    lines = []
    lines.extend(fetch_updates_for_host("PVE", config.SSH_PROXMOX_USER, config.SSH_PROXMOX_HOST))
    lines.extend(fetch_updates_for_host("OMV", config.SSH_PINAS_USER, config.SSH_PINAS_HOST))
    lines.extend(fetch_updates_for_host("PBS", config.SSH_PBS_USER, config.SSH_PBS_HOST))
    return lines


def _raw_print_system_report():
    report_lines = [i18n.tr("receipt.system.title"), datetime.now().strftime('%d.%m.%Y %H:%M')]

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
    _raw_print_message(None, text)


@system_bp.route("/system", methods=["GET"])
def system_page():
    return render_template("system.html", message=None, success=None, csrf_token=get_csrf_token())


@system_bp.route("/print/system", methods=["POST", "GET"])
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
