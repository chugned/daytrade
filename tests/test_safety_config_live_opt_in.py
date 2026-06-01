"""Tests for the SafetyConfig live-trading two-key opt-in.

The validator should refuse:
  - Any live flag set without the acknowledgement phrase
  - The acknowledgement phrase set without all the live flags
  - Partial flag flips (one or two of the three flags, but not all)

It should ACCEPT only the fully-aligned configuration:
  live_trading_enabled=True
  allow_real_orders=True
  paper_trading=False
  live_acknowledgement=LIVE_ACKNOWLEDGEMENT_PHRASE
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from daytrade.config.schema import LIVE_ACKNOWLEDGEMENT_PHRASE, SafetyConfig


# ---------------------------------------------------------------------------
# Default = paper. is_live should be False.
# ---------------------------------------------------------------------------


def test_default_is_paper():
    cfg = SafetyConfig()
    assert cfg.live_trading_enabled is False
    assert cfg.allow_real_orders is False
    assert cfg.paper_trading is True
    assert cfg.live_acknowledgement == ""
    assert cfg.is_live is False


# ---------------------------------------------------------------------------
# Flag-flips without acknowledgement → refused
# ---------------------------------------------------------------------------


def test_live_trading_enabled_alone_refused():
    with pytest.raises(ValidationError, match="live_acknowledgement"):
        SafetyConfig(live_trading_enabled=True)


def test_allow_real_orders_alone_refused():
    with pytest.raises(ValidationError, match="live_acknowledgement"):
        SafetyConfig(allow_real_orders=True)


def test_paper_trading_off_alone_refused():
    with pytest.raises(ValidationError, match="live_acknowledgement"):
        SafetyConfig(paper_trading=False)


def test_two_flags_without_ack_refused():
    with pytest.raises(ValidationError, match="live_acknowledgement"):
        SafetyConfig(live_trading_enabled=True, allow_real_orders=True)


def test_three_flags_without_ack_refused():
    with pytest.raises(ValidationError, match="live_acknowledgement"):
        SafetyConfig(
            live_trading_enabled=True,
            allow_real_orders=True,
            paper_trading=False,
        )


# ---------------------------------------------------------------------------
# Acknowledgement without flags → also refused (half-finished edit)
# ---------------------------------------------------------------------------


def test_ack_without_flags_refused():
    with pytest.raises(ValidationError, match="no live-trading flags"):
        SafetyConfig(live_acknowledgement=LIVE_ACKNOWLEDGEMENT_PHRASE)


# ---------------------------------------------------------------------------
# Partial flag-flips with correct ack → refused
# ---------------------------------------------------------------------------


def test_partial_flags_with_correct_ack_refused():
    with pytest.raises(ValidationError, match="CONSISTENTLY"):
        SafetyConfig(
            live_trading_enabled=True,
            allow_real_orders=False,
            paper_trading=False,
            live_acknowledgement=LIVE_ACKNOWLEDGEMENT_PHRASE,
        )


def test_paper_still_true_with_correct_ack_refused():
    with pytest.raises(ValidationError):
        SafetyConfig(
            live_trading_enabled=True,
            allow_real_orders=True,
            paper_trading=True,  # not flipped
            live_acknowledgement=LIVE_ACKNOWLEDGEMENT_PHRASE,
        )


# ---------------------------------------------------------------------------
# Wrong acknowledgement string → refused
# ---------------------------------------------------------------------------


def test_typo_in_ack_refused():
    bad = LIVE_ACKNOWLEDGEMENT_PHRASE.replace("real money", "real cash")
    with pytest.raises(ValidationError, match="live_acknowledgement"):
        SafetyConfig(
            live_trading_enabled=True,
            allow_real_orders=True,
            paper_trading=False,
            live_acknowledgement=bad,
        )


def test_lowercase_ack_refused():
    with pytest.raises(ValidationError):
        SafetyConfig(
            live_trading_enabled=True,
            allow_real_orders=True,
            paper_trading=False,
            live_acknowledgement=LIVE_ACKNOWLEDGEMENT_PHRASE.lower(),
        )


def test_truncated_ack_refused():
    with pytest.raises(ValidationError):
        SafetyConfig(
            live_trading_enabled=True,
            allow_real_orders=True,
            paper_trading=False,
            live_acknowledgement=LIVE_ACKNOWLEDGEMENT_PHRASE[:30],
        )


# ---------------------------------------------------------------------------
# Fully-aligned configuration → accepted; is_live = True
# ---------------------------------------------------------------------------


def test_fully_aligned_config_accepted():
    cfg = SafetyConfig(
        live_trading_enabled=True,
        allow_real_orders=True,
        paper_trading=False,
        live_acknowledgement=LIVE_ACKNOWLEDGEMENT_PHRASE,
    )
    assert cfg.is_live is True


def test_is_live_false_when_any_field_missing():
    """Even when constructed with all four args, mutating one to a wrong
    value AFTER construction would normally bypass validation — but the
    section is frozen, so that's impossible. Verify."""
    cfg = SafetyConfig(
        live_trading_enabled=True,
        allow_real_orders=True,
        paper_trading=False,
        live_acknowledgement=LIVE_ACKNOWLEDGEMENT_PHRASE,
    )
    assert cfg.is_live is True
    with pytest.raises(ValidationError):
        # Frozen section — assignment is refused.
        cfg.live_trading_enabled = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# The phrase itself is non-trivial — verify it has the load-bearing content
# ---------------------------------------------------------------------------


def test_phrase_mentions_real_money_and_max_loss():
    assert "real money" in LIVE_ACKNOWLEDGEMENT_PHRASE.lower()
    assert "real orders" in LIVE_ACKNOWLEDGEMENT_PHRASE.lower()
    assert "maximum loss" in LIVE_ACKNOWLEDGEMENT_PHRASE.lower()


def test_phrase_long_enough_to_be_deliberate():
    # 50+ chars is a reasonable floor against accidental auto-completion.
    assert len(LIVE_ACKNOWLEDGEMENT_PHRASE) > 50
