"""
Base class for all device abstractions.

Provides a thin wrapper around the Pplapp NATS interface so that each device
can read its own measurements and send commands without knowing about the
underlying transport.
"""
from __future__ import annotations
from typing import Any, Optional


class Device:
    """Base class wrapping NATS read/write for a single device."""

    def __init__(self, app, device_id: str):
        self._app = app
        self.device_id = device_id

    # -- reading helpers ------------------------------------------------------

    def read(self, register: str) -> Optional[str]:
        """Read a single register value. Returns None if unavailable."""
        return self._app.getMeasurements(self.device_id, register)

    def read_float(self, register: str, default: float = 0.0) -> float:
        """Read a register and convert to float. Returns *default* on failure."""
        raw = self.read(register)
        if raw is None:
            return default
        try:
            return float(raw)
        except (ValueError, TypeError):
            return default

    def read_int(self, register: str, default: int = 0) -> int:
        """Read a register and convert to int. Returns *default* on failure."""
        return int(self.read_float(register, float(default)))

    # -- writing helpers ------------------------------------------------------

    def write(self, commands: dict[str, Any]) -> None:
        """Send a dict of register→value commands to this device."""
        str_commands = {k: str(v) for k, v in commands.items()}
        self._app.setCommands(self.device_id, str_commands)

    # -- status helpers -------------------------------------------------------

    @property
    def is_available(self) -> bool:
        """True when the device reports a known state (not communication fault)."""
        state = self.read("state")
        return state is not None and state != "communicationFault"
