"""
Who is looking at the print history or the pending list, and which
entries that account may see (shared by modules/history/ and
modules/pending/, and the counters in app.py).

A viewer is the dict from current_viewer(). Its scope is what the stores
take (history_store._scope_sql, pending_store._in_scope):
  - while no admin account exists there are no accounts, and every
    entry is visible as before;
  - an admin sees every entry and can narrow it down with ?owner=
    ("all", "system" or an account id);
  - any other account only ever sees its own entries, whatever ?owner=
    says.

An entry belongs to an account only if both the account id and its
creation time match (see history_store.py). An owner that matches no
current account is a deleted account: only admins see those entries.
"""
import re

from flask import g

import auth_store
import i18n

_OWNER_ID_RE = re.compile(r"[1-9][0-9]{0,9}")


def current_viewer():
    """{"accounts": bool, "admin": bool, "owner": (id, created) or None},
    worked out once per request. created is read from auth.db, the
    session only carries the id."""
    if "history_viewer" in g:
        return g.history_viewer
    if not auth_store.admin_exists():
        viewer = {"accounts": False, "admin": True, "owner": None}
    else:
        user = g.get("auth_user")
        account = auth_store.get_user(user["id"]) if user else None
        if account is None:
            viewer = {"accounts": True, "admin": False, "owner": None}
        else:
            viewer = {"accounts": True, "admin": user["role"] == "admin",
                      "owner": (account["id"], account["created"])}
    g.history_viewer = viewer
    return viewer


def own_scope(viewer):
    """The scope of the viewer's own entries. None for a viewer without
    an account (nothing of its own); with no accounts at all, everything
    counts as its own, as before."""
    if not viewer["accounts"]:
        return ("all",)
    if viewer["owner"] is None:
        return None
    return ("owner", *viewer["owner"])


def scope_for(viewer, requested):
    """(scope, selected filter value) for a list page. requested is the
    raw ?owner= value; it only counts for admins with accounts in use,
    and anything that isn't "all", "system" or a plain positive account
    id falls back to "all"."""
    if not viewer["accounts"]:
        return ("all",), "all"
    if not viewer["admin"]:
        return own_scope(viewer), "own"
    if requested == "system":
        return ("system",), "system"
    if isinstance(requested, str) and _OWNER_ID_RE.fullmatch(requested):
        owner_id = int(requested)
        account = auth_store.get_user(owner_id)
        if account is not None:
            return ("owner", account["id"], account["created"]), requested
        return ("owner_id", owner_id), requested
    return ("all",), "all"


def accounts_by_id():
    return {u["id"]: u for u in auth_store.list_users()}


def owner_label(owner, accounts):
    """Display text for an owner pair: the account name, "System", or the
    deleted-account text when the pair matches no current account."""
    if owner is None:
        return i18n.tr("history.owner.system")
    account = accounts.get(owner[0])
    if account is not None and account["created"] == owner[1]:
        return account["name"]
    return i18n.tr("history.owner.deleted", id=owner[0])


def filter_options(accounts, owners):
    """[(value, label)] for the admin's owner filter: all, System, every
    current account, and every deleted account id that still has entries
    (owners: owner pairs found in the stores). A deleted account whose id
    was given to a new account (after auth.db was reset) has no option of
    its own; its entries show up under "all"."""
    options = [("all", i18n.tr("history.filter.all")), ("system", i18n.tr("history.owner.system"))]
    for account in sorted(accounts.values(), key=lambda a: a["name"]):
        options.append((str(account["id"]), account["name"]))
    deleted_ids = sorted({owner_id for owner_id, _created in owners if owner_id not in accounts})
    for owner_id in deleted_ids:
        options.append((str(owner_id), i18n.tr("history.owner.deleted", id=owner_id)))
    return options
