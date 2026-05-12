"""
D4: Device Sequencing - Step-by-step contactor and device startup/shutdown.

Called by D1 during startup/shutdown and by D2/D3 during mode transitions.

Procedures:
  A - BESS Black Start (preferred startup path)
  B - Grid Black Start (BESS unavailable)
  C - Converdan Disable (SOC limit or shutdown pre-step)
  D - Planned Shutdown
  E - BESS + Converdan Reconnect (called by Proc B when BESS SOC recovers to ≥ 20%)

Each procedure is modelled as a step-based sequence. The orchestrator advances
one step per control cycle by calling advance(). Steps may block on confirmation
(e.g. "poll status and confirm ready") which is simulated by checking device state.

VOLTAGE MATCH CONTACTOR:
  K1 only - no precharge resistor circuit. PLC ensures REG setpoint is within
  5V of DC bus voltage (Acrel) before issuing contactor close command.
"""
from __future__ import annotations

import enum
import logging
import time
from dataclasses import dataclass, field

from microgrid import config

log = logging.getLogger("microgrid.d4_sequencing")


class ProcedureStatus(enum.Enum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


@dataclass
class ProcedureState:
    """Tracks current step and timing for a procedure."""
    step: int = 0
    status: ProcedureStatus = ProcedureStatus.NOT_STARTED
    step_entry_time: float = 0.0
    description: str = ""

    def advance(self, desc: str = "") -> None:
        self.step += 1
        self.step_entry_time = time.time()
        self.description = desc

    def start(self) -> None:
        self.step = 0
        self.status = ProcedureStatus.IN_PROGRESS
        self.step_entry_time = time.time()

    def complete(self) -> None:
        self.status = ProcedureStatus.COMPLETE

    def fail(self, reason: str) -> None:
        self.status = ProcedureStatus.FAILED
        self.description = reason

    @property
    def time_in_step(self) -> float:
        return time.time() - self.step_entry_time


# =============================================================================
# PROCEDURE A - BESS BLACK START (preferred startup path)
# =============================================================================
# Steps:
#   A0: START - PLC alive (UPS / aux supply). Check BESS SOC ≥ 20%.
#       Check all contactors open.
#   A1: BESS_STARTUP - Command BESS internal contactors to close, poll to confirm
#       status. Poll SOC msg - confirm ≥ 20%.
#   A2: CONVERDAN_P2_PRECHARGE - Precharge Port 2 of Converdan via K4.
#   A3: CONVERDAN_STARTUP - Set base address (hardcoded MAC).
#       Send 0x201: ratio=1, mode 5. Send 0x201: B0=enable, B1=0x05.
#       Poll 0x202C; wait P1 voltage stable.
#   A4: CONVERDAN_SET_RATIO - Send 0x203: ratio=1.058, mode 5.
#       Port 1 ramp/o power = 725–757V.
#       Poll 0x202C; wait P1 voltage stable (±5V). Start keepalive.
#   A5: CLOSE_K3 - Close K3 and connect Port 1 of Converdan to DC bus.
#       Confirm stable DC bus voltage via Acrel DC meter.
#   A6: START_INFY_REG - Confirm grid available (AC meter reading:
#       stable voltage 216–253V on 3 phases).
#       Confirm Infy REG online and send V (limit = DC bus voltage, I limit = 0).
#       Start keepalive.
#   A7: CLOSE_K1 - Poll Infy REG and confirm output voltage within 5V of DC bus
#       voltage. Close K1 contactor.
#   A8: ENABLE_CHARGERS - Precharge EV chargers through K11 and K13.
#       Poll status and confirm ready with no alarms.
#   A9: COMPLETE - Bus live. EV chargers online.
#       Grid present? → D1: GRID_CONNECTED
#       Grid absent? → D1: ISLANDED


class ProcedureBatteryBlackStart:
    """Procedure A - BESS Black Start."""

    def __init__(self):
        self.state = ProcedureState()

    def start(self) -> None:
        self.state.start()
        log.info("Proc A: Battery Black Start initiated")

    @property
    def is_complete(self) -> bool:
        return self.state.status == ProcedureStatus.COMPLETE

    @property
    def is_running(self) -> bool:
        return self.state.status == ProcedureStatus.IN_PROGRESS

    def advance(
        self,
        battery_soc: float,
        battery_contactor_closed: bool,
        converdan_port1_voltage: float,
        dc_bus_voltage: float,
        ac_grid_available: bool,
        reg_output_voltage: float,
        chargers_ready: bool,
    ) -> dict:
        """
        Advance one step. Returns a dict of commands to issue this cycle.

        Commands dict keys: 'bess_close_contactor', 'converdan_enable',
        'converdan_ratio', 'close_k3', 'reg_enable', 'reg_voltage',
        'close_k1', 'enable_chargers'
        """
        commands: dict = {}
        step = self.state.step

        if step == 0:
            # A0: Check preconditions
            if battery_soc >= config.BATTERY_SOC_BLACKSTART_MIN:
                self.state.advance("A1: Commanding BESS contactor close")
                commands["battery_close_contactor"] = True
            else:
                self.state.fail(f"BESS SOC {battery_soc:.1f}% < {config.BATTERY_SOC_BLACKSTART_MIN}%")

        elif step == 1:
            # A1: Wait for BESS contactor to close
            if battery_contactor_closed:
                self.state.advance("A2: Precharging Converdan P2")
            elif self.state.time_in_step > 30:
                self.state.fail("BESS contactor did not close within 30s")

        elif step == 2:
            # A2: Converdan precharge (simulated by time delay)
            if self.state.time_in_step >= 5:
                self.state.advance("A3: Starting Converdan")
                commands["converdan_enable"] = True
                commands["converdan_ratio"] = 1

        elif step == 3:
            # A3: Converdan startup - wait for P1 voltage stable
            if converdan_port1_voltage > 0 and self.state.time_in_step >= 5:
                self.state.advance("A4: Setting Converdan ratio")

        elif step == 4:
            # A4: Set ratio, wait for P1 voltage stable (±5V)
            commands["converdan_ratio"] = config.CONVERDAN_RATIO_NOMINAL
            if self.state.time_in_step >= 5:
                self.state.advance("A5: Closing K3")
                commands["close_k3"] = True

        elif step == 5:
            # A5: Confirm DC bus voltage stable via Acrel
            if dc_bus_voltage > config.DC_BUS_VOLTAGE_SUSPEND_THRESHOLD:
                self.state.advance("A6: Starting Infy REG")
            elif self.state.time_in_step > 30:
                self.state.fail("DC bus voltage not established after K3 close")

        elif step == 6:
            # A6: Start Infy REG at DC bus voltage, I-limit = 0
            if ac_grid_available:
                commands["reg_enable"] = True
                commands["reg_voltage"] = dc_bus_voltage
                commands["reg_current"] = 0
                if self.state.time_in_step >= 5:
                    self.state.advance("A7: Closing K1")
            elif self.state.time_in_step > 30:
                # No grid - proceed without REG (islanded start)
                self.state.advance("A8: Enabling chargers (no grid)")

        elif step == 7:
            # A7: Voltage match → close K1
            voltage_diff = abs(reg_output_voltage - dc_bus_voltage)
            if voltage_diff <= 5:
                commands["close_k1"] = True
                self.state.advance("A8: Enabling chargers")
            elif self.state.time_in_step > 15:
                self.state.fail("REG voltage match failed within 15s")

        elif step == 8:
            # A8: Enable EV chargers via K11, K13
            commands["enable_chargers"] = True
            if chargers_ready or self.state.time_in_step >= 10:
                self.state.advance("A9: Complete")
                self.state.complete()
                log.info("Proc A: Battery Black Start COMPLETE")

        return commands


# =============================================================================
# PROCEDURE B - GRID BLACK START (BESS unavailable)
# =============================================================================
class ProcedureGridBlackStart:
    """Procedure B - Grid Black Start (BESS SOC < 20% or BESS fault)."""

    def __init__(self):
        self.state = ProcedureState()

    def start(self) -> None:
        self.state.start()
        log.info("Proc B: Grid Black Start initiated")

    @property
    def is_complete(self) -> bool:
        return self.state.status == ProcedureStatus.COMPLETE

    @property
    def is_running(self) -> bool:
        return self.state.status == ProcedureStatus.IN_PROGRESS

    def advance(
        self,
        ac_grid_available: bool,
        dc_bus_voltage: float,
        reg_output_voltage: float,
        battery_available: bool,
        battery_soc: float,
        chargers_ready: bool,
    ) -> dict:
        """Advance one step. Returns commands dict."""
        commands: dict = {}
        step = self.state.step

        if step == 0:
            # B0: Check grid/AC confirmed present
            if ac_grid_available:
                self.state.advance("B1: Starting Infy REG at grid-only voltage")
                commands["reg_enable"] = True
                commands["reg_voltage"] = 750.0  # target DC bus voltage
                commands["reg_current"] = 0
            elif self.state.time_in_step > 30:
                self.state.fail("Grid not available for grid blackstart")

        elif step == 1:
            # B1: Confirm REG online, output voltage at 750VDC. I limit = 0.
            # Start keepalive.
            if reg_output_voltage > 700 and self.state.time_in_step >= 5:
                self.state.advance("B2: Closing K1")
                commands["close_k1"] = True

        elif step == 2:
            # B2: Close K1 contactor
            if dc_bus_voltage > config.DC_BUS_VOLTAGE_SUSPEND_THRESHOLD:
                self.state.advance("B3: Bus live, grid only")
            elif self.state.time_in_step > 15:
                self.state.fail("DC bus voltage not established via REG")

        elif step == 3:
            # B3: BUS_LIVE_GRID_ONLY - BESS/Converdan not yet connected.
            # Keepalive continues at B1=0x03, B1=0x05.
            # REG I-limit = 100A (sole supply). Start keepalive.
            commands["reg_current"] = config.RECTIFIER_CURRENT_MAX
            if self.state.time_in_step >= 5:
                self.state.advance("B4: Enabling chargers")
                commands["enable_chargers"] = True

        elif step == 4:
            # B4: Enable EV chargers through K11 and K13.
            # Poll status and confirm ready with no alarms.
            if chargers_ready or self.state.time_in_step >= 10:
                self.state.advance("B5: Awaiting battery recovery")

        elif step == 5:
            # B5: Monitor BESS status via Modbus. Wait for SOC >= 20%.
            # Execute Procedure E (Converdan reconnect).
            # Until then: D2 GRID_SOLE_SUPPLY.
            if battery_available and battery_soc >= config.BATTERY_SOC_BLACKSTART_MIN:
                self.state.advance("B6: Battery recovered, initiating Proc E")
            # This step remains until BESS recovers or stays as grid-only

        elif step == 6:
            # B6: Complete - transitions to D2 GRID_SOLE_SUPPLY / BESS_RECHARGING
            self.state.complete()
            log.info("Proc B: Grid Black Start COMPLETE")

        return commands


# =============================================================================
# PROCEDURE C - CONVERDAN DISABLE (SOC limit or shutdown pre-step)
# =============================================================================
class ProcedureConverdanDisable:
    """Procedure C - Disable Converdan (SOC limit or shutdown pre-step)."""

    def __init__(self):
        self.state = ProcedureState()

    def start(self) -> None:
        self.state.start()
        log.info("Proc C: Converdan Disable initiated")

    @property
    def is_complete(self) -> bool:
        return self.state.status == ProcedureStatus.COMPLETE

    @property
    def is_running(self) -> bool:
        return self.state.status == ProcedureStatus.IN_PROGRESS

    def advance(
        self,
        battery_soc: float,
        ev_sessions_active: bool,
        prev_infy_w: float,
        prev_winline_w: float,
        charger_output_confirmed_zero: bool,
    ) -> dict:
        """Advance one step. Returns commands dict."""
        commands: dict = {}
        step = self.state.step

        if step == 0:
            # C0: TRIGGER - BESS SOC ≤ 20% (with EV sessions active) OR
            # SOC ≤ 15% + ≤ 85% (hard protection) OR planned shutdown.
            self.state.advance("C1: Ramping down chargers")

        elif step == 1:
            # C1: RAMP_DOWN_CHARGERS - Set all EV charger power setpoints → 0kW.
            # Poll charger output power until confirmed = 0.
            commands["infy_charger_w"] = max(0, prev_infy_w - config.CHARGER_RAMP_STEP_W)
            commands["winline_charger_w"] = max(0, prev_winline_w - config.CHARGER_RAMP_STEP_W)
            if commands["infy_charger_w"] <= 0 and commands["winline_charger_w"] <= 0:
                self.state.advance("C2: Raising REG I-limit")

        elif step == 2:
            # C2: RAISE_REG_LIMIT - Set REG I-limit → 100A (CAN cmd 0x1C).
            # Walk-in = 5s (factory default - confirm active).
            commands["reg_current"] = config.RECTIFIER_CURRENT_MAX
            if self.state.time_in_step >= config.WALK_IN_TIME_S:
                self.state.advance("C3: Converdan passive")

        elif step == 3:
            # C3: CONVERDAN_PASSIVE - CAN 0x207: B0=0x01, B1=0x05.
            # Walk-in = 5s (factory default - confirm active).
            # Poll 0x202C until P1 current = 0A.
            # Converdan now in passive state.
            commands["converdan_disable"] = True
            if self.state.time_in_step >= config.WALK_IN_TIME_S:
                self.state.advance("C4: Opening K3")

        elif step == 4:
            # C4: OPEN_K3 - Issue K3 open command.
            # K3 opens - Converdan Port 1 isolated from DC bus.
            # Bus now held solely by REG.
            commands["open_k3"] = True
            if self.state.time_in_step >= 2:
                self.state.advance("C5: Complete")
                self.state.complete()
                log.info("Proc C: Converdan Disable COMPLETE")

        return commands


# =============================================================================
# PROCEDURE D - PLANNED SHUTDOWN
# =============================================================================
class ProcedurePlannedShutdown:
    """Procedure D - Graceful ramp-down and shutdown."""

    def __init__(self):
        self.state = ProcedureState()

    def start(self) -> None:
        self.state.start()
        log.info("Proc D: Planned Shutdown initiated")

    @property
    def is_complete(self) -> bool:
        return self.state.status == ProcedureStatus.COMPLETE

    @property
    def is_running(self) -> bool:
        return self.state.status == ProcedureStatus.IN_PROGRESS

    def advance(
        self,
        prev_infy_w: float,
        prev_winline_w: float,
        charger_output_zero: bool,
        reg_output_zero: bool,
    ) -> dict:
        """Advance one step. Returns commands dict."""
        commands: dict = {}
        step = self.state.step

        if step == 0:
            # D0: TRIGGER - Operator shutdown command OR fault-induced graceful shutdown.
            self.state.advance("D1: Stopping charger sessions")

        elif step == 1:
            # D1: STOP_CHARGER_SESSIONS - Set all EV power setpoints = 0kW/h via DEP.
            # Stop all active sessions.
            commands["infy_charger_w"] = 0
            commands["winline_charger_w"] = 0
            commands["stop_sessions"] = True
            if charger_output_zero or self.state.time_in_step >= 10:
                self.state.advance("D2: Isolating chargers")

        elif step == 2:
            # D2: ISOLATE_CHARGERS - Open K11 (Infy EV charger via DEP).
            # Open K13 (Winline EV charger via DEP).
            # Chargers now isolated from DC bus.
            commands["open_k11"] = True
            commands["open_k13"] = True
            if self.state.time_in_step >= 2:
                self.state.advance("D3: Disabling REG")

        elif step == 3:
            # D3: DISABLE_REG - Ramp REG I-limit → 0A (CAN 0x1C).
            # Send REG OFF (CAN 0x1A).
            # Poll CAN telemetry until REG output = 0.
            commands["reg_current"] = 0
            commands["reg_disable"] = True
            if reg_output_zero or self.state.time_in_step >= 10:
                self.state.advance("D4: Opening K1")

        elif step == 4:
            # D4: OPEN_K1 - Issue K1 open command.
            # REG fully isolated from DC bus.
            commands["open_k1"] = True
            if self.state.time_in_step >= 2:
                self.state.advance("D5: Converdan passive")

        elif step == 5:
            # D5: CONVERDAN_PASSIVE - CAN 0x207: B0=0x01, B1=0x02.
            # Poll 0x202C; wait P1 current = 2A.
            # Stop Converdan keepalive.
            commands["converdan_disable"] = True
            if self.state.time_in_step >= 5:
                self.state.advance("D6: Opening K3")

        elif step == 6:
            # D6: OPEN_K3 - Open K3. Converdan Port 1 isolated from bus.
            # Bus voltage now decaying on capacitance.
            commands["open_k3"] = True
            if self.state.time_in_step >= 2:
                self.state.advance("D7: Opening K4")

        elif step == 7:
            # D7: OPEN_K4 - Open K4. BESS Port 2 side now isolated.
            # BESS internal contactors remain under BMS control.
            commands["open_k4"] = True
            commands["battery_open_contactor"] = True
            if self.state.time_in_step >= 2:
                self.state.advance("D8: Complete")
                self.state.complete()
                log.info("Proc D: Planned Shutdown COMPLETE")

        return commands


# =============================================================================
# PROCEDURE E - BESS + CONVERDAN RECONNECT
# =============================================================================
class ProcedureConverdanReconnect:
    """
    Procedure E - Reconnect BESS + Converdan.
    Called by Proc B when BESS SOC recovers to ≥ 20%.
    """

    def __init__(self):
        self.state = ProcedureState()

    def start(self) -> None:
        self.state.start()
        log.info("Proc E: Converdan Reconnect initiated")

    @property
    def is_complete(self) -> bool:
        return self.state.status == ProcedureStatus.COMPLETE

    @property
    def is_running(self) -> bool:
        return self.state.status == ProcedureStatus.IN_PROGRESS

    def advance(
        self,
        battery_contactor_closed: bool,
        converdan_port1_voltage: float,
        dc_bus_voltage: float,
        reg_output_voltage: float,
    ) -> dict:
        """Advance one step. Returns commands dict."""
        commands: dict = {}
        step = self.state.step

        if step == 0:
            # E0: TRIGGER - Bus live via REG (grid black start done).
            self.state.advance("E1: Battery startup")
            commands["battery_close_contactor"] = True

        elif step == 1:
            # E1: Battery startup - command battery internal contactors to close,
            # poll to confirm status. Poll SOC msg - confirm ≥ 20%.
            if battery_contactor_closed:
                self.state.advance("E2: Precharging Converdan P2")
            elif self.state.time_in_step > 30:
                self.state.fail("Battery contactor did not close within 30s")

        elif step == 2:
            # E2: CONVERDAN_P2_PRECHARGE - Precharge Port 2 of Converdan via K4.
            if self.state.time_in_step >= 5:
                self.state.advance("E3: Starting Converdan")
                commands["converdan_enable"] = True
                commands["converdan_ratio"] = config.CONVERDAN_RATIO_NOMINAL

        elif step == 3:
            # E3: CONVERDAN_STARTUP - Set base address (hardcoded MAC).
            # Send 0x201: B0=enable, B1=0x05. Send 0x201: ratio=1.058, mode 5.
            # Port 1 ramp/o power = 725–757V.
            # Poll 0x202C; wait P1 voltage stable (±5V). Start keepalive.
            if converdan_port1_voltage > 0 and self.state.time_in_step >= 5:
                self.state.advance("E4: Setting ratio")

        elif step == 4:
            # E4: CONVERDAN_SET_RATIO - Set ratio 1.058, mode 5.
            commands["converdan_ratio"] = config.CONVERDAN_RATIO_NOMINAL
            if self.state.time_in_step >= 5:
                self.state.advance("E5: Matching REG voltage")

        elif step == 5:
            # E5: INFY_REG_MATCH - Set REG V to match P1 voltage.
            # Poll Acrel DC meter to confirm.
            commands["reg_voltage"] = converdan_port1_voltage
            voltage_diff = abs(reg_output_voltage - converdan_port1_voltage) if converdan_port1_voltage > 0 else 999
            if voltage_diff <= 5 and self.state.time_in_step >= 5:
                self.state.advance("E6: Closing K3")
                commands["close_k3"] = True

        elif step == 6:
            # E6: CLOSE_K3 - Close K3 and connect Port 1 of Converdan to DC bus.
            # Confirm stable voltage via Acrel DC meter.
            if dc_bus_voltage > config.DC_BUS_VOLTAGE_SUSPEND_THRESHOLD:
                self.state.advance("E7: Reducing REG I-limit")
            elif self.state.time_in_step > 15:
                self.state.fail("DC bus not stable after K3 close")

        elif step == 7:
            # E7: REDUCE_REG_LIMIT - Ramp REG I-limit → 0A.
            # REG remains as voltage follower / backup.
            commands["reg_current"] = 0
            if self.state.time_in_step >= 5:
                self.state.advance("E8: Complete")
                self.state.complete()
                log.info("Proc E: Converdan Reconnect COMPLETE → D2 BESS_SOLE_SUPPLY")

        return commands
