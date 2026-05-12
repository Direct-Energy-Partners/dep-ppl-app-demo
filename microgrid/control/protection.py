"""
Software protection module - evaluates system-wide safety conditions every
control loop and returns a set of active protection flags.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from microgrid import config


@dataclass
class ProtectionFlags:
    """Snapshot of active protection states for the current control cycle."""
    bus_undervoltage: bool = False          # DC bus < 700 V
    bus_undervoltage_resume: bool = True    # DC bus >= 720 V (hysteresis cleared)
    bus_overvoltage: bool = False           # DC bus > 780 V
    ac_overcurrent: bool = False            # AC meter >= 60 A
    battery_soc_critical_low: bool = False     # SOC <= 10 %
    battery_soc_critical_high: bool = False    # SOC >= 90 %
    cabinet_overtemp: bool = False          # Cabinet temp > 45 °C
    communication_loss: bool = False        # Device(s) not reporting
    equipment_fault: bool = False           # Any device fault active
    charging_suspended: bool = False        # Derived: should all charging stop?
    shutdown_required: bool = False         # Derived: planned shutdown needed?
    active_reasons: list[str] = field(default_factory=list)


class ProtectionManager:
    """Stateful protection evaluator (keeps hysteresis state between cycles)."""

    def __init__(self):
        self._charging_suspended = False  # latched until resume threshold met
        self._bus_resume_since: float = 0.0

    def evaluate(
        self,
        dc_bus_voltage: float,
        battery_soc: float,
        ac_current: float = 0.0,
        cabinet_temp: float = 25.0,
        battery_available: bool = True,
        converdan_available: bool = True,
        converdan_has_fault: bool = False,
    ) -> ProtectionFlags:
        flags = ProtectionFlags()
        now = time.time()

        # --- DC bus voltage protection ---------------------------------------
        # Bus < 700V → suspend all chargers
        if dc_bus_voltage < config.DC_BUS_VOLTAGE_SUSPEND_THRESHOLD:
            self._charging_suspended = True
            self._bus_resume_since = 0.0
            flags.bus_undervoltage = True
            flags.active_reasons.append(f"DC bus {dc_bus_voltage:.0f} V < {config.DC_BUS_VOLTAGE_SUSPEND_THRESHOLD} V")

        # Resume > 720V after 30s stable
        if dc_bus_voltage >= config.DC_BUS_VOLTAGE_RESUME_THRESHOLD:
            if self._bus_resume_since == 0.0:
                self._bus_resume_since = now
            elif (now - self._bus_resume_since) >= config.GRID_STABILITY_TIMER_S:
                self._charging_suspended = False
                flags.bus_undervoltage_resume = True
            else:
                flags.bus_undervoltage_resume = False
        else:
            self._bus_resume_since = 0.0
            flags.bus_undervoltage_resume = False

        flags.charging_suspended = self._charging_suspended

        # Bus > 780V → stop REG + disable Converdan (resume < 760V after 30s stable)
        if dc_bus_voltage > config.DC_BUS_VOLTAGE_OVERVOLTAGE:
            flags.bus_overvoltage = True
            flags.active_reasons.append(f"DC bus {dc_bus_voltage:.0f} V > {config.DC_BUS_VOLTAGE_OVERVOLTAGE} V (overvoltage)")

        # --- AC overcurrent --------------------------------------------------
        if ac_current >= config.AC_OVERCURRENT_THRESHOLD:
            flags.ac_overcurrent = True
            flags.active_reasons.append(f"AC current {ac_current:.1f} A >= {config.AC_OVERCURRENT_THRESHOLD} A")

        # --- BESS SOC critical -----------------------------------------------
        # SOC ≤ 10% → disable Converdan, curtail all → FAULT
        if battery_soc <= config.BATTERY_SOC_CRITICAL_LOW:
            flags.battery_soc_critical_low = True
            flags.active_reasons.append(f"Battery SOC {battery_soc:.1f}% <= {config.BATTERY_SOC_CRITICAL_LOW}%")

        # SOC ≥ 90% → disable Converdan, curtail all
        if battery_soc >= config.BATTERY_SOC_CRITICAL_HIGH:
            flags.battery_soc_critical_high = True
            flags.active_reasons.append(f"Battery SOC {battery_soc:.1f}% >= {config.BATTERY_SOC_CRITICAL_HIGH}%")

        # --- Cabinet temperature ---------------------------------------------
        if cabinet_temp > config.CABINET_TEMP_MAX:
            flags.cabinet_overtemp = True
            flags.shutdown_required = True
            flags.active_reasons.append(f"Cabinet temp {cabinet_temp:.1f} °C > {config.CABINET_TEMP_MAX} °C")

        # --- Communication loss ----------------------------------------------
        if not battery_available and not converdan_available:
            flags.communication_loss = True
            flags.shutdown_required = True
            flags.active_reasons.append("Communication loss - no devices reporting")

        # --- Equipment fault --------------------------------------------------
        if converdan_has_fault:
            flags.equipment_fault = True
            flags.active_reasons.append("Converdan fault active")

        return flags
