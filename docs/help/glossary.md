# Glossary

*[Deutsche Version](glossary.de.md)*

Short explanations of terms used in this documentation.

## Admin

An account that may do everything, including all settings. See
[Login and accounts](../settings/accounts.md).

## API

A way for other programs to talk to ReceiptPi, for example to print
from Home Assistant. See [Automation API](../integrations/api.md).

## API token

A long secret word that other programs send along to prove they may use
the [API](#api). It is set in the [configuration file](#configuration-file).

## Configuration file

The file `/etc/receiptpi/config.py` on the Raspberry Pi. It holds a few
values that are set once and rarely changed, such as the
[API token](#api-token) or the FRITZ!Box login. Everything else is set in
the web interface. Change it in the [terminal](#terminal) and restart
ReceiptPi afterwards.

## Device default

A setting that applies to the whole device and to every account that
hasn't chosen its own. See [My settings](../settings/my-settings.md).

## Dithering

Turning grey tones into a pattern of black dots, because a receipt
printer can only print black. See [Image](../modules/images.md).

## Easy-Read

Larger text on the receipt and in the web interface. See
[Print rules](../settings/print-rules.md#larger-text-easy-read).

## JSON

A simple text format for data, for example
`{"title": "Hello", "text": "World"}`. The [API](#api) and [MQTT](#mqtt)
use it.

## MQTT

A messaging system many smart homes use. A central program, the
*broker*, passes messages between devices. See
[MQTT](../integrations/mqtt.md).

## Pending job

A print that didn't come out yet, because of quiet hours, the print
limit or a printer problem. See [Pending jobs](../modules/pending.md).

## Print rules

Rules for when and how much ReceiptPi prints: quiet hours, print limit,
duplicate suppression. See [Print rules](../settings/print-rules.md).

## Quiet hours

Times when ReceiptPi prints nothing, for example at night. See
[Print rules](../settings/print-rules.md).

## SSH

The way to log in to the Raspberry Pi from another computer:
`ssh <user>@<hostname>`, for example `ssh pi@receiptpi.local`. Switch it
on when you set up the SD card in Raspberry Pi Imager.

## Terminal

A text window in which you type commands for the Raspberry Pi. You
reach it from another computer with [SSH](#ssh): open the terminal there
(Windows: *PowerShell*, macOS: *Terminal*) and connect to the Raspberry
Pi. Commands in this documentation are shown in grey boxes: copy them
exactly, press `Enter`, and enter your password when `sudo` asks for it.

## Thermal paper

The paper of receipt printers. It turns black where it is heated, so the
printer needs no ink. It only prints on one side and fades over time.

## Watcher

A small helper that runs in the background and prints on its own when
something happens. See [Automatic watchers](../integrations/watchers.md).

## Webhook

An address other programs send a message to, so something happens. In
ReceiptPi: `/print/automation`, see [Automation API](../integrations/api.md).

---

[Back to the overview](../README.md)
