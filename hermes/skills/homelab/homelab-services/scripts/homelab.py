#!/usr/bin/env python3
"""homelab.py — one CLI for every homelab service the Hermes agent may use.

Services live in a registry (services.yaml). Secrets stay in .env and are
referenced by name (token_env, user_env, password_env), never stored in the
registry. Auth headers are added per service type, so the agent only has to
say *what* it wants.

Usage:
  homelab.py where                          Show registry and .env paths in use
  homelab.py list                           Services, URL, whether credentials are set
  homelab.py check [name...]                Authenticated health check (OK / AUTH / DOWN)
  homelab.py call <name> <METHOD> <path> [--data JSON|@file] [--query k=v ...]
                                            [--full] [--yes]
                                            Authenticated API request (DELETE needs --yes)
  homelab.py discover [--proxmox name] [--write]
                                            List Proxmox guests + IPs, probe known service
                                            ports, print (or merge) registry entries
  homelab.py types                          Supported service types and their auth

Registry lookup order: $HOMELAB_REGISTRY, $HERMES_HOME/homelab/services.yaml,
~/.hermes/homelab/services.yaml, /etc/hermes/homelab/services.yaml
"""

import base64
import json
import os
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request


def _ensure_yaml():
    try:
        import yaml  # noqa: F401
        return
    except ImportError:
        pass
    # Re-run under Hermes' own venv, which ships PyYAML.
    home = os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes"))
    for py in (f"{home}/hermes-agent/venv/bin/python", f"{home}/hermes-agent/.venv/bin/python"):
        if os.path.exists(py) and os.path.realpath(py) != os.path.realpath(sys.executable):
            os.execv(py, [py] + sys.argv)
    sys.exit("error: PyYAML missing. Run: python3 -m pip install --user pyyaml")


_ensure_yaml()
import yaml  # noqa: E402

TYPES = {
    # type: (health path, auth kind, notes)
    "homeassistant": ("/api/", "bearer", "Long-lived token: HA profile > Security > Long-lived access tokens"),
    "jellyfin": ("/System/Info", "mediabrowser", "API key: Dashboard > API Keys"),
    "emby": ("/System/Info", "mediabrowser", "API key: Dashboard > API Keys"),
    "couchdb": ("/_all_dbs", "basic", "Admin user/password from the CouchDB container env"),
    "postiz": ("{api_base}/integrations", "raw", "Settings > Public API key; api_base default /api/public/v1"),
    "proxmox": ("/api2/json/version", "pve", "Datacenter > Permissions > API Tokens; value USER@REALM!TOKENID=SECRET"),
    "portainer": ("/api/status", "x-api-key", "My account > Access tokens"),
    "grafana": ("/api/health", "bearer", "Administration > Service accounts > token"),
    "n8n": ("/api/v1/workflows?limit=1", "x-n8n", "Settings > n8n API"),
    "immich": ("/api/server/ping", "x-api-key", "Account settings > API Keys"),
    "adguard": ("/control/status", "basic", "AdGuard Home admin user/password (user_env/password_env)"),
    "sonarr": ("/api/v3/system/status", "x-api-key", "Settings > General > API Key"),
    "radarr": ("/api/v3/system/status", "x-api-key", "Settings > General > API Key"),
    "generic": ("/", "custom", "Set header_name / token_env / token_prefix / health"),
}

# Ports probed by `discover`, with the service types that commonly use them.
# Shared ports (80, 3000) are told apart by FINGERPRINT paths.
KNOWN_PORTS = {
    8123: ["homeassistant"], 8096: ["jellyfin"], 5984: ["couchdb"], 5000: ["postiz"],
    4200: ["postiz"], 9443: ["portainer"], 9000: ["portainer"],
    3000: ["adguard", "grafana"], 80: ["adguard"], 5678: ["n8n"], 2283: ["immich"],
    8989: ["sonarr"], 7878: ["radarr"],
}
FINGERPRINT = {
    "adguard": "/control/status", "grafana": "/api/health", "homeassistant": "/api/",
    "jellyfin": "/System/Info/Public", "couchdb": "/", "postiz": "/", "portainer": "/api/status",
    "n8n": "/healthz", "immich": "/api/server/ping", "sonarr": "/api/v3/system/status",
    "radarr": "/api/v3/system/status",
}
BASIC_AUTH_TYPES = {"couchdb", "adguard"}


def _home():
    return os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes"))


def registry_path():
    candidates = [
        os.environ.get("HOMELAB_REGISTRY"),
        os.path.join(_home(), "homelab", "services.yaml"),
        os.path.expanduser("~/.hermes/homelab/services.yaml"),
        "/etc/hermes/homelab/services.yaml",
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return next(p for p in candidates if p)


def env_files():
    # The registry may be a symlink to the main agent's home; its .env sits one level up.
    real_reg_home = os.path.dirname(os.path.dirname(os.path.realpath(registry_path())))
    files = [os.path.join(_home(), ".env"), os.path.expanduser("~/.hermes/.env"),
             os.path.join(real_reg_home, ".env")]
    seen, out = set(), []
    for f in files:
        r = os.path.realpath(f)
        if r not in seen and os.path.exists(f):
            seen.add(r)
            out.append(f)
    return out


def load_env():
    for path in env_files():
        try:
            lines = open(path, encoding="utf-8").read().splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip().rstrip("\r")
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip().removeprefix("export ").strip()
            val = val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)


def load_registry():
    path = registry_path()
    if not os.path.exists(path):
        sys.exit(f"error: registry not found: {path}\n"
                 f"  create it from templates/services.example.yaml or run: homelab.py discover --write")
    data = yaml.safe_load(open(path, encoding="utf-8")) or {}
    services = data.get("services") or {}
    if not isinstance(services, dict):
        sys.exit(f"error: 'services' in {path} must be a mapping")
    return services


def _secret(svc, key):
    name = svc.get(key)
    return os.environ.get(name, "") if name else ""


def auth_headers(svc):
    kind = TYPES.get(svc.get("type", "generic"), TYPES["generic"])[1]
    token = _secret(svc, "token_env")
    h = {}
    if kind == "bearer" and token:
        h["Authorization"] = f"Bearer {token}"
    elif kind == "mediabrowser" and token:
        h["Authorization"] = f'MediaBrowser Token="{token}"'
        h["X-Emby-Token"] = token
    elif kind == "basic":
        user, pw = _secret(svc, "user_env"), _secret(svc, "password_env")
        if user:
            h["Authorization"] = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
    elif kind == "raw" and token:
        h["Authorization"] = token
    elif kind == "pve" and token:
        h["Authorization"] = token if token.startswith("PVEAPIToken=") else f"PVEAPIToken={token}"
    elif kind == "x-api-key" and token:
        h["X-Api-Key"] = token
    elif kind == "x-n8n" and token:
        h["X-N8N-API-KEY"] = token
    elif kind == "custom" and token:
        h[svc.get("header_name", "Authorization")] = svc.get("token_prefix", "") + token
    for k, v in (svc.get("headers") or {}).items():
        h[k] = os.path.expandvars(str(v))
    return h


def credentials_missing(svc):
    missing = [svc[k] for k in ("token_env", "user_env", "password_env")
               if svc.get(k) and not os.environ.get(svc[k])]
    return missing


def _ssl_ctx(svc):
    if svc.get("verify_tls", True):
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _url(svc, path):
    base = str(svc["url"]).rstrip("/")
    path = path.replace("{api_base}", str(svc.get("api_base", "/api/public/v1")).rstrip("/"))
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return base + "/" + path.lstrip("/")


def request(svc, method, path, data=None, query=None, timeout=20):
    url = _url(svc, path)
    if query:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(query)
    body = None
    headers = {"Accept": "application/json", "User-Agent": "hermes-homelab/1.0"}
    headers.update(auth_headers(svc))
    if data is not None:
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, method=method.upper(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx(svc)) as r:
            return r.status, r.read().decode(errors="replace"), url
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace"), url
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
        reason = getattr(e, "reason", e)
        return None, str(reason), url


def _hint(status, text, svc):
    if status is None:
        if "CERTIFICATE_VERIFY_FAILED" in text:
            return "self-signed TLS: add 'verify_tls: false' to this service"
        return ("host unreachable from THIS machine: check IP/port, that the container is running, "
                "and that the Hermes host can route to it (ping/curl from the Hermes container)")
    if status in (401, 403):
        miss = credentials_missing(svc)
        if miss:
            return f"credential not set in .env: {', '.join(miss)}"
        return "credential rejected: regenerate the token/API key and update .env"
    if status == 404:
        return "endpoint not found: check url / api_base for this service version"
    return ""


def cmd_where(_):
    print(f"registry : {registry_path()}  ({'exists' if os.path.exists(registry_path()) else 'MISSING'})")
    print("env files: " + (", ".join(env_files()) or "(none found)"))


def cmd_types(_):
    for t, (health, kind, note) in TYPES.items():
        print(f"{t:14} auth={kind:13} health={health:28} {note}")


def cmd_list(_):
    services = load_registry()
    for name, svc in services.items():
        miss = credentials_missing(svc)
        cred = "creds: missing " + ",".join(miss) if miss else "creds: ok"
        print(f"{name:16} {svc.get('type', 'generic'):14} {svc.get('url', '?'):40} {cred}")


def check_services(names=None):
    """Return list of (name, state, http_status, hint) for the selected services."""
    services = load_registry()
    selected = names or list(services)
    results = []
    for name in selected:
        svc = services.get(name)
        if not svc:
            results.append((name, "UNKNOWN", None, "not in registry"))
            continue
        health = svc.get("health") or TYPES.get(svc.get("type", "generic"), TYPES["generic"])[0]
        status, text, _ = request(svc, "GET", health, timeout=8)
        if status is not None and 200 <= status < 300:
            state = "OK"
        elif status in (401, 403):
            state = "AUTH"
        elif status is None:
            state = "DOWN"
        else:
            state = f"HTTP{status}"
        results.append((name, state, status, _hint(status, text, svc)))
    return results


def cmd_check(args):
    results = check_services(args or None)
    bad = 0
    for name, state, _, hint in results:
        mark = "✓" if state == "OK" else "✗"
        bad += state != "OK"
        print(f"{mark} {name:16} {state:8} {hint}")
    sys.exit(1 if bad else 0)


def _parse_call(args):
    if len(args) < 3:
        sys.exit("usage: homelab.py call <name> <METHOD> <path> [--data JSON|@file] [--query k=v] [--full] [--yes]")
    name, method, path, rest = args[0], args[1].upper(), args[2], args[3:]
    data, query, full, yes = None, {}, False, False
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--data":
            raw = rest[i + 1]
            raw = open(raw[1:], encoding="utf-8").read() if raw.startswith("@") else raw
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                sys.exit(f"error: --data is not valid JSON: {e}")
            i += 2
        elif a == "--query":
            k, _, v = rest[i + 1].partition("=")
            query[k] = v
            i += 2
        elif a == "--full":
            full, i = True, i + 1
        elif a == "--yes":
            yes, i = True, i + 1
        else:
            sys.exit(f"error: unknown option {a}")
    return name, method, path, data, query, full, yes


def cmd_call(args):
    name, method, path, data, query, full, yes = _parse_call(args)
    services = load_registry()
    svc = services.get(name) or sys.exit(f"error: service '{name}' not in registry ({', '.join(services)})")
    if method == "DELETE" and not yes:
        sys.exit("refused: DELETE needs --yes (ask the user ONCE, then re-run with --yes)")
    status, text, url = request(svc, method, path, data=data, query=query)
    print(f"{method} {url} -> {status if status is not None else 'NO CONNECTION'}")
    hint = _hint(status, text, svc) if status is None or status >= 400 else ""
    if hint:
        print(f"hint: {hint}")
    try:
        text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, ValueError):
        pass
    if not full and len(text) > 6000:
        text = text[:6000] + f"\n... ({len(text) - 6000} more chars, use --full or narrow the query)"
    print(text)
    sys.exit(0 if status is not None and status < 400 else 1)


def _port_open(ip, port, timeout=0.6):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def _fingerprint(base_url, stypes):
    """Pick the first candidate type whose fingerprint path exists (any status but 404)."""
    probe = {"url": base_url, "verify_tls": False}
    for t in stypes:
        status, _, _ = request(probe, "GET", FINGERPRINT.get(t, "/"), timeout=4)
        if status is not None and status != 404:
            return t
    return None


def _pve_ips(pve, node, kind, vmid):
    if kind == "lxc":
        status, text, _ = request(pve, "GET", f"/api2/json/nodes/{node}/lxc/{vmid}/interfaces", timeout=8)
        if status == 200:
            ips = []
            for iface in json.loads(text).get("data") or []:
                if iface.get("name") == "lo":
                    continue
                for key in ("inet", "ip-address"):
                    v = iface.get(key)
                    if v:
                        ips.append(v.split("/")[0])
            return ips
    else:
        status, text, _ = request(pve, "GET",
                                  f"/api2/json/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces", timeout=8)
        if status == 200:
            ips = []
            for iface in (json.loads(text).get("data") or {}).get("result") or []:
                if iface.get("name") == "lo":
                    continue
                for a in iface.get("ip-addresses") or []:
                    if a.get("ip-address-type") == "ipv4":
                        ips.append(a["ip-address"])
            return ips
    return []


def cmd_discover(args):
    services = load_registry() if os.path.exists(registry_path()) else {}
    pve_name = args[args.index("--proxmox") + 1] if "--proxmox" in args else next(
        (n for n, s in services.items() if s.get("type") == "proxmox"), None)
    if not pve_name or pve_name not in services:
        sys.exit("error: add a 'proxmox' service with url + token_env to the registry first")
    pve = services[pve_name]
    status, text, url = request(pve, "GET", "/api2/json/nodes")
    if status != 200:
        sys.exit(f"error: Proxmox API {url} -> {status}: {_hint(status, text, pve) or text[:200]}")
    found = {}
    known_urls = {str(s.get("url", "")).rstrip("/") for s in services.values()}
    for node in [n["node"] for n in json.loads(text)["data"]]:
        for kind in ("lxc", "qemu"):
            st, tx, _ = request(pve, "GET", f"/api2/json/nodes/{node}/{kind}")
            if st != 200:
                continue
            for g in json.loads(tx).get("data") or []:
                ips = _pve_ips(pve, node, kind, g["vmid"]) if g.get("status") == "running" else []
                print(f"{kind:4} {g['vmid']:>5} {g.get('name', '?'):24} {g.get('status', '?'):8} "
                      f"{', '.join(ips) or '-'}")
                for ip in ips:
                    for port, stypes in KNOWN_PORTS.items():
                        if not _port_open(ip, port):
                            continue
                        scheme = "https" if port in (9443,) else "http"
                        u = f"{scheme}://{ip}:{port}"
                        if u in known_urls:
                            continue
                        stype = _fingerprint(u, stypes)
                        if not stype:
                            continue
                        key = f"{stype}-{g.get('name', g['vmid'])}".lower().replace(" ", "-")
                        var = key.upper().replace("-", "_")
                        entry = {"type": stype, "url": u}
                        if stype in BASIC_AUTH_TYPES:
                            entry.update(user_env=f"{var}_USER", password_env=f"{var}_PASSWORD")
                        else:
                            entry["token_env"] = f"{var}_TOKEN"
                        if scheme == "https":
                            entry["verify_tls"] = False
                        found[key] = entry
    if not found:
        print("\nno new services found on known ports")
        return
    print("\n# suggested registry entries (fill the *_TOKEN values in .env):")
    print(yaml.safe_dump({"services": found}, sort_keys=False))
    if "--write" in args:
        path = registry_path()
        data = yaml.safe_load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
        data = data or {}
        data.setdefault("services", {})
        for k, v in found.items():
            data["services"].setdefault(k, v)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
        print(f"merged {len(found)} entries into {path}")


COMMANDS = {"where": cmd_where, "types": cmd_types, "list": cmd_list, "check": cmd_check,
            "call": cmd_call, "discover": cmd_discover}


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help") or argv[0] not in COMMANDS:
        print(__doc__)
        sys.exit(0 if argv and argv[0] in ("-h", "--help", "help") else 2)
    load_env()
    COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    main(sys.argv[1:])
