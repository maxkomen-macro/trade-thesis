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
    avg_thesis_pnl_pct: float | None = None
    positions: list[dict[str, Any]] = []  # idea id, symbol, name, both P&Ls, exit reason


class Divergence(BaseModel):
    available: bool
    note: str
    resolved_positions: int = 0
    open_positions: int = 0
    option_beat_thesis: int = 0
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


CELLS = (
    "thesis_right_option_won",
    "thesis_right_option_lost",
    "thesis_wrong_option_won",
    "thesis_wrong_option_lost",
)


def divergence_matrix(ideas: list[Idea]) -> Divergence:
    """Four cells from closed positions on resolved ideas (Phase 6). Thesis right/wrong is the direction flag of the
    idea; option won/lost is the sign of the position's return on premium. Every closed position counts once."""
    cells: dict[str, list[tuple[Idea, Any]]] = {k: [] for k in CELLS}
    open_count = resolved = beat = 0
    for idea in ideas:
        for pos in idea.positions or []:
            if pos.status == "open":
                open_count += 1
                continue
            d = ledger.divergence_of(idea, pos)
            if not d or not d["cell"]:
                continue
            resolved += 1
            beat += 1 if d.get("option_beat_thesis") else 0
            cells[d["cell"]].append((idea, pos))

    def cell(key: str) -> DivergenceCell:
        rows = cells[key]
        opt = [p.pnl_pct for _, p in rows if p.pnl_pct is not None]
        th = [i.hypothetical_pnl_pct for i, _ in rows if i.hypothetical_pnl_pct is not None]
        return DivergenceCell(
            count=len(rows),
            avg_option_pnl_pct=round(sum(opt) / len(opt), 1) if opt else None,
            avg_thesis_pnl_pct=round(sum(th) / len(th), 2) if th else None,
            positions=[
                {
                    "idea_id": i.id,
                    "symbol": i.instrument.symbol,
                    "name": p.name,
                    "thesis_pnl_pct": i.hypothetical_pnl_pct,
                    "option_pnl_pct": p.pnl_pct,
                    "exit_reason": p.exit_reason,
                    "idea_reason": i.resolution_reason,
                }
                for i, p in rows
            ],
        )

    if resolved == 0:
        if open_count:
            plural = "s" if open_count != 1 else ""
            note = f"{open_count} open position{plural} marking daily; the matrix fills in as they close."
        else:
            note = "Fills in once an expression has been taken and closed. Take one from the Expression page."
    else:
        rl = cells["thesis_right_option_lost"]
        note = f"{resolved} resolved position{'s' if resolved != 1 else ''}: the option beat the thesis in {beat}. " + (
            f"{len(rl)} where the call was right and the contract still lost "
            f"({', '.join(sorted({(p.exit_reason or '?') for _, p in rl}))})."
            if rl
            else "No right-call, wrong-contract trades yet."
        )
    return Divergence(
        available=resolved > 0,
        note=note,
        resolved_positions=resolved,
        open_positions=open_count,
        option_beat_thesis=beat,
        **{k: cell(k) for k in CELLS},
    )


@router.get("/review", response_model=ReviewOut)
def review(request: Request, db: Session = Depends(get_db)) -> ReviewOut:
    hide = hide_dollars_for(request)
    all_ideas = (
        db.execute(select(Idea).options(selectinload(Idea.instrument), selectinload(Idea.positions))).scalars().all()
    )
    ideas = [i for i in all_ideas if not ledger.is_seed(i)]
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
        divergence=divergence_matrix(ideas),
    )
