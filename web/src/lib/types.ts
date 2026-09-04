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
