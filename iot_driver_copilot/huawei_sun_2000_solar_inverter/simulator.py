from __future__ import annotations

import asyncio
import math
import random
import struct
import time
from datetime import datetime

from pymodbus import ModbusDeviceIdentification
from pymodbus.server import ModbusTcpServer
from pymodbus.simulator import SimData, SimDevice
from pymodbus.simulator.simdata import DataType


def to_fixed_string(value: str, register_count: int) -> str:
    return value[: register_count * 2].ljust(register_count * 2, "\x00")


def signed_register_word(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value > 0x7FFF else value


def int32_to_registers(value: int) -> list[int]:
    packed = struct.pack(">i", int(value))
    return [int.from_bytes(packed[:2], "big"), int.from_bytes(packed[2:], "big")]


def uint32_to_registers(value: int) -> list[int]:
    packed = struct.pack(">I", int(value) & 0xFFFFFFFF)
    return [int.from_bytes(packed[:2], "big"), int.from_bytes(packed[2:], "big")]

def zero_registers(count: int) -> list[int]:
    return [0 for _ in range(count)]


class SolarState:
    def __init__(self) -> None:
        self.rated_power_w = 100_000
        self.base_voltage_ll_v = 400.0
        self.base_frequency_hz = 50.0
        self.startup_time = int(time.time() - 3600)
        self.total_energy_wh = 5_234_567
        self.last_update = time.time()
        self.device_state = 0x8000

    def irradiance_factor(self) -> float:
        now = datetime.now()
        hour = now.hour + now.minute / 60.0
        if hour < 6 or hour > 18:
            return 0.0
        normalized = (hour - 6) / 12.0
        bell = math.sin(normalized * math.pi)
        cloud = 0.7 + random.random() * 0.3
        return max(0.0, bell * cloud)

    def snapshot(self) -> dict[str, int]:
        active_power_w = int(self.irradiance_factor() * self.rated_power_w * 0.95)
        reactive_power_var = int(active_power_w * (0.05 + random.random() * 0.10)) if active_power_w else 0

        line_voltage = self.base_voltage_ll_v
        grid_l1_l2 = int((line_voltage + random.uniform(-8, 8)) * 10)
        grid_l2_l3 = int((line_voltage + random.uniform(-8, 8)) * 10)
        grid_l3_l1 = int((line_voltage + random.uniform(-8, 8)) * 10)

        phase_base = line_voltage / math.sqrt(3)
        phase_a_v = int((phase_base + random.uniform(-4, 4)) * 10)
        phase_b_v = int((phase_base + random.uniform(-4, 4)) * 10)
        phase_c_v = int((phase_base + random.uniform(-4, 4)) * 10)

        if active_power_w:
            avg_phase_v = (phase_a_v + phase_b_v + phase_c_v) / 3 / 10
            pf = 0.95 + random.random() * 0.04
            phase_current_a = active_power_w / (math.sqrt(3) * avg_phase_v * pf)
            phase_a_i = int(phase_current_a * (0.94 + random.random() * 0.12) * 1000)
            phase_b_i = int(phase_current_a * (0.94 + random.random() * 0.12) * 1000)
            phase_c_i = int(phase_current_a * (0.94 + random.random() * 0.12) * 1000)
            power_factor = int((active_power_w / math.sqrt(active_power_w**2 + reactive_power_var**2)) * 1000)
        else:
            phase_a_i = 0
            phase_b_i = 0
            phase_c_i = 0
            power_factor = 1000

        frequency = int((self.base_frequency_hz + random.uniform(-0.2, 0.2)) * 100)

        now_ts = time.time()
        delta_h = (now_ts - self.last_update) / 3600.0
        self.total_energy_wh += int(active_power_w * delta_h)
        self.last_update = now_ts

        total_energy_kwh_x100 = int(self.total_energy_wh / 10)
        daily_energy_kwh_x100 = int(max(active_power_w / 1000, 0) * 2)
        monthly_energy_kwh_x100 = 1250
        yearly_energy_kwh_x100 = 12500
        prev_hour_kwh_x100 = 5000
        prev_day_kwh_x100 = 40000
        prev_month_kwh_x100 = 120000
        prev_year_kwh_x100 = 1_000_000
        efficiency_x100 = 9850
        cabinet_temp_x10 = int((35 + random.uniform(-2, 5)) * 10)

        values = {
            32000: [3],
            32008: [0],
            32009: [0],
            32010: [0],
            32064: int32_to_registers(int(active_power_w * 1.05)),
            32066: [grid_l1_l2],
            32067: [grid_l2_l3],
            32068: [grid_l3_l1],
            32069: [phase_a_v],
            32070: [phase_b_v],
            32071: [phase_c_v],
            32072: int32_to_registers(phase_a_i),
            32074: int32_to_registers(phase_b_i),
            32076: int32_to_registers(phase_c_i),
            32078: int32_to_registers(active_power_w),
            32080: int32_to_registers(active_power_w),
            32082: int32_to_registers(reactive_power_var),
            32084: [power_factor],
            32085: [frequency],
            32086: [efficiency_x100],
            32087: [cabinet_temp_x10],
            32088: [2500],
            32089: [self.device_state],
            32090: [0],
            32091: uint32_to_registers(self.startup_time),
            32093: [0, 0],
            32106: uint32_to_registers(total_energy_kwh_x100),
            32114: uint32_to_registers(daily_energy_kwh_x100),
            32116: uint32_to_registers(monthly_energy_kwh_x100),
            32118: uint32_to_registers(yearly_energy_kwh_x100),
            32151: [0],
            32152: [0],
            32153: [0],
            32154: [0],
            32158: uint32_to_registers(prev_hour_kwh_x100),
            32162: uint32_to_registers(prev_day_kwh_x100),
            32166: uint32_to_registers(prev_month_kwh_x100),
            32170: uint32_to_registers(prev_year_kwh_x100),
        }

        pv_base_voltage_x10 = int((480 + random.uniform(-20, 20)) * 10) if active_power_w else 0
        pv_base_current_x100 = int((active_power_w / 20 / 480) * 100) if active_power_w else 0
        for string_idx in range(20):
            voltage_addr = 32016 + string_idx * 2
            current_addr = voltage_addr + 1
            pv_v = int(pv_base_voltage_x10 * (0.95 + random.random() * 0.1)) if active_power_w else 0
            pv_i = int(pv_base_current_x100 * (0.90 + random.random() * 0.2)) if active_power_w else 0
            values[voltage_addr] = [pv_v]
            values[current_addr] = [pv_i]
        return values


def build_device() -> SimDevice:
    blocks: list[SimData] = []
    writable_defaults = {
        40000: uint32_to_registers(int(time.time())),
        40037: [0],
        40038: [0],
        40120: [1000],
        40122: [1000],
        40123: [0],
        40124: [0],
        40125: [1000],
        40126: uint32_to_registers(100_000),
        40128: [0],
        40129: int32_to_registers(0),
        40133: zero_registers(21),
        40154: zero_registers(21),
        40175: zero_registers(21),
        40196: [0],
        40197: [1000],
        40198: [0],
        40199: [1000],
        40200: [0],
        40201: [0],
        40205: [0],
        40354: zero_registers(21),
        40375: [900],
        40376: [0],
        42000: [0],
        42001: [0],
        42002: [400],
        42003: [50],
        42014: [1],
        42015: uint32_to_registers(1000),
        42017: uint32_to_registers(1000),
        42019: uint32_to_registers(0),
        42021: uint32_to_registers(110_000),
        42023: uint32_to_registers(100_000),
        42025: uint32_to_registers(110_000),
        42027: uint32_to_registers(100_000),
        42029: [0],
        42030: uint32_to_registers(0),
        42032: [0],
        42037: [0],
        42040: [0],
        42041: [0],
        42042: [0],
        42043: [0],
        42046: [0],
        42047: [0],
        42048: [0],
        42049: [0],
        42050: [0],
        42051: [0],
        42052: [0],
        42053: [0],
        42054: [0],
        42055: [0],
        42061: [1],
        42062: [0],
        42063: [0],
        42064: [1],
        42065: [0],
        42066: [0],
        42067: [0],
        42069: [0],
        42070: [0],
        42072: [60],
        42073: [0],
        42074: [0],
    }

    def add_regs(address: int, regs: list[int]) -> None:
        blocks.append(
            SimData(
                address=address,
                values=[signed_register_word(reg) for reg in regs],
                datatype=DataType.REGISTERS,
            )
        )

    def add_string(address: int, value: str, register_count: int) -> None:
        blocks.append(
            SimData(
                address=address,
                values=to_fixed_string(value, register_count),
                datatype=DataType.STRING,
            )
        )

    add_string(30000, "SUN2000-100KTL-M2", 15)
    add_string(30015, "TEST123456789", 10)
    add_string(30025, "PN-100KTL", 10)
    add_string(30035, "V100R001C00SPC138", 15)
    add_string(30050, "APPV100R001", 15)
    add_regs(30068, uint32_to_registers(3))
    add_regs(30070, [150])
    add_regs(30071, [20])
    add_regs(30072, [10])
    add_regs(30073, uint32_to_registers(100_000))
    add_regs(30075, uint32_to_registers(110_000))
    add_regs(30077, uint32_to_registers(110_000))
    add_regs(30079, int32_to_registers(50_000))
    add_regs(30081, int32_to_registers(-50_000))
    for address, regs in sorted(writable_defaults.items()):
        add_regs(address, regs)

    state = SolarState()
    dynamic_registers = state.snapshot()
    for address, regs in sorted(dynamic_registers.items()):
        add_regs(address, regs)

    identity = ModbusDeviceIdentification()
    identity.VendorName = "Huawei"
    identity.ProductCode = "SUN2000"
    identity.ProductName = "Huawei Inverter Simulator"
    identity.ModelName = "SUN2000-100KTL-M2"
    identity.MajorMinorRevision = "1.0"

    async def action(function_code, start_address, address, count, current_registers, set_values):
        del function_code, start_address, count, set_values
        latest = state.snapshot()
        if address not in latest:
            return None
        regs = latest[address]
        for idx, value in enumerate(regs):
            if idx < len(current_registers):
                current_registers[idx] = value
        return None

    return SimDevice(id=0, simdata=blocks, identity=identity, action=action)


async def main() -> None:
    device = build_device()
    print("Starting Huawei inverter simulator on port 5020...", flush=True)
    server = ModbusTcpServer(device, address=("0.0.0.0", 5020))
    await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
