"""Grid Connected sub-FSM."""

from __future__ import annotations

import enum
import logging
import time
from dataclasses import dataclass, field

from microgrid import config
from microgrid.modes.base_mode import ModeOutput

log = logging.getLogger("microgrid.d2_grid_connected")


class GCState(enum.Enum):
    GC_STANDBY = "GC_STANDBY"
    BATTERY_SOLE_SUPPLY = "BATTERY_SOLE_SUPPLY"
    BATTERY_GRID_SHARED = "BATTERY_GRID_SHARED"
    BATTERY_LOW_SOC_HOLD = "BATTERY_LOW_SOC_HOLD"
    GRID_SOLE_SUPPLY = "GRID_SOLE_SUPPLY"
    BATTERY_RECHARGING = "BATTERY_RECHARGING"


@dataclass
class GCContext:
    """Persistent state for the D2 sub-FSM."""
    state: GCState = GCState.GC_STANDBY
    state_entry_time: float = field(default_factory=time.time)
    prev_reg_current: float = 0.0
    prev_infy_w: float = 0.0
    prev_winline_w: float = 0.0
    recharge_voltage_delta: float = 0.0

    def transition_to(self, new_state: GCState, reason: str = "") -> None:
        if new_state != self.state:
            log.info("grid_connected transition: %s → %s (%s)", self.state.value, new_state.value, reason)
            self.state = new_state
            self.state_entry_time = time.time()

    @property
    def time_in_state(self) -> float:
        return time.time() - self.state_entry_time


class GridConnectedFSM:
    """
    D2 sub-FSM. Called every tick when D1 mode == GRID_CONNECTED.
    """

    def __init__(self):
        self.ctx = GCContext()

    @property
    def state(self) -> GCState:
        return self.ctx.state

    def reset(self) -> None:
        """Reset to standby when entering GRID_CONNECTED."""
        self.ctx = GCContext()

    def evaluate(
        self,
        battery_soc: float,
        battery_available: bool,
        battery_voltage: float,
        dc_bus_voltage: float,
        ev_sessions_active: bool,
        ev_demand_w: float,
        battery_available_power_w: float,
        prev_reg_current: float,
        prev_infy_w: float,
        prev_winline_w: float,
    ) -> ModeOutput:
        """Evaluate D2 state transitions and produce setpoints."""
        self.ctx.prev_reg_current = prev_reg_current
        self.ctx.prev_infy_w = prev_infy_w
        self.ctx.prev_winline_w = prev_winline_w

        # --- Evaluate transitions ---
        self._evaluate_transitions(
            battery_soc, battery_available, ev_sessions_active,
            ev_demand_w, battery_available_power_w,
        )

        # --- Compute output for current state ---
        return self._compute_output(
            battery_soc, battery_voltage, dc_bus_voltage,
            ev_sessions_active, ev_demand_w, battery_available_power_w,
        )

    def _evaluate_transitions(
        self,
        battery_soc: float,
        battery_available: bool,
        ev_sessions_active: bool,
        ev_demand_w: float,
        battery_available_power_w: float,
    ) -> None:
        state = self.ctx.state

        if state == GCState.GC_STANDBY:
            # EV session starts (vehicle connected, BESS SOC > 20%)
            if ev_sessions_active and battery_soc > config.BATTERY_SOC_MIN:
                if ev_demand_w <= battery_available_power_w:
                    self.ctx.transition_to(GCState.BATTERY_SOLE_SUPPLY, "EV session started, BESS can cover")
                else:
                    self.ctx.transition_to(GCState.BATTERY_GRID_SHARED, "EV session started, demand > BESS")
            # No EV sessions AND BESS SOC ≤ 70% → recharge
            elif not ev_sessions_active and battery_soc <= config.BATTERY_SOC_RECHARGE_START:
                self.ctx.transition_to(GCState.BATTERY_RECHARGING, "No EV sessions, SOC ≤ 70%")

        elif state == GCState.BATTERY_SOLE_SUPPLY:
            # EV demand exceeds BESS available → shared
            if ev_demand_w > battery_available_power_w:
                self.ctx.transition_to(GCState.BATTERY_GRID_SHARED, "EV demand > BESS available power")
            # All EV sessions ended
            elif not ev_sessions_active:
                self.ctx.transition_to(GCState.GC_STANDBY, "All EV sessions ended")
            # BESS SOC ≤ 20% (EV sessions active)
            elif battery_soc <= config.BATTERY_SOC_MIN:
                self.ctx.transition_to(GCState.BATTERY_LOW_SOC_HOLD, "SOC ≤ 20%")

        elif state == GCState.BATTERY_GRID_SHARED:
            # EV demand drops back to within BESS capacity
            if ev_sessions_active and ev_demand_w <= battery_available_power_w:
                self.ctx.transition_to(GCState.BATTERY_SOLE_SUPPLY, "EV demand ≤ BESS available")
            # All EV sessions ended
            elif not ev_sessions_active:
                self.ctx.transition_to(GCState.GC_STANDBY, "All EV sessions ended, set REG I-limit = 0A")
            # BESS SOC ≤ 20% (EV sessions active)
            elif battery_soc <= config.BATTERY_SOC_MIN:
                self.ctx.transition_to(GCState.BATTERY_LOW_SOC_HOLD, "SOC ≤ 20%")

        elif state == GCState.BATTERY_LOW_SOC_HOLD:
            # Converdan disabled, transition complete → GRID_SOLE_SUPPLY
            # We detect "complete" when chargers have ramped down and time allows K3 to open
            if self.ctx.prev_infy_w <= 0 and self.ctx.prev_winline_w <= 0:
                self.ctx.transition_to(GCState.GRID_SOLE_SUPPLY, "Converdan disabled, transition complete")

        elif state == GCState.GRID_SOLE_SUPPLY:
            # All EV sessions ended → check if recharge needed
            if not ev_sessions_active:
                self.ctx.transition_to(GCState.BATTERY_RECHARGING, "All EV sessions ended, charger power = 0")
            # EV sessions end and SOC still low → will enter BESS_RECHARGING

        elif state == GCState.BATTERY_RECHARGING:
            # BESS SOC reaches 80% → stop recharging
            if battery_soc >= config.BATTERY_SOC_RECHARGE_STOP:
                self.ctx.transition_to(GCState.GC_STANDBY, "BESS SOC >= 80%, recharge complete")
            # EV session starts while recharging
            elif ev_sessions_active and battery_soc > config.BATTERY_SOC_MIN:
                self.ctx.transition_to(GCState.BATTERY_SOLE_SUPPLY, "EV session started during recharge")

    def _compute_output(
        self,
        battery_soc: float,
        battery_voltage: float,
        dc_bus_voltage: float,
        ev_sessions_active: bool,
        ev_demand_w: float,
        battery_available_power_w: float,
    ) -> ModeOutput:
        state = self.ctx.state

        if state == GCState.GC_STANDBY:
            return self._output_gc_standby(dc_bus_voltage)
        elif state == GCState.BATTERY_SOLE_SUPPLY:
            return self._output_battery_sole_supply(dc_bus_voltage, battery_available_power_w, ev_demand_w)
        elif state == GCState.BATTERY_GRID_SHARED:
            return self._output_battery_grid_shared(dc_bus_voltage, battery_available_power_w, ev_demand_w)
        elif state == GCState.BATTERY_LOW_SOC_HOLD:
            return self._output_battery_low_soc_hold(dc_bus_voltage)
        elif state == GCState.GRID_SOLE_SUPPLY:
            return self._output_grid_sole_supply(dc_bus_voltage, ev_demand_w)
        elif state == GCState.BATTERY_RECHARGING:
            return self._output_battery_recharging(battery_voltage, dc_bus_voltage)
        # Fallback
        return self._output_gc_standby(dc_bus_voltage)

    # ----- State output functions -----

    def _output_gc_standby(self, bus_v: float) -> ModeOutput:
        return ModeOutput(
            converdan_enabled=True,
            converdan_ratio=config.CONVERDAN_RATIO_NOMINAL,
            rectifier_enabled=True,
            rectifier_voltage=bus_v + config.RECTIFIER_VOLTAGE_SETPOINT_OFFSET,
            rectifier_current_limit=0,
            infypower_charger_power_w=0,
            winline_charger_power_w=0,
            description="GC_STANDBY - bus live, no EV sessions, REG I-limit=0A",
        )

    def _output_battery_sole_supply(
        self, bus_v: float, battery_available_w: float, ev_demand_w: float
    ) -> ModeOutput:
        # BESS can cover demand; REG I-limit = 0A (voltage follower only)
        max_total = battery_available_w * config.CHARGER_POWER_DERATING
        infy_w = min(config.INFYPOWER_CHARGER_POWER_MAX_W, max_total * 0.43)  # ~60/(60+80)
        winline_w = min(config.WINLINE_POWER_MAX_W, max_total - infy_w)

        return ModeOutput(
            converdan_enabled=True,
            converdan_ratio=config.CONVERDAN_RATIO_NOMINAL,
            rectifier_enabled=True,
            rectifier_voltage=bus_v + config.RECTIFIER_VOLTAGE_SETPOINT_OFFSET,
            rectifier_current_limit=0,
            infypower_charger_power_w=infy_w,
            winline_charger_power_w=winline_w,
            description=f"BESS_SOLE_SUPPLY - Infy {infy_w/1000:.0f}kW, Win {winline_w/1000:.0f}kW, REG I=0A",
        )

    def _output_battery_grid_shared(
        self, bus_v: float, battery_available_w: float, ev_demand_w: float
    ) -> ModeOutput:
        # EV demand > BESS available; raise REG I-limit to 100A (5s walk-in)
        target_reg_current = config.RECTIFIER_CURRENT_MAX
        # Walk-in: ramp up from previous value
        reg_current = min(
            target_reg_current,
            self.ctx.prev_reg_current + config.RECTIFIER_RECHARGE_RAMP_A_PER_STEP,
        )

        # EV setpoints: Infy + Winline up to 120kW combined
        max_total = min(config.CHARGER_COMBINED_MAX_GRID_W, ev_demand_w)
        infy_w = min(config.INFYPOWER_CHARGER_POWER_MAX_W, max_total * 0.5)
        winline_w = min(config.WINLINE_POWER_MAX_W, max_total - infy_w)

        return ModeOutput(
            converdan_enabled=True,
            converdan_ratio=config.CONVERDAN_RATIO_NOMINAL,
            rectifier_enabled=True,
            rectifier_voltage=bus_v + config.RECTIFIER_VOLTAGE_SETPOINT_OFFSET,
            rectifier_current_limit=reg_current,
            infypower_charger_power_w=infy_w,
            winline_charger_power_w=winline_w,
            description=f"BESS_GRID_SHARED - Infy {infy_w/1000:.0f}kW, Win {winline_w/1000:.0f}kW, REG I={reg_current:.0f}A",
        )

    def _output_battery_low_soc_hold(self, bus_v: float) -> ModeOutput:
        # Ramp down all charger setpoints at 1kW/s
        infy_w = max(0, self.ctx.prev_infy_w - config.CHARGER_RAMP_STEP_W)
        winline_w = max(0, self.ctx.prev_winline_w - config.CHARGER_RAMP_STEP_W)

        # Raise REG I-limit → 100A (5s walk-in) before disabling Converdan
        reg_current = min(
            config.RECTIFIER_CURRENT_MAX,
            self.ctx.prev_reg_current + config.RECTIFIER_RECHARGE_RAMP_A_PER_STEP,
        )

        # Keep Converdan enabled until chargers fully ramped down
        converdan_enabled = (infy_w + winline_w) > 0

        return ModeOutput(
            converdan_enabled=converdan_enabled,
            converdan_ratio=config.CONVERDAN_RATIO_NOMINAL,
            rectifier_enabled=True,
            rectifier_voltage=bus_v + config.RECTIFIER_VOLTAGE_SETPOINT_OFFSET,
            rectifier_current_limit=reg_current,
            infypower_charger_power_w=infy_w,
            winline_charger_power_w=winline_w,
            description=f"BESS_LOW_SOC_HOLD - ramping down, REG I={reg_current:.0f}A",
        )

    def _output_grid_sole_supply(self, bus_v: float, ev_demand_w: float) -> ModeOutput:
        # REG holds DC bus voltage, I-limit = 100A, Converdan disabled (K3 open)
        infy_w = min(config.INFYPOWER_CHARGER_POWER_MAX_W, ev_demand_w * 0.5) if ev_demand_w > 0 else 0
        winline_w = min(config.WINLINE_POWER_MAX_W, ev_demand_w - infy_w) if ev_demand_w > 0 else 0

        return ModeOutput(
            converdan_enabled=False,
            converdan_ratio=config.CONVERDAN_RATIO_NOMINAL,
            rectifier_enabled=True,
            rectifier_voltage=bus_v + config.RECTIFIER_VOLTAGE_SETPOINT_OFFSET,
            rectifier_current_limit=config.RECTIFIER_CURRENT_MAX,
            infypower_charger_power_w=infy_w,
            winline_charger_power_w=winline_w,
            description=f"GRID_SOLE_SUPPLY - REG I=100A, Converdan off, SOC≤20%",
        )

    def _output_battery_recharging(self, battery_voltage: float, bus_v: float) -> ModeOutput:
        # Re-enable Converdan, REG V = Converdan P1 + ΔV, ramp ΔV to push ~30kW into BESS
        converdan_p1_v = battery_voltage * config.CONVERDAN_RATIO_NOMINAL
        # Ramp ΔV by +5V / 5s (i.e. +1V per control cycle at 5s interval)
        self.ctx.recharge_voltage_delta = min(
            self.ctx.recharge_voltage_delta + 1.0,
            20.0,  # max delta to achieve ~30kW
        )
        reg_voltage = converdan_p1_v + self.ctx.recharge_voltage_delta

        return ModeOutput(
            converdan_enabled=True,
            converdan_ratio=config.CONVERDAN_RATIO_NOMINAL,
            rectifier_enabled=True,
            rectifier_voltage=reg_voltage,
            rectifier_current_limit=config.RECTIFIER_CURRENT_MAX,
            infypower_charger_power_w=0,
            winline_charger_power_w=0,
            description=f"BESS_RECHARGING - REG V={reg_voltage:.0f}V, ΔV={self.ctx.recharge_voltage_delta:.0f}V",
        )
