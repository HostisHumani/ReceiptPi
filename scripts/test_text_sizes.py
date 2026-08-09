"""
One-off diagnostic script - NOT part of the app, not imported by
anything. Prints the four GS! size combinations (1x1/1x2/2x1/2x2)
labeled on the real printer, so we can see the actual result before
finalizing the Easy-Read feature - see the text-size implementation
plan discussion.

Usage on the Pi (with the real printer connected):
    cd /home/adrian/receiptpi
    python3 scripts/test_text_sizes.py

Safe to delete after the hardware test - has no other purpose.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from printer import get_printer

COMBINATIONS = [
    ("1x1 Normal", 1, 1),
    ("1x2 Height", 1, 2),
    ("2x1 Width", 2, 1),
    ("2x2 Double", 2, 2),
]


def main():
    p = get_printer()
    try:
        p.set(align="left", bold=False, width=1, height=1, custom_size=True)
        p.text("Text-Groessen-Test\n")
        p.text("-" * 32 + "\n")
        for label, width, height in COMBINATIONS:
            p.set(align="left", bold=False, width=width, height=height, custom_size=True)
            p.text(f"{label}\n")
        p.set(align="left", bold=False, width=1, height=1, custom_size=True)
        p.text("-" * 32 + "\n")
        p.cut()
        print("Testdruck gesendet - bitte den physischen Bon pruefen.")
    finally:
        p.close()


if __name__ == "__main__":
    main()
