from pymodbus.server import StartTcpServer
from pymodbus.datastore import ModbusServerContext, ModbusSequentialDataBlock, ModbusDeviceContext
from pymodbus import ModbusDeviceIdentification
import threading
import time
import math
import random
import struct
from datetime import datetime

# Realistic solar inverter simulation parameters
class SolarSimulator:
    def __init__(self):
        self.rated_power = 100000  # 100kW inverter
        self.base_voltage = 400    # 400V three-phase
        self.base_frequency = 50.0 # 50Hz grid
        self.total_energy = 5234567  # Starting total energy in Wh
        self.daily_energy = 0      # Reset daily
        self.monthly_energy = 12500 # kWh this month so far
        self.yearly_energy = 125000 # kWh this year so far
        self.last_energy_update = time.time()
        self.startup_time = int(time.time() - 3600)  # Started 1 hour ago
        self.num_strings = 10      # Number of PV strings
        self.cabinet_temp = 35     # Cabinet temperature in °C
        self.efficiency = 98     # Inverter efficiency %
        
    def get_solar_irradiance_factor(self):
        """Simulate solar irradiance based on time of day (0.0 to 1.0)"""
        now = datetime.now()
        hour = now.hour + now.minute / 60.0
        
        # No solar at night (6PM - 6AM)
        if hour < 6 or hour > 18:
            return 0.0
        
        # Peak solar around noon (12PM)
        # Bell curve from 6AM to 6PM
        normalized_hour = (hour - 6) / 12  # 0 to 1 from 6AM to 6PM
        irradiance = math.sin(normalized_hour * math.pi)  # Bell curve
        
        # Add some cloud variation (0.7 to 1.0 of theoretical max)
        cloud_factor = 0.7 + random.random() * 0.3
        
        return irradiance * cloud_factor
    
    def get_active_power(self):
        """Get realistic active power based on solar conditions"""
        irradiance = self.get_solar_irradiance_factor()
        # Scale by rated power with some efficiency losses
        efficiency = 0.95  # 95% inverter efficiency
        power = int(irradiance * self.rated_power * efficiency)
        
        # Add small random variations (±2%)
        variation = power * 0.02 * (random.random() - 0.5) * 2
        return max(0, int(power + variation))
    
    def get_reactive_power(self, active_power):
        """Get realistic reactive power (usually small for good PF)"""
        if active_power == 0:
            return 0
        # Reactive power typically 5-15% of active power
        reactive_ratio = 0.05 + random.random() * 0.10  # 5-15%
        return int(active_power * reactive_ratio)
    
    def get_voltages(self):
        """Get realistic three-phase voltages with small variations"""
        base = self.base_voltage
        # Grid voltage variations: ±5%
        l1 = base + random.uniform(-base*0.05, base*0.05)
        l2 = base + random.uniform(-base*0.05, base*0.05) 
        l3 = base + random.uniform(-base*0.05, base*0.05)
        return int(l1 * 100), int(l2 * 100), int(l3 * 100)  # Scale by 100
    
    def get_currents(self, active_power, voltages):
        """Calculate realistic currents from power and voltages"""
        if active_power == 0:
            return 0, 0, 0
        
        # I = P / (sqrt(3) * V * PF) for three-phase
        v_avg = sum(v/100 for v in voltages) / 3  # Average voltage
        power_factor = 0.95 + random.random() * 0.04  # 0.95-0.99
        current_total = active_power / (math.sqrt(3) * v_avg * power_factor)
        
        # Distribute current across phases (with small imbalances)
        i1 = current_total * (0.9 + random.random() * 0.2)  # ±10% imbalance
        i2 = current_total * (0.9 + random.random() * 0.2)
        i3 = current_total * (0.9 + random.random() * 0.2)
        
        return int(i1 * 100), int(i2 * 100), int(i3 * 100)  # Scale by 100
    
    def get_power_factor(self, active_power, reactive_power):
        """Calculate power factor from active and reactive power"""
        if active_power == 0:
            return 1000  # PF = 1.0 when no power
        
        apparent_power = math.sqrt(active_power**2 + reactive_power**2)
        pf = active_power / apparent_power
        return int(pf * 1000)  # Scale by 1000
    
    def get_frequency(self):
        """Get grid frequency with small variations"""
        # Grid frequency variations: 49.8-50.2 Hz
        freq = self.base_frequency + random.uniform(-0.2, 0.2)
        return int(freq * 100)  # Scale by 100
    
    def update_total_energy(self, active_power):
        """Update cumulative energy production"""
        current_time = time.time()
        time_delta = current_time - self.last_energy_update  # seconds
        
        # Add energy: Power (W) * time (hours) = Wh
        energy_increment = active_power * (time_delta / 3600)
        self.total_energy += int(energy_increment)
        self.last_energy_update = current_time
        
        return self.total_energy

# Create simulator instance
solar_sim = SolarSimulator()

# Define initial Modbus register values with proper addresses
registers = {
    # Device information registers (30000 range)
    30000: 0x5355,  # 'SU' - Model name part 1
    30001: 0x4E32,  # 'N2' - Model name part 2  
    30002: 0x3030,  # '00' - Model name part 3
    30003: 0x3030,  # '00' - Model name part 4
    30004: 0x2D31,  # '-1' - Model name part 5
    30005: 0x3030,  # '00' - Model name part 6
    30006: 0x4B54,  # 'KT' - Model name part 7
    30007: 0x4C00,  # 'L\0' - Model name part 8
    
    30015: 0x5445,  # 'TE' - Serial number part 1
    30016: 0x5354,  # 'ST' - Serial number part 2
    30017: 0x3132,  # '12' - Serial number part 3
    30018: 0x3334,  # '34' - Serial number part 4
    30019: 0x3536,  # '56' - Serial number part 5
    
    30073: 100000,  # Rated Power (100kW) - low word
    30074: 0,       # High word of rated power
    
    # Telemetry registers (32000 range) - will be updated by simulator
    32066: 40000,   # Voltage L1 (400.0V * 100)
    32067: 40000,   # Voltage L2 (400.0V * 100) 
    32068: 40000,   # Voltage L3 (400.0V * 100)
    32069: 0,       # Current L1 (calculated)
    32070: 0,       # Current L2 (calculated)
    32071: 0,       # Current L3 (calculated)
    32080: 0,       # Active Power (calculated)
    32081: 0,       # High word of active power
    32082: 0,       # Reactive Power (calculated)
    32083: 0,       # High word of reactive power
    32084: 1000,    # Power Factor (1.0 * 1000)
    32085: 5000,    # Frequency (50Hz * 100)
    32090: 0,       # Alarm codes
    32106: 5234567, # Total Energy (low word)
    32107: 0,       # Total Energy (high word)
}

# Create a Modbus data store with 65536 holding registers to support full address range
hr_block = ModbusSequentialDataBlock(0, [0]*65536)
for addr, val in registers.items():
    hr_block.setValues(addr, [val])

# Create proper datastore context for pymodbus 3.x
device_context = ModbusDeviceContext(
    di=ModbusSequentialDataBlock(0, [0]*100),  # Discrete inputs
    co=ModbusSequentialDataBlock(0, [0]*100),  # Coils
    hr=hr_block,                                # Holding registers
    ir=ModbusSequentialDataBlock(0, [0]*100)   # Input registers
)
context = ModbusServerContext(devices=device_context, single=True)

# Add comprehensive telemetry support
class ComprehensiveTelemetrySimulator:
    def __init__(self, base_simulator):
        self.base_sim = base_simulator
        self.startup_time = int(time.time() - 3600)  # Started 1 hour ago
        self.device_state = 0x8000  # Normal operation state
        
    def split_32bit_value(self, value, signed=True):
        """Split a 32-bit value into two 16-bit registers (big endian)"""
        if signed:
            if value < 0:
                value = value & 0xFFFFFFFF
            packed = struct.pack('>i', value)
        else:
            packed = struct.pack('>I', value & 0xFFFFFFFF)
        
        high_word, low_word = struct.unpack('>HH', packed)
        return [high_word, low_word]
    
    def generate_pv_string_data(self, string_num, active_power):
        """Generate realistic PV string data"""
        if active_power == 0 or string_num > 16:
            return {'voltage': 0, 'current': 0}
        
        base_voltage = 450 + random.uniform(-50, 100)
        power_per_string = active_power / 16
        string_current = power_per_string / base_voltage if base_voltage > 0 else 0
        
        voltage_variation = random.uniform(-0.1, 0.1)
        current_variation = random.uniform(-0.15, 0.15)
        
        voltage = base_voltage * (1 + voltage_variation)
        current = string_current * (1 + current_variation)
        
        return {
            'voltage': int(voltage * 10),
            'current': int(current * 100)
        }
    
    def update_all_registers(self, hr_block):
        """Update all V3.0 telemetry registers"""
        # Get base telemetry
        active_power = self.base_sim.get_active_power()
        reactive_power = self.base_sim.get_reactive_power(active_power)
        
        # Get enhanced electrical parameters
        voltages = self.get_enhanced_voltages()
        currents = self.get_enhanced_currents(active_power, voltages)
        power_factor = self.base_sim.get_power_factor(active_power, reactive_power)
        frequency = self.base_sim.get_frequency()
        total_energy = self.base_sim.update_total_energy(active_power)
        
        # System status registers
        hr_block.setValues(32000, [3])  # inverter_state (Running)
        hr_block.setValues(32089, [self.device_state])  # device_state
        
        # Alarm registers
        hr_block.setValues(32008, [0])  # alarm_1
        hr_block.setValues(32009, [0])  # alarm_2
        hr_block.setValues(32010, [0])  # alarm_3
        hr_block.setValues(32090, [0])  # highest_priority_alarm_code
        
        # Timestamp registers
        startup_regs = self.split_32bit_value(self.startup_time, signed=False)
        hr_block.setValues(32091, startup_regs)  # startup_time
        hr_block.setValues(32093, [0, 0])       # shutdown_time (not shut down)
        
        # Alarm count registers
        hr_block.setValues(32151, [0])  # critical_alarms
        hr_block.setValues(32152, [0])  # major_alarms
        hr_block.setValues(32153, [0])  # minor_alarms
        hr_block.setValues(32154, [0])  # warning_alarms
        
        # Grid voltage registers (line-to-line)
        hr_block.setValues(32064, self.split_32bit_value(voltages['l1_l2']))
        hr_block.setValues(32066, self.split_32bit_value(voltages['l2_l3']))
        hr_block.setValues(32068, self.split_32bit_value(voltages['l3_l1']))
        
        # Phase voltage registers
        hr_block.setValues(32070, self.split_32bit_value(voltages['phase_a']))
        hr_block.setValues(32072, self.split_32bit_value(voltages['phase_b']))
        hr_block.setValues(32074, self.split_32bit_value(voltages['phase_c']))
        
        # Phase current registers
        hr_block.setValues(32076, self.split_32bit_value(currents['phase_a']))
        hr_block.setValues(32078, self.split_32bit_value(currents['phase_b']))
        hr_block.setValues(32080, self.split_32bit_value(currents['phase_c']))
        
        # Power registers
        hr_block.setValues(32082, self.split_32bit_value(int(active_power)))  # active_power (kW * 1000)
        hr_block.setValues(32084, self.split_32bit_value(int(reactive_power)))  # reactive_power (kVar * 1000)
        hr_block.setValues(32086, self.split_32bit_value(power_factor, signed=True))  # power_factor
        hr_block.setValues(32088, self.split_32bit_value(frequency, signed=True))     # grid_frequency
        
        # Energy registers
        energy_kwh = int(total_energy * 0.01)  # Convert Wh to kWh with 0.01 resolution
        hr_block.setValues(32106, self.split_32bit_value(energy_kwh, signed=False))  # cumulative_generated_electricity
        hr_block.setValues(32114, self.split_32bit_value(int(50), signed=False))     # daily_generated_electricity
        hr_block.setValues(32116, self.split_32bit_value(int(1250), signed=False))   # monthly_generated_electricity
        hr_block.setValues(32118, self.split_32bit_value(int(12500), signed=False))  # yearly_generated_electricity
        
        # Previous period energy
        hr_block.setValues(32158, self.split_32bit_value(int(5000), signed=False))   # previous_hour
        hr_block.setValues(32162, self.split_32bit_value(int(40000), signed=False))  # previous_day
        hr_block.setValues(32166, self.split_32bit_value(int(120000), signed=False)) # previous_month
        hr_block.setValues(32170, self.split_32bit_value(int(1000000), signed=False)) # previous_year
        
        # PV string registers (first 4 strings)
        for i in range(1, 5):
            pv_data = self.generate_pv_string_data(i, active_power)
            voltage_addr = 32016 + (i - 1) * 4
            current_addr = voltage_addr + 2
            hr_block.setValues(voltage_addr, self.split_32bit_value(pv_data['voltage'], signed=True))
            hr_block.setValues(current_addr, self.split_32bit_value(pv_data['current'], signed=True))
        
        # PV32 string (as example of higher numbered string)
        pv32_data = self.generate_pv_string_data(32, active_power)
        hr_block.setValues(32142, self.split_32bit_value(pv32_data['voltage'], signed=True))
        hr_block.setValues(32144, self.split_32bit_value(pv32_data['current'], signed=True))
        
        # Other parameters
        efficiency = int(95.5 * 100)  # 95.5% efficiency
        cabinet_temp = int((35 + (active_power / 100000) * 10) * 10)  # Temperature based on load
        dc_power = int(active_power / 0.955)  # DC power slightly higher than AC
        
        hr_block.setValues(32108, self.split_32bit_value(dc_power, signed=False))  # total_dc_input_power
        hr_block.setValues(32110, self.split_32bit_value(efficiency, signed=False))  # efficiency
        hr_block.setValues(32112, self.split_32bit_value(cabinet_temp, signed=True))  # cabinet_temperature
        
        return active_power, total_energy
    
    def get_enhanced_voltages(self):
        """Get enhanced voltage data for all measurement points"""
        base = 400  # 400V three-phase system
        variations = [random.uniform(-base*0.05, base*0.05) for _ in range(3)]
        
        # Line-to-line voltages
        l1_l2 = int((base + variations[0]) * 10)
        l2_l3 = int((base + variations[1]) * 10) 
        l3_l1 = int((base + variations[2]) * 10)
        
        # Phase voltages (approximately line voltage / sqrt(3))
        phase_base = base / math.sqrt(3)
        phase_a = int((phase_base + variations[0] / math.sqrt(3)) * 10)
        phase_b = int((phase_base + variations[1] / math.sqrt(3)) * 10)
        phase_c = int((phase_base + variations[2] / math.sqrt(3)) * 10)
        
        return {
            'l1_l2': l1_l2, 'l2_l3': l2_l3, 'l3_l1': l3_l1,
            'phase_a': phase_a, 'phase_b': phase_b, 'phase_c': phase_c
        }
    
    def get_enhanced_currents(self, active_power, voltages):
        """Get enhanced current data for all phases"""
        if active_power == 0:
            return {'phase_a': 0, 'phase_b': 0, 'phase_c': 0}
        
        # Calculate average phase voltage
        v_avg = (voltages['phase_a'] + voltages['phase_b'] + voltages['phase_c']) / (3 * 10)
        power_factor = 0.95 + random.random() * 0.04
        current_total = active_power / (math.sqrt(3) * v_avg * power_factor)
        
        # Distribute with imbalances
        i_a = int(current_total * (0.9 + random.random() * 0.2) * 1000)
        i_b = int(current_total * (0.9 + random.random() * 0.2) * 1000)
        i_c = int(current_total * (0.9 + random.random() * 0.2) * 1000)
        
        return {'phase_a': i_a, 'phase_b': i_b, 'phase_c': i_c}

# Create comprehensive telemetry simulator
comprehensive_sim = ComprehensiveTelemetrySimulator(solar_sim)

# Background thread to simulate realistic solar inverter operation with V3.0 telemetry
def update_solar_data():
    """Update all V3.0 telemetry registers with realistic solar inverter data"""
    while True:
        try:
            # Update all registers using comprehensive simulator
            active_power, total_energy = comprehensive_sim.update_all_registers(hr_block)
            
            # Print current status for debugging
            current_time = datetime.now().strftime('%H:%M:%S')
            irradiance = solar_sim.get_solar_irradiance_factor()
            print(f"[{current_time}] V3.0 Telemetry: {active_power:,}W, Irradiance: {irradiance:.1%}, " 
                  f"Energy: {total_energy:,}Wh, State: Running, Alarms: 0", flush=True)
            
        except Exception as e:
            print(f"Error updating V3.0 solar data: {e}", flush=True)
        
        # Update every 10 seconds for comprehensive telemetry
        time.sleep(10)

# Start realistic solar simulation
thread = threading.Thread(target=update_solar_data)
thread.daemon = True
thread.start()

# Identification (optional)
identity = ModbusDeviceIdentification()
identity.VendorName = 'Huawei'
identity.ProductCode = 'SUN2000-100KTL'
identity.VendorUrl = 'https://e.huawei.com'
identity.ProductName = 'Huawei Inverter Simulator'
identity.ModelName = 'SUN2000-100KTL'
identity.MajorMinorRevision = '1.0'

# Start Modbus TCP server on port 5020
print("Starting Huawei inverter simulator on port 5020...", flush=True)
StartTcpServer(context, identity=identity, address=("0.0.0.0", 5020))
