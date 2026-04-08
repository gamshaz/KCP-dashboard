# CLAUDE.md — Rates Desk Trading Dashboard

Read this file and `KCP_dashboard_plan.md` in full at the start of every session before doing anything. All architectural decisions are finalised in the plan — proceed directly to coding.

---

## Project

A rates desk trading dashboard for SOFR futures and options pricing and scenario analysis. Python + Streamlit, localhost only, Bloomberg Desktop API as primary data source. Phase 1 covers SR3 (3-month SOFR futures) only.

**Working directory:** the folder containing this file.

---

## Python tooling

- Python 3.14 via `uv`
- Virtual environment at `.venv/` — create with `uv venv`, install with `uv pip install`
- `blpapi` is NOT on PyPI — install manually from the Bloomberg Developer Portal
- Run the app with: `uv run streamlit run dashboard/app.py`

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
| 2 — Date / product / rates | `utils/date_utils.py`, `products/base.py`, `products/sofr.py`, `rates_engine.py` | 🔄 In progress — `rates_engine.py` not yet written |
| 3 — Scenario engine | `scenario_engine.py` | ⬜ Pending |
| 4 — Options pricer | `options_pricer.py` | ⬜ Pending |
| 5 — Strategy layer | `skew_logic.py`, `trade_structures.py`, `preferences.py` | ⬜ Pending |
| 6 — Trade builder / ranker | `trade_builder.py`, `ranker.py` | ⬜ Pending |
| 7 — Dashboard UI | `dashboard/scenario_panel.py`, `dashboard/trade_panel.py`, `dashboard/greeks_panel.py`, `dashboard/app.py` | ⬜ Pending |

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
