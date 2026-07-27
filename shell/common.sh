# Shared helpers for install-macos.sh and install-linux.sh.
#
# Sourced, never executed. The two OS scripts set the handful of variables
# marked "provided by the caller" below, then call the steps in order.
#
# Written for bash 3.2, which is what macOS still ships -- no associative
# arrays, no ${var,,}.

# --- provided by the caller -------------------------------------------------
# OS_NAME              human-readable platform name, used in messages
# PYMOL_SEARCH_PATHS   newline-separated globs to search for the executable
# CONDA_INSTALLER_URL  Miniforge installer for this platform
# ---------------------------------------------------------------------------

set -euo pipefail

CONDA_ENV_NAME=pymol-env

# Defaults; the OS scripts overwrite these from their flag parsing.
ASSUME_YES=${ASSUME_YES:-0}
SKIP_PYMOL=${SKIP_PYMOL:-0}
SKIP_CLIENTS=${SKIP_CLIENTS:-0}
FORCE_PYMOLRC=${FORCE_PYMOLRC:-0}
PYMOL=${PYMOL:-}

# Set by ensure_pymol; empty means "use conda run" (see run_pymol).
PYMOL_BIN=""
CONDA_BIN=""

# Collected during the run and printed by print_summary, so the user gets the
# manual follow-ups in one place rather than scrolling back through the log.
NOTES=""

# --- output -----------------------------------------------------------------

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'; C_RED=$'\033[31m'
  C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_OFF=$'\033[0m'
else
  C_BOLD=""; C_DIM=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_OFF=""
fi

step() { printf '\n%s==> %s%s\n' "$C_BOLD" "$*" "$C_OFF"; }
info() { printf '    %s\n' "$*"; }
ok()   { printf '    %s%s%s\n' "$C_GREEN" "$*" "$C_OFF"; }
# Stdout, not stderr: these are progress notes, and a separate stream
# interleaves out of order with the step headers once the run is piped to a file.
warn() { printf '    %s%s%s\n' "$C_YELLOW" "$*" "$C_OFF"; }
die()  { printf '\n%serror: %s%s\n' "$C_RED" "$*" "$C_OFF" >&2; exit 1; }

note() { NOTES="$NOTES$1
"; }

have() { command -v "$1" >/dev/null 2>&1; }

# --- flags ------------------------------------------------------------------

# Each OS script defines its own usage() before calling this; bash resolves the
# call at run time, so the shared parser can print a platform-specific banner.
parse_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      -y | --yes)       ASSUME_YES=1 ;;
      --skip-pymol)     SKIP_PYMOL=1 ;;
      --skip-clients)   SKIP_CLIENTS=1 ;;
      --force-pymolrc)  FORCE_PYMOLRC=1 ;;
      --pymol)
        [ $# -ge 2 ] || die "--pymol needs a path."
        PYMOL=$2; shift ;;
      --pymol=*)        PYMOL=${1#--pymol=} ;;
      -h | --help)      usage; exit 0 ;;
      *) die "unknown option: $1  (try --help)" ;;
    esac
    shift
  done

  # Checked here rather than in find_pymol, which runs inside a command
  # substitution: die's exit would end only that subshell, and an unusable
  # --pymol would quietly fall through to installing conda instead.
  if [ -n "$PYMOL" ] && [ ! -x "$PYMOL" ]; then
    die "--pymol $PYMOL: not an executable file."
  fi
}

usage_options() {
  cat <<'EOF'
Options:
  -y, --yes          Answer yes to every prompt (for CI or an unattended run)
      --pymol PATH   Use this PyMOL executable instead of searching for one
      --skip-pymol   Do not look for or install PyMOL
      --skip-clients Do not register the server with Claude Code or Codex
      --force-pymolrc  Overwrite an existing, unmanaged ~/.pymolrc.py
  -h, --help         Show this message

What it does:
  1. Installs uv, if missing
  2. uv sync -- this repo's Python dependencies, from uv.lock
  3. Finds PyMOL, or installs it from conda-forge into a 'pymol-env' conda env
  4. Installs the socket plugin into PyMOL
  5. Adds the listener auto-start block to ~/.pymolrc.py
  6. Installs the pymol-mcp skill for Claude Code and Codex
  7. Registers the MCP server with whichever of those CLIs is installed

Every step is safe to re-run.
EOF
}

# Yes on --yes, yes on a bare Enter, and yes when there is no terminal to ask
# (CI, or a piped `curl | bash`): every prompt here guards an install this
# script exists to perform, so declining is the unusual answer.
confirm() {
  if [ "$ASSUME_YES" = "1" ] || [ ! -t 0 ]; then
    return 0
  fi
  printf '    %s [Y/n] ' "$1"
  read -r reply || return 0
  case "$reply" in
    [nN] | [nN][oO]) return 1 ;;
    *) return 0 ;;
  esac
}

# --- repository -------------------------------------------------------------

# The repo root is the parent of shell/, resolved from this file rather than
# the working directory, so the scripts work from anywhere.
resolve_repo_root() {
  REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
  [ -f "$REPO_ROOT/pyproject.toml" ] ||
    die "$REPO_ROOT does not look like the pymol-mcp repo (no pyproject.toml)."
  cd "$REPO_ROOT"
}

# --- uv ---------------------------------------------------------------------

ensure_uv() {
  step "Checking for uv"

  # Astral's installer puts uv here and adds it to PATH by editing shell rc
  # files, which a non-interactive shell never reads. Look in the directory
  # itself first, or a second run reinstalls uv every time.
  if ! have uv && [ -x "$HOME/.local/bin/uv" ]; then
    PATH="$HOME/.local/bin:$PATH"
    export PATH
  fi

  if have uv; then
    ok "uv $(uv --version 2>/dev/null | awk '{print $2}') at $(command -v uv)"
    return
  fi

  # Homebrew's uv upgrades with everything else the user already upgrades, so
  # prefer it when it is there; fall back to Astral's installer.
  if have brew; then
    info "Installing uv with Homebrew."
    brew install uv
  else
    info "Installing uv from https://astral.sh/uv."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # The installer drops uv here but only edits shell rc files, which this
    # process has already read.
    PATH="$HOME/.local/bin:$PATH"
    export PATH
    note "uv was installed to ~/.local/bin. If a new shell cannot find it, add
  that directory to your PATH."
  fi

  have uv || die "uv install finished but 'uv' is still not on PATH."
  ok "uv $(uv --version 2>/dev/null | awk '{print $2}')"
}

sync_python_deps() {
  step "Installing this repo's Python dependencies"
  info "uv sync  (creates .venv from uv.lock)"
  uv sync
  ok "Dependencies installed into $REPO_ROOT/.venv"
}

# --- PyMOL ------------------------------------------------------------------

# Runs in a command substitution, so it must never call die -- see parse_args.
find_pymol() {
  if [ -n "$PYMOL" ]; then
    printf '%s\n' "$PYMOL"
    return 0
  fi

  if have pymol; then
    command -v pymol
    return 0
  fi

  # Word-split the caller's globs, then let the shell expand each one. An
  # unmatched glob stays literal, and the -x test simply fails on it.
  local pattern candidate
  while IFS= read -r pattern; do
    [ -n "$pattern" ] || continue
    for candidate in $pattern; do
      if [ -x "$candidate" ]; then
        printf '%s\n' "$candidate"
        return 0
      fi
    done
  done <<EOF
$PYMOL_SEARCH_PATHS
EOF

  return 1
}

find_conda() {
  local candidate
  for candidate in conda mamba micromamba; do
    if have "$candidate"; then
      command -v "$candidate"
      return 0
    fi
  done
  for candidate in \
      "$HOME/miniforge3/bin/conda" \
      "$HOME/miniconda3/bin/conda" \
      "$HOME/anaconda3/bin/conda" \
      "$HOME/mambaforge/bin/conda" \
      /opt/miniforge3/bin/conda \
      /opt/miniconda3/bin/conda \
      /opt/anaconda3/bin/conda \
      /opt/homebrew/Caskroom/miniforge/base/bin/conda \
      /usr/local/Caskroom/miniforge/base/bin/conda ; do
    if [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

install_miniforge() {
  local tmpdir installer
  # The installer refuses to run unless $0 ends in ".sh" -- it uses that to
  # detect being sourced. So it needs a fixed filename, which means a private
  # directory rather than a mktemp'd file, to stay safe in a shared /tmp.
  tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/pymol-mcp-setup.XXXXXX")
  installer="$tmpdir/miniforge.sh"

  info "Downloading Miniforge for $OS_NAME."
  curl -fsSL "$CONDA_INSTALLER_URL" -o "$installer"
  info "Installing to $HOME/miniforge3 (batch mode, no shell rc changes)."
  # -b is non-interactive; without -u a second run over an existing directory
  # aborts rather than overwriting it.
  bash "$installer" -b -u -p "$HOME/miniforge3"
  rm -rf "$tmpdir"
  CONDA_BIN="$HOME/miniforge3/bin/conda"
  [ -x "$CONDA_BIN" ] || die "Miniforge install finished but $CONDA_BIN is missing."
  note "Miniforge was installed to ~/miniforge3 but your shell was not modified.
  Run '~/miniforge3/bin/conda init \"\$(basename \"\$SHELL\")\"' if you want the
  'conda' command available in new shells."
}

create_pymol_env() {
  step "Installing PyMOL with conda"

  if CONDA_BIN=$(find_conda); then
    ok "Found conda at $CONDA_BIN"
  else
    warn "No conda, mamba, or micromamba found."
    info "PyMOL is not on PyPI, so this installs it from conda-forge."
    if ! confirm "Download and install Miniforge to ~/miniforge3?"; then
      note "PyMOL was not installed. Install it yourself, then rerun this script,
  or pass the path: shell/$(basename "$0") --pymol /path/to/pymol"
      return 1
    fi
    install_miniforge
  fi

  # Matched with case rather than `... | grep -q`, which under pipefail can
  # report a match as a failure when grep closes the pipe early.
  local nl=$'\n' env_list
  env_list=$("$CONDA_BIN" env list 2>/dev/null || true)
  case "$nl$env_list" in
    *"$nl$CONDA_ENV_NAME "* | *"$nl$CONDA_ENV_NAME$nl"*)
      ok "conda env '$CONDA_ENV_NAME' already exists." ;;
    *)
      info "conda env create -f environment.yml   (this pulls ~1 GB, give it a few minutes)"
      "$CONDA_BIN" env create -f environment.yml
      ok "Created conda env '$CONDA_ENV_NAME'." ;;
  esac

  # Prefer a real path over `conda run`, which buffers PyMOL's output and adds
  # a second of startup to every call.
  local prefix candidate
  prefix=$("$CONDA_BIN" info --base 2>/dev/null || true)
  for candidate in "$prefix/envs/$CONDA_ENV_NAME/bin/pymol" \
                   "$HOME/miniforge3/envs/$CONDA_ENV_NAME/bin/pymol"; do
    if [ -x "$candidate" ]; then
      PYMOL_BIN="$candidate"
      return 0
    fi
  done

  # Env exists but the binary is somewhere unexpected; run_pymol falls back to
  # `conda run`, which does not care where it lives.
  PYMOL_BIN=""
  return 0
}

ensure_pymol() {
  if [ "$SKIP_PYMOL" = "1" ]; then
    step "Skipping PyMOL (--skip-pymol)"
    return 0
  fi

  step "Looking for PyMOL"
  if PYMOL_BIN=$(find_pymol); then
    ok "Found $PYMOL_BIN"
    return 0
  fi

  info "Not on PATH, and not in the usual conda or app locations."
  PYMOL_BIN=""
  create_pymol_env || return 1
  return 0
}

# Runs PyMOL whether or not we know its path: `conda run` covers the case where
# the env exists but the executable is not where conda's base prefix implies.
run_pymol() {
  if [ -n "$PYMOL_BIN" ]; then
    "$PYMOL_BIN" "$@"
  elif [ -n "$CONDA_BIN" ]; then
    "$CONDA_BIN" run -n "$CONDA_ENV_NAME" pymol "$@"
  else
    return 127
  fi
}

pymol_available() {
  [ -n "$PYMOL_BIN" ] || [ -n "$CONDA_BIN" ]
}

# --- plugin, pymolrc, skill -------------------------------------------------

install_plugin() {
  step "Installing the PyMOL socket plugin"
  if ! pymol_available; then
    warn "No PyMOL to install into -- skipped."
    note "Once PyMOL is installed, run:  pymol -cq scripts/install_plugin.py"
    return 0
  fi
  run_pymol -cq scripts/install_plugin.py
  ok "Plugin installed."
}

install_pymolrc() {
  step "Configuring PyMOL to start the listener at launch"
  if [ "$FORCE_PYMOLRC" = "1" ]; then
    uv run python scripts/install_pymolrc.py --force
  else
    uv run python scripts/install_pymolrc.py
  fi
}

install_skill() {
  step "Installing the pymol-mcp skill"
  uv run python scripts/install_skill.py
}

# --- MCP clients ------------------------------------------------------------

# Delegates to scripts/install_mcp.py so this and `make install-mcp` cannot
# drift apart. They already had: only this path registered anything, and it
# checked whether an entry *existed* rather than whether it was *correct* --
# so a registration left naming the old flat pymol_mcp_server.py survived every
# rerun of the installer, reported as "already registered".
register_clients() {
  if [ "$SKIP_CLIENTS" = "1" ]; then
    step "Skipping MCP client registration (--skip-clients)"
    return 0
  fi

  step "Registering the MCP server with your clients"
  if uv run python scripts/install_mcp.py; then
    ok "MCP client registration reconciled."
  else
    warn "MCP client registration failed."
    note "Check with:  claude mcp list   /   codex mcp list"
  fi
}

# --- summary ----------------------------------------------------------------

print_summary() {
  step "Done"
  if [ -n "$NOTES" ]; then
    printf '%sStill to do:%s\n' "$C_BOLD" "$C_OFF"
    printf '  %s\n' "$NOTES"
  fi
  cat <<EOF
${C_BOLD}Next:${C_OFF}
  1. Restart PyMOL. It should print
     "MCP socket plugin auto-started on port 9876" (or the next free port).
  2. Start a new Claude Code, Codex, or Claude Desktop session so it picks up
     the server.
  3. Ask it to "load PDB 1UBQ and show it as cartoon".

${C_DIM}Launch PyMOL from its own terminal or desktop icon: it writes to the
terminal it was started from, which garbles a terminal client's display.${C_OFF}
EOF
}
