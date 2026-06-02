"""Shared base model and timestamp normalization for all domain models."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict


def normalize_timestamp(value: Any) -> datetime:
    """Coerce many timestamp representations to a timezone-aware UTC datetime.

    Accepts:
      * ``datetime`` (naive is assumed UTC; aware is converted to UTC)
      * ``int`` / ``float`` epoch — seconds, milliseconds or microseconds,
        auto-detected by magnitude
      * ISO-8601 ``str``

    Normalizing here means every model downstream can rely on tz-aware UTC
    timestamps, which is a precondition for correct, lookahead-free ordering.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    if isinstance(value, bool):  # bool is an int subclass — reject explicitly
        raise TypeError("timestamp cannot be a bool")

    if isinstance(value, (int, float)):
        epoch = float(value)
        # Auto-detect units by magnitude. Year ~2001 in seconds ~1e9.
        if epoch >= 1e17:  # nanoseconds
            epoch /= 1e9
        elif epoch >= 1e14:  # microseconds
            epoch /= 1e6
        elif epoch >= 1e11:  # milliseconds
            epoch /= 1e3
        return datetime.fromtimestamp(epoch, tz=timezone.utc)

    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    raise TypeError(f"cannot normalize timestamp from {type(value).__name__}")


class DaytradeModel(BaseModel):
    """Base for every domain model.

    * immutable (``frozen``) — domain objects are values, not mutable state,
      which removes a whole class of accidental-mutation bugs in the pipeline
    * extra fields rejected — typos in dict input fail loudly
    * validated on assignment / construction
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_assignment=True,
        ser_json_timedelta="float",
    )

    def to_json(self, *, indent: int | None = None) -> str:
        """Serialize to a JSON string (datetimes become ISO-8601)."""
        return self.model_dump_json(indent=indent)

    @classmethod
    def from_json(cls, raw: str | bytes) -> DaytradeModel:
        """Parse from a JSON string/bytes."""
        return cls.model_validate_json(raw)
