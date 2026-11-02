#!/usr/bin/env python3
"""
Console management of the web UI accounts in STATE_DIR/auth.db - the
way back in when nobody can log in any more (forgotten password, no
printer for the setup code).

Run it as the service user from the project directory, with the
project's venv, e.g.:

    cd ~/receiptpi && ~/receiptpi-env/bin/python scripts/manage_users.py list

Passwords are only ever read with getpass from the terminal: never from
an argument (visible to every local user in the process list and kept
in the shell history) and never printed.
"""
import argparse
import getpass
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth_store


def _fail(message):
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def _check_user():
    """auth.db is created with mode 0600 for the user that writes it. Run
    as any other user (typically root via sudo), the service could no
    longer read the file and nobody could log in."""
    try:
        owner = os.stat(auth_store.STATE_DIR).st_uid
    except FileNotFoundError:
        _fail(f"{auth_store.STATE_DIR} does not exist")
    if os.geteuid() != owner:
        _fail(f"run this as the owner of {auth_store.STATE_DIR} (uid {owner}), "
              f"e.g. with sudo -u <service user>")


def _read_new_password():
    for _ in range(3):
        first = getpass.getpass("New password: ")
        try:
            auth_store.validate_password(first)
        except auth_store.AuthError as e:
            print(_ERRORS.get(e.code, e.code), file=sys.stderr)
            continue
        if getpass.getpass("Repeat password: ") != first:
            print("The passwords do not match.", file=sys.stderr)
            continue
        return first
    _fail("no valid password entered")


_ERRORS = {
    "name_invalid": "Invalid name: 3-32 characters from a-z, 0-9, '.', '_', '-'.",
    "name_taken": "This name is already taken.",
    "admin_exists": "An admin account already exists. Use reset-password instead.",
    "password_too_short": f"The password needs at least {auth_store.PASSWORD_MIN_LEN} characters.",
    "password_too_long": f"The password may have at most {auth_store.PASSWORD_MAX_LEN} characters.",
    "user_not_found": "No such user.",
}


def _require_user(name):
    user = auth_store.get_user_by_name(name) if os.path.exists(auth_store.DB_FILE) else None
    if user is None:
        _fail(_ERRORS["user_not_found"])
    return user


def cmd_create_admin(args):
    try:
        name = auth_store.validate_name(args.name)
    except auth_store.AuthError as e:
        _fail(_ERRORS[e.code])
    if auth_store.admin_exists():
        _fail(_ERRORS["admin_exists"])
    password = _read_new_password()
    try:
        user = auth_store.create_user(name, password, role="admin", only_if_no_admin=True)
    except auth_store.AuthError as e:
        _fail(_ERRORS.get(e.code, e.code))
    print(f"Admin account '{user['name']}' created.")


def cmd_reset_password(args):
    user = _require_user(args.name)
    password = _read_new_password()
    auth_store.set_password(user["id"], password, revoke_sessions=True)
    print(f"Password of '{user['name']}' changed. All of its sessions have been ended.")


def cmd_list(_args):
    users = auth_store.list_users() if os.path.exists(auth_store.DB_FILE) else []
    if not users:
        print("No accounts.")
        return
    print(f"{'id':>4}  {'name':<32}  {'role':<5}  {'created':<16}  sessions")
    for u in users:
        created = datetime.fromtimestamp(u["created"]).strftime("%Y-%m-%d %H:%M")
        print(f"{u['id']:>4}  {u['name']:<32}  {u['role']:<5}  {created:<16}  {u['active_sessions']}")


def cmd_revoke_sessions(args):
    user = _require_user(args.name)
    count = auth_store.revoke_user_sessions(user["id"])
    print(f"{count} session(s) of '{user['name']}' ended.")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Manage the ReceiptPi web UI accounts (STATE_DIR/auth.db). "
                    "Passwords are always prompted for, never passed as arguments.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-admin", help="create the admin account (only if none exists yet)")
    p.add_argument("name", help="account name: 3-32 characters from a-z, 0-9, '.', '_', '-'")
    p.set_defaults(func=cmd_create_admin)

    p = sub.add_parser("reset-password", help="set a new password and end all of the account's sessions")
    p.add_argument("name")
    p.set_defaults(func=cmd_reset_password)

    p = sub.add_parser("list", help="list all accounts")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("revoke-sessions", help="end all sessions of an account (log out everywhere)")
    p.add_argument("name")
    p.set_defaults(func=cmd_revoke_sessions)

    args = parser.parse_args(argv)
    _check_user()
    try:
        args.func(args)
    except KeyboardInterrupt:
        print(file=sys.stderr)
        _fail("aborted")


if __name__ == "__main__":
    main()
