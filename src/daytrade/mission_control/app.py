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
from dataclasses import dataclass
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
    project_root: Path  # repo root for that bot
    process_match: List[str]  # substrings; any match = "this is the bot"
    log_path: Optional[Path] = None  # main log file, if any
    db_path: Optional[Path] = None  # observatory DB, if any
    dashboard_url: Optional[str] = None  # if the bot has its own dashboard
    notes: str = ""


def default_bots() -> List[Bot]:
    """The two bots known to live on this host.

    IMPORTANT: nighttrade has BOTH a dev directory (~/Desktop/coding/
    nighttrade) where source is edited and a deployed directory
    (~/nighttrade) where launchd actually runs it from. The launchd-
    managed observer writes to the DEPLOYED DB / log, so mission
    control reads from there to see truth. (Daytrade runs from its dev
    directory — no split.)
    """
    home = Path("/Users/nedimvejo")
    dt_root = home / "Desktop" / "coding" / "daytrade"
    nt_dev = home / "Desktop" / "coding" / "nighttrade"  # source of truth for code
    nt_deployed = home / "nighttrade"  # where launchd reads/writes
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
            project_root=nt_deployed,
            process_match=["nighttrade observe"],
            log_path=nt_deployed / "logs" / "nighttrade.log",
            db_path=nt_deployed / "artifacts" / "observatory.db",
            dashboard_url="http://100.127.143.106:8001",
            notes="continuous market-safety observer (S&P 500)",
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
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
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
        rows.append(
            {
                "pid": int(pid),
                "ppid": int(ppid),
                "pmem_pct": float(pmem),
                "pcpu_pct": float(pcpu),
                "rss_mb": round(float(rss_kb) / 1024.0, 1),
                "etime": etime,
                "command": command,
            }
        )
    return rows


def find_bot_processes(bot: Bot, snapshot: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    return out.splitlines()


def db_summary(db_path: Optional[Path]) -> Dict[str, Any]:
    """Pull the same 'is the bot OK' signal both dashboards use."""
    if not db_path or not db_path.exists():
        return {"available": False}
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        con.row_factory = sqlite3.Row
        run = con.execute(
            "SELECT id, pid, status, started_ts, stopped_ts, "
            "last_heartbeat_ts, cycles FROM bot_runs "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        closed = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(pnl), 0) FROM paper_trades " "WHERE status='closed'"
        ).fetchone()
        open_n = con.execute("SELECT COUNT(*) FROM paper_trades WHERE status='open'").fetchone()
        # Belt+suspenders: only count REAL errors. Rows whose context
        # starts with 'alert:' are informational market/learning alerts
        # (e.g. 'VETUSDT illiquid') that were historically miswritten to
        # the errors table — they're not bugs, they shouldn't trigger a
        # yellow warning banner.
        errors_24h = con.execute(
            "SELECT COUNT(*) FROM errors WHERE ts >= "
            "datetime('now', '-1 day') AND (context IS NULL OR context NOT LIKE 'alert:%')"
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
<title>Mission control</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: -apple-system, system-ui, sans-serif; margin: 0;
         padding: 12px; background: #0e1116; color: #e5e9ef; }
  h1 { font-size: 1.4rem; margin: 0 0 8px 0; }
  h2.section { font-size: 1rem; margin: 22px 0 8px 0; color: #98a0ab;
               font-weight: 500; letter-spacing: 0.04em;
               text-transform: uppercase; }
  .grid { display: grid; gap: 16px;
          grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }
  .card { background: #151b23; border: 1px solid #2a2f38;
          border-radius: 12px; padding: 16px; }
  .card h2.card-title { font-size: 1.15rem; margin: 0 0 4px 0;
                        display: flex; justify-content: space-between;
                        align-items: center; }
  .pill { font-size: 0.7rem; padding: 3px 10px; border-radius: 999px;
          font-weight: 500; }
  .pill.ok { background: #0a4a25; color: #6dd58c; }
  .pill.warn { background: #4d3a08; color: #f7c163; }
  .pill.bad { background: #5a1721; color: #ff6b6b; }
  .summary { font-size: 0.95rem; line-height: 1.4; margin: 8px 0 14px 0;
             color: #e5e9ef; }
  .stat-row { display: flex; gap: 16px; margin: 8px 0; flex-wrap: wrap; }
  .stat { background: #0a0d12; padding: 8px 12px; border-radius: 8px;
          min-width: 100px; flex: 1; }
  .stat .label { font-size: 0.72rem; color: #98a0ab; }
  .stat .value { font-size: 1.05rem; font-weight: 600;
                 font-variant-numeric: tabular-nums; margin-top: 2px; }
  pre { background: #0a0d12; padding: 8px; border-radius: 6px;
        font-size: 0.7rem; line-height: 1.3; overflow-x: auto;
        max-height: 160px; color: #b8c0cc; margin: 8px 0 0 0; }
  details { margin-top: 10px; }
  details summary { cursor: pointer; color: #6db3ff; font-size: 0.8rem; }
  .links a { color: #6db3ff; text-decoration: none; margin-right: 12px;
             font-size: 0.85rem; }
  .links a:hover { text-decoration: underline; }
  .meta { color: #6c727b; font-size: 0.75rem; margin: 10px 0 0 0; }
</style>
</head>
<body>
<h1>Mission control <span class="meta" id="updated"></span></h1>

<section class="card" id="ram-overview" style="margin-bottom:18px;">
  <div style="font-size:.72rem;color:#98a0ab;letter-spacing:.04em;text-transform:uppercase;margin-bottom:8px;">
    Memory used right now
  </div>
  <div id="ram-overview-body" style="display:flex;gap:12px;flex-wrap:wrap;"></div>
  <div id="ram-overview-combined" style="margin-top:14px;"></div>
</section>

<h2 class="section">Trading bots</h2>
<div class="grid" id="grid"></div>

<h2 class="section">What Claude is doing</h2>
<div class="grid" id="agent-grid"></div>

<p class="meta">Paper / simulation only. Read-only. Cannot place orders, kill processes, or edit databases.</p>
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

// Format helpers — output is plain English
function humanBytes(mb) {
  if (mb === null || mb === undefined) return '—';
  if (mb < 1024) return mb.toFixed(0) + ' MB';
  return (mb/1024).toFixed(2) + ' GB';
}
function humanUptime(etime) {
  // Convert '11-09:21:26' or '2-06:34' or '01:36' to plain English
  const s = parseEtimeSeconds(etime);
  if (s < 60) return Math.round(s) + ' seconds';
  if (s < 3600) return Math.round(s/60) + ' minutes';
  if (s < 86400) {
    const h = Math.floor(s/3600), m = Math.round((s%3600)/60);
    return h + ' hour' + (h!==1?'s':'') + (m?' '+m+' min':'');
  }
  const d = Math.floor(s/86400), h = Math.floor((s%86400)/3600);
  return d + ' day' + (d!==1?'s':'') + (h?' '+h+'h':'');
}
function humanAge(seconds) {
  if (seconds === null || seconds === undefined) return 'never';
  if (seconds < 60) return Math.round(seconds) + ' seconds ago';
  if (seconds < 3600) return Math.round(seconds/60) + ' minutes ago';
  if (seconds < 86400) return Math.round(seconds/3600) + ' hours ago';
  return Math.round(seconds/86400) + ' days ago';
}

function statusFor(bot) {
  // Returns {pillClass, pillText, summary} — one sentence summary in English
  const alive = bot.processes.length;
  const ageSec = bot.heartbeat_age_seconds;
  const procUptimes = bot.processes.map(p => parseEtimeSeconds(p.etime));
  const youngestProcSec = procUptimes.length ? Math.min(...procUptimes) : Infinity;
  const justStarted = youngestProcSec < 180;  // <3 min old

  // No process at all
  if (alive === 0) {
    return {pillClass:'bad', pillText:'STOPPED',
      summary:`This bot is NOT running. Last sign of life was ${humanAge(ageSec)}.`};
  }
  // Process exists + recent heartbeat
  if (ageSec !== null && ageSec < 600) {
    return {pillClass:'ok', pillText:'HEALTHY',
      summary:`This bot is running and reporting in regularly. Last activity ${humanAge(ageSec)}.`};
  }
  // Process exists but heartbeat is older than the process itself = just restarted
  if (youngestProcSec < (ageSec || Infinity)) {
    return {pillClass:'warn',
      pillText:'STARTING',
      summary:`This bot just started ${humanUptime(bot.processes[0].etime)} ago and is still warming up (downloading data, loading model). It hasn't started reporting in yet.`};
  }
  // Process exists, heartbeat is stale → bot is alive but stuck
  return {pillClass:'warn', pillText:'NOT RESPONDING',
    summary:`This bot is alive (process is running) but has not reported in for ${humanAge(ageSec)}. It may be stuck — consider restarting.`};
}

function ramTrend(ramHist) {
  if (!ramHist || ramHist.length < 2) return null;
  const vals = ramHist.map(r => r.rss_mb || 0);
  const first = vals[0], last = vals[vals.length - 1];
  const trendMb = last - first;
  const minutes = (new Date(ramHist[ramHist.length-1].ts) - new Date(ramHist[0].ts)) / 60000;
  let label = 'stable';
  let colour = '#6dd58c';
  if (Math.abs(trendMb) < 10) { label = 'stable'; colour = '#6dd58c'; }
  else if (trendMb > 50) { label = 'growing (possible leak)'; colour = '#ff6b6b'; }
  else if (trendMb > 10) { label = 'slowly growing'; colour = '#f7c163'; }
  else if (trendMb < -50) { label = 'dropped sharply'; colour = '#6db3ff'; }
  else { label = 'slowly shrinking'; colour = '#6db3ff'; }
  return {label, colour, trendMb, minutes, first, last};
}

function renderBots(data) {
  const grid = document.getElementById('grid');
  grid.innerHTML = '';
  for (const bot of data.bots) {
    const st = statusFor(bot);
    const rssMb = bot.total_rss_mb || 0;
    const trades = bot.db && bot.db.available && !bot.db.db_error;
    const ramHist = bot.ram_history || [];
    const trend = ramTrend(ramHist);

    // Stat row — 3-4 big numbers
    let stats = '';
    if (bot.processes[0]) {
      stats += `<div class="stat"><div class="label">Memory used</div>
                <div class="value">${humanBytes(rssMb)}</div></div>`;
      stats += `<div class="stat"><div class="label">Running for</div>
                <div class="value">${humanUptime(bot.processes[0].etime)}</div></div>`;
    }
    if (trades) {
      stats += `<div class="stat"><div class="label">Paper trades made</div>
                <div class="value">${bot.db.closed_trades.toLocaleString()}</div></div>`;
      const pnl = bot.db.realised_pnl_usdt;
      const pnlColor = pnl > 0 ? '#6dd58c' : pnl < 0 ? '#ff6b6b' : '#e5e9ef';
      stats += `<div class="stat"><div class="label">Paper money earned</div>
                <div class="value" style="color:${pnlColor}">€${pnl.toFixed(2)}</div></div>`;
    }

    // RAM trend sentence
    let trendSentence = '';
    if (trend) {
      trendSentence = `<div style="font-size:.85rem;margin:8px 0;">
        Memory has been <strong style="color:${trend.colour}">${trend.label}</strong>
        over the last ${Math.round(trend.minutes)} minutes
        (from ${humanBytes(trend.first)} to ${humanBytes(trend.last)}).
      </div>`;
    } else {
      trendSentence = `<div style="font-size:.85rem;margin:8px 0;color:#98a0ab;">
        Memory tracking is just starting — not enough data for a trend yet.
      </div>`;
    }

    // SVG sparkline (smaller, just a visual hint)
    let sparkSvg = '';
    if (ramHist.length > 1) {
      const W = 280, H = 36;
      const vals = ramHist.map(r => r.rss_mb || 0);
      const lo = Math.min(...vals), hi = Math.max(...vals);
      const range = Math.max(1, hi - lo);
      const pts = ramHist.map((r, i) => {
        const x = (i / (ramHist.length - 1)) * W;
        const y = H - ((r.rss_mb - lo) / range) * (H - 4) - 2;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      }).join(' ');
      sparkSvg = `<svg width="100%" height="${H}" viewBox="0 0 ${W} ${H}"
          preserveAspectRatio="none" style="background:#0a0d12;border-radius:4px;">
        <polyline points="${pts}" fill="none" stroke="${trend.colour}" stroke-width="1.5"/>
      </svg>
      <div style="font-size:.7rem;color:#6c727b;display:flex;justify-content:space-between;margin-top:2px;">
        <span>low: ${humanBytes(Math.min(...vals))}</span>
        <span>now: ${humanBytes(vals[vals.length-1])}</span>
        <span>high: ${humanBytes(Math.max(...vals))}</span>
      </div>`;
    }

    // Recent activity (collapsible) — drop full log dump from card
    const logHtml = bot.log_tail.length
      ? `<details><summary>Show recent activity log</summary>
         <pre>${bot.log_tail.slice(-10).map(l => escapeHtml(l)).join('\\n')}</pre>
         </details>`
      : '';

    // Errors today (only show if any)
    let errorLine = '';
    if (trades && bot.db.errors_last_24h > 0) {
      errorLine = `<div style="color:#f7c163;font-size:.85rem;margin:4px 0;">
        ⚠ ${bot.db.errors_last_24h} error${bot.db.errors_last_24h===1?'':'s'} in the last 24 hours.
      </div>`;
    }

    // Dashboard link
    const links = bot.dashboard_url
      ? `<div style="margin-top:8px;"><a href="${bot.dashboard_url}" target="_blank" style="color:#6db3ff;font-size:.85rem;">Open ${escapeHtml(bot.name)}'s own dashboard ↗</a></div>`
      : '';

    grid.innerHTML += `<section class="card">
      <h2 class="card-title">${escapeHtml(bot.name)}
        <span class="pill ${st.pillClass}">${st.pillText}</span></h2>
      <div class="summary">${st.summary}</div>
      <div class="stat-row">${stats}</div>
      ${errorLine}
      ${trendSentence}
      ${sparkSvg}
      ${links}
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
  const counts = {complete:0, in_progress:0, queued:0, blocked:0};
  for (const it of rm.items) counts[it.status] = (counts[it.status]||0)+1;
  const total = rm.items.length;
  const pct = total ? Math.round(counts.complete / total * 100) : 0;

  let html = `<div style="font-size:.9rem;margin-bottom:10px;">
    <div><strong>Goal:</strong> ${escapeHtml(rm.session_goal||'')}</div>
    <div style="margin-top:4px;"><strong>Right now:</strong> ${escapeHtml(rm.current_phase||'idle')}</div>
    <div style="margin-top:8px;font-size:.8rem;color:#98a0ab;">
      ${counts.complete}/${total} tasks done (${pct}%)
      · ${counts.in_progress} in progress
      · ${counts.queued} waiting
    </div>
    <div style="background:#0a0d12;border-radius:4px;height:6px;margin-top:6px;overflow:hidden;">
      <div style="background:#6dd58c;height:100%;width:${pct}%;"></div>
    </div>
  </div>`;

  const statusLabel = {
    in_progress: 'WORKING ON IT',
    queued: 'WAITING',
    complete: 'DONE',
    blocked: 'BLOCKED',
  };
  const statusColor = {
    in_progress: '#6dd58c',
    queued: '#98a0ab',
    complete: '#6c727b',
    blocked: '#ff6b6b',
  };

  for (const it of items) {
    const sev = it.severity || '';
    const sevColor = sev==='CRITICAL'?'#ff6b6b':sev==='HIGH'?'#f7c163':sev==='MEDIUM'?'#6db3ff':'transparent';
    const sc = statusColor[it.status] || '#98a0ab';
    const sl = statusLabel[it.status] || it.status;
    const titleStyle = it.status === 'complete' ? 'opacity:.5;text-decoration:line-through;' : '';
    html += `<div style="border-left:3px solid ${sc};padding:6px 10px;margin:4px 0;background:#0a0d12;border-radius:4px;">
      <div style="font-size:.85rem;display:flex;justify-content:space-between;align-items:center;${titleStyle}">
        <span>${escapeHtml(it.title)}</span>
        <span style="font-size:.65rem;color:${sc};font-weight:600;">${sl}</span>
      </div>
      ${it.summary?`<div style="font-size:.75rem;color:#98a0ab;margin-top:2px;${titleStyle}">${escapeHtml(it.summary)}</div>`:''}
      ${sev && it.status !== 'complete'?`<div style="font-size:.65rem;color:${sevColor};margin-top:2px;font-weight:600;">${escapeHtml(sev)} severity</div>`:''}
    </div>`;
  }
  return html;
}

function renderActivityTimeline(entries) {
  if (!entries || entries.length === 0)
    return '<div style="opacity:.5;font-size:.85rem;">No activity logged yet.</div>';
  const rev = entries.slice().reverse().slice(0, 20);
  const kindLabel = {
    status: 'Thinking', edit: 'Changed code', shell: 'Ran a command',
    spawn: 'Started helper', finding: 'Found problem',
    complete: 'Finished', roadmap: 'Updated plan',
  };
  const kindColor = {
    status: '#6db3ff', edit: '#6dd58c', shell: '#f7c163',
    spawn: '#c78dff', finding: '#ff6b6b', complete: '#6dd58c',
    roadmap: '#98a0ab',
  };
  let html = '';
  for (const e of rev) {
    const c = kindColor[e.kind] || '#98a0ab';
    const label = kindLabel[e.kind] || e.kind;
    const t = new Date(e.ts);
    const ageSec = (Date.now() - t.getTime()) / 1000;
    const tstr = ageSec < 60 ? Math.round(ageSec) + 's ago'
                : ageSec < 3600 ? Math.round(ageSec/60) + 'm ago'
                : t.toLocaleTimeString();
    html += `<div style="border-left:3px solid ${c};padding:6px 10px;margin:4px 0;background:#0a0d12;border-radius:4px;">
      <div style="font-size:.7rem;color:#98a0ab;display:flex;justify-content:space-between;">
        <span style="color:${c};font-weight:600;">${label}</span>
        <span>${tstr}</span>
      </div>
      <div style="font-size:.85rem;margin-top:2px;">${escapeHtml(e.summary)}</div>
      ${e.detail?`<div style="font-size:.7rem;color:#98a0ab;margin-top:2px;">${escapeHtml(e.detail)}</div>`:''}
    </div>`;
  }
  return html;
}

function renderRamOverview(data) {
  const body = document.getElementById('ram-overview-body');
  const combined = document.getElementById('ram-overview-combined');
  body.innerHTML = '';
  let totalMb = 0;
  for (const bot of data.bots) {
    const rss = bot.total_rss_mb || 0;
    totalMb += rss;
    let colour = '#6dd58c';
    if (rss > 1024) colour = '#ff6b6b';
    else if (rss > 500) colour = '#f7c163';
    const alive = bot.processes.length > 0;
    body.innerHTML += `<div style="flex:1;min-width:140px;background:#0a0d12;border-radius:8px;padding:12px;">
      <div style="font-size:.72rem;color:#98a0ab;">${escapeHtml(bot.name)}</div>
      <div style="font-size:1.6rem;font-weight:700;color:${colour};font-variant-numeric:tabular-nums;">
        ${humanBytes(rss)}
      </div>
      <div style="font-size:.7rem;color:#6c727b;">
        ${alive ? bot.processes.length + ' process' + (bot.processes.length!==1?'es':'') : 'not running'}
      </div>
    </div>`;
  }
  // Combined total
  body.innerHTML += `<div style="flex:1;min-width:140px;background:#0a0d12;border-radius:8px;padding:12px;border:1px solid #2a2f38;">
    <div style="font-size:.72rem;color:#98a0ab;">All bots combined</div>
    <div style="font-size:1.6rem;font-weight:700;font-variant-numeric:tabular-nums;">
      ${humanBytes(totalMb)}
    </div>
    <div style="font-size:.7rem;color:#6c727b;">
      out of ${(16384/1024).toFixed(0)} GB total laptop RAM (~${(totalMb/16384*100).toFixed(1)}%)
    </div>
  </div>`;

  // Combined stacked sparkline: each bot a different colour
  const allSeries = data.bots
    .filter(b => (b.ram_history || []).length > 1)
    .map(b => ({name: b.name, points: b.ram_history}));
  if (allSeries.length > 0) {
    const W = 600, H = 70;
    // Build a shared time axis
    const allTs = [];
    for (const s of allSeries) for (const p of s.points) allTs.push(new Date(p.ts).getTime());
    if (allTs.length < 2) { combined.innerHTML = ''; return; }
    const tMin = Math.min(...allTs), tMax = Math.max(...allTs);
    const tRange = Math.max(1, tMax - tMin);
    const allVals = allSeries.flatMap(s => s.points.map(p => p.rss_mb || 0));
    const vMax = Math.max(...allVals, 1);
    const colours = {daytrade: '#6db3ff', nighttrade: '#c78dff'};

    let svg = `<div style="font-size:.72rem;color:#98a0ab;margin-bottom:4px;">Memory over time (last hour)</div>
      <svg width="100%" height="${H}" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="background:#0a0d12;border-radius:4px;">`;
    // Y gridline at vMax/2
    svg += `<line x1="0" y1="${H/2}" x2="${W}" y2="${H/2}" stroke="#2a2f38" stroke-width="0.5" stroke-dasharray="2,3"/>`;
    for (const s of allSeries) {
      const colour = colours[s.name] || '#98a0ab';
      const pts = s.points.map(p => {
        const x = ((new Date(p.ts).getTime() - tMin) / tRange) * W;
        const y = H - ((p.rss_mb || 0) / vMax) * (H - 4) - 2;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      }).join(' ');
      svg += `<polyline points="${pts}" fill="none" stroke="${colour}" stroke-width="2"/>`;
    }
    svg += `</svg>`;
    // Legend
    svg += `<div style="font-size:.7rem;color:#6c727b;display:flex;gap:14px;margin-top:4px;">`;
    for (const s of allSeries) {
      const colour = colours[s.name] || '#98a0ab';
      svg += `<span><span style="display:inline-block;width:10px;height:10px;background:${colour};border-radius:2px;vertical-align:middle;"></span> ${escapeHtml(s.name)}</span>`;
    }
    svg += `<span style="margin-left:auto;">peak: ${humanBytes(vMax)}</span></div>`;
    combined.innerHTML = svg;
  } else {
    combined.innerHTML = '<div style="font-size:.75rem;color:#98a0ab;">Memory history graph will appear once enough samples are collected (~30 seconds).</div>';
  }
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
    renderRamOverview(data);
    renderBots(data);

    const agentGrid = document.getElementById('agent-grid');

    // Group activity by agent
    const byAgent = {};
    for (const e of (act.entries || [])) {
      (byAgent[e.agent] = byAgent[e.agent] || []).push(e);
    }
    const agents = Object.keys(byAgent).sort();
    let cards = '';

    // Plan card (always first)
    cards += `<section class="card">
      <h2 class="card-title">The plan</h2>
      ${renderRoadmap(rm)}
    </section>`;

    // One card per agent
    for (const ag of agents) {
      const entries = byAgent[ag];
      const last = entries[entries.length - 1];
      const ageSec = (Date.now() - new Date(last.ts).getTime()) / 1000;
      let pill = 'ok', pillText = 'WORKING', summary = '';
      if (ageSec > 600) {
        pill = 'bad'; pillText = 'IDLE';
        summary = `Hasn't done anything for ${humanAge(ageSec)}.`;
      } else if (ageSec > 120) {
        pill = 'warn'; pillText = 'PAUSED';
        summary = `Last action was ${humanAge(ageSec)}. May be waiting on something.`;
      } else {
        summary = `Last action ${humanAge(ageSec)}. ${entries.length} things done.`;
      }
      const displayName = ag === 'claude-main' ? 'Claude (you, the engineer)'
                        : ag.startsWith('qa-') ? 'Helper: QA auditor'
                        : ag;
      cards += `<section class="card">
        <h2 class="card-title">${escapeHtml(displayName)}
          <span class="pill ${pill}">${pillText}</span></h2>
        <div class="summary">${summary}</div>
        ${renderActivityTimeline(entries)}
      </section>`;
    }

    if (agents.length === 0) {
      cards += `<section class="card">
        <h2 class="card-title">Claude</h2>
        <div class="summary">Nothing has been logged yet.</div>
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
    app = FastAPI(title="daytrade — mission control", docs_url="/api/docs")

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
                hb_age = heartbeat_age_seconds(db["latest_run"].get("last_heartbeat_ts"))
            out_bots.append(
                {
                    "name": b.name,
                    "notes": b.notes,
                    "project_root": str(b.project_root),
                    "dashboard_url": b.dashboard_url,
                    "processes": procs,
                    "log_tail": tail_file(b.log_path, lines=30),
                    "db": db,
                    "heartbeat_age_seconds": hb_age,
                    "ram_history": ram_series.get(b.name, []),
                    "total_rss_mb": round(sum((p.get("rss_mb") or 0) for p in procs), 1),
                }
            )
        # Append current RAM samples so the next request has fresh history
        now_iso = datetime.now(timezone.utc).isoformat()
        _ram.append(
            [
                {
                    "ts": now_iso,
                    "bot": b["name"],
                    "pid": p["pid"],
                    "rss_mb": p.get("rss_mb"),
                    "pcpu_pct": p.get("pcpu_pct"),
                }
                for b in out_bots
                for p in b["processes"]
            ]
        )
        return JSONResponse(
            {
                "now": now_iso,
                "host": os.uname().nodename,
                "bots": out_bots,
                "all_python_processes": snapshot,
            }
        )

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
