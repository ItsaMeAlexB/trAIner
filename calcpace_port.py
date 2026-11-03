"""Python port of the Ruby `calcpace` running calculators.

The Ruby gem `calcpace` is a dependency the project deliberately avoids at
runtime (see README caveats). These functions are local ports of its core
running-science relations, keeping the same units so results are interchangeable.

Implemented (README "Calculators and conversions" -> Running):
  * Minetti et al. (2002) gradient cost model      -> grade time factor
  * El Helou et al. (2012) heat model              -> temperature time factor
  * NCAA altitude standard                         -> altitude time factor
  * Riegel (1977) race-time prediction             -> T2 = T1 * (D2/D1)^1.06
  * Jack Daniels VDOT                              -> aerobic fitness score

All "pace/time factors" are multipliers on finish time (>1 = slower). A log is
normalised to its flat, perfect-weather equivalent by multiplying duration by
the product of the applicable factors.
"""

from __future__ import annotations

import math


def minetti_pace_factor(grade: float) -> float:
    """Time factor for running on a grade (fraction, e.g. 0.05 = +5%).

    Minetti et al. 2002 energy cost of running (J/kg/m) as a polynomial of
    grade g; flat running cost is the g=0 term (3.6 J/kg/m). On average a
    grade cost ratio of 1 means the same energy as running on the flat; we
    return the time factor = cost(g)/cost(flat) so uphill slows the runner.
    """
    if grade == 0:
        return 1.0
    cost = (
        155.4 * grade ** 5
        - 30.4 * grade ** 4
        - 43.3 * grade ** 3
        + 46.3 * grade ** 2
        + 19.5 * grade
        + 3.6
    )
    flat = 3.6  # cost(0)
    return max(flat, cost) / flat if grade > 0 else flat / max(flat, cost)


def heat_pace_factor(temp_c: float) -> float:
    """Time factor for temperature (deg C), El Helou et al. 2012.

    Marathon performance is best around 8-13 C and degrades with heat. The
    published gradient is roughly ~1% per °C above ~8 C; we linearly apply it
    and add a cold penalty below ~3 C (as the port does). Values are capped so
    the factor stays plausible for very hot days.
    """
    optimal = 8.0
    if temp_c >= optimal:
        return 1.0 + min(0.35, (temp_c - optimal) * 0.01)  # ~1%/°C, cap +35%
    return 1.0 + max(0.0, (optimal - temp_c) * 0.004)  # mild cold penalty


def _altitude_factor(alt_m: float) -> float:
    """NCAA-style time factor: ~3% slower per 1000 m above 500 m."""
    if alt_m <= 500:
        return 1.0
    return 1.0 + (alt_m - 500) / 1000.0 * 0.03


def normalise_seconds(
    seconds: float,
    distance_m: float,
    elevation_gain_m: float = 0.0,
    temp_c: float = 8.0,
    alt_m: float = 0.0,
    downhill_returns: bool = False,
) -> float:
    """Convert a run's finish time to flat, perfect-weather equivalent seconds.

    grade = gain / distance (fraction). Unless `downhill_returns` is set, net
    negative elevation is treated as flat so a net-downhill effort is not
    gifted a free speedup.
    """
    if distance_m <= 0:
        return seconds
    grade = elevation_gain_m / distance_m
    if not downhill_returns and grade < 0:
        grade = 0.0
    factor = minetti_pace_factor(grade) * heat_pace_factor(temp_c) * _altitude_factor(alt_m)
    return seconds * factor


def riegel_predict(ref_seconds: float, ref_dist_m: float, target_dist_m: float) -> float:
    """Riegel (1977): T2 = T1 * (D2/D1)^1.06."""
    if ref_dist_m <= 0:
        raise ValueError("reference distance must be > 0")
    return ref_seconds * (target_dist_m / ref_dist_m) ** 1.06


def vdot(speed_m_per_min: float) -> float:
    """Jack Daniels VDOT from average speed (m/min)."""
    if speed_m_per_min <= 0:
        return 0.0
    return -4.60 + 0.182258 * speed_m_per_min + 0.000104 * speed_m_per_min ** 2


def seconds_per_km(seconds: float, distance_m: float) -> float:
    """Return pace in seconds-per-km for a given finish time and distance."""
    if distance_m <= 0:
        return 0.0
    return seconds / distance_m * 1000.0
