#!/bin/bash
# Prints the content of the VERSION file for a release tag. Every workflow
# that turns a tag into a version uses this, so there is one rule:
#
#   v1.2.3         -> 1.2.3
#   v1.2.3-alpha1  -> 1.2.3alpha
#   v1.2.3-beta1   -> 1.2.3beta
#
# Any other tag is refused with exit status 1.
#
#   deploy/release/version_from_tag.sh v2.0.0
set -euo pipefail

TAG=${1:-}
if [[ "$TAG" =~ ^v([0-9]+)\.([0-9]+)\.([0-9]+)-alpha1$ ]]; then
    VERSION="${BASH_REMATCH[1]}.${BASH_REMATCH[2]}.${BASH_REMATCH[3]}alpha"
elif [[ "$TAG" =~ ^v([0-9]+)\.([0-9]+)\.([0-9]+)-beta1$ ]]; then
    VERSION="${BASH_REMATCH[1]}.${BASH_REMATCH[2]}.${BASH_REMATCH[3]}beta"
elif [[ "$TAG" =~ ^v([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
    VERSION="${BASH_REMATCH[1]}.${BASH_REMATCH[2]}.${BASH_REMATCH[3]}"
else
    echo "Unsupported release tag: $TAG" >&2
    exit 1
fi
printf '%s\n' "$VERSION"
