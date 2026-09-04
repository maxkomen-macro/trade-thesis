# EODHD probe results

Probed on 2026-09-04 (UTC evening) with the account's API token. Token was sourced from the local
environment and is never printed here. Raw responses were kept only in the session scratchpad.

## Account

`GET /api/user` returned HTTP 200:

| field | value |
|---|---|
| subscriptionType | monthly |
| subscriptionMode | paid |
| dailyRateLimit | 100000 |
| extraLimit | 500 |
| apiRequests used today (before probe) | 3290 |

Response header `x-ratelimit-limit: 1200` was observed on every call, which is the per-minute cap.

## 1. Daily bars — `GET /api/eod/{symbol}?from=&to=&fmt=json`

Symbol `USO.US`, `from=2026-08-20&to=2026-09-04`. **HTTP 200**, 12 rows. First row:

```json
{"date": "2026-08-20", "open": 134.71, "high": 135.17, "low": 133.14, "close": 134.54, "adjusted_close": 134.54, "volume": 4434800}
```

Fields: `date, open, high, low, close, adjusted_close, volume`. Note the field is `adjusted_close`, not `adj_close`;
the `price_snapshots.adj_close` column maps from it.

## 2. Latest / delayed price — `GET /api/real-time/{symbol}?fmt=json`

Single symbol `USO.US`. **HTTP 200**:

```json
{"code": "USO.US", "timestamp": 1788552480, "gmtoffset": 0, "open": 140.15, "high": 143.03, "low": 138.01, "close": 141.96, "volume": 3678512, "previousClose": 142.09, "change": -0.13, "change_p": -0.0915}
```

Batched: `GET /api/real-time/SPY.US?s=XLE.US,FXY.US&fmt=json`. **HTTP 200**, returns a JSON array of 3 objects
with the same shape (`code, timestamp, gmtoffset, open, high, low, close, volume, previousClose, change, change_p`).
`timestamp` is Unix seconds UTC. Batching via `s=` works and costs one request per call, so the resolver
should batch all open-idea symbols into one call.

## 3. Symbol search — `GET /api/search/{query}`

Query `united states oil`, `limit=5`. **HTTP 200**, 3 hits. First hit:

```json
{"Code": "USO", "Exchange": "US", "Name": "United States Oil Fund LP", "Type": "ETF", "Country": "USA", "Currency": "USD", "ISIN": "US91232N2071", "isPrimary": false, "previousClose": 141.989, "previousCloseDate": "2026-09-04"}
```

Fields: `Code, Exchange, Name, Type, Country, Currency, ISIN, isPrimary, previousClose, previousCloseDate`.
EODHD symbol = `Code + "." + Exchange` (`USO.US`). `Type` values seen: `ETF`; stocks report `Common Stock`.

## 4. Options chain — AVAILABLE (UnicornBay marketplace add-on activated 2026-09-04)

History: the first probe earlier on 2026-09-04 returned **403 Forbidden** on every marketplace path (no entitlement).
The owner then activated the add-on and the same token was re-probed the same day. Everything below is from live
responses; raw bodies were kept in the session scratchpad only.

### Endpoints (all under `https://eodhd.com/api/mp/unicornbay/options`)

| endpoint | purpose | verified |
|---|---|---|
| `GET /contracts` | latest chain snapshot (as of last close; `bid_date`/`ask_date` carry timestamps) | 200 for USO, AAPL |
| `GET /eod` | daily history per contract; row `id` = `{contract}-{YYYY-MM-DD}` record date | 200 |
| `GET /underlying-symbols` | 6,944 symbols with listed options; USO, XLE, FXY, SPY, BNO all present | 200 |
| `GET /api/options/{sym}` (legacy, outside `/mp`) | retired | 404 `Ticker Not Found.` |

### Request contract (learned from live 200/422 responses)

- Body is JSON:API: `{"meta": {"offset", "limit", "fields": [...]}, "data": [{"id", "type", "attributes": {...}}]}`.
  No `total` in `meta` for `/contracts`; page until a short page.
- One of `filter[underlying_symbol]` or `filter[contract]` is **required** (422 otherwise). Symbols are bare (`USO`, not `USO.US`).
- Filters honored: `filter[exp_date_from]`, `filter[exp_date_to]`, `filter[type]=call|put`, `filter[strike_from]`,
  `filter[strike_to]`, `filter[tradetime]`, `filter[tradetime_from]`, `filter[tradetime_to]`. Unknown filter keys are
  silently ignored, so misspellings do not error.
- **`/contracts` returns expired contracts (2023 expiries seen) unless `filter[exp_date_from]` is set.** Always set it.
- `sort` accepts a single key: `exp_date`, `-exp_date`, `strike` verified; `dte` and comma-joined keys return 422.
  Ordering is not stable across pages for ties, so page with a strike band and dedupe by `contract`.
- `page[limit]` max **1000** (422 above); `page[offset]` works.
- `fields[options-contracts]=a,b,c` / `fields[options-eod]=...` trims attributes and payload (~285 KB per 1000 rows even trimmed).

### Response fields (43, from `meta.fields`)

`contract, underlying_symbol, exp_date, expiration_type, type, strike, exchange, currency, open, high, low, last,
last_size, change, pctchange, previous, previous_date, bid, bid_date, bid_size, ask, ask_date, ask_size, moneyness,
volume, volume_change, volume_pctchange, open_interest, open_interest_change, open_interest_pctchange, volatility,
volatility_change, volatility_pctchange, theoretical, delta, gamma, theta, vega, rho, tradetime, vol_oi_ratio, dte, midpoint`

Mapping to `chain_snapshots`: `volatility` → `iv` (decimal, e.g. `0.3881` = 38.8%), `midpoint` → `mid`,
`open_interest` → `oi`, `type` → `right`, `exp_date` → `expiry`. `tradetime` is the **last trade date** and can be
null for untraded contracts; the record date comes from the `/eod` row `id`, not from `tradetime`.

Sample live row (USO, 2026-09-04): `USO260911C00140000` call 140 exp 2026-09-11, bid 4.15 / ask 4.75 / mid 4.45,
IV 0.3881, OI 2779, dte 8.

### Sizing for the storage band (120 days, ±25% of spot)

USO on 2026-09-04, `exp_date` 2026-09-04..2027-01-02, strikes 106..178: **2 pages, 1,356 contracts, 0 duplicates**,
12 expiries (weeklies through Oct, then monthly), 540 with OI ≥ 100, 0 with null IV. Expiry discovery in one cheap
call: a tight strike band (`strike_from=140&strike_to=144`, `fields=exp_date`) listed all 21 live expiries.

### History for IV percentile

`/eod?filter[contract]=USO260925C00141000` returns one row per trading day with bid/ask/mid/IV/delta/OI. This means
the 1-year IV percentile can be **backfilled from EODHD** for at-the-money contracts instead of waiting a year of
our own `chain_snapshots`. Decision for Phase 5.

### Quota

`/api/user` `apiRequests` stayed at 3,290 across ~40 marketplace calls, so marketplace usage is not visible on that
counter and the per-call cost is unknown. Header `x-ratelimit-limit: 1200` per minute applies. The resolver should
budget ~3 calls per instrument per day (discovery + 2 band pages).

**Consequence for the build:** `settings.options_enabled` was set to `true` in the database on 2026-09-04 21:20 UTC.
Phases 5–6 are unblocked. Rerun `make probe-eodhd` any time; it exits with the entitlement state.

## Not on this plan (by design)

Fundamental Data and Calendar Data are off. Earnings dates are therefore manual input (`catalyst_date`).
