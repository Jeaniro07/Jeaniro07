#!/usr/bin/env bash
# hermes-doctor: satu perintah untuk mendiagnosis & memperbaiki Hermes.
#
#   Diagnosa saja : bash hermes-doctor.sh
#   Perbaiki      : sudo bash hermes-doctor.sh --fix --restart
#
# Tanpa clone repo:
#   curl -fsSL https://raw.githubusercontent.com/Jeaniro07/Jeaniro07/claude/elegant-rubin-wg1bba/hermes/doctor/hermes-doctor.sh | sudo bash -s -- --fix --restart
#
# Semua argumen diteruskan ke hermes_doctor.py (lihat --help).
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Jeaniro07/Jeaniro07.git}"
REPO_BRANCH="${REPO_BRANCH:-claude/elegant-rubin-wg1bba}"

# 1. Temukan bundle: di samping script ini, atau unduh.
SELF="${BASH_SOURCE[0]:-}"
if [ -n "$SELF" ] && [ -f "$(dirname "$SELF")/hermes_doctor.py" ]; then
  BUNDLE="$(cd "$(dirname "$SELF")/.." && pwd)"
else
  CACHE="${HERMES_DOCTOR_DIR:-/opt/hermes-doctor}"
  [ -w "$(dirname "$CACHE")" ] || CACHE="$HOME/.cache/hermes-doctor"
  if [ -d "$CACHE/.git" ]; then
    git -C "$CACHE" fetch --quiet --depth 1 origin "$REPO_BRANCH" && git -C "$CACHE" reset --quiet --hard FETCH_HEAD
  else
    rm -rf "$CACHE"
    git clone --quiet --depth 1 --branch "$REPO_BRANCH" "$REPO_URL" "$CACHE" \
      || { echo "clone gagal (repo privat? login dulu: gh auth login)"; exit 1; }
  fi
  BUNDLE="$CACHE/hermes"
fi

# 2. Pilih Python yang punya PyYAML: venv Hermes dulu, lalu python3 sistem.
PY=""
for cand in "${HERMES_HOME:-$HOME/.hermes}/hermes-agent/venv/bin/python" \
            ${SUDO_USER:+"$(getent passwd "$SUDO_USER" | cut -d: -f6)/.hermes/hermes-agent/venv/bin/python"} \
            /home/*/.hermes/hermes-agent/venv/bin/python python3; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import yaml, sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
    PY="$cand"; break
  fi
done
if [ -z "$PY" ]; then
  echo "PyYAML belum ada, memasang..."
  python3 -m pip install --user --quiet pyyaml 2>/dev/null \
    || python3 -m pip install --user --quiet --break-system-packages pyyaml
  PY=python3
fi

exec "$PY" "$BUNDLE/doctor/hermes_doctor.py" --bundle "$BUNDLE" "$@"
