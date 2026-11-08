# Troubleshooting

*[Deutsche Version](troubleshooting.de.md)*

**Who:** everyone. Some steps need the [terminal](glossary.md#terminal).

## What it does

Collects the most common problems in one place, with what to check
first. Problems with one feature are also listed on that feature's page.

## How to use it

Find your problem in the tables below and work through the steps from
top to bottom.

### The printer

| Problem | What to check |
|---------|---------------|
| The home page says **unreachable**. | Is the printer switched on? Is the USB cable plugged in at both ends? Switch the printer off and on again and wait a minute. |
| Nothing comes out, no error. | Is there paper, and is it inserted the right way round? Thermal paper only prints on one side: if a slip comes out blank, turn the roll around. |
| Prints come out faint. | Thermal paper ages and fades with heat and light. Try a fresh roll. |
| An error while printing. | The job waits under [Pending jobs](../modules/pending.md). Fix the printer, then print it from there. |

### Nothing is printed

| Problem | What to check |
|---------|---------------|
| "Error: Quiet hours active ..." | A [quiet-hours rule](../settings/print-rules.md) is running. The job waits under [Pending jobs](../modules/pending.md). |
| "Error: Rate limit reached ..." | Too many prints in the last hour, see [Print rules](../settings/print-rules.md). |
| "Error: Duplicate suppressed ..." | The same thing was printed less than a minute ago. |
| An automatic print didn't come. | See [Automatic watchers](../integrations/watchers.md#something-not-working). |

### The web interface

| Problem | What to check |
|---------|---------------|
| The page doesn't open. | Are you in the same network? Use the IP address and `:5000`, for example `http://192.168.178.50:5000`. Is the Raspberry Pi powered? |
| The page opens, but looks old. | Reload the page. On a phone, close the browser tab and open it again. |
| You can't log in. | See [Login and accounts](../settings/accounts.md#something-not-working). |
| A tile or menu entry is missing. | See [Home page and menu](../home-and-menu.md#something-not-working). |

### An update

| Problem | What to check |
|---------|---------------|
| The update page shows a reason. | See [Updates](../settings/updates.md#something-not-working). |
| "... and neither did going back to ..." | Use the way back over SSH below. |
| The device doesn't come back after an update. | Switch it off and on again: on start, ReceiptPi finishes or undoes an interrupted update by itself. |

The way back over SSH (terminal) returns to the version before the last
update and to the data saved right before it. Everything changed since
then is lost. The first command only shows the state:

```bash
sudo python3 -I -E -s /var/lib/receiptpi-update/updater.py status
sudo python3 -I -E -s /var/lib/receiptpi-update/updater.py rollback --yes
```

### Looking deeper (terminal)

These commands show what ReceiptPi is doing. They change nothing.

```bash
systemctl status receiptpi
journalctl -u receiptpi -n 50 --no-pager
```

The first one says whether ReceiptPi is running ("active (running)").
The second one shows the last 50 log lines, where errors are explained.

To restart ReceiptPi:

```bash
sudo systemctl restart receiptpi
```

## Good to know

- **A restart is harmless.** Settings, history and pending jobs stay.
- **The time on the receipts** comes from the Raspberry Pi. If it is
  wrong, check the time zone with `sudo raspi-config` (Localisation
  Options, Timezone).
- More technical details about the printer and the installation are in
  [Installation](../installation.md#troubleshooting).

## Something not working?

If none of this helps, open an issue on
[GitHub](https://github.com/HostisHumani/ReceiptPi/issues). Describe what
you did and what happened, and add the output of
`journalctl -u receiptpi -n 50 --no-pager`. Remove passwords and other
private data from it first.

---

[Back to the overview](../README.md)
