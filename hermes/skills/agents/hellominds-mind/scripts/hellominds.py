#!/usr/bin/env python3
"""Minimal CLI client for the HelloMinds Messaging API (stdlib only).

Lets Hermes talk to a Mind built on build.hellominds.ai. Auth uses the
HelloMinds Builder Access Key sent as the X-Access-Key header.

Environment:
  HELLOMINDS_ACCESS_KEY   Builder Access Key (required)
  HELLOMINDS_API_BASE     API base URL from the HelloMinds builder docs (required)
  HELLOMINDS_PATH_*       Optional per-endpoint path overrides, see PATHS below.

The default endpoint paths below are placeholders named after the documented
operations (CreateConversation, SendMessage, ...). Check them against the
Builder docs and override with HELLOMINDS_PATH_<OP> if they differ.

Usage:
  hellominds.py list
  hellominds.py create <mind_id>
  hellominds.py get <alias>
  hellominds.py history <alias> [--page N]
  hellominds.py send <alias> <message...>
  hellominds.py events
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

PATHS = {
    "LIST": "/conversations",
    "CREATE": "/conversations",
    "GET": "/conversations/{alias}",
    "HISTORY": "/conversations/{alias}/messages",
    "SEND": "/conversations/{alias}/messages",
    "EVENTS": "/events",
}


def _path(op, **kw):
    template = os.environ.get(f"HELLOMINDS_PATH_{op}", PATHS[op])
    return template.format(**{k: urllib.parse.quote(str(v), safe="") for k, v in kw.items()})


def _config():
    key = os.environ.get("HELLOMINDS_ACCESS_KEY")
    base = os.environ.get("HELLOMINDS_API_BASE")
    missing = [n for n, v in (("HELLOMINDS_ACCESS_KEY", key), ("HELLOMINDS_API_BASE", base)) if not v]
    if missing:
        sys.exit(f"error: set {', '.join(missing)} (see ~/.hermes/.env)")
    return base.rstrip("/"), key


def _request(method, path, body=None, query=None):
    base, key = _config()
    url = base + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-Access-Key", key)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
    except urllib.error.HTTPError as e:
        sys.exit(f"error: HTTP {e.code} {method} {url}\n{e.read().decode(errors='replace')}")
    except urllib.error.URLError as e:
        sys.exit(f"error: {method} {url}: {e.reason}")
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {"raw": raw}


def _stream_events():
    base, key = _config()
    req = urllib.request.Request(base + _path("EVENTS"))
    req.add_header("X-Access-Key", key)
    req.add_header("Accept", "text/event-stream")
    with urllib.request.urlopen(req) as resp:
        for line in resp:
            line = line.decode().rstrip("\n")
            if line.startswith("data:"):
                print(line[5:].strip(), flush=True)


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return
    cmd, args = argv[0], argv[1:]
    if cmd == "list":
        out = _request("GET", _path("LIST"))
    elif cmd == "create" and len(args) == 1:
        out = _request("POST", _path("CREATE"), {"mindId": args[0]})
    elif cmd == "get" and len(args) == 1:
        out = _request("GET", _path("GET", alias=args[0]))
    elif cmd == "history" and args:
        query = {}
        if "--page" in args:
            query["page"] = args[args.index("--page") + 1]
        out = _request("GET", _path("HISTORY", alias=args[0]), query=query)
    elif cmd == "send" and len(args) >= 2:
        out = _request("POST", _path("SEND", alias=args[0]), {"content": " ".join(args[1:])})
    elif cmd == "events":
        _stream_events()
        return
    else:
        sys.exit(__doc__)
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1:])
