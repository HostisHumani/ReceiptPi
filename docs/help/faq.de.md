# Häufige Fragen

*[English version](faq.md)*

**Wer:** alle

## Was kann das?

Kurze Antworten auf Fragen, die oft kommen.

## So geht's

### Braucht ReceiptPi Internet?

Nein, das Grundlegende funktioniert allein in deinem Heimnetz: Listen,
Nachrichten, Bilder, Spiele, Verlauf. Einige Funktionen holen Daten aus
dem Internet: Wetterbericht und Unwetterwarnung, der GitHub-Watcher und
die Update-Prüfung. Die Installation selbst braucht einmal Internet.

### Welche Drucker funktionieren?

Getestet ist der **Epson TM-T88V** mit USB und 80-mm-Papier. Andere
ESC/POS-Bondrucker können funktionieren, sind aber nicht getestet. Siehe
[Installation](../installation.de.md#drucker-und-usb).

### Können mehrere Personen ReceiptPi nutzen?

Ja. Jede Person kann ein Konto mit eigenen Entwürfen, eigenem Verlauf und
eigenen Einstellungen bekommen, siehe [Anmeldung und Konten](../settings/accounts.de.md).

### Komme ich auch von unterwegs an ReceiptPi?

Es ist für dein Heimnetz gemacht. Mach es nicht direkt im Internet
erreichbar. Willst du von unterwegs darauf zugreifen, nutze ein VPN in
dein Heimnetz.

### Werden meine Daten irgendwohin geschickt?

Nein, alles bleibt auf dem Raspberry Pi. Nur was eine Funktion braucht,
verlässt ihn: die Koordinaten deiner Wetter-Standorte gehen an den
Wetterdienst, die beobachteten Projektnamen an GitHub, die
Versionsprüfung an GitHub.

### Was passiert bei einem Stromausfall?

Einstellungen, Konten, Verlauf und ausstehende Aufträge liegen auf der
SD-Karte und sind nach dem Neustart wieder da. Beim Start druckt
ReceiptPi den **ONLINE**-Zettel.

### Warum kommt bei jedem Start ein Zettel heraus?

Das ist der **ONLINE**-Zettel mit Name und IP-Adresse. Er zeigt, dass
ReceiptPi läuft und wo du es findest.

### Wie aktualisiere ich ReceiptPi?

Wenn du es mit dem Installer installiert hast: Führe in dem Ordner, aus
dem du installiert hast, `git pull` und danach noch einmal
`sudo ./deploy/install.sh` aus. Konfiguration und Einstellungen bleiben
erhalten.

### Verblasst der Bon?

Thermopapier verblasst über Monate, schneller in der Sonne oder bei
Wärme. Für Einkaufszettel und Notizen ist das kein Problem. Was du
aufheben willst, fotografierst du am besten.

## Gut zu wissen

Deine Frage ist nicht dabei? Schau unter [Fehlerbehebung](troubleshooting.de.md)
oder in die [Übersicht](../README.de.md).

## Klappt nicht?

Siehe [Fehlerbehebung](troubleshooting.de.md).

---

[Zurück zur Übersicht](../README.de.md)
