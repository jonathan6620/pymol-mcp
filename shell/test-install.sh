#!/usr/bin/env bash
#
# Container tests for install-linux.sh, run by hand:
#
#     ./shell/test-install.sh            # fast: bootstrap, guards, idempotency
#     ./shell/test-install.sh --full     # adds the conda + PyMOL path (slow)
#
# Deliberately NOT wired into CI. The fast suite is about a minute; --full
# pulls ~1 GB of conda packages and fetches a structure from the PDB, which is
# too slow and too network-dependent to run on every push. Run it after
# touching shell/, scripts/install_*.py, or environment.yml.
#
# A container is the point: these scripts install uv, edit ~/.pymolrc.py and
# create conda environments, so a real test has to start from a machine with
# none of that and be thrown away afterwards.
#
# Most assertions below are regression tests for bugs that shipped in the
# first draft and that only showed up when the scripts were actually run --
# each is labelled with what it is guarding.

set -euo pipefail

IMAGE=${IMAGE:-ubuntu:24.04}
RUN_FULL=0

usage() {
  cat <<EOF
Usage: shell/test-install.sh [--full] [--image IMG]

  --full        Also test the conda path: Miniforge, the pymol-env
                environment, the plugin install, and driving a headless
                PyMOL over the socket. Needs ~1 GB of downloads and several
                minutes.
  --image IMG   Base image to test against (default: $IMAGE)
  -h, --help    This message

Requires Docker. Nothing is installed on this machine; every test runs in a
container that is discarded afterwards.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --full) RUN_FULL=1 ;;
    --image) IMAGE=$2; shift ;;
    --image=*) IMAGE=${1#--image=} ;;
    -h | --help) usage; exit 0 ;;
    *) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
  shift
done

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is not installed; these tests need it." >&2
  exit 2
fi
if ! docker info >/dev/null 2>&1; then
  echo "the docker daemon is not reachable; start it and retry." >&2
  exit 2
fi

echo "image:  $IMAGE"
echo "repo:   $REPO_ROOT"
echo "suite:  $([ "$RUN_FULL" = 1 ] && echo 'fast + full (slow)' || echo 'fast')"
echo

# The container script writes TAP-ish "ok -" / "not ok -" lines; the tally
# below is what decides this script's exit status, so a container that dies
# halfway cannot be mistaken for a pass.
output=$(mktemp "${TMPDIR:-/tmp}/pymol-mcp-test.XXXXXX")
trap 'rm -f "$output"' EXIT

set +e
docker run --rm -i \
  -e RUN_FULL="$RUN_FULL" \
  -v "$REPO_ROOT:/src:ro" \
  "$IMAGE" bash -s 2>&1 <<'CONTAINER' | tee "$output"
# Not set -e: a failing assertion must record itself and let the suite go on.
set -u

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); echo "ok - $1"; }
fail() { FAIL=$((FAIL + 1)); echo "not ok - $1"; }
check() {  # check <description> <command...>
  desc=$1; shift
  if "$@" >/dev/null 2>&1; then pass "$desc"; else fail "$desc"; fi
}
section() { echo; echo "# $1"; }

mkdir -p /work
tar -C /src -cf - --exclude=.venv --exclude=.git --exclude=node_modules \
                  --exclude=__pycache__ . | tar -C /work -xf -
cd /work

section "guards, before anything is installed"

# ubuntu:24.04 ships without curl, which is what fetches both uv and
# Miniforge. The script must say so rather than fail somewhere downstream.
out=$(./shell/install-linux.sh 2>&1)
rc=$?
if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -q "curl is required"; then
  pass "missing curl is reported clearly and exits non-zero"
else
  fail "missing curl is reported clearly and exits non-zero (rc=$rc)"
fi

out=$(./shell/install-linux.sh --pymol /no/such/pymol 2>&1)
rc=$?
# Guards a bug where this check lived inside a command substitution, so its
# exit ended only the subshell and an unusable --pymol quietly fell through
# to installing conda.
if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -q "not an executable file"; then
  pass "an unusable --pymol is a hard error, not a fallback to conda"
else
  fail "an unusable --pymol is a hard error, not a fallback to conda (rc=$rc)"
fi

out=$(./shell/install-linux.sh --bogus 2>&1)
rc=$?
if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -q "unknown option"; then
  pass "an unknown flag is rejected"
else
  fail "an unknown flag is rejected (rc=$rc)"
fi

apt-get -qq update >/dev/null 2>&1
apt-get -qq install -y curl ca-certificates >/dev/null 2>&1

section "bootstrap on a machine with nothing installed"

./shell/install-linux.sh --skip-pymol --skip-clients >/tmp/run1.log 2>&1
check "bootstrap run exits 0" test "$?" -eq 0
check "uv is installed"                 test -x "$HOME/.local/bin/uv"
check ".venv is created"                test -d /work/.venv
check "the pymol-mcp entry point is installed" test -x /work/.venv/bin/pymol-mcp
check "~/.pymolrc.py has the auto-start block" \
  grep -q _auto_start_mcp_socket "$HOME/.pymolrc.py"
check "the skill is linked for Claude Code" test -L "$HOME/.claude/skills/pymol-mcp"
check "the skill is linked for Codex"       test -L "$HOME/.agents/skills/pymol-mcp"
check "the run finishes without PyMOL and says what is left" \
  grep -q "Once PyMOL is installed" /tmp/run1.log

# Guards a bug where warnings went to stderr while headers went to stdout, so
# a piped log showed this warning underneath the *next* step's header.
warn_at=$(grep -n "No PyMOL to install into" /tmp/run1.log | head -1 | cut -d: -f1)
next_at=$(grep -n "Configuring PyMOL to start" /tmp/run1.log | head -1 | cut -d: -f1)
if [ -n "$warn_at" ] && [ -n "$next_at" ] && [ "$warn_at" -lt "$next_at" ]; then
  pass "warnings appear under their own step header, not the next one"
else
  fail "warnings appear under their own step header, not the next one"
fi

section "rerunning is idempotent"

./shell/install-linux.sh --skip-pymol --skip-clients >/tmp/run2.log 2>&1
check "second run exits 0" test "$?" -eq 0
# Guards a bug where uv was reinstalled every run: its installer advertises
# ~/.local/bin by editing shell rc files that a non-interactive shell never
# reads, so `command -v uv` kept coming up empty.
check "uv is not reinstalled on the second run" \
  sh -c '! grep -q "Installing uv from" /tmp/run2.log'
check "~/.pymolrc.py is left alone"  grep -q "Already up to date" /tmp/run2.log
check "the skill links are left alone" grep -q "Already linked" /tmp/run2.log

# The installer used to check whether a client entry *existed* and stop there,
# so a registration naming the pre-restructure pymol_mcp_server.py survived
# every rerun, reported as "already registered". Nothing here covered client
# registration at all, which is how that shipped. A stub CLI on PATH gives the
# reconcile path something to talk to without needing the real clients.
section "a stale client registration is repaired"

mkdir -p /tmp/stubbin
cat >/tmp/stubbin/claude <<'STUB'
#!/bin/sh
# Records its argv, and answers `mcp get` with the old, wrong command.
echo "$@" >>/tmp/claude-calls.log
case "$1 $2" in
  "mcp get")
    echo "pymol:"
    echo "  Type: stdio"
    echo "  Command: uv"
    echo "  Args: --directory /work run --quiet pymol_mcp_server.py"
    exit 0 ;;
  *) exit 0 ;;
esac
STUB
chmod +x /tmp/stubbin/claude
: >/tmp/claude-calls.log

PATH=/tmp/stubbin:$PATH ./shell/install-linux.sh --skip-pymol >/tmp/run4.log 2>&1
check "run with a stub client exits 0" test "$?" -eq 0
check "the stale entry is detected, not accepted" \
  grep -q "replaced a stale registration" /tmp/run4.log
check "the old command is named in the output" \
  grep -q "pymol_mcp_server.py" /tmp/run4.log
check "the stale entry is removed before being re-added" \
  grep -q "^mcp remove pymol" /tmp/claude-calls.log
check "the corrected command is registered" \
  grep -q "^mcp add pymol -s user -- uv --directory /work run --frozen pymol-mcp$" \
    /tmp/claude-calls.log

# Same stub, but already correct: reconciling must not rewrite it.
cat >/tmp/stubbin/claude <<'STUB'
#!/bin/sh
echo "$@" >>/tmp/claude-calls2.log
case "$1 $2" in
  "mcp get")
    echo "pymol:"
    echo "  Command: uv"
    echo "  Args: --directory /work run --frozen pymol-mcp"
    exit 0 ;;
  *) exit 0 ;;
esac
STUB
: >/tmp/claude-calls2.log

PATH=/tmp/stubbin:$PATH ./shell/install-linux.sh --skip-pymol >/tmp/run5.log 2>&1
check "a correct registration is reported as such" \
  grep -q "already registered correctly" /tmp/run5.log
check "a correct registration is not rewritten" \
  sh -c '! grep -q "mcp add" /tmp/claude-calls2.log'

if [ "${RUN_FULL:-0}" = "1" ]; then
  section "conda path: Miniforge, pymol-env, plugin, headless listener"

  ./shell/install-linux.sh --skip-clients >/tmp/run3.log 2>&1
  check "full run exits 0" test "$?" -eq 0
  # Guards the worst bug of the lot: Miniforge's installer refuses to run
  # unless $0 ends in ".sh", which it uses to detect being sourced. The
  # installer was downloaded to an mktemp name with no extension, so this
  # path failed for every user who did not already have conda.
  check "Miniforge is installed"   test -x "$HOME/miniforge3/bin/conda"
  check "the pymol-env environment has PyMOL" \
    test -x "$HOME/miniforge3/envs/pymol-env/bin/pymol"
  check "the plugin is linked into PyMOL's startup directory" \
    bash -c 'ls -d "$HOME"/miniforge3/envs/pymol-env/lib/python*/site-packages/pmg_tk/startup/pymol-mcp-socket-plugin'

  # No DISPLAY anywhere: proves the setup works headless, which is what a WSL
  # user without WSLg, and any server install, actually runs.
  nohup "$HOME/miniforge3/envs/pymol-env/bin/pymol" -cKq >/tmp/pymol.log 2>&1 &
  i=0
  while [ "$i" -lt 60 ]; do
    (exec 3<>/dev/tcp/localhost/9876) 2>/dev/null && break
    i=$((i + 1)); sleep 2
  done
  # bash -c, not sh: /dev/tcp is a bash feature and Ubuntu's sh is dash.
  check "the listener comes up on 9876 with no DISPLAY" \
    bash -c 'exec 3<>/dev/tcp/localhost/9876'

  /work/.venv/bin/python - >/tmp/probe.log 2>&1 <<'PY'
import json
import socket

sock = socket.create_connection(("localhost", 9876), 15)


def call(command, args):
    payload = {"type": "structured_command", "command": command, "args": args}
    sock.sendall((json.dumps(payload) + "\n").encode())
    buf = b""
    while b"\n" not in buf:
        buf += sock.recv(4096)
    return json.loads(buf.split(b"\n", 1)[0].decode())


call("fetch", {"code": "1ubq"})
reply = call("count", {"selection": "chain A and name CA"})
print("CA_ATOMS=%s" % reply["result"]["data"]["atoms"])
PY
  # Ubiquitin is 76 residues, so this is the whole path -- socket, dispatch,
  # and PyMOL itself -- returning a real answer rather than merely connecting.
  check "a headless PyMOL fetches 1UBQ and counts its 76 CA atoms" \
    grep -q "CA_ATOMS=76" /tmp/probe.log
fi

echo
echo "# passed $PASS, failed $FAIL"
CONTAINER
# PIPESTATUS, not $?: the pipeline ends in tee, which succeeds even when the
# container dies.
docker_rc=${PIPESTATUS[0]}
set -e

echo
if [ "$docker_rc" -ne 0 ]; then
  echo "FAIL: the container exited $docker_rc before finishing." >&2
  exit 1
fi
if grep -q '^not ok' "$output"; then
  echo "FAIL:" >&2
  grep '^not ok' "$output" >&2
  exit 1
fi
if ! grep -q '^# passed' "$output"; then
  echo "FAIL: the suite did not run to completion." >&2
  exit 1
fi
echo "PASS: $(grep -c '^ok' "$output") assertions."
