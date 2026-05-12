"""
Base class for operating mode strategies.

Each mode encapsulates:
  - Conditions under which it activates
  - Specific control actions for Converdan, Rectifier, and EV chargers
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from microgrid.control.orchestrator import SystemState


@dataclass
class ModeOutput:
    """Commands produced by a mode for one control cycle."""
    converdan_enabled: bool = False
    converdan_ratio: float = 1.058
    rectifier_enabled: bool = False
    rectifier_voltage: float = 0.0
    rectifier_current_limit: float = 0.0
    infypower_charger_power_w: float = 0.0
    winline_charger_power_w: float = 0.0
    infypower_charger_status: str = "idle"    # "idle" | "Charging"
    winline_charger_status: str = "idle"       # "idle" | "Charging"
    total_demand_w: float = 0.0
    description: str = ""


class OperatingMode(ABC):
    """Strategy interface - each scenario implements this."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def matches(self, state: SystemState) -> bool:
        """Return True if this mode should be the active mode given *state*."""
        ...

    @abstractmethod
    def compute(self, state: SystemState) -> ModeOutput:
        """Produce setpoints for this control cycle."""
        ...
