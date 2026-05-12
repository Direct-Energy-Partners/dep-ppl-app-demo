from __future__ import annotations
from dataclasses import dataclass

from microgrid.devices.battery import Battery
from microgrid.devices.converdan import Converdan
from microgrid.devices.dcdc_converter import DCDCConverter
from microgrid.devices.infypower_rectifier import InfypowerRectifier
from microgrid.devices.winline_charger import WinlineCharger
from microgrid.devices.infypower_charger import InfypowerCharger
from microgrid.devices.ac_meter import ACMeter
from microgrid.devices.dc_meter import DCMeter
from microgrid.devices.contactor import Contactor


@dataclass
class Converters:
    dcdc: DCDCConverter
    rectifier: InfypowerRectifier


@dataclass
class Chargers:
    winline: WinlineCharger
    infypower: InfypowerCharger


@dataclass
class Meters:
    ac: ACMeter
    dc: DCMeter


@dataclass
class Contactors:
    k1: Contactor
    k3: Contactor
    k4: Contactor
    k11: Contactor
    k13: Contactor


@dataclass
class Devices:
    battery: Battery
    converters: Converters
    chargers: Chargers
    meters: Meters
    contactors: Contactors
