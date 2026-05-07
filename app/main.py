"""
Main entry point for the app.

Connects to the PPL controller via NATS, initialises all device abstractions,
and runs the orchestrator control loop.
"""
from __future__ import annotations

import logging
import os
import sys
import time

from dotenv import load_dotenv

STARTUP_DELAY_S = 5
CONTROL_LOOP_INTERVAL_S = 5

# Add parent directory to path so we can import pplapp
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pplapp import Pplapp

# -- Logging ------------------------------------------------------------------

log = logging.getLogger("app")
log.setLevel(logging.INFO)
formatter = logging.Formatter(
    fmt="[%(asctime)s] %(levelname)s %(name)s %(message)s",
    datefmt="%d.%m.%Y %H:%M:%S",
)
fileHandler = logging.FileHandler("app.log")
fileHandler.setFormatter(formatter)
log.addHandler(fileHandler)

def processMeasurements(app):
    measurements = app.getAllMeasurements()
    
    for deviceId, measurement in measurements.items():
        state = measurement.get("state", "unknown")
        log.info("Device ID: %s - State: %s", deviceId, state)

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

    log.info("Waiting %d s for initial measurements", STARTUP_DELAY_S)
    time.sleep(STARTUP_DELAY_S)

    try:
        while True:
            try:
                processMeasurements(app)
            except Exception as e:
                log.exception("Error in control loop: %s", e)
            time.sleep(CONTROL_LOOP_INTERVAL_S)

    except KeyboardInterrupt:
        log.info("Shutdown requested")
        time.sleep(CONTROL_LOOP_INTERVAL_S)
        app.stop()
        log.info("Clean shutdown complete")


if __name__ == "__main__":
    main()
