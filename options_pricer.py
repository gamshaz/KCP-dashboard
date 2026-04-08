from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Black-76 core functions
# ---------------------------------------------------------------------------
# Black-76 is the standard model for European options on futures (r=0).
#
# NOTE: Bloomberg's IVOL_MID for SR3 options is typically quoted as a normal
# (Bachelier) vol in bp, not as a lognormal vol. Validate against PricingMonkey
# before using in production. If normal vol is confirmed, replace these
# functions with Bachelier equivalents.
# ---------------------------------------------------------------------------

def _ncdf(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _npdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1_d2(F: float, K: float, T: float, sigma: float) -> tuple[float, float]:
    if T <= 0.0 or sigma <= 0.0:
        inf = math.inf if F >= K else -math.inf
        return inf, inf
    sqrt_T = math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * sigma ** 2 * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    return d1, d2


def black76_price(put_call: str, F: float, K: float, T: float, sigma: float) -> float:
    """Black-76 theoretical price for a European option on a futures contract.
    Returns price in the same units as F and K (futures price points)."""
    if T <= 0.0:
        return max(F - K, 0.0) if put_call == "call" else max(K - F, 0.0)
    d1, d2 = _d1_d2(F, K, T, sigma)
    if put_call == "call":
        return F * _ncdf(d1) - K * _ncdf(d2)
    else:
        return K * _ncdf(-d2) - F * _ncdf(-d1)


def black76_delta(put_call: str, F: float, K: float, T: float, sigma: float) -> float:
    """dV/dF. Call delta in (0,1), put delta in (-1,0)."""
    if T <= 0.0:
        if put_call == "call":
            return 1.0 if F > K else 0.0
        else:
            return -1.0 if F < K else 0.0
    d1, _ = _d1_d2(F, K, T, sigma)
    return _ncdf(d1) if put_call == "call" else _ncdf(d1) - 1.0


def black76_gamma(F: float, K: float, T: float, sigma: float) -> float:
    """d²V/dF². Same for calls and puts."""
    if T <= 0.0 or sigma <= 0.0:
        return 0.0
    d1, _ = _d1_d2(F, K, T, sigma)
    return _npdf(d1) / (F * sigma * math.sqrt(T))


def black76_vega(F: float, K: float, T: float, sigma: float) -> float:
    """dV/dσ. Price change per unit change in vol (e.g. per 1.0, not per 0.01)."""
    if T <= 0.0:
        return 0.0
    d1, _ = _d1_d2(F, K, T, sigma)
    return F * _npdf(d1) * math.sqrt(T)


def black76_theta_per_day(
    put_call: str, F: float, K: float, T: float, sigma: float
) -> float:
    """Daily theta in price-point terms (calendar day convention, /365).

    Sign convention follows trading usage:
      negative = long premium (paying theta, losing value each day)
      positive = short premium (receiving theta, gaining value each day)

    i.e.  theta_per_day = -(dV/dT) / 365
    """
    if T <= 0.0:
        return 0.0
    d1, d2 = _d1_d2(F, K, T, sigma)
    dv_dt = -(F * sigma * _npdf(d1)) / (2.0 * math.sqrt(T))
    # dV/dT is positive (more time = more value), so negate for daily P&L sign
    return -dv_dt / 365.0


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class OptionLeg:
    """Inputs for a single option leg."""
    put_call: str           # "call" or "put"
    strike: float           # futures price (e.g. 95.5000)
    quantity: int           # +N = long, -N = short
    expiry_years: float     # time to expiry in years (caller computes via sofr.year_fraction)
    implied_vol: float      # annualised lognormal vol
    last_price: float       # OMON market price for rich/cheap calculation


@dataclass
class LegResult:
    """Pricing output for a single option leg."""
    theoretical_value: float    # Black-76 price, in futures price points
    delta: float                # per-leg delta (quantity-weighted)
    gamma: float                # per-leg gamma (quantity-weighted)
    vega: float                 # per-leg vega (quantity-weighted)
    theta_per_day: float        # per-leg daily theta (quantity-weighted, trading sign)
    rich_cheap: float           # theoretical - market, in price points (+ve = cheap)


@dataclass
class StructureResult:
    """Aggregated pricing output for a multi-leg structure."""
    legs: list[LegResult]
    net_theoretical_value: float    # sum of leg theoretical values (quantity-weighted)
    net_premium: float              # sum of last_prices * quantity (debit = positive)
    is_long_premium: bool           # True if net_premium > 0 (paid premium)
    delta: float                    # net structure delta
    gamma: float                    # net structure gamma
    vega: float                     # net structure vega
    theta_per_day: float            # net structure daily theta (trading sign)


# ---------------------------------------------------------------------------
# Pricer functions
# ---------------------------------------------------------------------------

def price_leg(
    leg: OptionLeg,
    forward: float,
    vol_override: Optional[float] = None,
    underlying_price_override: Optional[float] = None,
) -> LegResult:
    """Price a single option leg using Black-76.

    Args:
        leg:                      OptionLeg inputs.
        forward:                  Theoretical futures price from rates_engine.
        vol_override:             If set, supersedes leg.implied_vol entirely.
        underlying_price_override: If set, supersedes forward as the underlying price.

    Manual overrides are independent — any combination is valid.
    """
    F = underlying_price_override if underlying_price_override is not None else forward
    sigma = vol_override if vol_override is not None else leg.implied_vol
    T = leg.expiry_years
    q = leg.quantity

    raw_value = black76_price(leg.put_call, F, leg.strike, T, sigma)
    raw_delta = black76_delta(leg.put_call, F, leg.strike, T, sigma)
    raw_gamma = black76_gamma(F, leg.strike, T, sigma)
    raw_vega = black76_vega(F, leg.strike, T, sigma)
    raw_theta = black76_theta_per_day(leg.put_call, F, leg.strike, T, sigma)

    rich_cheap = raw_value - leg.last_price  # +ve = model says cheap vs market

    return LegResult(
        theoretical_value=raw_value * q,
        delta=raw_delta * q,
        gamma=raw_gamma * q,
        vega=raw_vega * q,
        theta_per_day=raw_theta * q,
        rich_cheap=rich_cheap,
    )


def price_structure(
    legs: list[OptionLeg],
    forward: float,
    vol_override: Optional[float] = None,
    underlying_price_override: Optional[float] = None,
) -> StructureResult:
    """Price a multi-leg structure. All legs must share the same underlying and expiry.

    Returns aggregated Greeks and net premium for the full structure.
    """
    leg_results = [
        price_leg(leg, forward, vol_override, underlying_price_override)
        for leg in legs
    ]

    net_theoretical = sum(r.theoretical_value for r in leg_results)
    net_premium = sum(leg.last_price * leg.quantity for leg in legs)
    net_delta = sum(r.delta for r in leg_results)
    net_gamma = sum(r.gamma for r in leg_results)
    net_vega = sum(r.vega for r in leg_results)
    net_theta = sum(r.theta_per_day for r in leg_results)

    return StructureResult(
        legs=leg_results,
        net_theoretical_value=net_theoretical,
        net_premium=net_premium,
        is_long_premium=net_premium > 0.0,
        delta=net_delta,
        gamma=net_gamma,
        vega=net_vega,
        theta_per_day=net_theta,
    )


def expiry_pnl(legs: list[OptionLeg], terminal_forward: float) -> float:
    """Compute net P&L at expiry for a structure given a terminal futures price.

    P&L = sum of intrinsic payoffs (quantity-weighted) minus net_premium.
    Used for the P&L chart in greeks_panel and trade_builder evaluation.
    """
    payoff = 0.0
    net_premium = 0.0
    for leg in legs:
        intrinsic = max(terminal_forward - leg.strike, 0.0) if leg.put_call == "call" \
                    else max(leg.strike - terminal_forward, 0.0)
        payoff += intrinsic * leg.quantity
        net_premium += leg.last_price * leg.quantity
    return payoff - net_premium
