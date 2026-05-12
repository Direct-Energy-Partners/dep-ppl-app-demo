"""
DC Meter device abstraction - Acrel DC energy meter.

Reads DC bus voltage.

Registers:
  measure.ports.port1.voltage  - DC bus voltage (V DC)
"""
from __future__ import annotations

from microgrid.devices.base import Device
from microgrid import config


class DCMeter(Device):

    def __init__(self, app):
        super().__init__(app, config.DC_METER_ID)

    # -- measurements ---------------------------------------------------------

    @property
    def voltage(self) -> float:
        return self.read_float("measure.ports.port1.voltage")

    # -- availability ---------------------------------------------------------

    @property
    def is_available(self) -> bool:
        """True when the meter is reporting a non-zero voltage."""
        return self.voltage > 0
