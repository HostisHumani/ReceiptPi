# Glossar

*[English version](glossary.md)*

Kurze Erklärungen zu Begriffen aus dieser Dokumentation.

## Admin

Ein Konto, das alles darf, auch alle Einstellungen. Siehe
[Anmeldung und Konten](../settings/accounts.de.md).

## API

Ein Weg, über den andere Programme mit ReceiptPi sprechen, zum Beispiel
um aus Home Assistant zu drucken. Siehe [Automations-API](../integrations/api.de.md).

## API-Token

Ein langes geheimes Wort, das andere Programme mitschicken, um zu
zeigen, dass sie die [API](#api) nutzen dürfen. Es wird in der
[Konfigurationsdatei](#konfigurationsdatei) festgelegt.

## Ausstehender Auftrag

Ein Druck, der noch nicht herausgekommen ist, wegen einer Ruhezeit, des
Drucklimits oder eines Druckerproblems. Siehe
[Ausstehende Aufträge](../modules/pending.de.md).

## Druckregeln

Regeln dafür, wann und wie viel ReceiptPi druckt: Ruhezeiten,
Drucklimit, Duplikat-Sperre. Siehe [Druckregeln](../settings/print-rules.de.md).

## Easy-Read

Größere Schrift auf dem Bon und in der Oberfläche. Siehe
[Druckregeln](../settings/print-rules.de.md#größere-schrift-easy-read).

## Gerätestandard

Eine Einstellung, die für das ganze Gerät und jedes Konto ohne eigene
Auswahl gilt. Siehe [Meine Einstellungen](../settings/my-settings.de.md).

## JSON

Ein einfaches Textformat für Daten, zum Beispiel
`{"title": "Hallo", "text": "Welt"}`. Die [API](#api) und [MQTT](#mqtt)
nutzen es.

## Konfigurationsdatei

Die Datei `/etc/receiptpi/config.py` auf dem Raspberry Pi. Sie enthält
ein paar Werte, die man einmal festlegt und selten ändert, etwa den
[API-Token](#api-token) oder die FRITZ!Box-Anmeldung. Alles andere stellst
du in der Oberfläche ein. Du änderst sie im [Terminal](#terminal) und
startest ReceiptPi danach neu.

## MQTT

Ein Nachrichtensystem, das viele Smart Homes nutzen. Ein zentrales
Programm, der *Broker*, reicht Nachrichten zwischen Geräten weiter.
Siehe [MQTT](../integrations/mqtt.de.md).

## Rasterverfahren

Grautöne in ein Muster aus schwarzen Punkten umwandeln, weil ein
Bondrucker nur Schwarz drucken kann. Siehe [Bild](../modules/images.de.md).

## Ruhezeit

Eine Zeit, in der ReceiptPi nichts druckt, zum Beispiel nachts. Siehe
[Druckregeln](../settings/print-rules.de.md).

## SSH

Der Weg, sich von einem anderen Rechner aus am Raspberry Pi anzumelden:
`ssh <benutzer>@<hostname>`, zum Beispiel `ssh pi@receiptpi.local`.
Schalte es ein, wenn du die SD-Karte im Raspberry Pi Imager einrichtest.

## Terminal

Ein Textfenster, in dem du Befehle für den Raspberry Pi eingibst. Du
erreichst es von einem anderen Rechner aus über [SSH](#ssh): Öffne dort
das Terminal (Windows: *PowerShell*, macOS: *Terminal*) und verbinde dich
mit dem Raspberry Pi. Befehle stehen in dieser Dokumentation in grauen
Kästen: Kopiere sie genau so, drück `Enter` und gib dein Passwort ein,
wenn `sudo` danach fragt.

## Thermopapier

Das Papier von Bondruckern. Es wird schwarz, wo es erhitzt wird, deshalb
braucht der Drucker keine Tinte. Es druckt nur auf einer Seite und
verblasst mit der Zeit.

## Watcher

Ein kleiner Helfer, der im Hintergrund läuft und von selbst druckt, wenn
etwas passiert. Siehe [Automatische Watcher](../integrations/watchers.de.md).

## Webhook

Eine Adresse, an die andere Programme eine Nachricht schicken, damit
etwas passiert. Bei ReceiptPi: `/print/automation`, siehe
[Automations-API](../integrations/api.de.md).

---

[Zurück zur Übersicht](../README.de.md)
