#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Build the relay as an Alpine .apk. Usage: build-apk.sh [version] [output-dir]
#
# Must run on Alpine with alpine-sdk installed, as an ordinary user: abuild
# refuses to run as root. In CI that is an Alpine container step; locally, an
# Alpine VM or container will do.
set -eu

version="${1:-0.0.0}"
outdir="${2:-dist}"
here=$(dirname "$(readlink -f "$0")")
server=$(readlink -f "$here/..")

command -v abuild >/dev/null || {
    echo "build-apk.sh: abuild not found; install alpine-sdk on Alpine Linux" >&2
    exit 1
}
[ "$(id -u)" != "0" ] || {
    echo "build-apk.sh: abuild refuses to run as root; use an ordinary user" >&2
    exit 1
}

# abuild signs every package it builds, so a key is needed. Reuse the builder's
# own if there is one, and make a throwaway otherwise: either way installing
# needs --allow-untrusted, since no published repository vouches for it.
[ -f "$HOME/.abuild/abuild.conf" ] || abuild-keygen -a -n

# The APKBUILD reads its sources from $startdir/../.., so recreate that shape
# in a scratch directory rather than writing into the checkout.
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/packaging/alpine" "$work/packaging/openrc"
cp "$here/alpine/APKBUILD" "$here/alpine/otprelay-server."*install "$work/packaging/alpine/"cp "$here/openrc/otprelay" "$here/openrc/otprelay.confd" "$work/packaging/openrc/"
cp "$server/otprelay.py" "$server/otprelay-admin" "$work/"
sed -i "s/^pkgver=.*/pkgver=$version/" "$work/packaging/alpine/APKBUILD"

mkdir -p "$outdir"
destination=$(readlink -f "$outdir")
( cd "$work/packaging/alpine" && abuild -F checksum && abuild -F -P "$destination" )

built=$(find "$destination" -name "otprelay-server-$version-r*.apk" | head -1)
[ -n "$built" ] || { echo "build-apk.sh: no package was produced" >&2; exit 1; }
final="$destination/otprelay-server-alpine-$version.apk"
mv "$built" "$final"
echo "$final"