"""
D3: Islanded sub-FSM.

Active when D1 System Mode is ISLANDED. REG unavailable. BESS is sole supply
via Converdan (DC transformer mode). No BESS charging possible.
Same continuous protection limits apply as per D2.

States:
  ISL_STANDBY           - Bus live via BESS + Converdan. Ratio fixed at 1.058.
                          No EV sessions. K11, K13 closed. REG offline (K14 open).

  ISL_EV_CHARGING       - EV session(s) active.
                          Total setpoints ≤ (P_BESS_available) × 90%.
                          Max combined: 80kW (40kW each when both active).
                          New vehicle: reduce other charger first.

  ISL_SOC_LOW           - BESS SOC approaching 20%.
                          Ramp down all charger setpoints at 1kW/s.
                          Suspend sessions when SOC ≤ 20%.
                          Converdan remains enabled (BESS still coupled).
                          Hard limit: SOC ≤ 10% → D1 FAULT.

  ISL_CHARGERS_SUSPENDED - SOC ≤ 20%. All EV sessions stopped.
                          K11, K13 remain closed (chargers online, no sessions).
                          Converdan enabled (bus voltage maintained).
                          Await grid restore or further SOC depletion.

  ISL_CRITICAL_SOC      - SOC ≤ 10%. Emergency shutdown.
                          Disable Converdan (passive → K3 open).
                          Bus collapses. Await grid restore.
                          Operator reset required before restart.

  GRID_RESTORE_PENDING  - AC meter detects grid voltage stable ≥ 30s.
                          REG starts up, sets V = Converdan P1 voltage.
                          Voltage match confirmed → close K14 (5s walk-in).
                          Transition D1 to GRID_CONNECTED.

Notes:
  - Grid detection: AC meter (Phoenix Contact) monitors AC voltage.
    Normal = 216-253VAC on all 3 phases. 30s stability timer.
  - Scenario 14: Grid restore, BESS low SOC:
    Converdan stays ENABLED (bus must remain live for REG voltage match).
    REG starts up, sets V = Converdan P1 voltage before K14 closes.
    Once K14 closed: BESS recharging begins.
"""
from __future__ import annotations

import enum
import logging
import time
from dataclasses import dataclass, field

from microgrid import config
from microgrid.modes.base_mode import ModeOutput

log = logging.getLogger("microgrid.d3_islanded")


class ISLState(enum.Enum):
    ISL_STANDBY = "ISL_STANDBY"
    ISL_EV_CHARGING = "ISL_EV_CHARGING"
    ISL_SOC_LOW = "ISL_SOC_LOW"
    ISL_CHARGERS_SUSPENDED = "ISL_CHARGERS_SUSPENDED"
    ISL_CRITICAL_SOC = "ISL_CRITICAL_SOC"
    GRID_RESTORE_PENDING = "GRID_RESTORE_PENDING"


@dataclass
class ISLContext:
    """Persistent state for the D3 sub-FSM."""
    state: ISLState = ISLState.ISL_STANDBY
    state_entry_time: float = field(default_factory=time.time)
    prev_infy_w: float = 0.0
    prev_winline_w: float = 0.0
    grid_stable_since: float = 0.0
    grid_restore_voltage_matched: bool = False

    def transition_to(self, new_state: ISLState, reason: str = "") -> None:
        if new_state != self.state:
            log.info("islanded transition: %s → %s (%s)", self.state.value, new_state.value, reason)
            self.state = new_state
            self.state_entry_time = time.time()

    @property
    def time_in_state(self) -> float:
        return time.time() - self.state_entry_time


class IslandedFSM:
    """
    D3 sub-FSM. Called every tick when D1 mode == ISLANDED.
    """

    def __init__(self):
        self.ctx = ISLContext()

    @property
    def state(self) -> ISLState:
        return self.ctx.state

    @property
    def is_critical_soc(self) -> bool:
        """Used by D1 to trigger FAULT."""
        return self.ctx.state == ISLState.ISL_CRITICAL_SOC

    @property
    def grid_restore_complete(self) -> bool:
        """Used by D1 to transition to GRID_CONNECTED."""
        return (
            self.ctx.state == ISLState.GRID_RESTORE_PENDING
            and self.ctx.grid_restore_voltage_matched
        )

    def reset(self) -> None:
        """Reset to standby when entering ISLANDED."""
        self.ctx = ISLContext()

    def evaluate(
        self,
        battery_soc: float,
        battery_available_power_w: float,
        battery_voltage: float,
        dc_bus_voltage: float,
        ev_sessions_active: bool,
        ev_demand_w: float,
        ac_grid_available: bool,
        prev_infy_w: float,
        prev_winline_w: float,
    ) -> ModeOutput:
        """Evaluate D3 state transitions and produce setpoints."""
        self.ctx.prev_infy_w = prev_infy_w
        self.ctx.prev_winline_w = prev_winline_w

        self._evaluate_transitions(
            battery_soc, ev_sessions_active, ac_grid_available,
        )

        return self._compute_output(
            battery_soc, battery_available_power_w, battery_voltage, ev_demand_w,
        )

    def _evaluate_transitions(
        self,
        battery_soc: float,
        ev_sessions_active: bool,
        ac_grid_available: bool,
    ) -> None:
        state = self.ctx.state

        # --- Grid restore detection (from any islanded state) ---
        if state not in (ISLState.ISL_CRITICAL_SOC, ISLState.GRID_RESTORE_PENDING):
            if ac_grid_available:
                if self.ctx.grid_stable_since == 0.0:
                    self.ctx.grid_stable_since = time.time()
                elif (time.time() - self.ctx.grid_stable_since) >= config.GRID_STABILITY_TIMER_S:
                    self.ctx.transition_to(ISLState.GRID_RESTORE_PENDING, "Grid stable ≥ 30s")
                    self.ctx.grid_stable_since = 0.0
                    return
            else:
                self.ctx.grid_stable_since = 0.0

        if state == ISLState.ISL_STANDBY:
            # BESS SOC ≤ 25% (no sessions, pre-warning)
            if battery_soc <= config.BATTERY_SOC_LOW_WARNING and not ev_sessions_active:
                self.ctx.transition_to(ISLState.ISL_CHARGERS_SUSPENDED, "SOC ≤ 25%, no sessions")
            # EV session starts (vehicle connected, SOC > 20%)
            elif ev_sessions_active and battery_soc > config.BATTERY_SOC_MIN:
                self.ctx.transition_to(ISLState.ISL_EV_CHARGING, "EV session started")

        elif state == ISLState.ISL_EV_CHARGING:
            # BESS SOC ≤ 25% → begin ramp-down
            if battery_soc <= config.BATTERY_SOC_LOW_WARNING:
                self.ctx.transition_to(ISLState.ISL_SOC_LOW, "SOC ≤ 25%, begin ramp-down")
            # All EV sessions ended
            elif not ev_sessions_active:
                self.ctx.transition_to(ISLState.ISL_STANDBY, "All EV sessions ended")

        elif state == ISLState.ISL_SOC_LOW:
            # SOC ≤ 20% and charger setpoints = 0 → suspended
            if battery_soc <= config.BATTERY_SOC_MIN:
                if self.ctx.prev_infy_w <= 0 and self.ctx.prev_winline_w <= 0:
                    self.ctx.transition_to(ISLState.ISL_CHARGERS_SUSPENDED, "SOC ≤ 20%, chargers off")
            # SOC ≤ 10% → critical (hard limit → D1 FAULT)
            if battery_soc <= config.BATTERY_SOC_CRITICAL_LOW:
                self.ctx.transition_to(ISLState.ISL_CRITICAL_SOC, "SOC ≤ 10%")

        elif state == ISLState.ISL_CHARGERS_SUSPENDED:
            # SOC ≤ 10% → critical
            if battery_soc <= config.BATTERY_SOC_CRITICAL_LOW:
                self.ctx.transition_to(ISLState.ISL_CRITICAL_SOC, "SOC ≤ 10%, grid not restored")

        elif state == ISLState.ISL_CRITICAL_SOC:
            # Stays here - operator reset via D1 FAULT
            pass

        elif state == ISLState.GRID_RESTORE_PENDING:
            # Voltage match confirmed after 5s walk-in → transition handled by D1
            if self.ctx.time_in_state >= config.WALK_IN_TIME_S:
                self.ctx.grid_restore_voltage_matched = True

    def _compute_output(
        self,
        battery_soc: float,
        battery_available_power_w: float,
        battery_voltage: float,
        ev_demand_w: float,
    ) -> ModeOutput:
        state = self.ctx.state

        if state == ISLState.ISL_STANDBY:
            return self._output_isl_standby()
        elif state == ISLState.ISL_EV_CHARGING:
            return self._output_isl_ev_charging(battery_available_power_w, ev_demand_w)
        elif state == ISLState.ISL_SOC_LOW:
            return self._output_isl_soc_low()
        elif state == ISLState.ISL_CHARGERS_SUSPENDED:
            return self._output_isl_chargers_suspended()
        elif state == ISLState.ISL_CRITICAL_SOC:
            return self._output_isl_critical_soc()
        elif state == ISLState.GRID_RESTORE_PENDING:
            return self._output_grid_restore_pending(battery_voltage)
        return self._output_isl_standby()

    # ----- State output functions -----

    def _output_isl_standby(self) -> ModeOutput:
        return ModeOutput(
            converdan_enabled=True,
            converdan_ratio=config.CONVERDAN_RATIO_NOMINAL,
            rectifier_enabled=False,
            rectifier_voltage=0,
            rectifier_current_limit=0,
            infypower_charger_power_w=0,
            winline_charger_power_w=0,
            description="ISL_STANDBY - BESS + Converdan, no EV, REG offline",
        )

    def _output_isl_ev_charging(
        self, battery_available_w: float, ev_demand_w: float
    ) -> ModeOutput:
        # Total setpoints ≤ P_BESS_available × 90%
        max_total = min(
            config.CHARGER_COMBINED_MAX_ISLANDED_W,
            battery_available_w * config.CHARGER_POWER_DERATING,
        )
        # Max 40kW each when both active
        max_per_charger = max_total / 2.0
        infy_w = min(max_per_charger, config.INFYPOWER_CHARGER_POWER_MAX_W)
        winline_w = min(max_per_charger, config.WINLINE_POWER_MAX_W)
        # Ensure total doesn't exceed limit
        if infy_w + winline_w > max_total:
            scale = max_total / (infy_w + winline_w)
            infy_w *= scale
            winline_w *= scale

        return ModeOutput(
            converdan_enabled=True,
            converdan_ratio=config.CONVERDAN_RATIO_NOMINAL,
            rectifier_enabled=False,
            rectifier_voltage=0,
            rectifier_current_limit=0,
            infypower_charger_power_w=infy_w,
            winline_charger_power_w=winline_w,
            description=f"ISL_EV_CHARGING - Infy {infy_w/1000:.0f}kW, Win {winline_w/1000:.0f}kW (BESS sole)",
        )

    def _output_isl_soc_low(self) -> ModeOutput:
        # Ramp down all charger setpoints at 1kW/s
        infy_w = max(0, self.ctx.prev_infy_w - config.CHARGER_RAMP_STEP_W)
        winline_w = max(0, self.ctx.prev_winline_w - config.CHARGER_RAMP_STEP_W)

        return ModeOutput(
            converdan_enabled=True,
            converdan_ratio=config.CONVERDAN_RATIO_NOMINAL,
            rectifier_enabled=False,
            rectifier_voltage=0,
            rectifier_current_limit=0,
            infypower_charger_power_w=infy_w,
            winline_charger_power_w=winline_w,
            description=f"ISL_SOC_LOW - ramping down, Infy {infy_w/1000:.0f}kW, Win {winline_w/1000:.0f}kW",
        )

    def _output_isl_chargers_suspended(self) -> ModeOutput:
        return ModeOutput(
            converdan_enabled=True,
            converdan_ratio=config.CONVERDAN_RATIO_NOMINAL,
            rectifier_enabled=False,
            rectifier_voltage=0,
            rectifier_current_limit=0,
            infypower_charger_power_w=0,
            winline_charger_power_w=0,
            description="ISL_CHARGERS_SUSPENDED - SOC≤20%, all EV stopped, awaiting grid",
        )

    def _output_isl_critical_soc(self) -> ModeOutput:
        # Disable Converdan (passive → K3 open). Bus collapses.
        return ModeOutput(
            converdan_enabled=False,
            converdan_ratio=config.CONVERDAN_RATIO_NOMINAL,
            rectifier_enabled=False,
            rectifier_voltage=0,
            rectifier_current_limit=0,
            infypower_charger_power_w=0,
            winline_charger_power_w=0,
            description="ISL_CRITICAL_SOC - SOC≤10%, Converdan off, bus collapsed",
        )

    def _output_grid_restore_pending(self, battery_voltage: float) -> ModeOutput:
        # REG starts up, sets V = Converdan P1 voltage
        # Converdan stays ENABLED (bus must remain live for REG voltage match)
        reg_voltage = battery_voltage * config.CONVERDAN_RATIO_NOMINAL

        return ModeOutput(
            converdan_enabled=True,
            converdan_ratio=config.CONVERDAN_RATIO_NOMINAL,
            rectifier_enabled=True,
            rectifier_voltage=reg_voltage,
            rectifier_current_limit=0,  # K14 not yet closed; will ramp after match
            infypower_charger_power_w=0,
            winline_charger_power_w=0,
            description=f"GRID_RESTORE_PENDING - REG V={reg_voltage:.0f}V matching bus, K14 pending",
        )
