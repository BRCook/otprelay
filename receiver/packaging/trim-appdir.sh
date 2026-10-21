#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Runs inside the AppImage build, just before packaging ($APPDIR is set).
#
# PySide6 ships its own deployment tooling, including Jinja templates saved
# with a .py suffix. Those are not valid Python, so byte-compiling the bundle
# fails on them. Nothing in a running app touches any of it, so it goes.
set -eu

site=$(find "$APPDIR/python/lib" -maxdepth 2 -type d -name site-packages | head -1)
[ -n "$site" ] || { echo "trim-appdir: no site-packages under $APPDIR" >&2; exit 1; }

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
echo "trim-appdir: removed $removed development-only item(s) from PySide6"
