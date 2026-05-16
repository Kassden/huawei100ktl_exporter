"""
Modbus client for Huawei SUN2000 solar inverters.
Separated to avoid circular import issues.
"""

import struct
from typing import Any, List
from fastapi import HTTPException
from pymodbus.client import AsyncModbusSerialClient, AsyncModbusTcpClient
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

def parse_register_value(registers, data_type):
    """Parse a Modbus register payload based on the declared data type."""
    if data_type == "string":
        return parse_string_registers(registers)
    if data_type == "int32":
        return parse_int32_registers(registers)
    if data_type == "uint32":
        return parse_uint32_registers(registers)
    if data_type == "uint16":
        return parse_uint16_register(registers)
    if data_type == "int16":
        return parse_int16_register(registers)
    if data_type == "epoch_seconds":
        return parse_epoch_seconds_registers(registers)
    if data_type == "mld":
        return list(registers)
    return registers[0] if len(registers) == 1 else list(registers)

def build_int32_registers(value):
    """Convert signed 32-bit integer to two 16-bit registers (big endian)"""
    packed = struct.pack(">i", int(value))
    return [struct.unpack(">H", packed[:2])[0], struct.unpack(">H", packed[2:])[0]]

def build_uint32_registers(value):
    """Convert unsigned 32-bit integer to two 16-bit registers (big endian)"""
    packed = struct.pack(">I", int(value))
    return [struct.unpack(">H", packed[:2])[0], struct.unpack(">H", packed[2:])[0]]

def build_uint16_register(value):
    """Convert unsigned 16-bit integer to a single 16-bit register"""
    return [int(value) & 0xFFFF]

def build_int16_register(value):
    """Convert signed 16-bit integer to a single 16-bit register"""
    packed = struct.pack(">h", int(value))
    return [struct.unpack(">H", packed)[0]]

def build_register_payload(spec, value):
    """Build Modbus register payload from a register spec and human-scale value."""
    data_type = spec["type"]

    if data_type == "mld":
        if not isinstance(value, (list, tuple)):
            raise ValueError("MLD register writes require a list of integer register values")
        if len(value) != spec["count"]:
            raise ValueError(f"MLD register write requires exactly {spec['count']} values")
        return [int(item) & 0xFFFF for item in value]

    scale = spec.get("scale", 1)
    raw_value = value / scale if scale not in (0, None) else value

    if data_type == "uint16":
        return build_uint16_register(round(raw_value))
    if data_type == "int16":
        return build_int16_register(round(raw_value))
    if data_type == "uint32":
        return build_uint32_registers(round(raw_value))
    if data_type == "int32":
        return build_int32_registers(round(raw_value))
    if data_type == "epoch_seconds":
        return build_uint32_registers(round(raw_value))

    raise ValueError(f"Unsupported control register type: {data_type}")

class HuaweiModbusClient:
    def __init__(
        self,
        host,
        port,
        unit_id,
        timeout=5.0,
        transport="tcp",
        serial_port=None,
        baudrate=9600,
        parity="N",
        bytesize=8,
        stopbits=1,
    ):
        self.transport = transport.strip().lower()
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.timeout = timeout
        self.serial_port = serial_port
        self.baudrate = baudrate
        self.parity = parity
        self.bytesize = bytesize
        self.stopbits = stopbits
        self.client = None

    async def connect(self):
        if self.client is not None and self.client.connected:
            return

        if self.transport == "tcp":
            self.client = AsyncModbusTcpClient(
                self.host,
                port=self.port,
                timeout=self.timeout,
            )
        elif self.transport == "rtu":
            if not self.serial_port:
                raise ValueError("SUN2000_SERIAL_PORT is required when SUN2000_MODBUS_TRANSPORT=rtu")
            self.client = AsyncModbusSerialClient(
                port=self.serial_port,
                baudrate=self.baudrate,
                parity=self.parity,
                bytesize=self.bytesize,
                stopbits=self.stopbits,
                timeout=self.timeout,
            )
        else:
            raise ValueError(f"Unsupported Modbus transport: {self.transport}")

        connected = await self.client.connect()
        if not connected or not self.client.connected:
            if self.transport == "tcp":
                raise ConnectionError(f"Unable to connect to Modbus device at {self.host}:{self.port}")
            raise ConnectionError(f"Unable to connect to Modbus RTU device at {self.serial_port}")

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

ModbusTCPClient = HuaweiModbusClient

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
    "dc_power": {"address": 32064, "count": 2, "type": "int32", "scale": 0.001, "unit": "kW"},
    "grid_voltage_L1_L2": {"address": 32066, "count": 1, "type": "uint16", "scale": 0.1, "unit": "V"},
    "grid_voltage_L2_L3": {"address": 32067, "count": 1, "type": "uint16", "scale": 0.1, "unit": "V"},
    "grid_voltage_L3_L1": {"address": 32068, "count": 1, "type": "uint16", "scale": 0.1, "unit": "V"},
    "phase_A_voltage": {"address": 32069, "count": 1, "type": "uint16", "scale": 0.1, "unit": "V"},
    "phase_B_voltage": {"address": 32070, "count": 1, "type": "uint16", "scale": 0.1, "unit": "V"},
    "phase_C_voltage": {"address": 32071, "count": 1, "type": "uint16", "scale": 0.1, "unit": "V"},
    "phase_A_current": {"address": 32072, "count": 2, "type": "int32", "scale": 0.001, "unit": "A"},
    "phase_B_current": {"address": 32074, "count": 2, "type": "int32", "scale": 0.001, "unit": "A"},
    "phase_C_current": {"address": 32076, "count": 2, "type": "int32", "scale": 0.001, "unit": "A"},
    "peak_active_power_of_day": {"address": 32078, "count": 2, "type": "int32", "scale": 0.001, "unit": "kW"},
    "active_power": {"address": 32080, "count": 2, "type": "int32", "scale": 0.001, "unit": "kW"},
    "reactive_power": {"address": 32082, "count": 2, "type": "int32", "scale": 0.001, "unit": "kVar"},
    "power_factor": {"address": 32084, "count": 1, "type": "int16", "scale": 0.001, "unit": ""},
    "grid_frequency": {"address": 32085, "count": 1, "type": "uint16", "scale": 0.01, "unit": "Hz"},
    "efficiency": {"address": 32086, "count": 1, "type": "uint16", "scale": 0.01, "unit": "%"},
    "cabinet_temperature": {"address": 32087, "count": 1, "type": "int16", "scale": 0.1, "unit": "°C"},
    "insulation_resistance": {"address": 32088, "count": 1, "type": "uint16", "scale": 0.001, "unit": "MΩ"},

    # Energy Statistics
    "cumulative_generated_electricity": {"address": 32106, "count": 2, "type": "uint32", "scale": 0.01, "unit": "kWh"},
    "daily_generated_electricity": {"address": 32114, "count": 2, "type": "uint32", "scale": 0.01, "unit": "kWh"},
    "monthly_generated_electricity": {"address": 32116, "count": 2, "type": "uint32", "scale": 0.01, "unit": "kWh"},
    "yearly_generated_electricity": {"address": 32118, "count": 2, "type": "uint32", "scale": 0.01, "unit": "kWh"},
    "electricity_generated_previous_hour": {"address": 32158, "count": 2, "type": "uint32", "scale": 0.01, "unit": "kWh"},
    "electricity_generated_previous_day": {"address": 32162, "count": 2, "type": "uint32", "scale": 0.01, "unit": "kWh"},
    "electricity_generated_previous_month": {"address": 32166, "count": 2, "type": "uint32", "scale": 0.01, "unit": "kWh"},
    "electricity_generated_previous_year": {"address": 32170, "count": 2, "type": "uint32", "scale": 0.01, "unit": "kWh"},

    # Other Important Parameters
    "inverter_state": {"address": 32000, "count": 1, "type": "uint16", "scale": 1, "unit": ""},
    "device_state": {"address": 32089, "count": 1, "type": "uint16", "scale": 1, "unit": ""},
}

for string_index in range(1, 21):
    voltage_address = 32016 + ((string_index - 1) * 2)
    current_address = voltage_address + 1
    TELEMETRY_MAP[f"pv{string_index}_voltage"] = {
        "address": voltage_address,
        "count": 1,
        "type": "int16",
        "scale": 0.1,
        "unit": "V",
    }
    TELEMETRY_MAP[f"pv{string_index}_current"] = {
        "address": current_address,
        "count": 1,
        "type": "int16",
        "scale": 0.01,
        "unit": "A",
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
    "active_power_kw_derating": {"address": 40120, "count": 1, "type": "uint16", "scale": 0.1},
    "power_factor_setting": {"address": 40122, "count": 1, "type": "int16", "scale": 0.001},
    "reactive_power_compensation_qs": {"address": 40123, "count": 1, "type": "int16", "scale": 0.001},
    "reactive_power_adjustment_time": {"address": 40124, "count": 1, "type": "uint16", "scale": 1},
    "active_power_percentage_derating": {"address": 40125, "count": 1, "type": "int16", "scale": 0.1},
    "active_power_fixed_value_derating_w": {"address": 40126, "count": 2, "type": "uint32", "scale": 1},
    "active_power_percentage_control": {"address": 40199, "count": 1, "type": "int16", "scale": 0.1},
    "power_on": {"address": 40200, "count": 1, "type": "uint16", "scale": 1},
    "shutdown": {"address": 40201, "count": 1, "type": "uint16", "scale": 1},
    "reset": {"address": 40205, "count": 1, "type": "uint16", "scale": 1},
}

SETTINGS_MAP = {
    "system_time_local_time": {"address": 40000, "count": 2, "type": "epoch_seconds", "scale": 1, "unit": "s", "description": "System time in epoch seconds"},
    "q_u_curve_model": {"address": 40037, "count": 1, "type": "uint16", "scale": 1, "unit": "", "description": "Q-U characteristic curve model"},
    "q_u_scheduling_trigger_power_percentage": {"address": 40038, "count": 1, "type": "int16", "scale": 1, "unit": "%", "description": "Q-U scheduling trigger power percentage"},
    "active_power_kw_derating": {"address": 40120, "count": 1, "type": "uint16", "scale": 0.1, "unit": "kW", "description": "Active power fixed value derating"},
    "power_factor_setting": {"address": 40122, "count": 1, "type": "int16", "scale": 0.001, "unit": "", "description": "Power factor setpoint"},
    "reactive_power_compensation_qs": {"address": 40123, "count": 1, "type": "int16", "scale": 0.001, "unit": "", "description": "Reactive power compensation (Q/S)"},
    "reactive_power_adjustment_time": {"address": 40124, "count": 1, "type": "uint16", "scale": 1, "unit": "s", "description": "Reactive power adjustment time"},
    "active_power_percentage_derating": {"address": 40125, "count": 1, "type": "int16", "scale": 0.1, "unit": "%", "description": "Active power percentage derating"},
    "active_power_fixed_value_derating_w": {"address": 40126, "count": 2, "type": "uint32", "scale": 1, "unit": "W", "description": "Active power fixed value derating"},
    "reactive_power_compensation_at_night_qs": {"address": 40128, "count": 1, "type": "int16", "scale": 0.001, "unit": "", "description": "Night reactive power compensation (Q/S)"},
    "fixed_reactive_power_at_night": {"address": 40129, "count": 2, "type": "int32", "scale": 0.001, "unit": "kVar", "description": "Night fixed reactive power"},
    "cosphi_p_pn_characteristic_curve": {"address": 40133, "count": 21, "type": "mld", "scale": 1, "unit": "", "description": "cosphi-P/Pn characteristic curve raw registers"},
    "q_u_characteristic_curve": {"address": 40154, "count": 21, "type": "mld", "scale": 1, "unit": "", "description": "Q-U characteristic curve raw registers"},
    "pf_u_characteristic_curve": {"address": 40175, "count": 21, "type": "mld", "scale": 1, "unit": "", "description": "PF-U characteristic curve raw registers"},
    "characteristic_curve_reactive_power_adjustment_time": {"address": 40196, "count": 1, "type": "uint16", "scale": 1, "unit": "s", "description": "Characteristic curve reactive power adjustment time"},
    "percent_apparent_power": {"address": 40197, "count": 1, "type": "uint16", "scale": 0.1, "unit": "%", "description": "Percent apparent power"},
    "q_u_scheduling_exit_power_percentage": {"address": 40198, "count": 1, "type": "int16", "scale": 1, "unit": "%", "description": "Q-U scheduling exit power percentage"},
    "active_power_percentage_control": {"address": 40199, "count": 1, "type": "int16", "scale": 0.1, "unit": "%", "description": "Active power percentage control"},
    "q_p_characteristic_curve": {"address": 40354, "count": 21, "type": "mld", "scale": 1, "unit": "", "description": "Q-P characteristic curve raw registers"},
    "minimum_pf_limit_for_q_u_curve": {"address": 40375, "count": 1, "type": "uint16", "scale": 0.001, "unit": "", "description": "Minimum PF limit for Q-U curve"},
    "q_u_curve_effective_delay_time": {"address": 40376, "count": 1, "type": "uint16", "scale": 1, "unit": "s", "description": "Q-U curve effective delay time"},
    "grid_standard_code": {"address": 42000, "count": 1, "type": "uint16", "scale": 1, "unit": "", "description": "Grid standard code"},
    "output_mode": {"address": 42001, "count": 1, "type": "uint16", "scale": 1, "unit": "", "description": "Output mode"},
    "voltage_level": {"address": 42002, "count": 1, "type": "uint16", "scale": 1, "unit": "V", "description": "Voltage level"},
    "frequency_level": {"address": 42003, "count": 1, "type": "uint16", "scale": 1, "unit": "Hz", "description": "Frequency level"},
    "remote_power_scheduling": {"address": 42014, "count": 1, "type": "uint16", "scale": 1, "unit": "", "description": "Remote power scheduling enable"},
    "reactive_power_variation_gradient": {"address": 42015, "count": 2, "type": "uint32", "scale": 0.001, "unit": "%/s", "description": "Reactive power variation gradient"},
    "active_power_gradient": {"address": 42017, "count": 2, "type": "uint32", "scale": 0.001, "unit": "%/s", "description": "Active power gradient"},
    "scheduling_instruction_maintenance_time": {"address": 42019, "count": 2, "type": "uint32", "scale": 1, "unit": "s", "description": "Scheduling instruction maintenance time"},
    "maximum_apparent_power": {"address": 42021, "count": 2, "type": "uint32", "scale": 0.001, "unit": "kVA", "description": "Maximum apparent power"},
    "maximum_active_power": {"address": 42023, "count": 2, "type": "uint32", "scale": 0.001, "unit": "kW", "description": "Maximum active power"},
    "apparent_power_reference": {"address": 42025, "count": 2, "type": "uint32", "scale": 0.001, "unit": "kVar", "description": "Apparent power reference"},
    "active_power_reference": {"address": 42027, "count": 2, "type": "uint32", "scale": 0.001, "unit": "kW", "description": "Active power reference"},
    "power_station_active_power_gradient": {"address": 42029, "count": 1, "type": "uint16", "scale": 1, "unit": "min/100%", "description": "Power station active power gradient"},
    "power_station_average_active_power_filtering_time": {"address": 42030, "count": 2, "type": "uint32", "scale": 1, "unit": "ms", "description": "Power station average active power filtering time"},
    "pf_u_voltage_detection_filter_time": {"address": 42032, "count": 1, "type": "uint16", "scale": 0.1, "unit": "s", "description": "PF-U voltage detection filter time"},
    "frequency_detection_filter_time": {"address": 42037, "count": 1, "type": "uint16", "scale": 1, "unit": "ms", "description": "Frequency detection filter time"},
    "frequency_active_derating_recovery_delay_time": {"address": 42040, "count": 1, "type": "uint16", "scale": 1, "unit": "s", "description": "Frequency active derating recovery delay time"},
    "effective_delay_time_active_frequency_derating": {"address": 42041, "count": 1, "type": "uint16", "scale": 1, "unit": "ms", "description": "Effective delay time of active frequency derating"},
    "frequency_active_derating_hysteresis_loop": {"address": 42042, "count": 1, "type": "uint16", "scale": 1, "unit": "", "description": "Frequency active derating hysteresis loop"},
    "fm_control_response_dead_zone": {"address": 42043, "count": 1, "type": "uint16", "scale": 0.001, "unit": "Hz", "description": "FM control response dead zone"},
    "pq_mode": {"address": 42046, "count": 1, "type": "uint16", "scale": 1, "unit": "", "description": "PQ mode"},
    "panel_type": {"address": 42047, "count": 1, "type": "uint16", "scale": 1, "unit": "", "description": "Panel type"},
    "pid_compensation_direction": {"address": 42048, "count": 1, "type": "uint16", "scale": 1, "unit": "", "description": "PID compensation direction"},
    "string_connection_mode": {"address": 42049, "count": 1, "type": "uint16", "scale": 1, "unit": "", "description": "String connection mode"},
    "isolation_settings": {"address": 42050, "count": 1, "type": "uint16", "scale": 1, "unit": "", "description": "Isolation settings"},
    "frequency_modulation_control_power_variation_gradient": {"address": 42051, "count": 1, "type": "uint16", "scale": 1, "unit": "%/min", "description": "Frequency modulation control power variation gradient"},
    "fm_control_power_variation_limit": {"address": 42052, "count": 1, "type": "uint16", "scale": 0.1, "unit": "%", "description": "FM control power variation limit"},
    "fm_control_delay_response_time": {"address": 42053, "count": 1, "type": "uint16", "scale": 1, "unit": "ms", "description": "FM control delay response time"},
    "mppt_multimodal_scanning": {"address": 42054, "count": 1, "type": "uint16", "scale": 1, "unit": "", "description": "MPPT multimodal scanning"},
    "mppt_scanning_interval": {"address": 42055, "count": 1, "type": "uint16", "scale": 1, "unit": "min", "description": "MPPT scanning interval"},
    "automatic_power_grid_fault_recovery": {"address": 42061, "count": 1, "type": "uint16", "scale": 1, "unit": "", "description": "Automatic power grid fault recovery"},
    "power_limit_zero_percent_shutdown": {"address": 42062, "count": 1, "type": "uint16", "scale": 1, "unit": "", "description": "Power limit 0 percent shutdown"},
    "automatic_shutoff_communication_link_disconnection": {"address": 42063, "count": 1, "type": "uint16", "scale": 1, "unit": "", "description": "Automatic shut-off on communication link disconnection"},
    "communication_resumes_automatic_power_on": {"address": 42064, "count": 1, "type": "uint16", "scale": 1, "unit": "", "description": "Communication resumes automatic power-on"},
    "power_quality_optimization_mode": {"address": 42065, "count": 1, "type": "uint16", "scale": 1, "unit": "", "description": "Power quality optimization mode"},
    "rcd_enhancement": {"address": 42066, "count": 1, "type": "uint16", "scale": 1, "unit": "", "description": "RCD enhancement"},
    "no_time_work": {"address": 42067, "count": 1, "type": "uint16", "scale": 1, "unit": "", "description": "No-time work"},
    "night_pid_protection": {"address": 42069, "count": 1, "type": "uint16", "scale": 1, "unit": "", "description": "Night PID protection"},
    "reactive_power_parameter_takes_effect_at_night": {"address": 42070, "count": 1, "type": "uint16", "scale": 1, "unit": "", "description": "Reactive power parameter takes effect at night"},
    "communication_disconnection_detection_time": {"address": 42072, "count": 1, "type": "uint16", "scale": 1, "unit": "s", "description": "Communication disconnection detection time"},
    "afci": {"address": 42073, "count": 1, "type": "uint16", "scale": 1, "unit": "", "description": "AFCI enable"},
    "afci_detection_adaptation_mode": {"address": 42074, "count": 1, "type": "uint16", "scale": 1, "unit": "", "description": "AFCI detection adaptation mode"},
}
