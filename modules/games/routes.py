"""
Module: offline receipt games - Sudoku, a generic five-dice score
block, and Tic-Tac-Toe. UI-only (no /print/games/* JSON API, unlike
most other modules) - deliberately not wired into the automation
webhook. Everything still goes through
enqueue_print(), so queue/history/rate-limit/quiet-hours all apply
exactly as for every other module without any extra work here.

No logo slot for this module (not in logos.MODULE_KEYS) - also a
deliberate decision, matching the reasoning already used for the
images module (print content is dominated by a generated grid/table,
not a place for a header logo).
"""
from datetime import datetime

from flask import Blueprint, render_template, request

import i18n
from print_queue import enqueue_print
from printer import get_printer
from security import csrf_protect, get_csrf_token
from text_style import BODY_COLUMNS, get_text_scale

from . import sudoku
from .render import render_sudoku_grid, render_tictactoe_board

games_bp = Blueprint("games", __name__)

# ---------------------------------------------------------------
# Sudoku
# ---------------------------------------------------------------

SUDOKU_DIFFICULTIES = ("easy", "medium", "hard")


def _raw_print_sudoku(puzzle, difficulty):
    scale = get_text_scale()
    img = render_sudoku_grid(puzzle)
    p = get_printer()
    try:
        p.set(align="center", bold=True, width=scale.heading_width, height=scale.heading_height, custom_size=True)
        p.text(i18n.tr("games.sudoku.receipt_title", difficulty=i18n.tr(f"games.sudoku.difficulty_{difficulty}")) + "\n")
        p.text("\n")
        # Grid is a fixed, print-optimized bitmap regardless of
        # Easy-Read - only the surrounding text (heading/footer)
        # scales, same reasoning as the images module not scaling
        # photos.
        p.set(align="center", bold=False, underline=0, width=1, height=1, custom_size=True)
        p.image(img)
        p.text("\n")
        p.set(align="center")
        p.text(f"-- {datetime.now().strftime('%d.%m.%Y %H:%M')} --\n")
        p.cut()
    finally:
        p.close()


def _raw_print_sudoku_solution(solution):
    img = render_sudoku_grid(solution)
    scale = get_text_scale()
    p = get_printer()
    try:
        p.set(align="center", bold=True, width=scale.heading_width, height=scale.heading_height, custom_size=True)
        p.text(i18n.tr("games.sudoku.receipt_solution_title") + "\n")
        p.text("\n")
        p.set(align="center", bold=False, underline=0, width=1, height=1, custom_size=True)
        p.image(img)
        p.text("\n")
        p.set(align="center")
        p.text(f"-- {datetime.now().strftime('%d.%m.%Y %H:%M')} --\n")
        p.cut()
    finally:
        p.close()


@games_bp.route("/games", methods=["GET"])
def games_index():
    return render_template("games_index.html")


@games_bp.route("/games/sudoku", methods=["GET"])
def games_sudoku_page():
    return render_template(
        "games_sudoku.html", message=None, success=None, csrf_token=get_csrf_token(),
    )


@games_bp.route("/ui/games/sudoku", methods=["POST"])
@csrf_protect
def ui_print_sudoku():
    difficulty = request.form.get("difficulty", "medium")
    if difficulty not in SUDOKU_DIFFICULTIES:
        difficulty = "medium"
    print_solution = request.form.get("print_solution") == "on"

    puzzle, solution = sudoku.generate_puzzle(difficulty)
    summary = i18n.tr("games.sudoku.receipt_title", difficulty=i18n.tr(f"games.sudoku.difficulty_{difficulty}"))
    ok, detail, _status_code = enqueue_print(
        _raw_print_sudoku, puzzle, difficulty, job_type="games", summary=summary, source="ui",
    )

    solution_ok = True
    solution_detail = ""
    if ok and print_solution:
        solution_ok, solution_detail, _sc = enqueue_print(
            _raw_print_sudoku_solution, solution,
            job_type="games", summary=i18n.tr("games.sudoku.receipt_solution_title"), source="ui",
        )

    if not ok:
        message = i18n.tr("print.error_prefix") + detail
    elif not solution_ok:
        # Puzzle printed fine, but the separate solution job was
        # blocked (e.g. by quiet hours/rate limit) - say so explicitly
        # rather than reporting a plain success that hides the missing
        # second receipt.
        message = i18n.tr("print.success") + " " + i18n.tr("print.error_prefix") + solution_detail
    else:
        message = i18n.tr("print.success")
    return render_template(
        "games_sudoku.html", message=message, success=ok, csrf_token=get_csrf_token(),
    )


# ---------------------------------------------------------------
# Dice score - plain ESC/POS text, no bitmap/font involved at all
# (category names are words, not just digits - see render.py).
#
# One receipt per player - num_players sets how many separate
# score-sheet receipts get printed. Each sheet has several GAME
# columns side by side (not player columns - that was an earlier,
# wrong reading of the request), separated by "|" like a real score
# pad where one person plays several rounds down the same sheet.
# Grouped into the standard sections (upper number
# section, upper subtotal+bonus, lower combo section, upper/lower
# grand totals, final total), full-width divider lines between
# sections.
# ---------------------------------------------------------------

DICE_SECTIONS = (
    ("ones", "twos", "threes", "fours", "fives", "sixes"),
    ("subtotal", "bonus"),
    ("three_of_kind", "four_of_kind", "full_house", "small_straight", "large_straight", "five_alike", "chance"),
    ("upper_total", "lower_total"),
    ("total",),
)

MAX_DICE_PLAYERS = 12  # sane upper bound - beyond this it's not a quick receipt printout anymore

# Row layout: label, left-justified, then N game columns each preceded
# by a "|" divider (blank cell, filled in by hand), plus one trailing
# "|" to close the table. _ROW_LABEL_WIDTH=10 fits every category
# label in both languages (longest: "Full House"/"Up. Total" = 10) -
# see modules/games/translations/*.json.
_ROW_LABEL_WIDTH = 10
_CELL_WIDTH = 3
_TARGET_GAME_COLUMNS = 6 


def _dice_game_columns():
    """As many game columns as fit in the current text scale's line
    width, capped at _TARGET_GAME_COLUMNS and never below 1. Same
    "shrink to fit Easy-Read's halved column budget" idea as the
    original column logic, just applied to game columns instead of
    player columns now that each receipt is one player."""
    scale = get_text_scale()
    available = BODY_COLUMNS.get(scale.body_width, 42)
    max_fit = (available - _ROW_LABEL_WIDTH - 1) // (_CELL_WIDTH + 1)
    return max(1, min(max_fit, _TARGET_GAME_COLUMNS))


def _dice_row(label, n_cols):
    cells = "".join(f"|{'':^{_CELL_WIDTH}}" for _ in range(n_cols))
    return f"{label.ljust(_ROW_LABEL_WIDTH)}{cells}|"


def _dice_header_row(n_cols):
    cells = "".join(f"|{i + 1!s:^{_CELL_WIDTH}}" for i in range(n_cols))
    return f"{'':<{_ROW_LABEL_WIDTH}}{cells}|"


def _dice_separator(n_cols):
    return "-" * (_ROW_LABEL_WIDTH + n_cols * (_CELL_WIDTH + 1) + 1)


def _raw_print_dice_blocks(num_players):
    scale = get_text_scale()
    n_cols = _dice_game_columns()
    separator = _dice_separator(n_cols)

    p = get_printer()
    try:
        for player_num in range(num_players):
            p.set(align="center", bold=True, width=scale.heading_width, height=scale.heading_height, custom_size=True)
            p.text(i18n.tr("games.dice.receipt_title") + "\n")
            p.text("\n")

            p.set(align="left", bold=False, width=scale.body_width, height=scale.body_height, custom_size=True)
            p.text(i18n.tr("games.dice.player_label", n=player_num + 1) + " " + "_" * 24 + "\n")
            p.text("\n")
            p.text(_dice_header_row(n_cols) + "\n")

            for i, section in enumerate(DICE_SECTIONS):
                if i > 0:
                    p.text(separator + "\n")
                for key in section:
                    label = i18n.tr(f"games.dice.category.{key}")
                    p.text(_dice_row(label, n_cols) + "\n")

            p.text("\n")
            # custom_size=True with width=1/height=1 explicitly, not
            # just p.set(align="center") - the size branch in escpos's
            # set() only runs when custom_size is truthy, so without it
            # the table's body scale (2x1 in Easy-Read) would stay
            # active for the footer instead of resetting to normal.
            p.set(align="center", bold=False, width=1, height=1, custom_size=True)
            p.text(f"-- {datetime.now().strftime('%d.%m.%Y %H:%M')} --\n")
            p.cut()
    finally:
        p.close()


@games_bp.route("/games/dice", methods=["GET"])
def games_dice_page():
    return render_template("games_dice.html", message=None, success=None, csrf_token=get_csrf_token())


@games_bp.route("/ui/games/dice", methods=["POST"])
@csrf_protect
def ui_print_dice():
    try:
        num_players = int(request.form.get("num_players", 1))
    except ValueError:
        num_players = 1
    num_players = max(1, min(num_players, MAX_DICE_PLAYERS))

    summary = f"{i18n.tr('games.dice.receipt_title')} ({num_players})"
    ok, detail, _status_code = enqueue_print(
        _raw_print_dice_blocks, num_players, job_type="games", summary=summary, source="ui",
    )
    if not ok:
        message = i18n.tr("print.error_prefix") + detail
    elif num_players > 1:
        # More than one physical receipt (one per player) came out of
        # this single submit - worth saying explicitly, not just
        # "printed".
        message = i18n.tr("print.success") + " " + i18n.tr("games.dice.sheets_printed", n=num_players)
    else:
        message = i18n.tr("print.success")
    return render_template("games_dice.html", message=message, success=ok, csrf_token=get_csrf_token())


# ---------------------------------------------------------------
# Tic-Tac-Toe
# ---------------------------------------------------------------

TICTACTOE_ROUND_CHOICES = (3, 6, 9)


def _raw_print_tictactoe(rounds):
    scale = get_text_scale()
    board_img = render_tictactoe_board()  # same blank board reused for every round - no need to regenerate

    p = get_printer()
    try:
        p.set(align="center", bold=True, width=scale.heading_width, height=scale.heading_height, custom_size=True)
        p.text(i18n.tr("games.tictactoe.receipt_title") + "\n")
        p.text("\n")

        for i in range(rounds):
            p.set(align="center", bold=False, width=1, height=1, custom_size=True)
            p.text(i18n.tr("games.tictactoe.round_label", n=i + 1, total=rounds) + "\n")
            p.set(align="center", bold=False, underline=0, width=1, height=1, custom_size=True)
            p.image(board_img)
            if i < rounds - 1:
                p.text("\n")

        p.text("\n")
        p.set(align="center")
        p.text(f"-- {datetime.now().strftime('%d.%m.%Y %H:%M')} --\n")
        p.cut()
    finally:
        p.close()


@games_bp.route("/games/tictactoe", methods=["GET"])
def games_tictactoe_page():
    return render_template("games_tictactoe.html", message=None, success=None, csrf_token=get_csrf_token())


@games_bp.route("/ui/games/tictactoe", methods=["POST"])
@csrf_protect
def ui_print_tictactoe():
    try:
        rounds = int(request.form.get("rounds", 3))
    except ValueError:
        rounds = 3
    if rounds not in TICTACTOE_ROUND_CHOICES:
        rounds = 3

    summary = f"{i18n.tr('games.tictactoe.receipt_title')} ({rounds})"
    ok, detail, _status_code = enqueue_print(
        _raw_print_tictactoe, rounds, job_type="games", summary=summary, source="ui",
    )
    message = i18n.tr("print.success") if ok else i18n.tr("print.error_prefix") + detail
    return render_template("games_tictactoe.html", message=message, success=ok, csrf_token=get_csrf_token())
