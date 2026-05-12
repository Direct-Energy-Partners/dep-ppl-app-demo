"""
Simulation runner with mocked device interfaces.

Provides a MockPplapp that behaves like the real Pplapp but stores
measurements in memory and logs commands - no NATS connection required.

Usage:
    python -m microgrid.simulation
"""
from __future__ import annotations

import logging
import time

from microgrid import config
from microgrid.devices.battery import Battery
from microgrid.devices.dcdc_converter import DCDCConverter
from microgrid.devices.infypower_rectifier import InfypowerRectifier
from microgrid.devices.winline_charger import WinlineCharger
from microgrid.devices.infypower_charger import InfypowerCharger
from microgrid.devices.ac_meter import ACMeter
from microgrid.devices.dc_meter import DCMeter
from microgrid.devices.contactor import Contactor
from microgrid.control.orchestrator import Orchestrator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-28s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("microgrid.simulation")


# =============================================================================
# Mock Pplapp - no NATS, fully in-memory
# =============================================================================

class MockPplapp:
    """Drop-in replacement for Pplapp that stores measurements locally."""

    def __init__(self):
        self.measurements: dict[str, dict[str, str]] = {}
        self._command_log: list[dict] = []

    # --- read interface (same signatures as real Pplapp) ---------------------

    def getMeasurements(self, device_id: str, register: str):
        return self.measurements.get(device_id, {}).get(register)

    def getAllMeasurements(self):
        return self.measurements

    # --- write interface -----------------------------------------------------

    def setCommands(self, device_id: str, commands: dict) -> None:
        log.debug("CMD  %s → %s", device_id, commands)
        self._command_log.append({"device_id": device_id, "commands": commands})
        # Echo commands back into measurements so the control loop sees them
        if device_id not in self.measurements:
            self.measurements[device_id] = {}
        for k, v in commands.items():
            # Map control registers to their measurement counterparts
            meas_key = k.replace("control.", "measure.")
            self.measurements[device_id][meas_key] = str(v)

    # --- helpers -------------------------------------------------------------

    def inject(self, device_id: str, values: dict[str, str | float]) -> None:
        """Pre-populate measurement registers for a simulated device."""
        if device_id not in self.measurements:
            self.measurements[device_id] = {}
        for k, v in values.items():
            self.measurements[device_id][k] = str(v)

    @property
    def last_commands(self) -> list[dict]:
        return self._command_log

    def stop(self) -> None:
        pass


# =============================================================================
# Scenario helpers
# =============================================================================

def setup_default_measurements(app: MockPplapp, soc: float = 50.0) -> None:
    """Inject realistic default measurements for all five devices."""

    battery_voltage = 768 + (797 - 768) * (soc - 20) / (80 - 20)  # linear interpolation
    bus_voltage = battery_voltage * config.CONVERDAN_RATIO_NOMINAL

    app.inject(config.BATTERY_ID, {
        "state": "online",
        "measure.ports.port1.voltage": battery_voltage,
        "measure.ports.port1.current": 0,
        "measure.ports.port1.power": 0,
        "measure.ports.port1.soc": soc,
        "measure.contactor.status": "close",
        "measure.ports.port1.power.charge.max": 50000,
        "measure.ports.port1.power.discharge.max": 50000,
    })

    _converdan_data = {
        "state": "online",
        "measure.ports.port1.voltage": battery_voltage,
        "measure.ports.port2.voltage": bus_voltage,
        "measure.ports.port2.current": 0,
        "measure.ports.port2.power": 0,
        "measure.ports.port2.method": "idle",
        "measure.transformer.ratio": config.CONVERDAN_RATIO_NOMINAL,
    }
    app.inject(config.CONVERTER_ID_1, _converdan_data)
    app.inject(config.CONVERTER_ID_2, _converdan_data)

    app.inject(config.RECTIFIER_ID, {
        "state": "online",
        "measure.ports.port2.voltage": bus_voltage,
        "measure.ports.port2.current": 0,
        "measure.ports.port2.power": 0,
    })

    app.inject(config.WINLINE_CHARGER_ID, {
        "state": "online",
        "measure.charger.status": "idle",
        "measure.gunA.power": 0,
        "measure.gunB.power": 0,
    })

    app.inject(config.INFYPOWER_CHARGER_ID, {
        "state": "online",
        "measure.charger.status": "idle",
        "measure.charger.power": 0,
    })


def run_scenario(
    name: str,
    soc: float,
    winline_charging: bool = False,
    infypower_charging: bool = False,
    grid_available: bool = True,
    ticks: int = 3,
) -> None:
    """Run a named scenario for *ticks* control cycles and print results."""
    log.info("=" * 70)
    log.info("SCENARIO: %s", name)
    log.info("=" * 70)

    app = MockPplapp()
    setup_default_measurements(app, soc=soc)

    # Override charger / grid state for this scenario
    if winline_charging:
        app.inject(config.WINLINE_CHARGER_ID, {"measure.charger.status": "Charging"})
    if infypower_charging:
        app.inject(config.INFYPOWER_CHARGER_ID, {"measure.charger.status": "Charging"})
    if not grid_available:
        app.inject(config.RECTIFIER_ID, {"state": "outage"})

    orchestrator = Orchestrator(
        battery=Battery(app),
        converdan=DCDCConverter(app),
        rectifier=InfypowerRectifier(app),
        winline=WinlineCharger(app),
        infypower_charger=InfypowerCharger(app),
        ac_meter=ACMeter(app),
        dc_meter=DCMeter(app),
        k1=Contactor(app, config.CONTACTOR_K1),
        k3=Contactor(app, config.CONTACTOR_K3),
        k4=Contactor(app, config.CONTACTOR_K4),
        k11=Contactor(app, config.CONTACTOR_K11),
        k13=Contactor(app, config.CONTACTOR_K13),
    )

    for i in range(ticks):
        status = orchestrator.tick()
        print(f"  tick {i + 1}: {status}")

    print()


# =============================================================================
# Main - run through all key scenarios
# =============================================================================

def main() -> None:
    log.info("Starting DC microgrid simulation …")

    run_scenario(
        "1. Startup, no vehicles",
        soc=50,
    )
    run_scenario(
        "2. Infypower charger running, BESS sole supply",
        soc=60,
        infypower_charging=True,
    )
    run_scenario(
        "3. Winline charger running, BESS sole supply",
        soc=60,
        winline_charging=True,
    )
    run_scenario(
        "4. Both chargers, BESS + grid",
        soc=50,
        winline_charging=True,
        infypower_charging=True,
    )
    run_scenario(
        "5–7. BESS SOC low, transition to grid sole supply",
        soc=18,
        winline_charging=True,
        infypower_charging=True,
    )
    run_scenario(
        "8. BESS SOC low, recharge from grid",
        soc=18,
    )
    run_scenario(
        "9. Islanded, no vehicles",
        soc=50,
        grid_available=False,
    )
    run_scenario(
        "10. Islanded, Infypower charger",
        soc=60,
        infypower_charging=True,
        grid_available=False,
    )
    run_scenario(
        "11. Islanded, Winline charger",
        soc=60,
        winline_charging=True,
        grid_available=False,
    )
    run_scenario(
        "12. Islanded, both chargers",
        soc=60,
        winline_charging=True,
        infypower_charging=True,
        grid_available=False,
    )
    run_scenario(
        "13. Islanded, BESS low SOC",
        soc=12,
        grid_available=False,
    )
    run_scenario(
        "14. Grid restored, BESS low SOC",
        soc=18,
        grid_available=True,
    )

    log.info("Simulation complete.")


if __name__ == "__main__":
    main()
