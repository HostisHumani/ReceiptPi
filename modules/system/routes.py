"""System report module.

Collects host status over SSH, including Proxmox resource usage, VM and LXC
state, OMV storage and containers, PBS tasks, and pending package updates.
Passwordless SSH access must be configured for every target host."""
import json
import subprocess
from datetime import datetime

import config
from flask import Blueprint, jsonify, render_template

from modules.message.routes import _raw_print_message
from print_queue import enqueue_print
from security import csrf_protect, get_csrf_token, require_api_token

system_bp = Blueprint("system", __name__)


def ssh_run(user, host, remote_command, timeout=10):
    """Run a command on a remote host over SSH and return stdout.

    Raises an exception on command failure or timeout."""
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
        raise RuntimeError(result.stderr.strip() or f"SSH-Befehl fehlgeschlagen (Exit {result.returncode})")
    return result.stdout.strip()


def fetch_pve_status():
    """Return CPU temperature, load, and memory usage from Proxmox."""
    lines = []
    cpu_temp = ssh_run(config.SSH_PROXMOX_USER, config.SSH_PROXMOX_HOST,
                        "cat /sys/class/thermal/thermal_zone0/temp | awk '{printf \"%.0f\", $1/1000}'")
    cpu_load = ssh_run(config.SSH_PROXMOX_USER, config.SSH_PROXMOX_HOST,
                        "top -bn1 | grep 'Cpu' | awk '{printf \"%.0f\", 100-$8}'")
    ram = ssh_run(config.SSH_PROXMOX_USER, config.SSH_PROXMOX_HOST,
                  "free -h | awk '/^Mem:/{print $3\"/\"$2}'")
    lines.append(f"CPU-Temp: {cpu_temp}C")
    lines.append(f"CPU-Last: {cpu_load}%")
    lines.append(f"RAM: {ram}")
    return lines


def fetch_lxc_vm_status():
    """Return the current Proxmox LXC and VM lists."""
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

    return lines or ["Keine LXC/VMs gefunden"]


def fetch_omv_status():
    """Return NVMe storage usage from the OMV host."""
    disk = ssh_run(config.SSH_PINAS_USER, config.SSH_PINAS_HOST,
                    "df -h /dev/nvme0n1p2 | awk 'NR==2{print $3\"/\"$2\" (\"$5\")\"}'")
    return [f"NVMe: {disk}"]


def fetch_docker_status():
    """Return Docker container status from the OMV host."""
    output = ssh_run(
        config.SSH_PINAS_USER, config.SSH_PINAS_HOST,
        "docker ps --format '{{.Names}}: {{.Status}}'",
    )
    return output.splitlines() if output else ["Keine laufenden Container gefunden"]


def fetch_pbs_recent_backups(limit=5):
    """Return recent PBS maintenance and backup tasks."""
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
    return lines or ["Keine Backup-Tasks gefunden"]


UPDATE_LIST_THRESHOLD = 10  # Print package names up to this threshold; otherwise print only the count.


def fetch_updates_for_host(label, user, host):
    """Return pending package updates for one host."""
    output = ssh_run(user, host, "apt list --upgradable 2>/dev/null | grep -v '^Listing'")
    if not output:
        return [f"{label}: aktuell"]

    packages = [line.split("/")[0] for line in output.splitlines() if line.strip()]
    count = len(packages)
    if count <= UPDATE_LIST_THRESHOLD:
        lines = [f"{label}: {count} Update(s)"]
        lines.extend(f"  - {p}" for p in packages)
        return lines
    return [f"{label}: {count} Updates verfügbar"]


def fetch_update_counts():
    """Return update status for the configured PVE, OMV, and PBS hosts."""
    lines = []
    lines.extend(fetch_updates_for_host("PVE", config.SSH_PROXMOX_USER, config.SSH_PROXMOX_HOST))
    lines.extend(fetch_updates_for_host("OMV", config.SSH_PINAS_USER, config.SSH_PINAS_HOST))
    lines.extend(fetch_updates_for_host("PBS", config.SSH_PBS_USER, config.SSH_PBS_HOST))
    return lines


def _raw_print_system_report():
    report_lines = ["SYSTEMBERICHT", datetime.now().strftime('%d.%m.%Y %H:%M')]

    sections = [
        ("Proxmox (Mac Mini)", fetch_pve_status),
        ("LXC / VMs", fetch_lxc_vm_status),
        ("piNAS (OMV)", fetch_omv_status),
        ("Docker-Container", fetch_docker_status),
        ("PBS Backups (letzte 5)", fetch_pbs_recent_backups),
        ("Updates verfügbar", fetch_update_counts),
    ]

    for title, fetch_func in sections:
        report_lines.append("-" * 32)
        report_lines.append(title)
        try:
            report_lines.extend(fetch_func())
        except Exception as e:
            report_lines.append(f"Fehler: {e}")

    text = "\n".join(report_lines)
    _raw_print_message(None, text)


@system_bp.route("/system", methods=["GET"])
def system_page():
    return render_template("system.html", message=None, success=None, csrf_token=get_csrf_token())


@system_bp.route("/print/system", methods=["POST", "GET"])
@require_api_token
def print_system():
    ok, detail, status_code = enqueue_print(_raw_print_system_report)
    if ok:
        return jsonify({"status": "gedruckt"}), 200
    return jsonify({"status": "error", "detail": detail}), status_code


@system_bp.route("/ui/system", methods=["POST"])
@csrf_protect
def ui_print_system():
    ok, detail, _status_code = enqueue_print(_raw_print_system_report)
    message = "Systembericht gedruckt ✓" if ok else f"Fehler: {detail}"
    return render_template("system.html", message=message, success=ok, csrf_token=get_csrf_token())
