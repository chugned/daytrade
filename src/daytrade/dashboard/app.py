"""FastAPI backend for the Market Safety Observatory dashboard.

Serves a single-page dashboard and a small read-only JSON API over the
observatory database, plus a WebSocket that pushes the overview live.

The backend is strictly read-only — it observes the observatory's database
and never writes orders, touches wallets, or moves money.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from ..observatory.database import DEFAULT_DB_PATH
from ..runtime import get_logger
from .data import DashboardData

_log = get_logger("dashboard")
_STATIC = Path(__file__).resolve().parent / "static"

# Optional HTTP Basic-Auth gate. Enabled only when DASHBOARD_PASSWORD is set,
# so local runs and the test suite stay open while a public deployment can be
# locked down. The username is not checked — only the password.
_PASSWORD_ENV = "DASHBOARD_PASSWORD"


class _BasicAuthMiddleware:
    """ASGI middleware: require HTTP Basic Auth on every http/websocket request."""

    def __init__(self, app, password: str) -> None:
        self.app = app
        self._password = password

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] in ("http", "websocket") and not self._ok(scope):
            if scope["type"] == "http":
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"www-authenticate",
                                         b'Basic realm="daytrade dashboard"'),
                                        (b"content-type", b"text/plain")]})
                await send({"type": "http.response.body",
                            "body": b"Authentication required"})
            else:  # reject the websocket handshake
                await send({"type": "websocket.close", "code": 1008})
            return
        await self.app(scope, receive, send)

    def _ok(self, scope) -> bool:
        raw = dict(scope.get("headers") or []).get(b"authorization")
        if not raw:
            return False
        try:
            kind, _, encoded = raw.decode().partition(" ")
            if kind.lower() != "basic":
                return False
            _, _, pw = base64.b64decode(encoded).decode("utf-8").partition(":")
        except Exception:  # noqa: BLE001 - any malformed header == unauthorized
            return False
        return secrets.compare_digest(pw, self._password)


def create_app(db_path: Path | str = DEFAULT_DB_PATH) -> FastAPI:
    """Build the dashboard FastAPI application bound to ``db_path``."""
    app = FastAPI(title="daytrade — Market Safety Observatory",
                  docs_url="/api/docs")

    password = os.environ.get(_PASSWORD_ENV, "").strip()
    if password:
        app.add_middleware(_BasicAuthMiddleware, password=password)
        _log.info("dashboard password protection ENABLED (%s is set)",
                  _PASSWORD_ENV)
    else:
        _log.info("dashboard password protection disabled — set %s to "
                  "require a password", _PASSWORD_ENV)

    def data() -> DashboardData:
        return DashboardData(db_path)

    def _safe(fn) -> Any:
        """Run a data accessor, returning an error payload instead of a 500."""
        accessor = data()
        try:
            return fn(accessor)
        except Exception as exc:  # noqa: BLE001 - dashboard must stay up
            _log.exception("dashboard data error")
            return {"error": repr(exc)}
        finally:
            accessor.close()

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (_STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/api/overview")
    def overview() -> Any:
        return _safe(lambda d: d.overview())

    @app.get("/api/symbols")
    def symbols() -> Any:
        return _safe(lambda d: d.symbols())

    @app.get("/api/symbol/{symbol}")
    def symbol_detail(symbol: str) -> Any:
        return _safe(lambda d: d.symbol_detail(symbol))

    @app.get("/api/accuracy")
    def accuracy() -> Any:
        return _safe(lambda d: d.accuracy())

    @app.get("/api/paper")
    def paper() -> Any:
        return _safe(lambda d: d.paper())

    @app.get("/api/shadow-vs-paper")
    def shadow_vs_paper() -> Any:
        """Side-by-side ledger of shadow-mode trades vs paper trades.

        Reads BOTH the paper DB (this dashboard's main DB) and the
        shadow DB (default `observatory-shadow.db` next to the paper
        DB). Returns aggregates + last-5 trades from each. If shadow
        DB does not exist, only paper is returned."""
        from pathlib import Path as _P

        shadow_path = _P(db_path).with_name("observatory-shadow.db")

        def _summary(p) -> dict:
            if not _P(p).exists():
                return {"available": False, "path": str(p)}
            d = DashboardData(p)
            try:
                closed = d.db.closed_paper_trades(limit=10000)
                opens = d.db.open_paper_trades()
            finally:
                d.close()
            pnl = sum((t.get("pnl") or 0.0) for t in closed)
            wins = sum(1 for t in closed if (t.get("pnl") or 0.0) > 0)
            n = len(closed)
            return {
                "available": True,
                "path": str(p),
                "closed_trades": n,
                "open_trades": len(opens),
                "win_rate": (wins / n * 100) if n else 0.0,
                "total_pnl": pnl,
                "fees_paid": sum((t.get("fees") or 0.0) for t in closed),
                "slippage": sum((t.get("slippage") or 0.0) for t in closed),
                "last_5_trades": closed[-5:],
            }

        paper_sum = _summary(db_path)
        shadow_sum = _summary(shadow_path)
        delta = None
        if paper_sum.get("available") and shadow_sum.get("available"):
            p_pnl = paper_sum["total_pnl"]
            s_pnl = shadow_sum["total_pnl"]
            if abs(p_pnl) > 1e-9:
                rel_pct = (s_pnl - p_pnl) / abs(p_pnl) * 100
                delta = {
                    "pnl_abs": s_pnl - p_pnl,
                    "pnl_rel_pct": rel_pct,
                    "verdict": ("within_15pct" if abs(rel_pct) <= 15
                                else "outside_15pct_investigate"),
                }
        return {
            "paper": paper_sum,
            "shadow": shadow_sum,
            "delta": delta,
        }

    @app.get("/api/risk")
    def risk() -> Any:
        return _safe(lambda d: d.risk())

    @app.get("/api/safety-history")
    def safety_history() -> Any:
        return _safe(lambda d: d.safety_history())

    @app.get("/api/equity")
    def equity() -> Any:
        return _safe(lambda d: d.equity_history())

    # --- learning observatory endpoints ---

    @app.get("/api/status")
    def status() -> Any:
        return _safe(lambda d: d.status())

    @app.get("/api/logs")
    def logs(lines: int = 200) -> Any:
        return _safe(lambda d: d.logs(lines))

    @app.get("/api/db-writes")
    def db_writes(lines: int = 300) -> Any:
        return _safe(lambda d: d.db_writes(lines))

    @app.get("/api/price-chart")
    def price_chart(symbol: str = "BTCUSDT", range: str = "1D") -> Any:
        return _safe(lambda d: d.price_chart(symbol, range))

    @app.get("/api/progress")
    def progress() -> Any:
        return _safe(lambda d: d.progress())

    @app.get("/api/regimes")
    def regimes() -> Any:
        return _safe(lambda d: d.regimes())

    @app.get("/api/calibration")
    def calibration() -> Any:
        return _safe(lambda d: d.calibration())

    @app.get("/api/readiness")
    def readiness() -> Any:
        return _safe(lambda d: d.readiness())

    @app.get("/api/learning")
    def learning() -> Any:
        return _safe(lambda d: d.learning())

    @app.get("/api/activity")
    def activity() -> Any:
        return _safe(lambda d: d.activity())

    @app.get("/api/gates")
    def gates() -> Any:
        return _safe(lambda d: d.gates())

    @app.get("/api/learning-summary")
    def learning_summary() -> Any:
        return _safe(lambda d: d.learning_summary())

    @app.get("/api/predictions")
    def predictions() -> Any:
        return _safe(lambda d: d.predictions())

    @app.get("/api/paper-trades")
    def paper_trades() -> Any:
        return _safe(lambda d: d.paper())

    @app.get("/api/daily-reports")
    def daily_reports() -> Any:
        return _safe(lambda d: d.daily_reports())

    @app.get("/api/symbols/{symbol}")
    def symbol_detail_plural(symbol: str) -> Any:
        return _safe(lambda d: d.symbol_detail(symbol))

    @app.get("/api/health")
    def health() -> JSONResponse:
        return JSONResponse({"ok": True, "real_trading": False,
                             "paper_only": True, "wallets": False,
                             "bank_transfers": False})

    async def _ws_loop(socket: WebSocket) -> None:
        await socket.accept()
        try:
            while True:
                payload = {
                    "overview": _safe(lambda d: d.overview()),
                    "status": _safe(lambda d: d.status()),
                    "progress": _safe(lambda d: d.progress()),
                }
                await socket.send_text(json.dumps(payload, default=str))
                await asyncio.sleep(4.0)
        except WebSocketDisconnect:
            return
        except Exception:  # noqa: BLE001 - never crash the server on a socket
            return

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        """Push overview + status + progress every few seconds."""
        await _ws_loop(socket)

    @app.websocket("/ws/live")
    async def ws_live(socket: WebSocket) -> None:
        """Alias of /ws (the endpoint name from the spec)."""
        await _ws_loop(socket)

    return app


# Module-level app for ``uvicorn daytrade.dashboard.app:app``.
app = create_app()
