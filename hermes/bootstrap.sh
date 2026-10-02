#!/usr/bin/env bash
# Pemasangan lengkap dalam satu perintah: Hermes Agent × Descript × HelloMinds.
#
#   HELLOMINDS_ACCESS_KEY="..." HELLOMINDS_API_BASE="https://..." \
#     bash -c "$(curl -fsSL https://raw.githubusercontent.com/Jeaniro07/Jeaniro07/claude/elegant-rubin-wg1bba/hermes/bootstrap.sh)"
#
# Variabel opsional:
#   HELLOMINDS_ACCESS_KEY  Builder Access Key HelloMinds (boleh diisi nanti)
#   HELLOMINDS_API_BASE    Base URL Messaging API HelloMinds (boleh diisi nanti)
#   HELLOMINDS_PATH_*      Override path endpoint (mis. HELLOMINDS_PATH_SEND)
#   HELLOMINDS_MIND_ID     Jika diisi, uji koneksi dengan membuat percakapan ke Mind ini
#   INSTALL_HERMES=1       Pasang Hermes Agent dulu jika perintah `hermes` belum ada
#   HERMES_HOME            Default ~/.hermes
#   REPO_URL / REPO_BRANCH Sumber paket (default repo & branch ini)
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Jeaniro07/Jeaniro07.git}"
REPO_BRANCH="${REPO_BRANCH:-claude/elegant-rubin-wg1bba}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
BUNDLE_DIR="$HERMES_HOME/bundles/jeaniro07"
ENV_FILE="$HERMES_HOME/.env"

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '    \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

step "1/7 Memeriksa prasyarat"
for bin in git python3; do
  command -v "$bin" >/dev/null || die "$bin belum terpasang"
  ok "$bin: $(command -v "$bin")"
done
python3 -c 'import sys; sys.exit(sys.version_info < (3, 8))' || die "butuh Python >= 3.8"
if ! python3 -c 'import yaml' 2>/dev/null; then
  warn "PyYAML belum ada, memasang..."
  python3 -m pip install --user --quiet pyyaml 2>/dev/null \
    || python3 -m pip install --user --quiet --break-system-packages pyyaml \
    || die "gagal memasang PyYAML: jalankan 'pip install pyyaml' lalu ulangi"
fi
ok "PyYAML $(python3 -c 'import yaml; print(yaml.__version__)')"

step "2/7 Memeriksa Hermes Agent"
if command -v hermes >/dev/null; then
  ok "hermes: $(command -v hermes)"
elif [ "${INSTALL_HERMES:-0}" = "1" ]; then
  warn "hermes belum ada, memasang dari NousResearch/hermes-agent..."
  curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
  export PATH="$HOME/.local/bin:$PATH"
  command -v hermes >/dev/null || die "Hermes terpasang tapi belum ada di PATH: buka terminal baru lalu ulangi"
  ok "hermes terpasang"
else
  warn "perintah 'hermes' tidak ditemukan. Skill tetap dipasang ke $HERMES_HOME."
  warn "Jalankan ulang dengan INSTALL_HERMES=1 untuk memasang Hermes sekaligus."
fi

step "3/7 Mengambil paket dari $REPO_URL ($REPO_BRANCH)"
mkdir -p "$(dirname "$BUNDLE_DIR")"
if [ -d "$BUNDLE_DIR/.git" ]; then
  git -C "$BUNDLE_DIR" fetch --quiet --depth 1 origin "$REPO_BRANCH"
  git -C "$BUNDLE_DIR" reset --quiet --hard FETCH_HEAD
  ok "diperbarui: $BUNDLE_DIR"
else
  rm -rf "$BUNDLE_DIR"
  git clone --quiet --depth 1 --branch "$REPO_BRANCH" "$REPO_URL" "$BUNDLE_DIR" \
    || die "clone gagal. Repo privat? Login dulu (gh auth login) atau set REPO_URL dengan token."
  ok "di-clone ke $BUNDLE_DIR"
fi
[ -x "$BUNDLE_DIR/hermes/install.sh" ] || chmod +x "$BUNDLE_DIR/hermes/install.sh"

step "4/7 Memasang skill dan server MCP Descript"
HERMES_HOME="$HERMES_HOME" "$BUNDLE_DIR/hermes/install.sh" | sed -n '/^==>/,/^$/p' | sed 's/^/  /'

step "5/7 Menyimpan kredensial HelloMinds ke $ENV_FILE"
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"
python3 - "$ENV_FILE" <<'PY'
import os, re, sys
path = sys.argv[1]
keys = ["HELLOMINDS_ACCESS_KEY", "HELLOMINDS_API_BASE"] + sorted(
    k for k in os.environ if k.startswith("HELLOMINDS_PATH_"))
lines = open(path).read().splitlines()
for key in keys:
    val = os.environ.get(key, "")
    if not val:
        print(f"    - {key}: tidak diberikan, nilai lama dipertahankan")
        continue
    entry = f"{key}={val}"
    for i, line in enumerate(lines):
        if re.match(rf"^{key}=", line):
            lines[i] = entry
            break
    else:
        lines.append(entry)
    shown = val if key != "HELLOMINDS_ACCESS_KEY" else val[:4] + "…" + f"({len(val)} karakter)"
    print(f"    ✓ {key}={shown}")
open(path, "w").write("\n".join(lines) + "\n")
PY

step "6/7 Verifikasi"
for s in media/descript-video-editor agents/hellominds-mind agents/studio-orchestrator; do
  [ -f "$HERMES_HOME/skills/$s/SKILL.md" ] && ok "skill $s" || die "skill $s tidak ditemukan"
done
python3 - "$HERMES_HOME/config.yaml" <<'PY' || die "server MCP descript tidak ada di config.yaml"
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1])) or {}
d = (cfg.get("mcp_servers") or {})["descript"]
print(f"    \033[32m✓\033[0m MCP descript -> {d['url']} (auth: {d.get('auth')})")
PY
if command -v hermes >/dev/null; then
  hermes mcp list 2>/dev/null | sed 's/^/    /' || warn "'hermes mcp list' gagal"
fi

eval "$(python3 - "$ENV_FILE" <<'PY'
import shlex, sys
for line in open(sys.argv[1]):
    key, sep, val = line.rstrip("\n").partition("=")
    if sep and key.startswith("HELLOMINDS_") and key.replace("_", "").isalnum():
        print(f"export {key}={shlex.quote(val.strip().strip(chr(34)).strip(chr(39)))}")
PY
)"
HM="$HERMES_HOME/skills/agents/hellominds-mind/scripts/hellominds.py"
if [ -n "${HELLOMINDS_ACCESS_KEY:-}" ] && [ -n "${HELLOMINDS_API_BASE:-}" ]; then
  if python3 "$HM" list >/dev/null 2>"$HERMES_HOME/.hellominds-test.log"; then
    ok "HelloMinds API terhubung (ListConversations)"
    if [ -n "${HELLOMINDS_MIND_ID:-}" ]; then
      python3 "$HM" create "$HELLOMINDS_MIND_ID" | sed 's/^/    /' && ok "percakapan dengan Mind dibuat"
    fi
  else
    warn "uji HelloMinds gagal: $(head -c 300 "$HERMES_HOME/.hellominds-test.log")"
    warn "cek key, base URL, dan path endpoint di dokumentasi Builder"
  fi
  rm -f "$HERMES_HOME/.hellominds-test.log"
else
  warn "HelloMinds belum dikonfigurasi: isi HELLOMINDS_ACCESS_KEY & HELLOMINDS_API_BASE di $ENV_FILE"
fi

step "7/7 Selesai"
cat <<EOF
    Paket   : $BUNDLE_DIR
    Skill   : $HERMES_HOME/skills/{media/descript-video-editor,agents/hellominds-mind,agents/studio-orchestrator}
    Config  : $HERMES_HOME/config.yaml (backup: config.yaml.bak.*)
    Rahasia : $ENV_FILE (chmod 600)

    Berikutnya:
      1. Jalankan 'hermes' -> browser terbuka untuk login OAuth Descript, pilih Drive.
         (Server tanpa layar: pakai skill opsional mcp-oauth-remote-gateway.)
      2. Coba: "Pakai studio-orchestrator: minta Mind saya menulis naskah 60 detik,
         lalu edit rekaman terbaru saya di Descript, tambah caption dan Studio Sound."
      3. Update kapan saja: jalankan perintah yang sama lagi.
EOF
