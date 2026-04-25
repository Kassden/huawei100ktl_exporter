#!/usr/bin/env python3
"""
Huawei SUN2000 Solar Inverter Exporter
Main application entry point with InfluxDB integration
"""

import logging
import uvicorn
from config import config

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

def main():
    """Main application entry point"""
    logger.info("Starting Huawei SUN2000 Solar Inverter Exporter")
    logger.info(f"Configuration loaded:")
    logger.info(f"  Modbus: {config.modbus.host}:{config.modbus.port}")
    logger.info(f"  HTTP Server: {config.http.host}:{config.http.port}")
    logger.info(f"  InfluxDB: {config.influxdb.url} (org: {config.influxdb.org})")
    logger.info(f"  Collection Interval: {config.exporter.collection_interval}s")
    logger.info(f"  Device ID: {config.exporter.device_id}")
    logger.info(f"  Site ID: {config.exporter.site_id}")
    
    # Import the FastAPI app from the driver module
    from iot_driver_copilot.huawei_sun_2000_solar_inverter.driver import app
    
    # Run the server
    uvicorn.run(
        app,
        host=config.http.host,
        port=config.http.port,
        log_level="info",
        access_log=True
    )

if __name__ == "__main__":
    main()
