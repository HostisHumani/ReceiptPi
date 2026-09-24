"""
Module: recipes - search recipes in a recipe manager (currently Mealie
only, see modules/recipes/mealie.py), preview and print them, pick a
random recipe by ingredient, and import a Mealie shopping list into
ReceiptPi's own shopping list (/shopping, see static/mealie-import.js).

Connection settings (provider, URL, encrypted API token) live in
settings_store's "recipes" section and are edited on /settings/recipes
(modules/settings/routes.py) - this module only reads them.

UI-only (no /print/* JSON API, same as games): every flow here starts
from a person browsing recipes in the web UI. No logo slot (not in
logos.MODULE_KEYS): the printout is a long text document, same
reasoning as games/images.
"""
import textwrap
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request

import i18n
from print_queue import enqueue_print
from printer import get_printer
from security import MAX_TITLE_LEN, csrf_protect, get_csrf_token
from text_style import BODY_COLUMNS, get_text_scale

from . import mealie
from .mealie import MealieError

recipes_bp = Blueprint("recipes", __name__)

JOB_TYPE = "recipes"


def error_message(err):
    """Translated, user-facing text for a MealieError."""
    return i18n.tr(f"recipes.error.{err.kind}")


def _decimal_sep():
    return "," if i18n.current_language() == "de" else "."


# ---------------------------------------------------------------
# Printing
# ---------------------------------------------------------------

def _wrapped(text, cols, first_prefix, next_indent):
    """Word-wraps one entry (ingredient/step), keeping manual line
    breaks inside a step, with continuation lines indented under the
    text past the "- " / "3. " prefix."""
    lines = []
    prefix = first_prefix
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            continue
        lines.append(textwrap.fill(
            paragraph.strip(), width=cols, initial_indent=prefix, subsequent_indent=next_indent,
        ))
        prefix = next_indent
    return "\n".join(lines)


def _raw_print_recipe(recipe):
    """recipe: the dict from mealie.recipe_to_printable() - also exactly
    what's stored as the pending-reprint payload, so a replay never
    needs Mealie to be reachable."""
    fix = mealie.to_ascii_fractions
    scale = get_text_scale()
    cols = BODY_COLUMNS.get(scale.body_width, 42)
    # Recipe titles can be long ("Crispy Rice Salmon Sushi Salad with
    # Spicy Mayo Dressing") - word-wrapped at the HEADING's own width
    # multiplier, not the body's, or the printer hard-wraps mid-word.
    heading_cols = BODY_COLUMNS.get(scale.heading_width, 42)
    p = get_printer()
    try:
        p.set(align="center", bold=True, width=scale.heading_width, height=scale.heading_height, custom_size=True)
        p.text(textwrap.fill(fix(recipe["title"]), width=heading_cols) + "\n")
        p.set(align="left", bold=False, width=scale.body_width, height=scale.body_height, custom_size=True)
        if recipe.get("total_time"):
            p.text("\n")
            p.text(textwrap.fill(
                f"{i18n.tr('recipes.total_time_label')}: {fix(recipe['total_time'])}", width=cols,
            ) + "\n")
        p.text("\n")

        if recipe["ingredients"]:
            p.set(align="left", bold=False, width=1, height=1, custom_size=True)
            p.text("-" * 32 + "\n")
            p.set(align="left", bold=True, width=scale.body_width, height=scale.body_height, custom_size=True)
            p.text(i18n.tr("recipes.ingredients_heading") + "\n")
            p.set(align="left", bold=False, width=scale.body_width, height=scale.body_height, custom_size=True)
            for ingredient in recipe["ingredients"]:
                p.text(_wrapped(fix(ingredient), cols, "- ", "  ") + "\n")
            p.text("\n")

        if recipe["steps"]:
            p.set(align="left", bold=False, width=1, height=1, custom_size=True)
            p.text("-" * 32 + "\n")
            p.set(align="left", bold=True, width=scale.body_width, height=scale.body_height, custom_size=True)
            p.text(i18n.tr("recipes.steps_heading") + "\n")
            p.set(align="left", bold=False, width=scale.body_width, height=scale.body_height, custom_size=True)
            for n, step in enumerate(recipe["steps"], start=1):
                prefix = f"{n}. "
                p.text(_wrapped(fix(step), cols, prefix, " " * len(prefix)) + "\n")
                p.text("\n")

        p.set(align="left", bold=False, width=1, height=1, custom_size=True)
        p.text("-" * 32 + "\n")
        p.set(align="center")
        p.text(f"-- {datetime.now().strftime('%d.%m.%Y %H:%M')} --\n")
        p.cut()
    finally:
        p.close()


def do_print_recipe(recipe, source="ui"):
    """Returns (ok, detail, http_status), see enqueue_print()."""
    ok, detail, status_code = enqueue_print(
        _raw_print_recipe, recipe,
        job_type=JOB_TYPE, summary=recipe["title"], source=source,
        retry_payload=dict(recipe),
    )
    if ok:
        detail = "recipe printed"
    return ok, detail, status_code


# ---------------------------------------------------------------
# Pages
# ---------------------------------------------------------------

def _render_index(message=None, success=None, query="", results=None, total=0,
                  ingredients="", match_all=False):
    return render_template(
        "recipes.html", message=message, success=success,
        configured=mealie.is_configured(),
        query=query, results=results, total=total,
        ingredients=ingredients, match_all=match_all,
    )


def _render_detail(recipe, message=None, success=None, random_query=None):
    """random_query: the ingredient/match args when this recipe came
    from the random picker - enables a "pick another one" link that
    repeats the same filter."""
    return render_template(
        "recipes_detail.html", message=message, success=success,
        csrf_token=get_csrf_token(), recipe=recipe, random_query=random_query,
    )


@recipes_bp.route("/recipes", methods=["GET"])
def recipes_page():
    """Search runs as a plain GET (?q=...) - it's read-only, and this
    way the result list survives the browser's back button from a
    recipe's detail page."""
    query = request.args.get("q", "").strip()[:MAX_TITLE_LEN]
    if not query or not mealie.is_configured():
        return _render_index(query=query)
    try:
        results, total = mealie.search_recipes(query)
    except MealieError as e:
        return _render_index(error_message(e), False, query=query)
    return _render_index(query=query, results=results, total=total)


@recipes_bp.route("/recipes/view/<slug>", methods=["GET"])
def recipe_detail_page(slug):
    try:
        recipe = mealie.recipe_to_printable(mealie.get_recipe(slug))
    except MealieError as e:
        return _render_index(error_message(e), False)
    return _render_detail(recipe)


def _parse_ingredient_names(raw):
    """Comma-separated form input -> distinct non-empty names, capped
    at MAX_RANDOM_INGREDIENTS (each one costs a /api/foods request)."""
    names = []
    for part in raw.split(","):
        name = part.strip()[:MAX_TITLE_LEN]
        if name and name.casefold() not in {n.casefold() for n in names}:
            names.append(name)
    return names[:mealie.MAX_RANDOM_INGREDIENTS]


@recipes_bp.route("/recipes/random", methods=["GET"])
def random_recipe_page():
    raw_ingredients = request.args.get("ingredients", "")
    match_all = request.args.get("match") == "all"
    names = _parse_ingredient_names(raw_ingredients)
    form_state = {"ingredients": raw_ingredients, "match_all": match_all}

    try:
        food_ids = []
        for name in names:
            food, suggestions = mealie.resolve_food(name)
            if food is None:
                if suggestions:
                    message = i18n.tr(
                        "recipes.random.food_not_found_suggest",
                        name=name, suggestions=", ".join(suggestions),
                    )
                else:
                    message = i18n.tr("recipes.random.food_not_found", name=name)
                return _render_index(message, False, **form_state)
            food_ids.append(food["id"])

        picked = mealie.random_recipe(food_ids, require_all=match_all)
        if picked is None:
            key = "recipes.random.none_found" if names else "recipes.random.no_recipes"
            return _render_index(i18n.tr(key, names=", ".join(names)), False, **form_state)
        recipe = mealie.recipe_to_printable(mealie.get_recipe(picked["slug"]))
    except MealieError as e:
        return _render_index(error_message(e), False, **form_state)

    return _render_detail(recipe, random_query={
        "ingredients": raw_ingredients, "match": "all" if match_all else "any",
    })


@recipes_bp.route("/ui/recipes/print", methods=["POST"])
@csrf_protect
def ui_print_recipe():
    """Re-fetches the recipe by slug instead of trusting preview data
    posted back from the browser - the printout always matches what's
    in Mealie right now."""
    slug = request.form.get("slug", "")
    try:
        recipe = mealie.recipe_to_printable(mealie.get_recipe(slug))
    except MealieError as e:
        return _render_index(error_message(e), False)
    ok, detail, _status_code = do_print_recipe(recipe)
    message = i18n.tr("print.success") if ok else i18n.tr("print.error_prefix") + detail
    return _render_detail(recipe, message, ok)


# ---------------------------------------------------------------
# Shopping list import - JSON for static/mealie-import.js on the
# /shopping page. Plain GETs: read-only on both sides (Mealie is only
# read, and nothing is saved here - the script just appends the
# returned lines to the list textarea, which then goes through the
# normal draft autosave).
# ---------------------------------------------------------------

def _json_error(err):
    status = 400 if err.kind == "not_configured" else 502
    return jsonify({"status": "error", "detail": error_message(err)}), status


@recipes_bp.route("/recipes/import/lists", methods=["GET"])
def import_shopping_lists():
    try:
        lists = mealie.get_shopping_lists()
    except MealieError as e:
        return _json_error(e)
    return jsonify({"status": "ok", "lists": lists}), 200


@recipes_bp.route("/recipes/import/lists/<list_id>", methods=["GET"])
def import_shopping_list_items(list_id):
    try:
        lines, skipped_checked = mealie.get_shopping_list_items(list_id, _decimal_sep())
    except MealieError as e:
        return _json_error(e)
    return jsonify({
        "status": "ok",
        "items": lines,
        "message": i18n.tr("recipes.import.appended", count=len(lines), skipped=skipped_checked),
    }), 200
