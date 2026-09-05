"""Thesis parser: prose -> thesis schema. The only place the Anthropic API is called for parsing.

Division of labour (CLAUDE.md rule 3): the model converts prose into structured fields and suggests rules;
everything numeric that reaches the UI (last close, realized vol, distance to target, regime) is computed in
Python from stored EODHD snapshots. The model never sees or produces market numbers.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

import anthropic
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from api.config import settings
from api.db.models import Instrument
from api.services import ledger, prices
from api.services.eodhd import EODHDClient, EODHDError, eodhd_symbol
from api.services.radar import latest_regime, readout
from api.services.rules import describe, first_level, validate_invalidation, validate_rule

log = logging.getLogger("tt.parser")

PARSER_MODEL = "claude-sonnet-4-6"  # per the project spec; swap via settings.parser_model if ever needed
MAX_TOKENS = 4096
REALIZED_VOL_WINDOW = 20
TRADEABLE_TYPES = {"Common Stock", "ETF", "Preferred Stock", "Index"}  # EODHD search `Type`; drops mutual funds

# --- what the model returns (structured output schema) -------------------------------------------------------------
# Kept deliberately flat: no nested objects, no `X | None` unions, small enums. Anthropic's structured-output
# grammar rejected a nested/optional version with "Schema is too complex" (2026-09-05). "" and 0 mean "not stated".

RuleType = Literal["level", "direction", "pct_move", "relative", "none"]
ComparatorOrNone = Literal["close_at_or_below", "close_at_or_above", "touch_at_or_below", "touch_at_or_above", "none"]
Direction = Literal["up", "down", "outperform", "underperform", "range"]


class ParsedThesisLLM(BaseModel):
    title: str = Field(description="Short title, at most 80 characters, no ticker prefix")
    instrument_query: str = Field(description="The instrument exactly as the author referred to it")
    instrument_symbol_guess: str = Field(
        description="US ticker without exchange suffix if confident, else empty string"
    )
    instrument_kind: str = Field(description="stock | etf | sector_proxy | commodity_etf | fx_etf | index, or empty")
    direction: Direction
    benchmark_query: str = Field(description="Benchmark for relative ideas as written, else empty string")
    benchmark_symbol_guess: str = Field(description="US ticker guess for the benchmark, else empty string")
    success_type: RuleType
    success_comparator: ComparatorOrNone
    success_level: float = Field(description="Price level for a level rule, else 0")
    success_pct: float = Field(description="Percent move for a pct_move rule, else 0")
    success_spread_pct: float = Field(description="Spread in percent for a relative rule, else 0")
    stop_comparator: ComparatorOrNone
    stop_level: float = Field(description="Invalidation price level, else 0")
    invalidation_is_note_only: bool
    window_start: str = Field(description="ISO date; today's date if the author starts now")
    window_end: str = Field(description="ISO date, or empty string if no horizon was stated")
    catalyst_date: str = Field(description="ISO date or empty string")
    catalyst_note: str
    conviction_pct: float = Field(description="Stated odds in percent, else 0")
    tags: list[str]
    questions: list[str] = Field(
        description="One entry per missing field, formatted 'field: question', fields among success_rule, "
        "invalidation_rule, window_end, catalyst_date, conviction_pct, instrument, benchmark"
    )


class Question(BaseModel):
    field: str
    question: str


def _questions(llm: ParsedThesisLLM) -> list[dict[str, str]]:
    out = []
    for q in llm.questions:
        field, _, text = q.partition(":")
        field, text = field.strip(), text.strip().replace(" \u2014 ", ", ").replace("\u2014", ", ")
        if field and text:
            out.append({"field": field, "question": text})
    return out


SYSTEM_PROMPT = """You turn a trader's plain-English thesis into structured fields. Output only the schema.

Rules:
- Never invent a target level, a stop level, a window, or a conviction number. If the text does not state one, leave
  the field null and add a question for it (field names: success_rule, invalidation_rule, window_end, catalyst_date,
  conviction_pct, instrument, benchmark).
- Instruments: map company names and plain-language instruments to a US ticker guess when you are confident
  (crude oil ETF -> USO, energy sector -> XLE, financials -> XLF, technology -> XLK, semiconductors -> SMH,
  yen ETF -> FXY, gold ETF -> GLD, S&P 500 -> SPY, Nasdaq 100 -> QQQ, Micron -> MU, Lockheed Martin -> LMT).
  Sector ideas must use the proxy ETF as the instrument. Leave the guess null when ambiguous and ask.
- Direction: up / down for outright ideas; outperform / underperform when the thesis is relative to a benchmark
  (then fill benchmark_query); range when the author expects no move.
- Success rule from the prose, marked as a suggestion: "breaks 68.50" on a bearish idea -> level close_at_or_below
  68.5; "reaches 142" on a bullish idea -> level close_at_or_above 142; "touches" -> touch_* comparators;
  "moves 4%" -> pct_move 4; "outperforms energy by 3%" -> relative with benchmark_query "energy sector ETF" and
  spread_pct 3; "up by the end of the window" with no level -> direction.
- Invalidation: only a level rule ("wrong if it closes back above 74" -> close_at_or_above 74). If the author says
  the stop is just a note, set invalidation_is_note_only true.
- Windows: convert relative phrases ("next three weeks", "into earnings on Oct 22") to ISO dates using today's date
  given in the message. window_start is today unless the author says otherwise.
- Tags: 2 to 5 lowercase tags (sector, style such as technical / macro / earnings / momentum / mean-reversion).
- Title: at most 80 characters, no ticker prefix, no trailing period.
- Unknown strings are "" and unknown numbers are 0; success_type "none" and comparator "none" mean not stated.
- Questions are one plain sentence each, no em dashes, no parentheses.
"""


# --- model call ---


def _call_model(thesis_text: str, today: date) -> ParsedThesisLLM:
    """Single structured-output request. Kept separate so tests can replace it."""
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=60.0, max_retries=1)
    response = client.messages.parse(
        model=PARSER_MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Today is {today.isoformat()}.\n\nThesis:\n{thesis_text.strip()}"}],
        output_format=ParsedThesisLLM,
    )
    parsed = response.parsed_output
    if parsed is None:
        raise RuntimeError(f"Parser returned no structured output (stop_reason={response.stop_reason})")
    log.info(
        "parser tokens in=%s out=%s request_id=%s",
        response.usage.input_tokens,
        response.usage.output_tokens,
        getattr(response, "_request_id", None),
    )
    return parsed


# --- symbol verification via EODHD search -----------------------------------------------------------------------------


def _candidates(client: EODHDClient, query: str | None, guess: str | None) -> list[dict[str, Any]]:
    """EODHD search hits for a guess and/or the author's phrase, US exchange only, deduplicated by symbol."""
    seen: dict[str, dict[str, Any]] = {}
    for q in [g for g in (guess, query) if g]:
        try:
            hits = client.search(q, limit=10)
        except EODHDError as exc:
            log.warning("search %r failed: %s", q, exc)
            continue
        for h in hits:
            if str(h.get("Exchange")) != "US" or str(h.get("Type", "")) not in TRADEABLE_TYPES:
                continue
            sym = eodhd_symbol(str(h["Code"]), "US")
            seen.setdefault(
                sym,
                {
                    "symbol": sym,
                    "name": str(h.get("Name", "")),
                    "type": str(h.get("Type", "")),
                    "previous_close": h.get("previousClose") if h.get("previousClose") not in (None, "NA") else None,
                    "previous_close_date": h.get("previousCloseDate"),
                },
            )
    return list(seen.values())


def resolve_symbol(
    client: EODHDClient, query: str | None, guess: str | None
) -> tuple[str | None, list[dict[str, Any]]]:
    """(symbol, candidates). Accept only when the guess matches a US hit exactly, or exactly one US hit exists."""
    cands = _candidates(client, query, guess)
    if guess:
        exact = [c for c in cands if c["symbol"] == eodhd_symbol(guess.upper(), "US")]
        if exact:
            return exact[0]["symbol"], cands
    if len(cands) == 1:
        return cands[0]["symbol"], cands
    return None, cands


def _kind_from(candidate: dict[str, Any] | None, llm_kind: str | None) -> str:
    if llm_kind:
        return llm_kind
    if candidate and candidate.get("type", "").upper() == "ETF":
        return "etf"
    return "stock"


# --- context tile (deterministic, from stored snapshots) --------------------------------------------------------------


def realized_vol_pct(closes: list[float], window: int = REALIZED_VOL_WINDOW) -> float | None:
    """Annualized std of daily log returns over the last `window` returns, in %. None with too few bars."""
    if len(closes) < window + 1:
        return None
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - window, len(closes))]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return round(math.sqrt(var) * math.sqrt(252) * 100.0, 2)


def context_for(
    db: Session, inst: Instrument, target: float | None, client: EODHDClient, today: date
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "symbol": inst.symbol,
        "last_close": None,
        "last_close_at": None,
        "last_close_source": None,
        "realized_vol_20d_pct": None,
        "realized_vol_as_of": None,
        "distance_to_target_pct": None,
        "errors": [],
    }
    try:
        price, at, src = prices.stamp_entry(db, inst, client)
        out["last_close"], out["last_close_at"], out["last_close_source"] = price, at.isoformat(), src
    except EODHDError as exc:
        out["errors"].append({"what": "last_close", **exc.to_dict()})
    try:
        prices.ensure_eod(db, inst, today - timedelta(days=60), today, client)
        bars = prices.bars(db, inst.id, today - timedelta(days=60), today)
        vol = realized_vol_pct([b.close for b in bars])
        out["realized_vol_20d_pct"] = vol
        out["realized_vol_as_of"] = bars[-1].as_of.isoformat() if bars and vol is not None else None
    except EODHDError as exc:
        out["errors"].append({"what": "realized_vol", **exc.to_dict()})
    if target is not None and out["last_close"]:
        out["distance_to_target_pct"] = round((target / out["last_close"] - 1.0) * 100.0, 2)
    return out


# --- assembly ---


def _success_rule(llm: ParsedThesisLLM, benchmark_symbol: str | None) -> dict[str, Any] | None:
    t = llm.success_type
    if t == "level" and llm.success_comparator != "none" and llm.success_level > 0:
        raw: dict[str, Any] = {"type": "level", "comparator": llm.success_comparator, "level": llm.success_level}
    elif t == "direction":
        raw = {"type": "direction"}
    elif t == "pct_move" and llm.success_pct > 0:
        raw = {"type": "pct_move", "pct": llm.success_pct}
    elif t == "relative" and llm.success_spread_pct >= 0 and benchmark_symbol:
        raw = {"type": "relative", "benchmark": benchmark_symbol, "spread_pct": llm.success_spread_pct}
    else:
        return None
    raw["suggested"] = True
    try:
        return validate_rule(raw)
    except ValidationError as exc:
        log.warning("model suggested an invalid rule %r: %s", raw, exc)
        return None


def _invalidation_rule(llm: ParsedThesisLLM) -> dict[str, Any] | None:
    if llm.stop_comparator == "none" or llm.stop_level <= 0:
        return None
    try:
        return validate_invalidation(
            {"type": "level", "comparator": llm.stop_comparator, "level": llm.stop_level, "suggested": True}
        )
    except ValidationError as exc:
        log.warning("model suggested an invalid stop: %s", exc)
        return None


def _iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def parse_thesis(
    db: Session, thesis_text: str, today: date | None = None, client: EODHDClient | None = None
) -> dict[str, Any]:
    today = today or datetime.now(UTC).date()
    client = client or EODHDClient()
    llm = _call_model(thesis_text, today)
    questions: list[dict[str, str]] = _questions(llm)

    symbol, candidates = resolve_symbol(client, llm.instrument_query, llm.instrument_symbol_guess or None)
    chosen = next((c for c in candidates if c["symbol"] == symbol), None)
    if symbol is None and not any(q["field"] == "instrument" for q in questions):
        questions.append(
            {
                "field": "instrument",
                "question": (
                    f"Which instrument is '{llm.instrument_query}'? Pick one of the EODHD matches or type a symbol."
                ),
            }
        )

    benchmark_symbol: str | None = None
    benchmark_candidates: list[dict[str, Any]] = []
    if llm.direction in ("outperform", "underperform") or llm.success_type == "relative":
        bq = llm.benchmark_query or None
        benchmark_symbol, benchmark_candidates = resolve_symbol(client, bq, llm.benchmark_symbol_guess or None)
        if benchmark_symbol is None and not any(q["field"] == "benchmark" for q in questions):
            questions.append({"field": "benchmark", "question": f"Which benchmark is '{bq or 'the benchmark'}'?"})

    success_rule = _success_rule(llm, benchmark_symbol)
    if success_rule is None and not any(q["field"] == "success_rule" for q in questions):
        questions.append({"field": "success_rule", "question": "What level, move, or spread counts as right?"})
    invalidation_rule = _invalidation_rule(llm)
    if invalidation_rule is None and not any(q["field"] == "invalidation_rule" for q in questions):
        questions.append(
            {"field": "invalidation_rule", "question": "Where is the idea wrong? A price level, or no stop?"}
        )

    window_start = _iso(llm.window_start or None) or today.isoformat()
    window_end = _iso(llm.window_end or None)
    if window_end is None and not any(q["field"] == "window_end" for q in questions):
        questions.append({"field": "window_end", "question": "By when? The idea needs an end date to be scored."})

    context: dict[str, Any] | None = None
    instrument_out: dict[str, Any] | None = None
    if symbol:
        try:
            inst = ledger.get_or_create_instrument(
                db, symbol, chosen["name"] if chosen else None, _kind_from(chosen, llm.instrument_kind or None), client
            )
            instrument_out = {
                "id": inst.id,
                "symbol": inst.symbol,
                "display_name": inst.display_name,
                "kind": inst.kind,
            }
            context = context_for(db, inst, first_level(success_rule), client, today)
        except EODHDError as exc:
            context = {"symbol": symbol, "errors": [{"what": "instrument", **exc.to_dict()}]}

    regime = readout(latest_regime(db))
    return {
        "thesis_text": thesis_text,
        "title": llm.title[:200],
        "instrument": instrument_out,
        "symbol": symbol,
        "symbol_candidates": candidates,
        "instrument_query": llm.instrument_query,
        "direction": llm.direction,
        "benchmark_symbol": benchmark_symbol,
        "benchmark_candidates": benchmark_candidates,
        "success_rule_json": success_rule,
        "success_rule_text": describe(success_rule, llm.direction) if success_rule else None,
        "invalidation_rule_json": invalidation_rule,
        "invalidation_rule_text": describe(invalidation_rule, llm.direction) if invalidation_rule else None,
        "invalidation_is_note_only": llm.invalidation_is_note_only,
        "window_start": window_start,
        "window_end": window_end,
        "catalyst_date": _iso(llm.catalyst_date or None),
        "catalyst_note": llm.catalyst_note or None,
        "conviction_pct": llm.conviction_pct if llm.conviction_pct > 0 else None,
        "tags": sorted({t.strip().lower() for t in llm.tags if t.strip()}),
        "questions": questions,
        "context": context,
        "regime": regime.model_dump(mode="json"),
        "parsed_json": {"model": PARSER_MODEL, "parsed_at": datetime.now(UTC).isoformat(), "llm": llm.model_dump()},
    }
