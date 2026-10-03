#!/usr/bin/env python3
"""hermes-doctor — diagnose and fix the beginner problems that make Hermes
agents loop, ignore tools/MCP/skills, or fail to reach homelab services.

  hermes_doctor.py                 diagnose only (changes nothing)
  hermes_doctor.py --fix           diagnose, then apply safe fixes (with backups)
  hermes_doctor.py --fix --restart also restart the Hermes gateway afterwards

Options:
  --homes A,B          Hermes homes to check (default: auto-discover)
  --probe N            model probe rounds (default 3, 0 = skip)
  --base-url URL --model NAME --key-env VAR
                       override the model endpoint used by the probe
  --bundle DIR         bundle directory (default: parent of this file)
"""

import argparse
import datetime as dt
import glob
import json
import os
import pwd
import re
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import urllib.error
import urllib.request

try:
    import yaml
except ImportError:
    sys.exit("PyYAML missing: run via hermes-doctor.sh, or: python3 -m pip install --user pyyaml")

RULES_START = "<!-- hermes-doctor:rules:start -->"
RULES_END = "<!-- hermes-doctor:rules:end -->"
OUR_SKILLS = ["homelab/homelab-services", "software-development/coding-delegate"]
TS = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
IS_ROOT = os.geteuid() == 0

C = {"CRIT": "\033[31m", "WARN": "\033[33m", "OK": "\033[32m", "INFO": "\033[36m",
     "FIXED": "\033[35m", "END": "\033[0m", "B": "\033[1m"}
if not sys.stdout.isatty():
    C = {k: "" for k in C}
ICON = {"CRIT": "✗", "WARN": "!", "OK": "✓", "INFO": "·", "FIXED": "⚒"}


class Report:
    def __init__(self):
        self.items = []
        self.lines = []

    def out(self, text=""):
        print(text)
        self.lines.append(re.sub(r"\033\[[0-9;]*m", "", text))

    def section(self, title):
        self.out(f"\n{C['B']}━━ {title} ━━{C['END']}")

    home = ""

    def add(self, level, title, detail="", fix=None):
        self.items.append({"level": level, "title": title, "detail": detail, "fix": fix, "home": self.home})
        self.out(f"  {C[level]}{ICON[level]} {title}{C['END']}")
        for line in str(detail).splitlines():
            if line.strip():
                self.out(f"      {line}")
        if fix and level in ("CRIT", "WARN"):
            self.out(f"      → {fix}")


R = Report()


# ───────────────────────── helpers ─────────────────────────

def run(cmd, timeout=60, env=None):
    try:
        p = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True,
                           timeout=timeout, env=env)
        return p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s"
    except (FileNotFoundError, PermissionError) as e:
        return 127, str(e)


def owner_of(path):
    st = os.stat(path)
    return st.st_uid, st.st_gid


def chown_like(path, ref):
    """When running as root, give path (recursively) the owner of ref."""
    if not IS_ROOT or not os.path.lexists(path):
        return
    uid, gid = owner_of(ref)
    paths = [path]
    if os.path.isdir(path) and not os.path.islink(path):
        for root, dirs, files in os.walk(path):
            paths += [os.path.join(root, n) for n in dirs + files]
    for p in paths:
        try:
            os.lchown(p, uid, gid)
        except OSError:
            pass


def backup(home, path):
    """Copy the ORIGINAL file once per run; later fixes in the same run keep that copy."""
    if not os.path.exists(path):
        return
    bdir = os.path.join(home, "backups", f"doctor-{TS}")
    dst = os.path.join(bdir, os.path.basename(path).lstrip(".") or "file")
    if os.path.exists(dst):
        return
    os.makedirs(bdir, exist_ok=True)
    shutil.copy2(path, dst)
    os.chmod(dst, 0o600)
    chown_like(os.path.join(home, "backups"), home)


def read(path):
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        return f.read()


def write(path, text, ref_home):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    chown_like(path, ref_home)


def parse_env(path):
    out = {}
    if not os.path.exists(path):
        return out
    for line in read(path).splitlines():
        line = line.strip().rstrip("\r")
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip().removeprefix("export ").strip()] = v.strip().strip('"').strip("'")
    return out


def walk(obj, path=()):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield path + (str(k),), v
            yield from walk(v, path + (str(k),))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield path + (str(i),), v
            yield from walk(v, path + (str(i),))


# ───────────────────────── discovery ─────────────────────────

def is_hermes_home(d):
    return any(os.path.exists(os.path.join(d, f)) for f in ("config.yaml", "SOUL.md", ".env", "skills"))


def discover_homes(explicit):
    if explicit:
        return [os.path.abspath(os.path.expanduser(h)) for h in explicit.split(",")]
    cands = []
    if os.environ.get("HERMES_HOME"):
        cands.append(os.environ["HERMES_HOME"])
    cands.append(os.path.expanduser("~/.hermes"))
    if IS_ROOT:
        cands += ["/root/.hermes"] + glob.glob("/home/*/.hermes")
    if os.environ.get("SUDO_USER"):
        cands.append(os.path.join(pwd.getpwnam(os.environ["SUDO_USER"]).pw_dir, ".hermes"))
    more = []
    for c in list(cands):
        more += glob.glob(os.path.join(c, "profiles", "*"))
    more += glob.glob("/opt/hermes-team/agents/*") + glob.glob("/opt/hermes-team/agents/*/.hermes")
    seen, homes = set(), []
    for c in cands + more:
        r = os.path.realpath(c)
        if r not in seen and os.path.isdir(r) and is_hermes_home(r):
            seen.add(r)
            homes.append(r)
    return homes


def find_hermes():
    for c in [shutil.which("hermes"), os.path.expanduser("~/.local/bin/hermes"),
              os.path.expanduser("~/.hermes/hermes-agent/venv/bin/hermes")] + \
            glob.glob("/home/*/.local/bin/hermes") + glob.glob("/home/*/.hermes/hermes-agent/venv/bin/hermes"):
        if c and os.path.exists(c):
            return c
    return None


# ───────────────────────── system checks ─────────────────────────

def check_system(bundle):
    R.section("Sistem")
    hermes = find_hermes()
    if not hermes:
        R.add("CRIT", "perintah 'hermes' tidak ditemukan",
              fix="pasang Hermes atau tambahkan ke PATH (~/.local/bin)")
    else:
        rc, v = run([hermes, "--version"], 30)
        R.add("OK" if rc == 0 else "WARN", f"hermes: {hermes}", v.splitlines()[0] if v else "")
        rc, out = run([hermes, "doctor"], 120)
        if rc != 127 and out and "invalid choice" not in out and "No such command" not in out:
            bad = [l for l in out.splitlines() if re.search(r"✗|❌|error|fail|missing|not found", l, re.I)]
            if bad:
                R.add("WARN", f"'hermes doctor' melaporkan {len(bad)} masalah", "\n".join(bad[:15]),
                      fix="lihat output 'hermes doctor' lengkap")
            else:
                R.add("OK", "'hermes doctor' tidak menemukan masalah")

    rc, ps = run("ps -eo user,pid,etime,args | grep -i '[h]ermes' | grep -iv doctor", 10)
    gw = [l for l in ps.splitlines() if "gateway" in l.lower()]
    if gw:
        R.add("OK", f"gateway berjalan ({len(gw)} proses)", "\n".join(l[:160] for l in gw[:6]))
        users = {l.split()[0] for l in gw}
        if "root" in users and len(users) == 1:
            R.add("WARN", "gateway berjalan sebagai root",
                  "file yang dibuat agent jadi milik root; skill/akses lain bisa gagal",
                  fix="jalankan gateway sebagai user biasa (systemd User=)")
    else:
        R.add("WARN", "gateway Hermes tidak terdeteksi berjalan",
              fix="jalankan 'hermes gateway' atau aktifkan service systemd-nya")

    script = os.path.join(bundle, "skills/software-development/coding-delegate/scripts/code-delegate.sh")
    rc, out = run(["bash", script, "--list"], 60)
    have = [l.split()[1] for l in out.splitlines() if l.startswith("✓")]
    if have:
        R.add("OK", f"coding agent terpasang: {', '.join(have)}", out)
    else:
        R.add("WARN", "tidak ada coding agent (claude/opencode/omp/codex) di PATH user ini",
              "agent tidak bisa mendelegasikan coding",
              fix="pasang di user yang menjalankan gateway, mis. npm i -g @anthropic-ai/claude-code opencode-ai")
    if "claude" in have:
        cred = [p for p in glob.glob(os.path.expanduser("~/.claude/.credentials.json")) +
                glob.glob(os.path.expanduser("~/.claude.json"))]
        if not cred and not os.environ.get("ANTHROPIC_API_KEY"):
            R.add("WARN", "Claude Code terpasang tapi belum login untuk user ini",
                  fix="jalankan 'claude' sekali sebagai user gateway lalu login (atau set ANTHROPIC_API_KEY)")


# ───────────────────────── per-home checks ─────────────────────────

def fix_text_file(home, path, label, args):
    """CRLF/BOM/tab cleanup that is always safe for YAML/.env."""
    raw = read(path)
    new = raw.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    issues = []
    if raw.startswith("﻿"):
        issues.append("BOM")
    if "\r" in raw:
        issues.append("CRLF (diedit dari Windows)")
    if issues:
        if args.fix:
            backup(home, path)
            write(path, new, home)
            R.add("FIXED", f"{label}: {', '.join(issues)} dibersihkan")
        else:
            R.add("WARN", f"{label}: {', '.join(issues)}", fix="--fix akan membersihkannya")
    return new


def check_config(home, args):
    path = os.path.join(home, "config.yaml")
    if not os.path.exists(path):
        R.add("INFO", "config.yaml tidak ada (memakai default)")
        return {}
    text = fix_text_file(home, path, "config.yaml", args)
    try:
        cfg = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        if "\t" in text:
            fixed = text.expandtabs(2)
            try:
                cfg = yaml.safe_load(fixed) or {}
                if args.fix:
                    backup(home, path)
                    write(path, fixed, home)
                    R.add("FIXED", "config.yaml: TAB diganti spasi; YAML kini valid")
                else:
                    R.add("CRIT", "config.yaml rusak karena TAB", str(e)[:300], fix="--fix akan memperbaikinya")
                return cfg
            except yaml.YAMLError:
                pass
        R.add("CRIT", "config.yaml TIDAK VALID: Hermes memakai default, MCP & setting Anda diabaikan",
              str(e)[:400], fix="perbaiki baris yang ditunjuk (indentasi 2 spasi, tanpa TAB)")
        return {}
    if not isinstance(cfg, dict):
        R.add("CRIT", "config.yaml bukan mapping YAML")
        return {}
    R.add("OK", "config.yaml valid")
    return cfg


def set_approvals_smart(home, args, current):
    path = os.path.join(home, "config.yaml")
    text = read(path) if os.path.exists(path) else ""
    lines = text.splitlines()
    idx = next((i for i, l in enumerate(lines) if re.match(r"^approvals:\s*(#.*)?$", l)), None)
    if idx is None:
        new = text.rstrip("\n") + ("\n\n" if text.strip() else "") + "approvals:\n  mode: smart\n"
    else:
        end = idx + 1
        while end < len(lines) and (lines[end].startswith((" ", "\t")) or not lines[end].strip()):
            end += 1
        block = lines[idx + 1:end]
        mi = next((i for i, l in enumerate(block) if re.match(r"^\s+mode:", l)), None)
        if mi is None:
            block.insert(0, "  mode: smart")
        else:
            block[mi] = re.sub(r"(mode:\s*).*", r"\1smart", block[mi])
        lines[idx + 1:end] = block
        new = "\n".join(lines) + "\n"
    try:
        ok = (yaml.safe_load(new) or {}).get("approvals", {}).get("mode") == "smart"
    except yaml.YAMLError:
        ok = False
    if not ok:
        R.add("WARN", "gagal mengubah approvals.mode otomatis", fix="set manual: approvals:\\n  mode: smart")
        return
    backup(home, path)
    write(path, new, home)
    R.add("FIXED", f"approvals.mode: {current or '(default)'} → smart")


def check_approvals(home, cfg, args):
    appr = cfg.get("approvals") if isinstance(cfg.get("approvals"), dict) else {}
    mode = appr.get("mode")
    yolo = cfg.get("yolo") or os.environ.get("HERMES_YOLO_MODE") == "1"
    if yolo or mode == "off":
        R.add("WARN", "persetujuan dimatikan (yolo/off): agent bisa menjalankan perintah berbahaya tanpa tanya",
              fix="pakai approvals.mode: smart")
    elif mode == "smart":
        R.add("OK", "approvals.mode: smart")
    elif args.fix:
        set_approvals_smart(home, args, mode)
    else:
        R.add("WARN", f"approvals.mode: {mode or 'default'}: setiap perintah ditanya → terasa berputar-putar",
              fix="--fix mengubahnya ke 'smart' (risiko rendah otomatis, berbahaya tetap ditanya)")
    for p, v in walk(cfg):
        if p[-1] in ("command_allowlist", "allowlist") and isinstance(v, list):
            R.add("INFO", f"{'.'.join(p)}: {len(v)} entri")


def check_env(home, args):
    path = os.path.join(home, ".env")
    if not os.path.exists(path):
        R.add("INFO", ".env tidak ada")
        return {}
    text = fix_text_file(home, path, ".env", args)
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode & 0o077:
        if args.fix:
            os.chmod(path, 0o600)
            R.add("FIXED", f".env permission {oct(mode)} → 0o600")
        else:
            R.add("WARN", f".env bisa dibaca user lain ({oct(mode)})", fix="chmod 600 .env")
    keys, dupes, empty, spaced = {}, [], [], []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        if k != k.strip() or v != v.strip():
            spaced.append(k.strip())
        k = k.strip()
        if k in keys:
            dupes.append(k)
        keys[k] = v.strip().strip('"').strip("'")
        if not keys[k]:
            empty.append(k)
    if (dupes or spaced) and args.fix:
        # Keep comments and order; trim spaces around '='; keep the LAST value of a duplicate key.
        out, last = [], {}
        lines = text.splitlines()
        for i, line in enumerate(lines):
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                last[s.split("=", 1)[0].strip()] = i
        for i, line in enumerate(lines):
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, v = s.split("=", 1)
                if last[k.strip()] != i:
                    continue
                line = f"{k.strip()}={v.strip()}"
            out.append(line)
        backup(home, path)
        write(path, "\n".join(out) + "\n", home)
        os.chmod(path, 0o600)
        R.add("FIXED", ".env dirapikan" + (f": duplikat {sorted(set(dupes))} (nilai terakhir dipakai)" if dupes else "")
              + (f": spasi di {spaced}" if spaced else ""))
    else:
        if dupes:
            R.add("WARN", f".env: kunci ganda {sorted(set(dupes))}: yang terpakai hanya salah satu",
                  fix="--fix menyisakan nilai terakhir")
        if spaced:
            R.add("WARN", f".env: spasi di sekitar '=' pada {spaced}: nilai bisa terbaca salah",
                  fix="--fix merapikannya")
    if empty:
        R.add("WARN", f".env: nilai kosong: {', '.join(empty)}")
    R.add("OK", f".env: {len(keys)} variabel")
    return keys


def check_var_refs(cfg, env):
    refs = set()
    for _, v in walk(cfg):
        if isinstance(v, str):
            refs |= set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", v))
    missing = sorted(r for r in refs if r not in env and r not in os.environ)
    if missing:
        R.add("CRIT", f"config.yaml memakai variabel yang tidak ada di .env: {', '.join(missing)}",
              "server MCP / provider yang memakainya akan gagal tanpa pesan jelas",
              fix="tambahkan ke .env: " + ", ".join(f"{m}=..." for m in missing))


def probe_url(url, headers=None, timeout=8):
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.status, ""
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:  # noqa: BLE001
        return None, str(getattr(e, "reason", e))


def check_mcp(cfg, env):
    servers = cfg.get("mcp_servers") or {}
    if not isinstance(servers, dict) or not servers:
        R.add("INFO", "tidak ada mcp_servers di config.yaml")
        return
    if len(servers) > 8:
        R.add("WARN", f"{len(servers)} server MCP aktif: ratusan tool membuat model bingung dan malah tidak memakai tool",
              fix="batasi dengan 'tools: include: [...]' per server, matikan yang jarang dipakai")
    for name, spec in servers.items():
        if not isinstance(spec, dict):
            R.add("CRIT", f"MCP {name}: format salah (harus mapping)")
            continue
        if spec.get("enabled") is False:
            R.add("INFO", f"MCP {name}: dinonaktifkan")
            continue
        def expand(s):
            return re.sub(r"\$\{(\w+)\}", lambda m: env.get(m.group(1), os.environ.get(m.group(1), "")), str(s))
        if spec.get("url"):
            url = expand(spec["url"])
            hdrs = {k: expand(v) for k, v in (spec.get("headers") or {}).items()}
            st, err = probe_url(url, hdrs)
            if st is None:
                R.add("CRIT", f"MCP {name}: tidak bisa dihubungi dari mesin ini ({url})", err,
                      fix="cek URL/port, firewall, dan bahwa host Hermes bisa route ke IP itu")
            elif st in (401, 403) and spec.get("auth") != "oauth" and not hdrs:
                R.add("WARN", f"MCP {name}: butuh autentikasi (HTTP {st}) tapi tidak ada headers/auth",
                      fix="tambahkan headers Authorization atau 'auth: oauth'")
            elif st in (401, 403) and spec.get("auth") == "oauth":
                R.add("INFO", f"MCP {name}: OAuth, server merespons {st} (normal sebelum login)")
            else:
                R.add("OK", f"MCP {name}: terjangkau (HTTP {st})")
        elif spec.get("command"):
            cmd = spec["command"]
            if shutil.which(cmd) or os.path.exists(cmd):
                R.add("OK", f"MCP {name}: perintah '{cmd}' ada")
            else:
                R.add("CRIT", f"MCP {name}: perintah '{cmd}' tidak ditemukan di PATH",
                      fix=f"pasang '{cmd}' (mis. nodejs/npx atau uv/uvx) untuk user gateway")
        else:
            R.add("CRIT", f"MCP {name}: tidak ada 'url' atau 'command'")


def add_to_yaml_list(home, keypath, item):
    """Append item to the YAML list at keypath, editing text so comments survive."""
    path = os.path.join(home, "config.yaml")
    lines = read(path).splitlines()
    key = re.escape(keypath[-1])
    for i, line in enumerate(lines):
        m = re.match(rf"^(\s*){key}:\s*\[(.*)\]\s*(#.*)?$", line)
        if m:
            inner = m.group(2).strip()
            lines[i] = f"{m.group(1)}{keypath[-1]}: [{inner + ', ' if inner else ''}{item}]"
            break
        m = re.match(rf"^(\s*){key}:\s*(#.*)?$", line)
        if m and i + 1 < len(lines) and re.match(r"^\s*- ", lines[i + 1]):
            indent = re.match(r"^(\s*)- ", lines[i + 1]).group(1)
            j = i + 1
            while j < len(lines) and re.match(rf"^{indent}- ", lines[j]):
                j += 1
            lines.insert(j, f"{indent}- {item}")
            break
    new = "\n".join(lines) + "\n"
    try:
        node = yaml.safe_load(new)
        for k in keypath:
            node = node[int(k)] if isinstance(node, list) else node[k]
        ok = item in node
    except (yaml.YAMLError, KeyError, IndexError, TypeError, ValueError):
        ok = False
    if ok:
        backup(home, path)
        write(path, new, home)
    return ok


def check_toolsets(home, cfg, args):
    found = False
    for p, v in list(walk(cfg)):
        key = ".".join(p).lower()
        if "toolset" in key and isinstance(v, list):
            found = True
            low = [str(x).lower() for x in v]
            plat = next((x for x in ("telegram", "discord", "whatsapp", "slack") if x in key), None)
            if plat and not any(t in low for t in ("terminal", "all", "*", "hermes-" + plat)):
                if args.fix and add_to_yaml_list(home, p, "terminal"):
                    R.add("FIXED", f"'terminal' ditambahkan ke {'.'.join(p)}: agent {plat} kini bisa menjalankan script skill")
                else:
                    R.add("CRIT", f"{'.'.join(p)} tidak memuat 'terminal': di {plat} agent tidak bisa menjalankan script skill",
                          f"isi sekarang: {v}", fix=f"tambahkan 'terminal' ke {'.'.join(p)}")
            else:
                R.add("INFO", f"{'.'.join(p)}: {v}")
    if not found:
        R.add("INFO", "tidak ada pembatasan toolset di config (memakai default)")


def detect_model(cfg, env, args):
    base, model, key = args.base_url, args.model, None
    if args.key_env:
        key = env.get(args.key_env) or os.environ.get(args.key_env)
    m = cfg.get("model")
    if isinstance(m, str):
        model = model or m
    elif isinstance(m, dict):
        model = model or m.get("default") or m.get("model") or m.get("name")
        base = base or m.get("base_url")
        key = key or m.get("api_key")
    if not base:
        for _, v in walk(cfg):
            if isinstance(v, dict) and v.get("base_url"):
                base, key = v["base_url"], key or v.get("api_key")
                model = model or v.get("model") or v.get("default")
                break
    if key and "${" in str(key):
        key = re.sub(r"\$\{(\w+)\}", lambda mm: env.get(mm.group(1), os.environ.get(mm.group(1), "")), key)
    if not key:
        for name in ("OMNIROUTE_API_KEY", "CUSTOM_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "LLM_API_KEY"):
            if env.get(name) or os.environ.get(name):
                key = env.get(name) or os.environ.get(name)
                break
    if not base:
        for name in ("OPENAI_BASE_URL", "OMNIROUTE_BASE_URL", "CUSTOM_BASE_URL"):
            if env.get(name) or os.environ.get(name):
                base = env.get(name) or os.environ.get(name)
                break
    return base, model, key


def chat(base, key, payload, timeout=90):
    urls = [base.rstrip("/") + "/chat/completions"]
    if not base.rstrip("/").endswith("/v1"):
        urls.append(base.rstrip("/") + "/v1/chat/completions")
    last = None
    for url in urls:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {key or 'none'}"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}"
            if e.code != 404:
                break
        except Exception as e:  # noqa: BLE001
            last = str(getattr(e, "reason", e))
            break
    raise RuntimeError(last)


TOOLS = [{"type": "function", "function": {
    "name": "run_command", "description": "Run a shell command on the homelab server",
    "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}]


def probe_model(cfg, env, args):
    R.section("Uji model: apakah model benar-benar memanggil tool?")
    if args.probe <= 0:
        R.add("INFO", "dilewati (--probe 0)")
        return
    base, model, key = detect_model(cfg, env, args)
    if not base or not model:
        R.add("WARN", "endpoint/model tidak terdeteksi dari config, uji dilewati",
              fix="jalankan ulang dengan --base-url http://IP:PORT/v1 --model NAMA --key-env NAMA_VAR")
        return
    R.out(f"  endpoint: {base}   model: {model}")
    if re.search(r"combo|auto|router|fallback", str(model), re.I) or "omniroute" in str(base).lower():
        R.add("WARN", "model adalah combo/router: tiap pesan bisa dijawab model berbeda",
              "model yang lemah dalam tool-calling membuat agent cuma ngobrol, tanya ulang, atau mengaku 'tidak bisa akses'",
              fix="untuk agent kerja (JEV, trading, homelab), pin SATU model kuat; combo hanya untuk chat santai")
    tests = {
        "pakai-tool": [{"role": "system", "content": "You are a homelab agent. Use tools to act."},
                       {"role": "user", "content": "Cek uptime server sekarang."}],
        "ikuti-ya": [{"role": "system", "content": "You are a homelab agent. Use tools to act."},
                     {"role": "user", "content": "Restart container jellyfin."},
                     {"role": "assistant", "content": "Saya akan menjalankan `pct reboot 105` untuk me-restart Jellyfin. Lanjutkan?"},
                     {"role": "user", "content": "ya"}],
    }
    score = {k: 0 for k in tests}
    seen = set()
    errors = []
    for i in range(args.probe):
        for name, msgs in tests.items():
            try:
                resp = chat(base, key, {"model": model, "messages": msgs, "tools": TOOLS,
                                        "tool_choice": "auto", "temperature": 0, "max_tokens": 300})
            except RuntimeError as e:
                errors.append(str(e))
                continue
            seen.add(resp.get("model", "?"))
            msg = (resp.get("choices") or [{}])[0].get("message") or {}
            if msg.get("tool_calls"):
                score[name] += 1
    if errors and not any(score.values()) and len(errors) >= args.probe:
        R.add("CRIT", "endpoint model tidak bisa dipanggil", errors[0],
              fix="cek base_url, API key, dan bahwa OmniRoute berjalan")
        return
    if len(seen) > 1:
        R.add("WARN", f"jawaban datang dari {len(seen)} model berbeda: {', '.join(sorted(seen))}")
    for name, n in score.items():
        label = {"pakai-tool": "langsung memakai tool saat diminta",
                 "ikuti-ya": "langsung eksekusi setelah dijawab 'ya'"}[name]
        lvl = "OK" if n == args.probe else ("WARN" if n else "CRIT")
        R.add(lvl, f"{label}: {n}/{args.probe}",
              fix=None if lvl == "OK" else "model ini penyebab utama agent tidak pakai tool / tanya ulang: ganti ke model dengan tool-calling kuat")


def check_skills(home, bundle, args):
    sdir = os.path.join(home, "skills")
    files = glob.glob(os.path.join(sdir, "**", "SKILL.md"), recursive=True)
    broken, recent = [], []
    week = dt.datetime.now().timestamp() - 3 * 86400
    for f in files:
        txt = read(f)
        m = re.match(r"^﻿?---\s*\n(.*?)\n---", txt.replace("\r\n", "\n"), re.S)
        try:
            meta = yaml.safe_load(m.group(1)) if m else None
        except yaml.YAMLError:
            meta = None
        if not isinstance(meta, dict) or not meta.get("name") or not meta.get("description"):
            broken.append(os.path.relpath(f, sdir))
        if os.path.getmtime(f) > week and RULES_START not in txt:
            recent.append(os.path.relpath(os.path.dirname(f), sdir))
    R.add("OK" if files else "WARN", f"{len(files)} skill terpasang")
    if broken:
        R.add("CRIT", f"{len(broken)} skill frontmatter rusak: Hermes tidak akan memuatnya",
              "\n".join(broken[:15]), fix="setiap SKILL.md harus diawali ---, name:, description:, ---")
    if len(files) > 150:
        R.add("WARN", "skill sangat banyak: model sulit memilih yang tepat",
              fix="arsipkan skill yang tidak dipakai")
    if recent:
        R.add("INFO", "skill yang berubah 3 hari terakhir (cek apakah diubah self-improvement):",
              "\n".join(sorted(set(recent))[:20]))
    for rel in OUR_SKILLS:
        dst = os.path.join(sdir, rel)
        src = os.path.join(bundle, "skills", rel)
        if not os.path.isdir(src):
            continue
        present = os.path.exists(os.path.join(dst, "SKILL.md"))
        if args.fix:
            install_skill(src, dst, home)
            R.add("FIXED", f"skill {rel} {'diperbarui' if present else 'dipasang'}")
        elif not present:
            R.add("WARN", f"skill {rel} belum ada", fix="--fix akan memasangnya")


def install_skill(src, dst, home):
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    for f in glob.glob(os.path.join(dst, "**", "*"), recursive=True):
        if f.endswith((".sh", ".py")):
            os.chmod(f, 0o755)
    skill_md = os.path.join(dst, "SKILL.md")
    write(skill_md, read(skill_md).replace("$SKILL_DIR", dst), home)
    chown_like(os.path.join(home, "skills"), home)


def check_soul(home, bundle, args):
    path = os.path.join(home, "SOUL.md")
    rules = read(os.path.join(bundle, "rules", "agent-rules.md")).strip()
    block = f"{RULES_START}\n{rules}\n{RULES_END}"
    text = read(path) if os.path.exists(path) else ""
    if len(text) > 20000:
        R.add("WARN", f"SOUL.md sangat panjang ({len(text)} karakter): instruksi penting tenggelam",
              fix="ringkas SOUL.md, pindahkan detail ke skill")
    if block in text:
        R.add("OK", "SOUL.md: aturan kerja anti-loop sudah terpasang (versi terbaru)")
        return
    has_old = RULES_START in text
    if not args.fix:
        R.add("WARN", "SOUL.md: aturan anti-loop/pakai-tools " + ("versi lama" if has_old else "belum ada"),
              fix="--fix akan menambahkannya")
        return
    backup(home, path)
    if has_old:
        text = re.sub(re.escape(RULES_START) + r".*?" + re.escape(RULES_END), lambda _: block, text, flags=re.S)
    else:
        text = (text.rstrip() + "\n\n" if text.strip() else "") + block + "\n"
    write(path, text, home)
    R.add("FIXED", f"SOUL.md: aturan anti-loop {'diperbarui' if has_old else 'ditambahkan'}")


def check_ownership(home, args):
    uid, _ = owner_of(home)
    wrong = []
    for root, dirs, files in os.walk(home):
        dirs[:] = [d for d in dirs if d not in ("hermes-agent", "node_modules", ".git", "backups", "venv", ".venv")]
        for n in dirs + files:
            p = os.path.join(root, n)
            try:
                if os.lstat(p).st_uid != uid:
                    wrong.append(p)
            except OSError:
                pass
        if len(wrong) > 500:
            break
    if not wrong:
        R.add("OK", f"kepemilikan file konsisten ({pwd.getpwuid(uid).pw_name})")
        return
    others = sorted({pwd.getpwuid(os.lstat(p).st_uid).pw_name for p in wrong[:50] if os.path.exists(p)})
    detail = "\n".join(os.path.relpath(p, home) for p in wrong[:10])
    if args.fix and IS_ROOT:
        for p in wrong:
            try:
                os.lchown(p, uid, owner_of(home)[1])
            except OSError:
                pass
        R.add("FIXED", f"{len(wrong)} file milik {others} dikembalikan ke {pwd.getpwuid(uid).pw_name}")
    else:
        R.add("CRIT", f"{len(wrong)} file di home ini milik {others} (biasanya akibat sudo): agent gagal baca/tulis → PermissionError",
              detail, fix="jalankan doctor dengan sudo --fix")


def check_registry(home, primary, bundle, args):
    reg = os.path.join(home, "homelab", "services.yaml")
    primary_reg = os.path.join(primary, "homelab", "services.yaml")
    if not os.path.exists(reg):
        if args.fix:
            os.makedirs(os.path.dirname(reg), exist_ok=True)
            if home != primary and os.path.exists(primary_reg):
                os.symlink(primary_reg, reg)
                R.add("FIXED", f"registry homelab ditautkan ke {primary_reg}")
            else:
                shutil.copy(os.path.join(bundle, "templates", "services.example.yaml"), reg)
                R.add("FIXED", f"registry homelab dibuat dari template: {reg}",
                      "EDIT IP/port & isi token di .env, lalu jalankan doctor lagi")
            chown_like(os.path.dirname(reg), home)
        else:
            R.add("WARN", "registry layanan homelab belum ada: agent tidak tahu alamat & token Postiz/HA/Jellyfin/CouchDB/AdGuard",
                  fix="--fix membuat ~/.hermes/homelab/services.yaml dari template")
            return
    template = os.path.join(bundle, "templates", "services.example.yaml")
    if os.path.exists(template) and read(os.path.realpath(reg)) == read(template):
        R.add("WARN", f"registry homelab masih berisi CONTOH: agent belum tahu IP asli Postiz/HA/Jellyfin/CouchDB/AdGuard",
              f"edit {os.path.realpath(reg)}",
              fix="isi IP:port container Proxmox Anda (atau isi proxmox lalu jalankan 'homelab discover --write'), "
                  "isi token di .env, lalu jalankan doctor lagi")
        return
    script = os.path.join(bundle, "skills/homelab/homelab-services/scripts/homelab.py")
    env = dict(os.environ, HERMES_HOME=home, HOMELAB_REGISTRY=reg)
    rc, out = run([sys.executable, script, "check"], 180, env=env)
    if "registry not found" in out:
        return
    ok = [l for l in out.splitlines() if l.startswith("✓")]
    bad = [l for l in out.splitlines() if l.startswith("✗")]
    if ok:
        R.add("OK", f"{len(ok)} layanan homelab bisa dipakai agent", "\n".join(ok))
    if bad:
        R.add("CRIT", f"{len(bad)} layanan homelab TIDAK bisa dipakai agent", "\n".join(bad),
              fix="ikuti petunjuk per baris (DOWN = jaringan/IP, AUTH = token di .env)")
    if not ok and not bad and out:
        R.add("WARN", "cek layanan homelab gagal", out[:500])


def link_commands(primary, args):
    if not args.fix:
        return
    bindir = "/usr/local/bin" if IS_ROOT else os.path.expanduser("~/.local/bin")
    os.makedirs(bindir, exist_ok=True)
    for name, rel in (("homelab", "skills/homelab/homelab-services/scripts/homelab.py"),
                      ("code-delegate", "skills/software-development/coding-delegate/scripts/code-delegate.sh")):
        target = os.path.join(primary, rel)
        link = os.path.join(bindir, name)
        if os.path.exists(target):
            if os.path.islink(link) or not os.path.exists(link):
                if os.path.islink(link):
                    os.unlink(link)
                os.symlink(target, link)
    R.add("FIXED", f"perintah 'homelab' dan 'code-delegate' tersedia di {bindir}")


def restart_gateway(args):
    if not args.restart:
        return
    R.section("Restart gateway")
    rc, units = run("systemctl list-units --all --plain --no-legend '*hermes*' 2>/dev/null; "
                    "systemctl --user list-units --all --plain --no-legend '*hermes*' 2>/dev/null", 20)
    names = sorted({l.split()[0] for l in units.splitlines() if l.strip() and l.split()[0].endswith(".service")})
    if names:
        for n in names:
            rc, out = run(f"systemctl restart {n} || systemctl --user restart {n}", 60)
            R.add("FIXED" if rc == 0 else "WARN", f"restart {n}", out[:200])
        return
    hermes = find_hermes()
    if hermes:
        rc, out = run([hermes, "gateway", "restart"], 60)
        R.add("FIXED" if rc == 0 else "WARN", "hermes gateway restart", out[:300],
              fix=None if rc == 0 else "restart manual: hentikan proses gateway lalu jalankan 'hermes gateway'")
    else:
        R.add("WARN", "tidak menemukan service gateway untuk di-restart", fix="restart manual")


def summary(args, homes):
    R.section("Ringkasan")
    n = {k: sum(1 for i in R.items if i["level"] == k) for k in ICON}
    R.out(f"  {C['CRIT']}✗ kritis: {n['CRIT']}{C['END']}   {C['WARN']}! peringatan: {n['WARN']}{C['END']}   "
          f"{C['FIXED']}⚒ diperbaiki: {n['FIXED']}{C['END']}   {C['OK']}✓ ok: {n['OK']}{C['END']}")
    todo = [i for i in R.items if i["level"] in ("CRIT", "WARN")]
    if todo:
        R.out(f"\n  {C['B']}Yang masih perlu Anda lakukan:{C['END']}")
        for k, i in enumerate(sorted(todo, key=lambda x: x["level"] != "CRIT"), 1):
            where = f" ({i['home']})" if i["home"] and len(homes) > 1 else ""
            R.out(f"  {k}. [{i['level']}] {i['title']}{where}" + (f"\n     → {i['fix']}" if i["fix"] else ""))
    if not args.fix and todo:
        R.out(f"\n  Jalankan lagi dengan {C['B']}--fix{C['END']} untuk memperbaiki yang bisa diperbaiki otomatis.")
    if args.fix and not args.restart:
        R.out("\n  Restart gateway Hermes agar perubahan dimuat (atau jalankan lagi dengan --restart).")
    logdir = os.path.join(homes[0] if homes else os.path.expanduser("~/.hermes"), "logs")
    os.makedirs(logdir, exist_ok=True)
    log = os.path.join(logdir, f"doctor-{TS}.md")
    with open(log, "w", encoding="utf-8") as f:
        f.write("\n".join(R.lines) + "\n")
    if homes:
        chown_like(logdir, homes[0])
    print(f"\n  Laporan lengkap: {log}")
    return 1 if n["CRIT"] else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--restart", action="store_true")
    ap.add_argument("--homes")
    ap.add_argument("--probe", type=int, default=3)
    ap.add_argument("--base-url")
    ap.add_argument("--model")
    ap.add_argument("--key-env")
    ap.add_argument("--bundle", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    args = ap.parse_args()

    R.out(f"{C['B']}hermes-doctor{C['END']}  mode: {'PERBAIKI (--fix)' if args.fix else 'DIAGNOSA (tidak mengubah apa pun)'}"
          f"  user: {pwd.getpwuid(os.geteuid()).pw_name}")
    check_system(args.bundle)
    homes = discover_homes(args.homes)
    if not homes:
        R.add("CRIT", "tidak menemukan home Hermes (~/.hermes)", fix="pakai --homes /path/ke/.hermes")
        return summary(args, homes)
    primary = homes[0]
    probed = False
    for home in homes:
        R.section(f"Home: {home}")
        R.home = home
        if not os.access(home, os.W_OK) and args.fix:
            R.add("WARN", "tidak punya izin tulis ke home ini", fix="jalankan dengan sudo")
        cfg = check_config(home, args)
        env = check_env(home, args)
        check_var_refs(cfg, env)
        check_approvals(home, cfg, args)
        check_toolsets(home, cfg, args)
        check_mcp(cfg, env)
        check_skills(home, args.bundle, args)
        check_soul(home, args.bundle, args)
        check_registry(home, primary, args.bundle, args)
        check_ownership(home, args)
        if not probed and (cfg.get("model") or args.base_url):
            probe_model(cfg, env, args)
            probed = True
    if not probed:
        probe_model({}, parse_env(os.path.join(primary, ".env")), args)
    link_commands(primary, args)
    restart_gateway(args)
    return summary(args, homes)


if __name__ == "__main__":
    sys.exit(main())
