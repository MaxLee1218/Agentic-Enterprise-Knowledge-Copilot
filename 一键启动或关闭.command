#!/bin/bash

# Finder launches .command files with an arbitrary working directory. Follow a
# desktop symlink back to this repository before locating the implementation.
SOURCE="${BASH_SOURCE[0]}"
while [[ -L "$SOURCE" ]]; do
  SOURCE_DIR="$(cd "$(dirname "$SOURCE")" && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  [[ "$SOURCE" != /* ]] && SOURCE="$SOURCE_DIR/$SOURCE"
done
SCRIPT_DIR="$(cd "$(dirname "$SOURCE")" && pwd)"
exec /bin/bash "$SCRIPT_DIR/scripts/local_enterprise_toggle.sh"
