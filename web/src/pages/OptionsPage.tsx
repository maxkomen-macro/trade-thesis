import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, getWriteToken } from "../lib/api";
import type { IdeaDetail, OptionAnalysis, OptionCandidate, PositionOut, PositionTake, SystemStatus } from "../lib/types";
import { exitReasonLabel, fmtDate, fmtDateTime, fmtMoney, fmtPct, fmtPrice } from "../lib/format";
import { Notice, Panel } from "../components/Panel";
import { PayoffChart } from "../components/PayoffChart";

/** Expression page, laid out per design/trade-thesis-mockup.html ("Options / expression" screen): verdict banner
 *  with the vehicle comparison, three candidate cards each with the scenario heatmap, the payoff-at-expiry chart.
 *  Every number on this page is read from the stored option_analyses row (chain quotes, model values, rationale);
 *  nothing is computed in the browser beyond formatting. */
export function OptionsPage() {
  const { id } = useParams();
  const qc = useQueryClient();
  const canWrite = Boolean(getWriteToken());
  const status = useQuery({ queryKey: ["status"], queryFn: () => api.get<SystemStatus>("/api/status") });
  const idea = useQuery({ queryKey: ["idea", id], queryFn: () => api.get<IdeaDetail>(`/api/ideas/${id}`) });
  const analysis = useQuery({
    queryKey: ["options", id],
    queryFn: () => api.get<OptionAnalysis>(`/api/ideas/${id}/options`),
    retry: false,
  });
  const positions = useQuery({ queryKey: ["positions", id], queryFn: () => api.get<PositionOut[]>(`/api/ideas/${id}/positions`) });
  const openPosition = positions.data?.find((p) => p.status === "open") ?? null;
  const [inverse, setInverse] = useState("");
  const [leverage, setLeverage] = useState("2");
  const [err, setErr] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  const run = useMutation({
    mutationFn: () =>
      api.post<OptionAnalysis>(
        `/api/ideas/${id}/options`,
        inverse.trim() ? { inverse_symbol: inverse.trim().toUpperCase(), inverse_leverage: Number(leverage) } : {},
      ),
    onSuccess: () => {
      setErr(null);
      qc.invalidateQueries({ queryKey: ["options", id] });
      qc.invalidateQueries({ queryKey: ["idea", id] });
    },
    onError: (e) => setErr(describeError(e)),
  });

  const enabled = status.data?.options_enabled ?? false;
  const notFound = analysis.error instanceof ApiError && analysis.error.status === 404;
  const a = analysis.data ?? null;
  const i = idea.data ?? null;

  return (
    <div className="space-y-8">
      <div className="text-sm text-muted">
        <Link to={`/ideas/${id}`} className="underline">
          Back to idea
        </Link>
        {i && (
          <>
            {" "}
            · <span className="ticker text-accent">{i.instrument.symbol.replace(/\.US$/, "")}</span> {i.title}
          </>
        )}
      </div>

      {status.data && !enabled && (
        <Panel title="Options selector is disabled">
          <p className="text-sm text-muted">
            <span className="font-mono text-xs">options_enabled</span> is off in Settings. It is switched on only after the EODHD options probe
            returns live chain data. Nothing on this page is estimated.
          </p>
        </Panel>
      )}

      {a && i && <Banner a={a} idea={i} />}
      {!a && i && !analysis.isLoading && (
        <div className="grid gap-10 md:grid-cols-[1.1fr_.9fr]">
          <div>
            <div className="mb-2 text-sm text-muted">
              <Eyebrow idea={i} a={null} />
            </div>
            <h1 className="text-3xl font-medium leading-tight">
              {notFound ? "No expression analysed yet." : analysis.error ? "Could not load the analysis." : ""}
            </h1>
            <p className="mt-4 max-w-[58ch] text-sm text-muted">
              {notFound
                ? "Run the selector to pull the live chain, score every candidate against the thesis, and store the result. Public viewers see the latest stored run."
                : analysis.error
                  ? describeError(analysis.error)
                  : ""}
            </p>
          </div>
        </div>
      )}

      {enabled && (
        <div className="flex flex-wrap items-end gap-3 border-b border-line pb-6 text-sm">
          {canWrite ? (
            <>
              <button
                onClick={() => run.mutate()}
                disabled={run.isPending || !i || i.status !== "open"}
                title={i && i.status !== "open" ? "Only open ideas can be expressed" : ""}
                className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-bg disabled:opacity-50"
              >
                {run.isPending ? "Pulling the chain and scoring…" : a ? "Re-run with today's chain" : "Run the selector"}
              </button>
              <label className="flex items-center gap-2 text-xs text-muted">
                Compare with an inverse ETF
                <input
                  value={inverse}
                  onChange={(e) => setInverse(e.target.value)}
                  placeholder="SCO.US"
                  className="num w-24 rounded border border-line bg-bg px-2 py-1 text-xs text-text outline-none focus:border-accent"
                />
                <select value={leverage} onChange={(e) => setLeverage(e.target.value)} className="rounded border border-line bg-bg px-2 py-1 text-xs text-text">
                  <option value="1">1x</option>
                  <option value="2">2x</option>
                  <option value="3">3x</option>
                </select>
              </label>
            </>
          ) : (
            <span className="text-xs text-muted">Read only. Paste the write token in Settings to run the selector.</span>
          )}
          {a && (
            <span className="num ml-auto text-xs text-muted">
              analysed {fmtDateTime(a.created_at)} · spot {fmtPrice(a.spot)} ({a.spot_source}, {fmtDateTime(a.spot_as_of)}) · rate {a.rate_pct.toFixed(1)}%
              from Settings
            </span>
          )}
        </div>
      )}
      {err && <Notice tone="wrong">{err}</Notice>}
      {a?.params.sizing?.capital_exceeds_risk_budget && (
        <Notice tone="accent">
          {a.dollars_hidden || a.params.sizing.capital_assigned == null || a.params.sizing.risk_budget == null
            ? `Capital assigned to this idea exceeds the account risk budget (${a.params.sizing.risk_pct.toFixed(1)}% of the account size).`
            : `Capital assigned ${fmtMoney(a.params.sizing.capital_assigned)} exceeds your risk budget of ${fmtMoney(a.params.sizing.risk_budget)} (${a.params.sizing.risk_pct.toFixed(1)}% of a ${fmtMoney(a.params.sizing.account_size)} account). Contract counts below use the assigned capital.`}
        </Notice>
      )}
      {a?.params.errors && a.params.errors.length > 0 && (
        <Notice tone="wrong">EODHD could not supply {a.params.errors.map((e) => String(e.what)).join(", ")}: {String(a.params.errors[0].error)}</Notice>
      )}

      {openPosition && (
        <Notice tone="accent">
          Position open on this idea: <span className="num">{openPosition.name}</span>, {openPosition.pnl_pct != null ? fmtPct(openPosition.pnl_pct, 0) : "–"} on premium
          {openPosition.last_value_as_of ? ` as of ${fmtDate(openPosition.last_value_as_of)}` : ""}.{" "}
          <Link to={`/ideas/${id}`} className="underline">
            Marks and exit rules are on the idea page.
          </Link>
        </Notice>
      )}
      {!openPosition && positions.data && positions.data.length > 0 && (
        <p className="text-xs text-muted">
          Earlier expression on this idea: {positions.data.map((p) => `${p.name} (${exitReasonLabel(p.exit_reason)}, ${fmtPct(p.pnl_pct, 0)})`).join("; ")}.
        </p>
      )}

      {a && a.candidates.filter((c) => c.rank).length > 0 && (
        <div className="grid items-start gap-5 md:grid-cols-[1.35fr_1fr_1fr]">
          {a.candidates
            .filter((c) => c.rank)
            .slice(0, 3)
            .map((c, idx) => (
              <CandidateCard key={c.name} c={c} idx={idx} a={a} idea={i} openPosition={openPosition} settings={status.data?.settings} canWrite={canWrite} />
            ))}
        </div>
      )}
      {a && a.candidates.filter((c) => c.rank).length === 0 && a.candidates.length > 0 && (
        <Notice>
          {a.candidates.length} candidates were built and none passed the liquidity filters (open interest at least {a.params.filters?.min_open_interest ?? 100}{" "}
          on every leg, bid/ask width at most {a.params.filters?.max_spread_width_pct ?? 10}% on every leg). The full list is below.
        </Notice>
      )}

      {a && a.candidates.filter((c) => c.rank).length > 0 && (
        <section>
          <div className="mb-2 flex items-baseline justify-between">
            <h2 className="text-lg">Payoff at expiry</h2>
            <span className="text-xs text-muted">per share after the debit · amber line is your target, grey is spot</span>
          </div>
          <PayoffChart candidates={a.candidates.filter((c) => c.rank).slice(0, 3)} target={a.shares_comparison.target} spot={a.spot} />
        </section>
      )}

      {a && (
        <section className="text-xs text-muted">
          <p>
            {a.params.counts?.generated ?? a.candidates.length} candidates built across{" "}
            {Array.isArray(a.params.chain?.expiries) ? (a.params.chain!.expiries as string[]).length : "?"} expiries ({String(a.params.chain?.rows ?? "?")} chain rows,
            trade date {fmtDate(a.chain_trade_date)}); {a.params.counts?.passing ?? "?"} passed the filters. Ranked by return on premium at the target on the window end,
            minus penalties for bid/ask width, theta over the window, implied vol above realized, and an expiry before the window end. Black-Scholes at each leg's chain IV,
            held constant; rate {a.rate_pct.toFixed(1)}% from Settings. IV percentile needs a year of stored chain history and shows as IV versus 20-day realized until then.
            {a.params.rationale?.source === "template"
              ? " Rationale text is from templates (the model was unavailable or added a figure not in its input)."
              : ` Rationale text written by ${a.params.rationale?.model ?? "the model"} from the computed numbers only.`}
          </p>
          {a.params.iv_percentile && (
            <p className="mt-1">
              IV percentile: {a.iv_percentile_1y != null ? `${a.iv_percentile_1y.toFixed(0)}th of the trailing year (${String(a.params.iv_percentile.note)})` : String(a.params.iv_percentile.note)}
            </p>
          )}
          {a.candidates.length > 0 ? (
            <button onClick={() => setShowAll((v) => !v)} className="mt-2 underline">
              {showAll ? "Hide the full candidate list" : `Show all ${a.candidates.length} candidates`}
            </button>
          ) : (
            <p className="mt-2">No candidates were built for this idea, so there is no list to show.</p>
          )}
          {showAll && a.candidates.length > 0 && <AllCandidates a={a} />}
        </section>
      )}
    </div>
  );
}

function describeError(e: unknown): string {
  if (e instanceof ApiError) {
    const b = e.body as { detail?: unknown } | null;
    const d = b && typeof b === "object" ? b.detail : null;
    if (d && typeof d === "object" && "message" in (d as Record<string, unknown>)) return String((d as Record<string, unknown>).message);
    return e.message;
  }
  return (e as Error).message;
}

function Eyebrow({ idea, a }: { idea: IdeaDetail; a: OptionAnalysis | null }) {
  const sym = idea.instrument.symbol.replace(/\.US$/, "");
  const side = idea.direction === "down" || idea.direction === "underperform" ? "short" : idea.direction === "range" ? "range" : "long";
  const target = (a?.shares_comparison.target ?? idea.target_level) as number | null;
  return (
    <>
      <b className="num font-medium text-text">{sym}</b> {side} · target <b className="num font-medium text-text">{target !== null ? fmtPrice(target) : "none"}</b> by{" "}
      <b className="num font-medium text-text">{fmtDate(idea.window_end)}</b>
      {!idea.dollars_hidden && idea.capital_assigned !== null && (
        <>
          {" "}
          · <b className="num font-medium text-text">{fmtMoney(idea.capital_assigned)}</b> assigned
        </>
      )}
      {a && (
        <>
          {" "}
          · chain as of <b className="num font-medium text-text">{fmtDate(a.chain_trade_date)}</b> close
        </>
      )}
    </>
  );
}

function Banner({ a, idea }: { a: OptionAnalysis; idea: IdeaDetail }) {
  const best = a.candidates.find((c) => c.rank === 1) ?? null;
  const s = a.shares_comparison;
  const hidden = a.dollars_hidden;
  return (
    <div className="grid items-start gap-10 border-b border-line pb-8 md:grid-cols-[1.1fr_.9fr]">
      <div>
        <div className="mb-2 text-sm text-muted">
          <Eyebrow idea={idea} a={a} />
        </div>
        <h1 className="text-3xl font-medium leading-tight md:text-4xl">
          {a.verdict === "trade" && best ? (
            <>
              Best expression: <span className="font-bold text-accent">{best.name}</span>
            </>
          ) : (
            <>
              No trade<span className="text-muted">.</span> {best ? <span className="text-muted">Best available: {best.name}</span> : null}
            </>
          )}
        </h1>
        <p className="mt-4 max-w-[58ch] text-[15px] leading-relaxed text-muted">{a.verdict_text}</p>
      </div>
      <div className="rounded-lg border border-line bg-surface px-5 py-4">
        <div className="text-xs text-muted">Same move, other vehicles</div>
        {s.target === null && <p className="mt-2 text-sm text-muted">No price target on the idea, so there is no move to compare.</p>}
        {s.inverse_etf && (
          <VehicleRow label={`${s.inverse_etf.label ?? s.inverse_etf.symbol}${hidden || s.inverse_etf.capital == null ? "" : `, ${fmtMoney(s.inverse_etf.capital)}`}`} value={fmtPct(s.inverse_etf.return_pct, 1)} tone={s.inverse_etf.return_pct} note={s.inverse_etf.note} />
        )}
        {s.shares && <VehicleRow label={`${s.shares.label}${hidden || s.shares.capital == null ? "" : `, ${fmtMoney(s.shares.capital)}`}`} value={fmtPct(s.shares.return_pct, 1)} tone={s.shares.return_pct} />}
        {s.best_option && (
          <VehicleRow
            label={`${s.best_option.name}${hidden || s.best_option.at_risk == null ? "" : `, ${fmtMoney(s.best_option.at_risk)} at risk`}`}
            value={fmtPct(s.best_option.return_pct, 0)}
            tone={s.best_option.return_pct}
            note={`on premium, at target by the window end${s.best_option.return_at_expiry_pct !== null ? ` · ${fmtPct(s.best_option.return_at_expiry_pct, 0)} at expiry` : ""}`}
          />
        )}
        {s.target !== null && (
          <div className="flex items-baseline justify-between py-2 text-sm">
            <span>Verdict</span>
            <span className={`num ${s.verdict === "option" ? "text-accent" : "text-text"}`}>{s.verdict_text}</span>
          </div>
        )}
      </div>
    </div>
  );
}

function VehicleRow({ label, value, tone, note }: { label: string; value: string; tone: number; note?: string }) {
  return (
    <div className="border-b border-line py-2 text-sm">
      <div className="flex items-baseline justify-between gap-3">
        <span>{label}</span>
        <span className="num" style={{ color: tone > 0 ? "var(--color-right)" : tone < 0 ? "var(--color-wrong)" : "var(--color-muted)" }}>
          {value}
        </span>
      </div>
      {note && <div className="text-[11px] text-muted">{note}</div>}
    </div>
  );
}

const RANK_LABEL = ["Best fit", "Second", "Third"];

function CandidateCard({
  c,
  idx,
  a,
  idea,
  openPosition,
  settings,
  canWrite,
}: {
  c: OptionCandidate;
  idx: number;
  a: OptionAnalysis;
  idea: IdeaDetail | null;
  openPosition: PositionOut | null;
  settings: Record<string, unknown> | undefined;
  canWrite: boolean;
}) {
  const best = idx === 0 && a.verdict === "trade";
  const hidden = a.dollars_hidden;
  const [taking, setTaking] = useState(false);
  const ret = c.ret_at_target_window_end_pct ?? null;
  const cushionTone = c.cushion_days < 0 ? "text-wrong" : "text-muted";
  return (
    <div className={`rounded-lg border bg-surface p-5 ${best ? "border-accent/60" : "border-line"}`}>
      <div className={`mb-2 text-xs ${best ? "text-accent" : "text-muted"}`}>{RANK_LABEL[idx] ?? `#${c.rank}`}</div>
      <div className="num text-lg font-medium">{c.name}</div>
      <div className="mt-0.5 text-xs text-muted">
        {c.kind} · {c.tier_ok ? "Tier 2 eligible" : "not Tier 2"} · <span className={cushionTone}>{c.cushion}</span>
      </div>

      <dl className="mt-4 grid grid-cols-[1fr_auto] gap-x-4 gap-y-1.5 text-[13px]">
        <dt className="text-muted">Return at target</dt>
        <dd className="text-right">
          <span className="font-heading text-[22px] font-semibold tracking-tight" style={{ color: ret === null ? "var(--color-muted)" : ret > 0 ? "var(--color-right)" : "var(--color-wrong)" }}>
            {ret === null ? "–" : fmtPct(ret, 0)}
          </span>
          <div className="text-[11px] text-muted">
            by {fmtDate(c.eval_date)}
            {c.ret_at_target_expiry_pct != null ? ` · ${fmtPct(c.ret_at_target_expiry_pct, 0)} at expiry` : ""}
          </div>
        </dd>
        <dt className="text-muted">{c.structure === "debit_spread" ? "Debit" : "Premium"}</dt>
        <dd className="num text-right">{c.debit !== null ? c.debit.toFixed(2) : "–"}</dd>
        <dt className="text-muted">Breakeven</dt>
        <dd className="num text-right">
          {c.breakeven != null ? c.breakeven.toFixed(2) : "–"}
          {c.move_spent_to_breakeven_pct != null && <div className="text-[11px] text-muted">{c.move_spent_to_breakeven_pct.toFixed(0)}% of the move to reach it</div>}
        </dd>
        <dt className="text-muted">{hidden ? "Contracts" : `Contracts for ${fmtMoney(c.capital_assigned)}`}</dt>
        <dd className="num text-right">
          {hidden ? (
            <span className="text-muted">hidden</span>
          ) : (
            <>
              {c.contracts ?? 0}
              {c.at_risk != null && c.contracts ? ` · ${fmtMoney(c.at_risk)} at risk` : ""}
              <div className="text-[11px] text-muted">
                {fmtMoney(c.cost_per_contract)} per contract
                {c.capital_exceeds_risk_budget && c.risk_budget != null ? ` · above the ${fmtMoney(c.risk_budget)} risk budget` : ""}
              </div>
            </>
          )}
        </dd>
        <dt className="text-muted">Market-implied P(profit)</dt>
        <dd className="num text-right">{c.pop_pct != null ? `${c.pop_pct.toFixed(0)}%` : "–"}</dd>
        <dt className="text-muted">Theta you can afford</dt>
        <dd className="num text-right">
          {c.days_of_theta === null || c.days_of_theta === undefined ? (
            <span className="text-muted">keeps value at spot</span>
          ) : (
            `${c.days_of_theta} days at spot`
          )}
          {c.theta_week_pct != null && <div className="text-[11px] text-muted">{c.theta_week_pct.toFixed(1)}% of premium per week</div>}
        </dd>
        <dt className="text-muted">Bid/ask width</dt>
        <dd className="num text-right" style={{ color: (c.spread_width_pct ?? 0) > 10 ? "var(--color-wrong)" : undefined }}>
          {c.spread_width_pct != null ? `${c.spread_width_pct.toFixed(1)}%` : "–"}
          {c.leg_widths_pct && c.leg_widths_pct.length > 1 && (
            <div className="text-[11px] text-muted">legs {c.leg_widths_pct.map((w) => (w == null ? "–" : `${w.toFixed(1)}%`)).join(" / ")}</div>
          )}
        </dd>
        <dt className="text-muted">{c.iv_percentile_1y != null ? "IV percentile, 1y" : "IV vs 20-day realized"}</dt>
        <dd className="num text-right">
          {c.iv_percentile_1y != null ? `${c.iv_percentile_1y.toFixed(0)}th` : c.iv_rv_ratio != null ? `${c.iv_rv_ratio.toFixed(2)}x` : "–"}
          <div className="text-[11px] text-muted">
            IV {c.iv != null ? `${(c.iv * 100).toFixed(0)}%` : "–"}
            {c.iv_fallback ? " (realized used: chain IV missing)" : ""} · OI {c.open_interest ?? "–"}
          </div>
        </dd>
      </dl>

      {c.grid && <Heatmap c={c} />}

      {c.why && <p className="mt-4 border-t border-line pt-3 text-[13px] text-muted">{c.why}</p>}
      {taking && idea ? (
        <TakeForm c={c} idea={idea} settings={settings} onDone={() => setTaking(false)} />
      ) : (
        <button
          onClick={() => setTaking(true)}
          disabled={!canWrite || !idea || idea.status !== "open" || Boolean(openPosition) || !c.passes_filters}
          title={
            !canWrite
              ? "Paste the write token in Settings to take an expression"
              : openPosition
                ? `A position is already open on this idea (${openPosition.name})`
                : idea && idea.status !== "open"
                  ? "Only open ideas can be expressed"
                  : "Quote every leg afresh, set the exit rules, and start daily marks"
          }
          className={`mt-4 w-full rounded-md px-3 py-2 text-sm ${best ? "bg-accent font-semibold text-bg" : "border border-line hover:border-accent"} disabled:opacity-50`}
        >
          {best ? "Take this expression" : "Take this instead"}
        </button>
      )}
    </div>
  );
}

/** Take-this-expression form: contracts (from the idea's capital at the analysis debit), an optional real fill,
 *  and the three exit rules prefilled from Settings. The server re-quotes every leg before storing anything. */
function TakeForm({ c, idea, settings, onDone }: { c: OptionCandidate; idea: IdeaDetail; settings: Record<string, unknown> | undefined; onDone: () => void }) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [contracts, setContracts] = useState(c.contracts ? String(c.contracts) : "1");
  const [fill, setFill] = useState("");
  const [tp, setTp] = useState(String(settings?.default_take_profit_pct ?? 100));
  const [sl, setSl] = useState(String(settings?.default_stop_loss_pct ?? 50));
  const [ts, setTs] = useState(String(settings?.default_time_stop_days_before_expiry ?? 5));
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const take = useMutation({
    mutationFn: () => {
      const body: PositionTake = {
        candidate_name: c.name,
        contracts: Number(contracts) > 0 ? Number(contracts) : undefined,
        fill_price: fill.trim() ? Number(fill) : undefined,
        take_profit_pct: Number(tp),
        stop_loss_pct: Number(sl),
        time_stop_days_before_expiry: Number(ts),
        note: note.trim() || undefined,
      };
      return api.post<PositionOut>(`/api/ideas/${idea.id}/positions`, body);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["positions", String(idea.id)] });
      qc.invalidateQueries({ queryKey: ["idea", String(idea.id)] });
      qc.invalidateQueries({ queryKey: ["ideas"] });
      nav(`/ideas/${idea.id}`);
    },
    onError: (e) => setErr(describeError(e)),
  });
  const expiry = new Date(c.expiry + "T00:00:00");
  const timeStop = new Date(expiry.getTime() - Number(ts || 0) * 86_400_000);
  return (
    <div className="mt-4 rounded-md border border-accent/50 bg-surface-2 p-3 text-[13px]">
      <div className="mb-2 font-medium">Take {c.name}</div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-2">
        <Field label="Contracts">
          <input value={contracts} onChange={(e) => setContracts(e.target.value)} className="num w-full rounded border border-line bg-bg px-2 py-1 text-right outline-none focus:border-accent" />
        </Field>
        <Field label="Fill (optional)">
          <input value={fill} onChange={(e) => setFill(e.target.value)} placeholder={c.debit != null ? `mid ${c.debit.toFixed(2)}` : "mid"} className="num w-full rounded border border-line bg-bg px-2 py-1 text-right outline-none focus:border-accent" />
        </Field>
        <Field label="Stop loss, % of premium">
          <input value={sl} onChange={(e) => setSl(e.target.value)} className="num w-full rounded border border-line bg-bg px-2 py-1 text-right outline-none focus:border-accent" />
        </Field>
        <Field label="Take profit, % of premium">
          <input value={tp} onChange={(e) => setTp(e.target.value)} className="num w-full rounded border border-line bg-bg px-2 py-1 text-right outline-none focus:border-accent" />
        </Field>
        <Field label="Time stop, days before expiry">
          <input value={ts} onChange={(e) => setTs(e.target.value)} className="num w-full rounded border border-line bg-bg px-2 py-1 text-right outline-none focus:border-accent" />
        </Field>
        <Field label="Note">
          <input value={note} onChange={(e) => setNote(e.target.value)} className="w-full rounded border border-line bg-bg px-2 py-1 outline-none focus:border-accent" />
        </Field>
      </div>
      <p className="mt-2 text-[11px] text-muted">
        Exit rules prefilled from Settings. Time stop lands on {timeStop.toLocaleDateString("en-US", { month: "short", day: "numeric" })}; expiry {fmtDate(c.expiry)}. Entry is the
        structure mid from a fresh quote of every leg unless you state a fill. Marks come from the daily chain; nothing is modeled.
      </p>
      {err && <div className="mt-2 text-wrong">{err}</div>}
      <div className="mt-3 flex gap-2">
        <button onClick={() => take.mutate()} disabled={take.isPending} className="rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-bg disabled:opacity-50">
          {take.isPending ? "Quoting legs…" : "Open position"}
        </button>
        <button onClick={onDone} className="rounded-md border border-line px-3 py-1.5 text-sm">
          Cancel
        </button>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-0.5 block text-[11px] text-muted">{label}</span>
      {children}
    </label>
  );
}

function heatColor(v: number | null): string {
  if (v === null) return "#1c2129";
  const neutral: [number, number, number] = [230, 223, 216];
  const green: [number, number, number] = [61, 220, 132];
  const red: [number, number, number] = [255, 94, 91];
  const t = v >= 0 ? Math.min(v / 150, 1) : Math.min(-v / 100, 1);
  const to = v >= 0 ? green : red;
  const mix = neutral.map((n, i) => Math.round(n + (to[i] - n) * t));
  return `rgb(${mix[0]},${mix[1]},${mix[2]})`;
}

function Heatmap({ c }: { c: OptionCandidate }) {
  const g = c.grid!;
  return (
    <div className="mt-4">
      <div className="mb-1.5 flex items-baseline justify-between text-[11px] text-muted">
        <span>Return on premium, by price and date</span>
        <span>IV held at {g.iv_held != null ? `${(g.iv_held * 100).toFixed(0)}%` : "chain"}</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full border-separate" style={{ borderSpacing: 3 }}>
          <thead>
            <tr>
              <th />
              {g.dates.map((d, j) => (
                <th key={d} className={`num pb-1 text-center text-[11px] font-normal ${j === g.window_end_col ? "text-accent" : "text-muted"}`}>
                  {new Date(d + "T00:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" })}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {g.prices.map((p, r) => (
              <tr key={p}>
                <td className={`num w-12 pr-1.5 text-right text-[11px] ${r === g.target_row ? "text-accent" : r === g.spot_row ? "text-text" : "text-muted"}`}>
                  {p.toFixed(p >= 100 ? 1 : 2)}
                </td>
                {g.ret_pct[r].map((v, j) => (
                  <td
                    key={j}
                    className="num h-[26px] rounded-[3px] text-center text-[11px]"
                    style={{
                      background: heatColor(v),
                      color: "#0a0d12",
                      outline: r === g.target_row && j === g.window_end_col ? "2px solid #f0b429" : undefined,
                      outlineOffset: -2,
                    }}
                    title={`${p.toFixed(2)} on ${g.dates[j]}: value ${g.values[r][j] ?? "–"}, ${v == null ? "–" : fmtPct(v, 0)} on premium`}
                  >
                    {v == null ? "–" : Math.abs(v) < 0.5 ? "0" : `${v > 0 ? "+" : "−"}${Math.abs(v).toFixed(0)}`}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-1 text-[10px] text-muted">rows: stop to 5% past target, target row in amber; columns: entry, weekly, window end (amber), expiry</div>
    </div>
  );
}

function AllCandidates({ a }: { a: OptionAnalysis }) {
  return (
    <div className="mt-3 overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="text-left text-muted">
          <tr className="border-b border-line">
            <th className="py-1.5 pr-3 font-normal">#</th>
            <th className="py-1.5 pr-3 font-normal">Structure</th>
            <th className="py-1.5 pr-3 font-normal">Expiry</th>
            <th className="py-1.5 pr-3 text-right font-normal">Debit</th>
            <th className="py-1.5 pr-3 text-right font-normal">Return at target</th>
            <th className="py-1.5 pr-3 text-right font-normal">Breakeven</th>
            <th className="py-1.5 pr-3 text-right font-normal">P(profit)</th>
            <th className="py-1.5 pr-3 text-right font-normal">Width</th>
            <th className="py-1.5 pr-3 text-right font-normal">OI</th>
            <th className="py-1.5 pr-3 text-right font-normal">Score</th>
            <th className="py-1.5 font-normal">Filters</th>
          </tr>
        </thead>
        <tbody>
          {a.candidates.map((c) => (
            <tr key={c.name + c.expiry} className={`border-b border-line/60 ${c.passes_filters ? "" : "text-muted"}`}>
              <td className="num py-1 pr-3">{c.rank ?? ""}</td>
              <td className="py-1 pr-3">
                <span className="num text-text">{c.name}</span> <span className="text-muted">{c.kind}</span>
              </td>
              <td className="num py-1 pr-3">{fmtDate(c.expiry)}</td>
              <td className="num py-1 pr-3 text-right">{c.debit?.toFixed(2) ?? "–"}</td>
              <td className="num py-1 pr-3 text-right">{c.ret_at_target_window_end_pct != null ? fmtPct(c.ret_at_target_window_end_pct, 0) : "–"}</td>
              <td className="num py-1 pr-3 text-right">{c.breakeven != null ? c.breakeven.toFixed(2) : "–"}</td>
              <td className="num py-1 pr-3 text-right">{c.pop_pct != null ? `${c.pop_pct.toFixed(0)}%` : "–"}</td>
              <td className="num py-1 pr-3 text-right">{c.spread_width_pct != null ? `${c.spread_width_pct.toFixed(1)}%` : "–"}</td>
              <td className="num py-1 pr-3 text-right">{c.open_interest ?? "–"}</td>
              <td className="num py-1 pr-3 text-right">{c.score != null ? c.score.toFixed(1) : "–"}</td>
              <td className="py-1">{c.passes_filters ? "pass" : c.filter_reasons.join("; ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
