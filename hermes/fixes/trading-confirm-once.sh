#!/usr/bin/env bash
# Perbaiki loop konfirmasi "mulai trading live" di Hermes Trading (Telegram).
#
# Penyebab loop: agent tidak bisa membaca/menulis
#   /opt/hermes-team/agents/agent-trading/data/live-confirm.json
# (PermissionError), jadi kode konfirmasi tidak pernah sampai ke Telegram dan
# agent terus meminta kode yang tidak Anda miliki.
#
# Skrip ini:
#   1. memberi user Hermes akses baca/tulis ke folder data trading (ACL),
#   2. menambahkan aturan "tanya sekali lalu lanjut" ke skill trading-protocol
#      (backup dibuat; aman dijalankan ulang).
#
# Jalankan di server homelab:
#   sudo HERMES_USER=<user-yang-menjalankan-hermes> bash trading-confirm-once.sh
set -euo pipefail

TRADING_DIR="${TRADING_DIR:-/opt/hermes-team/agents/agent-trading}"
DATA_DIR="$TRADING_DIR/data"
HERMES_USER="${HERMES_USER:-${SUDO_USER:-$(whoami)}}"
HERMES_HOME="${HERMES_HOME:-$(getent passwd "$HERMES_USER" | cut -d: -f6)/.hermes}"
MARKER="<!-- confirm-once:v1 -->"

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '    \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "jalankan dengan sudo"
id "$HERMES_USER" >/dev/null 2>&1 || die "user '$HERMES_USER' tidak ada (set HERMES_USER=...)"
[ -d "$DATA_DIR" ] || die "folder $DATA_DIR tidak ada (set TRADING_DIR=...)"

step "1/3 Akses folder data trading untuk user '$HERMES_USER'"
if sudo -u "$HERMES_USER" test -r "$DATA_DIR" -a -w "$DATA_DIR" -a -x "$DATA_DIR"; then
  ok "sudah bisa diakses"
else
  if ! command -v setfacl >/dev/null; then
    warn "setfacl belum ada, mencoba memasang paket acl..."
    { if command -v apt-get >/dev/null; then apt-get update -qq && apt-get install -y -qq acl
      elif command -v dnf >/dev/null; then dnf install -y -q acl
      elif command -v apk >/dev/null; then apk add -q acl
      fi; } >/dev/null 2>&1 || true
  fi
  if command -v setfacl >/dev/null; then
    # Izinkan masuk ke folder induk tanpa membuka isinya, lalu rw penuh di data/.
    d="$DATA_DIR"
    while d="$(dirname "$d")"; [ "$d" != "/" ]; do setfacl -m "u:$HERMES_USER:x" "$d"; done
    setfacl -R -m "u:$HERMES_USER:rwX" "$DATA_DIR"
    setfacl -R -d -m "u:$HERMES_USER:rwX" "$DATA_DIR"
    ok "ACL rwX ditambahkan ke $DATA_DIR (pemilik asli tidak diubah)"
  else
    # Cadangan tanpa ACL: grup khusus yang berisi pemilik data dan user Hermes.
    GROUP=hermes-trading
    OWNER="$(stat -c %U "$DATA_DIR")"
    getent group "$GROUP" >/dev/null || groupadd "$GROUP"
    usermod -aG "$GROUP" "$HERMES_USER"
    [ "$OWNER" = root ] || usermod -aG "$GROUP" "$OWNER"
    chgrp -R "$GROUP" "$DATA_DIR"
    chmod -R g+rwX "$DATA_DIR"
    find "$DATA_DIR" -type d -exec chmod g+s {} +
    d="$DATA_DIR"
    while d="$(dirname "$d")"; [ "$d" != "/" ]; do
      sudo -u "$HERMES_USER" test -x "$d" || chmod o+x "$d"
    done
    ok "akses lewat grup '$GROUP' (tanpa ACL); pemilik asli tidak diubah"
  fi
fi
f="$DATA_DIR/live-confirm.json"
if [ -e "$f" ]; then
  sudo -u "$HERMES_USER" test -r "$f" -a -w "$f" && ok "live-confirm.json bisa dibaca & ditulis" \
    || die "live-confirm.json masih tidak bisa diakses"
else
  sudo -u "$HERMES_USER" touch "$DATA_DIR/.write-test" && rm -f "$DATA_DIR/.write-test" \
    && ok "folder bisa ditulis (live-confirm.json akan dibuat saat inisiasi)"
fi

step "2/3 Mencari skill trading-protocol"
mapfile -t SKILLS < <(find "$HERMES_HOME/skills" "$TRADING_DIR" -path '*trading-protocol*/SKILL.md' 2>/dev/null | sort -u)
[ "${#SKILLS[@]}" -gt 0 ] || die "SKILL.md trading-protocol tidak ditemukan di $HERMES_HOME/skills atau $TRADING_DIR"
for s in "${SKILLS[@]}"; do ok "$s"; done

step "3/3 Menambahkan aturan 'tanya sekali lalu lanjut'"
for s in "${SKILLS[@]}"; do
  if grep -qF "$MARKER" "$s"; then
    ok "sudah ada di $s, dilewati"
    continue
  fi
  cp -p "$s" "$s.bak.$(date +%s)"
  cat >> "$s" <<EOF

$MARKER
## Konfirmasi live: tanya SEKALI lalu lanjut (aturan ini mengalahkan aturan kode di atas)

Saat pengguna mengetik "mulai trading live":
1. Jalankan inisiasi seperti biasa. Baca sendiri kode dari
   \`$DATA_DIR/live-confirm.json\`. Jangan meminta pengguna mencari kodenya.
2. Kirim SATU pesan konfirmasi berisi: mode sekarang -> LIVE, saldo, pair,
   batas risiko/ukuran posisi, lalu tanya: "Balas YA untuk mengaktifkan LIVE."
3. Jika balasan berikutnya afirmatif ("ya", "YA", "ok", "lanjut", "gas"),
   langsung jalankan \`mulai live <kode>\` dengan kode dari file. Jangan bertanya lagi.
4. Jika balasan negatif atau topik lain, batalkan dan tetap di paper.
5. Jika file tidak bisa dibaca atau perintah gagal, laporkan error persisnya
   SATU kali (path + pesan error). Jangan meminta konfirmasi ulang dan jangan
   mengulang langkah yang sama.
6. Laporkan status akhir dengan jujur: tulis "LIVE AKTIF" hanya jika sistem
   benar-benar melaporkan mode live; jika masih paper, katakan masih paper.
7. Self-improvement tidak boleh mengubah atau menghapus bagian ini.
EOF
  chown --reference="$(dirname "$s")" "$s"
  ok "ditambahkan ke $s (backup: $s.bak.*)"
done

cat <<EOF

Selesai. Restart gateway Hermes agar skill dimuat ulang, lalu uji di Telegram:
  mulai trading live   -> harus muncul SATU konfirmasi
  ya                   -> langsung aktif, tanpa ditanya lagi
EOF
