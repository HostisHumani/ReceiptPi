# Style guide for the documentation

The docs are written for people who have never set up a server. If a
step needs knowledge the reader may not have, the page explains it or
links to the [glossary](help/glossary.md).

## Languages

- English is the main language: `<page>.md`.
- German lives next to it: `<page>.de.md`, with the same content and the
  same structure.
- The first line under the title links to the other language.
- A change to one language changes the other in the same commit.

## Every page has the same four parts

1. **What it does** (*Was kann das?*): two or three sentences, no
   technical terms.
2. **How to use it** (*So geht's*): numbered steps, one action per step.
3. **Good to know** (*Gut zu wissen*): limits and side effects, for
   example that quiet hours hold back a print.
4. **Something not working?** (*Klappt nicht?*): the two or three most
   common causes, each with what to do.

Under the title, one line says who can use the feature:
`**Who:** everyone` or `**Who:** admins only`.

The overview (`README.md`), the glossary and the developer reference are
not feature pages and don't follow this pattern. Glossary entries are sorted
alphabetically in each language, so the order differs between the two.

## Writing

- Short sentences. Talk to the reader ("you", "du").
- Labels from the interface in bold and exactly as they appear, in the
  language of the page: **Print**, **Settings** → **Print rules**.
- Messages ReceiptPi shows go in quotes, word for word from the
  translation files.
- No walls of text: if a section gets longer than a screen on a phone,
  split it or use a table.
- Numbers (limits, defaults, intervals) come from the code, not from
  memory. Check them whenever the code changes.
- Things that need a terminal are marked as such and come with the exact
  command to copy.

## Screenshots

- Phone width, glass effect on, theme Warm, light, unless the page is
  about something else.
- Demo data only: no real names, addresses, passwords or network names.
- One image per language, since the interface text differs:
  `docs/images/<section>/<name>.webp` (English) and `<name>.de.webp`
  (German). WebP, 600 px wide.
- In the page: `<img src="../images/<section>/<name>.webp" alt="..." width="300">`,
  alt text in the language of the page, right after the step it shows.
- Don't take them by hand. `scripts/docs_screenshots/run.sh` starts a demo
  instance with made-up data, takes every screenshot in both languages
  and writes them to `docs/images`. Run it again after a change to the
  interface. A new screenshot goes into `shoot.js`; new demo data into
  `demo_server.py`.

## Keeping it up to date

A change to a feature updates its page in the same commit, in both
languages.
