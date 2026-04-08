from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import yaml

from utils import date_utils

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SAVED_SCENARIOS_DIR = os.path.join(os.path.dirname(__file__), "saved_scenarios")
_TEMPLATES_PATH = os.path.join(os.path.dirname(__file__), "config", "scenarios.yaml")
_DEFAULT_SOFR_FFR_SPREAD_BP = 5.0

# Valid bp outcome values
_VALID_OUTCOMES_BP = {0, -25, -50, +25}

# Default settlement target range widths in ticks
_DEFAULT_RANGE_TICKS = [6, 12, 18]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Scenario:
    name: str
    meetings: dict[date, int]       # {meeting_date: bp_change} — explicit only
    sofr_ffr_spread_bp: float = _DEFAULT_SOFR_FFR_SPREAD_BP
    is_custom: bool = False


@dataclass
class SettlementTargetRange:
    contract: str
    lower_bp: float                 # ticks below scenario midpoint price
    upper_bp: float                 # ticks above scenario midpoint price
    is_default: bool = True


# ---------------------------------------------------------------------------
# YAML parsing helpers
# ---------------------------------------------------------------------------

def _parse_bp(value: str) -> int:
    """Parse a bp string such as '0bp', '-25bp', '+25bp' to an integer."""
    s = str(value).strip().replace("bp", "")
    return int(s)


def _parse_meeting_window(window: list, wirp_data: dict) -> list[date]:
    """Expand a [YYYY-MM, YYYY-MM] window to the list of FOMC meeting dates within it."""
    start_str, end_str = window[0], window[1]
    start_year, start_month = int(start_str[:4]), int(start_str[5:])
    end_year, end_month = int(end_str[:4]), int(end_str[5:])
    from_date = date(start_year, start_month, 1)
    # Last day of end month
    if end_month == 12:
        to_date = date(end_year + 1, 1, 1)
    else:
        to_date = date(end_year, end_month + 1, 1)
    from datetime import timedelta
    to_date = to_date - timedelta(days=1)
    return date_utils.get_fomc_dates(from_date, to_date)


def _expand_rule_based(rules: list, wirp_data: dict) -> dict[date, int]:
    """Expand rule-based scenario shorthand to explicit {meeting_date: bp_change}."""
    meetings: dict[date, int] = {}
    for rule in rules:
        window = rule["meeting_window"]
        outcome_bp = _parse_bp(rule["outcome"])
        for meeting in _parse_meeting_window(window, wirp_data):
            meetings[meeting] = outcome_bp
    return meetings


def _load_yaml_scenario(entry: dict, wirp_data: dict) -> Scenario:
    """Parse one YAML template entry into a Scenario dataclass."""
    name = entry["name"]
    scenario_type = entry.get("type", "explicit")

    if scenario_type == "rule_based":
        meetings = _expand_rule_based(entry.get("rules", []), wirp_data)
    else:
        raw = entry.get("meetings", {})
        meetings = {
            date.fromisoformat(str(k)): _parse_bp(v)
            for k, v in raw.items()
        }

    spread = entry.get("sofr_ffr_spread_bp", _DEFAULT_SOFR_FFR_SPREAD_BP)
    return Scenario(name=name, meetings=meetings, sofr_ffr_spread_bp=float(spread), is_custom=False)


# ---------------------------------------------------------------------------
# Scenario management functions
# ---------------------------------------------------------------------------

def load_templates(wirp_data: Optional[dict] = None) -> list[Scenario]:
    """Load template scenarios from config/scenarios.yaml.

    Rule-based templates are expanded to explicit meeting lists using wirp_data
    to resolve meeting windows. If wirp_data is None, rule-based templates fall
    back to date_utils.get_fomc_dates for window resolution.
    """
    wirp_data = wirp_data or {}
    with open(_TEMPLATES_PATH, "r") as f:
        raw = yaml.safe_load(f)
    return [_load_yaml_scenario(entry, wirp_data) for entry in raw.get("templates", [])]


def build_custom_scenario(
    name: str,
    meetings: dict[date, int],
    sofr_ffr_spread_bp: float = _DEFAULT_SOFR_FFR_SPREAD_BP,
) -> Scenario:
    """Construct a custom scenario. meetings must be an explicit {date: bp_change} dict."""
    return Scenario(name=name, meetings=meetings, sofr_ffr_spread_bp=sofr_ffr_spread_bp, is_custom=True)


def save_scenario(scenario: Scenario) -> None:
    """Persist a custom scenario to saved_scenarios/ as YAML in explicit form.
    Never serialised as rule-based shorthand."""
    os.makedirs(_SAVED_SCENARIOS_DIR, exist_ok=True)
    filename = _safe_filename(scenario.name) + ".yaml"
    path = os.path.join(_SAVED_SCENARIOS_DIR, filename)
    data = {
        "name": scenario.name,
        "type": "explicit",
        "sofr_ffr_spread_bp": scenario.sofr_ffr_spread_bp,
        "meetings": {
            k.isoformat(): f"{v:+d}bp" for k, v in sorted(scenario.meetings.items())
        },
    }
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


def load_saved_scenarios() -> list[Scenario]:
    """Load all user-saved scenarios from saved_scenarios/."""
    if not os.path.isdir(_SAVED_SCENARIOS_DIR):
        return []
    scenarios = []
    for fname in sorted(os.listdir(_SAVED_SCENARIOS_DIR)):
        if not fname.endswith(".yaml"):
            continue
        path = os.path.join(_SAVED_SCENARIOS_DIR, fname)
        with open(path, "r") as f:
            entry = yaml.safe_load(f)
        scenario = _load_yaml_scenario(entry, wirp_data={})
        scenario.is_custom = True
        scenarios.append(scenario)
    return scenarios


def delete_scenario(name: str) -> None:
    """Delete a saved custom scenario by name."""
    filename = _safe_filename(name) + ".yaml"
    path = os.path.join(_SAVED_SCENARIOS_DIR, filename)
    if os.path.exists(path):
        os.remove(path)


def _safe_filename(name: str) -> str:
    """Convert a scenario name to a safe filename."""
    return "".join(c if c.isalnum() or c in " _-" else "_" for c in name).strip().replace(" ", "_")


# ---------------------------------------------------------------------------
# Rate path assembly
# ---------------------------------------------------------------------------

def assemble_rate_path(scenario: Scenario, wirp_data: dict) -> dict[date, int]:
    """Assemble the full rate path for a scenario.

    Returns {meeting_date: bp_change} where:
    - Meetings explicitly defined in the scenario use the scenario's bp_change.
    - All other FOMC meetings in wirp_data inherit the WIRP-implied expected change
      (rounded to nearest 25bp for the scenario path — passed as raw bp_change int).

    Note: rates_engine.py accepts float bp_change and handles the WIRP inheritance
    internally. This function returns the scenario's explicit overrides only, which
    rates_engine uses as the scenario_path argument.
    """
    return dict(scenario.meetings)


# ---------------------------------------------------------------------------
# val_date management
# ---------------------------------------------------------------------------

class ValDateManager:
    """Holds the val_date override for the session. Passed as argument to rates_engine."""

    def __init__(self):
        self._override: Optional[date] = None

    def set_val_date(self, d: date) -> None:
        self._override = d

    def get_val_date(self) -> date:
        return self._override if self._override is not None else date.today()

    def reset_val_date(self) -> None:
        self._override = None

    @property
    def is_overridden(self) -> bool:
        return self._override is not None


# ---------------------------------------------------------------------------
# Settlement target range management
# ---------------------------------------------------------------------------

def get_default_ranges(contract: str, scenario_midpoint_price: float) -> list[SettlementTargetRange]:
    """Generate the three default settlement target ranges (±6, ±12, ±18 ticks).

    Args:
        contract:               SR3 contract code.
        scenario_midpoint_price: Theoretical price at the scenario's expected settlement.
    """
    tick = 0.005
    return [
        SettlementTargetRange(
            contract=contract,
            lower_bp=width * tick,
            upper_bp=width * tick,
            is_default=True,
        )
        for width in _DEFAULT_RANGE_TICKS
    ]


def set_explicit_range(contract: str, lower_bp: float, upper_bp: float) -> SettlementTargetRange:
    """Create an explicit (trader-defined) settlement target range."""
    return SettlementTargetRange(
        contract=contract,
        lower_bp=lower_bp,
        upper_bp=upper_bp,
        is_default=False,
    )


def clear_explicit_range(explicit_ranges: dict, contract: str) -> None:
    """Remove an explicit range override for a contract, reverting to defaults."""
    explicit_ranges.pop(contract, None)


def get_active_ranges(
    contract: str,
    scenario_midpoint_price: float,
    explicit_ranges: dict,
) -> list[SettlementTargetRange]:
    """Return the active settlement target ranges for a contract.

    If an explicit range has been set, returns that single range.
    Otherwise returns the three default ranges.
    """
    if contract in explicit_ranges:
        return [explicit_ranges[contract]]
    return get_default_ranges(contract, scenario_midpoint_price)
