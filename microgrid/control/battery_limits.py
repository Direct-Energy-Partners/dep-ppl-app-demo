"""
Battery limit calculator - determines safe charge / discharge power bounds.

This module is intentionally isolated and stateless so it can be unit-tested
and reused independently of the rest of the control system.
"""
from __future__ import annotations

from dataclasses import dataclass
from microgrid import config


@dataclass
class BatteryLimits:
    """Computed safe operating envelope for the battery."""
    max_charge_power_w: float    # positive value - max power INTO the battery
    max_discharge_power_w: float  # positive value - max power OUT OF the battery
    charge_allowed: bool
    discharge_allowed: bool
    reason: str = ""


def compute_battery_limits(
    soc: float,
    voltage: float,
    battery_charge_max_w: float = 0.0,
    battery_discharge_max_w: float = 0.0,
    soc_min: float = config.BATTERY_SOC_MIN,
    soc_max: float = config.BATTERY_SOC_MAX,
    soc_critical_low: float = config.BATTERY_SOC_CRITICAL_LOW,
    soc_critical_high: float = config.BATTERY_SOC_CRITICAL_HIGH,
) -> BatteryLimits:
    """Calculate the maximum safe charge and discharge power for the battery."""

    charge_power = float("inf")
    discharge_power = float("inf")
    charge_allowed = True
    discharge_allowed = True
    reasons: list[str] = []

    # ----- Critical SOC protection -------------------------------------------
    if soc <= soc_critical_low:
        discharge_power = 0.0
        discharge_allowed = False
        reasons.append(f"SOC {soc:.1f}% <= critical low {soc_critical_low}%")

    if soc >= soc_critical_high:
        charge_power = 0.0
        charge_allowed = False
        reasons.append(f"SOC {soc:.1f}% >= critical high {soc_critical_high}%")

    # ----- Normal SOC window -------------------------------------------------
    if soc <= soc_min:
        discharge_power = 0.0
        discharge_allowed = False
        reasons.append(f"SOC {soc:.1f}% <= min {soc_min}%")

    if soc >= soc_max:
        charge_power = 0.0
        charge_allowed = False
        reasons.append(f"SOC {soc:.1f}% >= max {soc_max}%")

    # ----- Apply battery-reported limits if available --------------------------
    if battery_charge_max_w > 0:
        charge_power = min(charge_power, battery_charge_max_w)
    if battery_discharge_max_w > 0:
        discharge_power = min(discharge_power, battery_discharge_max_w)

    # ----- Voltage protection ------------------------------------------------
    if voltage <= 0:
        charge_power = 0.0
        discharge_power = 0.0
        charge_allowed = False
        discharge_allowed = False
        reasons.append("Invalid battery voltage")

    # ----- Final clamping ----------------------------------------------------
    if charge_power == float("inf"):
        charge_power = battery_charge_max_w if battery_charge_max_w > 0 else 0.0
    if discharge_power == float("inf"):
        discharge_power = battery_discharge_max_w if battery_discharge_max_w > 0 else 0.0

    charge_power = max(0.0, charge_power)
    discharge_power = max(0.0, discharge_power)

    return BatteryLimits(
        max_charge_power_w=charge_power,
        max_discharge_power_w=discharge_power,
        charge_allowed=charge_allowed,
        discharge_allowed=discharge_allowed,
        reason="; ".join(reasons) if reasons else "OK",
    )
