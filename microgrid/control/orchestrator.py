"""
System orchestrator - the top-level control loop that:

1. Reads all device measurements into a SystemState snapshot.
2. Evaluates software protection flags.
3. Computes battery limits.
4. Runs the hierarchical FSM: D1 (system mode) → D2/D3 (sub-modes) → D4 (sequencing).
5. Applies protection overrides.
6. Sends commands to hardware.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from microgrid import config
from microgrid.devices.battery import Battery
from microgrid.devices.converdan import Converdan
from microgrid.devices.infypower_rectifier import InfypowerRectifier
from microgrid.devices.winline_charger import WinlineCharger
from microgrid.devices.infypower_charger import InfypowerCharger
from microgrid.devices.ac_meter import ACMeter
from microgrid.devices.dc_meter import DCMeter
from microgrid.devices.contactor import Contactor
from microgrid.control.battery_limits import BatteryLimits, compute_battery_limits
from microgrid.control.protection import ProtectionFlags, ProtectionManager
from microgrid.modes.base_mode import ModeOutput
from microgrid.modes.system_mode import SystemMode, SystemModeFSM
from microgrid.modes.grid_connected import GridConnectedFSM
from microgrid.modes.islanded import IslandedFSM
from microgrid.modes.device_sequencing import (
    ProcedureBatteryBlackStart,
    ProcedureGridBlackStart,
    ProcedureConverdanDisable,
    ProcedurePlannedShutdown,
    ProcedureConverdanReconnect,
)

log = logging.getLogger("microgrid.orchestrator")


# =============================================================================
# SystemState - immutable snapshot of the current control cycle
# =============================================================================

@dataclass
class SystemState:
    """All inputs the FSMs need to make decisions."""
    # Device availability
    battery_available: bool = False
    ac_grid_available: bool = True

    # Battery
    battery_soc: float = 0.0
    battery_voltage: float = 0.0
    battery_power: float = 0.0
    battery_contactor_closed: bool = False
    battery_limits: BatteryLimits = field(default_factory=lambda: BatteryLimits(0, 0, False, False))

    # DC bus
    dc_bus_voltage: float = 0.0

    # Converdan
    converdan_port1_voltage: float = 0.0

    # REG
    reg_output_voltage: float = 0.0

    # Charger states
    infypower_charger_charging: bool = False
    winline_charger_charging: bool = False
    ev_contactors_closed: bool = False

    # Previous-cycle setpoints (for ramping)
    prev_infypower_charger_w: float = 0.0
    prev_winline_charger_w: float = 0.0
    prev_rectifier_current: float = 0.0

    # Protection
    protection: ProtectionFlags = field(default_factory=ProtectionFlags)

    # Derived convenience
    @property
    def battery_soc_in_range(self) -> bool:
        return config.BATTERY_SOC_MIN <= self.battery_soc <= config.BATTERY_SOC_MAX

    @property
    def ev_sessions_active(self) -> bool:
        return self.infypower_charger_charging or self.winline_charger_charging

    @property
    def battery_available_power_w(self) -> float:
        return self.battery_limits.max_discharge_power_w

    @property
    def ev_demand_w(self) -> float:
        """Approximate current EV demand from previous setpoints."""
        return self.prev_infypower_charger_w + self.prev_winline_charger_w

    @property
    def bus_live(self) -> bool:
        return self.dc_bus_voltage >= config.DC_BUS_VOLTAGE_SUSPEND_THRESHOLD


# =============================================================================
# Orchestrator
# =============================================================================

class Orchestrator:
    """Main control loop logic - call ``tick()`` once per control cycle."""

    def __init__(
        self,
        battery: Battery,
        converdan: Converdan,
        rectifier: InfypowerRectifier,
        winline: WinlineCharger,
        infypower_charger: InfypowerCharger,
        ac_meter: ACMeter,
        dc_meter: DCMeter,
        k1: Contactor,
        k3: Contactor,
        k4: Contactor,
        k11: Contactor,
        k13: Contactor,
    ):
        self.battery = battery
        self.converdan = converdan
        self.rectifier = rectifier
        self.winline = winline
        self.infypower_charger = infypower_charger
        self.ac_meter = ac_meter
        self.dc_meter = dc_meter
        self.k1 = k1
        self.k3 = k3
        self.k4 = k4
        self.k11 = k11
        self.k13 = k13

        # Protection manager
        self.protection = ProtectionManager()

        # Hierarchical FSMs
        self.d1 = SystemModeFSM()
        self.d2 = GridConnectedFSM()
        self.d3 = IslandedFSM()

        # D4 Procedures
        self.proc_a = ProcedureBatteryBlackStart()
        self.proc_b = ProcedureGridBlackStart()
        self.proc_c = ProcedureConverdanDisable()
        self.proc_d = ProcedurePlannedShutdown()
        self.proc_e = ProcedureConverdanReconnect()

        # Persistent state across ticks
        self._prev_infypower_w: float = 0.0
        self._prev_winline_w: float = 0.0
        self._prev_rectifier_current: float = 0.0

    # --------------------------------------------------------------------- #
    # Public API
    # --------------------------------------------------------------------- #

    def tick(self) -> str:
        """Execute one control cycle. Returns a human-readable status string."""
        state = self._read_state()

        # --- D1: Evaluate top-level system mode ---
        self.d1.evaluate(
            battery_available=state.battery_available,
            battery_soc=state.battery_soc,
            ac_grid_available=state.ac_grid_available,
            bus_live=state.bus_live,
            ev_contactors_closed=state.ev_contactors_closed,
            all_devices_idle=self._all_devices_idle(state),
            comms_loss=state.protection.communication_loss,
            equipment_fault=state.protection.equipment_fault,
            battery_soc_critical=state.protection.battery_soc_critical_low,
        )

        # --- Dispatch to sub-FSM based on D1 mode ---
        output = self._dispatch_sub_fsm(state)

        # --- Apply protection overrides ---
        output = self._apply_protection_overrides(output, state)

        # --- Send commands to hardware ---
        self._apply_output(output)

        # --- Store setpoints for ramping ---
        self._prev_infypower_w = output.infypower_charger_power_w
        self._prev_winline_w = output.winline_charger_power_w
        self._prev_rectifier_current = output.rectifier_current_limit

        d1_mode = self.d1.mode.value
        sub_state = self._get_sub_state_name()
        summary = (
            f"[D1:{d1_mode}|{sub_state}] {output.description} | "
            f"SOC={state.battery_soc:.1f}% BusV={state.dc_bus_voltage:.0f}V "
            f"Infy={output.infypower_charger_power_w/1000:.0f}kW "
            f"Win={output.winline_charger_power_w/1000:.0f}kW "
            f"REG_I={output.rectifier_current_limit:.0f}A"
        )
        return summary

    @property
    def active_mode(self) -> str:
        return f"{self.d1.mode.value}|{self._get_sub_state_name()}"

    def request_shutdown(self) -> None:
        """External interface for operator shutdown."""
        self.d1.request_shutdown()

    def request_fault_reset(self) -> None:
        """External interface for operator fault reset."""
        self.d1.request_fault_reset()

    # --------------------------------------------------------------------- #
    # Internal - Sub-FSM dispatch
    # --------------------------------------------------------------------- #

    def _dispatch_sub_fsm(self, state: SystemState) -> ModeOutput:
        """Run the appropriate sub-FSM based on current D1 mode."""
        mode = self.d1.mode

        if mode == SystemMode.POWERED_OFF:
            return self._output_powered_off()

        elif mode == SystemMode.BATTERY_BLACKSTART:
            return self._run_battery_blackstart(state)

        elif mode == SystemMode.GRID_BLACKSTART:
            return self._run_grid_blackstart(state)

        elif mode == SystemMode.GRID_CONNECTED:
            return self._run_grid_connected(state)

        elif mode == SystemMode.ISLANDED:
            return self._run_islanded(state)

        elif mode == SystemMode.FAULT:
            return self._output_fault()

        elif mode == SystemMode.PLANNED_SHUTDOWN:
            return self._run_planned_shutdown(state)

        return self._output_safe()

    def _run_battery_blackstart(self, state: SystemState) -> ModeOutput:
        """D4 Procedure A - BESS Black Start."""
        if not self.proc_a.is_running:
            self.proc_a.start()

        commands = self.proc_a.advance(
            battery_soc=state.battery_soc,
            battery_contactor_closed=state.battery_contactor_closed,
            converdan_port1_voltage=state.converdan_port1_voltage,
            dc_bus_voltage=state.dc_bus_voltage,
            ac_grid_available=state.ac_grid_available,
            reg_output_voltage=state.reg_output_voltage,
            chargers_ready=True,
        )
        self._execute_procedure_commands(commands)

        return ModeOutput(
            converdan_enabled=commands.get("converdan_enable", False),
            converdan_ratio=commands.get("converdan_ratio", config.CONVERDAN_RATIO_NOMINAL),
            rectifier_enabled=commands.get("reg_enable", False),
            rectifier_voltage=commands.get("reg_voltage", 0),
            rectifier_current_limit=commands.get("reg_current", 0),
            infypower_charger_power_w=0,
            winline_charger_power_w=0,
            infypower_charger_status="idle",
            winline_charger_status="idle",
            total_demand_w=0,
            description=f"BATTERY_BLACKSTART - {self.proc_a.state.description}",
        )

    def _run_grid_blackstart(self, state: SystemState) -> ModeOutput:
        """D4 Procedure B - Grid Black Start."""
        if not self.proc_b.is_running:
            self.proc_b.start()

        commands = self.proc_b.advance(
            ac_grid_available=state.ac_grid_available,
            dc_bus_voltage=state.dc_bus_voltage,
            reg_output_voltage=state.reg_output_voltage,
            battery_available=state.battery_available,
            battery_soc=state.battery_soc,
            chargers_ready=True,
        )
        self._execute_procedure_commands(commands)

        return ModeOutput(
            converdan_enabled=commands.get("converdan_enable", False),
            converdan_ratio=commands.get("converdan_ratio", config.CONVERDAN_RATIO_NOMINAL),
            rectifier_enabled=commands.get("reg_enable", False),
            rectifier_voltage=commands.get("reg_voltage", 750.0),
            rectifier_current_limit=commands.get("reg_current", 0),
            infypower_charger_power_w=0,
            winline_charger_power_w=0,
            infypower_charger_status="idle",
            winline_charger_status="idle",
            total_demand_w=0,
            description=f"GRID_BLACKSTART - {self.proc_b.state.description}",
        )

    def _run_grid_connected(self, state: SystemState) -> ModeOutput:
        """D2 sub-FSM - Grid Connected energy management."""
        return self.d2.evaluate(
            battery_soc=state.battery_soc,
            battery_available=state.battery_available,
            battery_voltage=state.battery_voltage,
            dc_bus_voltage=state.dc_bus_voltage,
            ev_sessions_active=state.ev_sessions_active,
            ev_demand_w=state.ev_demand_w,
            battery_available_power_w=state.battery_available_power_w,
            prev_reg_current=self._prev_rectifier_current,
            prev_infy_w=self._prev_infypower_w,
            prev_winline_w=self._prev_winline_w,
        )

    def _run_islanded(self, state: SystemState) -> ModeOutput:
        """D3 sub-FSM - Islanded mode."""
        output = self.d3.evaluate(
            battery_soc=state.battery_soc,
            battery_available_power_w=state.battery_available_power_w,
            battery_voltage=state.battery_voltage,
            dc_bus_voltage=state.dc_bus_voltage,
            ev_sessions_active=state.ev_sessions_active,
            ev_demand_w=state.ev_demand_w,
            ac_grid_available=state.ac_grid_available,
            prev_infy_w=self._prev_infypower_w,
            prev_winline_w=self._prev_winline_w,
        )

        # Check if D3 signals grid restore complete → D1 handles transition
        if self.d3.grid_restore_complete:
            self.d1.ctx.transition_to(SystemMode.GRID_CONNECTED, "D3 grid restore complete")
            self.d2.reset()

        return output

    def _run_planned_shutdown(self, state: SystemState) -> ModeOutput:
        """D4 Procedure D - Planned Shutdown."""
        if not self.proc_d.is_running:
            self.proc_d.start()

        commands = self.proc_d.advance(
            charger_output_zero=(
                self.infypower_charger.total_power <= 0
                and self.winline.total_power <= 0
            ),
            reg_output_zero=self.rectifier.port2_current <= 0.5,
            converdan_port1_current=self.converdan.port1_current,
            k1_open=self.k1.is_open,
            k3_open=self.k3.is_open,
            k4_open=self.k4.is_open,
            k11_open=self.k11.is_open,
            k13_open=self.k13.is_open,
        )
        self._execute_procedure_commands(commands)

        return ModeOutput(
            converdan_enabled=not commands.get("converdan_disable", False),
            converdan_ratio=config.CONVERDAN_RATIO_NOMINAL,
            rectifier_enabled=not commands.get("reg_disable", False),
            rectifier_voltage=state.dc_bus_voltage,
            rectifier_current_limit=commands.get("reg_current", 0),
            infypower_charger_power_w=commands.get("infy_charger_w", 0),
            winline_charger_power_w=commands.get("winline_charger_w", 0),
            infypower_charger_status="idle",
            winline_charger_status="idle",
            total_demand_w=0,
            description=f"PLANNED_SHUTDOWN - {self.proc_d.state.description}",
        )

    # --------------------------------------------------------------------- #
    # Internal - Static outputs
    # --------------------------------------------------------------------- #

    def _output_powered_off(self) -> ModeOutput:
        return ModeOutput(
            converdan_enabled=False,
            rectifier_enabled=False,
            infypower_charger_power_w=0,
            winline_charger_power_w=0,
            infypower_charger_status="idle",
            winline_charger_status="idle",
            total_demand_w=0,
            description="POWERED_OFF - all devices idle, PLC on UPS",
        )

    def _output_fault(self) -> ModeOutput:
        reasons = ", ".join(self.d1.ctx.fault_reasons) if self.d1.ctx.fault_reasons else "unknown"
        return ModeOutput(
            converdan_enabled=False,
            rectifier_enabled=False,
            infypower_charger_power_w=0,
            winline_charger_power_w=0,
            infypower_charger_status="idle",
            winline_charger_status="idle",
            total_demand_w=0,
            description=f"FAULT - {reasons}. Operator reset required.",
        )

    def _output_safe(self) -> ModeOutput:
        return ModeOutput(
            converdan_enabled=False,
            rectifier_enabled=False,
            infypower_charger_power_w=0,
            winline_charger_power_w=0,
            infypower_charger_status="idle",
            winline_charger_status="idle",
            total_demand_w=0,
            description="Safe mode - all devices disabled",
        )

    # --------------------------------------------------------------------- #
    # Internal - State reading
    # --------------------------------------------------------------------- #

    def _read_state(self) -> SystemState:
        """Build a SystemState snapshot from live device readings."""
        soc = self.battery.soc
        voltage = self.battery.voltage

        bat_limits = compute_battery_limits(
            soc=soc,
            voltage=voltage,
            battery_charge_max_w=self.battery.charge_power_max,
            battery_discharge_max_w=self.battery.discharge_power_max,
        )

        # DC bus voltage
        dc_bus_v = self.dc_meter.voltage

        # Converdan port1 voltage
        converdan_p1_v = self.converdan.port1_voltage

        # REG output voltage
        reg_out_v = self.rectifier.port2_voltage

        # AC grid availability - determined by Phoenix Contact AC meter (all three phases 216–253 V)
        ac_grid_available = self.ac_meter.ac_available

        prot_flags = self.protection.evaluate(
            dc_bus_voltage=dc_bus_v,
            battery_soc=soc,
            ac_current=self.ac_meter.current,
            cabinet_temp=self.battery.temperature,
            battery_available=self.battery.is_available,
            converdan_available=self.converdan.is_available,
            converdan_has_fault=self.converdan.has_active_faults(),
        )

        return SystemState(
            battery_available=self.battery.is_available,
            ac_grid_available=ac_grid_available,
            battery_soc=soc,
            battery_voltage=voltage,
            battery_power=self.battery.power,
            battery_contactor_closed=self.battery.contactor_closed,
            battery_limits=bat_limits,
            dc_bus_voltage=dc_bus_v,
            converdan_port1_voltage=converdan_p1_v,
            reg_output_voltage=reg_out_v,
            infypower_charger_charging=self.infypower_charger.is_charging,
            winline_charger_charging=self.winline.is_charging,
            ev_contactors_closed=self.k11.is_closed and self.k13.is_closed,
            prev_infypower_charger_w=self._prev_infypower_w,
            prev_winline_charger_w=self._prev_winline_w,
            prev_rectifier_current=self._prev_rectifier_current,
            protection=prot_flags,
        )

    # --------------------------------------------------------------------- #
    # Internal - Protection overrides
    # --------------------------------------------------------------------- #

    def _apply_protection_overrides(self, output: ModeOutput, state: SystemState) -> ModeOutput:
        """Enforce hard safety limits regardless of what the mode decided."""
        prot = state.protection

        # --- Bus overvoltage: stop REG, disable Converdan --------------------
        if prot.bus_overvoltage:
            output.rectifier_enabled = False
            output.rectifier_current_limit = 0
            output.converdan_enabled = False
            output.description += " [OV override]"

        # --- Charging suspended (bus undervoltage with hysteresis) ------------
        if prot.charging_suspended:
            output.infypower_charger_power_w = 0
            output.winline_charger_power_w = 0
            output.infypower_charger_status = "idle"
            output.winline_charger_status = "idle"
            output.description += " [charging suspended]"

        # --- BESS critical SOC - curtail everything --------------------------
        if prot.battery_soc_critical_low or prot.battery_soc_critical_high:
            output.converdan_enabled = False
            output.infypower_charger_power_w = 0
            output.winline_charger_power_w = 0
            output.infypower_charger_status = "idle"
            output.winline_charger_status = "idle"
            output.description += " [BESS SOC critical]"

        # --- AC overcurrent - limit REG and charger power --------------------
        if prot.ac_overcurrent:
            output.rectifier_current_limit = min(
                output.rectifier_current_limit,
                config.AC_OVERCURRENT_THRESHOLD,
            )
            total = output.infypower_charger_power_w + output.winline_charger_power_w
            if total > config.AC_OVERCURRENT_POWER_LIMIT and total > 0:
                scale = config.AC_OVERCURRENT_POWER_LIMIT / total
                output.infypower_charger_power_w *= scale
                output.winline_charger_power_w *= scale
            output.description += " [AC overcurrent curtail]"

        # --- Equipment fault - disable Converdan -----------------------------
        if prot.equipment_fault:
            output.converdan_enabled = False
            output.description += " [Converdan fault]"

        return output

    # --------------------------------------------------------------------- #
    # Internal - Hardware output
    # --------------------------------------------------------------------- #

    def _execute_procedure_commands(self, commands: dict) -> None:
        """
        Execute one-shot side-effect calls from a D4 procedure step.
        Only handles actions that cannot be expressed as continuous setpoints:
        contactor open/close, charger session stop, charger isolation.

        Continuous device setpoints (REG voltage/current, Converdan ratio) are
        carried in the ModeOutput returned by the procedure runner and applied
        by _apply_output in the normal tick path.
        """
        if commands.get("battery_close_contactor"):
            log.info("Proc: closing battery contactor")
            self.battery.close_contactor()

        if commands.get("battery_open_contactor") or commands.get("open_k4"):
            log.info("Proc: opening battery contactor")
            self.battery.open_contactor()

        if commands.get("close_k3"):
            log.info("Proc: closing K3")
            self.converdan.write({"control.contactor.k3": "close"})

        if commands.get("open_k3"):
            log.info("Proc: opening K3")
            self.converdan.write({"control.contactor.k3": "open"})

        if commands.get("close_k1"):
            log.info("Proc: closing K1")
            self.rectifier.write({"control.contactor.k1": "close"})

        if commands.get("open_k1"):
            log.info("Proc: opening K1")
            self.rectifier.write({"control.contactor.k1": "open"})

        if commands.get("open_k11"):
            log.info("Proc: stopping Infypower charger / opening K11")
            self.infypower_charger.disable()

        if commands.get("open_k13"):
            log.info("Proc: stopping Winline charger / opening K13")
            self.winline.disable()

    def _apply_output(self, output: ModeOutput) -> None:
        """
        Send continuous setpoints to hardware every tick.
        Called for all D1 modes including procedure modes - the ModeOutput
        produced by procedure runners already reflects the correct state
        (e.g. converdan_enabled=False when Converdan is being disabled).
        One-shot contactor/session actions are handled before this via
        _execute_procedure_commands().
        """
        # -- Converdan --------------------------------------------------------
        if output.converdan_enabled:
            self.converdan.enable(output.converdan_ratio)
        else:
            self.converdan.disable()

        # -- Infypower REG rectifier ------------------------------------------
        if output.rectifier_enabled:
            self.rectifier.enable(output.rectifier_voltage, output.rectifier_current_limit)
        else:
            self.rectifier.disable()

        # -- Winline EV charger -----------------------------------------------
        if output.winline_charger_status == "Charging":
            self.winline.set_total_power(output.winline_charger_power_w)
        else:
            self.winline.disable()

        # -- Infypower EV charger ---------------------------------------------
        if output.infypower_charger_status == "Charging":
            self.infypower_charger.set_power(output.infypower_charger_power_w)
        else:
            self.infypower_charger.disable()

    # --------------------------------------------------------------------- #
    # Internal - Helpers
    # --------------------------------------------------------------------- #

    def _all_devices_idle(self, state: SystemState) -> bool:
        """Check if all devices are idle (for shutdown completion)."""
        return (
            not state.ev_sessions_active
            and state.prev_rectifier_current <= 0
            and self.proc_d.is_complete
        )


    def _get_sub_state_name(self) -> str:
        """Get the current sub-state name for logging."""
        mode = self.d1.mode
        if mode == SystemMode.GRID_CONNECTED:
            return self.d2.state.value
        elif mode == SystemMode.ISLANDED:
            return self.d3.state.value
        elif mode == SystemMode.BATTERY_BLACKSTART:
            return f"ProcA_step{self.proc_a.state.step}"
        elif mode == SystemMode.GRID_BLACKSTART:
            return f"ProcB_step{self.proc_b.state.step}"
        elif mode == SystemMode.PLANNED_SHUTDOWN:
            return f"ProcD_step{self.proc_d.state.step}"
        return mode.value
