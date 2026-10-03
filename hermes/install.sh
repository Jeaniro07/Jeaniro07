#!/usr/bin/env bash
# Pasang skill Descript + HelloMinds + orkestrator ke Hermes Agent.
# Aman dijalankan berulang: skill disalin ulang, config MCP digabung tanpa menimpa server lain.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
CONFIG="$HERMES_HOME/config.yaml"

mkdir -p "$HERMES_HOME/skills"

echo "==> Menyalin skill ke $HERMES_HOME/skills"
for dir in "$HERE"/skills/*/*/; do
  rel="${dir#"$HERE"/skills/}"
  rel="${rel%/}"
  mkdir -p "$HERMES_HOME/skills/$rel"
  cp -R "$dir". "$HERMES_HOME/skills/$rel/"
  # Ganti $SKILL_DIR dengan path absolut agar agent langsung bisa menjalankan script-nya.
  sed -i "s|\$SKILL_DIR|$HERMES_HOME/skills/$rel|g" "$HERMES_HOME/skills/$rel/SKILL.md"
  echo "    + $rel"
done
find "$HERMES_HOME/skills" -path '*/scripts/*' \( -name '*.py' -o -name '*.sh' \) -exec chmod +x {} +

echo "==> Menggabungkan server MCP ke $CONFIG"
[ -f "$CONFIG" ] && cp "$CONFIG" "$CONFIG.bak.$(date +%s)"
python3 - "$CONFIG" "$HERE/config.mcp.yaml" <<'PY'
import os, sys
try:
    import yaml
except ImportError:
    sys.exit("PyYAML belum terpasang: pip install pyyaml  (atau salin config.mcp.yaml manual)")
config_path, snippet_path = sys.argv[1], sys.argv[2]
config = {}
if os.path.exists(config_path):
    with open(config_path) as f:
        config = yaml.safe_load(f) or {}
with open(snippet_path) as f:
    snippet = yaml.safe_load(f)
servers = config.setdefault("mcp_servers", {}) or {}
config["mcp_servers"] = servers
for name, spec in snippet["mcp_servers"].items():
    if name in servers:
        print(f"    = {name} sudah ada, dilewati")
    else:
        servers[name] = spec
        print(f"    + {name}")
with open(config_path, "w") as f:
    yaml.safe_dump(config, f, sort_keys=False, allow_unicode=True)
PY

ENV_FILE="$HERMES_HOME/.env"
touch "$ENV_FILE"
for var in HELLOMINDS_ACCESS_KEY HELLOMINDS_API_BASE; do
  grep -q "^$var=" "$ENV_FILE" || echo "$var=" >> "$ENV_FILE"
done

cat <<EOF

Selesai. Langkah berikutnya:
  1. Isi HELLOMINDS_ACCESS_KEY dan HELLOMINDS_API_BASE di $ENV_FILE
  2. Jalankan: hermes        (browser terbuka untuk login OAuth Descript + pilih Drive)
  3. Cek:      hermes mcp list   dan   hermes skills list
  4. Coba:     "Pakai studio-orchestrator: buat klip 60 detik dari podcast terbaru saya"
EOF
