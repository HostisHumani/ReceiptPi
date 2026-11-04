"""Draws the made-up demo picture for the image page screenshot.
usage: python make_picture.py <out.jpg>"""
import sys

from PIL import Image, ImageDraw

img = Image.new("RGB", (900, 700), (200, 225, 245))
d = ImageDraw.Draw(img)
for i in range(300):
    d.ellipse((620 - i // 2, 80 - i // 4, 820 + i // 4, 280 + i // 4), outline=(255, max(200 - i // 3, 120), 60))
d.ellipse((640, 90, 800, 250), fill=(255, 200, 60))
d.rectangle((0, 480, 900, 700), fill=(90, 160, 80))
d.polygon([(200, 480), (380, 300), (560, 480)], fill=(170, 60, 50))
d.rectangle((240, 480, 520, 640), fill=(235, 220, 190))
d.rectangle((350, 540, 410, 640), fill=(110, 70, 40))
d.rectangle((270, 510, 330, 560), fill=(150, 200, 230))
d.rectangle((430, 510, 490, 560), fill=(150, 200, 230))
img.save(sys.argv[1], quality=90)
