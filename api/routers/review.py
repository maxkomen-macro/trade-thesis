"""Review breakdowns. Placeholder (seed) ideas are excluded from every figure. Dollar totals follow hide_dollars."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from api.db.models import Idea
from api.db.session import get_db
from api.routers.ideas import hide_dollars_for
from api.services import ledger

router = APIRouter(prefix="/api", tags=["review"])

RESOLVED = ("right", "wrong", "expired")


class Bucket(BaseModel):
    key: str
    ideas: int
    resolved: int
    right: int
    wrong: int
    expired: int
    direction_hit_rate: float | None
    target_hit_rate: float | None
    avg_pnl_pct: float | None
    total_pnl_abs: float | None  # null when dollars are hidden


class DivergenceCell(BaseModel):
    count: int
    avg_option_pnl_pct: float | None


class Divergence(BaseModel):
    available: bool
    note: str
    thesis_right_option_won: DivergenceCell
    thesis_right_option_lost: DivergenceCell
    thesis_wrong_option_won: DivergenceCell
    thesis_wrong_option_lost: DivergenceCell


class ReviewOut(BaseModel):
    ideas: int
    seed_count: int
    dollars_hidden: bool
    by_outcome: list[Bucket]
    by_tag: list[Bucket]
    by_regime: list[Bucket]
    by_idea_type: list[Bucket]
    by_rule_type: list[Bucket]
    divergence: Divergence


def rule_type_of(rule: dict[str, Any] | None) -> str:
    if not rule:
        return "none"
    t = str(rule.get("type", "none"))
    return "compound" if t in ("all_of", "any_of") else t


def _bucket(key: str, group: list[Idea], hide: bool) -> Bucket:
    res = [i for i in group if i.status in RESOLVED]
    flags = [f for f in (ledger.direction_right_of(i) for i in res) if f is not None]
    pnl_pct = [i.hypothetical_pnl_pct for i in group if i.hypothetical_pnl_pct is not None]
    pnl_abs = [i.hypothetical_pnl_abs for i in group if i.hypothetical_pnl_abs is not None]
    return Bucket(
        key=key,
        ideas=len(group),
        resolved=len(res),
        right=sum(1 for i in group if i.status == "right"),
        wrong=sum(1 for i in group if i.status == "wrong"),
        expired=sum(1 for i in group if i.status == "expired"),
        direction_hit_rate=round(sum(flags) / len(flags) * 100, 1) if flags else None,
        target_hit_rate=round(sum(1 for i in res if i.status == "right") / len(res) * 100, 1) if res else None,
        avg_pnl_pct=round(sum(pnl_pct) / len(pnl_pct), 2) if pnl_pct else None,
        total_pnl_abs=None if hide else (round(sum(pnl_abs), 2) if pnl_abs else None),
    )


def _grouped(ideas: list[Idea], keyfn, hide: bool, order: list[str] | None = None) -> list[Bucket]:
    groups: dict[str, list[Idea]] = defaultdict(list)
    for i in ideas:
        keys = keyfn(i)
        for k in keys if isinstance(keys, list | set | tuple) else [keys]:
            groups[k].append(i)
    names = order or sorted(groups)
    return [_bucket(k, groups[k], hide) for k in names if k in groups]


@router.get("/review", response_model=ReviewOut)
def review(request: Request, db: Session = Depends(get_db)) -> ReviewOut:
    hide = hide_dollars_for(request)
    all_ideas = db.execute(select(Idea).options(selectinload(Idea.instrument))).scalars().all()
    ideas = [i for i in all_ideas if not ledger.is_seed(i)]
    empty = DivergenceCell(count=0, avg_option_pnl_pct=None)
    return ReviewOut(
        ideas=len(ideas),
        seed_count=len(all_ideas) - len(ideas),
        dollars_hidden=hide,
        by_outcome=_grouped(ideas, lambda i: i.status, hide, ["open", "right", "wrong", "expired", "closed_manual"]),
        by_tag=_grouped(ideas, lambda i: list(i.tags or []) or ["untagged"], hide),
        by_regime=_grouped(ideas, lambda i: i.radar_regime or "Unknown", hide),
        by_idea_type=_grouped(ideas, lambda i: i.idea_type, hide, ["real", "paper"]),
        by_rule_type=_grouped(
            ideas,
            lambda i: rule_type_of(i.success_rule_json),
            hide,
            ["level", "pct_move", "relative", "direction", "compound"],
        ),
        divergence=Divergence(
            available=False,
            note="Thesis-versus-option divergence fills in once option positions exist (Phase 6).",
            thesis_right_option_won=empty,
            thesis_right_option_lost=empty,
            thesis_wrong_option_won=empty,
            thesis_wrong_option_lost=empty,
        ),
    )
