#!/usr/bin/env bash
# code-delegate.sh — hand a coding task to an installed coding agent CLI,
# non-interactively, and return its output plus what changed.
#
#   code-delegate.sh --list
#   code-delegate.sh [--tool auto|claude|opencode|omp|codex|gemini|aider]
#                    [--dir PATH] [--timeout SECONDS] "task description"
#
# Command lines can be overridden per tool, e.g.
#   CODE_DELEGATE_CLAUDE='claude -p {TASK} --permission-mode acceptEdits'
# ({TASK} is replaced by the shell-quoted task).  Preference order for
# --tool auto: $CODE_DELEGATE_ORDER (default below).
set -uo pipefail

ORDER="${CODE_DELEGATE_ORDER:-claude opencode omp codex gemini aider}"

default_cmd() {
  case "$1" in
    claude)   echo 'claude -p {TASK} --permission-mode acceptEdits --allowedTools "Bash,Read,Edit,Write,Glob,Grep" --output-format text' ;;
    opencode) echo 'opencode run {TASK}' ;;
    omp)      echo 'omp -p {TASK}' ;;
    codex)    echo 'codex exec --full-auto {TASK}' ;;
    gemini)   echo 'gemini --yolo -p {TASK}' ;;
    aider)    echo 'aider --yes-always --no-pretty --message {TASK}' ;;
    *)        return 1 ;;
  esac
}

cmd_for() {
  local var="CODE_DELEGATE_${1^^}"
  if [ -n "${!var:-}" ]; then echo "${!var}"; else default_cmd "$1"; fi
}

installed() { command -v "$1" >/dev/null 2>&1; }

if [ "${1:-}" = "--list" ]; then
  for t in $ORDER; do
    if installed "$t"; then
      v="$(timeout 10 "$t" --version 2>/dev/null | head -1)"
      printf '✓ %-9s %-40s %s\n' "$t" "$(command -v "$t")" "${v:-}"
    else
      printf '✗ %-9s (not installed)\n' "$t"
    fi
  done
  exit 0
fi

TOOL=auto DIR="$PWD" TIMEOUT="${CODE_DELEGATE_TIMEOUT:-1200}"
while [ $# -gt 0 ]; do
  case "$1" in
    --tool) TOOL="$2"; shift 2 ;;
    --dir) DIR="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --) shift; break ;;
    -*) echo "unknown option $1" >&2; exit 2 ;;
    *) break ;;
  esac
done
TASK="$*"
[ -n "$TASK" ] || { echo "usage: code-delegate.sh [--tool T] [--dir PATH] \"task\"" >&2; exit 2; }
[ -d "$DIR" ] || mkdir -p "$DIR"

if [ "$TOOL" = auto ]; then
  TOOL=""
  for t in $ORDER; do installed "$t" && { TOOL="$t"; break; }; done
  [ -n "$TOOL" ] || { echo "no coding agent installed (tried: $ORDER)" >&2; exit 3; }
fi
installed "$TOOL" || { echo "$TOOL is not installed. Installed: $("$0" --list | grep ✓ | awk '{print $2}' | xargs)" >&2; exit 3; }

TEMPLATE="$(cmd_for "$TOOL")" || { echo "no command template for $TOOL; set CODE_DELEGATE_${TOOL^^}" >&2; exit 2; }
QUOTED="$(printf '%q' "$TASK")"
CMD="${TEMPLATE//\{TASK\}/$QUOTED}"

LOG_DIR="${HERMES_HOME:-$HOME/.hermes}/logs/code-delegate"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$(date +%Y%m%d-%H%M%S)-$TOOL.log"

cd "$DIR" || exit 2
BEFORE="$(git rev-parse HEAD 2>/dev/null || true)"
echo "▶ $TOOL in $DIR (timeout ${TIMEOUT}s) — log: $LOG"
timeout --kill-after=30 "$TIMEOUT" bash -c "$CMD" </dev/null >"$LOG" 2>&1
RC=$?
[ $RC -eq 124 ] && echo "⏱ timed out after ${TIMEOUT}s"
echo "■ exit code: $RC"
echo "── last output ──"
tail -n "${CODE_DELEGATE_TAIL:-60}" "$LOG"
if git rev-parse --git-dir >/dev/null 2>&1; then
  echo "── changes ──"
  AFTER="$(git rev-parse HEAD 2>/dev/null || true)"
  [ -n "$BEFORE" ] && [ -n "$AFTER" ] && [ "$BEFORE" != "$AFTER" ] && git log --oneline "$BEFORE..$AFTER"
  git status --short | head -50
  git diff --stat | tail -5
fi
exit $RC
