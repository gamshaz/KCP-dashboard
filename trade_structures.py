from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class StructureType(Enum):
    FLY_6               = "fly_6"           # symmetric fly, 6-wide wings
    FLY_12              = "fly_12"          # symmetric fly, 12-wide wings
    BROKEN_FLY_6        = "broken_fly_6"    # asymmetric fly, 6-wide
    BROKEN_FLY_12       = "broken_fly_12"   # asymmetric fly, 12-wide
    CONDOR              = "condor"          # regular condor
    BROKEN_CONDOR       = "broken_condor"   # asymmetric condor
    CALL_SPREAD         = "call_spread"
    PUT_SPREAD          = "put_spread"
    LADDER              = "ladder"
    CALENDAR            = "calendar"        # stub — Phase 2 only


# ---------------------------------------------------------------------------
# Leg definition
# ---------------------------------------------------------------------------

@dataclass
class LegDef:
    """Definition of one leg within a structure.

    strike_offset: distance from the body/centre strike in ticks (positive = higher strike).
    quantity:      signed number of contracts (+ve = long, -ve = short).
    put_call:      "call" or "put".
    """
    strike_offset: int      # ticks from reference strike (1 tick = 0.005 = 0.5bp)
    quantity: int           # +ve = long, -ve = short
    put_call: str           # "call" or "put"


# ---------------------------------------------------------------------------
# Base structure
# ---------------------------------------------------------------------------

@dataclass
class Structure:
    """Base class for all trade structures.

    centre_strike: the reference futures price around which offsets are applied.
    legs:          list of LegDef defining the full structure.
    structure_type: StructureType enum.
    width:         primary wing width in ticks (informational).
    """
    structure_type: StructureType
    centre_strike: float        # futures price of the body/centre
    legs: list[LegDef]
    width: int                  # primary wing width in ticks

    TICK: float = 0.005         # SR3 tick size

    def strike_for(self, leg: LegDef) -> float:
        """Absolute strike for a leg given its offset from centre."""
        return round(self.centre_strike + leg.strike_offset * self.TICK, 5)

    def compute_payoff(self, terminal_forward: float) -> float:
        """Intrinsic payoff of the structure at expiry given a terminal futures price.

        Returns the gross payoff (before premium). Positive = in-the-money.
        Use options_pricer.expiry_pnl for net P&L (payoff minus premium).
        """
        payoff = 0.0
        for leg in self.legs:
            K = self.strike_for(leg)
            if leg.put_call == "call":
                intrinsic = max(terminal_forward - K, 0.0)
            else:
                intrinsic = max(K - terminal_forward, 0.0)
            payoff += intrinsic * leg.quantity
        return payoff


# ---------------------------------------------------------------------------
# Structure factories
# ---------------------------------------------------------------------------
# Each factory returns a Structure with the correct legs for the given type.
# All strike offsets are in ticks. Width conventions:
#   fly_6:   body at 0, wings at ±6 ticks
#   fly_12:  body at 0, wings at ±12 ticks
#   condor:  body at ±3 ticks, wings at ±9 ticks (6-wide condor)
# ---------------------------------------------------------------------------

def make_fly(
    centre_strike: float,
    width: int,
    put_call: str,
    broken_upper: Optional[int] = None,
) -> Structure:
    """Symmetric or broken fly centred at centre_strike.

    Args:
        centre_strike: futures price of the body.
        width:         wing distance in ticks (6 or 12).
        put_call:      "call" or "put".
        broken_upper:  if set, upper wing is at +broken_upper ticks instead of +width
                       (makes the fly asymmetric / broken).
    """
    upper = broken_upper if broken_upper is not None else width
    is_broken = broken_upper is not None and broken_upper != width

    structure_type = (
        StructureType.BROKEN_FLY_6 if is_broken and width == 6 else
        StructureType.BROKEN_FLY_12 if is_broken and width == 12 else
        StructureType.FLY_6 if width == 6 else
        StructureType.FLY_12
    )

    legs = [
        LegDef(strike_offset=-width, quantity=-1, put_call=put_call),   # lower wing
        LegDef(strike_offset=0,      quantity=+2, put_call=put_call),   # body (long x2)
        LegDef(strike_offset=+upper, quantity=-1, put_call=put_call),   # upper wing
    ]
    return Structure(structure_type=structure_type, centre_strike=centre_strike,
                     legs=legs, width=width)


def make_condor(
    centre_strike: float,
    inner_width: int,
    outer_width: int,
    put_call: str,
    broken_upper_outer: Optional[int] = None,
) -> Structure:
    """Regular or broken condor.

    A condor has two body strikes (inner_width apart from centre) and two wing
    strikes (outer_width from centre). Regular condor: symmetric upper and lower.
    Broken condor: upper outer wing at broken_upper_outer instead of outer_width.

    Standard 6-wide condor: inner_width=3, outer_width=9.
    """
    upper_outer = broken_upper_outer if broken_upper_outer is not None else outer_width
    is_broken = broken_upper_outer is not None and broken_upper_outer != outer_width

    structure_type = StructureType.BROKEN_CONDOR if is_broken else StructureType.CONDOR

    legs = [
        LegDef(strike_offset=-outer_width, quantity=-1, put_call=put_call),  # lower wing
        LegDef(strike_offset=-inner_width, quantity=+1, put_call=put_call),  # lower body
        LegDef(strike_offset=+inner_width, quantity=+1, put_call=put_call),  # upper body
        LegDef(strike_offset=+upper_outer, quantity=-1, put_call=put_call),  # upper wing
    ]
    return Structure(structure_type=structure_type, centre_strike=centre_strike,
                     legs=legs, width=outer_width)


def make_call_spread(lower_strike: float, upper_strike: float) -> Structure:
    """Long call spread: long lower strike call, short upper strike call."""
    tick = Structure.TICK
    offset = round((upper_strike - lower_strike) / tick)
    legs = [
        LegDef(strike_offset=0,       quantity=+1, put_call="call"),
        LegDef(strike_offset=+offset, quantity=-1, put_call="call"),
    ]
    return Structure(structure_type=StructureType.CALL_SPREAD,
                     centre_strike=lower_strike, legs=legs, width=offset)


def make_put_spread(upper_strike: float, lower_strike: float) -> Structure:
    """Long put spread: long upper strike put, short lower strike put."""
    tick = Structure.TICK
    offset = round((upper_strike - lower_strike) / tick)
    legs = [
        LegDef(strike_offset=0,       quantity=+1, put_call="put"),
        LegDef(strike_offset=-offset, quantity=-1, put_call="put"),
    ]
    return Structure(structure_type=StructureType.PUT_SPREAD,
                     centre_strike=upper_strike, legs=legs, width=offset)


def make_ladder(
    centre_strike: float,
    width: int,
    put_call: str,
) -> Structure:
    """1x2x3 ratio ladder centred at centre_strike.

    Long 1 at centre - width, long 2 at centre, short 3 at centre + width.
    Used to express a directional vol view with specific payoff profile.
    """
    legs = [
        LegDef(strike_offset=-width, quantity=+1, put_call=put_call),
        LegDef(strike_offset=0,      quantity=+2, put_call=put_call),
        LegDef(strike_offset=+width, quantity=-3, put_call=put_call),
    ]
    return Structure(structure_type=StructureType.LADDER,
                     centre_strike=centre_strike, legs=legs, width=width)


def make_calendar_stub(near_expiry_strike: float) -> Structure:
    """Calendar spread stub — Phase 2 only. Raises NotImplementedError."""
    raise NotImplementedError("Calendar structures are deferred to Phase 2.")
