"""Converts the raw screenshots to 600 px wide WebP for docs/images: sharp
enough at the 300 px they are shown at, and about 40 KB per image instead
of ~700 KB as PNG. usage: python convert.py <rawdir> <docs/images>"""
import glob
import os
import sys

from PIL import Image

raw, dest = sys.argv[1], sys.argv[2]
total = 0
for src in sorted(glob.glob(os.path.join(raw, "*", "*.png"))):
    section = os.path.basename(os.path.dirname(src))
    os.makedirs(os.path.join(dest, section), exist_ok=True)
    out = os.path.join(dest, section, os.path.basename(src)[:-4] + ".webp")
    im = Image.open(src).convert("RGB")
    im = im.resize((600, round(im.height * 600 / im.width)), Image.LANCZOS)
    im.save(out, "WEBP", quality=82, method=6)
    total += os.path.getsize(out)
print(f"{total // 1024} KB")
