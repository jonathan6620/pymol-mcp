#!/usr/bin/env bash
#
# One-shot setup for PyMOL-MCP on macOS.
#
#     ./shell/install-macos.sh
#
# See --help for the flags, or shell/README.md for what each step does.

set -euo pipefail

OS_NAME="macOS"

# Where PyMOL hides on a Mac. The Homebrew Caskroom entry is miniforge's, not
# PyMOL's -- conda installs the executable inside an env under that prefix.
PYMOL_SEARCH_PATHS='/opt/homebrew/bin/pymol
/usr/local/bin/pymol
'"$HOME"'/*conda*/envs/*/bin/pymol
'"$HOME"'/*forge*/envs/*/bin/pymol
'"$HOME"'/*conda*/bin/pymol
/opt/homebrew/Caskroom/*/base/envs/*/bin/pymol
/usr/local/Caskroom/*/base/envs/*/bin/pymol
/opt/*conda*/envs/*/bin/pymol
/Applications/PyMOL.app/Contents/bin/pymol
/Applications/PyMOL*.app/Contents/MacOS/PyMOL'

# arm64 on Apple silicon, x86_64 on Intel. A Rosetta shell reports x86_64 and
# gets the Intel build, which is correct for it.
CONDA_INSTALLER_URL="https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-MacOSX-$(uname -m).sh"

. "$(dirname "${BASH_SOURCE[0]}")/common.sh"

usage() {
  cat <<EOF
${C_BOLD}PyMOL-MCP setup for macOS${C_OFF}

Usage: shell/install-macos.sh [options]

EOF
  usage_options
}

parse_args "$@"

[ "$(uname -s)" = "Darwin" ] ||
  die "this is the macOS script, but uname says $(uname -s). Use install-linux.sh."

resolve_repo_root

printf '%sPyMOL-MCP setup -- macOS %s (%s)%s\n' \
  "$C_BOLD" "$(sw_vers -productVersion 2>/dev/null || echo '?')" "$(uname -m)" "$C_OFF"
printf '%s%s%s\n' "$C_DIM" "$REPO_ROOT" "$C_OFF"

ensure_uv
sync_python_deps
ensure_pymol || true
install_plugin
install_pymolrc
install_skill
register_clients
print_summary
