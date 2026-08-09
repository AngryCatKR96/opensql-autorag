#!/usr/bin/env bash
# Copy the licensed OpenSQL artifacts into the docker build context.
#
# Usage:
#   ./infra/opensql/stage-artifacts.sh <opensql-package.tar.gz> <license.xml>
#
# Both artifacts are gitignored: the distribution tarball and the license XML
# are provided by Tmax and must not be committed.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
package="${1:-}"
license="${2:-}"

if [[ -z "$package" || -z "$license" ]]; then
    sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 2
fi

[[ -f "$package" ]] || { echo "package not found: $package" >&2; exit 1; }
[[ -f "$license" ]] || { echo "license not found: $license" >&2; exit 1; }

mkdir -p "$here/dist" "$here/licenses"
install -m 644 "$package" "$here/dist/$(basename "$package")"
install -m 644 "$license" "$here/licenses/license.xml"

echo "staged $(basename "$package") -> infra/opensql/dist/"
echo "staged $(basename "$license") -> infra/opensql/licenses/license.xml"

if [[ "$(basename "$package")" != "Tmax_OpenSQL_3.17.8.7_rockylinux9.7_buildtime20260720.tar.gz" ]]; then
    echo
    echo "note: OPENSQL_PACKAGE in infra/docker-compose.yml still points at the"
    echo "      3.17.8.7 package; update it to $(basename "$package")."
fi
