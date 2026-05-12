"""
Output data structure for all operating modes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Any


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
    description: str = ""
    procedure_commands: Dict[str, Any] = field(default_factory=dict)
