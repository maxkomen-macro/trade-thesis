"""Success / invalidation rule schema: a small discriminated union validated with Pydantic.

    {"type":"level","comparator":"close_at_or_below","level":68.5}
        comparators: close_at_or_below | close_at_or_above | touch_at_or_below | touch_at_or_above
    {"type":"direction"}                       right if the window-end close is on the stated side of entry_price
    {"type":"pct_move","pct":4.0}               right if any close inside the window moved >= pct in the
                                               stated direction
    {"type":"relative","benchmark":"SPY.US","spread_pct":3.0}
                                               right if (instrument return - benchmark return), signed by direction,
                                               reaches spread_pct at any close on or before window_end
    {"type":"all_of","rules":[...]} / {"type":"any_of","rules":[...]}

Extra keys such as `suggested: true` (parser proposal) or `seed: true` (placeholder) are preserved.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

Comparator = Literal["close_at_or_below", "close_at_or_above", "touch_at_or_below", "touch_at_or_above"]


class _RuleBase(BaseModel):
    model_config = ConfigDict(extra="allow")


class LevelRule(_RuleBase):
    type: Literal["level"]
    comparator: Comparator
    level: float = Field(gt=0)


class DirectionRule(_RuleBase):
    type: Literal["direction"]


class PctMoveRule(_RuleBase):
    type: Literal["pct_move"]
    pct: float = Field(gt=0)


class RelativeRule(_RuleBase):
    type: Literal["relative"]
    benchmark: str
    spread_pct: float = Field(ge=0)


class AllOfRule(_RuleBase):
    type: Literal["all_of"]
    rules: list[Rule] = Field(min_length=1)


class AnyOfRule(_RuleBase):
    type: Literal["any_of"]
    rules: list[Rule] = Field(min_length=1)


Rule = Annotated[
    LevelRule | DirectionRule | PctMoveRule | RelativeRule | AllOfRule | AnyOfRule, Field(discriminator="type")
]
AllOfRule.model_rebuild()
AnyOfRule.model_rebuild()

_adapter: TypeAdapter[Any] = TypeAdapter(Rule)


def validate_rule(raw: dict[str, Any]) -> dict[str, Any]:
    """Raise pydantic.ValidationError on a malformed rule; return it normalized (extras kept)."""
    return _adapter.validate_python(raw).model_dump()


def validate_invalidation(raw: dict[str, Any]) -> dict[str, Any]:
    """Invalidation rules are level rules only."""
    return LevelRule.model_validate(raw).model_dump()


def first_level(rule: dict[str, Any] | None) -> float | None:
    """The first price level inside a rule tree (for chart lines and ledger progress)."""
    if not rule:
        return None
    if rule.get("type") == "level":
        return float(rule["level"])
    for child in rule.get("rules", []) or []:
        lvl = first_level(child)
        if lvl is not None:
            return lvl
    return None


def benchmarks_in(rule: dict[str, Any] | None) -> set[str]:
    if not rule:
        return set()
    out: set[str] = set()
    if rule.get("type") == "relative":
        out.add(rule["benchmark"])
    for child in rule.get("rules", []) or []:
        out |= benchmarks_in(child)
    return out


def describe(rule: dict[str, Any] | None, direction: str = "up") -> str:
    """Plain-English rule description for the UI (deterministic, no LLM)."""
    if not rule:
        return "none"
    t = rule.get("type")
    if t == "level":
        words = {
            "close_at_or_below": "closes at or below",
            "close_at_or_above": "closes at or above",
            "touch_at_or_below": "touches",
            "touch_at_or_above": "touches",
        }
        return f"{words[rule['comparator']]} {rule['level']:g}"
    if t == "direction":
        side = {"up": "above", "down": "below", "outperform": "above", "underperform": "below"}.get(
            direction, "away from"
        )
        return f"window-end close {side} entry"
    if t == "pct_move":
        return f"moves {rule['pct']:g}% {'up' if direction in ('up', 'outperform') else 'down'} at any close"
    if t == "relative":
        verb = "outperforms" if direction in ("up", "outperform") else "underperforms"
        return f"{verb} {rule['benchmark']} by {rule['spread_pct']:g}%"
    if t in ("all_of", "any_of"):
        joiner = " and " if t == "all_of" else " or "
        return "(" + joiner.join(describe(r, direction) for r in rule.get("rules", [])) + ")"
    return str(rule)
