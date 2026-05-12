"""
AC Meter device abstraction - Phoenix Contact AC energy meter.

Reads three-phase AC voltages to determine grid availability.
Normal operating range: 216-253 V AC on all three phases.

Registers:
  measure.ports.port1.voltage.a - Phase A voltage (V AC)
  measure.ports.port1.voltage.b - Phase B voltage (V AC)
  measure.ports.port1.voltage.c - Phase C voltage (V AC)
"""
from __future__ import annotations

from microgrid.devices.base import Device
from microgrid import config


class ACMeter(Device):

    def __init__(self, app):
        super().__init__(app, config.AC_METER_ID)

    # -- measurements ---------------------------------------------------------

    @property
    def voltage_a(self) -> float:
        return self.read_float("measure.ports.port1.voltage.a")

    @property
    def voltage_b(self) -> float:
        return self.read_float("measure.ports.port1.voltage.b")

    @property
    def voltage_c(self) -> float:
        return self.read_float("measure.ports.port1.voltage.c")

    @property
    def current(self) -> float:
        return self.read_float("measure.ports.port1.current")

    # -- availability ---------------------------------------------------------

    @property
    def is_available(self) -> bool:
        """True when the meter is reporting at least one non-zero phase voltage."""
        return self.voltage_a > 0 or self.voltage_b > 0 or self.voltage_c > 0

    @property
    def ac_available(self) -> bool:
        """True when all three phases are within the normal AC voltage band (216-253 V)."""
        if not self.is_available:
            return False
        return all(
            config.AC_VOLTAGE_NORMAL_MIN <= v <= config.AC_VOLTAGE_NORMAL_MAX
            for v in (self.voltage_a, self.voltage_b, self.voltage_c)
        )
