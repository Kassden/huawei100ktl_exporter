"""
Modbus TCP client for Huawei SUN2000 solar inverters
Separated to avoid circular import issues
"""

import struct
from typing import List
from fastapi import HTTPException
from pymodbus.client import AsyncModbusTcpClient
from pymodbus import ModbusException

def parse_string_registers(registers):
    """Convert list of registers to string (2 bytes per register, big endian utf-8)."""
    raw = b"".join(struct.pack(">H", r) for r in registers)
    # Remove trailing nulls/garbage
    return raw.decode("utf-8", errors="ignore").replace('\x00', '').strip()

def parse_int32_registers(registers):
    """Convert two 16-bit registers to signed 32-bit integer (big endian)"""
    if len(registers) < 2:
        return None
    return struct.unpack(">i", struct.pack(">HH", registers[0], registers[1]))[0]

def parse_uint32_registers(registers):
    """Convert two 16-bit registers to unsigned 32-bit integer (big endian)"""
    if len(registers) < 2:
        return None
    return struct.unpack(">I", struct.pack(">HH", registers[0], registers[1]))[0]

def parse_uint16_register(registers):
    """Convert single 16-bit register to unsigned integer"""
    if len(registers) < 1:
        return None
    return registers[0]

def parse_int16_register(registers):
    """Convert single 16-bit register to signed integer"""
    if len(registers) < 1:
        return None
    return struct.unpack(">h", struct.pack(">H", registers[0]))[0]

def parse_epoch_seconds_registers(registers):
    """Convert two 16-bit registers to epoch seconds timestamp"""
    if len(registers) < 2:
        return None
    return struct.unpack(">I", struct.pack(">HH", registers[0], registers[1]))[0]

def build_int32_registers(value):
    """Convert signed 32-bit integer to two 16-bit registers (big endian)"""
    packed = struct.pack(">i", int(value))
    return [struct.unpack(">H", packed[:2])[0], struct.unpack(">H", packed[2:])[0]]

class ModbusTCPClient:
    def __init__(self, host, port, unit_id, timeout=5.0):
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.timeout = timeout
        self.client = None

    async def connect(self):
        self.client = AsyncModbusTcpClient(
            self.host,
            port=self.port,
            timeout=self.timeout,
        )
        connected = await self.client.connect()
        if not connected or not self.client.connected:
            raise ConnectionError(f"Unable to connect to Modbus device at {self.host}:{self.port}")

    def is_connected(self):
        return self.client is not None and self.client.connected

    def close(self):
        if self.client:
            try:
                self.client.close()
            except:
                pass  # Ignore close errors
            self.client = None

    async def read_holding_registers(self, address, count):
        if self.client is None or not self.client.connected:
            await self.connect()
        resp = await self.client.read_holding_registers(address, count=count, device_id=self.unit_id)
        if resp.isError():
            raise HTTPException(status_code=502, detail=f"Modbus read error: {resp}")
        return resp.registers

    async def write_registers(self, address, values):
        if self.client is None or not self.client.connected:
            await self.connect()
        if len(values) == 1:
            resp = await self.client.write_register(address, values[0], device_id=self.unit_id)
        else:
            resp = await self.client.write_registers(address, values, device_id=self.unit_id)
        if resp.isError():
            raise HTTPException(status_code=502, detail=f"Modbus write error: {resp}")

# Register mappings based on the "Solar Inverter Modbus Interface Definitions (V3.0)" specification
TELEMETRY_MAP = {
    # Alarms and Events
    "alarm_1": {"address": 32008, "count": 1, "type": "uint16", "scale": 1, "unit": ""},
    "alarm_2": {"address": 32009, "count": 1, "type": "uint16", "scale": 1, "unit": ""},
    "alarm_3": {"address": 32010, "count": 1, "type": "uint16", "scale": 1, "unit": ""},
    "highest_priority_alarm_code": {"address": 32090, "count": 1, "type": "uint16", "scale": 1, "unit": ""},
    "startup_time": {"address": 32091, "count": 2, "type": "epoch_seconds", "scale": 1, "unit": "s"},
    "shutdown_time": {"address": 32093, "count": 2, "type": "epoch_seconds", "scale": 1, "unit": "s"},
    "number_of_critical_alarms": {"address": 32151, "count": 1, "type": "uint16", "scale": 1, "unit": ""},
    "number_of_major_alarms": {"address": 32152, "count": 1, "type": "uint16", "scale": 1, "unit": ""},
    "number_of_minor_alarms": {"address": 32153, "count": 1, "type": "uint16", "scale": 1, "unit": ""},
    "number_of_warning_alarms": {"address": 32154, "count": 1, "type": "uint16", "scale": 1, "unit": ""},

    # Grid and Power
    "grid_voltage_L1_L2": {"address": 32064, "count": 2, "type": "int32", "scale": 0.1, "unit": "V"},
    "grid_voltage_L2_L3": {"address": 32066, "count": 2, "type": "int32", "scale": 0.1, "unit": "V"},
    "grid_voltage_L3_L1": {"address": 32068, "count": 2, "type": "int32", "scale": 0.1, "unit": "V"},
    "phase_A_voltage": {"address": 32070, "count": 2, "type": "int32", "scale": 0.1, "unit": "V"},
    "phase_B_voltage": {"address": 32072, "count": 2, "type": "int32", "scale": 0.1, "unit": "V"},
    "phase_C_voltage": {"address": 32074, "count": 2, "type": "int32", "scale": 0.1, "unit": "V"},
    "phase_A_current": {"address": 32076, "count": 2, "type": "int32", "scale": 0.001, "unit": "A"},
    "phase_B_current": {"address": 32078, "count": 2, "type": "int32", "scale": 0.001, "unit": "A"},
    "phase_C_current": {"address": 32080, "count": 2, "type": "int32", "scale": 0.001, "unit": "A"},
    "active_power": {"address": 32082, "count": 2, "type": "int32", "scale": 0.001, "unit": "kW"},
    "reactive_power": {"address": 32084, "count": 2, "type": "int32", "scale": 0.001, "unit": "kVar"},
    "power_factor": {"address": 32086, "count": 2, "type": "int16", "scale": 0.001, "unit": ""},
    "grid_frequency": {"address": 32088, "count": 2, "type": "int16", "scale": 0.01, "unit": "Hz"},

    # Energy Statistics
    "cumulative_generated_electricity": {"address": 32106, "count": 2, "type": "uint32", "scale": 0.01, "unit": "kWh"},
    "daily_generated_electricity": {"address": 32114, "count": 2, "type": "uint32", "scale": 0.01, "unit": "kWh"},
    "monthly_generated_electricity": {"address": 32116, "count": 2, "type": "uint32", "scale": 0.01, "unit": "kWh"},
    "yearly_generated_electricity": {"address": 32118, "count": 2, "type": "uint32", "scale": 0.01, "unit": "kWh"},
    "electricity_generated_previous_hour": {"address": 32158, "count": 2, "type": "uint32", "scale": 0.01, "unit": "kWh"},
    "electricity_generated_previous_day": {"address": 32162, "count": 2, "type": "uint32", "scale": 0.01, "unit": "kWh"},
    "electricity_generated_previous_month": {"address": 32166, "count": 2, "type": "uint32", "scale": 0.01, "unit": "kWh"},
    "electricity_generated_previous_year": {"address": 32170, "count": 2, "type": "uint32", "scale": 0.01, "unit": "kWh"},

    # PV Strings (first few and last for example - you can add all 32)
    "pv1_voltage": {"address": 32016, "count": 2, "type": "int16", "scale": 0.1, "unit": "V"},
    "pv1_current": {"address": 32018, "count": 2, "type": "int16", "scale": 0.01, "unit": "A"},
    "pv2_voltage": {"address": 32020, "count": 2, "type": "int16", "scale": 0.1, "unit": "V"},
    "pv2_current": {"address": 32022, "count": 2, "type": "int16", "scale": 0.01, "unit": "A"},
    "pv3_voltage": {"address": 32024, "count": 2, "type": "int16", "scale": 0.1, "unit": "V"},
    "pv3_current": {"address": 32026, "count": 2, "type": "int16", "scale": 0.01, "unit": "A"},
    "pv4_voltage": {"address": 32028, "count": 2, "type": "int16", "scale": 0.1, "unit": "V"},
    "pv4_current": {"address": 32030, "count": 2, "type": "int16", "scale": 0.01, "unit": "A"},
    # ... (add more PV strings as needed)
    "pv32_voltage": {"address": 32142, "count": 2, "type": "int16", "scale": 0.1, "unit": "V"},
    "pv32_current": {"address": 32144, "count": 2, "type": "int16", "scale": 0.01, "unit": "A"},

    # Other Important Parameters
    "inverter_state": {"address": 32000, "count": 1, "type": "uint16", "scale": 1, "unit": ""},
    "device_state": {"address": 32089, "count": 1, "type": "uint16", "scale": 1, "unit": ""},
    "total_dc_input_power": {"address": 32108, "count": 2, "type": "uint32", "scale": 0.001, "unit": "kW"},
    "efficiency": {"address": 32110, "count": 2, "type": "uint16", "scale": 0.01, "unit": "%"},
    "cabinet_temperature": {"address": 32112, "count": 2, "type": "int16", "scale": 0.1, "unit": "°C"},
}

DEVICE_MAP = {
    "model": {"address": 30000, "count": 15, "type": "string", "unit": ""},
    "serial_number": {"address": 30015, "count": 10, "type": "string", "unit": ""},
    "product_number": {"address": 30025, "count": 10, "type": "string", "unit": ""},
    "firmware_version": {"address": 30035, "count": 15, "type": "string", "unit": ""},
    "software_version": {"address": 30050, "count": 15, "type": "string", "unit": ""},
    "modbus_protocol_version": {"address": 30068, "count": 2, "type": "uint32", "unit": ""},
    "model_id": {"address": 30070, "count": 1, "type": "uint16", "unit": ""},
    "number_of_strings": {"address": 30071, "count": 1, "type": "uint16", "unit": ""},
    "number_of_mppts": {"address": 30072, "count": 1, "type": "uint16", "unit": ""},
    "rated_power": {"address": 30073, "count": 2, "type": "uint32", "scale": 0.001, "unit": "kW"},
    "max_active_power": {"address": 30075, "count": 2, "type": "uint32", "scale": 0.001, "unit": "kW"},
    "max_apparent_power": {"address": 30077, "count": 2, "type": "uint32", "scale": 0.001, "unit": "kVA"},
    "max_reactive_power_feed_to_grid": {"address": 30079, "count": 2, "type": "int32", "scale": 0.001, "unit": "kVar"},
    "max_reactive_power_absorb_from_grid": {"address": 30081, "count": 2, "type": "int32", "scale": 0.001, "unit": "kVar"},
}

CONTROL_MAP = {
    "power_on_off": {"address": 40000, "count": 1, "type": "uint16", "scale": 1},
    "active_power_control": {"address": 40120, "count": 1, "type": "uint16", "scale": 1},
    "fixed_active_power_setting": {"address": 40121, "count": 2, "type": "uint32", "scale": 0.001},
    "percentage_active_power_setting": {"address": 40123, "count": 2, "type": "uint16", "scale": 0.1},
    "reactive_power_control": {"address": 40125, "count": 1, "type": "uint16", "scale": 1},
    "power_factor_setting": {"address": 40126, "count": 1, "type": "int16", "scale": 0.001},
    "reactive_power_output_setting": {"address": 40127, "count": 2, "type": "int32", "scale": 0.001},
    "cosphi_p_characteristic_curve": {"address": 40133, "count": 21, "type": "mld", "scale": 1},
    "q_u_characteristic_curve": {"address": 40154, "count": 21, "type": "mld", "scale": 1},
    "pf_u_characteristic_curve": {"address": 40175, "count": 21, "type": "mld", "scale": 1},
    "system_time_year": {"address": 43000, "count": 1, "type": "uint16", "scale": 1},
    "system_time_month": {"address": 43001, "count": 1, "type": "uint16", "scale": 1},
    "system_time_day": {"address": 43002, "count": 1, "type": "uint16", "scale": 1},
    "system_time_hour": {"address": 43003, "count": 1, "type": "uint16", "scale": 1},
    "system_time_minute": {"address": 43004, "count": 1, "type": "uint16", "scale": 1},
    "system_time_seconds": {"address": 43005, "count": 1, "type": "uint16", "scale": 1},
}
