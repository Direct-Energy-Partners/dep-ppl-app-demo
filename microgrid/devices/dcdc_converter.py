"""
DC-DC converter group - represents two Converdan units operating in parallel.

All commands are broadcast to both units. Aggregate measurements (voltages,
currents, power) are averaged/summed as appropriate. Fault and availability
checks require both units to be healthy for the group to be considered healthy.
"""
from __future__ import annotations

from microgrid.devices.converdan import Converdan
from microgrid import config


class DCDCConverter:
    """Parallel group of two Converdan DC-DC converters."""

    def __init__(self, app):
        self.unit_1 = Converdan(app, config.CONVERTER_ID_1)
        self.unit_2 = Converdan(app, config.CONVERTER_ID_2)

    # -- availability / fault -------------------------------------------------

    @property
    def is_available(self) -> bool:
        """True only when both units are available (no communication fault)."""
        return self.unit_1.is_available and self.unit_2.is_available

    def has_active_faults(self) -> bool:
        """True if either unit has an active fault."""
        return self.unit_1.has_active_faults() or self.unit_2.has_active_faults()

    @property
    def status(self) -> tuple[str | None, str | None]:
        """Raw state strings from both units as (unit_1_status, unit_2_status)."""
        return self.unit_1.status, self.unit_2.status

    # -- measurements (port 1 - battery side) ---------------------------------

    @property
    def port1_voltage(self) -> float:
        """Average port-1 voltage across both units."""
        return (self.unit_1.port1_voltage + self.unit_2.port1_voltage) / 2

    @property
    def port1_current(self) -> float:
        """Total port-1 current (sum of both units)."""
        return self.unit_1.port1_current + self.unit_2.port1_current

    @property
    def port1_power(self) -> float:
        """Total port-1 power (sum of both units)."""
        return self.unit_1.port1_power + self.unit_2.port1_power

    # -- measurements (port 2 - DC bus side) ----------------------------------

    @property
    def port2_voltage(self) -> float:
        """Average port-2 voltage across both units."""
        return (self.unit_1.port2_voltage + self.unit_2.port2_voltage) / 2

    @property
    def port2_current(self) -> float:
        """Total port-2 current (sum of both units)."""
        return self.unit_1.port2_current + self.unit_2.port2_current

    @property
    def port2_power(self) -> float:
        """Total port-2 power (sum of both units)."""
        return self.unit_1.port2_power + self.unit_2.port2_power

    @property
    def transformer_ratio(self) -> float:
        """Average transformer ratio across both units."""
        return (self.unit_1.transformer_ratio + self.unit_2.transformer_ratio) / 2

    # -- commands (broadcast to both units) -----------------------------------

    def set_transformer_ratio(self, ratio: float) -> None:
        self.unit_1.set_transformer_ratio(ratio)
        self.unit_2.set_transformer_ratio(ratio)

    def set_mode_assignment(self, mode_assignment: str) -> None:
        self.unit_1.set_mode_assignment(mode_assignment)
        self.unit_2.set_mode_assignment(mode_assignment)

    def set_command(self, command: str) -> None:
        self.unit_1.set_command(command)
        self.unit_2.set_command(command)

    def set_mode(self, mode: str) -> None:
        self.unit_1.set_mode(mode)
        self.unit_2.set_mode(mode)

    def set_port2_current(self, current: float) -> None:
        """Split the current setpoint equally between both units."""
        per_unit = current / 2
        self.unit_1.set_port2_current(per_unit)
        self.unit_2.set_port2_current(per_unit)

    def enable(self, ratio: float | None = None) -> None:
        self.unit_1.enable(ratio)
        self.unit_2.enable(ratio)

    def disable(self) -> None:
        self.unit_1.disable()
        self.unit_2.disable()

    def write(self, commands: dict) -> None:
        """Broadcast arbitrary register commands to both units."""
        self.unit_1.write(commands)
        self.unit_2.write(commands)
