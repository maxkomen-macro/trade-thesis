# Regime push from Macro Regime Radar

Radar's FastAPI backend is localhost-only and stays that way, so Trade Thesis never calls it at runtime. Instead
Radar pushes its daily regime into Trade Thesis, and every idea logged afterwards is stamped with the latest stored
row.

## Endpoint

`POST {TRADE_THESIS_URL}/api/jobs/regime`
`Authorization: Bearer <CRON_SECRET>` (the write token `TT_WRITE_TOKEN` is also accepted).

Body: the JSON Radar's own `GET /api/regime/latest` returns, forwarded unchanged. Optional `source` (default `radar`).

```json
{
  "date": "2026-09-03",
  "label": "Goldilocks",
  "confidence": 0.71,
  "growth_trend": 0.4,
  "inflation_trend": -0.2,
  "prob_goldilocks": 0.71,
  "prob_overheating": 0.12,
  "prob_stagflation": 0.09,
  "prob_recession": 0.08
}
```

Response: the stored readout (`available`, `as_of`, `regime`, the probabilities, `source`, `stored_at`, `age_days`).
The push is idempotent per `(date, source)`: re-posting the same day replaces the row.

## Local test

```bash
curl -sS -X POST http://127.0.0.1:8001/api/jobs/regime \
  -H "Authorization: Bearer $CRON_SECRET" -H "Content-Type: application/json" \
  -d '{"date":"2026-09-03","label":"Goldilocks","confidence":0.71,"prob_goldilocks":0.71,"prob_overheating":0.12,"prob_stagflation":0.09,"prob_recession":0.08}'
```

## GitHub Action sketch (lives in the Radar repo, owner-maintained)

Run the Radar API locally in the job (or read the regime straight from Radar's database), then:

```yaml
- name: Push regime to Trade Thesis
  run: |
    curl -sS -X POST "$TT_URL/api/jobs/regime" \
      -H "Authorization: Bearer $TT_CRON_SECRET" -H "Content-Type: application/json" \
      -d "$(curl -sS http://127.0.0.1:8000/api/regime/latest)"
  env:
    TT_URL: ${{ secrets.TT_URL }}
    TT_CRON_SECRET: ${{ secrets.TT_CRON_SECRET }}
```

## Where it shows up

- Top bar: latest regime, confidence, probability bar; flagged as stale after 4 days.
- `GET /api/status`: "Radar regime feed" row with the age of the latest snapshot.
- Ideas: `radar_regime` and `radar_probs_json` are stamped from the latest row at creation (null when empty).
- Review: hit rate by regime (Phase 4).
