# CLAUDE.md — Rates Desk Trading Dashboard

Read this file and `KCP_dashboard_plan.md` in full at the start of every session before doing anything. All architectural decisions are finalised in the plan — proceed directly to coding.

---

## Project

A rates desk trading dashboard for SOFR futures and options pricing and scenario analysis. Python + Streamlit, localhost only, Bloomberg Desktop API as primary data source. Phase 1 covers SR3 (3-month SOFR futures) only.

**Working directory:** the folder containing this file.

---

## Python tooling

- Python 3.12+ (3.14 via `uv` was the original plan but `uv` is not required — plain pip works fine)
- Virtual environment at `.venv/` in the repo root:
  ```
  python -m venv .venv
  .venv\Scripts\pip install -r requirements.txt
  ```
- `.venv/` is gitignored — must be recreated on each machine
- `blpapi` is NOT on PyPI — install from Bloomberg's own pip index **after** the above:
  ```
  .venv\Scripts\pip install --index-url=https://blpapi.bloomberg.com/repository/releases/python/simple blpapi
  ```
  This fetches a pre-built wheel (no C++ compiler needed). Bloomberg Terminal must be running when this is installed on a BBG machine.
- Run the app with: `.venv\Scripts\streamlit run dashboard\app.py`
- The app auto-detects whether `blpapi` is importable. If not (or if `USE_MOCK=1`), it runs in mock mode with synthetic data.

---

## Workflow

- Write one file at a time
- After each file, explain what was done and wait for explicit confirmation before moving to the next file
- Update this file and `KCP_dashboard_plan.md` whenever a build decision is made or a sprint status changes

---

## Build sequence

| Sprint | Files | Status |
|--------|-------|--------|
| 1 — Data layer | `utils/cache.py`, `data/base.py`, `bloomberg.py` | ✅ Complete |
| 2 — Date / product / rates | `utils/date_utils.py`, `products/base.py`, `products/sofr.py`, `rates_engine.py` | ✅ Complete |
| 3 — Scenario engine | `scenario_engine.py` | ✅ Complete |
| 4 — Options pricer | `options_pricer.py` | ✅ Complete |
| 5 — Strategy layer | `skew_logic.py`, `trade_structures.py`, `preferences.py` | ✅ Complete |
| 6 — Trade builder / ranker | `trade_builder.py`, `ranker.py` | ✅ Complete |
| 7 — Dashboard UI | `dashboard/scenario_panel.py`, `dashboard/trade_panel.py`, `dashboard/greeks_panel.py`, `dashboard/app.py` | ✅ Complete |

---

## Decisions made during coding (not in plan)

### WIRP arithmetic sign correction
The plan's formula `P(cut) = expected_change / 0.25` has a sign error — it produces negative probabilities for cuts. Corrected implementation in `bloomberg.py`:

```python
expected_change_bp = (back_rate - front_rate) * 100  # signed: negative=cut, positive=hike
p_move = min(1.0, abs(expected_change_bp) / 25.0)
p_hold = 1.0 - p_move
outcomes = {0: p_hold, -25: p_move}  # cut
outcomes = {0: p_hold, +25: p_move}  # hike
```

Hikes are fully supported — the original clamp to `[0, 1]` was removed. User confirmed this approach.

### OMON scenario binding
`BloombergDataSource._omon_pulled_for` holds the `scenario_id` the OMON was last pulled for. The app layer (`dashboard/app.py`) must set this when the Pull Options button fires.

### blpapi ticker convention
FF contracts use the `"{code} Comdty"` suffix, e.g. `"FF1 Comdty"`. SR3 contracts likewise, e.g. `"SR3H Comdty"`. OMON tickers are returned raw from BBG's `CHAIN_TICKERS` field and used as-is.

---

## Current status (as of 2026-04-21)

### What works
- Full backend is built and tested (Sprints 1–6)
- Dashboard UI skeleton exists (Sprint 7) but has not been validated against live Bloomberg data
- `blpapi` successfully connects to Bloomberg Terminal on Windows via the pip index above

### Known issues requiring fixes before the dashboard is usable

**1. SR3 ticker format is wrong**
`config/products.yaml` has SR3 contract codes without year suffixes (e.g. `SR3H`, `SR3M`). Bloomberg requires full codes including the year, e.g. `SR3H6`, `SR3M6`, `SR3U6`, `SR3Z6` (single-digit year for near contracts) or `SR3H26`. On first live run, Bloomberg returned "Unknown/Invalid Security" for all SR3 tickers. The config and any ticker-generation logic in `products/sofr.py` need to be corrected.

**2. Dashboard UI needs full redesign**
The trader reviewed the Sprint 7 dashboard and wants it rebuilt from scratch — layout, panel structure, and feature implementation are all to be redone. The backend logic (Sprints 1–6) is reusable; only the `dashboard/` folder needs replacing. **Do not start the redesign until the trader specifies the new layout and requirements.**
