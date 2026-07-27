#!/usr/bin/env bash
#
# One-shot setup for PyMOL-MCP on Linux.
#
#     ./shell/install-linux.sh
#
# See --help for the flags, or shell/README.md for what each step does.

set -euo pipefail

OS_NAME="Linux"

PYMOL_SEARCH_PATHS='/usr/bin/pymol
/usr/local/bin/pymol
'"$HOME"'/.local/bin/pymol
'"$HOME"'/*conda*/envs/*/bin/pymol
'"$HOME"'/*forge*/envs/*/bin/pymol
'"$HOME"'/*conda*/bin/pymol
/opt/*conda*/envs/*/bin/pymol
/opt/*forge*/envs/*/bin/pymol
/usr/local/*conda*/envs/*/bin/pymol'

# aarch64 and x86_64 both have Miniforge builds; anything else has no PyMOL
# conda package either, and gets the distro-package suggestion below.
CONDA_INSTALLER_URL="https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-$(uname -m).sh"

. "$(dirname "${BASH_SOURCE[0]}")/common.sh"

usage() {
  cat <<EOF
${C_BOLD}PyMOL-MCP setup for Linux${C_OFF}

Usage: shell/install-linux.sh [options]

EOF
  usage_options
  cat <<'EOF'

Your distribution may package PyMOL already, which is quicker than conda:
  Debian/Ubuntu   sudo apt install pymol
  Fedora/RHEL     sudo dnf install pymol
  Arch            sudo pacman -S pymol
Install it that way first and this script will find it on PATH.
EOF
}

parse_args "$@"

[ "$(uname -s)" = "Linux" ] ||
  die "this is the Linux script, but uname says $(uname -s). Use install-macos.sh."

resolve_repo_root

distro=$(. /etc/os-release 2>/dev/null && printf '%s' "${PRETTY_NAME:-Linux}")
printf '%sPyMOL-MCP setup -- %s (%s)%s\n' \
  "$C_BOLD" "${distro:-Linux}" "$(uname -m)" "$C_OFF"
printf '%s%s%s\n' "$C_DIM" "$REPO_ROOT" "$C_OFF"

# curl is what fetches both uv and Miniforge, and a minimal container image
# often lacks it.
if ! have curl; then
  die "curl is required. Install it first, e.g. 'sudo apt install curl'."
fi

ensure_uv
sync_python_deps

if ! ensure_pymol; then
  note "Your package manager may have PyMOL: 'sudo apt install pymol',
  'sudo dnf install pymol', or 'sudo pacman -S pymol'."
fi

install_plugin
install_pymolrc
install_skill
register_clients
print_summary
