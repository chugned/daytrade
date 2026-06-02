"""Run mission control: python -m daytrade.mission_control --port 8002"""

from __future__ import annotations

import argparse

import uvicorn

from .app import create_app


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="0.0.0.0",
                   help="Bind interface. Default 0.0.0.0 so Tailscale "
                        "peers can reach it.")
    p.add_argument("--port", type=int, default=8002,
                   help="Port. Default 8002 (8000 = daytrade, 8001 = "
                        "nighttrade, 8002 = mission-control).")
    args = p.parse_args()
    uvicorn.run(create_app(), host=args.host, port=args.port,
                log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
