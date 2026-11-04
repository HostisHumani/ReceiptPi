# Entwickler-Referenz

*[English version](developer-reference.md)*

Die technischen Details von ReceiptPi für Entwickler und alle, die genau
wissen wollen, wie etwas funktioniert: Projektstruktur, Routen und
Endpunkte, Anmeldung und Rollen, MQTT-Themen, Speicherung und
Sicherheit. Wie man ReceiptPi *benutzt*, steht im
[Rest der Dokumentation](README.de.md).

## Entwicklung ohne Installationsskript


```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp config.example.py config.py
# config.py mit echten Werten füllen (HA-Token, Netatmo-Entities, Fritz!Box-Zugang, ...)

python3 app.py
```

Server läuft dann auf Port 5000, Web-UI unter `http://<pi-hostname>:5000`.
`python3 app.py` nutzt den Flask-Entwicklungsserver – für den Dauerbetrieb
läuft der Service stattdessen über Gunicorn mit `gunicorn.conf.py` und genau einem Worker, da die Print-Queue prozesslokal arbeitet.
Bei jedem Dienststart druckt Gunicorn einmal einen kurzen Boot-Gruß
(Hostname und IP), aus einem eigenen kurzlebigen Prozess
(`boot_greeting.py`) – nicht erneut, wenn der Worker neu startet. Ein nicht
erreichbarer Drucker oder ein Gruß, der länger als 30 Sekunden braucht,
wird nur protokolliert; der Server startet trotzdem.

Die meisten Einstellungen (Ruhezeit-Regeln, Wetter-Standorte, beobachtete
GitHub-Repos, SSH-Ziele für den Systembericht, Logos je Druckart,
UI-Sprache) werden komplett über die Settings-Seite der Web-UI verwaltet –
dafür muss `config.py` nicht angefasst werden. Nur Bootstrap-Werte
(Secret Key, API-Token, Drucker-USB-IDs, echte Zugangsdaten wie
HA-Token oder Fritz!Box-Passwort) bleiben in `config.py`. Bis ein
Admin-Konto eingerichtet ist, ist die Web-UI im Netzwerk offen, siehe
[Anmeldung und Konten](#anmeldung-und-konten).

## Struktur

```
receiptpi/
├── app.py                 Flask-App erzeugen, Blueprints registrieren
├── gunicorn.conf.py       on_starting-Hook, startet den Boot-Gruß einmal pro Dienststart
├── boot_greeting.py       Boot-Gruß-Bon (läuft als eigener kurzlebiger Prozess)
├── print_queue.py         zentrale Druck-Queue + Druckregeln-Prüfung
├── printer.py             Drucker-Hardware-Zugriff (USB)
├── printer_status.py      Druckerstatus ohne Druck (USB-Präsenz + Port-Status der Druckerklasse), genutzt von /health und MQTT
├── mqtt_bridge.py         optionale MQTT-Bridge: Druckbefehle rein, Druckerstatus raus, Home Assistant Discovery
├── security.py            CSRF-Schutz, API-Token-Schutz, JSON-Parsing, Zugriffsmarkierungen für die Login-Prüfung
├── auth_store.py          Konten und Anmeldungen der Web-UI (SQLite STATE_DIR/auth.db, 0600)
├── user_prefs.py          persönliche Einstellungen pro Konto (Oberflächensprache, Design, Bon-Sprache, Kacheln; gespeichert in auth.db)
├── secrets_crypto.py      Fernet-Verschlüsselung für System-Report-SSH-Passwörter, den API-Token des Rezeptmanagers und das MQTT-Passwort (Schlüssel in STATE_DIR, nicht im Git)
├── settings_store.py      zentrale Settings (JSON in STATE_DIR, nicht im Projektordner)
├── history_store.py       SQLite-Druckhistorie (STATE_DIR), Auto-Pruning nach 180 Tagen
├── lists_store.py          angefangene Listen-/Task-Karten-Entwürfe pro Konto (STATE_DIR, 0600), gelöscht nach Druck
├── pending_store.py        blockierte/fehlgeschlagene Druckaufträge (STATE_DIR, 0600), siehe "Ausstehende Aufträge" unten
├── logos.py                Logo-Auflösung je Druckart, Upload-Validierung, Seeding
├── i18n.py                 minimales Übersetzungs-Lookup (JSON-Dateien, kein Flask-Babel)
├── module_catalog.py       Registry der ein-/ausschaltbaren Home-Module (Key, Icon, URL, Gruppe auf der Startseite) + deren tatsächlicher Aktiv-Zustand
├── text_style.py           Easy-Read-Textgrößen-Skalierung, gemeinsam für Druck und Web-UI; Bereinigung von Steuerzeichen im Drucktext
├── themes.py                Registry der wählbaren Web-UI-Farbschemata
├── static_versioning.py     Inhalts-Hash-URLs (`?v=`) für statische Dateien (Cache-Busting)
├── version.py               liest VERSION, minimaler Vergleich für ReceiptPis eigenes Versionsschema
├── VERSION                  einzige Versionsquelle (z.B. "1.2.3")
├── config.example.py       Vorlage für config.py (lokal ausfüllen)
├── deploy/
│   ├── install.sh              Installationsskript für Raspberry Pi OS Lite; ohne laufendes systemd aktiviert es die Units nur
│   ├── generate_config.py      erzeugt beim ersten Start /etc/receiptpi/config.py mit eigenem SECRET_KEY pro Gerät
│   ├── requirements.lock       Python-Pakete mit Hashes festgelegt, genutzt von install.sh
│   ├── systemd/                Units für Server, Erststart und Watcher, Watcher-Timer
│   ├── udev/                   Regel für den Druckerzugriff des Dienst-Benutzers
│   └── image/                  Image-Bau aus Raspberry Pi OS Lite (.github/workflows/image.yml, nur von Hand gestartet)
├── modules/
│   ├── lists/                 Einkaufslisten, To-Do-Listen, Task-/Kanban-Karten
│   ├── message/               Statusmeldungen (freier Titel + Text)
│   ├── images/                Bilder drucken (drei Dithering-Verfahren + Vorschau)
│   ├── wifi/                  Gäste-WLAN-Zettel (Text + QR-Code)
│   ├── weather/                Wetterbericht (DWD + Netatmo)
│   ├── system/                 Systembericht (Proxmox/PBS via SSH, plus eigene SSH-Befehle für andere Hosts)
│   ├── games/                  Offline-Spiele (Sudoku, Würfelblock, Tic-Tac-Toe)
│   ├── recipes/                Rezeptsuche/-druck + Einkaufslisten-Import (Mealie)
│   ├── automation/            generischer Automation-Webhook
│   ├── history/                Druckhistorie-Dashboard
│   ├── pending/                 Ausstehende Druckaufträge ansehen/nachdrucken/verwerfen
│   ├── backup/                  Sicherung und Wiederherstellung der Einstellungen (.zip)
│   ├── auth/                    Anmeldung, Ersteinrichtung, eigenes Konto, persönliche Einstellungen, Nutzerverwaltung, Login- und Rollenprüfung für jeden Request
│   └── settings/                Settings-Seiten (Web-UI) + Settings-API
├── watchers/
│   ├── github_star_watch.py     Timer/Cron: druckt bei neuem GitHub-Star
│   ├── fritzbox_wifi_watch.py    Timer/Cron: druckt WLAN-Zettel bei aktiviertem Gästenetz
│   ├── storm_warning_watch.py    Timer/Cron: druckt bei neuer aktiver Unwetterwarnung
│   ├── update_check_watch.py     Timer/Cron (alle 12h): cached, ob ein neueres GitHub-Release existiert (für den Footer)
│   └── crontab.example           Cron-Vorlage für alle vier Watcher, für Installationen ohne deploy/install.sh
├── scripts/
│   ├── manage_users.py         Kontenverwaltung auf der Konsole (create-admin, reset-password, list, revoke-sessions)
│   └── docs_screenshots/       Screenshots für docs/ aus einer Demo-Instanz mit erfundenen Daten (run.sh)
├── docs/                    die Dokumentation (Englisch + Deutsch), siehe docs/STYLE.md
├── assets/example-logos/    mitgelieferter Logo-Startsatz (Strichzeichnungen)
├── static/
│   ├── style.css             Design-System (Spacing/Farben/Themes, Card-/Tile-/Icon-Stile)
│   ├── draft-autosave.js      generisches Auto-Save für die Listen-/Task-Karten-Formulare
│   ├── mealie-import.js       Import einer Mealie-Einkaufsliste auf der Einkaufszettel-Seite
│   ├── image-preview.js       Vorschau des 1-Bit-Druckbilds auf der Bilder-Seite
│   ├── manifest.json          Web-App-Manifest (Name/Icons/Farben für den Homescreen)
│   ├── sw.js                  Service Worker (ausgeliefert unter /sw.js, nur über HTTPS)
│   ├── fonts/                 selbst gehostete IBM-Plex-Schriften (siehe LICENSE.txt in diesem Ordner)
│   └── icons/                 lokaler Lucide-SVG-Iconsatz (siehe LICENSE in diesem Ordner)
└── templates/                gemeinsames Layout und Modul-Seiten
    ├── base.html
    ├── home.html
    ├── shopping.html
    ├── lists_*.html            Listen-Übersicht, To-Do-Seite, Task-/Kanban-Karten-Seite
    ├── message.html
    ├── images.html
    ├── wifi.html
    ├── weather.html
    ├── system.html
    ├── history.html
    ├── games_*.html            Spiele-Übersicht + eine Seite je Spiel
    ├── recipes*.html           Rezeptsuche/Zufallsrezept + Rezept-Vorschau
    ├── auth_*.html             Anmeldung, Ersteinrichtung, eigenes Konto, 403-Seite
    ├── preferences*.html       persönliche Einstellungen und Startseiten-Kacheln eines Kontos
    └── settings_*.html        Settings-Übersicht + eine Unterseite je Bereich
```

Jede Funktion ist als eigener Flask-Blueprint umgesetzt. Nicht abgefangene Ausnahmen betreffen nur den jeweiligen Request; die Module teilen sich weiterhin Prozess, Druck-Queue und Settings-Store.

## Web-UI

Die Web-UI ist mobile-first (eine einzelne schmale Spalte, kein eigenes
Desktop-Layout) und als kleines Card-/Kachel-Design-System statt reiner
Formulare umgesetzt:

- **Home/Dashboard** (`/`) – ein Status-Streifen (Drucker-Erreichbarkeit,
  live per `/health`-Poll alle 60 Sekunden geprüft; wie viele der
  schaltbaren Module aktuell aktiv sind; Druckanzahl aus dem
  Verlaufs-Store – mit Konten die eigenen Drucke), gefolgt von einem Kachel-Grid mit Links zu jedem
  aktuell aktivierten Modul. Jedes Konto kann Kacheln ausblenden und
  innerhalb ihrer Gruppe umsortieren, siehe
  [Persönliche Einstellungen](#persönliche-einstellungen).
- **Navigation** – ein Topbar-Wordmark, ein kleiner Drucker-Status-Punkt
  und ein Burger-Menü (Verlauf und Ausstehende Aufträge; Einstellungen und
  nach der Anmeldung Nutzer – nur für Admins, siehe
  [Anmeldung und Konten](#anmeldung-und-konten); nach der Anmeldung
  zusätzlich Meine Einstellungen, Konto und Abmelden).
- **Themes** – 6 wählbare Farbschemata (Warm [Standard], Forrest, Dark
  Lime, Frost, Butter Bean, White Purple). Den Gerätestandard legt
  `/settings/design` fest; mit Konten kann jedes Konto unter Meine
  Einstellungen ein eigenes wählen. Angewendet über ein
  `data-theme`-Attribut plus CSS Custom Properties. Warm ist das einzige Theme, das zusätzlich
  automatisch der Hell-/Dunkel-Einstellung von System/Browser folgt.
- **Icons** – ein lokaler Satz von Lucide-SVG-Icons unter `static/icons/`
  (keine externe Icon-Schriftart oder CDN), gerendert per CSS
  `mask-image`, sodass jedes Icon automatisch die Akzentfarbe des
  aktiven Themes annimmt, statt fest auf eine Farbe codiert zu sein.
  Siehe [Third-Party Licenses](../README.de.md#third-party-licenses).
- **Easy-Read** – ein Textgrößen-Schalter skaliert sowohl Web-UI als auch
  Bon-Ausdruck, siehe [Easy-Read](#easy-read-große-schrift) unten.
- **Footer** – zeigt auf jeder Seite die laufende Version und einen
  Link zum GitHub-Repo; findet `watchers/update_check_watch.py` (Cron,
  alle 12h) ein neueres GitHub-Release (auch Alpha-/Prereleases),
  verlinkt der Footer direkt dorthin. Reine Information, kein Auto-Update.

Jede Seite zeigt in der Topbar ein zu ihrem Inhalt passendes kleines
Icon (auf der Startseite ein Drucker-Icon, auf der Nachrichten-Seite
ein Nachrichten-Icon, und so weiter). Ein Favicon für den Browser-Tab
gibt es noch nicht.

**Homescreen / installierbare Web-App** – `static/manifest.json` sowie
`theme-color` und `apple-touch-icon` in `templates/base.html` erlauben
es, die Web-UI mit eigenem Namen und Icon auf den Homescreen eines
Handys zu legen (iOS: Safari-Teilen-Menü → „Zum Home-Bildschirm“;
Android: Browser-Menü). Wird die UI über **HTTPS** ausgeliefert (z. B.
hinter einem Reverse-Proxy), registriert sich zusätzlich ein Service
Worker (`static/sw.js`, ausgeliefert unter `/sw.js`), sodass Chrome auf
Android „App installieren“ anbietet. Über reines HTTP
(`http://<pi>:5000`) wird kein Service Worker registriert – die
Homescreen-Verknüpfung funktioniert trotzdem.

Der Service Worker cached nur `/static/`-Dateien derselben Origin, die
einen `?v=<Inhalts-Hash>` tragen (Stylesheet, Schriften, Icons,
Manifest) – deren URL ändert sich mit jedem geänderten Inhalt, veraltete
Antworten sind damit ausgeschlossen. Seiten, `/health`, `/print/*`,
`/ui/*`, `/settings/*` und alle anderen Anfragen gehen immer ans Netz.
Der Cache ist nach der `VERSION`-Datei benannt; ein neues Release ersetzt
den Worker und löscht den alten Cache. Ein Offline-Modus ist das nicht –
die UI braucht weiterhin den erreichbaren Pi. Die Icons unter
`static/icons/` (`icon-192.png`, `icon-512.png`, `icon-maskable-512.png`,
`apple-touch-icon.png`) sind eine **Interimslösung** aus dem bestehenden
Logo – ein echtes Icon-Set folgt.

## Anmeldung und Konten

Solange kein Admin-Konto existiert, verhält sich die Web-UI wie bisher: Jede
Seite ist für alle im Netzwerk offen, und ein Hinweis oben auf den Seiten
führt zur Ersteinrichtung. Nach der Einrichtung verlangt jede Seite der Web-UI eine
Anmeldung. Ohne Anmeldung erreichbar sind nur `/health`, `/static/*`,
`/sw.js`, `/login`, `/logout` und die per `X-Api-Token` geschützten JSON-API-Endpunkte
(siehe [Endpunkte](#endpunkte)). Welche Routen offen sind, entscheiden die
Decorators an der jeweiligen Route, nicht die URL; eine Route ohne einen
davon verlangt eine Admin-Anmeldung (siehe Rollen unten). Eine ohne Anmeldung aufgerufene Seite leitet
auf `/login` weiter. Ein Formular, das von einer Seite mit abgelaufener
Anmeldung abgeschickt wird, leitet ebenfalls dorthin weiter (`303`); nach der
Anmeldung landet der Browser wieder auf der Seite, auf der das Formular war
(aus dem `Referer`, wenn er auf diesen Host zeigt, sonst auf der Startseite).
Das Formular selbst wird nicht erneut gesendet, es wird also nichts doppelt
gedruckt. Jede andere Anfrage (`fetch()`, JSON oder jede Anfrage mit
`X-Api-Token`-Header) bekommt `401` mit
`{"status": "error", "error": "login_required", "detail": "..."}`.

**Ersteinrichtung** (`/setup`, antwortet mit `404`, sobald ein Admin
existiert): Benutzername (3–32 Zeichen aus `a-z 0-9 . _ -`, gespeichert in
Kleinbuchstaben), Passwort (10–128 Zeichen, sonst keine Vorgaben) und ein
Einrichtungscode:
- Ist `API_TOKEN` in `config.py` gesetzt, ist der Code dieses Token.
- Ohne Token druckt ein Knopf „Code drucken“ einen Einmalcode auf dem
  Bondrucker. Er gilt 15 Minuten, nur der zuletzt gedruckte Code zählt, er
  liegt nur im Arbeitsspeicher des Serverprozesses und taucht weder im
  Druckverlauf noch in den ausstehenden Aufträgen oder in einem Log auf.
  Schlägt der Druck fehl, ist der Code ungültig. Dann einen neuen anfordern
  oder den Admin mit dem Konsolenskript unten anlegen.

**Anmeldung** (`/login`): Benutzername und Passwort; ein falscher Name und
ein falsches Passwort bekommen dieselbe Antwort. Ohne „Eingeloggt bleiben“
endet die Anmeldung beim Schließen des Browsers oder nach 24 Stunden ohne
Nutzung, mit Haken gilt sie 90 Tage und verlängert sich bei Nutzung. Unter
`/account` lässt sich das eigene Passwort ändern (beendet alle anderen
Anmeldungen des Kontos), und „Auf allen Geräten abmelden“ beendet alle
Anmeldungen außer der aktuellen. Abmelden steht im Menü.

**Rollen**: Jedes Konto ist entweder `admin` oder `user` (Nutzer). Admins
dürfen alles. Nutzer dürfen Startseite, Nachricht, Listen (einschließlich
Entwürfen und `/shopping`), Wetter, Gast-WLAN, Rezepte, Spiele und Bilder
verwenden sowie ihr eigenes Konto, ihre eigenen persönlichen Einstellungen
(`/preferences`, `/preferences/home`, siehe
[Persönliche Einstellungen](#persönliche-einstellungen)), ihren eigenen
Druckverlauf (`/history`)
und ihre eigenen ausstehenden Aufträge (`/pending`, `/pending/print`,
`/pending/discard`, siehe unten) und Abmelden – einschließlich der
Formular-Posts dieser Seiten. Alles andere ist nur für Admins: alle
Einstellungsseiten und `/ui/settings/*`-Routen, der Systembericht
(`/system`, `/ui/system`), die Sicherung, die Nutzerverwaltung sowie
Sprache und Design als Gerätestandard. Routen sind nur für Admins, sofern sie nicht anders markiert
sind; eine später ergänzte Route ohne Markierung ist für Nutzer also
gesperrt. Das Menü zeigt Einstellungen und Nutzer nur Admins, und die
Startseite zeigt Nutzern keine Kachel für eine
Seite, die sie nicht öffnen dürfen. Öffnet ein Nutzer eine Admin-Seite im
Browser, kommt eine übersetzte `403`-Seite mit Link zur Startseite; eine
`fetch()`- oder JSON-Anfrage bekommt `403` mit
`{"status": "error", "error": "forbidden", "detail": "..."}`. Die Rolle wird
bei jeder Anfrage aus `auth.db` gelesen; eine Rollenänderung, Sperre oder
Löschung wirkt also mit der nächsten Anfrage des Kontos.

**Verlauf und ausstehende Aufträge pro Konto**: Jeder Druckauftrag gehört
dem Konto, das ihn ausgelöst hat – von der Warteschlange über den Druck,
den Verlauf und die ausstehenden Aufträge bis zum Nachdruck von dort.
Aufträge ohne angemeldetes Konto (API-Token, Webhook, MQTT, Watcher,
Boot-Gruß, Unwetterwarnung oder solange kein Admin eingerichtet ist)
gehören „System“. Ein Konto wird mit seiner ID und seiner Anlagezeit
gespeichert; ein nach dem Zurücksetzen von `auth.db` angelegtes Konto
erbt deshalb keine Einträge eines älteren Kontos, auch nicht bei
gleicher ID. Nutzer sehen auf `/history` und `/pending` nur ihre eigenen
Einträge und können nur eigene nachdrucken oder verwerfen; IDs fremder
Einträge werden ignoriert. Admins sehen alle Einträge mit Besitzer und
können nach Besitzer filtern (alle, System, ein Konto oder ein gelöschtes
Konto); Einträge eines gelöschten Kontos bleiben erhalten und erscheinen
für Admins als „Gelöschtes Konto #id“. Den gespeicherten Inhalt eines
ausstehenden Auftrags (etwa ein WLAN-Passwort) sieht niemand, nur die
Felder der Liste. Der Nachdruck eines Nutzers wartet wie ein neuer
Auftrag auf das Ende der Ruhezeit; nur der Nachdruck eines Admins
umgeht sie. Die Startseite zählt die eigenen Drucke (auch bei Admins),
das Badge im Menü die eigenen ausstehenden Aufträge (Admins: alle).
Rate-Limit, Duplikat-Sperre und Ruhezeiten gelten für alle gleich, und
der MQTT-Status (letzter Auftrag, Anzahl ausstehender Aufträge) umfasst
weiter das ganze Gerät. Beim ersten Start gehen vorhandene
Verlaufseinträge aus der Web-UI (Quelle „ui“ und deren Nachdrucke) an
den Admin mit der kleinsten ID; alles andere bleibt System.

**Persönliche Einstellungen**: Jedes Konto kann eigene Oberflächensprache,
eigenes Design, eigene Bon-Sprache und eigene Startseiten-Kacheln wählen
und hat eigene Listen-Entwürfe, siehe
[Persönliche Einstellungen](#persönliche-einstellungen).

**Nutzerverwaltung** (`/settings/users`, nur Admins, auch im Menü und auf
der Einstellungsseite): listet alle Konten mit Rolle, Status (aktiv,
gesperrt, Startpasswort) und letzter Anmeldung. Ein Admin kann ein Konto
anlegen (Name und Rolle), seine Rolle ändern, es sperren und entsperren,
sein Passwort zurücksetzen und es löschen (nach einer Bestätigung). Beim
Anlegen und beim Zurücksetzen zeigt die Seite ein vom Server erzeugtes
Startpasswort (16 Zeichen aus `a-z` und `2-9` ohne verwechselbare Zeichen,
in Vierergruppen mit `-` getrennt; die Striche gehören zum Passwort) genau
einmal an: Es wird nicht gespeichert (nur sein Hash), nicht geloggt, und
die Seite wird mit `Cache-Control: no-store` ausgeliefert. Ein Konto mit
Startpasswort kann nur seine Kontoseite öffnen, um ein eigenes Passwort
festzulegen; jede andere Seite leitet dorthin um, und `fetch()` bekommt
`403` mit `"error": "password_change_required"`. Zurücksetzen und Sperren
beenden sofort alle Anmeldungen des Kontos. Ein gesperrtes Konto kann sich
nicht anmelden und bekommt dieselbe Antwort wie bei einem falschen
Passwort. Der letzte aktive Admin kann nicht gelöscht, gesperrt oder zum
Nutzer gemacht werden, auch nicht durch zwei gleichzeitige Anfragen. Das
eigene Konto lässt sich auf dieser Seite nicht ändern (das eigene Passwort
steht unter Konto).

**Schutz gegen Durchprobieren**: Fehlversuche werden auf `/login` pro
Benutzername und pro Client-IP gezählt (ebenso für das aktuelle Passwort
auf `/account`), auf `/setup` pro Client-IP und insgesamt. Ab dem 5.
Fehlversuch werden weitere Versuche 30 s lang abgewiesen, mit jedem
weiteren Fehlversuch doppelt so lange, höchstens 15 Minuten, mit derselben
Antwort für jeden Benutzernamen. Die Zähler liegen nur im Arbeitsspeicher
des Servers und werden durch einen Neustart des Dienstes zurückgesetzt.

**Konsolenskript** – der Notweg (Passwort vergessen, kein Drucker für den
Einrichtungscode). Als Dienst-Nutzer im Projektverzeichnis mit dem venv des
Projekts ausführen:

```bash
cd ~/receiptpi
~/receiptpi-env/bin/python scripts/manage_users.py create-admin <name>
~/receiptpi-env/bin/python scripts/manage_users.py reset-password <name>   # jedes Konto; beendet alle seine Anmeldungen, kein Startpasswort
~/receiptpi-env/bin/python scripts/manage_users.py list
~/receiptpi-env/bin/python scripts/manage_users.py revoke-sessions <name>
```

Passwörter werden nur im Terminal abgefragt, nie als Argument übernommen.
Das Skript verweigert den Start unter einem anderen Nutzer als dem
Eigentümer von `STATE_DIR` (z. B. als root per `sudo`), weil der Dienst eine
als root geschriebene `auth.db` nicht mehr lesen könnte.

**Speicherung**: Konten, Anmeldungen und persönliche Einstellungen liegen in
`STATE_DIR/auth.db` (SQLite, Dateirechte 0600; die persönlichen
Einstellungen als JSON-Objekt pro Konto, ein ungültiger Wert gilt als
„Gerätestandard“). Passwörter werden als scrypt-Hash gespeichert
(Standard von Werkzeug), Anmeldungen nur als SHA-256-Hash des Cookie-Werts.
`auth.db` gehört nicht zur Sicherung der Einstellungen, die persönlichen
Einstellungen also auch nicht. Listen-Entwürfe liegen in
`STATE_DIR/lists_drafts.json` (0600), ein Bereich pro Konto plus System. Dienst stoppen,
`auth.db` löschen und Dienst wieder starten stellt den Zustand ohne Admin
wieder her (Web-UI offen).

**Konfiguration** (`config.py`, siehe `config.example.py`):
- `LOGIN_COOKIE_SECURE` (Standard `False`) – markiert das Login-Cookie
  `receiptpi_session` als `Secure`. Ausgeschaltet lassen, solange die Web-UI
  über einfaches HTTP genutzt wird (`http://<pi>:5000`): Browser schicken ein
  Secure-Cookie nicht über HTTP, dann könnte niemand angemeldet bleiben. Nur
  einschalten, wenn die UI ausschließlich über HTTPS erreichbar ist. Das
  Cookie ist immer `HttpOnly` und `SameSite=Lax`. Über einfaches HTTP gehen
  Passwörter und Login-Cookie unverschlüsselt durch das lokale Netzwerk.
- `TRUSTED_PROXY_COUNT` (Standard `0`) – Anzahl der Reverse-Proxys vor dem
  Server. Nur bei einem Wert über 0 werden `X-Forwarded-For/-Proto/-Host/
  -Port` ausgewertet (für genau so viele Stufen), z. B. für den Schutz pro
  Client-IP; bei 0 werden sie ignoriert.

**Wirkung auf API und Watcher**: Endpunkte mit `X-Api-Token` funktionieren
genau wie bisher und akzeptieren nie das Login-Cookie, nur den Header – die
Watcher und Automationen brauchen keine Anmeldung. Mit leerem `API_TOKEN`
und vorhandenem Admin bleibt `/print/*` offen, die JSON-Settings-API unter
`/settings/` antwortet aber mit `403`; die Settings-Seite zeigt dazu einen
Warnhinweis.

## Settings-Seiten

`/settings` ist eine Übersichtsseite (Kachel-Grid, gruppiert in
Allgemein / Drucken / Integrationen / System), die auf eine eigene
Unterseite pro Bereich verlinkt – statt eines langen Formulars:

- `/settings/language` – Sprache als Gerätestandard (Deutsch/Englisch): Oberflächensprache der Anmeldeseite und aller Konten ohne eigene Wahl sowie Bon-Sprache aller Aufträge ohne eigene
- `/settings/design` – Farbschema der Web-UI als Gerätestandard
- `/settings/modules` – einzelne Home-Module ein-/ausblenden, siehe [Modul-Schalter](#modul-schalter)
- `/settings/print-rules` – Ruhezeit-Regeln (mehrere unabhängige Regeln, je mit eigenen Wochentagen und Zeitfenster) + Rate-Limit + Duplikat-Sperre + der Easy-Read-Textgrößen-Schalter
- `/settings/logos` – globaler Logo-Schalter, Standard-Logo, sowie je Druckart eigener Schalter/Upload/Vorschau (fällt auf das Standard-Logo zurück, wenn kein eigenes gesetzt ist)
- `/settings/weather` – Wetterbericht-Anbieter (DWD oder Open-Meteo), Wetter-Standorte, sowie ein unabhängiger Unwetterwarnungs-Anbieter (DWD, MeteoAlarm oder NWS) mit eigenem Aktiv-Schalter und optionalem "Ruhezeiten ignorieren"
- `/settings/github-watch` – beobachtete GitHub-Repos
- `/settings/system-report` – eine frei erweiterbare Liste von SSH-Zielen für den Systembericht, siehe [Systembericht](#systembericht) unten
- `/settings/recipes` – Rezeptmanager (Aus / Mealie), Mealie-Base-URL, verschlüsselter API-Token, Verbindungstest, siehe [Rezepte](#rezepte) unten
- `/settings/mqtt` – Verbindung zum MQTT-Broker, Themen-Präfix und Home Assistant Discovery, siehe [MQTT](#mqtt) unten
- `/settings/backup` – Sicherung der Einstellungen und Logos herunterladen oder einspielen (siehe unten)
- `/settings/users` – Nutzerverwaltung, siehe [Anmeldung und Konten](#anmeldung-und-konten)

**Sicherung und Wiederherstellung** (`/settings/backup`): Die Sicherung ist
eine `.zip` mit einer Allowlist-Kopie der Einstellungen, allen Logo-Dateien
und einem Manifest (Format- und App-Version, SHA-256 je Datei). Sie enthält
nie Geheimnisse: nicht `config.py`, nicht den API-Token, nicht den
verschlüsselten Mealie-Token oder SSH-Passwörter (auch nicht verschlüsselt),
nicht `secret.key`, nicht den SSH-`command` der Systembericht-Server und
nicht Passwort, Benutzernamen oder CA-Datei für MQTT.
Druckhistorie, ausstehende Aufträge, Listen-Entwürfe, Konten und ihre
persönlichen Einstellungen sind ebenfalls nicht enthalten. Die Datei enthält trotzdem persönliche Daten (Host- und
Benutzernamen, Orte, Adressen) und sollte entsprechend aufbewahrt werden.
Beim Einspielen wird das Archiv zuerst geprüft und eine Zusammenfassung
angezeigt; geändert wird erst nach Bestätigung. Geheimnisse auf dem Gerät
bleiben unverändert, alles, was nicht in der Sicherung ist, muss danach neu
eingegeben werden (die Ergebnisseite listet es auf), und Werte aus
`config.py` müssen auf einem neuen Gerät von Hand gesetzt werden. Sobald
ein Admin-Konto existiert, brauchen Download und Wiederherstellung eine
Admin-Anmeldung und kein Token. Ohne Admin-Konto gilt: Ist `API_TOKEN`
gesetzt, fragen Download und Wiederherstellung danach; ohne Token ist die
Wiederherstellung gesperrt. Eingespielt wird nur bei leerer
Druck-Queue, vorher wird der aktuelle Stand gesichert (die letzten 5 liegen
in `STATE_DIR/restore_snapshots/`), ein Neustart ist nicht nötig. Ein Archiv
darf höchstens 8 MB groß sein; ein hochgeladenes, nicht bestätigtes Archiv
wird nach einer Stunde verworfen. Grenze:
Ein Stromausfall mitten im Einspielen kann eine Mischung aus alten und neuen
Dateien hinterlassen – der letzte Schnappschuss ist dann der Rückweg.

## Endpunkte

Alle `/print/*`- und `/settings/*`-Endpunkte verlangen den Header
`X-Api-Token: <wert>`, sobald `API_TOKEN` in `config.py` gesetzt ist (leer = kein Schutz).
Das Login-Cookie der Web-UI akzeptieren sie nie. Mit leerem `API_TOKEN`
antworten die Settings-API-Endpunkte mit `403`, sobald ein Admin-Konto
existiert, während `/print/*` offen bleibt (siehe
[Anmeldung und Konten](#anmeldung-und-konten)).
Vor jedem Druckauftrag greifen zusätzlich die zentralen Druckregeln: Ruhezeiten, Rate-Limit und Duplikat-Sperre.
Ein blockierter Auftrag antwortet mit `429`.

**Drucken**
- `POST /print/message` – `{ "title": "...", "text": "..." }`
- `POST /print/list` – `{ "title": "...", "items": ["..."] }` – Einkaufsliste, unverändert seit vor dem Lists-Modul, weiterhin voll abwärtskompatibel
- `POST /print/todo` – `{ "title": "...", "items": ["..."] }` – To-Do-Liste, gleiche Form wie `/print/list`
- `POST /print/task` – `{ "title": "...", "description": "optional", "items": ["optionale Checkliste"], "priority": "low"|"medium"|"high", "due_date": "YYYY-MM-DD" }` – druckbare Task-/Kanban-Karte; nur `title` ist Pflicht
- `POST /print/image` – `{ "image_base64": "...", "dither": "floyd-steinberg" }` (max. 6000px pro Seite, 12MB); `dither` ist optional: `floyd-steinberg` (Standard), `atkinson` oder `bayer`. Bilder, die breiter als 512px sind, werden auf 512px Breite verkleinert (schmalere behalten ihre Größe), die EXIF-Ausrichtung wird nach dem Skalieren angewendet; Atkinson ist auf 1.024.000 Pixel nach dem Skalieren (512x2000) und 15 Sekunden Rechenzeit begrenzt, sonst `400`/`504`
- `POST /print/image/preview` – gleicher Body wie `/print/image`, liefert das 1-Bit-Druckbild als PNG, ohne zu drucken (keine Druckregeln, keine Historie)
- `POST /print/wifi` – `{ "ssid": "...", "password": "...", "auth_type": "WPA" }`
- `POST /print/weather` – optional `{ "location": "Berlin" }`, sonst Standard-Standort; auch `GET /print/weather?location=Berlin`
- `POST /print/system` – kein Body nötig
- `POST /print/automation` – `{ "title": "optional", "text": "..." }` – generischer Automation-Webhook (kompatibel mit Home Assistant, Node-RED, n8n oder jedem Tool, das HTTP-POST beherrscht)
- `GET /health` – Drucker-Erreichbarkeit prüfen, ohne zu drucken (kein Token nötig): USB-Präsenz plus Statusbyte des Druckers, 10 s für alle Aufrufer gemeinsam gecacht, kein Druckauftrag und kein USB-Reset. 200 `{"status": "ok", "printer": "reachable", "state": "ready", "problem": false}`, solange der Drucker angeschlossen und eingeschaltet ist – auch bei leerem Papier oder offenem Deckel, dann ist `state` `paper_out`/`error` und `problem` `true`; `state` ist `unknown`, wenn sich das Statusbyte gerade nicht lesen lässt (während eines Druckauftrags oder nach dem Einschalten bis zum ersten Druck, solange der Kernel-Treiber `usblp` den Drucker belegt). 500 `{"status": "error", "detail": "...", "state": "offline", "problem": false}`, wenn der Drucker nicht am USB-Bus hängt (ausgeschaltet oder abgezogen)

**Settings-API**
- `GET /settings/api` – alle Einstellungsbereiche als JSON; verschlüsselte Geheimnisse sind durch Flags ersetzt (`has_token` für den Mealie-Token, `has_password` für SSH- und MQTT-Passwörter)
- `POST /settings/print_rules` – Rate-Limit/Duplikat-Fenster ändern
- `GET|POST /settings/quiet_hours/rules`, `POST /settings/quiet_hours/rules/<id>/toggle`, `DELETE /settings/quiet_hours/rules/<id>`
- `GET|POST /settings/weather/locations`, `DELETE /settings/weather/locations/<name>`
- `GET /settings/system_report` – aktuelle Liste der System-Report-Hosts (JSON, Passwörter als `has_password`-Flag redigiert)
- `POST /settings/system_report` – `{ "name": "...", "role": "proxmox"|"pbs"|"custom", "host": "...", "user": "...", "command": "nur bei role=custom Pflicht", "password": "optional" }` – fügt einen Host hinzu; `host` und `user` müssen einer Allowlist entsprechen (DNS-Name oder IP-Adresse; POSIX-Benutzername; kein führendes `-`, keine Leerzeichen), sonst `400`
- `DELETE /settings/system_report/<id>` – entfernt einen Host
- `GET|POST /settings/github_watch/repos`, `DELETE /settings/github_watch/repos/<owner>/<repo>`
- `GET|POST /settings/logos/config` – globale/pro Modul Logo-Schalter
- `POST /settings/logos/upload/<slot>`, `DELETE /settings/logos/upload/<slot>` – Logo-Bild hochladen/löschen (base64), `slot` ist `default` oder ein Modul-Key

**Rezepte (Web-UI-Routen)**

Das Rezepte-Modul hat keine `/print/*`-JSON-API und keine Settings-API –
seine Routen bedienen die Web-UI und brauchen kein `X-Api-Token`, aber eine
Anmeldung, sobald ein Admin-Konto existiert. Alle
antworten mit `404`, solange das Modul inaktiv ist (siehe
[Modul-Schalter](#modul-schalter)).
- `GET /recipes?q=<Begriff>` – Suchseite mit Treffern
- `GET /recipes/view/<slug>` – Rezept-Vorschau
- `GET /recipes/random?ingredients=<a,b>&match=all` – Zufallsrezept; `ingredients` optional (max. 5), `match=all` verlangt alle statt irgendeiner Zutat
- `POST /ui/recipes/print` – Formular-Post (CSRF-geschützt), Feld `slug`
- `GET /recipes/import/lists` – Mealie-Einkaufslisten als JSON (für den Import auf `/shopping`)
- `GET /recipes/import/lists/<id>` – nicht abgehakte Einträge einer Liste als JSON, fertig formatiert als Einkaufszettel-Zeilen

## MQTT

Optional und standardmäßig aus. Eingerichtet wird es auf `/settings/mqtt`;
es nutzt das Paket `paho-mqtt` aus `requirements.txt` (bei einer
bestehenden Installation `pip install -r requirements.txt` im venv des
Dienstes erneut ausführen). Ist MQTT ausgeschaltet, wird nichts davon
importiert oder gestartet.

Themen, mit `<p>` als einstellbarem Präfix (Standard `receiptpi`):

| Thema | Richtung | Retained | Inhalt |
|---|---|---|---|
| `<p>/availability` | aus | ja | `online` / `offline` (auch als Last Will) |
| `<p>/state` | aus | ja | JSON, nur bei Änderung veröffentlicht |
| `<p>/cmd/message` | ein | – | `{ "title": "optional", "text": "..." }` – wie `/print/automation` |
| `<p>/cmd/list` | ein | – | `{ "title": "...", "items": ["..."] }` – wie `/print/list` |
| `<p>/cmd/todo` | ein | – | `{ "title": "...", "items": ["..."] }` – wie `/print/todo` |
| `<p>/cmd/task` | ein | – | gleicher Body wie `/print/task` |
| `<p>/cmd/weather` | ein | – | `{}` oder `{ "location": "..." }` – wie `/print/weather` |
| `<p>/result` | aus | nein | ein Ergebnis pro Befehl |

Jeder Befehl kann eine optionale `"id"` mitbringen (1–64 Zeichen
`A-Z a-z 0-9 _ . -`), die im Ergebnis zurückkommt:

```json
{"id": "k42", "cmd": "list", "status": "printed", "code": 200, "detail": "2 items printed"}
{"id": "k43", "cmd": "message", "status": "error", "code": 429, "detail": "..."}
```

Die Statusnachricht:

```json
{"printer": "ready", "printer_since": "2030-01-15T08:00:12", "problem": false, "pending": 0,
 "last_job_type": "shopping", "last_job_result": "ok", "last_job_at": "2030-01-15T07:59:58", "version": "1.3.0"}
```

`printer` ist `ready`, `paper_out`, `error` (jeder andere Druckerfehler,
z. B. offener Deckel), `offline` (USB-Gerät weg) oder `unknown`. Der Wert stammt aus dem Port-Status der USB-Druckerklasse
(kein Druckauftrag, kein USB-Reset), abgefragt alle 15 Sekunden, nach jedem
Druckauftrag und sobald das USB-Gerät wieder auftaucht; die Anwesenheit wird
alle 5 Sekunden geprüft. Hält ein Kernel-Treiber (`usblp`) das
Drucker-Interface, lautet der Status `unknown`.

Befehle durchlaufen genau dieselben Prüfungen wie die HTTP-API:
Eingabeprüfung, Ruhezeiten, Rate-Limit, Duplikatsperre und die Liste der
ausstehenden Aufträge (Ergebniscode `429`, wenn blockiert). Nachrichten über
64 KiB, ungültiges JSON, unbekannte Befehle und deaktivierte Module werden
mit einem Ergebnis abgelehnt. Retained-Nachrichten auf Befehlsthemen werden
ignoriert. WLAN-Zettel, Systembericht und Bilder gibt es bewusst nicht per
MQTT.

**Home Assistant Discovery** (optionaler Schalter auf der Einstellungsseite)
legt ein Gerät mit Druckerstatus, Problem, ausstehenden Aufträgen, letztem
Auftrag und einem Button „Wetterbericht drucken“ an. Beim Abschalten wird
das Gerät wieder entfernt.

**Sicherheit.** Wer auf dem Broker nach `<p>/cmd/#` schreiben darf, kann
drucken. ReceiptPi selbst unterstützt Benutzername/Passwort (verschlüsselt
gespeichert, nie angezeigt, nie über die API ausgegeben, nie im Backup), TLS
mit Prüfung von Zertifikat und Hostname sowie eine eigene CA. Benutzer und
Zugriffsregeln sind Sache des Brokers. Die Einstellungsseite warnt, wenn kein
Benutzername oder kein TLS eingerichtet ist. Eine eigene CA muss eine
saubere RFC-5280-CA sein (Erweiterungen Key Usage und Key Identifier): ab
Python 3.13 werden Zertifikate streng geprüft (`VERIFY_X509_STRICT`).
Beispiel für Mosquitto 2.x (Platzhalter für Pfade und Namen):

```
listener 8883
cafile /etc/mosquitto/certs/ca.crt
certfile /etc/mosquitto/certs/server.crt
keyfile /etc/mosquitto/certs/server.key
allow_anonymous false
password_file /etc/mosquitto/passwd
acl_file /etc/mosquitto/acl
max_packet_size 65536
```

`/etc/mosquitto/acl`:

```
user receiptpi
topic readwrite receiptpi/#
topic write homeassistant/device/#
topic read homeassistant/status

user homeassistant
topic readwrite homeassistant/#
topic read receiptpi/#
topic write receiptpi/cmd/#
```

## Nach der Installation

`requirements.txt` enthält bewusst keine Versionsnummern. `install.sh`
installiert die Versionen aus `deploy/requirements.lock`, festgelegt mit
den Hashes der PyPI-Dateien, damit jedes Gerät dieselben getesteten Pakete
bekommt.

### Watcher

Die vier Scripts unter `watchers/` (GitHub-Star-Benachrichtigung,
Gäste-WLAN-Zettel, Unwetterwarnung, Update-Check) laufen nur, wenn sie
irgendwo eingeplant sind – `app.py`/Gunicorn startet sie nie selbst.
`install.sh` richtet sie als systemd-Timer unter dem Dienst-Benutzer ein,
mit dem jeweils im Docstring des Scripts empfohlenen Intervall; der Timer
für das Gäste-WLAN wird installiert, aber nicht aktiviert, siehe
[docs/installation.de.md](installation.de.md#watcher). Ohne das
Installationsskript ist `watchers/crontab.example` eine Vorlage für Cron:
installieren mit `crontab watchers/crontab.example` (nach Anpassen der
Platzhalter `PYTHON`/`PROJECT_DIR`), oder einzelne Zeilen in eine
bestehende Crontab übernehmen (`crontab -e`).

## Features

- Listen: Einkaufslisten, To-Do-Listen und druckbare Task-/Kanban-Karten, siehe [Listen](#listen) unten
- Freie Textmeldungen
- Bilddruck aus der Web-UI oder per JSON-API, mit wählbarem Dithering (Floyd-Steinberg, Atkinson, Bayer) und einer Vorschau des exakten 1-Bit-Druckbilds
- Gäste-WLAN-Zugangsdaten und QR-Codes
- Wetterberichte (DWD oder Open-Meteo wählbar, + optional Netatmo), mit optionalem Unwetterwarnungs-Watcher (DWD, MeteoAlarm oder NWS, druckt nur bei tatsächlich aktiver Warnung)
- Systemberichte: Proxmox/PBS via SSH, plus eine Eigener-Befehl-Rolle für jeden anderen Host (z. B. ein NAS), mit optionalem verschlüsseltem Passwort-Login, siehe [Systembericht](#systembericht) unten
- Offline-Spiele (Sudoku, Würfelblock, Tic-Tac-Toe), siehe [Spiele](#spiele) unten
- Rezepte aus [Mealie](https://mealie.io): Rezepte suchen, ansehen und drucken, Zufallsrezept nach Zutat, Mealie-Einkaufsliste in den Einkaufszettel importieren, siehe [Rezepte](#rezepte) unten
- Druckhistorie-Dashboard (Statistik + paginierte Liste, SQLite-basiert)
- Ausstehende Aufträge: durch Ruhezeiten/Rate-Limit blockierte oder durch echten Druckerfehler fehlgeschlagene Aufträge ansehen, nachdrucken oder verwerfen, siehe "Ausstehende Aufträge" unten
- Optionale Logos je Druckart mit globalem Standard-Fallback
- Einzeln ein-/ausschaltbare Home-Module, siehe [Modul-Schalter](#modul-schalter) unten
- Webbasierte Einstellungen, aufgeteilt in Unterseiten je Bereich und nach Thema gruppiert
- 6 wählbare Farbschemata für die Web-UI
- Easy-Read: ein Schalter für größere Schrift auf Bon und Web-UI zugleich, siehe [Easy-Read](#easy-read-große-schrift) unten
- Deutsch/Englisch-UI, inklusive des eigentlichen Bon-Inhalts (nicht nur der UI drumherum)
- USB-angeschlossener ESC/POS-Drucker (python-escpos + pyusb, Epson-TM-T88V-Profil)
- Versions-Footer mit GitHub-Release-Update-Hinweis (alle 12h geprüft, kein Auto-Update)
- Optional MQTT: Druckbefehle, Druckerstatus und Home Assistant Discovery, siehe [MQTT](#mqtt)
- Sicherung und Wiederherstellung von Einstellungen und Logos, ohne Geheimnisse, siehe [Settings-Seiten](#settings-seiten)

### Listen

Aus dem ursprünglichen Einkaufslisten-Modul hervorgegangen; alle drei
Listentypen teilen sich dieselbe Druck-Queue, Historie, Rate-Limit und
Ruhezeiten wie jedes andere Modul.

- **Einkaufsliste** (`/shopping`, auch erreichbar unter `/lists/shopping`)
  – eine einfache Checkliste. Der API-Endpunkt `/print/list` ist
  unverändert seit vor dem Lists-Modul, weiterhin voll abwärtskompatibel
  für bestehende Integrationen. Einziger Listentyp mit Logo-Slot (nutzt
  den bestehenden Einkaufs-Logo-Slot) – To-Do-Listen und Task-Karten
  zeigen nie ein Logo.
- **To-Do-Liste** (`/lists/todo`) – derselbe Checklisten-Mechanismus wie
  die Einkaufsliste, separat gedruckt und beschriftet. JSON-API
  `POST /print/todo`.
- **Task-/Kanban-Karte** (`/lists/task`) – ein Titel plus optionaler
  Beschreibung, optionaler Checkliste, optionaler Priorität
  (niedrig/mittel/hoch) und optionalem Fälligkeitsdatum; es werden nur
  tatsächlich gesetzte Felder gedruckt, keine leeren Platzhalter auf dem
  Bon. JSON-API `POST /print/task` – geeignet für einen einfachen
  `curl`-Aufruf, einen Home-Assistant-`rest_command`, oder jede andere
  Automation, die HTTP-POST beherrscht, mit derselben `X-Api-Token`-Auth,
  Druck-Queue, Ruhezeiten, Rate-Limit und Historie wie jeder andere
  `/print/*`-Endpunkt.
- **Entwürfe** – alle drei Formulare speichern automatisch zum Server
  (`lists_store.py`, ~1s nach Tipp-Ende), damit eine angefangene Liste
  auch beim Wegnavigieren oder Tab-Schließen erhalten bleibt; gelöscht
  wird der Entwurf erst nach tatsächlichem Druck. Ein "Verwerfen"-Button
  setzt das Formular auf leer zurück. Mit Konten hat jedes Konto eigene
  Entwürfe, siehe [Persönliche Einstellungen](#persönliche-einstellungen).

### Systembericht

`/settings/system-report` verwaltet eine frei erweiterbare Liste von
SSH-Zielen statt einer festen Anzahl – jeder Eintrag hat einen Namen,
eine Rolle, einen Host und einen Nutzer, und verbindet sich entweder
per SSH-Key (Standard) oder optional per Passwort.

- **Proxmox** und **PBS** sind strukturierte Rollen mit eigenem, festem
  Satz an SSH-Befehlen (CPU/RAM/LXC-/VM-Liste bei Proxmox, letzte
  Backup-Tasks bei PBS).
- **Eigener Befehl** führt einen frei vom Nutzer eingegebenen
  SSH-Befehl 1:1 auf dem Zielhost aus – die generische Rolle für alles,
  was nicht Proxmox/PBS ist, z. B. ein NAS beliebiger Art. Es wird an
  keiner Stelle im Code eine bestimmte NAS-Software vorausgesetzt.
- Ein Update-Check (`apt list --upgradable`) läuft unabhängig von der
  Rolle über jeden konfigurierten Host.
- **Passwort-Login**: ein optionales Passwort-Feld ist die Alternative
  zu SSH-Key-Auth, für Hosts mit eigenem, eingeschränktem
  Passwort-Nutzer. Verbindet über `paramiko` statt das System-`ssh`-CLI
  (vermeidet die Passwortübergabe als Prozessargument, anders als
  `sshpass`). Das Passwort wird vor dem Schreiben nach
  `settings.json` mit Fernet (Paket `cryptography`) verschlüsselt; der
  Schlüssel dafür liegt in einer eigenen Datei unter `STATE_DIR`
  (`secret.key`, Modus 0600), beim ersten Gebrauch erzeugt, weder im
  Git noch in `settings.json` selbst.
- **Host-Keys und Eingaben**: der SSH-Key eines Hosts wird beim ersten
  Kontakt festgehalten (`StrictHostKeyChecking=accept-new` bei Key-Login,
  dieselbe Regel in `STATE_DIR/ssh_known_hosts` bei Passwort-Login); zeigt
  ein Host später einen anderen Key, erscheint ein Fehler im Bericht
  statt einer Verbindung. Host und Nutzer müssen eine Allowlist passieren
  (kein führendes `-`, keine Leerzeichen), und der `ssh`-Aufruf übergibt
  sie als `-l <nutzer> -- <host>`, sodass keiner von beiden als
  `ssh`-Option gelesen werden kann.

### Spiele

Drei Offline-Spiele, die über dieselbe zentrale Druck-Queue laufen wie
jedes andere Modul. Anders als die meisten Module sind sie reine
UI-Funktionen – es gibt keine `/print/games/*`-JSON-API, sie sind nicht
an den Automation-Webhook angebunden, und es gibt für sie keinen
Logo-Slot.

- **Sudoku** (`/games/sudoku`) – ein lokal generiertes 9x9-Rätsel mit drei
  Schwierigkeitsgraden (leicht/mittel/schwer), optional mit einem
  zweiten Beleg für die vollständige Lösung.
- **Würfelblock** (`/games/dice`) – ein generischer Punkteblock für
  Würfelspiele mit 5 Würfeln (oberer Bereich Einser–Sechser mit
  Zwischensumme/Bonus, unterer Bereich 3er-/4er-Pasch, Full House,
  kleine/große Straße, 5 Gleiche, Chance, plus Gesamtsummen oben/unten/
  gesamt). Ein Beleg pro Spieler, 1–12 Spieler pro Druck.
- **Tic-Tac-Toe** (`/games/tictactoe`) – leere 3x3-Spielfelder zum
  handschriftlichen Ausfüllen, 3/6/9 Runden pro Druck.


### Rezepte

Verbindet ReceiptPi mit einer selbst gehosteten
[Mealie](https://mealie.io)-Instanz (getestet mit v3.27.0). Einrichtung
unter `/settings/recipes`: Base-URL + API-Token (in Mealie unter Profil →
API-Tokens erzeugen). Der Token wird Fernet-verschlüsselt gespeichert
(gleiches Verfahren wie die SSH-Passwörter des Systemberichts) und nie
wieder angezeigt; bleibt das Token-Feld beim Speichern leer, bleibt der
gespeicherte erhalten (nur solange die Base-URL gleich bleibt – bei
geänderter URL muss der Token neu eingegeben werden, damit der
gespeicherte nie an eine neue Adresse geht), "Token entfernen" löscht ihn.
"Verbindung testen"
prüft URL und Token (über `/api/users/self` – Mealies `/api/app/about`
antwortet ohne Anmeldung und kann einen Token allein nicht prüfen), ohne
etwas zu speichern.

Das Modul ist nur aktiv, wenn sein Schalter unter `/settings/modules` an
**und** ein Rezeptmanager gewählt ist – steht der Rezeptmanager auf
"Aus", verhält es sich wie ein deaktiviertes Modul (keine Kachel, `404`
auf allen Routen, kein Import auf `/shopping`), siehe
[Modul-Schalter](#modul-schalter).

- **Suchen + drucken** (`/recipes`) – durchsucht Rezepttitel und
  -beschreibung (Mealies Suche umfasst keine Zutaten), zeigt eine
  Vorschau, druckt Titel, Gesamtzeit, Zutaten und Zubereitung.
- **Zufallsrezept** – optional gefiltert nach einer oder mehreren Zutaten
  (eine/alle). Zutatennamen müssen exakt einem Mealie-Lebensmittel
  entsprechen (Groß-/Kleinschreibung egal, Singular oder Plural) –
  sonst werden ähnliche Namen vorgeschlagen.
- **Einkaufslisten-Import** (auf `/shopping`) – hängt die nicht
  abgehakten Einträge einer Mealie-Einkaufsliste an den aktuellen Zettel
  an. Nur anhängen, und gegenüber Mealie nur lesend (dort wird nichts
  abgehakt).

Nur Web-UI (keine `/print/*`-JSON-API, siehe [Endpunkte](#endpunkte)),
kein Logo-Slot. Bruch-Zeichen in Mealies Zutatentext (z. B. `¹/₂`)
werden als einfaches ASCII (`1/2`) gedruckt.

### Easy-Read (große Schrift)

Ein einzelner Schalter unter `/settings/print-rules` ("Textgröße":
Normal / Groß) skaliert sowohl den Bon-Ausdruck als auch die Web-UI aus
derselben Einstellung heraus:

- **Bon**: Überschriften werden in doppelter Breite/Höhe gedruckt,
  Fließtext in doppelter Breite (was die nutzbare Zeilenbreite halbiert,
  Fließtext bricht dadurch öfter um); Datum/Uhrzeit am Ende und
  Trennlinien bleiben immer normal groß.
- **Web-UI**: die Basis-Schriftgröße wächst von 16px auf 19px, und der
  Status-Streifen sowie das Kachel-Grid der Module fallen von mehreren
  Spalten auf eine einzelne Spalte, damit nichts gequetscht wird.

Es handelt sich um einen reinen Textgrößen-Schalter, keine vollständige
Accessibility-Überarbeitung – es gibt kein separates
Screen-Reader-Markup oder einen eigenen Kontrastmodus dazu.

### Steuerzeichen im Drucktext

`text()` aus python-escpos schickt Steuerzeichen unverändert an den
Drucker, eine ESC- oder GS-Folge in einem Textfeld liefe also als
Druckerbefehl (Reset, Schnitt, Codepage-Wechsel). Deshalb bereinigt
`text_style.clean_print_text()` jedes Feld, das von außerhalb des Codes
kommt, bevor es umbrochen und gedruckt wird: CRLF wird LF, ein Tab wird
ein Leerzeichen, alle anderen Steuerzeichen (C0 außer LF, DEL, C1)
fallen weg. Umlaute, das Euro-Zeichen und alle anderen druckbaren
Zeichen bleiben. Das gilt für jeden Weg zum Druck: Web-UI, `/print/*`-API,
MQTT, Nachdruck ausstehender Aufträge und Daten externer Dienste (Wetter,
Mealie, Fritz!Box, Systembericht). Der QR-Code fürs Gäste-WLAN behält
SSID und Passwort exakt, nur die gedruckten Zeilen werden bereinigt.

### Ausstehende Aufträge

Ein durch Ruhezeiten/Rate-Limit blockierter Druckauftrag – oder einer,
der an einem echten Druckerfehler (Gerät nicht erreichbar, USB-Fehler,
…) scheitert – verschwindet nicht einfach: der Inhalt wird gespeichert,
damit er später überprüft und manuell nachgedruckt oder verworfen
werden kann, erreichbar über das Burger-Menü (jedes Konto sieht seine
eigenen Einträge, sobald ein Admin-Konto existiert, siehe
[Anmeldung und Konten](#anmeldung-und-konten)). Gilt gleichermaßen für
jede Quelle (Web-UI, JSON-API, Automation-Webhook, Watcher), da zentral
an `enqueue_print()` angeknüpft wird statt einzeln pro Modul.
Duplikat-Sperren-Blockaden sind bewusst ausgeschlossen – die spiegeln
eine bereits akzeptierte frühere Einreichung desselben Inhalts wider,
keinen verlorenen Inhalt.

Häkchen bei den gewünschten Einträgen setzen, dann "Ausgewählte
drucken" oder "Ausgewählte verwerfen" wählen; "Alle drucken"/"Alle
verwerfen" überspringen die Auswahl komplett. Ein Nachdruck umgeht
Ruhezeiten (bewusste, geprüfte Handlung) und Duplikat-Sperre, das
Rate-Limit gilt aber weiterhin. Ein Eintrag wird erst entfernt, wenn
der Nachdruck BESTÄTIGT erfolgreich war – ein fehlgeschlagener Versuch
lässt ihn stehen und erzeugt nie einen zweiten Pending-Eintrag für
denselben Inhalt. `/settings/print-rules` hat eine optionale
Auto-Aufräum-Einstellung (0 = nie, sonst Anzahl Tage).

Nicht jede Druckart ist dabei: Wetter/Systembericht holen beim
Nachdruck ohnehin live neue Daten (kein Veraltet-Risiko), ein
Bild-Upload wird dagegen als bereits verkleinerte/gedithert
druckfertige Version gespeichert (wenige KB, nicht das Original-Foto).

### Modul-Schalter

Unter `/settings/modules` lässt sich jedes der 8 Katalog-Module
(Listen, Nachricht, Wetter, Bild, Gäste-WLAN, System, Spiele, Rezepte)
einzeln ein- oder ausschalten:

- Ein deaktiviertes Modul verschwindet nicht nur von der Startseite,
  sondern **alle** seine Routen – Web-UI-Seiten, `/ui/*`-Formular-Posts
  und `/print/*`-API-Endpunkte gleichermaßen – antworten mit 404.
- Ein Modul kann zusätzlich zu seinem Schalter eine Voraussetzung haben:
  **Rezepte** ist nur aktiv, solange unter `/settings/recipes` ein
  Rezeptmanager gewählt ist. Steht der Rezeptmanager auf "Aus", wird es
  genau wie ein deaktiviertes Modul behandelt; seine Checkbox hier
  bleibt wie gespeichert und zeigt einen Hinweis, warum das Modul
  ausgeblendet ist.
- Der Status-Streifen der Startseite zeigt an, wie viele dieser 8 Module
  aktuell aktiv sind (Schalter und Voraussetzung).
- Dieser Schalter gilt für alle. Ein Konto kann eine Kachel zusätzlich
  nur auf seiner eigenen Startseite ausblenden (siehe
  [Persönliche Einstellungen](#persönliche-einstellungen)); das sperrt
  keine Routen und ändert die Anzahl nicht.
- Verlauf, die Settings-Seiten selbst und der generische
  Automation-Webhook liegen außerhalb dieses Schalter-Systems und
  bleiben immer erreichbar.
- Zwei der drei Watcher berücksichtigen das: `fritzbox_wifi_watch.py`
  und `storm_warning_watch.py` prüfen den Status des `wifi`- bzw.
  `weather`-Moduls und überspringen sauber, wenn es deaktiviert ist.
  `github_star_watch.py` prüft das nicht und scheitert einfach an
  seinem Druckaufruf, wenn das `message`-Modul deaktiviert ist.

### Persönliche Einstellungen

Sobald ein Admin-Konto existiert, kann jedes Konto unter „Meine
Einstellungen“ im Menü (`/preferences`) selbst wählen:
- **Sprache der Oberfläche** (Deutsch/Englisch),
- **Design** (eines der 6 Themes),
- **Sprache der Bons** – die Sprache von allem, was das Konto druckt
  (Beschriftungen und feste Texte auf dem Bon, nicht der eingegebene
  Inhalt).

Jede Auswahl hat als erste Option „Gerätestandard“; dann wird nichts
gespeichert, und das Konto folgt der Einstellung des Geräts
(`/settings/language`, `/settings/design`), auch wenn ein Admin sie
später ändert. Ohne eigene Bon-Sprache druckt ein Konto in der Sprache
des Geräts, nicht in seiner Oberflächensprache. Aufträge ohne Konto
(API-Token, MQTT, Watcher, Boot-Begrüßung oder noch kein Admin
eingerichtet) drucken immer in der Sprache des Geräts. Ein Neudruck aus
den ausstehenden Aufträgen druckt in der Bon-Sprache, die sein Besitzer
zum Zeitpunkt des Neudrucks gewählt hat (Sprache des Geräts für
System-Einträge und gelöschte Konten). Die Anmeldeseite zeigt immer
Sprache und Design des Geräts. Sobald Konten existieren, sind die
Geräteseiten als „Gerätestandard“ beschriftet. Ein Konto mit
Startpasswort erreicht nur seine Kontoseite, bis es ein eigenes
Passwort gesetzt hat.

**Kacheln der Startseite** (`/preferences/home`, verlinkt unten auf der
Startseite und unter Meine Einstellungen): Ein Konto kann eine Kachel
per Haken ausblenden und Kacheln mit Knöpfen innerhalb ihrer Gruppe
(Drucken, Extras, Spiele) nach oben oder unten verschieben – ohne
Drag-and-drop. Jeder Knopf speichert das ganze Formular; „Standard
wiederherstellen“ entfernt die Kachel-Einstellungen. Ausgeblendete
Kacheln fehlen nur auf der Startseite dieses Kontos: Die Seite bleibt
über ihre Adresse erreichbar, und die Modulanzahl auf der Startseite
bleibt die des Geräts. Ein Modul für alle abschalten geht weiterhin
unter `/settings/modules`. In einer Gruppe, die das Konto umsortiert
hat, erscheint ein in einer späteren Version neues Modul am Ende; eine
unveränderte Gruppe behält die Standardreihenfolge. Aufgelistet sind
nur Kacheln, die das Konto sehen kann; Reihenfolge und Sichtbarkeit der
übrigen (z. B. eines Moduls, das ein Admin abgeschaltet hat) bleiben
erhalten.

**Listen-Entwürfe pro Konto**: Die Entwürfe von Einkaufsliste, To-do-Liste
und Task-Karte (siehe [Listen](#listen)) gehören dem Konto, das sie
eingetippt hat; kein anderes Konto kann sie sehen oder überschreiben.
Ein Druck über die Web-UI leert nur den Entwurf des druckenden Kontos.
Ein Druck über die API oder MQTT leert nur den System-Entwurf – den, den
die Web-UI nutzt, solange kein Admin-Konto existiert, und in dem auch
der eine gemeinsame Entwurf älterer Versionen landet. Beim Löschen eines
Kontos werden seine Entwürfe und persönlichen Einstellungen gelöscht.

---

[Zurück zur Übersicht](README.de.md)
