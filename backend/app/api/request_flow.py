"""Page → request → handler tracing, derived from source.

Answers the question the component map cannot: *when I open this page, what
requests fire, and where does each one actually go?*

Everything here is PARSED FROM THE REPO, never hand-written. A curated list of
"page X calls API Y" is wrong the first time someone adds a fetch, and wrong
silently — which is the failure mode this whole monitor exists to prevent. The
cost is that parsing must tolerate real code, so each scanner is deliberately
narrow and reports only what it can actually see.

Chain assembled per call site:

    page/component (.tsx:line)
        → apiService.<method>            (api.ts:line)
        → HTTP <VERB> <path>
        → nginx  /neuradex/backend → backend:8000
        → @router.<verb>                 (app/api/<mod>.py:line)
        → handler function
        → Kibana: real requests + responses for that exact path

The repo is mounted read-only at /repo (docker-compose.yml, backend service).
"""
from __future__ import annotations

import functools
import os
import re

REPO_ROOT = os.environ.get("REPO_ROOT", "/repo")
GITHUB_BASE = "https://github.com/AbhinavShah421/NeuradeX/blob/main"

_API_CLIENT = "frontend/src/services/api.ts"
_CALLER_DIRS = [
    ("page", "frontend/src/pages"),
    ("component", "frontend/src/components"),
    ("hook", "frontend/src/hooks"),
]
_BACKEND_API_DIR = "backend/app/api"
_BACKEND_MAIN = "backend/app/main.py"

# `this.api.get('/api/x')` and the backtick form `this.api.get(`/api/x/${id}`)`
_CALL_RE = re.compile(r"this\.api\.(get|post|put|delete|patch)\(\s*[`'\"]([^`'\"]+)[`'\"]")
# Prettier wraps long calls, putting the URL on a following line:
#     const response = await this.api.get(
#       `/api/predictions/${symbol}/history`,
_CALL_OPEN_RE = re.compile(r"this\.api\.(get|post|put|delete|patch)\(\s*$")
_PATH_ONLY_RE = re.compile(r"^\s*[`'\"]([^`'\"]+)[`'\"]")
_METHOD_RE = re.compile(r"^\s*(?:async\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")
_USES_RE = re.compile(r"apiService\.([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")
_ROUTE_RE = re.compile(r"^\s*@router\.(get|post|put|delete|patch)\(\s*[\"']([^\"']*)[\"']")
_APP_ROUTE_RE = re.compile(r"^\s*@app\.(get|post|put|delete|patch)\(\s*[\"']([^\"']*)[\"']")
_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")
_INCLUDE_RE = re.compile(r"include_router\(\s*([a-zA-Z_][a-zA-Z0-9_]*)\.router\s*,\s*prefix\s*=\s*[\"']([^\"']+)[\"']")
_ROUTE_RE_JSX = re.compile(r'<Route\s+path=[\"\']([^\"\']+)[\"\']\s+element=\{<([A-Za-z0-9_]+)')

# Nearest enclosing construct → what triggers the call. Approximate by design:
# a backward scan, not a parse. Labelled as derived so nobody reads it as gospel.
_TRIGGER_PATTERNS = [
    (re.compile(r"\buseEffect\s*\("), "on page load (useEffect)"),
    (re.compile(r"\bsetInterval\s*\("), "polling (setInterval)"),
    (re.compile(r"\buseCallback\s*\("), "callback"),
    (re.compile(r"const\s+(handle[A-Za-z0-9_]*)\s*=", ), "user action: {0}"),
    (re.compile(r"const\s+([a-zA-Z0-9_]+)\s*=\s*(?:async\s*)?\("), "function: {0}"),
    (re.compile(r"(?:async\s+)?function\s+([a-zA-Z0-9_]+)"), "function: {0}"),
]


def _read(rel: str) -> list[str]:
    try:
        with open(os.path.join(REPO_ROOT, rel), "r", encoding="utf-8", errors="replace") as fh:
            return fh.readlines()
    except Exception:
        return []


def _gh(rel: str, line: int | None = None) -> str:
    return f"{GITHUB_BASE}/{rel}" + (f"#L{line}" if line else "")


def _ref(rel: str, line: int | None) -> dict:
    return {"path": rel, "line": line, "label": f"{rel}:{line}" if line else rel,
            "github": _gh(rel, line)}


def _normalise(path: str) -> str:
    """`/api/x/${id}` and `/api/x/{id}` both become `/api/x/*` for matching.

    Query strings are dropped: the client writes them inline
    (`/api/orders/feedback/agent-accuracy?min_trades=${n}`) while the route is
    declared on the path alone, so keeping them meant those calls never matched.
    """
    p = path.split("?", 1)[0].split("#", 1)[0]
    p = re.sub(r"\$\{[^}]*\}", "*", p)
    p = re.sub(r"\{[^}]*\}", "*", p)
    return p.rstrip("/") or "/"


# ── scanners ─────────────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=1)
def scan_api_client() -> dict:
    """apiService method → {verb, path, line}. Reads the method name, then the
    first this.api.<verb> inside it."""
    lines = _read(_API_CLIENT)
    out: dict[str, dict] = {}
    current: str | None = None
    current_line = 0
    for i, raw in enumerate(lines, 1):
        m = _METHOD_RE.match(raw)
        if m and ("async " in raw or "): Promise" in raw):
            name = m.group(1)
            if name not in ("if", "for", "while", "switch", "catch", "constructor", "return"):
                current, current_line = name, i
        c = _CALL_RE.search(raw)
        if c and current and current not in out:
            out[current] = {"verb": c.group(1).upper(), "path": c.group(2),
                            "line": current_line, "call_line": i}
            continue
        # Wrapped call: verb on this line, URL on one of the next two.
        o = _CALL_OPEN_RE.search(raw)
        if o and current and current not in out:
            for k in range(i, min(i + 3, len(lines))):
                p = _PATH_ONLY_RE.match(lines[k])
                if p:
                    out[current] = {"verb": o.group(1).upper(), "path": p.group(1),
                                    "line": current_line, "call_line": k + 1}
                    break
    return out


@functools.lru_cache(maxsize=1)
def scan_routes() -> list[dict]:
    """Every backend route: full path, handler, file and line."""
    prefixes: dict[str, str] = {}
    for raw in _read(_BACKEND_MAIN):
        m = _INCLUDE_RE.search(raw)
        if m:
            prefixes[m.group(1)] = m.group(2)

    routes: list[dict] = []

    # Routes declared straight on the app object (e.g. "/" and "/health") never
    # go through include_router, so scanning only app/api/*.py missed them and
    # reported healthCheck as unroutable.
    main_lines = _read(_BACKEND_MAIN)
    for i, raw in enumerate(main_lines):
        m = _APP_ROUTE_RE.match(raw)
        if not m:
            continue
        verb, path = m.group(1).upper(), m.group(2)
        handler, hline = None, None
        for j in range(i + 1, min(i + 12, len(main_lines))):
            d = _DEF_RE.match(main_lines[j])
            if d:
                handler, hline = d.group(1), j + 1
                break
        routes.append({"verb": verb, "path": path, "norm": _normalise(path),
                       "handler": handler, "module": "main",
                       "ref": _ref(_BACKEND_MAIN, hline or (i + 1))})

    api_dir = os.path.join(REPO_ROOT, _BACKEND_API_DIR)
    try:
        files = sorted(f for f in os.listdir(api_dir) if f.endswith(".py"))
    except Exception:
        files = []
    for fname in files:
        mod = fname[:-3]
        prefix = prefixes.get(mod)
        if prefix is None:
            continue
        rel = f"{_BACKEND_API_DIR}/{fname}"
        lines = _read(rel)
        for i, raw in enumerate(lines):
            m = _ROUTE_RE.match(raw)
            if not m:
                continue
            verb, sub = m.group(1).upper(), m.group(2)
            handler, hline = None, None
            for j in range(i + 1, min(i + 12, len(lines))):
                d = _DEF_RE.match(lines[j])
                if d:
                    handler, hline = d.group(1), j + 1
                    break
            full = (prefix + sub).replace("//", "/")
            routes.append({"verb": verb, "path": full, "norm": _normalise(full),
                           "handler": handler, "module": mod,
                           "ref": _ref(rel, hline or (i + 1))})
    return routes


def _trigger_for(lines: list[str], idx: int) -> str:
    """Nearest enclosing construct above a call site."""
    for k in range(idx, max(idx - 40, -1), -1):
        for pat, label in _TRIGGER_PATTERNS:
            m = pat.search(lines[k])
            if m:
                return label.format(*m.groups()) if "{0}" in label else label
    return "unknown"


@functools.lru_cache(maxsize=1)
def scan_callers() -> dict:
    """Every page/component/hook → the apiService calls it makes.

    Two things this deliberately does NOT do any more:

    * It no longer uses os.listdir, which only sees the top of a directory.
      `components/` keeps 47 files across dashboard/, pattern-memory/ and
      portfolio/ subfolders, so a flat scan found 9 of the 22 that call the API.
    * It no longer drops files with zero calls. "This page requests nothing
      itself — its data arrives as props" is a real answer to *where does this
      screen get its data*, and silently omitting the file makes the catalogue
      look complete when it is not.

    Keys are slugs of the path (unique), because two components in different
    folders can share a basename.
    """
    out: dict[str, dict] = {}
    for kind, d in _CALLER_DIRS:
        root = os.path.join(REPO_ROOT, d)
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [x for x in dirnames if x not in ("node_modules", "dist", "__tests__")]
            for fname in sorted(filenames):
                if not fname.endswith((".tsx", ".ts")) or fname.endswith(".d.ts"):
                    continue
                abs_path = os.path.join(dirpath, fname)
                rel = os.path.relpath(abs_path, REPO_ROOT).replace(os.sep, "/")
                lines = _read(rel)
                calls = []
                for i, raw in enumerate(lines):
                    for m in _USES_RE.finditer(raw):
                        calls.append({"method": m.group(1), "line": i + 1,
                                      "trigger": _trigger_for(lines, i)})
                name = fname.rsplit(".", 1)[0]
                sub = os.path.relpath(dirpath, root).replace(os.sep, "/")
                key = name if sub in (".", "") else f"{sub}/{name}".replace("/", "__")
                out[key] = {
                    "key": key, "name": name, "kind": kind,
                    "folder": None if sub in (".", "") else f"{d}/{sub}",
                    "file": rel, "github": _gh(rel), "calls": calls,
                }
    return out


@functools.lru_cache(maxsize=1)
def scan_routes_map() -> dict:
    """Route lookup keyed by (VERB, normalised path)."""
    return {(r["verb"], r["norm"]): r for r in scan_routes()}


@functools.lru_cache(maxsize=1)
def scan_page_routes() -> dict:
    """Component name → the URL the SPA serves it at (from App.tsx)."""
    out: dict[str, str] = {}
    for raw in _read("frontend/src/App.tsx"):
        m = _ROUTE_RE_JSX.search(raw)
        if m:
            out.setdefault(m.group(2), m.group(1))
    return out


# ── assembly ─────────────────────────────────────────────────────────────────

def _kibana_for_path(path: str, dv: str | None) -> str | None:
    """Real requests + responses for one endpoint."""
    if not dv:
        return None
    from urllib.parse import quote
    literal = _normalise(path).rstrip("*").rstrip("/")
    q = f'path:"{literal}"'.replace("'", "''")
    cols = "log_type,http_method,path,status_code,duration_ms,request_id,response_body"
    g = "(time:(from:now-4h,to:now))"
    a = (f"(columns:!({cols}),index:'{dv}',"
         f"query:(language:kuery,query:'{q}'),sort:!(!('@timestamp',desc)))")
    return ("/neuradex/dev/logs/app/discover#/?_g=" + quote(g, safe="(),:-")
            + "&_a=" + quote(a, safe="(),:-!*"))


def trace_call(method: str, dv: str | None = None) -> dict:
    """Full chain for one apiService method."""
    client = scan_api_client().get(method)
    chain: list[dict] = []

    if not client:
        return {"method": method, "resolved": False,
                "note": "No this.api.<verb> found for this method in api.ts — "
                        "it may be a helper that builds a URL rather than calling one.",
                "chain": chain}

    chain.append({
        "stage": "API client", "detail": f"apiService.{method}()",
        "ref": _ref(_API_CLIENT, client["line"]),
    })
    chain.append({
        "stage": "HTTP request", "detail": f"{client['verb']} {client['path']}",
        "ref": _ref(_API_CLIENT, client["call_line"]),
    })
    chain.append({
        "stage": "Reverse proxy",
        "detail": "nginx /neuradex/backend/ → backend:8000",
        "ref": _ref("config/nginx.conf", None),
    })

    route = scan_routes_map().get((client["verb"], _normalise(client["path"])))
    if route:
        chain.append({
            "stage": "Route", "detail": f"@router.{route['verb'].lower()}(\"{route['path']}\")",
            "ref": route["ref"],
        })
        chain.append({
            "stage": "Handler", "detail": f"{route['handler']}()", "ref": route["ref"],
        })
    else:
        chain.append({
            "stage": "Route", "detail": "no matching backend route found — "
                                        "this endpoint may be served by another service or proxied",
            "ref": None,
        })

    return {
        "method": method, "resolved": True,
        "verb": client["verb"], "path": client["path"],
        "handler": route["handler"] if route else None,
        "module": route["module"] if route else None,
        "chain": chain,
        "kibana": _kibana_for_path(client["path"], dv),
    }


def list_pages() -> list[dict]:
    callers = scan_callers()
    routes = scan_page_routes()
    client = scan_api_client()
    out = []
    for key, info in sorted(callers.items()):
        methods = sorted({c["method"] for c in info["calls"]})
        on_load = sorted({c["method"] for c in info["calls"]
                          if "page load" in c["trigger"] or "polling" in c["trigger"]})
        # Carry each call's verb+path so the UI can filter by API path, not just
        # by method name — "which page hits /api/orders?" is the more common
        # question when tracing a request.
        apis = [
            {"method": m,
             "verb": (client.get(m) or {}).get("verb"),
             "path": (client.get(m) or {}).get("path")}
            for m in methods
        ]
        out.append({
            "key": key, "name": info["name"], "kind": info["kind"],
            "folder": info.get("folder"), "file": info["file"],
            "github": info["github"], "route": routes.get(info["name"]),
            "callCount": len(info["calls"]), "uniqueApis": len(methods),
            "onLoad": [m for m in on_load if m in client],
            "methods": methods, "apis": apis,
        })
    # Pages before components; within each, the ones that actually call the API
    # first — a long tail of zero-call presentational components should not bury
    # the screens you are trying to debug.
    out.sort(key=lambda p: (p["kind"] != "page", p["uniqueApis"] == 0, p["name"].lower()))
    return out


def page_detail(name: str, dv: str | None = None) -> dict | None:
    callers = scan_callers()
    info = callers.get(name)
    if not info:
        # Tolerate a basename when the key is folder-qualified.
        info = next((v for v in callers.values() if v["name"] == name), None)
    if not info:
        return None
    seen: dict[str, dict] = {}
    for c in info["calls"]:
        entry = seen.setdefault(c["method"], {"method": c["method"], "sites": [], "trace": None})
        entry["sites"].append({"trigger": c["trigger"], "ref": _ref(info["file"], c["line"])})
    for m, entry in seen.items():
        entry["trace"] = trace_call(m, dv)
    calls = sorted(seen.values(), key=lambda e: e["method"])
    return {
        "name": info["name"], "key": info["key"], "kind": info["kind"],
        "folder": info.get("folder"), "file": info["file"],
        "github": info["github"], "route": scan_page_routes().get(info["name"]),
        "calls": calls,
    }


def refresh() -> None:
    for fn in (scan_api_client, scan_routes, scan_callers, scan_routes_map, scan_page_routes):
        fn.cache_clear()
