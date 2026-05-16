#!/usr/bin/env python3
"""
InfluxDB Connection Test Script
Tests connection to your InfluxDB Cloud instance
"""

import os
from datetime import datetime, timezone
from config import config
from influxdb_writer import TelemetryPoint, influxdb_writer

async def test_influxdb_connection():
    """Test InfluxDB connection and write a sample data point"""
    print("🔌 Testing InfluxDB Connection...")
    print(f"   URL: {config.influxdb.url}")
    print(f"   Org: {config.influxdb.org}")
    print(f"   Bucket: {config.influxdb.bucket}")
    print(f"   Measurement: {config.influxdb.measurement}")
    print()
    
    try:
        # Test connection
        print("1. Testing connection...")
        connected = await influxdb_writer.connect()
        if not connected:
            print("❌ Failed to connect to InfluxDB")
            return False
        
        print("✅ Connected to InfluxDB successfully!")
        
        # Test health check
        print("\n2. Checking InfluxDB health...")
        health = await influxdb_writer.health_check()
        print(f"   Status: {health.get('status', 'unknown')}")
        if health.get('error'):
            print(f"   Error: {health['error']}")
        
        # Test write operation with sample data
        print("\n3. Testing write operation with sample solar data...")
        
        # Create a test telemetry point (simulating solar inverter data)
        test_point = TelemetryPoint(
            timestamp=datetime.now(timezone.utc),
            device_id=config.exporter.device_id,
            site_id=config.exporter.site_id,
            measurements={
                "active_power": 25.0,                       # 25.0 kW
                "reactive_power": 1.5,                     # 1.5 kVar
                "phase_A_voltage": 230.5,                  # 230.5 V
                "phase_B_voltage": 229.8,                  # 229.8 V
                "phase_C_voltage": 231.2,                  # 231.2 V
                "phase_A_current": 36.2,                   # 36.2 A
                "phase_B_current": 35.8,                   # 35.8 A
                "phase_C_current": 36.5,                   # 36.5 A
                "power_factor": 0.95,                      # 0.95
                "grid_frequency": 50.0,                    # 50.0 Hz
                "cumulative_generated_electricity": 123456.78,  # kWh
                "highest_priority_alarm_code": 0           # No alarms
            },
            device_info={
                "model": "SUN2000-100KTL-M2",
                "serial_number": "TEST123456789",
                "firmware_version": "V100R001C00SPC138"
            }
        )
        
        # Write the test point
        success = await influxdb_writer.write_single_point(test_point)
        
        if success:
            print("✅ Successfully wrote test data to InfluxDB!")
            print(f"   Device ID: {config.exporter.device_id}")
            print(f"   Site ID: {config.exporter.site_id}")
            print("   Sample metrics: active_power=25.0kW, phase_A_voltage=230.5V")
        else:
            print("❌ Failed to write test data")
            return False
            
        print("\n🎉 InfluxDB connection test completed successfully!")
        print("\nYou can now:")
        print("1. Check your InfluxDB Cloud dashboard for the test data")
        print("2. Start the simulator: python iot_driver_copilot/huawei_sun_2000_solar_inverter/simulator.py")
        print("3. Start the exporter: python main.py")
        
        await influxdb_writer.disconnect()
        return True
        
    except Exception as e:
        print(f"❌ Error during InfluxDB test: {e}")
        return False

if __name__ == "__main__":
    import asyncio
    asyncio.run(test_influxdb_connection())
