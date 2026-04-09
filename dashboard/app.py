"""dashboard/app.py — Streamlit entry point, session lifecycle, and layout.

Run with:
    uv run streamlit run dashboard/app.py
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from typing import Optional

import yaml
import streamlit as st

# Add repo root to path so modules resolve without install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bloomberg import BloombergConnectionError, BloombergDataSource
from preferences import (
    KinkMode, OpenRisk, TraderPreferences, VolView, default_preferences,
)
from rates_engine import RatesEngine
from scenario_engine import ValDateManager
from utils import date_utils

from dashboard.scenario_panel import render_scenario_panel
from dashboard.trade_panel import render_trade_panel
from dashboard.greeks_panel import render_greeks_panel

# ---------------------------------------------------------------------------
# Mock mode — set USE_MOCK=1 or if blpapi is not installed
# ---------------------------------------------------------------------------

def _use_mock() -> bool:
    if os.environ.get("USE_MOCK", "0") == "1":
        return True
    try:
        import blpapi  # noqa: F401
        return False
    except ImportError:
        return True

# ---------------------------------------------------------------------------
# Page config — must be the first Streamlit call
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Rates Desk Dashboard",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# Cached resources (survive re-runs, reset only on server restart)
# ---------------------------------------------------------------------------

@st.cache_resource
def _load_config() -> dict:
    cfg_path = os.path.join(os.path.dirname(__file__), "..", "config", "products.yaml")
    with open(cfg_path, "r") as f:
        return yaml.safe_load(f)


@st.cache_resource
def _init_data_source(ff_contracts: tuple, sr3_contracts: tuple):
    """Create and connect the data source once per server process.

    Returns (DataSource, error_message_or_None).
    Uses MockDataSource automatically when blpapi is not installed or USE_MOCK=1.
    """
    if _use_mock():
        from mock_data_source import MockDataSource
        return MockDataSource(), None

    ds = BloombergDataSource(
        ff_contracts=list(ff_contracts),
        sr3_contracts=list(sr3_contracts),
    )
    try:
        ds.initialise_session()
        return ds, None
    except BloombergConnectionError as exc:
        return ds, str(exc)
    except Exception as exc:
        return ds, f"Unexpected error starting Bloomberg session: {exc}"


# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------

def _init_session_state(config: dict) -> None:
    """Populate session state with defaults on first load. Never overwrites."""
    defaults: dict = {
        # Data
        "data_source":             None,
        "wirp_data":               {},
        "futures_prices":          {},
        "omon_chain":              None,
        "rates_engine":            None,
        # Scenario
        "active_scenario":         None,
        "explicit_ranges":         {},
        # Candidate selection
        "selected_candidate":      None,
        # Overrides
        "sofr_ffr_spread_override":None,
        "underlying_price_override":None,
        "current_sofr_fixing":     config["sofr"].get("default_sofr_fixing", 5.33),
        # Preferences
        "preferences":             default_preferences(),
        # Timestamp / stale flags
        "omon_stale":              False,
        "last_wirp_pull":          None,
        "last_futures_pull":       None,
        "last_omon_pull":          None,
        "omon_pulled_for":         None,
        # Internal UI state
        "val_date_manager":        ValDateManager(),
        "_show_custom_builder":    False,
        "_strike_overrides":       {},
        "_startup_pulled":         False,
        # Computed cache
        "_ranked_lists":           [],
        "_ranked_cache_key":       None,
        "_active_ranges":          [],
        "_scenario_prices_by_expiry": {},
        # Contracts list (from config)
        "sr3_contracts":           config["bloomberg"]["sr3_contracts"],
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val


# ---------------------------------------------------------------------------
# Startup and refresh pulls
# ---------------------------------------------------------------------------

def _fomc_window() -> list[date]:
    """2-year FOMC meeting window starting today."""
    today = date.today()
    end = date(min(today.year + 2, 2027), 12, 31)
    return date_utils.get_fomc_dates(today, end)


def _do_startup_pull(ds: BloombergDataSource, config: dict) -> Optional[str]:
    """Pull WIRP + futures on first load. Returns error string or None."""
    try:
        fomc_dates = _fomc_window()
        contracts = config["bloomberg"]["sr3_contracts"]
        wirp_data, futures_prices = ds.pull_startup_data(fomc_dates, contracts)
        st.session_state.wirp_data = wirp_data
        st.session_state.futures_prices = futures_prices
        _sync_timestamps(ds)
        return None
    except Exception as exc:
        return str(exc)


def _do_refresh(ds: BloombergDataSource, config: dict) -> None:
    """Re-pull WIRP and futures. Mark OMON stale (re-pull triggered via Pull Options).

    NOTE: Per-plan, Refresh re-pulls OMON if it was previously pulled.
    This implementation marks it stale and leaves re-pull to the Pull Options
    button — a safe default that avoids redundant BBG calls on every Refresh.
    Automatic OMON re-pull on Refresh is a Phase 2 upgrade.
    """
    fomc_dates = _fomc_window()
    contracts = config["bloomberg"]["sr3_contracts"]
    wirp_data, futures_prices = ds.pull_startup_data(fomc_dates, contracts)
    st.session_state.wirp_data = wirp_data
    st.session_state.futures_prices = futures_prices
    _sync_timestamps(ds)

    # Invalidate trade candidate cache so it recomputes with fresh curves
    st.session_state._ranked_cache_key = None
    st.session_state._ranked_lists = []

    # Mark OMON stale if it was previously pulled
    if st.session_state.get("omon_pulled_for"):
        st.session_state.omon_stale = True


def _sync_timestamps(ds: BloombergDataSource) -> None:
    """Copy pull timestamps from the data source object into session state."""
    ts = ds.get_pull_timestamps()
    st.session_state.last_wirp_pull   = ts.get("last_wirp_pull")
    st.session_state.last_futures_pull = ts.get("last_futures_pull")
    st.session_state.last_omon_pull   = ts.get("last_omon_pull")
    st.session_state.omon_pulled_for  = ts.get("omon_pulled_for")


# ---------------------------------------------------------------------------
# RatesEngine — rebuilt on every render (cheap, no BBG calls)
# ---------------------------------------------------------------------------

def _build_rates_engine(config: dict) -> Optional[RatesEngine]:
    """Construct RatesEngine from current session state."""
    wirp_data = st.session_state.wirp_data
    futures_prices = st.session_state.futures_prices
    if not wirp_data and not futures_prices:
        return None

    spread = st.session_state.get("sofr_ffr_spread_override")
    if spread is None:
        active = st.session_state.get("active_scenario")
        spread = (
            active.sofr_ffr_spread_bp if active is not None
            else config["sofr"]["sofr_ffr_spread_bp"]
        )

    return RatesEngine(
        wirp_data=wirp_data,
        live_futures_prices=futures_prices,
        current_sofr_fixing=st.session_state.current_sofr_fixing,
        sofr_ffr_spread_bp=float(spread),
        contracts=st.session_state.sr3_contracts,
        val_date=st.session_state.val_date_manager.get_val_date(),
    )


# ---------------------------------------------------------------------------
# Sidebar — trader preferences
# ---------------------------------------------------------------------------

def _render_sidebar() -> None:
    with st.sidebar:
        st.title("Preferences")

        open_risk_val = st.selectbox(
            "Open Risk",
            options=[e.value for e in OpenRisk],
            index=[e.value for e in OpenRisk].index(
                st.session_state.preferences.open_risk.value
            ),
            key="_pref_open_risk",
        )
        vol_view_val = st.selectbox(
            "Vol View",
            options=[e.value for e in VolView],
            index=[e.value for e in VolView].index(
                st.session_state.preferences.vol_view.value
            ),
            key="_pref_vol_view",
        )
        theta_prop = st.slider(
            "Theta Propensity",
            min_value=0.0, max_value=1.0,
            value=st.session_state.preferences.theta_propensity,
            step=0.05,
            key="_pref_theta_prop",
        )
        kink_mode_val = st.selectbox(
            "Kink Mode",
            options=[e.value for e in KinkMode],
            index=[e.value for e in KinkMode].index(
                st.session_state.preferences.kink_mode.value
            ),
            key="_pref_kink_mode",
        )

        new_prefs = TraderPreferences(
            open_risk=OpenRisk(open_risk_val),
            vol_view=VolView(vol_view_val),
            theta_propensity=theta_prop,
            kink_mode=KinkMode(kink_mode_val),
        )
        # Invalidate trade cache if preferences changed
        old = st.session_state.preferences
        if (
            new_prefs.open_risk != old.open_risk
            or new_prefs.vol_view != old.vol_view
            or new_prefs.kink_mode != old.kink_mode
            or abs(new_prefs.theta_propensity - old.theta_propensity) > 1e-6
        ):
            st.session_state.preferences = new_prefs
            st.session_state._ranked_cache_key = None
            st.session_state._ranked_lists = []


# ---------------------------------------------------------------------------
# Header bar — timestamps, SOFR fixing, Refresh button
# ---------------------------------------------------------------------------

def _render_header(ds: Optional[BloombergDataSource], config: dict) -> None:
    title_col, ts_col, ctrl_col = st.columns([2, 5, 2])

    with title_col:
        st.markdown("#### Rates Desk Dashboard")

    with ts_col:
        last_wirp  = st.session_state.get("last_wirp_pull")
        last_fut   = st.session_state.get("last_futures_pull")
        last_opt   = st.session_state.get("last_omon_pull")
        pulled_for = st.session_state.get("omon_pulled_for") or ""
        stale      = st.session_state.get("omon_stale", False)

        wirp_str = last_wirp.strftime("%H:%M:%S") if last_wirp else "—"
        fut_str  = last_fut.strftime("%H:%M:%S")  if last_fut  else "—"

        if last_opt:
            opt_ts  = last_opt.strftime("%H:%M:%S")
            opt_str = f"Options: {opt_ts} ({pulled_for})"
            opt_md  = f":orange[{opt_str}]" if stale else opt_str
        else:
            opt_md = "Options: —"

        st.caption(f"WIRP: {wirp_str}  ·  Futures: {fut_str}  ·  {opt_md}")

    with ctrl_col:
        c_fix, c_btn = st.columns([1, 1])
        with c_fix:
            new_fixing = st.number_input(
                "SOFR fixing (%)",
                value=float(st.session_state.current_sofr_fixing),
                step=0.01, format="%.2f",
                label_visibility="collapsed",
                key="_sofr_fixing_input",
            )
            st.caption(f"SOFR {new_fixing:.2f}%")
            if abs(new_fixing - st.session_state.current_sofr_fixing) > 1e-4:
                st.session_state.current_sofr_fixing = new_fixing
                st.session_state._ranked_cache_key = None

        with c_btn:
            if st.button("Refresh Data", type="primary", key="_btn_refresh"):
                if ds is not None:
                    with st.spinner("Refreshing…"):
                        _do_refresh(ds, config)
                    st.rerun()
                else:
                    st.error("No Bloomberg connection.")

    st.divider()


# ---------------------------------------------------------------------------
# Main script
# ---------------------------------------------------------------------------

config = _load_config()
_init_session_state(config)

# BBG data source (cached — created once per server process)
ff_contracts  = tuple(config["bloomberg"]["ff_contracts"])
sr3_contracts = tuple(config["bloomberg"]["sr3_contracts"])
ds, bbg_error = _init_data_source(ff_contracts, sr3_contracts)
st.session_state.data_source = ds

# Mock mode banner
if _use_mock():
    st.info("Running in mock mode — synthetic market data. Bloomberg not required.", icon="ℹ️")

# Surface connection error (non-fatal — cached data may still be available)
if bbg_error:
    st.error(f"Bloomberg connection failed: {bbg_error}", icon="🔴")

# Initial startup pull — once per session
if not st.session_state._startup_pulled:
    if not bbg_error:
        with st.spinner("Loading market data…"):
            pull_error = _do_startup_pull(ds, config)
        if pull_error:
            st.warning(f"Startup data pull failed: {pull_error}")
    st.session_state._startup_pulled = True

# Rebuild RatesEngine every render (cheap)
st.session_state.rates_engine = _build_rates_engine(config)

# Sidebar — preferences
_render_sidebar()

# Header bar
_render_header(ds, config)

# Three-column layout — 30 / 40 / 30
col1, col2, col3 = st.columns([30, 40, 30])

with col1:
    render_scenario_panel()

with col2:
    render_trade_panel()

with col3:
    render_greeks_panel()
