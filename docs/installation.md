# Installation

*[Deutsche Version / German version](installation.de.md)*

This guide installs ReceiptPi on a fresh Raspberry Pi OS Lite with the
installer script `deploy/install.sh`.

---

## Status

The installer has been tested in a Debian 13 "trixie" arm64 container
without a running systemd:

- installation, a second run with an identical result, and a third run
  that keeps the existing configuration
- owners and permissions of everything it installs, and no configuration
  or secret in the installed files before the first start
- the first-start script, which creates the configuration
- the server started as the `receiptpi` user with the command line of
  `receiptpi.service`, and every watcher started the same way

Not yet tested: a real Raspberry Pi, systemd starting the services and
timers, and printer access over USB with the udev rule.

---

## Tested Hardware

The application itself runs on:

| Component | Version |
|-----------|---------|
| Single-board computer | Raspberry Pi Zero 2 W |
| Operating system | Raspberry Pi OS Lite, 64-bit (Debian 13 "trixie") |
| Thermal printer | Epson TM-T88V (USB, ID `04b8:0e02`) |

Other Raspberry Pi models and printers have not been tested.

---

## Prerequisites

- Raspberry Pi OS Lite **64-bit** (trixie). `deploy/requirements.lock`
  only installs prebuilt packages, and they were checked for 64-bit
  (aarch64) only.
- Network access: the installer downloads system packages with apt and
  Python packages from PyPI.
- A user with `sudo` rights. The installer does not change the time zone or
  the Wi-Fi country: set them in Raspberry Pi Imager or with
  `sudo raspi-config`.

---

## Installing

```bash
sudo apt install git
git clone https://github.com/HostisHumani/ReceiptPi.git
cd ReceiptPi
sudo ./deploy/install.sh
```

The installer can be run again; it keeps an existing configuration.

What it sets up:

| Path / unit | Purpose |
|-------------|---------|
| `/opt/receiptpi` | Program code, owned by root, not writable for the service |
| `/opt/receiptpi/venv` | Python environment with the versions and hashes from `deploy/requirements.lock` |
| `/etc/receiptpi/config.py` | Configuration (`root:receiptpi`, mode 0640), created on the first start |
| `/var/lib/receiptpi` | Settings, history, accounts (mode 0750) |
| `receiptpi` | System user without login that runs the server and the watchers |
| `receiptpi.service` | The server (Gunicorn, port 5000), enabled |
| `receiptpi-firstboot.service` | Creates the configuration before the first server start |
| `receiptpi-*.timer` | The watchers, see below |
| `/etc/udev/rules.d/99-receiptpi-printer.rules` | Printer access for the `lp` group |

A `config.py`, `secret.key` or `settings.json` in the checkout is not
copied.

---

## First start

Before the server starts for the first time, `receiptpi-firstboot.service`
creates `/etc/receiptpi/config.py` from `config.example.py`:

- a random `SECRET_KEY` of its own for every device
- `API_TOKEN` empty
- the sample entries of the template (system report hosts, watched GitHub
  repos) empty

An existing `config.py` is never replaced.

Then open the web UI and create the admin account:

```
http://<hostname>:5000/setup
```

With an empty `API_TOKEN` the setup page prints a one-time setup code on the
printer, so the printer has to be connected and switched on for this step.
Until an admin account exists, the web UI is open to everyone on the
network.

### Changing the configuration

`config.py` is read when the server starts:

```bash
sudo nano /etc/receiptpi/config.py
sudo systemctl restart receiptpi
```

Most settings are made in the web UI instead (Settings). `config.py` only
holds start-up values such as the printer IDs, the API token and
credentials, see the comments in the file.

---

## Wi-Fi setup (image only)

A device written from the ReceiptPi image needs no Wi-Fi settings in
Raspberry Pi Imager; it sets up its Wi-Fi on its own. Status: tested with a
simulated NetworkManager, not yet on a real device.

1. Switch the device on with the printer connected. If it finds no network
   within 2 minutes, it prints a receipt **Wi-Fi setup** with a network name
   (`ReceiptPi-…`), a password and a QR code.
2. Scan the QR code with your phone, or join the network by hand with the
   name and password from the receipt.
3. The setup page opens by itself. If not, open `http://10.42.0.1` in the
   browser.
4. Choose your Wi-Fi, enter its password and tap **Connect**.
5. The setup network closes. The device prints **Wi-Fi connected** with its
   address and a QR code to open the web UI. Continue with
   [First start](#first-start).

Good to know:

- If joining fails, the setup network opens again with the same password
  and a note is printed.
- If the device loses its Wi-Fi for 10 minutes later on (new router, new
  password), the setup network opens again with a new password. Every 10
  minutes it checks for a minute whether the saved Wi-Fi is back.
- A network cable plugged into the device ends the setup at once.
- A USB Wi-Fi adapter is used before the built-in Wi-Fi.
- The clock: the device has none that keeps running without power. It
  sets the time from the time server your router announces, otherwise from
  public time servers. If none of them answers, the time of your phone
  from the setup page is used, so the date is right from the start.
- Installed with `install.sh`, the device never opens a setup network.

---

## Printer and USB

ReceiptPi talks to the printer directly over USB (python-escpos with the
`TM-T88V` profile). The layout assumes an 80 mm printer with 42 characters
per line (font A) and images 512 pixels wide.

The configuration and the udev rule are set up for the Epson TM-T88V
(`VENDOR_ID = 0x04b8`, `PRODUCT_ID = 0x0e02`). `lsusb` shows it as
`ID 04b8:0e02 Seiko Epson Corp. TM-T88V`.

Another printer (not tested) needs its IDs in `/etc/receiptpi/config.py`
and a udev rule of the same form in a separate file, for example
`/etc/udev/rules.d/99-receiptpi-local.rules`; `99-receiptpi-printer.rules`
is replaced each time the installer runs.

Without a printer the server still starts: the boot greeting only logs a
warning, and `/health` answers with status 500 and `"state": "offline"`.

---

## Watchers

The scripts under `watchers/` run as systemd timers under the `receiptpi`
user. They read the same configuration as the server and send print jobs
to it on `localhost:5000`, so an `API_TOKEN` set later applies to them as
well.

| Timer | Script | Interval |
|-------|--------|----------|
| `receiptpi-github-star.timer` | `github_star_watch.py` | every 5 minutes |
| `receiptpi-storm-warning.timer` | `storm_warning_watch.py` | every 15 minutes |
| `receiptpi-update-check.timer` | `update_check_watch.py` | every 12 hours |
| `receiptpi-wifi-watch.timer` | `fritzbox_wifi_watch.py` | every minute, not enabled |

Without further setup the first two find nothing to do (no repos watched,
storm warning switched off in the settings) and the update check only
caches whether a newer release exists.

The guest network watcher is installed but not enabled. With a Fritz!Box,
enter its credentials in `/etc/receiptpi/config.py` and enable the timer:

```bash
sudo systemctl enable --now receiptpi-wifi-watch.timer
```

---

## Troubleshooting

```bash
systemctl status receiptpi
journalctl -u receiptpi -n 50 --no-pager
journalctl -u receiptpi-firstboot --no-pager
systemctl list-timers 'receiptpi-*'
journalctl -u 'receiptpi-watcher@*' -n 50 --no-pager
```

- **Printer not found** (`/health` reports `offline`): check that `lsusb`
  lists the printer with the IDs from `config.py`, and that its device node
  belongs to the group `lp` (`ls -l /dev/bus/usb/*/*`).
