# FAQ

*[Deutsche Version](faq.de.md)*

**Who:** everyone

## What it does

Short answers to questions that come up often.

## How to use it

### Does ReceiptPi need the internet?

No, the basics work in your home network alone: lists, messages,
images, games, history. Some features fetch data from the internet: the
weather report and storm warnings, the GitHub watcher and the update
check. The installation itself needs the internet once.

### Which printers work?

Tested is the **Epson TM-T88V** with USB and 80 mm paper. Other ESC/POS
receipt printers may work, but haven't been tested. See
[Installation](../installation.md#printer-and-usb).

### Can several people use it?

Yes. Every person can get an account with their own drafts, history and
settings, see [Login and accounts](../settings/accounts.md).

### Can I reach ReceiptPi from outside my home?

It is built for your home network. Don't open it to the internet
directly. If you want to reach it from outside, use a VPN to your home
network.

### Is my data sent anywhere?

No, everything stays on the Raspberry Pi. Only what a feature needs
leaves it: the coordinates of your weather locations go to the weather
service, the watched project names to GitHub, the version check to
GitHub.

### What happens during a power cut?

Settings, accounts, history and pending jobs are stored on the SD card
and are there again after the restart. When ReceiptPi starts, it prints
the **ONLINE** slip.

### Why does a slip come out every time ReceiptPi starts?

That is the **ONLINE** slip with name and IP address. It shows that
ReceiptPi is running and where to find it.

### How do I update ReceiptPi?

If you installed it with the installer: in the folder you installed
from, run `git pull` and then `sudo ./deploy/install.sh` again. Your
configuration and settings are kept.

### Does the receipt fade?

Thermal paper fades over months, faster in the sun or near heat. For
shopping lists and notes that's fine. For things to keep, take a photo.

## Good to know

Your question isn't here? Look at [Troubleshooting](troubleshooting.md)
or the [overview](../README.md).

## Something not working?

See [Troubleshooting](troubleshooting.md).

---

[Back to the overview](../README.md)
