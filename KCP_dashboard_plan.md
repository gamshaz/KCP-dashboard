# Rates Desk Trading Dashboard
## Project Planning Document
### SOFR Futures — Phase 1
*Confidential — Internal Use Only*

---

# 1. Project Overview

This document is the complete planning record for a rates desk trading dashboard built in Python. It covers architectural decisions, module specifications, and all design choices made during the planning phase. The document is intended to allow work to resume in a new session without loss of context.

The dashboard is a pricer and scenario tool for the rates desk covering US, UK, and European rates. Phase 1 covers SOFR futures and options only. The architecture is modular to allow SONIA, EURIBOR, bond futures, and gilt futures to be added in later phases.

---

# 2. Key Architectural Decisions

| **Decision** | **Chosen Approach** |
| --- | --- |
| **Language** | Python. VBA rejected — no package ecosystem, no version control, not maintainable. |
| **Frontend** | Streamlit, locally hosted. Green light received from desk. |
| **Data source (primary)** | Bloomberg API (blpapi). Desktop API only. WIRP and OMON pulls. |
| **Data source (fallback)** | IBKR — separate project, built after BBG version is complete. |
| **PricingMonkey** | No API available. Scraping rejected (compliance risk, fragility). Black-76 pricer built independently. |
| **Excel role** | Validation only during development. Existing SONIA/EURIBOR Excel pricers used as reference to validate Python ports. Not in critical path. |
| **Hosting** | Localhost only. No web hosting — compliance risk of routing market data through external servers. |
| **Vol skew model** | Discrete OMON strike ladder only. No continuous curve fitting. All comparisons at real tradeable strikes. |
| **Day count convention** | Each product file handles its own convention. date_utils.py provides raw business day counts only. |
| **SOFR contract scope** | 3-month SOFR futures (SR3) only for Phase 1. |
| **Holiday calendar** | CME calendar, hardcoded. Can switch to BBG pull later. |
| **FOMC meeting dates** | Hardcoded 2-year rolling window. Can switch to BBG pull later. |
| **val_date override** | Moves the time-to-expiry clock only. Vol surface stays fixed at OMON pull. Vol, underlying price, and date can all be overridden manually and independently. Changing val_date does NOT trigger a BBG data pull. |
| **Scenario scenarios** | Both rule-based shorthand (expands to meeting list) and explicit meeting-by-meeting overrides. Saveable locally. |
| **Simultaneous scenarios** | Default one at a time. Multi-scenario display available on request. |
| **Remaining meetings (partial scenario)** | Inherit WIRP-implied expected outcome. |
| **BBG API budget** | Hard limit: 150k hits/day (well within 500k/user/day). Startup pull: WIRP + futures only. OMON pulled separately on explicit "Pull Options" button. Only an explicit Refresh Data button triggers a full re-pull. val_date, vol, underlying price, and scenario changes never trigger BBG calls. UI shows last-pulled timestamp per data type. |
| **BBG session type** | Desktop API. Synchronous calls. Session initialised once at startup via st.cache_resource. |
| **WIRP pull method** | Pull FF1–FF24 Fed Funds futures prices via BDP. Compute meeting-implied probabilities using standard WIRP arithmetic in bloomberg.py. blpapi cannot access the WIRP calculated screen directly. |
| **OMON pull method** | Two-stage. Stage 1: BDS call with CHAIN_TICKERS field to get option tickers per expiry. Stage 2: batched BDP across filtered tickers for implied vol, last price, delta, strike, expiry date. Pull anchored to scenario target strike ±5 strikes, not ATM. |
| **OMON pull trigger** | Explicit "Pull Options" button only. Fires after trader selects a scenario. Never fires on startup or on scenario change. Stale flag shown if scenario changes after pull. |
| **SOFR-FFR spread** | Explicit user-facing parameter in scenario panel. WIRP gives FFR-implied path; spread converts to SOFR path. Overridable per scenario. Default stored in products.yaml. |
| **Kink detection mode** | Two variants via dropdown. SELL_CHEAP: kinked-down is SELL_TARGET. FADE_KINK: kinked-down is BUY_TARGET. Stored as kink_mode enum on TraderPreferences. |
| **Option expiry mapping** | Hardcoded in sofr.py. Three serial months of the chosen quarterly contract only. June options on September underlying excluded — not traded in practice. Calendar structures deferred to Phase 2. |
| **Trade structure scope** | Full universe considered by builder for every scenario. Single-expiry structures only in Phase 1. |
| **Strike universe** | ±5 strikes (±31.25bp at 6.25bp spacing) around scenario target strike per expiry. |
| **Preference filtering** | Hard filters applied by trade_builder.py before ranking: FLAT removes non-zero delta structures, RISK_OFF removes long-premium, VOL_DOWN removes net positive vega. |
| **Settlement target range** | Set explicitly by trader. Default when not set: three symmetric bands ±6, ±12, ±18 ticks around scenario midpoint. Three independent ranked lists, one per band. |
| **Explicit range** | Single trader-defined range replaces all three defaults. Lower and upper bounds are independent (asymmetric allowed). |
| **Ranking — Phase 1** | No composite score. Three independent columns: R:R (long-premium only), theta sign (binary POSITIVE/NEGATIVE), market distance (informational only, not used in sorting). Trader sorts manually. |
| **R:R definition** | max_pnl within range / abs(net_premium). Long-premium structures only. N/A for short-premium. |
| **Theta score** | Binary sign only in Phase 1. POSITIVE = receiving theta. NEGATIVE = paying theta. |
| **Market distance** | Ticks between scenario target price and current live futures price. Informational display only. |
| **Scenario outcome format** | Numeric bp values only: 0bp, -25bp, -50bp, +25bp. No "hold" string — 0bp used everywhere including UI dropdown. |

---

# 3. Module Map

The project is structured in five layers. Dependencies flow strictly downward — no module imports from a layer above it.

## Layer 1 — Data
- `data/base.py` — Abstract DataSource class defining the interface all sources implement
- `bloomberg.py` — Implements DataSource. blpapi wrapper for WIRP, OMON, futures prices
- `pricing_monkey.py` — Stub. Returns NotImplementedError

## Layer 2 — Core
- `rates_engine.py` — Curve builder. SOFR meeting-step logic. WIRP base curve and scenario curves
- `scenario_engine.py` — Custom and template scenarios. val_date management. Overlay on rates engine
- `options_pricer.py` — Black-76. Greeks. Scenario PnL. Manual vol override

## Layer 3 — Products
- `products/base.py` — Abstract Product class
- `products/sofr.py` — SOFR-specific contract specs, expiry conventions, Actual/360
- `products/sonia.py` — Stub. Reference: existing Excel pricer
- `products/euribor.py` — Stub. Reference: existing Excel pricer

## Layer 4 — Strategy
- `trade_structures.py` — Payoff definitions for all structure types. Pure data, no logic
- `skew_logic.py` — Discrete vol ladder analysis. Kink detection. Wing selection
- `preferences.py` — TraderPreferences dataclass. open_risk, vol_view, theta_propensity
- `trade_builder.py` — Constructs and evaluates trade candidates from scenario, target range, preferences
- `ranker.py` — R:R, theta sign, market distance. Three independent ranked lists per range band

## Layer 5 — Output
- `dashboard/scenario_panel.py` — Scenario builder UI. Template selector. Date override. Pull Options button
- `dashboard/trade_panel.py` — Ranked trade display. Range tabs. Structure detail with manual strike editing
- `dashboard/greeks_panel.py` — Aggregate Greeks. Scenario PnL chart. Time decay
- `dashboard/app.py` — Streamlit entry point. Session state. Refresh logic

## Config and Utils
- `config/products.yaml` — Product registry
- `config/scenarios.yaml` — Template scenario library
- `utils/date_utils.py` — CME calendar. IMM expiries. Business day arithmetic
- `utils/cache.py` — In-memory cache. No TTL. Explicit invalidation only

---

# 4. Finalised Module Specs

## 4.1 date_utils.py

**Owns:** All date arithmetic. No other module computes dates independently.

**Functions exposed:**
- `get_fomc_dates(from_date, to_date)` — returns FOMC meeting dates in window
- `get_imm_expiry(year, month)` — third Wednesday of month, CME holiday adjusted
- `get_contract_expiries(n, from_date)` — next n IMM expiry dates
- `business_days_between(date_a, date_b)` — CME business day count. Returns raw integer. Year fraction conversion done by each product file
- `is_good_business_day(date)` — bool. Used internally and by product files

**Does not own:**
- Day count fractions or year fraction conventions — these live in each product file
- val_date state — held in scenario_engine.py and passed as an argument

---

## 4.2 rates_engine.py

**Owns:** Building the WIRP base curve and scenario-overridden curves as theoretical futures prices. Outputs in price terms (100 minus rate) to match CME convention.

**The two curves:**
- WIRP base curve (orange) — probability-weighted expected rate path from WIRP
- Scenario curve (blue) — user-defined meeting-by-meeting path overlaid on same chart

**Inputs:**
- `wirp_data` — meeting date to probability distribution dict from bloomberg.py
- `live_futures_prices` — contract code to market price dict from bloomberg.py
- `current_sofr_fixing` — today's effective SOFR, pulled from BBG on init
- `val_date` — from scenario_engine.py, defaults to today

**SOFR-FFR spread:**
- WIRP prices off Fed Funds Target Rate, not SOFR directly
- Configurable spread (default in products.yaml) converts FFR path to SOFR path
- User-facing parameter in scenario panel, overridable per scenario

**WIRP base curve logic:**
- For each FOMC meeting: `expected_change = sum(probability[outcome] x bp_change[outcome])`
- Apply incrementally from current_sofr_fixing to produce meeting-by-meeting expected FFR level
- Apply SOFR-FFR spread to convert FFR path to SOFR path

**Settlement price compounding:**
For each SR3 contract covering calendar days d1 to d2:
- Identify all FOMC meetings within [d1, d2]
- Split period into segments by meeting dates
- Each segment uses the cumulative expected rate at its start
- Geometric compound: product of (1 + rate_i x days_i / 360) across all segments
- Convert to annualised rate then to futures price: 100 - rate
- Encapsulated in `compute_settlement_price(contract_start, contract_end, rate_path)`

**Functions exposed:**
- `get_wirp_curve()` — contract code to theoretical price, WIRP path
- `get_scenario_curve(scenario_path)` — contract code to theoretical price, scenario path
- `get_live_curve()` — raw live futures prices from BBG, passed through unchanged
- `get_rich_cheap(scenario_path)` — contract code to price tick difference between scenario curve and live curve

---

## 4.3 options_pricer.py

**Owns:** Black-76 pricing for SOFR options. Greeks. Scenario PnL. Manual override capability.

**Inputs per option leg:**
- Forward rate — from scenario engine via rates engine
- Strike — from OMON live chain
- Time to expiry — from date_utils.py business_days_between(val_date, expiry), converted to years by sofr.py
- Implied vol — from OMON, or manually overridden

**Outputs:**
- Theoretical value
- Delta, gamma, vega, theta
- Structure-level Greeks — aggregated across all legs
- Rich/cheap signal — gap between OMON market price and theoretical value

**Manual override behaviour:**
- `vol_override` — optional float. Supersedes OMON-sourced vol entirely
- `val_date` — moves clock only. Vol surface unchanged
- `underlying_price` — optional float. Supersedes live BBG price
- All three overrides are independent of each other

---

## 4.4 skew_logic.py

**Owns:** Discrete vol ladder analysis from OMON. Wing selection. Kink detection. No interpolation, no continuous curve fitting.

**Kink detection — discrete second difference:**
`vol[K] - 0.5 x (vol[K-1tick] + vol[K+1tick])`

**Two modes via dropdown (kink_mode enum on TraderPreferences):**
- `SELL_CHEAP` (default): kinked-down is SELL_TARGET, kinked-up is BUY_TARGET
- `FADE_KINK`: kinked-down is BUY_TARGET, kinked-up is SELL_TARGET
- No material kink → NEUTRAL in both modes

**Wing selection logic:**
- For fly centred at K: compares implied vols at K-6, K+6, K-12, K+12, and broken combinations
- Recommends placement maximising vol differential in favour of structure
- Kink threshold configurable in products.yaml

**Output — SkewAnalysis dataclass:**
- Full discrete vol ladder: strike to (implied vol, bid vol, offer vol)
- `wing_recommendation` per candidate structure width
- `kink_flags`: strike to SELL_TARGET | BUY_TARGET | NEUTRAL

**Does not own:**
- Continuous vol curve fitting
- Interpolation between strikes
- Vol at strikes not present in OMON chain

---

## 4.5 preferences.py

**Owns:** TraderPreferences dataclass only. No logic. Validated data container.

**Fields:**
- `open_risk` — tri-state enum: OPEN_RISK | FLAT | RISK_OFF
- `vol_view` — enum: VOL_UP | NEUTRAL | VOL_DOWN
- `theta_propensity` — float [0, 1]
- `kink_mode` — enum: SELL_CHEAP | FADE_KINK

---

## 4.6 trade_structures.py

**Owns:** Pure structure definitions. No pricing logic. Dataclasses only.

**Structure types defined:**
- Fly — regular (symmetric) and broken (asymmetric), 6-wide and 12-wide
- Condor — regular and broken
- Call spread, put spread
- Ladder
- Calendar (stub — Phase 2 only)

**Each structure dataclass holds:**
- Leg definitions: strike offsets, quantities, put/call flag
- Width parameters
- `compute_payoff(terminal_rate)` method

---

## 4.7 scenario_engine.py

**Owns:** val_date management. Scenario construction and persistence. Rate path assembly. Settlement target range management.

**Does not own:**
- Curve computation — delegates to rates_engine.py
- Options pricing — delegates to options_pricer.py
- BBG data pulls — consumes outputs from bloomberg.py
- Date arithmetic — delegates to date_utils.py

### Inputs
- `wirp_data` — from bloomberg.py
- `live_futures_prices` — from bloomberg.py
- `current_sofr_fixing` — from bloomberg.py
- `val_date` — defaults to today

### Core Concepts

**Rate path assembly:**
Two layers — explicit overrides for meetings the trader has assigned, WIRP inheritance for all remaining meetings. Assembled path passed to rates_engine.py.

**Quarterly contract and option expiry mapping:**
- Trader selects one quarterly SR3 contract
- sofr.py returns the three associated serial option expiries
- Hardcoded in sofr.py, not derived dynamically from OMON
- Single-expiry structures only in Phase 1

### Scenario Formats

**Rule-based shorthand:**
```yaml
name: Hold Through Year End
rules:
  - meeting_window: [2026-03, 2026-12]
    outcome: 0bp
```

**Explicit meeting-by-meeting:**
```yaml
name: Two Cuts H2
meetings:
  2026-03-19: 0bp
  2026-05-07: 0bp
  2026-07-30: -25bp
  2026-09-17: -25bp
```

Rule-based shorthands expand to explicit meeting lists on load. Internally only explicit lists are used.

### Dataclasses

**Scenario:**
- `name` — str
- `meetings` — dict[date, int] — bp change per meeting. Explicit meetings only.
- `sofr_ffr_spread_bp` — float
- `is_custom` — bool

**SettlementTargetRange:**
- `contract` — str
- `lower_bp` — float (ticks below midpoint)
- `upper_bp` — float (ticks above midpoint)
- `is_default` — bool

When `is_default` is True, three instances are generated automatically (±6, ±12, ±18). When trader sets explicit bounds, single instance with `is_default = False` replaces all three defaults.

### Functions Exposed

**Scenario management:**
- `load_templates()` → list[Scenario]
- `build_custom_scenario(name, meetings, sofr_ffr_spread_bp)` → Scenario
- `save_scenario(scenario)` — persists to local flat file
- `load_saved_scenarios()` → list[Scenario]
- `delete_scenario(name)`

**Rate path assembly:**
- `assemble_rate_path(scenario, wirp_data)` → dict[date, float]

**val_date management:**
- `set_val_date(date)`
- `get_val_date()` → date
- `reset_val_date()`

**Settlement target range:**
- `get_default_ranges(contract, scenario)` → list[SettlementTargetRange] — three defaults
- `set_explicit_range(contract, lower_bp, upper_bp)` → SettlementTargetRange
- `clear_explicit_range(contract)`
- `get_active_ranges(contract, scenario)` → list[SettlementTargetRange] — single call for trade_builder.py

### Session State
- `active_scenario`
- `val_date_override`
- `explicit_ranges` — dict[contract_str, SettlementTargetRange]

### Multi-scenario Display
- Default: one active scenario
- Multi-scenario mode: multiple scenarios on same chart vs WIRP. Display only — trade builder operates on one scenario at a time.

### Persistence
Custom scenarios saved as YAML in expanded explicit form. Never re-serialised as rule-based shorthand.

---

## 4.8 trade_builder.py

**Owns:** Generating the full universe of candidate structures. Evaluating P&L across active settlement target ranges. Passing evaluated candidates to ranker.py.

**Does not own:**
- Ranking — delegates to ranker.py
- Vol analysis — consumes skew_logic.py outputs
- Options pricing — delegates to options_pricer.py

### Inputs
- `omon_chain` — dict[expiry, dict[strike, OptionQuote]]
- `scenario_curve` — from rates_engine.get_scenario_curve()
- `active_ranges` — list[SettlementTargetRange] from scenario_engine.get_active_ranges()
- `skew_analysis` — SkewAnalysis from skew_logic.py
- `preferences` — TraderPreferences
- `val_date` — from scenario_engine.get_val_date()

### Strike Universe
Scenario target strike = strike on OMON chain nearest to scenario settlement price. Builder considers ±5 strikes (±31.25bp) around target strike per expiry. Strike spacing: 6.25bp.

### Candidate Generation
Iterates over all three serial expiries independently. Generates all valid instances of every structure type from trade_structures.py within the ±5 strike window. Leg prices sourced from OMON mid. options_pricer.py called once per leg.

**Premium sign convention:**
- Net debit → long-premium structure
- Net credit → short-premium structure

### P&L Evaluation
For each candidate and each active SettlementTargetRange:
- Fine grid across [lower_bound, upper_bound] at 1-tick step
- compute_payoff(terminal_rate) at each grid point
- Net P&L = payoff minus premium paid (or plus premium received)
- Stored per candidate per range: min_pnl, max_pnl, mean_pnl

### Preference Filtering
Hard filters before passing to ranker:
- `FLAT` → remove non-zero net delta structures
- `RISK_OFF` → remove long-premium structures
- `VOL_DOWN` → remove net positive vega structures

### Output — CandidateStructure dataclass
- `expiry` — date
- `structure_type` — enum
- `legs` — list of leg definitions
- `net_premium` — float (positive = debit, negative = credit)
- `is_long_premium` — bool
- `greeks` — delta, gamma, vega, theta
- `theta_sign` — POSITIVE | NEGATIVE
- `kink_flags` — per-leg kink signal
- `pnl_by_range` — dict[range_width_ticks, PnLProfile]
- `market_distance_ticks` — int (informational only)

---

## 4.9 ranker.py

**Owns:** Scoring and sorting candidates. One ranked list per active settlement target range.

**Does not own:**
- Candidate generation — trade_builder.py
- Pricing — options_pricer.py

### Inputs
- `candidates` — list[CandidateStructure]
- `active_ranges` — list[SettlementTargetRange]

### Scoring (Phase 1)

**R:R score:**
- Long-premium only: max_pnl / abs(net_premium)
- Short-premium: N/A

**Theta sign:**
- Binary: POSITIVE (receiving) | NEGATIVE (paying)
- Display column only, not used for sorting

**Market distance:**
- market_distance_ticks carried through unchanged
- Informational display only, not used for sorting

### Ranking
Per active range: long-premium sorted by R:R descending. Short-premium grouped below, sorted by mean_pnl descending.

### Output Dataclasses

**RankedList:**
- `range_width_ticks` — int
- `is_default` — bool
- `long_premium` — list[RankedCandidate]
- `short_premium` — list[RankedCandidate]

**RankedCandidate:**
- `candidate` — CandidateStructure
- `rr_score` — float or None
- `theta_sign` — POSITIVE | NEGATIVE
- `market_distance_ticks` — int
- `pnl_profile` — PnLProfile for this range

### Phase 2 Reminder
Upgrade to weighted composite scoring: placeholder weights in products.yaml, theta_propensity from TraderPreferences wired as multiplier on theta component. Replace binary theta sign with continuous theta score. Add vol_view as vega-score multiplier.

---

## 4.10 bloomberg.py

**Owns:** All blpapi session management. WIRP-implied probability computation. Live futures price pulls. OMON chain pulls. Caching. Stale data flagging.

**Implements:** data/base.py DataSource interface.

### Session Management
Desktop API. Synchronous. Session initialised once at startup and held open.

```python
import blpapi

def _create_session() -> blpapi.Session:
    opts = blpapi.SessionOptions()
    opts.setServerHost("localhost")
    opts.setServerPort(8194)
    session = blpapi.Session(opts)
    if not session.start():
        raise ConnectionError("Bloomberg session failed to start")
    if not session.openService("//blp/refdata"):
        raise ConnectionError("Failed to open //blp/refdata")
    return session
```

### Caching
All pulled data cached in utils/cache.py. No TTL. Cache keys:
- `"wirp"`
- `"futures_prices"`
- `"omon:{contract}:{scenario_id}"`

### WIRP Pull
Pull FF1–FF24 via BDP field `PX_LAST`. Compute meeting-implied probabilities per meeting:

1. Identify front contract (expires same month as meeting) and back contract (expires month after)
2. implied rate before meeting = 100 - front price
3. implied rate after meeting = 100 - back price
4. expected_change_bp = (back implied rate - front implied rate) × 100  — signed: negative=cut, positive=hike
5. p_move = min(1.0, abs(expected_change_bp) / 25)
6. outcomes = {0: 1−p_move, −25: p_move} if cut; {0: 1−p_move, +25: p_move} if hike

Note: the original formula `P(cut) = expected_change / 0.25` had a sign error (produces negative probabilities for cuts) and silently discarded hike pricing. Corrected during Sprint 1 implementation. Hikes fully supported — no clamping.

Known limitation: single-move-or-hold model only. Multi-outcome (50bp) extension is Phase 2.

**Output — ProbabilityDistribution dataclass:**
- `meeting_date` — date
- `outcomes` — dict[int, float] e.g. {0: 0.65, -25: 0.35}
- `expected_change_bp` — float

### Live Futures Pull
SR3 front 8 quarters via BDP field `PX_LAST`. Returns dict[contract_code, float].

### OMON Pull
Two-stage. Triggered only by explicit "Pull Options" button.

**Stage 1:** BDS call with `CHAIN_TICKERS` field per expiry (3 calls). Returns option ticker list.

**Stage 2:** Filter to ±5 strikes around scenario target strike. Batched BDP across filtered tickers.

**BBG fields per strike:**
- `IVOL_MID` — implied vol
- `PX_LAST` — last price
- `DELTA_MID` — delta
- `STRIKE_PX` — strike
- `OPT_EXPIRE_DT` — expiry date

**Output — OptionQuote dataclass:**
- `strike` — float
- `expiry` — date
- `implied_vol` — float
- `last_price` — float
- `delta` — float
- `underlying` — str

**Estimated data hits per options pull:** ~168 (3 BDS + ~165 BDP)

### Stale OMON Detection
On scenario change, app checks cached OMON key against current scenario_id. If mismatch, stale flag set. Cached data not cleared — remains accessible but flagged.

### Error Handling
- `BloombergConnectionError` — session fails. UI shows cached data with warning.
- Field error — log ticker and field, return None, do not fail entire pull.
- `BloombergTimeoutError` — default 30s. Cache unchanged.

### Timestamps
Per data type: `last_wirp_pull`, `last_futures_pull`, `last_omon_pull`. OMON timestamp also stores scenario name pulled for.

### Functions Exposed
- `initialise_session()`
- `pull_startup_data(fomc_dates, contracts)` → tuple[dict, dict]
- `pull_option_chain(underlying)` → list[str]
- `pull_strike_data(tickers, target_strike)` → dict[expiry, dict[strike, OptionQuote]]
- `get_cached_wirp()` → dict | None
- `get_cached_futures()` → dict | None
- `get_cached_omon()` → dict | None
- `is_omon_stale(current_scenario_id)` → bool
- `get_pull_timestamps()` → dict[str, datetime | None]
- `refresh_all(fomc_dates, contracts, target_strike, current_scenario_id)`

### Phase 2 Note
Extend ProbabilityDistribution to support three outcomes {-50: p, -25: q, 0: r} for meetings where 50bp moves are priced.

---

## 4.11 data/base.py

Abstract DataSource interface. All data sources implement this.

```python
from abc import ABC, abstractmethod
from datetime import date

class DataSource(ABC):

    @abstractmethod
    def pull_startup_data(self, fomc_dates: list[date], contracts: list[str]) -> tuple[dict, dict]:
        """Pull WIRP probabilities and live futures prices. Returns (wirp_data, futures_prices)."""

    @abstractmethod
    def pull_option_chain(self, underlying: str) -> list[str]:
        """Return list of option tickers for the given underlying futures contract."""

    @abstractmethod
    def pull_strike_data(self, tickers: list[str], target_strike: float) -> dict:
        """Pull OptionQuote data for tickers within ±5 strikes of target_strike."""

    @abstractmethod
    def get_cached_wirp(self) -> dict | None:
        """Return cached WIRP data or None if not yet pulled."""

    @abstractmethod
    def get_cached_futures(self) -> dict | None:
        """Return cached futures prices or None if not yet pulled."""

    @abstractmethod
    def get_cached_omon(self) -> dict | None:
        """Return cached OMON chain or None if not yet pulled."""

    @abstractmethod
    def is_omon_stale(self, current_scenario_id: str) -> bool:
        """Return True if cached OMON data was pulled for a different scenario."""

    @abstractmethod
    def get_pull_timestamps(self) -> dict:
        """Return dict of last pull timestamps keyed by data type."""
```

---

## 4.12 products/base.py

Abstract Product interface. All product files implement this.

```python
from abc import ABC, abstractmethod
from datetime import date

class Product(ABC):

    @abstractmethod
    def get_contract_code(self) -> str: ...

    @abstractmethod
    def get_expiry(self, year: int, month: int) -> date: ...

    @abstractmethod
    def get_serial_expiries(self, quarterly_code: str) -> list[date]: ...

    @abstractmethod
    def year_fraction(self, date_a: date, date_b: date) -> float: ...

    @abstractmethod
    def get_tick_size(self) -> float: ...

    @abstractmethod
    def get_tick_value(self) -> float: ...

    @abstractmethod
    def get_contract_size(self) -> float: ...

    @abstractmethod
    def get_day_count_convention(self) -> str: ...
```

---

## 4.13 products/sofr.py

**Implements:** products/base.py Product interface.

**Contract specs:**
- Root code: SR3
- Tick size: 0.005 (half a basis point, $12.50 per tick)
- Tick value: $12.50
- Contract size: $1,000,000
- Day count: Actual/360
- Strike spacing: 0.0625 (6.25bp)

**Expiry convention:** IMM dates — third Wednesday of contract month, CME holiday adjusted. Delegated to date_utils.get_imm_expiry().

**Serial month mapping (hardcoded):**
```python
SERIAL_MONTHS = {
    3:  [1, 2, 3],
    6:  [4, 5, 6],
    9:  [7, 8, 9],
    12: [10, 11, 12]
}
```

`get_serial_expiries(quarterly_code)` parses contract code, looks up three serial months, returns IMM expiry dates.

**Year fraction:**
```python
def year_fraction(self, date_a: date, date_b: date) -> float:
    raw_days = date_utils.business_days_between(date_a, date_b)
    return raw_days / 360
```

---

## 4.14 products/sonia.py
Stub. All methods raise NotImplementedError. Phase 2. Reference existing Excel pricer.

## 4.15 products/euribor.py
Stub. All methods raise NotImplementedError. Phase 2. Reference existing Excel pricer.

---

## 4.16 utils/cache.py

In-memory key-value cache. No TTL. Explicit invalidation only.

```python
from datetime import datetime

class Cache:

    def __init__(self):
        self._store: dict[str, any] = {}
        self._timestamps: dict[str, datetime] = {}

    def set(self, key: str, value: any) -> None:
        self._store[key] = value
        self._timestamps[key] = datetime.now()

    def get(self, key: str) -> any | None:
        return self._store.get(key, None)

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)
        self._timestamps.pop(key, None)

    def invalidate_all(self) -> None:
        self._store.clear()
        self._timestamps.clear()

    def get_timestamp(self, key: str) -> datetime | None:
        return self._timestamps.get(key, None)

    def has(self, key: str) -> bool:
        return key in self._store
```

---

## 4.17 config/products.yaml

```yaml
sofr:
  root_code: SR3
  tick_size: 0.005
  tick_value: 12.50
  contract_size: 1_000_000
  day_count: Actual/360
  strike_spacing: 0.0625
  kink_threshold: 0.002
  sofr_ffr_spread_bp: 5.0
  default_range_ticks:
    - 6
    - 12
    - 18

sonia:
  root_code: SER
  tick_size: 0.005
  tick_value: 12.50
  contract_size: 1_000_000
  day_count: Actual/365
  strike_spacing: 0.0625
  kink_threshold: null
  sofr_ffr_spread_bp: null

euribor:
  root_code: ERB
  tick_size: 0.005
  tick_value: 12.50
  contract_size: 1_000_000
  day_count: Actual/360
  strike_spacing: 0.0625
  kink_threshold: null
  sofr_ffr_spread_bp: null

bloomberg:
  daily_hit_budget: 150_000
  session_host: localhost
  session_port: 8194
  request_timeout_seconds: 30
  ff_contracts:
    - FF1
    - FF2
    - FF3
    - FF4
    - FF5
    - FF6
    - FF7
    - FF8
    - FF9
    - FF10
    - FF11
    - FF12
    - FF13
    - FF14
    - FF15
    - FF16
    - FF17
    - FF18
    - FF19
    - FF20
    - FF21
    - FF22
    - FF23
    - FF24
  sr3_contracts:
    - SR3H
    - SR3M
    - SR3U
    - SR3Z
    - SR3H_1
    - SR3M_1
    - SR3U_1
    - SR3Z_1

fomc:
  rolling_window_years: 2
```

---

## 4.18 config/scenarios.yaml

```yaml
templates:

  - name: Hold Through Year End
    type: rule_based
    rules:
      - meeting_window: [2026-03, 2026-12]
        outcome: 0bp

  - name: Two Cuts H2 2026
    type: explicit
    meetings:
      2026-03-19: 0bp
      2026-05-07: 0bp
      2026-06-18: 0bp
      2026-07-30: -25bp
      2026-09-17: -25bp
      2026-10-29: 0bp
      2026-12-10: 0bp

  - name: Aggressive Easing
    type: explicit
    meetings:
      2026-03-19: -25bp
      2026-05-07: -25bp
      2026-06-18: -25bp
      2026-07-30: -25bp
      2026-09-17: 0bp
      2026-10-29: 0bp
      2026-12-10: 0bp
```

Valid outcome values: `0bp`, `-25bp`, `-50bp`, `+25bp`. No "hold" string anywhere.

---

## 4.19 dashboard/scenario_panel.py

**Owns:** Scenario selection, custom scenario builder, val_date override, SOFR-FFR spread override, settlement target range inputs, curve chart, Pull Options button.

### Layout (top to bottom)
1. Curve chart
2. Scenario selector
3. Custom scenario builder (collapsed by default)
4. Parameter overrides (val_date, SOFR-FFR spread, underlying price)
5. Settlement target range inputs
6. Pull Options button

### Curve Chart
Three series:
- Orange line — WIRP base curve from rates_engine.get_wirp_curve()
- Blue line — scenario curve from rates_engine.get_scenario_curve()
- Grey dots — live futures prices from rates_engine.get_live_curve()

X-axis: contract expiry dates. Y-axis: futures price. Updates immediately on scenario change. No BBG call triggered. Multi-scenario mode: each scenario gets its own blue line.

### Scenario Selector
Dropdown with templates and saved custom scenarios in separate labelled groups. "New custom scenario" option at bottom expands builder.

### Custom Scenario Builder
Table with one row per FOMC meeting in 2-year window. Columns: meeting date (read only), WIRP-implied outcome (read only, grey), override dropdown (0bp | -25bp | -50bp | +25bp | blank). Below table: name input, Save button, Activate button.

### Parameter Overrides
Three independent inputs each with reset button:
- val_date — date picker. Calls scenario_engine.set_val_date(). No BBG call.
- SOFR-FFR spread — numeric input in bp. Stored on active scenario.
- Underlying price — numeric input. Passed to options_pricer.py as override.
When any override is active: small "overriding live data" indicator shown.

### Settlement Target Range
Shown only when scenario is active.

**Default state:** Read-only display of ±6, ±12, ±18 tick bands. Scenario midpoint price shown explicitly. "Set custom range" button.

**Explicit state:** Two numeric inputs (lower and upper bounds in ticks, independent). "Reset to defaults" button.

### Pull Options Button
Shown only when scenario active. Label: "Pull Options for [scenario name]". Disabled during pull. Timestamp shown after pull. Warning shown if scenario changed since last pull.

---

## 4.20 dashboard/trade_panel.py

**Owns:** Ranked candidate display. Range tabs. Structure detail with manual strike editing. Stale OMON warning.

### Layout (top to bottom)
1. Stale OMON warning (conditional)
2. Range tabs
3. Ranked list
4. Structure detail view (expands on row selection)

### Stale OMON Warning
Yellow banner when bloomberg.is_omon_stale() returns True. Persists until fresh pull completed for current scenario.

### Range Tabs
Three default tabs: ±6 ticks, ±12 ticks, ±18 ticks. Single tab when explicit range set. Active tab persists across scenario changes.

### Ranked List
Sortable table. Default sort: R:R descending, long-premium first.

Columns: Expiry (sortable), Structure, Type, R:R, Theta (▲/▼), Mkt Distance (ticks), Min P&L, Max P&L, Mean P&L.

### Structure Detail View
Expands inline on row selection. Five sections:

1. **Leg breakdown table** — expiry, put/call, strike (editable), quantity, last price, delta. Strike edit triggers immediate reprice and in-place update of ranked list row.
2. **Structure summary** — net premium, structure delta, ATM vol
3. **P&L chart** — P&L at expiry across settlement range. Vertical dashed line at scenario midpoint. Shaded active range.
4. **Per-leg Greeks table** — delta, gamma, vega, theta per leg plus structure totals
5. **Kink flags** — per-leg SELL_TARGET | BUY_TARGET | NEUTRAL. Shown only when skew_analysis available.

---

## 4.21 dashboard/greeks_panel.py

**Owns:** Greeks display and scenario P&L chart for selected candidate. Time decay.

### Layout
Two columns side by side:
- Left: Greeks summary card + time decay table
- Right: Scenario P&L chart

Placeholder text when no candidate selected.

### Greeks Summary Card
Net delta, gamma, vega, theta. Directional colour indicator. Updates on candidate selection or manual strike change.

### Time Decay Table
One row per business day from val_date to expiry. Columns: date, days to expiry, daily theta (ticks), cumulative theta. Computed by calling options_pricer.py at each date step, vol and underlying held constant.

### Scenario P&L Chart
Three series across settlement price grid:
- P&L at expiry
- P&L at val_date (live vol)
- P&L at midpoint date (vol interpolated linearly)

Vertical dashed line at scenario midpoint. Shaded active range. Updates on candidate change, manual strike edit, or val_date override change.

---

## 4.22 dashboard/app.py

**Owns:** Streamlit entry point. Session state initialisation. Layout. BBG session lifecycle. Refresh logic.

### Entry Point
```python
st.set_page_config(
    page_title="Rates Desk Dashboard",
    layout="wide",
    initial_sidebar_state="collapsed"
)
```

### Session State Defaults
```python
defaults = {
    "data_source":         None,
    "cache":               Cache(),
    "active_scenario":     None,
    "val_date_override":   None,
    "explicit_ranges":     {},
    "selected_candidate":  None,
    "multi_scenario_mode": False,
    "omon_stale":          False,
    "last_wirp_pull":      None,
    "last_futures_pull":   None,
    "last_omon_pull":      None,
    "omon_pulled_for":     None,
}
```

### Startup Sequence
BBG session via st.cache_resource. pull_startup_data() called immediately on first load. BloombergConnectionError surfaces as full-width error banner.

### Layout
Three columns, ratio 30 / 40 / 30. Each panel rendered as st.fragment.

```
| scenario_panel | trade_panel | greeks_panel |
```

### Refresh Logic
**"Refresh Data" button** (fixed header bar):
- Re-pulls WIRP and futures unconditionally
- Re-pulls OMON only if scenario active and OMON previously pulled
- Does not reset overrides or scenario selection
- Disabled during pull

**Stale detection:** After scenario change, app sets omon_stale = True if bloomberg.is_omon_stale() returns True.

### Timestamp Display (fixed header bar)
```
WIRP: 09:32:14    Futures: 09:32:14    Options: 10:15:43 (Two Cuts H2 2026)
```
Options timestamp shown in amber when stale.

---

# 5. Build Sequence

- **Sprint 1** — Data layer: `utils/cache.py`, `data/base.py`, `bloomberg.py`. Validate WIRP and futures pulls against manual BBG checks.
- **Sprint 2** — `utils/date_utils.py`, `products/base.py`, `products/sofr.py`, `rates_engine.py`. Validate WIRP base curve against SOFRWatch visually.
- **Sprint 3** — `scenario_engine.py`. Template and custom scenarios. Validate scenario curves against manual calculations.
- **Sprint 4** — `options_pricer.py`. Black-76, Greeks, scenario PnL. Validate against PricingMonkey manually.
- **Sprint 5** — `skew_logic.py`, `trade_structures.py`, `preferences.py`.
- **Sprint 6** — `trade_builder.py`, `ranker.py`.
- **Sprint 7** — Streamlit dashboard. Wire all modules into UI. Live refresh with st.fragment.

---

# 6. Notes for Resuming

- Paste this document into the first message of the new session
- All planning decisions are finalised. Proceed directly to coding.
- Sprint 1 coding order: `utils/cache.py` → `data/base.py` → `bloomberg.py`
- After Sprint 1, validate WIRP pull output against manual BBG checks before proceeding to Sprint 2

*End of Document*
