---
name: homelab-services
description: Use the user's homelab services on Proxmox — Home Assistant, Jellyfin, CouchDB, Postiz, AdGuard Home, Proxmox itself, Portainer, Grafana, n8n, Immich and any other HTTP API — through one authenticated CLI. Use whenever a request mentions a homelab app, container, VM, smart-home device, media library, database or social post scheduling.
version: 1.0.0
author: jeaniro07
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [homelab, proxmox, home-assistant, jellyfin, couchdb, postiz, adguard, api]
    category: homelab
    requires_toolsets: [terminal]
    related_skills: [coding-delegate]
---

# Homelab Services

Every service is in one registry. **Do not ask the user for IPs, ports or
tokens and do not say "I can't access it" before running the commands below.**

```bash
H="python3 $SKILL_DIR/scripts/homelab.py"
$H list                 # what exists, and whether its credentials are set
$H check                # live health check of all services
$H call <service> GET <path> [--query k=v] [--data '{json}'] [--full]
```

`call` adds the right auth header for the service type automatically.

## Recipes

| Need | Command |
|---|---|
| HA: all entity states | `$H call homeassistant GET /api/states` |
| HA: one entity | `$H call homeassistant GET /api/states/light.ruang_tamu` |
| HA: turn on a light | `$H call homeassistant POST /api/services/light/turn_on --data '{"entity_id":"light.ruang_tamu"}'` |
| HA: run a script/scene | `$H call homeassistant POST /api/services/scene/turn_on --data '{"entity_id":"scene.malam"}'` |
| Jellyfin: server info | `$H call jellyfin GET /System/Info` |
| Jellyfin: search media | `$H call jellyfin GET /Items --query searchTerm=batman --query Recursive=true --query Limit=10` |
| Jellyfin: rescan library | `$H call jellyfin POST /Library/Refresh` |
| CouchDB: databases | `$H call couchdb GET /_all_dbs` |
| CouchDB: query docs | `$H call couchdb POST /<db>/_find --data '{"selector":{"type":"order"},"limit":20}'` |
| CouchDB: insert doc | `$H call couchdb POST /<db> --data '{"type":"note","text":"..."}'` |
| Postiz: channels | `$H call postiz GET {api_base}/integrations` |
| Postiz: posts | `$H call postiz GET {api_base}/posts --query startDate=2026-10-01T00:00:00Z --query endDate=2026-10-31T23:59:59Z` |
| AdGuard: status | `$H call adguard GET /control/status` |
| AdGuard: statistics | `$H call adguard GET /control/stats` |
| AdGuard: query log | `$H call adguard GET /control/querylog --query limit=50` |
| AdGuard: block a domain | `$H call adguard GET /control/filtering/status`, then `POST /control/filtering/set_rules` with the full rules list plus `\|\|domain.com^` |
| AdGuard: pause protection 10 min | `$H call adguard POST /control/protection --data '{"enabled":false,"duration":600000}'` |
| Proxmox: nodes | `$H call proxmox GET /api2/json/nodes` |
| Proxmox: containers | `$H call proxmox GET /api2/json/nodes/<node>/lxc` |
| Proxmox: start LXC | `$H call proxmox POST /api2/json/nodes/<node>/lxc/<vmid>/status/start` |
| New services | `$H discover` (lists Proxmox guests + detects known ports), `$H discover --write` |

API paths differ between app versions. If you get 404, look up the app's API
docs or `$H types`, adjust the path, and try again. Don't give up after one try.

## Rules

1. **Read requests (GET) never need confirmation.** Run them immediately.
2. **Changes (POST/PUT/PATCH):** if the user's message already asks for it
   ("nyalakan lampu", "start container 105"), just do it. Only ask once when
   the action is ambiguous or large (bulk changes, restarts of many services).
3. **DELETE** needs `--yes`. Ask the user ONCE; after any affirmative reply,
   re-run with `--yes` and do not ask again.
4. On failure, `homelab.py` prints a `hint:` line. Report the exact command, the
   status and the hint. `DOWN` means the Hermes host cannot reach that IP, and
   `AUTH` means the token in `.env` is missing or wrong. Don't loop on the same call.
5. Never print tokens. Never put secrets in the registry, only in `.env`.

## Setup (done once by hermes-doctor)

- Registry: `~/.hermes/homelab/services.yaml` (see `$H where`)
- Secrets: `~/.hermes/.env`, e.g. `HA_TOKEN=...`, `JELLYFIN_API_KEY=...`
