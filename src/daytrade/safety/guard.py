"""The real-trading guard.

This module is the single, intentional choke point for live execution. Any
code that represents a boundary to a real exchange order calls
``forbid_real_trading()``, which always raises.
"""

from __future__ import annotations

from .. import REAL_TRADING_ENABLED

_REAL_TRADING_DISABLED_MESSAGE = "Real trading is disabled."


def forbid_real_trading(context: str = "") -> None:
    """Always raise — there is no code path in this platform that places a
    real order, holds leverage, or stores withdrawal credentials.

    Args:
        context: optional caller description, included in the error message.

    Raises:
        NotImplementedError: unconditionally.
    """
    suffix = f" ({context})" if context else ""
    raise NotImplementedError(_REAL_TRADING_DISABLED_MESSAGE + suffix)


def assert_paper_only() -> None:
    """Defensive invariant check: the build-time flag must be False.

    Called by execution components at construction time. If this ever fails
    the package has been tampered with.
    """
    if REAL_TRADING_ENABLED:  # pragma: no cover - cannot happen in a clean tree
        raise RuntimeError(
            "REAL_TRADING_ENABLED is True — refusing to run. " + _REAL_TRADING_DISABLED_MESSAGE
        )
