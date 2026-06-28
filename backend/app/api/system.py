"""System / Docker control surface.

Talks to the Docker Engine API over the mounted unix socket (no extra Python
package needed — httpx speaks to it over a UDS transport). Powers the floating
system-status panel: list every project container, show its logs, and
start/stop/restart it.

Security note: this mounts the Docker socket, which is root-equivalent on the
host. It is intentionally scoped to this project's containers (name prefix
`stock-prediction-`) and is meant for the operator's own local/personal use.
"""
from __future__ import annotations

import asyncio
import html
import re
from datetime import datetime

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from app.utils.elk_logger import get_logger

logger = get_logger(__name__)
router = APIRouter()

_DOCKER_SOCK = "/var/run/docker.sock"
# Only expose containers belonging to this project.
_PROJECT_PREFIX = "stock-prediction-"

# Friendly labels + Material icon per container (falls back to a generic icon).
_META: dict[str, dict] = {
    "stock-prediction-backend":         {"label": "Backend",          "icon": "dns"},
    "stock-prediction-frontend":        {"label": "Frontend",         "icon": "web"},
    "stock-prediction-session-runner":  {"label": "Session Runner",   "icon": "directions_run"},
    "stock-prediction-market-data":     {"label": "Market Data",      "icon": "candlestick_chart"},
    "stock-prediction-technical-agent": {"label": "Technical Agent",  "icon": "show_chart"},
    "stock-prediction-sentiment-agent": {"label": "Sentiment Agent",  "icon": "article"},
    "stock-prediction-macro-agent":     {"label": "Macro Agent",      "icon": "public"},
    "stock-prediction-pattern-agent":   {"label": "Pattern Agent",    "icon": "pattern"},
    "stock-prediction-rl-agent":        {"label": "RL Agent",         "icon": "smart_toy"},
    "stock-prediction-ensemble-engine": {"label": "Ensemble Engine",  "icon": "hub"},
    "stock-prediction-feedback-service":{"label": "Feedback Service", "icon": "feedback"},
    "stock-prediction-stock-scanner":   {"label": "Stock Scanner",    "icon": "radar"},
    "stock-prediction-autopilot":       {"label": "Autopilot",        "icon": "auto_mode"},
    "stock-prediction-groww-feed":      {"label": "Groww Feed",       "icon": "sensors"},
    "stock-prediction-sentiment":       {"label": "Sentiment Service","icon": "mood"},
    "stock-prediction-model-trainer":   {"label": "Model Trainer",    "icon": "model_training"},
    "stock-prediction-risk-engine":     {"label": "Risk Engine",      "icon": "security"},
    "stock-prediction-trade-executor":  {"label": "Trade Executor",   "icon": "currency_exchange"},
    "stock-prediction-postgres":        {"label": "PostgreSQL",       "icon": "storage"},
    "stock-prediction-mongodb":         {"label": "MongoDB",          "icon": "storage"},
    "stock-prediction-redis":           {"label": "Redis",            "icon": "memory"},
    "stock-prediction-rabbitmq":        {"label": "RabbitMQ",         "icon": "forum"},
    "stock-prediction-influxdb":        {"label": "InfluxDB",         "icon": "timeline"},
    "stock-prediction-elasticsearch":   {"label": "Elasticsearch",    "icon": "search"},
    "stock-prediction-kibana":          {"label": "Kibana",           "icon": "insights"},
    "stock-prediction-mlflow":          {"label": "MLflow",           "icon": "science"},
    "stock-prediction-docs":            {"label": "Docs",             "icon": "menu_book"},
    "stock-prediction-ngrok":           {"label": "ngrok",            "icon": "vpn_lock"},
    "stock-prediction-nginx":           {"label": "Nginx",            "icon": "router"},
}


def _client() -> httpx.AsyncClient:
    """An httpx client bound to the Docker Engine API over its unix socket."""
    transport = httpx.AsyncHTTPTransport(uds=_DOCKER_SOCK)
    return httpx.AsyncClient(transport=transport, base_url="http://docker", timeout=10.0)


def _strip_name(raw_names: list[str]) -> str:
    # Docker returns names like ["/stock-prediction-backend"].
    if not raw_names:
        return ""
    return raw_names[0].lstrip("/")


def _meta_for(name: str) -> dict:
    if name in _META:
        return _META[name]
    label = name.replace(_PROJECT_PREFIX, "").replace("-", " ").title()
    return {"label": label, "icon": "widgets"}


def _cpu_pct(stats: dict) -> float:
    """Compute CPU% the same way `docker stats` does, from one non-streaming
    sample (which carries precpu_stats for the delta)."""
    try:
        cpu = stats.get("cpu_stats", {})
        pre = stats.get("precpu_stats", {})
        cpu_total = cpu.get("cpu_usage", {}).get("total_usage", 0)
        pre_total = pre.get("cpu_usage", {}).get("total_usage", 0)
        sys_total = cpu.get("system_cpu_usage", 0)
        pre_sys   = pre.get("system_cpu_usage", 0)
        online    = cpu.get("online_cpus") or len(cpu.get("cpu_usage", {}).get("percpu_usage", []) or []) or 1
        cpu_delta = cpu_total - pre_total
        sys_delta = sys_total - pre_sys
        if sys_delta > 0 and cpu_delta >= 0:
            return round((cpu_delta / sys_delta) * online * 100.0, 1)
    except Exception:
        pass
    return 0.0


def _mem_used_mb(stats: dict) -> float:
    """Memory used in MB, cache-adjusted to match `docker stats`."""
    try:
        mem = stats.get("memory_stats", {})
        usage = mem.get("usage", 0) or 0
        detail = mem.get("stats", {}) or {}
        cache = detail.get("inactive_file", detail.get("cache", 0)) or 0
        used = max(0, usage - cache)
        return round(used / (1024 * 1024), 1)
    except Exception:
        return 0.0


async def _stats_for(client: httpx.AsyncClient, name: str) -> dict:
    """One non-streaming stats sample → {cpu_pct, mem_used_mb}."""
    try:
        r = await client.get(f"/containers/{name}/stats", params={"stream": "false"}, timeout=8.0)
        r.raise_for_status()
        s = r.json()
        return {"cpu_pct": _cpu_pct(s), "mem_used_mb": _mem_used_mb(s)}
    except Exception:
        return {"cpu_pct": None, "mem_used_mb": None}


# Severity scan over the recent log tail. Uppercase word-boundary tokens (real log
# levels are uppercase, so this avoids matching prose like "no errors") plus the
# structured level keys (case-insensitive) for JSON-formatted logs.
_ERR_RE   = re.compile(r'\b(ERROR|CRITICAL|FATAL)\b')
_ERR_KEY  = re.compile(r'"(?:log\.level|levelname|level)"\s*:\s*"(?:error|critical|fatal)"', re.I)
_WARN_RE  = re.compile(r'\b(WARNING|WARN)\b')
_WARN_KEY = re.compile(r'"(?:log\.level|levelname|level)"\s*:\s*"(?:warn|warning)"', re.I)


def _scan_severity(text: str) -> str:
    """'error' if the recent logs contain any error/critical, else 'warning' if any
    warning, else 'ok'."""
    if _ERR_RE.search(text) or _ERR_KEY.search(text):
        return "error"
    if _WARN_RE.search(text) or _WARN_KEY.search(text):
        return "warning"
    return "ok"


async def _severity_for(client: httpx.AsyncClient, name: str, tail: int = 200) -> str:
    """Fetch the last `tail` log lines and classify their worst severity."""
    try:
        r = await client.get(
            f"/containers/{name}/logs",
            params={"stdout": 1, "stderr": 1, "tail": tail, "timestamps": 0},
            timeout=6.0,
        )
        r.raise_for_status()
        return _scan_severity(_demux(r.content))
    except Exception:
        return "ok"


def _demux(data: bytes) -> str:
    """Docker multiplexes stdout/stderr with an 8-byte frame header on non-TTY
    containers. Decode the frames; fall back to raw text for TTY streams."""
    out: list[str] = []
    i, n = 0, len(data)
    while i + 8 <= n:
        stream_type = data[i]
        if stream_type not in (0, 1, 2):
            # Not framed — this is a raw (TTY) stream.
            return data.decode("utf-8", "replace")
        size = int.from_bytes(data[i + 4:i + 8], "big")
        i += 8
        out.append(data[i:i + size].decode("utf-8", "replace"))
        i += size
    if i < n:
        out.append(data[i:].decode("utf-8", "replace"))
    return "".join(out)


_svc_cache: dict = {"data": None, "ts": 0.0}
_SVC_TTL = 6.0   # smooth out overlapping polls without showing stale data for long


async def _compute_services(stats: bool = True) -> dict:
    """Build the full snapshot: list project containers + (optionally) enrich each
    with live CPU/mem and recent-log severity, all in one concurrent pass. This is
    the heavy part (~6s for ~29 containers) — callers serve it via a cache."""
    async with _client() as c:
        r = await c.get("/containers/json", params={"all": 1})
        r.raise_for_status()
        containers = r.json()

        services = []
        for ct in containers:
            name = _strip_name(ct.get("Names", []))
            if not name.startswith(_PROJECT_PREFIX):
                continue
            state = ct.get("State", "unknown")    # running | exited | restarting | paused | created
            status_text = ct.get("Status", "")    # e.g. "Up 7 minutes (healthy)"
            health = None
            if "(healthy)" in status_text:
                health = "healthy"
            elif "(unhealthy)" in status_text:
                health = "unhealthy"
            elif "(health: starting)" in status_text:
                health = "starting"
            meta = _meta_for(name)
            services.append({
                "name":   name,
                "label":  meta["label"],
                "icon":   meta["icon"],
                "state":  state,
                "status": status_text,
                "health": health,
                "image":  ct.get("Image", ""),
                "running": state == "running",
                "cpu_pct": None,
                "mem_used_mb": None,
                "log_severity": "ok",
            })

        if stats:
            async def _enrich(svc: dict):
                tasks = [_severity_for(c, svc["name"])]
                if svc["running"]:
                    tasks.append(_stats_for(c, svc["name"]))
                res = await asyncio.gather(*tasks, return_exceptions=True)
                sev = res[0]
                svc["log_severity"] = sev if isinstance(sev, str) else "ok"
                if svc["running"] and len(res) > 1 and isinstance(res[1], dict):
                    svc["cpu_pct"] = res[1]["cpu_pct"]
                    svc["mem_used_mb"] = res[1]["mem_used_mb"]

            await asyncio.gather(*[_enrich(s) for s in services], return_exceptions=True)

    services.sort(key=lambda s: (s["running"], s["label"].lower()))
    totals = {
        "running":     sum(1 for s in services if s["running"]),
        "count":       len(services),
        "cpu_pct":     round(sum((s["cpu_pct"] or 0) for s in services), 1),
        "mem_used_mb": round(sum((s["mem_used_mb"] or 0) for s in services), 1),
    }
    return {"status": "success", "data": services, "totals": totals}


_svc_lock = asyncio.Lock()
_refresh_task: asyncio.Task | None = None


async def _revalidate():
    """Recompute the snapshot in the background and refresh the cache."""
    global _refresh_task
    import time as _t
    try:
        async with _svc_lock:
            snap = await _compute_services(stats=True)
            _svc_cache["data"] = snap
            _svc_cache["ts"] = _t.time()
    except Exception as exc:
        logger.warning("services revalidate failed: %s", exc)
    finally:
        _refresh_task = None


@router.get("/services")
async def list_services(stats: bool = True, fresh: bool = False):
    """Project containers + live CPU/mem + recent-log severity for the system panel.

    Stale-while-revalidate: once warm, this returns the cached snapshot instantly
    and refreshes in the background, so the slow (~6s) Docker stats sweep never
    blocks the UI. `fresh=true` (manual Refresh) forces a synchronous recompute.
    """
    global _refresh_task
    import time as _t

    if not stats:
        return await _compute_services(stats=False)

    have = _svc_cache["data"] is not None
    age = _t.time() - _svc_cache["ts"]

    # No cache yet, or an explicit fresh request → compute now (bounded; serialized).
    if fresh or not have:
        async with _svc_lock:
            if not fresh and _svc_cache["data"] is not None and (_t.time() - _svc_cache["ts"]) < _SVC_TTL:
                return _svc_cache["data"]
            snap = await _compute_services(stats=True)
            _svc_cache["data"] = snap
            _svc_cache["ts"] = _t.time()
            return snap

    # Warm cache → serve instantly; kick a background refresh if it's getting old.
    if age > _SVC_TTL and _refresh_task is None:
        _refresh_task = asyncio.create_task(_revalidate())
    return _svc_cache["data"]


@router.post("/services/{name}/{action}")
async def control_service(name: str, action: str):
    """Start / stop / restart a project container."""
    if not name.startswith(_PROJECT_PREFIX):
        raise HTTPException(403, "Only project containers can be controlled.")
    if action not in ("start", "stop", "restart"):
        raise HTTPException(400, f"Unsupported action '{action}'.")
    # Don't let the UI stop the very container serving the request.
    if name == "stock-prediction-backend" and action in ("stop", "restart"):
        raise HTTPException(400, "Refusing to stop/restart the backend from itself.")
    try:
        async with _client() as c:
            r = await c.post(f"/containers/{name}/{action}", timeout=30.0)
            if r.status_code not in (204, 304):
                raise HTTPException(r.status_code, f"Docker: {r.text}")
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("docker %s %s failed: %s", action, name, exc)
        raise HTTPException(503, f"Docker action failed: {exc}")
    return {"status": "success", "data": {"name": name, "action": action}}


@router.get("/candles/coverage")
async def candles_coverage():
    """What our own 1-second tick dataset holds — per symbol/day tick counts +
    size, plus an aggregate summary. Powers the dataset panel."""
    from app.data.candle_store import coverage, coverage_summary
    return {"status": "success", "data": coverage(), "summary": coverage_summary()}


@router.post("/candles/enrich-volume")
async def candles_enrich_volume(symbol: str, date: str):
    """Backfill real per-minute volume for a captured day from the Groww 1-min
    historical API (the live stream is price-only)."""
    from app.data.candle_capture import enrich_volume
    n = await enrich_volume(symbol, date)
    return {"status": "success", "data": {"symbol": symbol, "date": date, "minutes_stored": n}}


@router.post("/restart-all")
async def restart_all():
    """Restart every running project container except the backend itself (which
    can't restart from within its own request)."""
    try:
        async with _client() as c:
            r = await c.get("/containers/json", params={"all": 0})  # running only
            r.raise_for_status()
            names = [
                _strip_name(ct.get("Names", []))
                for ct in r.json()
                if _strip_name(ct.get("Names", [])).startswith(_PROJECT_PREFIX)
            ]
            names = [n for n in names if n != "stock-prediction-backend"]

            async def _restart(n: str):
                try:
                    rr = await c.post(f"/containers/{n}/restart", timeout=40.0)
                    return n, rr.status_code in (204, 304)
                except Exception:
                    return n, False

            results = await asyncio.gather(*[_restart(n) for n in names])
    except Exception as exc:
        logger.warning("restart-all failed: %s", exc)
        raise HTTPException(503, f"Docker action failed: {exc}")

    ok = [n for n, good in results if good]
    failed = [n for n, good in results if not good]
    return {"status": "success", "data": {"restarted": ok, "failed": failed,
                                          "skipped": ["stock-prediction-backend"]}}


@router.delete("/services/{name}/logs")
async def clear_logs(name: str):
    """Truncate a container's json-log file(s) to zero bytes. Docker keeps
    appending afterwards, so this is a non-disruptive 'clear' — once empty, the
    severity scan finds nothing and the panel's logs icon goes green."""
    if not name.startswith(_PROJECT_PREFIX):
        raise HTTPException(403, "Only project containers can be cleared.")
    try:
        async with _client() as c:
            r = await c.get(f"/containers/{name}/json")
            r.raise_for_status()
            logpath = r.json().get("LogPath")
    except Exception as exc:
        raise HTTPException(503, f"Docker inspect failed: {exc}")
    if not logpath:
        raise HTTPException(404, "No log path for this container (non json-file driver?).")

    import glob
    import os
    targets = [logpath] + glob.glob(logpath + ".*")   # include rotated segments
    cleared, errors = 0, []
    for f in targets:
        try:
            os.truncate(f, 0)
            cleared += 1
        except FileNotFoundError:
            pass
        except PermissionError as exc:
            errors.append(f"permission denied: {exc}")
        except Exception as exc:
            errors.append(str(exc))
    if cleared == 0 and errors:
        raise HTTPException(500, f"Could not clear logs: {'; '.join(errors)}")
    return {"status": "success", "data": {"name": name, "files_cleared": cleared, "errors": errors}}


@router.get("/services/{name}/logs", response_class=HTMLResponse)
async def service_logs(name: str, tail: int = 500):
    """Return the last `tail` log lines as a styled terminal-style HTML page,
    meant to be opened in a new browser tab."""
    if not name.startswith(_PROJECT_PREFIX):
        raise HTTPException(403, "Only project containers can be inspected.")
    tail = max(50, min(tail, 5000))
    label = _meta_for(name)["label"]
    try:
        async with _client() as c:
            r = await c.get(
                f"/containers/{name}/logs",
                params={"stdout": 1, "stderr": 1, "tail": tail, "timestamps": 0},
            )
            r.raise_for_status()
            text = _demux(r.content)
    except Exception as exc:
        text = f"[failed to fetch logs: {exc}]"

    escaped = html.escape(text) or "[no log output]"
    fetched = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    page = (
        _LOGS_PAGE
        .replace("__LABEL__", html.escape(label))
        .replace("__NAME__", html.escape(name))
        .replace("__TAIL__", str(tail))
        .replace("__FETCHED__", fetched)
        .replace("__LOGS__", escaped)
    )
    return HTMLResponse(content=page)


# Interactive log viewer. Logs are embedded once (hidden) and filtered entirely
# client-side: level chips, free-text search, from/to time range, and clear.
# Token-replace template (not an f-string) so the JS braces stay literal.
_LOGS_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>__LABEL__ — logs</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; background: #0b0f14; color: #d7dde5;
         font: 13px/1.55 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
  header { position: sticky; top: 0; z-index: 5; background: #11161d;
           border-bottom: 1px solid #1e2630; }
  .row1 { display: flex; align-items: center; gap: 12px; padding: 10px 16px; }
  .row2 { display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
          padding: 0 16px 10px; }
  h1 { margin: 0; font-size: 14px; font-weight: 700; color: #fff; }
  .sub { color: #7c8896; font-size: 11px; }
  .meta { margin-left: auto; display: flex; align-items: center; gap: 8px;
          font-size: 11px; color: #7c8896; }
  button, input { font: inherit; }
  button { font-size: 12px; cursor: pointer; border-radius: 7px;
           border: 1px solid #2a3340; background: #1a212b; color: #d7dde5; padding: 6px 11px; }
  button:hover { background: #232c38; }
  input[type=text] { background: #0b0f14; border: 1px solid #2a3340; color: #d7dde5;
           border-radius: 7px; padding: 6px 10px; min-width: 220px; }
  input[type=datetime-local] { background: #0b0f14; border: 1px solid #2a3340;
           color: #d7dde5; border-radius: 7px; padding: 5px 8px; font-size: 11px; }
  select { background: #0b0f14; border: 1px solid #2a3340; color: #d7dde5;
           border-radius: 7px; padding: 5px 8px; font-size: 11px; cursor: pointer; }
  #ar.live { border-color: #00b386; color: #5fe0b8; }
  #del { border-color: #5a2330; background: #2a151a; color: #ff8a8a; }
  #del:hover { background: #3a1a20; }
  .chip { border-radius: 999px; padding: 5px 12px; font-size: 11px; font-weight: 600;
          border: 1px solid #2a3340; background: #161c25; color: #8b97a6; user-select: none; }
  .chip.on.err   { background: rgba(239,68,68,0.18);  border-color: #ef4444; color: #ff8a8a; }
  .chip.on.warn  { background: rgba(245,158,11,0.18); border-color: #f59e0b; color: #ffce7a; }
  .chip.on.info  { background: rgba(59,130,246,0.16); border-color: #3b82f6; color: #93c0ff; }
  .chip.on.trace { background: rgba(148,163,184,0.18);border-color: #94a3b8; color: #cbd5e1; }
  label.lbl { font-size: 11px; color: #7c8896; }
  #out { margin: 0; padding: 10px 16px 60px; }
  .line { white-space: pre-wrap; word-break: break-word; padding: 1px 0;
          border-left: 2px solid transparent; padding-left: 8px; }
  .line.err   { color: #ff9a9a; border-left-color: #ef4444; background: rgba(239,68,68,0.05); }
  .line.warn  { color: #ffd28a; border-left-color: #f59e0b; }
  .line.info  { color: #cdd6e2; }
  .line.trace { color: #7c8896; }
  .line.other { color: #aab4c0; }
  mark { background: #f5d90033; color: #ffe680; border-radius: 2px; }
  .count { font-size: 11px; color: #7c8896; }
  .empty { padding: 24px 16px; color: #7c8896; font-style: italic; }
</style></head>
<body>
  <header>
    <div class="row1">
      <h1>__LABEL__ <span style="color:#3a8;">logs</span></h1>
      <span class="sub">__NAME__ · last __TAIL__ lines</span>
      <div class="meta">
        <span class="count" id="count"></span>
        <span>fetched __FETCHED__</span>
        <button onclick="location.reload()">↻ Refresh</button>
        <button onclick="scrollTo(0, document.body.scrollHeight)">↓ Bottom</button>
      </div>
    </div>
    <div class="row2">
      <input id="q" type="text" placeholder="Search… (substring, case-insensitive)" />
      <span class="chip on err"   data-lvl="err"   onclick="toggle(this)">Error</span>
      <span class="chip on warn"  data-lvl="warn"  onclick="toggle(this)">Warning</span>
      <span class="chip on info"  data-lvl="info"  onclick="toggle(this)">Info</span>
      <span class="chip on trace" data-lvl="trace" onclick="toggle(this)">Trace/Debug</span>
      <label class="lbl">From <input id="from" type="datetime-local" step="1" /></label>
      <label class="lbl">To <input id="to" type="datetime-local" step="1" /></label>
      <label class="lbl">Lines
        <select id="tail" onchange="changeTail(this.value)">
          <option value="200">200</option>
          <option value="500">500</option>
          <option value="1000">1000</option>
          <option value="2000">2000</option>
          <option value="5000">5000</option>
        </select>
      </label>
      <label class="lbl">Auto
        <select id="ar" onchange="setAuto(this.value)">
          <option value="0">Off</option>
          <option value="5">5s</option>
          <option value="15">15s</option>
          <option value="30">30s</option>
          <option value="60">60s</option>
        </select>
      </label>
      <button onclick="clearView()">Clear view</button>
      <button onclick="resetFilters()">Reset</button>
      <button id="del" onclick="deleteLogs()" title="Permanently truncate this container's log file">🗑 Delete logs</button>
    </div>
  </header>
  <pre id="raw" hidden>__LOGS__</pre>
  <div id="out"></div>
<script>
(function () {
  var raw = document.getElementById('raw').textContent || '';
  var lines = raw.split('\\n').filter(function (l) { return l.length > 0; });

  function levelOf(line) {
    var m = line.match(/"(?:log\\.level|levelname|level)"\\s*:\\s*"([^"]+)"/i);
    if (!m) m = line.match(/\\b(ERROR|CRITICAL|FATAL|WARNING|WARN|INFO|DEBUG|TRACE)\\b/);
    if (!m) return 'other';
    var l = m[1].toUpperCase();
    if (l === 'CRITICAL' || l === 'FATAL' || l === 'ERROR') return 'err';
    if (l === 'WARNING' || l === 'WARN') return 'warn';
    if (l === 'INFO') return 'info';
    if (l === 'DEBUG' || l === 'TRACE') return 'trace';
    return 'other';
  }
  function tsOf(line) {
    var m = line.match(/(\\d{4}-\\d{2}-\\d{2})[T ](\\d{2}:\\d{2}:\\d{2})/);
    return m ? new Date(m[1] + 'T' + m[2]) : null;
  }

  var parsed = lines.map(function (l) { return { raw: l, lvl: levelOf(l), ts: tsOf(l) }; });

  var out = document.getElementById('out');
  var qEl = document.getElementById('q');
  var fromEl = document.getElementById('from');
  var toEl = document.getElementById('to');
  var tailEl = document.getElementById('tail');
  var arEl = document.getElementById('ar');
  var countEl = document.getElementById('count');
  var cleared = false;

  // ── Persistence (per container) so auto-refresh reloads keep your filters ──
  var SKEY = 'nd-logs:__NAME__';
  function saveState() {
    try {
      localStorage.setItem(SKEY, JSON.stringify({
        q: qEl.value,
        levels: activeLevels(),
        ar: arEl.value,
      }));
    } catch (e) {}
  }
  function loadState() {
    try {
      var s = JSON.parse(localStorage.getItem(SKEY) || '{}');
      if (typeof s.q === 'string') qEl.value = s.q;
      if (s.levels) document.querySelectorAll('.chip').forEach(function (c) {
        c.classList.toggle('on', s.levels[c.dataset.lvl] !== false);
      });
      if (s.ar) arEl.value = s.ar;
    } catch (e) {}
  }

  // ── Tail size (server-side) — reload with a new ?tail= ──
  window.changeTail = function (n) {
    var u = new URL(location.href);
    u.searchParams.set('tail', n);
    location.href = u.toString();
  };

  // ── Auto-refresh — persisted; reloads the whole page on the chosen interval ─
  var arTimer = null;
  function applyAuto() {
    if (arTimer) { clearTimeout(arTimer); arTimer = null; }
    var secs = parseInt(arEl.value, 10) || 0;
    arEl.classList.toggle('live', secs > 0);
    if (secs > 0) arTimer = setTimeout(function () { location.reload(); }, secs * 1000);
  }
  window.setAuto = function () { saveState(); applyAuto(); };

  function activeLevels() {
    var s = {};
    document.querySelectorAll('.chip').forEach(function (c) {
      s[c.dataset.lvl] = c.classList.contains('on');
    });
    return s;
  }
  function render() {
    if (cleared) { out.innerHTML = '<div class="empty">Cleared — Refresh or Reset to reload.</div>'; countEl.textContent = ''; return; }
    var q = qEl.value.trim().toLowerCase();
    var lv = activeLevels();
    var from = fromEl.value ? new Date(fromEl.value) : null;
    var to = toEl.value ? new Date(toEl.value) : null;
    var frag = document.createDocumentFragment();
    var shown = 0;
    parsed.forEach(function (p) {
      // 'other' lines (no detectable level) ride along with Info so stack traces stay visible.
      var grp = p.lvl === 'other' ? 'info' : p.lvl;
      if (!lv[grp]) return;
      if (q && p.raw.toLowerCase().indexOf(q) === -1) return;
      if (from && p.ts && p.ts < from) return;
      if (to && p.ts && p.ts > to) return;
      var div = document.createElement('div');
      div.className = 'line ' + p.lvl;
      if (q) {
        var idx = p.raw.toLowerCase().indexOf(q);
        div.appendChild(document.createTextNode(p.raw.slice(0, idx)));
        var mk = document.createElement('mark');
        mk.textContent = p.raw.slice(idx, idx + q.length);
        div.appendChild(mk);
        div.appendChild(document.createTextNode(p.raw.slice(idx + q.length)));
      } else {
        div.textContent = p.raw;
      }
      frag.appendChild(div);
      shown++;
    });
    out.innerHTML = '';
    if (shown === 0) out.innerHTML = '<div class="empty">No lines match the current filters.</div>';
    else out.appendChild(frag);
    countEl.textContent = shown + ' / ' + parsed.length + ' lines';
  }

  window.toggle = function (el) { el.classList.toggle('on'); saveState(); render(); };
  window.clearView = function () { cleared = true; render(); };
  window.deleteLogs = function () {
    if (!confirm('Permanently delete this container\\'s logs? The file is truncated to empty; Docker keeps logging new lines afterwards.')) return;
    var btn = document.getElementById('del');
    btn.disabled = true; btn.textContent = 'Deleting…';
    // DELETE the logs resource (current path, minus any query string).
    fetch(location.pathname, { method: 'DELETE' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function () { location.href = location.pathname; })   // reload empty
      .catch(function (e) { btn.disabled = false; btn.textContent = '🗑 Delete logs'; alert('Delete failed: ' + e.message); });
  };
  window.resetFilters = function () {
    cleared = false; qEl.value = ''; fromEl.value = ''; toEl.value = '';
    document.querySelectorAll('.chip').forEach(function (c) { c.classList.add('on'); });
    saveState(); render(); scrollTo(0, document.body.scrollHeight);
  };
  qEl.addEventListener('input', function () { saveState(); render(); });
  fromEl.addEventListener('change', render);
  toEl.addEventListener('change', render);

  // ── Init ──
  tailEl.value = String(__TAIL__);
  loadState();
  applyAuto();
  render();
  scrollTo(0, document.body.scrollHeight);
})();
</script>
</body></html>"""
