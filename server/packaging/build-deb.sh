#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Build the relay as a .deb. Usage: build-deb.sh [version] [output-dir]
#
# Needs only dpkg-deb, which Debian and Ubuntu have out of the box.
set -eu

version="${1:-0.0.0}"
outdir="${2:-dist}"
here=$(dirname "$(readlink -f "$0")")
server="$here/.."

root=$(mktemp -d)
chmod 0755 "$root"   # mktemp is private by default; package roots are not
trap 'rm -rf "$root"' EXIT

# Lay out the files where a package puts them, rather than the /opt layout a
# git checkout uses.
install -d -m 0755 "$root/usr/lib/otprelay" "$root/usr/bin" \
    "$root/lib/systemd/system" "$root/etc/default" "$root/DEBIAN"
install -m 0644 "$server/otprelay.py" "$root/usr/lib/otprelay/otprelay.py"
install -m 0755 "$server/otprelay-admin" "$root/usr/lib/otprelay/otprelay-admin"
ln -s ../lib/otprelay/otprelay-admin "$root/usr/bin/otprelay-admin"

# The service file ships with the /opt path a checkout uses, so point it at
# the packaged location instead.
sed 's#/opt/otprelay/server/otprelay.py#/usr/lib/otprelay/otprelay.py#' \
    "$server/packaging/systemd/otprelay.service" > "$root/lib/systemd/system/otprelay.service"
chmod 0644 "$root/lib/systemd/system/otprelay.service"
install -m 0644 "$server/packaging/systemd/otprelay.env.example" "$root/etc/default/otprelay"

sed "s/@VERSION@/$version/" "$here/deb/control" > "$root/DEBIAN/control"
for script in postinst prerm postrm; do
    install -m 0755 "$here/deb/$script" "$root/DEBIAN/$script"
done
# Keep the administrator's settings on upgrade.
echo /etc/default/otprelay > "$root/DEBIAN/conffiles"

mkdir -p "$outdir"
package="$outdir/otprelay_${version}_all.deb"
dpkg-deb --root-owner-group --build "$root" "$package" >/dev/null
echo "$package"