# Home Assistant

*[Deutsche Version](home-assistant.de.md)*

**Who:** admins who use Home Assistant

## What it does

Three ways ReceiptPi and Home Assistant work together:

| Way | What you get |
|-----|--------------|
| **MQTT discovery** | ReceiptPi appears as a device: printer status, pending jobs, last job, a weather button. No YAML needed. |
| **REST command** | Automations print texts, lists or task cards, for example "The laundry is done". |
| **Netatmo values** | The [weather report](../modules/weather.md) also shows indoor and outdoor values from Home Assistant. |

## How to use it

### MQTT discovery

If Home Assistant already uses MQTT: set up [MQTT](mqtt.md) in ReceiptPi
and switch on **Home Assistant discovery**. The device appears in Home
Assistant on its own.

### Printing from automations (REST command)

1. Set an API token in ReceiptPi, see [Automation API](api.md#1-set-an-api-token).
2. In Home Assistant, put the token into `secrets.yaml`:

   ```yaml
   receiptpi_token: "YOUR_TOKEN"
   ```

3. Add this to `configuration.yaml` and restart Home Assistant:

   ```yaml
   rest_command:
     receiptpi_print:
       url: "http://192.168.178.50:5000/print/automation"
       method: POST
       content_type: "application/json"
       headers:
         X-Api-Token: !secret receiptpi_token
       payload: '{{ {"title": title, "text": text} | to_json }}'
   ```

   Use the IP address of your Raspberry Pi: Home Assistant often can't
   resolve `receiptpi.local`.
4. Use it in any automation:

   ```yaml
   action: rest_command.receiptpi_print
   data:
     title: "Washing machine"
     text: "The laundry is done."
   ```

For lists, task cards and the other prints, change the address and the
content, see [what you can print](api.md#what-you-can-print).

### Netatmo values in the weather report

1. In Home Assistant, open your profile, go to **Security** and create a
   **Long-lived access token**.
2. On the Raspberry Pi, open the configuration:

   ```bash
   sudo nano /etc/receiptpi/config.py
   ```

3. Fill in these lines with your values:

   ```python
   HA_BASE_URL = "http://192.168.178.10:8123"
   HA_TOKEN = "the long-lived access token"
   NETATMO_INDOOR_ENTITY = "sensor.netatmo_indoor_temperature"
   NETATMO_OUTDOOR_ENTITY = "sensor.netatmo_outdoor_temperature"
   ```

   The entity names are in Home Assistant under **Settings** →
   **Devices & services** → **Entities**.
4. Restart ReceiptPi: `sudo systemctl restart receiptpi`.

## Good to know

- **Print rules apply** to everything Home Assistant sends. During quiet
  hours, jobs wait under [Pending jobs](../modules/pending.md).
- **The device language** is used for prints from Home Assistant.
- Prints through `/print/automation` show up as **Webhook** in the
  [history](../modules/history.md).

## Something not working?

| Problem | What to do |
|---------|------------|
| Home Assistant logs an error `401`. | The token in `secrets.yaml` doesn't match `API_TOKEN` in ReceiptPi. |
| Home Assistant can't reach ReceiptPi. | Use the IP address instead of `receiptpi.local`, and don't forget `:5000`. |
| The weather report says "Netatmo error: ...". | Check `HA_BASE_URL`, the token and the entity names, then restart ReceiptPi. |

---

[Back to the overview](../README.md)
