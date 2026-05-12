"""
D1: System Modes - Top-level FSM.

States:
  POWERED_OFF        - All contactors open, all devices idle, PLC alive on UPS/aux
  BATTERY_BLACKSTART - BESS energises bus via Converdan (preferred startup path)
  GRID_BLACKSTART    - REG energises bus via K14 (BESS unavailable fallback)
  GRID_CONNECTED     - Bus live, BESS + REG available → see D2
  ISLANDED           - Grid absent, BESS sole supply → see D3
  FAULT              - Comms loss, equipment fault, operator shutdown
  PLANNED_SHUTDOWN   - Graceful ramp-down sequence → see D4

Transitions are evaluated every control cycle by the orchestrator.
"""
from __future__ import annotations

import enum
import logging
import time
from dataclasses import dataclass, field

from microgrid import config

log = logging.getLogger("microgrid.system_mode")


class SystemMode(enum.Enum):
    POWERED_OFF = "POWERED_OFF"
    BATTERY_BLACKSTART = "BATTERY_BLACKSTART"
    GRID_BLACKSTART = "GRID_BLACKSTART"
    GRID_CONNECTED = "GRID_CONNECTED"
    ISLANDED = "ISLANDED"
    FAULT = "FAULT"
    PLANNED_SHUTDOWN = "PLANNED_SHUTDOWN"


@dataclass
class SystemModeContext:
    """Persistent state for the D1 FSM across control cycles."""
    mode: SystemMode = SystemMode.POWERED_OFF
    previous_mode: SystemMode = SystemMode.POWERED_OFF
    fault_reasons: list[str] = field(default_factory=list)
    operator_reset_required: bool = False
    shutdown_requested: bool = False
    grid_stable_since: float = 0.0

    def transition_to(self, new_mode: SystemMode, reason: str = "") -> None:
        if new_mode != self.mode:
            log.info("system_mode transition: %s → %s (%s)", self.mode.value, new_mode.value, reason)
            self.previous_mode = self.mode
            self.mode = new_mode


class SystemModeFSM:
    """
    D1 top-level state machine. Evaluates transitions every tick.

    The orchestrator calls evaluate() with the current system snapshot and
    this FSM decides which top-level mode is active. Sub-FSMs (D2, D3) only
    run when the corresponding mode is active.
    """

    def __init__(self):
        self.ctx = SystemModeContext()

    @property
    def mode(self) -> SystemMode:
        return self.ctx.mode

    def request_shutdown(self) -> None:
        """Operator-initiated planned shutdown."""
        self.ctx.shutdown_requested = True

    def request_fault_reset(self) -> None:
        """Operator resets fault - allows transition back to POWERED_OFF."""
        self.ctx.operator_reset_required = False
        self.ctx.fault_reasons.clear()
        self.ctx.transition_to(SystemMode.POWERED_OFF, "operator fault reset")

    def evaluate(
        self,
        battery_available: bool,
        battery_soc: float,
        ac_grid_available: bool,
        bus_live: bool,
        ev_contactors_closed: bool,
        all_devices_idle: bool,
        comms_loss: bool,
        equipment_fault: bool,
        battery_soc_critical: bool,
    ) -> SystemMode:
        """Evaluate D1 transitions. Returns the current (possibly new) mode."""
        mode = self.ctx.mode

        # ----- FAULT detection (from any state except POWERED_OFF) -----
        if mode not in (SystemMode.POWERED_OFF, SystemMode.FAULT):
            if comms_loss:
                self._enter_fault("Communication loss")
                return self.ctx.mode
            if equipment_fault:
                self._enter_fault("Equipment fault / alarm")
                return self.ctx.mode
            if self.ctx.shutdown_requested:
                self.ctx.shutdown_requested = False
                self.ctx.transition_to(SystemMode.PLANNED_SHUTDOWN, "operator shutdown command")
                return self.ctx.mode

        # ----- State-specific transitions -----
        if mode == SystemMode.POWERED_OFF:
            return self._eval_powered_off(battery_available, battery_soc, ac_grid_available)

        elif mode == SystemMode.BATTERY_BLACKSTART:
            return self._eval_battery_blackstart(bus_live, ev_contactors_closed)

        elif mode == SystemMode.GRID_BLACKSTART:
            return self._eval_grid_blackstart(bus_live)

        elif mode == SystemMode.GRID_CONNECTED:
            return self._eval_grid_connected(ac_grid_available, battery_soc_critical)

        elif mode == SystemMode.ISLANDED:
            return self._eval_islanded(ac_grid_available, battery_soc_critical)

        elif mode == SystemMode.FAULT:
            return self._eval_fault()

        elif mode == SystemMode.PLANNED_SHUTDOWN:
            return self._eval_planned_shutdown(all_devices_idle)

        return self.ctx.mode

    # ----- Per-state transition logic -----

    def _eval_powered_off(self, battery_available: bool, battery_soc: float, ac_grid_available: bool) -> SystemMode:
        # Normal path: BESS available with SOC >= 20%
        if battery_available and battery_soc >= config.BATTERY_SOC_BLACKSTART_MIN:
            self.ctx.transition_to(SystemMode.BATTERY_BLACKSTART, "Battery available, SOC >= 20%")
        # Fallback path: BESS unavailable (SOC < 20% or fault) AND grid available
        elif (not battery_available or battery_soc < config.BATTERY_SOC_BLACKSTART_MIN) and ac_grid_available:
            self.ctx.transition_to(SystemMode.GRID_BLACKSTART, "Battery unavailable, grid available")
        return self.ctx.mode

    def _eval_battery_blackstart(self, bus_live: bool, ev_contactors_closed: bool) -> SystemMode:
        # Startup sequence complete: bus live, EV contactors closed
        if bus_live and ev_contactors_closed:
            self.ctx.transition_to(SystemMode.GRID_CONNECTED, "Startup complete - bus live, EV contactors closed")
        return self.ctx.mode

    def _eval_grid_blackstart(self, bus_live: bool) -> SystemMode:
        # Bus live via REG (BESS connects later per D4 Proc E)
        if bus_live:
            self.ctx.transition_to(SystemMode.GRID_CONNECTED, "Bus live via REG")
        return self.ctx.mode

    def _eval_grid_connected(self, ac_grid_available: bool, battery_soc_critical: bool) -> SystemMode:
        # Grid loss detection: AC meter voltage < threshold
        if not ac_grid_available:
            # REG output drops to 0, disable REG, open K14
            self.ctx.transition_to(SystemMode.ISLANDED, "Grid loss detected")
        # Critical SOC while grid connected → FAULT
        if battery_soc_critical:
            self._enter_fault("Battery SOC critical in GRID_CONNECTED")
        return self.ctx.mode

    def _eval_islanded(self, ac_grid_available: bool, battery_soc_critical: bool) -> SystemMode:
        # Grid restored: AC meter stable >= 30s
        if ac_grid_available:
            if self.ctx.grid_stable_since == 0.0:
                self.ctx.grid_stable_since = time.time()
            elif (time.time() - self.ctx.grid_stable_since) >= config.GRID_STABILITY_TIMER_S:
                # Grid stable for 30s → transition handled by D3 GRID_RESTORE_PENDING
                # which will ultimately tell D1 to go to GRID_CONNECTED
                self.ctx.grid_stable_since = 0.0
                self.ctx.transition_to(SystemMode.GRID_CONNECTED, "Grid restored (stable >= 30s)")
        else:
            self.ctx.grid_stable_since = 0.0

        # ISL_CRITICAL_SOC → FAULT
        if battery_soc_critical:
            self._enter_fault("Battery SOC <= 10% while islanded (ISL_CRITICAL_SOC)")
        return self.ctx.mode

    def _eval_fault(self) -> SystemMode:
        # Stays in FAULT until operator reset
        # (request_fault_reset() handles the transition externally)
        return self.ctx.mode

    def _eval_planned_shutdown(self, all_devices_idle: bool) -> SystemMode:
        # Shutdown complete: all contactors open, all devices idle
        if all_devices_idle:
            self.ctx.transition_to(SystemMode.POWERED_OFF, "Shutdown complete")
        return self.ctx.mode

    def _enter_fault(self, reason: str) -> None:
        self.ctx.fault_reasons.append(reason)
        self.ctx.operator_reset_required = True
        self.ctx.transition_to(SystemMode.FAULT, reason)
