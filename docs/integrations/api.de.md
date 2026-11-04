# Automations-API

*[English version](api.md)*

**Wer:** Admins, die Automationen einrichten. Braucht einmal das
[Terminal](../help/glossary.de.md#terminal).

## Was kann das?

Lässt andere Programme drucken: Home Assistant, Node-RED, n8n, ein Skript
oder alles, was eine Web-Anfrage schicken kann. Sie schicken einen kurzen
Text im [JSON](../help/glossary.de.md#json)-Format an eine Adresse von
ReceiptPi, und es wird gedruckt.

## So geht's

### 1. API-Token festlegen

Ohne Token kann jeder in deinem Netzwerk über die API drucken. Leg einen
fest:

1. Erzeuge auf dem Raspberry Pi einen zufälligen Token:

   ```bash
   openssl rand -hex 24
   ```

2. Öffne die Konfiguration und füge ihn in die Zeile `API_TOKEN` ein:

   ```bash
   sudo nano /etc/receiptpi/config.py
   ```

   ```python
   API_TOKEN = "der Token aus Schritt 1"
   ```

   Speichern mit `Strg+O`, `Enter`, schließen mit `Strg+X`.
3. Starte ReceiptPi neu:

   ```bash
   sudo systemctl restart receiptpi
   ```

Die [Watcher](watchers.de.md) übernehmen den Token von selbst.

### 2. Einen Druck schicken

Jede Anfrage ist ein `POST` mit dem Token im Header `X-Api-Token`.
Probier es von einem beliebigen Rechner in deinem Netzwerk:

```bash
curl -X POST http://receiptpi.local:5000/print/automation \
  -H "Content-Type: application/json" \
  -H "X-Api-Token: DEIN_TOKEN" \
  -d '{"title": "Hallo", "text": "Gedruckt über die API"}'
```

Die Antwort `{"status": "printed"}` heißt: Es hat geklappt.

### Was du drucken kannst

| Adresse | Was gedruckt wird | Inhalt |
|---------|-------------------|--------|
| `/print/automation` | ein Text aus einer Automation | `{"title": "optional", "text": "..."}` |
| `/print/message` | eine [Nachricht](../modules/message.de.md) | `{"title": "optional", "text": "..."}` |
| `/print/list` | ein [Einkaufszettel](../modules/lists.de.md) | `{"title": "...", "items": ["Milch", "Brot"]}` |
| `/print/todo` | eine To-do-Liste | wie `/print/list` |
| `/print/task` | eine Task-Karte | `{"title": "...", "description": "...", "items": [...], "priority": "low", "due_date": "2030-01-31"}`, nur `title` ist Pflicht |
| `/print/weather` | ein [Wetterbericht](../modules/weather.de.md) | `{}` für den Standard-Standort oder `{"location": "Zuhause"}` |
| `/print/wifi` | ein [Gäste-WLAN-Zettel](../modules/wifi.de.md) | `{"ssid": "...", "password": "...", "auth_type": "WPA"}` |
| `/print/image` | ein [Bild](../modules/images.de.md) | `{"image_base64": "...", "dither": "floyd-steinberg"}` |
| `/print/system` | der [Systemreport](../modules/system.de.md) | nichts |

`/health` sagt dir, ob der Drucker bereit ist, ohne Token und ohne zu
drucken.

## Gut zu wissen

- **`/print/automation` oder `/print/message`?** Beide drucken einen
  Text. Nimm für Automationen `/print/automation`: Es hat eine eigene
  Logo-Einstellung und erscheint im [Verlauf](../modules/history.de.md)
  als **Webhook**, so unterscheidest du es von Nachrichten, die jemand
  von Hand geschrieben hat.
- **Die Druckregeln gelten** auch für die API: Ruhezeiten, Drucklimit,
  Duplikate. Zurückgehaltene Aufträge warten unter
  [Ausstehende Aufträge](../modules/pending.de.md).
- **Die Gerätesprache** gilt für alle API-Drucke.
- **Ausgeschaltete Module** antworten mit `404`.
- **Rezepte und Spiele** haben keine API.
- Es gelten dieselben Grenzen wie in der Oberfläche, zum Beispiel 100
  Listeneinträge.

## Klappt nicht?

| Antwort | Bedeutung |
|---------|-----------|
| `401` „invalid or missing X-Api-Token“ | Der Token fehlt oder ist falsch. Prüfe den Header. |
| `429` | Eine Druckregel hat den Auftrag zurückgehalten (Ruhezeit, Drucklimit, Duplikat). Das Feld `detail` sagt, welche. |
| `400` | Der Inhalt stimmt nicht, zum Beispiel fehlt `text`. Das Feld `detail` sagt, was. |
| `404` | Das Modul ist ausgeschaltet oder die Adresse falsch. |
| `500` | Druckerproblem, siehe [Fehlerbehebung](../help/troubleshooting.de.md). |
| `503` | Es warten mehr als 100 Aufträge. Versuch es später. |

---

[Zurück zur Übersicht](../README.de.md)
