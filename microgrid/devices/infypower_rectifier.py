"""
AC-DC Rectifier device abstraction - wraps the Infypower module (REG).

The rectifier maintains its voltage setpoint at or slightly above DC bus voltage.
It can supply current up to 100 A.
Operates in CV (constant-voltage) mode by default; CC mode avoided when EV chargers online.

Registers:
  control.ports.port2.current
  control.ports.port2.voltage
"""
from __future__ import annotations

from microgrid.devices.base import Device
from microgrid import config


class InfypowerRectifier(Device):

    def __init__(self, app):
        super().__init__(app, config.RECTIFIER_ID)

    # -- measurements ---------------------------------------------------------

    @property
    def port2_voltage(self) -> float:
        return self.read_float("measure.ports.port2.voltage")

    @property
    def port2_current(self) -> float:
        return self.read_float("measure.ports.port2.current")

    @property
    def port2_power(self) -> float:
        return self.read_float("measure.ports.port2.power")

    @property
    def status(self) -> str | None:
        return self.read("state")

    # -- commands -------------------------------------------------------------

    def set_voltage(self, voltage: float) -> None:
        self.write({"control.ports.port2.voltage": voltage})

    def set_current_limit(self, current: float) -> None:
        current = max(0.0, min(current, config.RECTIFIER_CURRENT_MAX))
        self.write({"control.ports.port2.current": current})

    def set_enable(self) -> None:
        self.write({"control.enable": "on"})

    def set_disable(self) -> None:
        self.write({"control.enable": "off"})

    def enable(self, voltage: float, current_limit: float) -> None:
        """Enable rectifier at the given voltage setpoint and current limit."""
        self.set_voltage(voltage)
        self.set_current_limit(current_limit)
        self.set_enable()

    def disable(self) -> None:
        """Disable rectifier output by setting current limit to 0."""
        self.set_current_limit(0)
        self.set_disable()
