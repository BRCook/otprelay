#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Runs inside the AppImage build, just before packaging ($APPDIR is set).
#
# Two jobs:
#
#   1. Remove PySide6's own deployment tooling. It ships Jinja templates saved
#      with a .py suffix, which are not valid Python, so byte-compiling the
#      bundle fails on them. Nothing in a running app touches any of it.
#
#   2. Stamp the version into the desktop entry. Without X-AppImage-Version,
#      update tools report "version unknown" and offer an update forever. The
#      version is read from the package installed into the AppDir, so
#      pyproject.toml stays the one place it is set.
set -eu

site=$(find "$APPDIR/python/lib" -maxdepth 2 -type d -name site-packages | head -1)
[ -n "$site" ] || { echo "prepare-appdir: no site-packages under $APPDIR" >&2; exit 1; }

removed=0
for path in \
    "$site/PySide6/scripts" \
    "$site/PySide6/assistant" \
    "$site/PySide6/designer" \
    "$site/PySide6/linguist" \
    "$site/PySide6/lupdate" \
    "$site/PySide6/lrelease" \
    "$site/PySide6/examples" \
    "$site/PySide6/glue" \
    "$site/PySide6/include" \
    "$site/PySide6/typesystems" \
    "$site/shiboken6_generator"
do
    if [ -e "$path" ]; then
        rm -rf "$path"
        removed=$((removed + 1))
    fi
done
echo "prepare-appdir: removed $removed development-only item(s) from PySide6"

# --- stamp the version ------------------------------------------------------

version=$(find "$site" -maxdepth 1 -name "otprelay_receiver-*.dist-info" \
    | head -1 | sed 's/.*otprelay_receiver-\(.*\)\.dist-info/\1/')
if [ -z "$version" ]; then
    echo "prepare-appdir: cannot find the installed version" >&2
    exit 1
fi

stamped=0
for desktop in "$APPDIR"/*.desktop; do
    [ -e "$desktop" ] || continue
    # Replace any existing line, so rebuilding never leaves two.
    sed -i '/^X-AppImage-Version=/d' "$desktop"
    printf 'X-AppImage-Version=%s\n' "$version" >> "$desktop"
    stamped=$((stamped + 1))
done
[ "$stamped" -gt 0 ] || { echo "prepare-appdir: no desktop file in $APPDIR" >&2; exit 1; }
echo "prepare-appdir: stamped version $version into $stamped desktop file(s)"
