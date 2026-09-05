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
}

export interface JobSummary {
  job: string;
  as_of: string;
  ideas_checked: number;
  instruments_refreshed: number;
  bars_added: number;
  resolved: Array<Record<string, unknown>>;
  errors: Array<Record<string, unknown>>;
}
