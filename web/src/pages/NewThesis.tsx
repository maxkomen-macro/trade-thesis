import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, getWriteToken } from "../lib/api";
import type { Direction, IdeaDetail, ParseResponse, SystemStatus } from "../lib/types";
import { fmtDate, fmtMoney, fmtPct, fmtPrice, regimeColor } from "../lib/format";
import { Notice } from "../components/Panel";

/** Layout follows design/trade-thesis-mockup.html, "New thesis" screen: prose on the left with a context tile,
 *  parsed fields on the right with unanswered questions inline in amber. Every field is editable before saving. */

type RuleKind = "level" | "direction" | "pct_move" | "relative" | "none";
const COMPARATORS = ["close_at_or_below", "close_at_or_above", "touch_at_or_below", "touch_at_or_above"] as const;
const COMPARATOR_LABEL: Record<string, string> = {
  close_at_or_below: "close ≤",
  close_at_or_above: "close ≥",
  touch_at_or_below: "touch ≤",
  touch_at_or_above: "touch ≥",
};

interface Draft {
  symbol: string;
  title: string;
  direction: Direction;
  benchmark_symbol: string;
  ruleKind: RuleKind;
  comparator: string;
  level: string;
  pct: string;
  spread_pct: string;
  stopComparator: string;
  stopLevel: string;
  stopNoteOnly: boolean;
  window_start: string;
  window_end: string;
  catalyst_date: string;
  catalyst_note: string;
  conviction_pct: string;
  capital_assigned: string;
  idea_type: "real" | "paper";
  tags: string;
}

function draftFrom(p: ParseResponse, defaultCapital: number): Draft {
  const r = p.success_rule_json ?? {};
  const s = p.invalidation_rule_json ?? {};
  const kind = (r.type as RuleKind | undefined) ?? "none";
  return {
    symbol: p.symbol ?? "",
    title: p.title,
    direction: p.direction,
    benchmark_symbol: p.benchmark_symbol ?? "",
    ruleKind: kind,
    comparator: (r.comparator as string) ?? (p.direction === "down" ? "close_at_or_below" : "close_at_or_above"),
    level: r.level !== undefined ? String(r.level) : "",
    pct: r.pct !== undefined ? String(r.pct) : "",
    spread_pct: r.spread_pct !== undefined ? String(r.spread_pct) : "",
    stopComparator: (s.comparator as string) ?? (p.direction === "down" ? "close_at_or_above" : "close_at_or_below"),
    stopLevel: s.level !== undefined ? String(s.level) : "",
    stopNoteOnly: p.invalidation_is_note_only,
    window_start: p.window_start,
    window_end: p.window_end ?? "",
    catalyst_date: p.catalyst_date ?? "",
    catalyst_note: p.catalyst_note ?? "",
    conviction_pct: p.conviction_pct !== null ? String(p.conviction_pct) : "",
    capital_assigned: String(defaultCapital),
    idea_type: "paper",
    tags: p.tags.join(", "),
  };
}

function ruleFromDraft(d: Draft): Record<string, unknown> | null {
  switch (d.ruleKind) {
    case "level":
      return d.level ? { type: "level", comparator: d.comparator, level: Number(d.level) } : null;
    case "direction":
      return { type: "direction" };
    case "pct_move":
      return d.pct ? { type: "pct_move", pct: Number(d.pct) } : null;
    case "relative":
      return d.spread_pct && d.benchmark_symbol ? { type: "relative", benchmark: d.benchmark_symbol, spread_pct: Number(d.spread_pct) } : null;
    default:
      return null;
  }
}

export function NewThesis() {
  const nav = useNavigate();
  const canWrite = Boolean(getWriteToken());
  const status = useQuery({ queryKey: ["status"], queryFn: () => api.get<SystemStatus>("/api/status") });
  const defaultCapital = Number(status.data?.settings.default_capital ?? 1000);
  // Risk budget is account-level: account_size x default_risk_pct. account_size is null without the write token.
  const accountSize = status.data?.settings.account_size == null ? null : Number(status.data.settings.account_size);
  const riskPct = Number(status.data?.settings.default_risk_pct ?? 2);
  const riskBudget = accountSize === null ? null : (accountSize * riskPct) / 100;

  const [text, setText] = useState("");
  const [parsed, setParsed] = useState<ParseResponse | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pickSymbol, setPickSymbol] = useState(false);

  const parse = useMutation({
    mutationFn: () => api.post<ParseResponse>("/api/ideas/parse", { thesis_text: text }),
    onSuccess: (p) => {
      setParsed(p);
      setDraft(draftFrom(p, defaultCapital));
      setPickSymbol(!p.symbol);
      setError(null);
    },
    onError: (e) => setError((e as Error).message),
  });

  const save = useMutation({
    mutationFn: (goOptions: boolean) => {
      if (!draft || !parsed) throw new Error("Parse first");
      const success_rule_json = ruleFromDraft(draft);
      if (!success_rule_json) throw new Error("Set a success condition before saving");
      if (!draft.window_end) throw new Error("Set a window end date before saving");
      if (!draft.symbol) throw new Error("Pick an instrument before saving");
      const body = {
        symbol: draft.symbol,
        display_name: parsed.instrument?.display_name ?? parsed.symbol_candidates.find((c) => c.symbol === draft.symbol)?.name,
        title: draft.title,
        thesis_text: text,
        parsed_json: parsed.parsed_json,
        direction: draft.direction,
        benchmark_symbol: draft.benchmark_symbol || null,
        success_rule_json,
        invalidation_rule_json: draft.stopLevel ? { type: "level", comparator: draft.stopComparator, level: Number(draft.stopLevel) } : null,
        invalidation_is_note_only: draft.stopNoteOnly,
        window_start: draft.window_start,
        window_end: draft.window_end,
        catalyst_date: draft.catalyst_date || null,
        catalyst_note: draft.catalyst_note || null,
        conviction_pct: draft.conviction_pct ? Number(draft.conviction_pct) : null,
        capital_assigned: Number(draft.capital_assigned) || defaultCapital,
        idea_type: draft.idea_type,
        tags: draft.tags.split(",").map((t) => t.trim()).filter(Boolean),
      };
      return api.post<IdeaDetail>("/api/ideas", body).then((idea) => ({ idea, goOptions }));
    },
    onSuccess: ({ idea, goOptions }) => nav(goOptions ? `/ideas/${idea.id}/options` : `/ideas/${idea.id}`),
    onError: (e) => setError((e as Error).message),
  });

  const questions = useMemo(() => new Map((parsed?.questions ?? []).map((q) => [q.field, q.question])), [parsed]);
  const ctx = parsed?.context ?? null;
  const target = draft?.ruleKind === "level" && draft.level ? Number(draft.level) : null;
  const distance = ctx?.last_close && target ? (target / ctx.last_close - 1) * 100 : ctx?.distance_to_target_pct ?? null;
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setDraft((d) => (d ? { ...d, [k]: v } : d));

  return (
    <div className="grid gap-10 md:grid-cols-[1.15fr_.85fr]">
      <div>
        <h1 className="text-3xl font-medium">Write the idea before the trade.</h1>
        <p className="mt-1.5 mb-5 max-w-[52ch] text-sm text-muted">
          Plain English is fine. The parser fills the structured fields and asks for whatever it can't find. Nothing is inferred that you
          didn't say.
        </p>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Oil looks heavy. OPEC+ keeps adding barrels, US inventories built two weeks in a row, and USO just failed at the 200-day. I think it breaks the August low around 68.50 within the next three weeks. If it closes back above 74 the idea is wrong."
          className="min-h-[210px] w-full resize-y border-0 border-b border-line bg-transparent pb-3 font-sans text-lg leading-relaxed text-text outline-none placeholder:text-muted/60 focus:border-accent"
        />
        <div className="mt-3 flex items-center gap-3">
          <button
            onClick={() => parse.mutate()}
            disabled={!canWrite || parse.isPending || text.trim().length < 10}
            className="rounded-md border border-line px-4 py-2 text-sm hover:border-accent disabled:opacity-50"
          >
            {parse.isPending ? "Parsing…" : parsed ? "Parse again" : "Parse"}
          </button>
          {!canWrite && <span className="text-xs text-muted">Read only. Paste the write token in Settings to log ideas.</span>}
          {parsed && <span className="text-xs text-muted">Parsed with {String(parsed.parsed_json.model)}. Numbers below come from EODHD, not the model.</span>}
        </div>
        {error && <div className="mt-3"><Notice tone="wrong">{error}</Notice></div>}

        <div className="mt-8 grid grid-cols-2 gap-y-4 border-t border-line pt-4 md:grid-cols-4">
          <Ctx k={`${ctx?.symbol?.replace(/\.US$/, "") ?? "Instrument"} last close`} v={ctx?.last_close != null ? fmtPrice(ctx.last_close) : "–"} sub={ctx?.last_close_at ? `${ctx.last_close_source} · ${fmtDate(ctx.last_close_at)}` : undefined} />
          <Ctx k="20-day realized vol" v={ctx?.realized_vol_20d_pct != null ? `${ctx.realized_vol_20d_pct.toFixed(0)}%` : "–"} sub={ctx?.realized_vol_as_of ? `to ${fmtDate(ctx.realized_vol_as_of)}` : undefined} />
          <Ctx k="Distance to target" v={distance != null ? fmtPct(distance, 1) : "–"} tone={distance == null ? undefined : distance < 0 ? "wrong" : "right"} />
          <Ctx
            k="Regime at log"
            v={parsed?.regime.available ? `${parsed.regime.regime} ${parsed.regime.confidence != null ? Math.round(parsed.regime.confidence * 100) + "%" : ""}` : parsed ? "none stored" : "–"}
            color={parsed?.regime.available ? regimeColor(parsed.regime.regime) : undefined}
          />
        </div>
        {ctx && ctx.errors.length > 0 && (
          <div className="mt-3">
            <Notice tone="wrong">EODHD could not supply {ctx.errors.map((e) => String(e.what)).join(", ")}: {String(ctx.errors[0].error)}</Notice>
          </div>
        )}
      </div>

      <div>
        <h3 className="mb-1 text-sm text-muted">Parsed from your thesis</h3>
        {!draft && <p className="py-6 text-sm text-muted">Fields appear here after parsing. Anything the parser could not find shows as a question in amber.</p>}
        {draft && parsed && (
          <div>
            <Field label="Instrument" q={questions.get("instrument")}>
              {pickSymbol || !draft.symbol ? (
                <select value={draft.symbol} onChange={(e) => set("symbol", e.target.value)} className={inputCls}>
                  <option value="">Choose…</option>
                  {parsed.symbol_candidates.map((c) => (
                    <option key={c.symbol} value={c.symbol}>
                      {c.symbol} · {c.name}
                    </option>
                  ))}
                </select>
              ) : (
                <span className="num">
                  {draft.symbol} <span className="font-sans text-muted">· {parsed.instrument?.display_name ?? parsed.symbol_candidates.find((c) => c.symbol === draft.symbol)?.name}</span>
                  {parsed.symbol_candidates.length > 1 && (
                    <button onClick={() => setPickSymbol(true)} className="ml-2 font-sans text-xs text-muted underline">
                      change
                    </button>
                  )}
                </span>
              )}
            </Field>
            <Field label="Title">
              <input value={draft.title} onChange={(e) => set("title", e.target.value)} className={inputCls} />
            </Field>
            <Field label="Direction">
              <select value={draft.direction} onChange={(e) => set("direction", e.target.value as Direction)} className={inputCls}>
                {(["up", "down", "outperform", "underperform", "range"] as Direction[]).map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
            </Field>
            {(draft.direction === "outperform" || draft.direction === "underperform" || draft.ruleKind === "relative") && (
              <Field label="Benchmark" q={questions.get("benchmark")}>
                {parsed.benchmark_candidates.length > 1 ? (
                  <select value={draft.benchmark_symbol} onChange={(e) => set("benchmark_symbol", e.target.value)} className={inputCls}>
                    <option value="">Choose…</option>
                    {parsed.benchmark_candidates.map((c) => (
                      <option key={c.symbol} value={c.symbol}>
                        {c.symbol} · {c.name}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input value={draft.benchmark_symbol} onChange={(e) => set("benchmark_symbol", e.target.value.toUpperCase())} placeholder="SPY.US" className={inputCls} />
                )}
              </Field>
            )}
            <Field label="Success" q={questions.get("success_rule")} suggested={Boolean(parsed.success_rule_json?.suggested)}>
              <div className="flex flex-wrap items-center gap-2">
                <select value={draft.ruleKind} onChange={(e) => set("ruleKind", e.target.value as RuleKind)} className={inputCls + " w-28"}>
                  <option value="none">choose…</option>
                  <option value="level">level</option>
                  <option value="pct_move">% move</option>
                  <option value="relative">vs benchmark</option>
                  <option value="direction">direction</option>
                </select>
                {draft.ruleKind === "level" && (
                  <>
                    <select value={draft.comparator} onChange={(e) => set("comparator", e.target.value)} className={inputCls + " w-24"}>
                      {COMPARATORS.map((c) => (
                        <option key={c} value={c}>
                          {COMPARATOR_LABEL[c]}
                        </option>
                      ))}
                    </select>
                    <input value={draft.level} onChange={(e) => set("level", e.target.value)} placeholder="68.50" className={inputCls + " w-24"} />
                  </>
                )}
                {draft.ruleKind === "pct_move" && <input value={draft.pct} onChange={(e) => set("pct", e.target.value)} placeholder="4" className={inputCls + " w-20"} />}
                {draft.ruleKind === "relative" && <input value={draft.spread_pct} onChange={(e) => set("spread_pct", e.target.value)} placeholder="3" className={inputCls + " w-20"} />}
                {draft.ruleKind === "direction" && <span className="text-xs text-muted">right if the window-end close is on your side of entry</span>}
              </div>
            </Field>
            <Field label="Invalidation" q={questions.get("invalidation_rule")}>
              <div className="flex flex-wrap items-center gap-2">
                <select value={draft.stopComparator} onChange={(e) => set("stopComparator", e.target.value)} className={inputCls + " w-24"}>
                  {COMPARATORS.map((c) => (
                    <option key={c} value={c}>
                      {COMPARATOR_LABEL[c]}
                    </option>
                  ))}
                </select>
                <input value={draft.stopLevel} onChange={(e) => set("stopLevel", e.target.value)} placeholder="none" className={inputCls + " w-24"} />
                <label className="flex items-center gap-1 text-xs text-muted">
                  <input type="checkbox" checked={draft.stopNoteOnly} onChange={(e) => set("stopNoteOnly", e.target.checked)} /> note only
                </label>
                {!draft.stopNoteOnly && draft.stopLevel && <span className="text-xs text-muted">· closes as wrong</span>}
              </div>
            </Field>
            <Field label="Window" q={questions.get("window_end")}>
              <div className="flex items-center gap-2">
                <input type="date" value={draft.window_start} onChange={(e) => set("window_start", e.target.value)} className={inputCls + " w-36"} />
                <span className="text-muted">→</span>
                <input type="date" value={draft.window_end} onChange={(e) => set("window_end", e.target.value)} className={inputCls + " w-36"} />
                {draft.window_end && <span className="num text-xs text-muted">{Math.round((new Date(draft.window_end).getTime() - new Date(draft.window_start).getTime()) / 86400000)}d</span>}
              </div>
            </Field>
            <Field label="Catalyst" q={questions.get("catalyst_date")}>
              <div className="flex items-center gap-2">
                <input type="date" value={draft.catalyst_date} onChange={(e) => set("catalyst_date", e.target.value)} className={inputCls + " w-36"} />
                <input value={draft.catalyst_note} onChange={(e) => set("catalyst_note", e.target.value)} placeholder="note" className={inputCls} />
              </div>
            </Field>
            <Field label="Conviction" q={questions.get("conviction_pct")}>
              <input value={draft.conviction_pct} onChange={(e) => set("conviction_pct", e.target.value)} placeholder="% odds" className={inputCls + " w-24"} />
            </Field>
            <Field label="Capital for idea">
              <input value={draft.capital_assigned} onChange={(e) => set("capital_assigned", e.target.value)} className={inputCls + " w-28"} />
              {riskBudget !== null && Number(draft.capital_assigned) > riskBudget && (
                <div className="mt-1 text-sm text-accent">
                  Capital {fmtMoney(Number(draft.capital_assigned))} exceeds your risk budget of {fmtMoney(riskBudget)} ({riskPct.toFixed(1)}% of a{" "}
                  {fmtMoney(accountSize)} account).
                </div>
              )}
            </Field>
            <Field label="Type">
              <select value={draft.idea_type} onChange={(e) => set("idea_type", e.target.value as "real" | "paper")} className={inputCls + " w-32 font-sans"}>
                <option value="real">Real trade</option>
                <option value="paper">Paper idea</option>
              </select>
            </Field>
            <Field label="Tags">
              <input value={draft.tags} onChange={(e) => set("tags", e.target.value)} className={inputCls + " font-sans"} />
            </Field>
            <div className="mt-6 flex gap-2.5">
              <button
                onClick={() => save.mutate(true)}
                disabled={save.isPending || !status.data?.options_enabled}
                title={status.data?.options_enabled ? "" : "Options data is not enabled"}
                className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-bg disabled:opacity-50"
              >
                Log and find an expression
              </button>
              <button onClick={() => save.mutate(false)} disabled={save.isPending} className="rounded-md border border-line px-4 py-2 text-sm hover:border-accent disabled:opacity-50">
                {save.isPending ? "Saving…" : "Log only"}
              </button>
            </div>
            <p className="mt-3 text-xs text-muted">
              Capital is per idea. P&amp;L is the paper return on that amount, never your live account. Entry price is stamped from EODHD when you
              save.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

const inputCls =
  "w-full border-0 border-b border-line bg-transparent py-1 font-mono text-sm text-text outline-none focus:border-accent";

function Field({ label, q, suggested, children }: { label: string; q?: string; suggested?: boolean; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[120px_1fr] items-center gap-3 border-b border-line py-3">
      <label className="text-sm text-muted">
        {label}
        {suggested && <span className="ml-1 text-[10px] text-accent" title="Suggested by the parser; confirm or edit">suggested</span>}
      </label>
      <div>
        {children}
        {q && <div className="mt-1 text-sm text-accent">{q}</div>}
      </div>
    </div>
  );
}

function Ctx({ k, v, sub, tone, color }: { k: string; v: string; sub?: string; tone?: "right" | "wrong"; color?: string }) {
  const cls = tone === "right" ? "text-right" : tone === "wrong" ? "text-wrong" : "";
  return (
    <div>
      <div className="text-xs text-muted">{k}</div>
      <div className={`num mt-1 text-[15px] ${cls}`} style={color ? { color } : undefined}>
        {v}
      </div>
      {sub && <div className="text-[11px] text-muted">{sub}</div>}
    </div>
  );
}
