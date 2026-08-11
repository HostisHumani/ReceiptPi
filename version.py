"""
Single source of truth for the running ReceiptPi version, read once
from the project-root VERSION file (plain text, e.g. "0.1.9alpha").

Deliberately minimal: this only understands the version shapes
ReceiptPi actually uses today - MAJOR.MINOR.PATCH with an optional
alpha marker, in either of the two forms actually seen in this
project: the bare "alpha" suffix used in VERSION itself ("0.1.9alpha")
and the hyphenated, numbered "-alphaN" suffix GitHub release tags
actually use ("v0.1.9-alpha1", verified against the real
HostisHumani/ReceiptPi releases - every one of them uses this exact
shape). No build metadata, no "beta"/"rc", no other pre-release
labels. This is NOT a general SemVer/PEP 440 implementation - if
ReceiptPi's versioning scheme ever grows beyond this, this file needs
revisiting rather than stretching to guess at a shape it was never
verified against (see the hard rule in the project notes about never
shipping code built on unverified assumptions about external
formats).

An unnumbered "alpha" (VERSION's own shorthand) ranks the same as
"alpha1" - VERSION is meant to mirror the release it ships with, and
that release's first (and, in practice, only) alpha tag is always
"alphaN"=1, so this equivalence is what makes CURRENT_VERSION compare
equal to its own matching GitHub tag instead of looking permanently
out of date.
"""
import os
import re

_VERSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")

# Optional leading "v"/"V" (GitHub's own tagging convention). The alpha
# marker itself is optional and, if present, comes in two shapes: a
# bare "alpha" (VERSION's own "0.1.9alpha") or a hyphenated, numbered
# "-alphaN" (a real GitHub tag, "v0.1.9-alpha1") - group 4 captures the
# whole marker verbatim (for format_display, which reproduces it
# as-is), group 5 captures just the digits, if any (for comparison).
_VERSION_RE = re.compile(r"^[vV]?(\d+)\.(\d+)\.(\d+)(-?alpha(\d*))?$")


def _read_version_file():
    try:
        with open(_VERSION_FILE) as f:
            return f.read().strip()
    except OSError:
        # Should never happen in a normal checkout, but a missing
        # VERSION file must not crash the whole app - the footer would
        # just show an obviously-wrong "v0.0.0" instead of taking the
        # server down.
        return "0.0.0"


CURRENT_VERSION = _read_version_file()


def parse_version(raw):
    """Returns (major, minor, patch, alpha_rank) or None if `raw`
    doesn't match ReceiptPi's version scheme. alpha_rank is None for a
    final release (no alpha marker at all), otherwise an int >= 1 - an
    unnumbered "alpha" ranks as 1, "alpha2" ranks as 2, etc. (see
    module docstring for why unnumbered=1). Callers MUST treat None as
    "cannot compare" and skip - never guess an ordering for an
    unrecognized format."""
    match = _VERSION_RE.match(raw.strip())
    if not match:
        return None
    major, minor, patch, alpha_marker, alpha_digits = match.groups()
    if alpha_marker is None:
        alpha_rank = None
    else:
        alpha_rank = int(alpha_digits) if alpha_digits else 1
    return int(major), int(minor), int(patch), alpha_rank


def compare_versions(a, b):
    """Returns -1 (a<b), 0 (a==b), 1 (a>b), or None if either string
    doesn't match ReceiptPi's version scheme. Numeric (major, minor,
    patch) is compared first; if equal: a final release outranks any
    alpha of the same number (0.1.9 > 0.1.9-alpha1), and between two
    alphas of the same number, the higher alpha rank wins
    (0.1.9-alpha1 < 0.1.9-alpha2 < 0.1.9-alpha3)."""
    parsed_a = parse_version(a)
    parsed_b = parse_version(b)
    if parsed_a is None or parsed_b is None:
        return None

    numeric_a, numeric_b = parsed_a[:3], parsed_b[:3]
    if numeric_a != numeric_b:
        return -1 if numeric_a < numeric_b else 1

    alpha_a, alpha_b = parsed_a[3], parsed_b[3]
    if alpha_a == alpha_b:
        return 0
    if alpha_a is None:  # a is final, b is alpha -> a is newer
        return 1
    if alpha_b is None:  # b is final, a is alpha -> a is older
        return -1
    return -1 if alpha_a < alpha_b else 1  # both alpha -> lower rank is older


def format_display(raw):
    """Normalizes a version/tag string for display: always shown with
    one leading 'v', regardless of whether the input already had one
    (VERSION has none, GitHub tags conventionally do - this avoids
    "vv0.1.9alpha" or an inconsistent missing 'v' depending on which
    one it was). The alpha marker itself (if any) is reproduced
    verbatim - "0.1.9alpha" stays "v0.1.9alpha", "v0.1.9-alpha1" stays
    "v0.1.9-alpha1" - rather than normalized to one shape, since the
    two are genuinely different, both-valid encodings (VERSION's own
    shorthand vs. a real GitHub tag) and collapsing the tag's number
    away would hide real information. Falls back to the raw string
    (still 'v'-prefixed) if it doesn't match the known scheme, rather
    than hiding a real value the caller explicitly asked to display."""
    match = _VERSION_RE.match(raw.strip())
    if match is None:
        raw = raw.strip()
        return raw if raw[:1] in ("v", "V") else f"v{raw}"
    major, minor, patch, alpha_marker, _alpha_digits = match.groups()
    return f"v{major}.{minor}.{patch}{alpha_marker or ''}"
