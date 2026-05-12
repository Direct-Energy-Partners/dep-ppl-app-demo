"""
Battery (BESS) device abstraction - wraps the Poweroad battery.

Registers:
  measure.ports.port1.voltage
  measure.ports.port1.current
  measure.ports.port1.power
  measure.ports.port1.soc
  measure.contactor.status
  control.contactor
"""
from __future__ import annotations

from microgrid.devices.base import Device
from microgrid import config


class Battery(Device):

    def __init__(self, app):
        super().__init__(app, config.BATTERY_ID)

    # -- measurements ---------------------------------------------------------

    @property
    def voltage(self) -> float:
        return self.read_float("measure.ports.port1.voltage")

    @property
    def current(self) -> float:
        return self.read_float("measure.ports.port1.current")

    @property
    def power(self) -> float:
        return self.read_float("measure.ports.port1.power")

    @property
    def soc(self) -> float:
        return self.read_float("measure.ports.port1.soc")

    @property
    def temperature(self) -> float:
        return self.read_float("measure.battery.temperature")

    @property
    def contactor_status(self) -> str | None:
        return self.read("measure.contactor.status")

    @property
    def contactor_closed(self) -> bool:
        return self.contactor_status == "close"

    # -- battery-reported limits (may or may not be available) ----------------

    @property
    def charge_power_max(self) -> float:
        """Max charge power reported by BMS (positive value, watts)."""
        # return abs(self.read_float("measure.ports.port1.power.charge.max", 0.0))
        return abs(config.BATTERY_MAX_CHARGE_POWER_W)

    @property
    def discharge_power_max(self) -> float:
        """Max discharge power reported by BMS (positive value, watts)."""
        # return abs(self.read_float("measure.ports.port1.power.discharge.max", 0.0))
        return abs(config.BATTERY_MAX_DISCHARGE_POWER_W)

    # -- commands -------------------------------------------------------------

    def close_contactor(self) -> None:
        self.write({"control.contactor": "close"})

    def open_contactor(self) -> None:
        self.write({"control.contactor": "open"})
