"""
Print text size setting ("normal" vs "large"/Easy-Read) - see
settings_store's print_rules.text_size. Applied at render time within
each print function via get_text_scale(); doesn't touch the print
queue, history, quiet hours, or rate limiting.

Body uses 2x1 (double width, normal height) rather than 1x2 - decided
against 1x2 after a side-by-side print comparison on the real hardware
(2026-08-09): 2x1 reads noticeably better for body/item text. Width and
height are independent axes on this printer, so 2x1 halves the usable
line width (42 columns at Font A -> ~21), meaning body text wraps more
often than at normal size - accepted as the tradeoff for readability.
Heading stays 2x2 (both axes) since headings are short single words,
where the halved width doesn't matter.
Existing separators/list formatting behavior is deliberately left
untouched - see the boot greeting and the receipt footer timestamp,
which stay at "normal" size regardless of this setting (reference
info, not read-content).
"""
import textwrap
from dataclasses import dataclass

import settings_store


@dataclass(frozen=True)
class TextScale:
    body_width: int
    body_height: int
    heading_width: int
    heading_height: int


SCALES = {
    "normal": TextScale(1, 1, 1, 2),
    "large": TextScale(2, 1, 2, 2),
}


def get_text_scale() -> TextScale:
    value = settings_store.get_settings()["print_rules"].get("text_size", "normal")
    return SCALES.get(value, SCALES["normal"])


# Font A column count at width multiplier 1 on the TM-T88V (42 chars/
# line); width multiplier 2 halves that to ~21 - confirmed empirically
# against a real hardware print (2026-08-09: "Webhook-Testdruck fue" at
# body_width=2 was exactly 21 characters before the printer's own
# hard-wrap cut it off mid-word).
BODY_COLUMNS = {1: 42, 2: 21}


def wrap_body_text(text: str, scale: TextScale) -> str:
    """Word-wraps body text to the current scale's effective column
    count before sending it to the printer. Without this, the printer
    hard-wraps by raw character count with no word-boundary awareness,
    splitting words in half. Wraps each existing line separately, so
    manual line breaks/blank lines/paragraphs in the input text are
    preserved rather than being collapsed into one reflowed block."""
    cols = BODY_COLUMNS.get(scale.body_width, 42)
    return "\n".join(
        textwrap.fill(line, width=cols) if line.strip() else line
        for line in text.split("\n")
    )
