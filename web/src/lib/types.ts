export interface Health {
  status: string;
  version: string;
  env: string;
  db: string;
}

export interface ServiceStatus {
  name: string;
  ok: boolean;
  detail: string;
  checked_at: string;
}

export interface SystemStatus {
  db: ServiceStatus;
  eodhd: ServiceStatus;
  radar: ServiceStatus;
  options_enabled: boolean;
  public_hide_dollars: boolean;
  settings: Record<string, unknown>;
}

export interface RegimeReadout {
  available: boolean;
  as_of: string | null;
  regime: string | null;
  confidence: number | null;
  growth_trend: number | null;
  inflation_trend: number | null;
  prob_goldilocks: number | null;
  prob_overheating: number | null;
  prob_stagflation: number | null;
  prob_recession: number | null;
  source: string | null;
  stored_at: string | null;
  age_days: number | null;
}

export type Direction = "up" | "down" | "outperform" | "underperform" | "range";
export type IdeaStatus = "open" | "right" | "wrong" | "expired" | "closed_manual";

export interface InstrumentOut {
  id: number;
  symbol: string;
  display_name: string;
  kind: string;
  created_at: string;
}

export interface EventOut {
  id: number;
  event_type: string;
  price: number | null;
  benchmark_price: number | null;
  note: string | null;
  occurred_on: string;
  created_at: string;
}

export interface BarOut {
  as_of: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number;
}

export interface IdeaOut {
  id: number;
  instrument: InstrumentOut;
  title: string;
  thesis_text: string;
  parsed_json: Record<string, unknown> | null;
  direction: Direction;
  benchmark_symbol: string | null;
  success_rule_json: Record<string, unknown>;
  invalidation_rule_json: Record<string, unknown> | null;
  invalidation_is_note_only: boolean;
  success_rule_text: string;
  invalidation_rule_text: string | null;
  target_level: number | null;
  stop_level: number | null;
  window_start: string;
  window_end: string;
  days_left: number | null;
  catalyst_date: string | null;
  catalyst_note: string | null;
  conviction_pct: number | null;
  capital_assigned: number | null;
  idea_type: "real" | "paper";
  entry_price: number | null;
  entry_price_at: string | null;
  radar_regime: string | null;
  radar_probs_json: Record<string, unknown> | null;
  tags: string[];
  basket_symbols: string[] | null;
  status: IdeaStatus;
  resolved_at: string | null;
  resolution_reason: string | null;
  direction_right: boolean | null;
  hypothetical_pnl_pct: number | null;
  hypothetical_pnl_abs: number | null;
  spread_at_resolution_pct: number | null;
  spread_at_window_end_pct: number | null;
  last_price: number | null;
  last_price_as_of: string | null;
  progress_pct: number;
  progress_kind: "price" | "time";
  seed: boolean;
  dollars_hidden: boolean;
  position: PositionSummary | null;
  positions_count: number;
  divergence: Divergence | null;
  created_at: string;
  updated_at: string;
}

export interface IdeaDetail extends IdeaOut {
  events: EventOut[];
  bars: BarOut[];
  benchmark_bars: BarOut[];
}

export interface RegimeBucket {
  regime: string;
  ideas: number;
  resolved: number;
  direction_hit_rate: number | null;
  target_hit_rate: number | null;
}

export interface StatsOut {
  ideas_logged: number;
  seed_count: number;
  open_count: number;
  resolved_count: number;
  direction_hit_rate: number | null;
  target_hit_rate: number | null;
  hypothetical_pnl_abs: number | null;
  hypothetical_pnl_pct_avg: number | null;
  by_regime: RegimeBucket[];
  resolving_soon: IdeaOut[];
  dollars_hidden: boolean;
  positions_open: number;
  positions_closed: number;
  option_beat_thesis: number;
}

export interface JobSummary {
  job: string;
  as_of: string;
  ideas_checked: number;
  instruments_refreshed: number;
  bars_added: number;
  resolved: Array<Record<string, unknown>>;
  errors: Array<Record<string, unknown>>;
  positions: {
    positions_checked: number;
    marked: Array<Record<string, unknown>>;
    closed: Array<Record<string, unknown>>;
    errors: Array<Record<string, unknown>>;
    chain_requests: number;
  } | null;
}

export interface SymbolCandidate {
  symbol: string;
  name: string;
  type: string;
  previous_close: number | null;
  previous_close_date: string | null;
}

export interface ParseContext {
  symbol: string;
  last_close: number | null;
  last_close_at: string | null;
  last_close_source: string | null;
  realized_vol_20d_pct: number | null;
  realized_vol_as_of: string | null;
  distance_to_target_pct: number | null;
  errors: Array<Record<string, unknown>>;
}

export interface ParseResponse {
  thesis_text: string;
  title: string;
  instrument: { id: number; symbol: string; display_name: string; kind: string } | null;
  symbol: string | null;
  symbol_candidates: SymbolCandidate[];
  instrument_query: string;
  direction: Direction;
  benchmark_symbol: string | null;
  benchmark_candidates: SymbolCandidate[];
  success_rule_json: Record<string, unknown> | null;
  success_rule_text: string | null;
  invalidation_rule_json: Record<string, unknown> | null;
  invalidation_rule_text: string | null;
  invalidation_is_note_only: boolean;
  window_start: string;
  window_end: string | null;
  catalyst_date: string | null;
  catalyst_note: string | null;
  conviction_pct: number | null;
  tags: string[];
  questions: Array<{ field: string; question: string }>;
  context: ParseContext | null;
  regime: RegimeReadout;
  parsed_json: Record<string, unknown>;
}

export interface ReviewBucket {
  key: string;
  ideas: number;
  resolved: number;
  right: number;
  wrong: number;
  expired: number;
  direction_hit_rate: number | null;
  target_hit_rate: number | null;
  avg_pnl_pct: number | null;
  total_pnl_abs: number | null;
}

export interface DivergenceCell {
  count: number;
  avg_option_pnl_pct: number | null;
  avg_thesis_pnl_pct: number | null;
  positions: Array<{
    idea_id: number;
    symbol: string;
    name: string;
    thesis_pnl_pct: number | null;
    option_pnl_pct: number | null;
    exit_reason: string | null;
    idea_reason: string | null;
  }>;
}

export interface ReviewOut {
  ideas: number;
  seed_count: number;
  dollars_hidden: boolean;
  by_outcome: ReviewBucket[];
  by_tag: ReviewBucket[];
  by_regime: ReviewBucket[];
  by_idea_type: ReviewBucket[];
  by_rule_type: ReviewBucket[];
  divergence: {
    available: boolean;
    note: string;
    resolved_positions: number;
    open_positions: number;
    option_beat_thesis: number;
    thesis_right_option_won: DivergenceCell;
    thesis_right_option_lost: DivergenceCell;
    thesis_wrong_option_won: DivergenceCell;
    thesis_wrong_option_lost: DivergenceCell;
  };
}

export interface AppSettings {
  default_capital: number;
  default_risk_pct: number;
  public_hide_dollars: boolean;
  options_enabled: boolean;
  default_take_profit_pct: number;
  default_stop_loss_pct: number;
  default_time_stop_days_before_expiry: number;
  risk_free_rate_pct: number;
  account_size: number | null; // null for viewers without the write token
}

export interface Sizing {
  capital_assigned: number | null;
  account_size: number | null;
  risk_pct: number;
  risk_budget: number | null;
  capital_exceeds_risk_budget: boolean;
}

// --- options selector (Phase 5) ---------------------------------------------------------------------------------

export interface OptionLeg {
  contract: string;
  expiry: string;
  strike: number;
  right: "call" | "put";
  side: "long" | "short";
  qty: number;
  bid: number | null;
  ask: number | null;
  mid: number | null;
  iv: number | null;
  iv_source: string;
  oi: number | null;
  volume: number | null;
  delta: number | null;
}

export interface ScenarioGrid {
  prices: number[];
  dates: string[];
  ret_pct: Array<Array<number | null>>;
  values: Array<Array<number | null>>;
  target_row: number | null;
  spot_row: number | null;
  window_end_col: number;
  iv_held: number | null;
  assumption: string;
}

export interface OptionCandidate {
  rank: number | null;
  structure: "long_call" | "long_put" | "debit_spread" | "straddle" | "strangle";
  name: string;
  kind: string;
  expiry: string;
  cushion_days: number;
  cushion: string;
  window_days: number;
  tier_ok: boolean;
  tier_note: string;
  legs: OptionLeg[];
  iv_fallback: boolean;
  passes_filters: boolean;
  filter_reasons: string[];
  debit: number | null;
  structure_bid: number | null;
  structure_ask: number | null;
  spread_width_pct?: number | null;
  leg_width_pct_max?: number | null;
  leg_widths_pct?: Array<number | null>;
  open_interest?: number;
  volume?: number;
  eval_date?: string;
  model_value_at_entry?: number;
  model_vs_mid_pct?: number;
  value_at_spot_window_end?: number;
  ret_at_spot_window_end_pct?: number;
  iv?: number | null;
  iv_source?: string;
  iv_rv_ratio?: number | null;
  iv_percentile_1y?: number | null;
  iv_percentile_note?: string;
  breakevens?: number[];
  breakeven?: number | null;
  target_to_breakeven?: number | null;
  target_to_breakeven_pct?: number | null;
  target_beyond_breakeven?: boolean | null;
  move_spent_to_breakeven_pct?: number | null;
  value_at_target_window_end?: number | null;
  ret_at_target_window_end_pct?: number | null;
  value_at_target_expiry?: number | null;
  ret_at_target_expiry_pct?: number | null;
  pop_pct?: number | null;
  pop_iv?: number | null;
  theta_per_day?: number;
  delta?: number;
  theta_week_pct?: number;
  days_of_theta?: number | null;
  cost_per_contract?: number;
  capital_assigned?: number | null;
  account_size?: number | null;
  risk_budget?: number | null;
  risk_pct?: number;
  capital_exceeds_risk_budget?: boolean;
  contracts?: number | null;
  at_risk?: number | null;
  max_loss_per_contract?: number;
  max_gain_per_contract?: number | null;
  affordable?: boolean;
  score?: number | null;
  score_breakdown?: Record<string, number | string>;
  grid?: ScenarioGrid | null;
  payoff_curve?: Array<[number, number]>;
  why?: string;
  why_source?: "model" | "template";
}

export interface VehicleRow {
  label?: string;
  symbol?: string;
  leverage?: number;
  return_pct: number;
  pnl_abs: number | null;
  capital?: number | null;
  note?: string;
}

export interface SharesComparison {
  target: number | null;
  spot: number;
  move_pct: number | null;
  shares: VehicleRow | null;
  inverse_etf: VehicleRow | null;
  best_option: {
    name: string;
    kind: string;
    return_pct: number;
    return_at_expiry_pct: number | null;
    at_risk: number | null;
    contracts: number | null;
    pnl_abs: number | null;
  } | null;
  verdict: "option" | "shares" | "none";
  verdict_text: string;
}

export interface OptionAnalysis {
  id: number;
  idea_id: number;
  created_at: string;
  chain_as_of: string | null;
  chain_trade_date: string | null;
  spot: number;
  spot_as_of: string;
  spot_source: string;
  iv_percentile_1y: number | null;
  iv_rv_ratio: number | null;
  realized_vol_20d: number | null;
  rate_pct: number;
  verdict: "trade" | "no_trade";
  verdict_text: string;
  candidates: OptionCandidate[];
  shares_comparison: SharesComparison;
  params: {
    inputs?: Record<string, unknown>;
    chain?: Record<string, unknown>;
    sizing?: Sizing;
    counts?: { generated: number; passing: number; by_structure: Record<string, number> };
    verdict_reason?: string;
    rationale?: { model: string; source: string };
    filters?: Record<string, number>;
    score_weights?: Record<string, number>;
    min_return_at_target_pct?: number;
    iv_percentile?: { percentile: number | null; days: number; min_days: number; atm_iv: number | null; note: string };
    errors?: Array<Record<string, unknown>>;
  };
  dollars_hidden: boolean;
}

// --- option positions (Phase 6) ----------------------------------------------------------------------------------

export interface PositionSummary {
  id: number;
  name: string;
  kind: string;
  status: "open" | "closed";
  exit_reason: string | null;
  expiry: string;
  contracts: number | null;
  pnl_pct: number | null;
  pnl_abs: number | null;
  last_value: number | null;
  last_value_as_of: string | null;
}

export interface Divergence {
  thesis_pnl_pct: number | null;
  option_pnl_pct: number | null;
  thesis_right: boolean | null;
  option_won: boolean | null;
  option_beat_thesis: boolean | null;
  cell: "thesis_right_option_won" | "thesis_right_option_lost" | "thesis_wrong_option_won" | "thesis_wrong_option_lost" | null;
  resolved: boolean;
  text: string;
}

export interface PositionLeg {
  contract: string;
  expiry: string;
  strike: number;
  right: "call" | "put";
  side: "long" | "short";
  qty: number;
  entry_bid?: number | null;
  entry_ask?: number | null;
  entry_mid?: number | null;
  entry_iv?: number | null;
  entry_oi?: number | null;
}

export interface OptionSnapshotOut {
  as_of: string;
  value: number;
  bid: number | null;
  ask: number | null;
  pnl_pct: number;
  pnl_abs: number | null;
  spot: number | null;
  iv: number | null;
  delta: number | null;
  theta: number | null;
  source: "chain_mid" | "expiry_intrinsic" | string;
  legs: Array<Record<string, unknown>>;
  created_at: string;
}

export interface PositionOut {
  id: number;
  idea_id: number;
  analysis_id: number | null;
  name: string;
  structure: string;
  kind: string;
  legs: PositionLeg[];
  expiry: string;
  contracts: number | null;
  entry_debit: number;
  entry_cost: number | null;
  entry_as_of: string;
  entry_source: "chain_mid" | "fill" | string;
  entry_spot: number | null;
  take_profit_pct: number;
  stop_loss_pct: number;
  time_stop_days_before_expiry: number;
  time_stop_date: string;
  status: "open" | "closed";
  exit_reason: string | null;
  exit_value: number | null;
  exit_as_of: string | null;
  exit_source: string | null;
  closed_at: string | null;
  pnl_pct: number | null;
  pnl_abs: number | null;
  last_value: number | null;
  last_value_as_of: string | null;
  note: string | null;
  snapshots: OptionSnapshotOut[];
  dollars_hidden: boolean;
  created_at: string;
  updated_at: string;
}

export interface PositionTake {
  candidate_name?: string;
  rank?: number;
  contracts?: number;
  fill_price?: number;
  take_profit_pct?: number;
  stop_loss_pct?: number;
  time_stop_days_before_expiry?: number;
  note?: string;
}
