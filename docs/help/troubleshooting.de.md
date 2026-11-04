# Fehlerbehebung

*[English version](troubleshooting.md)*

**Wer:** alle. Einige Schritte brauchen das [Terminal](glossary.de.md#terminal).

## Was kann das?

Sammelt die häufigsten Probleme an einem Ort, mit dem, was du zuerst
prüfen solltest. Probleme mit einer bestimmten Funktion stehen auch auf
deren Seite.

## So geht's

Such dein Problem in den Tabellen unten und arbeite die Schritte von oben
nach unten ab.

### Der Drucker

| Problem | Was du prüfen kannst |
|---------|----------------------|
| Die Startseite zeigt **nicht erreichbar**. | Ist der Drucker eingeschaltet? Steckt das USB-Kabel an beiden Enden? Schalte den Drucker aus und wieder ein und warte eine Minute. |
| Es kommt nichts heraus, kein Fehler. | Ist Papier drin, und richtig herum? Thermopapier druckt nur auf einer Seite: Kommt ein leerer Zettel heraus, dreh die Rolle um. |
| Der Druck ist blass. | Thermopapier altert und verblasst durch Wärme und Licht. Probier eine frische Rolle. |
| Ein Fehler beim Drucken. | Der Auftrag wartet unter [Ausstehende Aufträge](../modules/pending.de.md). Bring den Drucker in Ordnung und drucke ihn dann von dort. |

### Es wird nichts gedruckt

| Problem | Was du prüfen kannst |
|---------|----------------------|
| „Fehler: Ruhezeit aktiv ...“ | Gerade läuft eine [Ruhezeit-Regel](../settings/print-rules.de.md). Der Auftrag wartet unter [Ausstehende Aufträge](../modules/pending.de.md). |
| „Fehler: Rate-Limit erreicht ...“ | In der letzten Stunde wurde zu viel gedruckt, siehe [Druckregeln](../settings/print-rules.de.md). |
| „Fehler: Duplikat unterdrückt ...“ | Dasselbe wurde vor weniger als einer Minute gedruckt. |
| Ein automatischer Druck kam nicht. | Siehe [Automatische Watcher](../integrations/watchers.de.md#klappt-nicht). |

### Die Oberfläche

| Problem | Was du prüfen kannst |
|---------|----------------------|
| Die Seite öffnet sich nicht. | Bist du im selben Netzwerk? Nimm die IP-Adresse und `:5000`, zum Beispiel `http://192.168.178.50:5000`. Hat der Raspberry Pi Strom? |
| Die Seite öffnet sich, sieht aber alt aus. | Lade die Seite neu. Auf dem Handy: Tab schließen und neu öffnen. |
| Du kannst dich nicht anmelden. | Siehe [Anmeldung und Konten](../settings/accounts.de.md#klappt-nicht). |
| Eine Kachel oder ein Menüeintrag fehlt. | Siehe [Startseite und Menü](../home-and-menu.de.md#klappt-nicht). |

### Genauer nachsehen (Terminal)

Diese Befehle zeigen, was ReceiptPi gerade tut. Sie ändern nichts.

```bash
systemctl status receiptpi
journalctl -u receiptpi -n 50 --no-pager
```

Der erste zeigt, ob ReceiptPi läuft („active (running)“). Der zweite
zeigt die letzten 50 Zeilen des Protokolls, in denen Fehler erklärt
werden.

ReceiptPi neu starten:

```bash
sudo systemctl restart receiptpi
```

## Gut zu wissen

- **Ein Neustart schadet nicht.** Einstellungen, Verlauf und ausstehende
  Aufträge bleiben erhalten.
- **Die Uhrzeit auf den Bons** kommt vom Raspberry Pi. Stimmt sie nicht,
  prüfe die Zeitzone mit `sudo raspi-config` (Localisation Options,
  Timezone).
- Mehr technische Details zu Drucker und Installation stehen unter
  [Installation](../installation.de.md#fehlersuche).

## Klappt nicht?

Hilft das alles nicht, eröffne ein Issue auf
[GitHub](https://github.com/HostisHumani/ReceiptPi/issues). Beschreib, was
du getan hast und was passiert ist, und füg die Ausgabe von
`journalctl -u receiptpi -n 50 --no-pager` bei. Entferne vorher
Passwörter und andere private Daten daraus.

---

[Zurück zur Übersicht](../README.de.md)
