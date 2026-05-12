"""
Main entry point for the DC microgrid control system.

Connects to the PPL controller via NATS, initialises all device abstractions,
and runs the orchestrator control loop.
"""
from __future__ import annotations

import logging
import os
import sys
import time

from dotenv import load_dotenv

# Add parent directory to path so we can import pplapp
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pplapp import Pplapp
from microgrid import config
from microgrid.devices.battery import Battery
from microgrid.devices.converdan import Converdan
from microgrid.devices.infypower_rectifier import InfypowerRectifier
from microgrid.devices.winline_charger import WinlineCharger
from microgrid.devices.infypower_charger import InfypowerCharger
from microgrid.devices.ac_meter import ACMeter
from microgrid.devices.dc_meter import DCMeter
from microgrid.devices.contactor import Contactor

from microgrid.control.orchestrator import Orchestrator

# -- Logging ------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-28s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("microgrid.main")


def build_orchestrator(app: Pplapp) -> Orchestrator:
    """Instantiate all device wrappers and wire them into the orchestrator."""
    battery = Battery(app)
    converdan = Converdan(app)
    rectifier = InfypowerRectifier(app)
    winline = WinlineCharger(app)
    infypower_charger = InfypowerCharger(app)
    ac_meter = ACMeter(app)
    dc_meter = DCMeter(app)
    k11 = Contactor(app, config.CONTACTOR_K11)
    k13 = Contactor(app, config.CONTACTOR_K13)

    return Orchestrator(
        battery=battery,
        converdan=converdan,
        rectifier=rectifier,
        winline=winline,
        infypower_charger=infypower_charger,
        ac_meter=ac_meter,
        dc_meter=dc_meter,
        k11=k11,
        k13=k13,
    )


def main() -> None:
    load_dotenv()

    ip_address = os.getenv("IP_ADDRESS")
    username = os.getenv("NATS_USERNAME")
    password = os.getenv("NATS_PASSWORD")

    if not ip_address or not username or not password:
        log.error("IP_ADDRESS, NATS_USERNAME, and NATS_PASSWORD must be set in .env")
        sys.exit(1)

    log.info("Connecting to PPL controller at %s", ip_address)
    app = Pplapp(ip_address, username, password)

    log.info("Waiting %ds for initial measurements", config.STARTUP_DELAY_S)
    time.sleep(config.STARTUP_DELAY_S)

    orchestrator = build_orchestrator(app)
    log.info("Orchestrator initialised - entering control loop (interval=%ds)", config.CONTROL_LOOP_INTERVAL_S)

    try:
        while True:
            try:
                status = orchestrator.tick()
                print(status)
            except Exception as e:
                log.exception("Error in control loop: %s", e)
            time.sleep(config.CONTROL_LOOP_INTERVAL_S)

    except KeyboardInterrupt:
        log.info("Shutdown requested - disabling all devices")
        orchestrator.converdan.disable()
        orchestrator.rectifier.disable()
        orchestrator.winline.disable()
        orchestrator.infypower_charger.disable()
        time.sleep(config.CONTROL_LOOP_INTERVAL_S)
        app.stop()
        log.info("Clean shutdown complete.")


if __name__ == "__main__":
    main()
