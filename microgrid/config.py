"""
System-wide configuration constants for the DC microgrid control system.

All voltages in V, currents in A, power in W, SOC in %, temperature in °C.
"""

# =============================================================================
# Device IDs (as registered in the NATS/PPL system)
# =============================================================================
BATTERY_ID = "poweroad"
CONVERTER_ID = "converdan"
RECTIFIER_ID = "infypower_module"
WINLINE_CHARGER_ID = "winline"
INFYPOWER_CHARGER_ID = "infypower_charger"
AC_METER_ID = "phoenix_contact_ac"
DC_METER_ID = "acrel_dc"

# =============================================================================
# DC Bus Limits
# =============================================================================
DC_BUS_VOLTAGE_MAX = 800  # V - absolute maximum allowable bus voltage
DC_BUS_VOLTAGE_MIN = 400  # V - absolute minimum (constrained by Winline charger)

DC_BUS_VOLTAGE_SUSPEND_THRESHOLD = 700   # V - suspend all charging sessions below this
DC_BUS_VOLTAGE_RESUME_THRESHOLD = 720    # V - resume charging sessions above this (hysteresis)
DC_BUS_VOLTAGE_OVERVOLTAGE = 780         # V - stop REG, CES P1, and disable Converdan

# =============================================================================
# Battery Limits
# =============================================================================
BATTERY_SOC_MIN = 20          # % - normal operating minimum (stop discharging)
BATTERY_SOC_MAX = 80          # % - normal operating maximum (stop charging)
BATTERY_SOC_CRITICAL_LOW = 10   # % - D3: ISL_CRITICAL_SOC → FAULT (D1)
BATTERY_SOC_CRITICAL_HIGH = 90  # % - hard ceiling
BATTERY_SOC_RECHARGE_START = 70  # % - D2: start recharging when SOC ≤ this (no EV sessions)
BATTERY_SOC_RECHARGE_STOP = 80   # % - D2: stop recharging when SOC reaches this
BATTERY_SOC_DISCHARGE_RESUME = 30  # % - D2: resume discharging after low SOC (hysteresis)
BATTERY_SOC_LOW_WARNING = 25   # % - D3: ISL_SOC_LOW threshold (begin ramp-down)
BATTERY_SOC_BLACKSTART_MIN = 20  # % - D1: minimum SOC for BESS blackstart path
BATTERY_SOC_CHARGER_HYSTERESIS = 25  # % - chargers can only start once SOC reaches this

# SOC-voltage reference points (from the SOC curve in the specification)
BATTERY_VOLTAGE_AT_SOC_20 = 768  # V - battery voltage at 20% SOC
BATTERY_VOLTAGE_AT_SOC_80 = 797  # V - battery voltage at 80% SOC
DC_BUS_VOLTAGE_AT_SOC_20 = 726  # V - DC bus voltage at 20% SOC
DC_BUS_VOLTAGE_AT_SOC_80 = 757  # V - DC bus voltage at 80% SOC

# Battery power limits
BATTERY_MAX_CHARGE_POWER_W = 100000  # W - maximum charge power
BATTERY_MAX_DISCHARGE_POWER_W = 100000  # W - maximum discharge power

# =============================================================================
# Converdan DC-DC Converter
# =============================================================================
CONVERDAN_MODE = 5                   # DC Transformer mode (high rate / boost when U Port1 <= U Port2)
CONVERDAN_RATIO_MIN = 1              # Minimum transformer ratio
CONVERDAN_RATIO_NOMINAL = 1.058      # Nominal ratio to get ~735-769 VDC on bus
CONVERDAN_OUTPUT_VOLTAGE_MIN = 675   # V - minimum output voltage
CONVERDAN_OUTPUT_VOLTAGE_MAX = 850   # V - maximum output voltage

# =============================================================================
# Infypower REG Rectifier (AC-DC)
# =============================================================================
RECTIFIER_CURRENT_MAX = 100  # A - maximum output current
RECTIFIER_VOLTAGE_SETPOINT_OFFSET = 5  # V - REG voltage slightly above DC bus voltage

# =============================================================================
# AC Input Protection
# =============================================================================
AC_OVERCURRENT_THRESHOLD = 60   # A - AC meter overcurrent detection threshold
AC_OVERCURRENT_POWER_LIMIT = 60000  # W - combined charger power limit during overcurrent

# =============================================================================
# EV Chargers
# =============================================================================
CHARGER_POWER_DERATING = 0.90  # Total EV charger power <= (P_available) * 90%
CHARGER_RAMP_STEP_W = 1000     # W - ramp step for charger setpoint changes (1 kW/s)

# Winline charger limits (from operating scenarios)
WINLINE_POWER_MAX_W = 80000   # W
# Infypower charger limits (from operating scenarios)
INFYPOWER_CHARGER_POWER_MAX_W = 60000  # W
# Combined charger maximums per D2/D3
CHARGER_COMBINED_MAX_ISLANDED_W = 80000  # W - max combined when islanded (40 kW each if both)
CHARGER_COMBINED_MAX_GRID_W = 120000     # W - max combined when grid connected (Infy 60 + Winline 80)
# EV setpoints: Infy 60kW, Winline 80kW (BESS sole supply)
# EV setpoints: Infy 60kW + Winline 120kW (BESS+grid shared, but limited by REG I-limit)

# =============================================================================
# Safety / Shutdown
# =============================================================================
CABINET_TEMP_MAX = 45  # °C - planned shutdown threshold

# =============================================================================
# Timing
# =============================================================================
STARTUP_DELAY_S = 5    # seconds - wait for NATS measurements to populate
CONTROL_LOOP_INTERVAL_S = 5  # seconds - main control loop period
WALK_IN_TIME_S = 5     # seconds - REG I-limit walk-in time (5s per D2/D4)
GRID_STABILITY_TIMER_S = 30  # seconds - AC meter must be stable for 30s before grid restore
CHARGER_RAMP_INTERVAL_S = 1  # seconds - charger ramp rate (1 kW/s)
REG_CAN_WALK_IN_S = 5  # seconds - REG CAN 0x13 cmd sets walk-in; factory default = 5s

# =============================================================================
# Converdan Ramp
# =============================================================================
CONVERDAN_RECONNECT_RAMP_W_PER_STEP = 1000  # W per control loop step
RECTIFIER_RECHARGE_RAMP_A_PER_STEP = 5      # A per control loop step for BESS recharge via grid
CONVERDAN_RECHARGE_POWER_W = 30000  # W - BESS charges at ~30 kW via grid (Proc E)

# =============================================================================
# AC Grid Detection (Phoenix Contact meter)
# =============================================================================
AC_VOLTAGE_NORMAL_MIN = 216  # V - minimum normal AC voltage (per phase)
AC_VOLTAGE_NORMAL_MAX = 253  # V - maximum normal AC voltage (per phase)

# =============================================================================
# Contactor IDs (logical names for PLC-controlled contactors)
# =============================================================================
CONTACTOR_K1 = "K1"    # REG output → DC bus (voltage match contactor)
CONTACTOR_K3 = "K3"    # Converdan Port 1 ↔ DC bus
CONTACTOR_K4 = "K4"    # BESS internal (BMS controlled)
CONTACTOR_K11 = "K11"  # EV charger Infypower → DC bus
CONTACTOR_K13 = "K13"  # EV charger Winline → DC bus
