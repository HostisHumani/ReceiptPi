"""
Bitmap rendering for the grid-based games (Sudoku, Tic-Tac-Toe) - pure
PIL drawing primitives (ImageDraw.line/rounded_rectangle), no font
file and no PIL font of any kind (neither a bundled TTF/OTF nor
ImageFont.load_default()) - deliberately avoids introducing any font/
license question for this feature. Digits are drawn as classic
7-segment glyphs built from rounded bars; see draw_digit() below.

512px is the hard width ceiling for p.image() on this printer profile
(see modules/images/routes.py) - both renderers target just under
that so nothing gets rejected or rescaled at print time.

Dice score is NOT rendered here - it's plain ESC/POS text (see
modules/games/routes.py), since the category names are words, not
just digits, and drawing a full alphabet by hand isn't worth it when
the printer's own font already does the job for text outside a grid.
"""
from PIL import Image, ImageDraw

# Classic 7-segment layout:
#  _a_
# f   b
#  _g_
# e   c
#  _d_
_SEGMENTS = {
    "1": {"b", "c"},
    "2": {"a", "b", "g", "e", "d"},
    "3": {"a", "b", "g", "c", "d"},
    "4": {"f", "g", "b", "c"},
    "5": {"a", "f", "g", "c", "d"},
    "6": {"a", "f", "g", "e", "c", "d"},
    "7": {"a", "b", "c"},
    "8": {"a", "b", "c", "d", "e", "f", "g"},
    "9": {"a", "b", "c", "d", "f", "g"},
}


def draw_digit(draw, digit, x, y, w, h, thickness):
    """Draws one 7-segment digit (1-9, no 0 needed for Sudoku) into
    the box (x, y, x+w, y+h) using only rounded_rectangle - no font
    involved. Verified visually against a real render at print target
    size before this shipped (see chat)."""
    segs = _SEGMENTS[digit]
    t = thickness

    def hseg(cy):
        draw.rounded_rectangle([x + t * 0.6, cy - t / 2, x + w - t * 0.6, cy + t / 2], radius=t / 2, fill="black")

    def vseg(cx, y0, y1):
        draw.rounded_rectangle([cx - t / 2, y0 + t * 0.6, cx + t / 2, y1 - t * 0.6], radius=t / 2, fill="black")

    if "a" in segs:
        hseg(y)
    if "g" in segs:
        hseg(y + h / 2)
    if "d" in segs:
        hseg(y + h)
    if "f" in segs:
        vseg(x, y, y + h / 2)
    if "b" in segs:
        vseg(x + w, y, y + h / 2)
    if "e" in segs:
        vseg(x, y + h / 2, y + h)
    if "c" in segs:
        vseg(x + w, y + h / 2, y + h)


# Target grid pixel width - comfortably under the 512px printer cap,
# generous cell size (~53px) for handwriting. margin=3 keeps the
# outer border from getting clipped by the printer's own margin.
SUDOKU_CELL = 53
SUDOKU_MARGIN = 3


def render_sudoku_grid(grid):
    """grid: 9x9 list of lists, 0 = empty cell. Returns a PIL Image
    ('L' mode, white background, black lines/digits) ready for
    p.image(). Thick lines every 3 cells (3x3 boxes), thin lines
    every single cell - same visual convention as any printed Sudoku."""
    n = 9
    cell = SUDOKU_CELL
    margin = SUDOKU_MARGIN
    size = margin * 2 + cell * n
    img = Image.new("L", (size, size), 255)
    draw = ImageDraw.Draw(img)

    for i in range(n + 1):
        width = 5 if i % 3 == 0 else 2
        x = margin + i * cell
        draw.line([(x, margin), (x, margin + n * cell)], fill=0, width=width)
        y = margin + i * cell
        draw.line([(margin, y), (margin + n * cell, y)], fill=0, width=width)

    for r in range(n):
        for c in range(n):
            v = grid[r][c]
            if not v:
                continue
            pad = cell * 0.22
            dx = margin + c * cell + pad
            dw = cell - 2 * pad
            dh = min(dw * 2.0, cell - 2 * pad)  # 7-seg digits are taller than wide
            dy = margin + r * cell + (cell - dh) / 2
            draw_digit(draw, str(v), dx, dy, dw, dh, thickness=max(3, cell * 0.075))

    return img


# Tic-Tac-Toe: fixed cell size regardless of how many boards are
# printed (3/6/9) - paper length is effectively unlimited, only width
# is capped, so there's no reason to shrink cells just because more
# rounds were requested. Boards stack vertically, printed as separate
# image calls (see routes.py) with a plain-text round label between
# them via the printer's own font - not baked into the bitmap.
TTT_CELL = 90
TTT_MARGIN = 4


def render_tictactoe_board():
    """Returns one blank 3x3 grid as a PIL Image - no font/text
    involved at all, just grid lines, since cells are left empty for
    handwritten play."""
    cell = TTT_CELL
    margin = TTT_MARGIN
    size = margin * 2 + cell * 3
    img = Image.new("L", (size, size), 255)
    draw = ImageDraw.Draw(img)
    for i in range(4):
        x = margin + i * cell
        draw.line([(x, margin), (x, margin + 3 * cell)], fill=0, width=3)
        y = margin + i * cell
        draw.line([(margin, y), (margin + 3 * cell, y)], fill=0, width=3)
    return img
