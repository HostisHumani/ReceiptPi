# Bild

*[English version](images.md)*

**Wer:** alle, die die Oberfläche öffnen können

## Was kann das?

Druckt ein Foto oder Bild von deinem Handy oder Rechner. Ein Bondrucker
kennt nur schwarze Punkte, deshalb wandelt ReceiptPi das Bild zuerst in
ein Punktmuster um. Eine Nachricht kannst du dazuschreiben, sie wird
unter dem Bild auf denselben Bon gedruckt. Das Ergebnis kannst du dir
vor dem Drucken ansehen.

## So geht's

1. Tippe auf der Startseite auf **Bild**.
2. Wähle unter **Bilddatei (PNG/JPG, max. 12MB)** ein Bild aus.
3. Wähle ein **Rasterverfahren**, also die Art, wie Grautöne zu Punkten
   werden:

   | Verfahren | Gut für |
   |-----------|---------|
   | **Floyd-Steinberg (Standard)** | die meisten Fotos |
   | **Atkinson (kontrastreicher)** | klarere Flächen, Zeichnungen, Logos |
   | **Bayer 8×8 (regelmäßiges Raster)** | ein gleichmäßiges Punktmuster im Retro-Look |

4. Optional: Schreib unter **Nachricht (optional)** einen Text. Der
   Zähler darunter zeigt, wie viele der 500 Zeichen du schon nutzt.
5. Tippe auf **Vorschau**, um genau zu sehen, was gedruckt wird. Die
   Nachricht steht unter dem Bild.
6. Tippe auf **Drucken**.

   <img src="../images/modules/images.de.webp" alt="Bild-Seite mit der Vorschau des Drucks" width="300">

## Gut zu wissen

- **Breite Bilder werden auf die Papierbreite skaliert.** Ein Bild, das
  breiter als 512 Punkte ist, wird auf 512 Punkte verkleinert, die Höhe
  ergibt sich daraus. Ein schmaleres Bild druckt in seiner eigenen
  Größe, linksbündig. Ein hohes Bild ergibt einen langen Bon.
- **Handyfotos kommen richtig herum.** Viele Handys speichern ein
  Hochkant-Foto quer und vermerken die Drehung in der Datei
  (EXIF-Ausrichtung). ReceiptPi wendet diesen Vermerk an, in der
  Oberfläche und in der API.
- **Die Nachricht** steht unter dem Bild, linksbündig, in der Schrift
  des Druckers, wie bei einer [Nachricht](message.de.md). Lange Zeilen
  brechen an Wortgrenzen um. Mit [Easy-Read](../settings/print-rules.de.md)
  druckt sie größer. Wo die Zeilen umbrechen, zeigt die Vorschau nur
  ungefähr, weil der Drucker den Text selbst setzt. Deine Zeilenumbrüche
  bleiben, Steuerzeichen fallen weg.
- **Kräftige Kontraste drucken am besten.** Dunkle, flaue Fotos werden
  matschig. Probier in der Vorschau ein anderes Verfahren.
- **Grenzen:** PNG oder JPG, höchstens 12 MB und 6.000 Pixel pro Seite.
  Atkinson geht bis 512×2000 Punkte nach dem Skalieren und bricht nach 15
  Sekunden ab.
- **Ein Bild nach dem anderen.** Während eins verarbeitet wird, muss ein
  zweites kurz warten.
- Die [Druckregeln](../settings/print-rules.de.md) gelten: Während einer
  Ruhezeit wartet das Bild unter [Ausstehende Aufträge](pending.de.md),
  schon in Punkte umgewandelt, zusammen mit seiner Nachricht. Dasselbe
  Bild mit einer anderen Nachricht gilt nicht als Duplikat.
- **Verlauf:** Der [Verlauf](history.de.md) zeigt die Bildgröße und die
  ersten 40 Zeichen der Nachricht.
- **Aus anderen Systemen:** Bilder können auch Skripte schicken, siehe
  [Automations-API](../integrations/api.de.md). Die API hat kein Feld
  für eine Nachricht.

## Klappt nicht?

| Du siehst | Was du tun kannst |
|-----------|-------------------|
| „Das Bild konnte nicht gelesen werden (nur PNG oder JPG).“ | Speichere das Bild als PNG oder JPG. iPhone-Fotos im HEIC-Format: als JPG teilen oder die Kamera auf „Maximale Kompatibilität“ stellen. |
| „Für Atkinson ist das Bild zu groß ...“ oder „Atkinson hat länger als 15 s gebraucht ...“ | Wähle Floyd-Steinberg oder Bayer oder schneide das Bild zu. |
| „Gerade wird schon ein Bild verarbeitet. ...“ | Warte ein paar Sekunden und versuch es noch einmal. |
| Der Druck ist zu dunkel oder zu hell. | Probier in der Vorschau ein anderes Rasterverfahren oder ändere vorher die Helligkeit des Bildes auf dem Handy. |

---

[Zurück zur Übersicht](../README.de.md)
