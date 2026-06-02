"""Unified mission-control FastAPI app.

Shows every bot on this host (daytrade + nighttrade + anything that
matches the configured patterns), their live process state, recent log
activity, and per-bot key metrics from their observatory DBs.

Read-only. Cannot place orders, cannot kill processes, cannot edit
DBs. Safe to expose on Tailscale.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from . import activity as _activity
from . import ram_history as _ram


# ---- Bot registry --------------------------------------------------------


@dataclass(frozen=True)
class Bot:
    """A bot the mission-control dashboard knows how to inspect."""

    name: str
    project_root: Path                 # repo root for that bot
    process_match: List[str]           # substrings; any match = "this is the bot"
    log_path: Optional[Path] = None    # main log file, if any
    db_path: Optional[Path] = None     # observatory DB, if any
    dashboard_url: Optional[str] = None  # if the bot has its own dashboard
    notes: str = ""


def default_bots() -> List[Bot]:
    """The two bots known to live on this host."""
    home = Path("/Users/nedimvejo")
    dt_root = home / "Desktop" / "coding" / "daytrade"
    nt_root = home / "Desktop" / "coding" / "nighttrade"
    return [
        Bot(
            name="daytrade",
            project_root=dt_root,
            process_match=["daytrade learn", "daytrade observe"],
            log_path=dt_root / "logs" / "daytrade.log",
            db_path=dt_root / "artifacts" / "observatory.db",
            dashboard_url="http://100.127.143.106:8000",
            notes="paper-trading learning observatory",
        ),
        Bot(
            name="nighttrade",
            project_root=nt_root,
            process_match=["nighttrade observe"],
            log_path=nt_root / "logs" / "nighttrade.log",
            db_path=nt_root / "artifacts" / "observatory.db",
            dashboard_url="http://100.127.143.106:8001",
            notes="continuous market-safety observer",
        ),
    ]


# ---- System probes (no extra deps) ---------------------------------------


def list_processes() -> List[Dict[str, Any]]:
    """Snapshot of running Python processes that look like bots.

    Uses macOS ``ps``; no external Python deps. Returns dicts shaped as
    {pid, ppid, pmem_pct, pcpu_pct, rss_mb, etime, command}.
    """
    try:
        out = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid=,%mem=,%cpu=,rss=,etime=,command="],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    rows: List[Dict[str, Any]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 6)
        if len(parts) < 7:
            continue
        pid, ppid, pmem, pcpu, rss_kb, etime, command = parts
        if "python" not in command.lower():
            continue
        rows.append({
            "pid": int(pid), "ppid": int(ppid),
            "pmem_pct": float(pmem), "pcpu_pct": float(pcpu),
            "rss_mb": round(float(rss_kb) / 1024.0, 1),
            "etime": etime, "command": command,
        })
    return rows


def find_bot_processes(bot: Bot, snapshot: List[Dict[str, Any]]
                       ) -> List[Dict[str, Any]]:
    matched: List[Dict[str, Any]] = []
    for p in snapshot:
        cmd = p["command"]
        if any(needle in cmd for needle in bot.process_match):
            matched.append(p)
    return matched


def tail_file(path: Optional[Path], lines: int = 30) -> List[str]:
    if not path or not path.exists():
        return []
    try:
        out = subprocess.run(
            ["tail", "-n", str(lines), str(path)],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    return out.splitlines()


def db_summary(db_path: Optional[Path]) -> Dict[str, Any]:
    """Pull the same 'is the bot OK' signal both dashboards use."""
    if not db_path or not db_path.exists():
        return {"available": False}
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True,
                              timeout=2)
        con.row_factory = sqlite3.Row
        run = con.execute(
            "SELECT id, pid, status, started_ts, stopped_ts, "
            "last_heartbeat_ts, cycles FROM bot_runs "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        closed = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(pnl), 0) FROM paper_trades "
            "WHERE status='closed'"
        ).fetchone()
        open_n = con.execute(
            "SELECT COUNT(*) FROM paper_trades WHERE status='open'"
        ).fetchone()
        errors_24h = con.execute(
            "SELECT COUNT(*) FROM errors WHERE ts >= "
            "datetime('now', '-1 day')"
        ).fetchone()
        con.close()
    except sqlite3.Error as exc:
        return {"available": True, "db_error": str(exc)}

    out: Dict[str, Any] = {
        "available": True,
        "closed_trades": int(closed[0] or 0),
        "open_trades": int(open_n[0] or 0),
        "realised_pnl_usdt": float(closed[1] or 0.0),
        "errors_last_24h": int(errors_24h[0] or 0),
    }
    if run:
        out["latest_run"] = dict(run)
    return out


def heartbeat_age_seconds(iso: Optional[str]) -> Optional[float]:
    if not iso:
        return None
    try:
        ts = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - ts).total_seconds()


# ---- App -----------------------------------------------------------------


_INDEX_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>daytrade — mission control</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: -apple-system, system-ui, sans-serif; margin: 0;
         padding: 12px; background: #0e1116; color: #e5e9ef; }
  h1 { font-size: 1.4rem; margin: 0 0 8px 0; }
  .grid { display: grid; gap: 16px;
          grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }
  .card { background: #151b23; border: 1px solid #2a2f38;
          border-radius: 12px; padding: 14px; }
  .card h2 { font-size: 1.1rem; margin: 0 0 8px 0; display: flex;
             justify-content: space-between; align-items: center; }
  .pill { font-size: 0.7rem; padding: 3px 10px; border-radius: 999px; }
  .pill.ok { background: #0a4a25; color: #6dd58c; }
  .pill.warn { background: #4d3a08; color: #f7c163; }
  .pill.bad { background: #5a1721; color: #ff6b6b; }
  .kv { display: grid; grid-template-columns: auto auto; gap: 2px 12px;
        font-size: 0.85rem; margin: 6px 0 12px 0; }
  .kv .k { color: #98a0ab; }
  .kv .v { text-align: right; font-variant-numeric: tabular-nums; }
  pre { background: #0a0d12; padding: 8px; border-radius: 6px;
        font-size: 0.7rem; line-height: 1.3; overflow-x: auto;
        max-height: 200px; color: #b8c0cc; margin: 0; }
  .links a { color: #6db3ff; text-decoration: none; margin-right: 12px;
             font-size: 0.85rem; }
  .links a:hover { text-decoration: underline; }
  .meta { color: #6c727b; font-size: 0.75rem; margin: 10px 0 0 0; }
</style>
</head>
<body>
<h1>mission control <span class="meta" id="updated"></span></h1>

<h2 style="font-size:1.05rem;margin:18px 0 6px 0;">bots</h2>
<div class="grid" id="grid"></div>

<h2 style="font-size:1.05rem;margin:24px 0 6px 0;">claude · agents · roadmap</h2>
<div class="grid" id="agent-grid"></div>

<p class="meta">paper / simulation only. read-only. cannot place orders, kill processes, or edit DBs.</p>
<script>
function escapeHtml(s) { return String(s||'').replace(/[<>&]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;'})[c]); }

function parseEtimeSeconds(etime) {
  // ps etime format: 'MM:SS' or 'HH:MM:SS' or 'D-HH:MM:SS'
  if (!etime) return 0;
  let days = 0, rest = etime;
  if (rest.includes('-')) { const p = rest.split('-'); days = +p[0]; rest = p[1]; }
  const parts = rest.split(':').map(Number);
  let s = 0;
  if (parts.length === 3) s = parts[0]*3600 + parts[1]*60 + parts[2];
  else if (parts.length === 2) s = parts[0]*60 + parts[1];
  else s = parts[0]||0;
  return s + days*86400;
}

function renderBots(data) {
  const grid = document.getElementById('grid');
  grid.innerHTML = '';
  for (const bot of data.bots) {
    const aliveCount = bot.processes.length;
    const ageSec = bot.heartbeat_age_seconds;
    // The youngest process — what's actually serving work right now
    const procUptimes = bot.processes.map(p => parseEtimeSeconds(p.etime));
    const youngestProcSec = procUptimes.length ? Math.min(...procUptimes) : Infinity;
    let pillClass = 'bad', pillText = 'OFFLINE';
    if (aliveCount > 0 && ageSec !== null && ageSec < 600) {
      pillClass = 'ok'; pillText = 'ONLINE';
    } else if (aliveCount > 0 && youngestProcSec < (ageSec || Infinity)) {
      // New process exists but its heartbeat hasn't landed yet =
      // bot is in CLI startup (e.g. nighttrade's yfinance warmup).
      pillClass = 'warn';
      pillText = 'STARTING (' + Math.round(youngestProcSec) + 's)';
    } else if (aliveCount > 0) {
      pillClass = 'warn'; pillText = 'STALE HEARTBEAT';
    } else if (ageSec !== null && ageSec < 600) {
      pillClass = 'warn'; pillText = 'NO PROCESS';
    }
    const links = (bot.dashboard_url
          ? `<a href="${bot.dashboard_url}" target="_blank">dashboard ↗</a>` : '');
    const kv = (k, v) => `<div class="k">${k}</div><div class="v">${v}</div>`;
    let kvHtml = '';
    kvHtml += kv('processes', `${aliveCount} alive`);
    if (bot.processes[0]) {
      kvHtml += kv('pid · uptime', `${bot.processes[0].pid} · ${bot.processes[0].etime}`);
      kvHtml += kv('cpu', `${bot.processes[0].pcpu_pct.toFixed(1)}%`);
      // RAM with total across all of this bot's procs and a colour cue
      const rssMb = bot.total_rss_mb || 0;
      let ramColour = '#6dd58c';
      if (rssMb > 1024) ramColour = '#ff6b6b';
      else if (rssMb > 500) ramColour = '#f7c163';
      const ramFormatted = rssMb >= 1024 ? (rssMb/1024).toFixed(2)+' GB' : rssMb.toFixed(0)+' MB';
      kvHtml += kv('RAM (RSS)', `<span style="color:${ramColour}">${ramFormatted}</span>`);
    }
    if (bot.db && bot.db.available && !bot.db.db_error) {
      kvHtml += kv('closed trades', bot.db.closed_trades);
      kvHtml += kv('open positions', bot.db.open_trades);
      kvHtml += kv('realised PnL', '€' + bot.db.realised_pnl_usdt.toFixed(2));
      kvHtml += kv('errors 24h', bot.db.errors_last_24h);
      kvHtml += kv('heartbeat age', ageSec !== null ? Math.round(ageSec) + 's' : '—');
    } else if (bot.db && bot.db.db_error) {
      kvHtml += kv('db error', bot.db.db_error);
    } else {
      kvHtml += kv('db', '(not found)');
    }
    const logHtml = bot.log_tail.length
          ? `<pre>${bot.log_tail.slice(-12).map(l => escapeHtml(l)).join('\\n')}</pre>`
          : `<pre style="opacity:.5">(no log lines)</pre>`;
    // RAM sparkline — last hour
    const ramHist = bot.ram_history || [];
    let sparkSvg = '';
    if (ramHist.length > 1) {
      const W = 280, H = 50;
      const vals = ramHist.map(r => r.rss_mb || 0);
      const lo = Math.min(...vals), hi = Math.max(...vals);
      const range = Math.max(1, hi - lo);
      const pts = ramHist.map((r, i) => {
        const x = (i / (ramHist.length - 1)) * W;
        const y = H - ((r.rss_mb - lo) / range) * (H - 4) - 2;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      }).join(' ');
      const trend = vals[vals.length - 1] - vals[0];
      const trendColour = trend > 50 ? '#ff6b6b' : trend > 5 ? '#f7c163' : '#6dd58c';
      sparkSvg = `<div style="margin-top:8px;">
        <div style="font-size:.7rem;color:#98a0ab;display:flex;justify-content:space-between;">
          <span>RAM 60min · ${ramHist.length} samples</span>
          <span style="color:${trendColour}">${trend>=0?'+':''}${trend.toFixed(1)} MB</span>
        </div>
        <svg width="100%" height="${H}" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="background:#0a0d12;border-radius:4px;">
          <polyline points="${pts}" fill="none" stroke="${trendColour}" stroke-width="1.5"/>
        </svg>
      </div>`;
    }
    grid.innerHTML += `<section class="card">
      <h2>${escapeHtml(bot.name)} <span class="pill ${pillClass}">${pillText}</span></h2>
      <div class="kv">${kvHtml}</div>
      <div class="links">${links}</div>
      ${sparkSvg}
      ${logHtml}
    </section>`;
  }
  if (data.bots.length === 0) {
    grid.innerHTML = `<p style="opacity:.6">No bots configured.</p>`;
  }
}

function renderRoadmap(rm) {
  if (!rm || !rm.items) return '';
  const order = {in_progress:0, queued:1, complete:2, blocked:3};
  const items = [...rm.items].sort((a,b) =>
    (order[a.status]??9) - (order[b.status]??9));
  let html = `<div style="font-size:.75rem;color:#98a0ab;margin-bottom:8px;">
    goal: ${escapeHtml(rm.session_goal||'')}<br>
    phase: ${escapeHtml(rm.current_phase||'')}
  </div>`;
  for (const it of items) {
    const sev = it.severity || '';
    const sevColor = sev==='CRITICAL'?'#ff6b6b':sev==='HIGH'?'#f7c163':sev==='MEDIUM'?'#6db3ff':'#6c727b';
    const statusColor = it.status==='in_progress'?'#6dd58c':it.status==='complete'?'#6c727b':it.status==='blocked'?'#ff6b6b':'#98a0ab';
    html += `<div style="border-left:3px solid ${statusColor};padding:4px 8px;margin:4px 0;background:#0a0d12;border-radius:4px;">
      <div style="font-size:.8rem;display:flex;justify-content:space-between;">
        <span><b>${escapeHtml(it.id)}</b> ${escapeHtml(it.title)}</span>
        <span style="color:${sevColor};">${escapeHtml(sev)}</span>
      </div>
      <div style="font-size:.7rem;color:#98a0ab;">
        ${escapeHtml(it.status)} · ${escapeHtml(it.summary||'')}
      </div>
    </div>`;
  }
  return html;
}

function renderActivityTimeline(entries) {
  if (!entries || entries.length === 0) return '<pre style="opacity:.5">(no activity yet)</pre>';
  // Newest at top
  const rev = entries.slice().reverse().slice(0, 30);
  let html = '';
  const kindColor = {
    status: '#6db3ff', edit: '#6dd58c', shell: '#f7c163',
    spawn: '#c78dff', finding: '#ff6b6b', complete: '#6dd58c',
    roadmap: '#98a0ab',
  };
  for (const e of rev) {
    const c = kindColor[e.kind] || '#98a0ab';
    const t = new Date(e.ts);
    const tstr = t.toLocaleTimeString();
    html += `<div style="border-left:3px solid ${c};padding:3px 8px;margin:2px 0;font-size:.75rem;">
      <span style="color:#6c727b;">${tstr}</span>
      <span style="color:${c};">${escapeHtml(e.agent)}</span>
      · ${escapeHtml(e.summary)}
      ${e.detail?`<div style="opacity:.6;font-size:.7rem;">${escapeHtml(e.detail)}</div>`:''}
    </div>`;
  }
  return html;
}

async function refresh() {
  try {
    const [stateR, actR, rmR] = await Promise.all([
      fetch('/api/state',    {cache: 'no-store'}),
      fetch('/api/activity', {cache: 'no-store'}),
      fetch('/api/roadmap',  {cache: 'no-store'}),
    ]);
    const data = await stateR.json();
    const act = await actR.json();
    const rm = await rmR.json();
    document.getElementById('updated').textContent =
        '· ' + new Date(data.now).toLocaleTimeString();
    renderBots(data);

    const agentGrid = document.getElementById('agent-grid');

    // Group activity by agent
    const byAgent = {};
    for (const e of (act.entries || [])) {
      (byAgent[e.agent] = byAgent[e.agent] || []).push(e);
    }
    const agents = Object.keys(byAgent).sort();
    let cards = '';

    // Roadmap card (always first)
    cards += `<section class="card">
      <h2>roadmap</h2>
      ${renderRoadmap(rm)}
    </section>`;

    // One card per agent
    for (const ag of agents) {
      const entries = byAgent[ag];
      const last = entries[entries.length - 1];
      const ageSec = (Date.now() - new Date(last.ts).getTime()) / 1000;
      let pill = 'ok', pillText = 'ACTIVE';
      if (ageSec > 600) { pill = 'bad'; pillText = 'IDLE >10m'; }
      else if (ageSec > 120) { pill = 'warn'; pillText = 'idle ' + Math.round(ageSec/60) + 'm'; }
      cards += `<section class="card">
        <h2>${escapeHtml(ag)} <span class="pill ${pill}">${pillText}</span></h2>
        <div style="font-size:.75rem;color:#98a0ab;margin:0 0 8px 0;">
          ${entries.length} entries · last: ${new Date(last.ts).toLocaleTimeString()}
        </div>
        ${renderActivityTimeline(entries)}
      </section>`;
    }

    if (agents.length === 0) {
      cards += `<section class="card">
        <h2>activity</h2>
        <pre style="opacity:.5">No agent activity logged yet.</pre>
      </section>`;
    }
    agentGrid.innerHTML = cards;
  } catch (e) {
    document.getElementById('updated').textContent = '(refresh failed: ' + e + ')';
  }
}
refresh();
setInterval(refresh, 5000);
</script>
</body></html>
"""


def create_app(bots: Optional[List[Bot]] = None) -> FastAPI:
    bots = bots if bots is not None else default_bots()
    app = FastAPI(title="daytrade — mission control",
                  docs_url="/api/docs")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _INDEX_HTML

    @app.get("/api/state")
    def state() -> JSONResponse:
        snapshot = list_processes()
        out_bots: List[Dict[str, Any]] = []
        # RAM history: pull last 60 minutes for the configured bots
        ram_series = _ram.by_bot([b.name for b in bots], window_minutes=60)
        for b in bots:
            procs = find_bot_processes(b, snapshot)
            db = db_summary(b.db_path)
            hb_age = None
            if db.get("available") and db.get("latest_run"):
                hb_age = heartbeat_age_seconds(
                    db["latest_run"].get("last_heartbeat_ts"))
            out_bots.append({
                "name": b.name,
                "notes": b.notes,
                "project_root": str(b.project_root),
                "dashboard_url": b.dashboard_url,
                "processes": procs,
                "log_tail": tail_file(b.log_path, lines=30),
                "db": db,
                "heartbeat_age_seconds": hb_age,
                "ram_history": ram_series.get(b.name, []),
                "total_rss_mb": round(sum((p.get("rss_mb") or 0)
                                          for p in procs), 1),
            })
        # Append current RAM samples so the next request has fresh history
        now_iso = datetime.now(timezone.utc).isoformat()
        _ram.append([
            {"ts": now_iso, "bot": b["name"], "pid": p["pid"],
             "rss_mb": p.get("rss_mb"), "pcpu_pct": p.get("pcpu_pct")}
            for b in out_bots for p in b["processes"]
        ])
        return JSONResponse({
            "now": now_iso,
            "host": os.uname().nodename,
            "bots": out_bots,
            "all_python_processes": snapshot,
        })

    @app.get("/api/health")
    def health() -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.get("/api/activity")
    def activity_endpoint(n: int = 80) -> JSONResponse:
        """Most recent ``n`` agent-activity entries."""
        return JSONResponse({"entries": _activity.tail(n)})

    @app.get("/api/roadmap")
    def roadmap_endpoint() -> JSONResponse:
        """Current QA / work roadmap."""
        return JSONResponse(_activity.read_roadmap())

    return app
