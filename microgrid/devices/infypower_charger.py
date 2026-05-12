"""
DC EV Charger #2 - Infypower charger.

Register definitions are not fully specified. This abstraction mirrors the
Winline charger interface so that the rest of the control system can treat
both chargers uniformly.

Assumption: the Infypower charger exposes a similar power-setpoint register.
Update the register paths once clarified.
"""
from __future__ import annotations

from microgrid.devices.base import Device
from microgrid import config


class InfypowerCharger(Device):

    def __init__(self, app):
        super().__init__(app, config.INFYPOWER_CHARGER_ID)
        self.power_max_w = config.INFYPOWER_CHARGER_POWER_MAX_W

    # -- measurements ---------------------------------------------------------

    @property
    def charger_status(self) -> str | None:
        return self.read("measure.charger.status")

    @property
    def is_charging(self) -> bool:
        return self.charger_status == "Charging"

    @property
    def total_power(self) -> float:
        """Measured charger output power (kW)."""
        return self.read_float("measure.charger.power")

    # -- commands -------------------------------------------------------------
    # NOTE: register paths are assumed; update when clarified.

    def set_power(self, power_w: float) -> None:
        power_w = max(0.0, min(power_w, self.power_max_w))
        self.write({"control.ports.port1.power.limit.static": power_w})

    def disable(self) -> None:
        self.set_power(0)
