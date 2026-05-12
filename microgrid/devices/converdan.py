"""
DC-DC Converter device abstraction - wraps the Converdan converter.

Operates only in DC Transformer mode (mode 5).
Variable ratio ≈1.058 to keep DC bus within ~735–769 VDC.
Max output between 675–850 VDC.

Registers:
  control.transformer.ratio
  control.ports.port2.current
"""
from __future__ import annotations

from microgrid.devices.base import Device
from microgrid import config


class Converdan(Device):

    def __init__(self, app, device_id: str = config.CONVERTER_ID_1):
        super().__init__(app, device_id)

    # -- measurements ---------------------------------------------------------

    @property
    def port1_voltage(self) -> float:
        return self.read_float("measure.ports.port1.voltage")

    @property
    def port1_current(self) -> float:
        return self.read_float("measure.ports.port1.current")

    @property
    def port1_power(self) -> float:
        return self.read_float("measure.ports.port1.power")

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
    def transformer_ratio(self) -> float:
        return self.read_float("measure.transformer.ratio", config.CONVERDAN_RATIO_NOMINAL)

    @property
    def status(self) -> str | None:
        return self.read("state")

    # -- fault helpers --------------------------------------------------------

    def has_active_faults(self) -> bool:
        if self.read("measure.status"):
            return True
        return False

    # -- commands -------------------------------------------------------------

    def set_transformer_ratio(self, ratio: float) -> None:
        ratio = max(config.CONVERDAN_RATIO_MIN, ratio)
        self.write({"control.transformer.ratio": ratio})

    def set_mode_assignment(self, mode_assignment: str) -> None:
        self.write({"control.mode.assignment": mode_assignment})

    def set_command(self, command: str) -> None:
        self.write({"control.command": command})

    def set_mode(self, mode: str) -> None:
        self.write({"control.mode": mode})

    def set_port2_current(self, current: float) -> None:
        self.write({"control.ports.port2.current": current})

    def enable(self, ratio: float | None = None) -> None:
        r = ratio if ratio is not None else config.CONVERDAN_RATIO_NOMINAL
        self.set_transformer_ratio(r)
        self.set_mode_assignment("transformer-high-ratio")
        self.set_command("enable")
        self.set_mode("transformer-high-ratio")

    def disable(self) -> None:
        self.set_command("none")
        self.set_mode("passive")
