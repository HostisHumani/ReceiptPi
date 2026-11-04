# Home Assistant

*[English version](home-assistant.md)*

**Wer:** Admins, die Home Assistant nutzen

## Was kann das?

Drei Wege, wie ReceiptPi und Home Assistant zusammenarbeiten:

| Weg | Was du bekommst |
|-----|-----------------|
| **MQTT Discovery** | ReceiptPi erscheint als Gerät: Druckerstatus, ausstehende Aufträge, letzter Auftrag, ein Wetter-Knopf. Ganz ohne YAML. |
| **REST-Befehl** | Automationen drucken Texte, Listen oder Task-Karten, zum Beispiel „Die Wäsche ist fertig“. |
| **Netatmo-Werte** | Der [Wetterbericht](../modules/weather.de.md) zeigt zusätzlich Innen- und Außenwerte aus Home Assistant. |

## So geht's

### MQTT Discovery

Nutzt Home Assistant schon MQTT: Richte [MQTT](mqtt.de.md) in ReceiptPi
ein und schalte **Home Assistant Discovery** ein. Das Gerät erscheint
von selbst in Home Assistant.

### Aus Automationen drucken (REST-Befehl)

1. Leg in ReceiptPi einen API-Token fest, siehe
   [Automations-API](api.de.md#1-api-token-festlegen).
2. Trag den Token in Home Assistant in `secrets.yaml` ein:

   ```yaml
   receiptpi_token: "DEIN_TOKEN"
   ```

3. Ergänze das in `configuration.yaml` und starte Home Assistant neu:

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

   Nimm die IP-Adresse deines Raspberry Pi: Home Assistant kann
   `receiptpi.local` oft nicht auflösen.
4. Nutze ihn in einer beliebigen Automation:

   ```yaml
   action: rest_command.receiptpi_print
   data:
     title: "Waschmaschine"
     text: "Die Wäsche ist fertig."
   ```

Für Listen, Task-Karten und die anderen Drucke änderst du Adresse und
Inhalt, siehe [was du drucken kannst](api.de.md#was-du-drucken-kannst).

### Netatmo-Werte im Wetterbericht

1. Öffne in Home Assistant dein Profil, geh zu **Sicherheit** und
   erzeuge einen **Langlebigen Zugriffstoken**.
2. Öffne auf dem Raspberry Pi die Konfiguration:

   ```bash
   sudo nano /etc/receiptpi/config.py
   ```

3. Fülle diese Zeilen mit deinen Werten:

   ```python
   HA_BASE_URL = "http://192.168.178.10:8123"
   HA_TOKEN = "der langlebige Zugriffstoken"
   NETATMO_INDOOR_ENTITY = "sensor.netatmo_indoor_temperature"
   NETATMO_OUTDOOR_ENTITY = "sensor.netatmo_outdoor_temperature"
   ```

   Die Namen der Entitäten findest du in Home Assistant unter
   **Einstellungen** → **Geräte & Dienste** → **Entitäten**.
4. Starte ReceiptPi neu: `sudo systemctl restart receiptpi`.

## Gut zu wissen

- **Die Druckregeln gelten** für alles, was Home Assistant schickt.
  Während einer Ruhezeit warten Aufträge unter
  [Ausstehende Aufträge](../modules/pending.de.md).
- **Die Gerätesprache** gilt für Drucke aus Home Assistant.
- Drucke über `/print/automation` erscheinen im
  [Verlauf](../modules/history.de.md) als **Webhook**.

## Klappt nicht?

| Problem | Was du tun kannst |
|---------|-------------------|
| Home Assistant meldet einen Fehler `401`. | Der Token in `secrets.yaml` passt nicht zu `API_TOKEN` in ReceiptPi. |
| Home Assistant erreicht ReceiptPi nicht. | Nimm die IP-Adresse statt `receiptpi.local` und vergiss `:5000` nicht. |
| Im Wetterbericht steht „Netatmo-Fehler: ...“. | Prüfe `HA_BASE_URL`, den Token und die Namen der Entitäten und starte ReceiptPi dann neu. |

---

[Zurück zur Übersicht](../README.de.md)
