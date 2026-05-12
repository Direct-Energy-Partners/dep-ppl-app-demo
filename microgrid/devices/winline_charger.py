"""
DC EV Charger #1 - Winline.

Has two guns (A and B) with individual power setpoints.
Power setpoints must be written before a charging session starts.

Registers:
  control.gunA.power
  control.gunB.power
"""
from __future__ import annotations

from microgrid.devices.base import Device
from microgrid import config


class WinlineCharger(Device):

    def __init__(self, app):
        super().__init__(app, config.WINLINE_CHARGER_ID)
        self.power_max_kw = config.WINLINE_POWER_MAX

    # -- measurements ---------------------------------------------------------

    @property
    def charger_status(self) -> str | None:
        """'Charging', 'idle', or None."""
        return self.read("measure.charger.status")

    @property
    def is_charging(self) -> bool:
        return self.charger_status == "Charging"

    @property
    def gun_a_power(self) -> float:
        """Measured power on gun A (kW)."""
        return self.read_float("measure.gunA.power")

    @property
    def gun_b_power(self) -> float:
        """Measured power on gun B (kW)."""
        return self.read_float("measure.gunB.power")

    @property
    def total_power(self) -> float:
        """Total measured power across both guns (kW)."""
        return self.gun_a_power + self.gun_b_power

    # -- commands -------------------------------------------------------------

    def set_gun_a_power(self, power_kw: float) -> None:
        power_kw = max(0.0, min(power_kw, self.power_max_kw))
        self.write({"control.gunA.power": round(power_kw, 1)})

    def set_gun_b_power(self, power_kw: float) -> None:
        power_kw = max(0.0, min(power_kw, self.power_max_kw))
        self.write({"control.gunB.power": round(power_kw, 1)})

    def set_total_power(self, power_kw: float) -> None:
        """Distribute *power_kw* evenly across both guns."""
        per_gun = max(0.0, min(power_kw, self.power_max_kw)) / 2.0
        self.set_gun_a_power(per_gun)
        self.set_gun_b_power(per_gun)

    def disable(self) -> None:
        self.set_gun_a_power(0)
        self.set_gun_b_power(0)
