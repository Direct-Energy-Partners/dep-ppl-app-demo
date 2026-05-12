"""
Precharge contactor device abstraction.
"""
from __future__ import annotations

from microgrid.devices.base import Device


class Contactor(Device):

    def __init__(self, app, device_id: str):
        super().__init__(app, device_id)

    @property
    def is_closed(self) -> bool:
        return self.read("measure.contactor.main") == "1"

    @property
    def is_open(self) -> bool:
        return self.read("measure.contactor.main") == "0"

    def close(self) -> None:
        self.write({"control.contactor.main": "1"})

    def open(self) -> None:
        self.write({"control.contactor.main": "0"})
