# Automation API

*[Deutsche Version](api.de.md)*

**Who:** admins setting up automations. Needs the
[terminal](../help/glossary.md#terminal) once.

## What it does

Lets other programs print: Home Assistant, Node-RED, n8n, a script, or
anything that can send a web request. They send a short text in
[JSON](../help/glossary.md#json) format to an address of ReceiptPi, and
it prints.

## How to use it

### 1. Set an API token

Without a token, anyone in your network can print through the API. Set
one:

1. Create a random token on the Raspberry Pi:

   ```bash
   openssl rand -hex 24
   ```

2. Open the configuration and paste it into the `API_TOKEN` line:

   ```bash
   sudo nano /etc/receiptpi/config.py
   ```

   ```python
   API_TOKEN = "the token from step 1"
   ```

   Save with `Ctrl+O`, `Enter`, and close with `Ctrl+X`.
3. Restart ReceiptPi:

   ```bash
   sudo systemctl restart receiptpi
   ```

The [watchers](watchers.md) pick up the token on their own.

### 2. Send a print

Every request is a `POST` with the token in the header `X-Api-Token`.
Try it from any computer in your network:

```bash
curl -X POST http://receiptpi.local:5000/print/automation \
  -H "Content-Type: application/json" \
  -H "X-Api-Token: YOUR_TOKEN" \
  -d '{"title": "Hello", "text": "Printed by the API"}'
```

The answer `{"status": "printed"}` means it worked.

### What you can print

| Address | What it prints | Content |
|---------|----------------|---------|
| `/print/automation` | a text from an automation | `{"title": "optional", "text": "..."}` |
| `/print/message` | a [message](../modules/message.md) | `{"title": "optional", "text": "..."}` |
| `/print/list` | a [shopping list](../modules/lists.md) | `{"title": "...", "items": ["milk", "bread"]}` |
| `/print/todo` | a to-do list | like `/print/list` |
| `/print/task` | a task card | `{"title": "...", "description": "...", "items": [...], "priority": "low", "due_date": "2030-01-31"}`, only `title` is required |
| `/print/weather` | a [weather report](../modules/weather.md) | `{}` for the default location, or `{"location": "Home"}` |
| `/print/wifi` | a [guest Wi-Fi slip](../modules/wifi.md) | `{"ssid": "...", "password": "...", "auth_type": "WPA"}` |
| `/print/image` | an [image](../modules/images.md) | `{"image_base64": "...", "dither": "floyd-steinberg"}` |
| `/print/system` | the [system report](../modules/system.md) | nothing |

`/health` tells you whether the printer is ready, without a token and
without printing.

## Good to know

- **`/print/automation` or `/print/message`?** Both print a text. Use
  `/print/automation` for automations: it has its own logo setting and
  shows up as **Webhook** in the [history](../modules/history.md), so you
  can tell it apart from messages typed by hand.
- **Print rules apply** to the API as well: quiet hours, print limit,
  duplicates. Held-back jobs wait under [Pending jobs](../modules/pending.md).
- **The device language** is used for all API prints.
- **Switched-off modules** answer with `404`.
- **Recipes and games** have no API.
- The same limits apply as in the web interface, for example 100 list
  entries.

## Something not working?

| Answer | Meaning |
|--------|---------|
| `401` "invalid or missing X-Api-Token" | The token is missing or wrong. Check the header. |
| `429` | A print rule held the job back (quiet hours, print limit, duplicate). The `detail` field says which. |
| `400` | The content is wrong, for example a missing `text`. The `detail` field says what. |
| `404` | The module is switched off, or the address is wrong. |
| `500` | Printer problem, see [Troubleshooting](../help/troubleshooting.md). |
| `503` | More than 100 jobs are waiting. Try again later. |

---

[Back to the overview](../README.md)
