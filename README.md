# Huawei SUN2000-100KTL Solar Inverter Exporter

A comprehensive data exporter for Huawei SUN2000 solar inverters that collects telemetry data via Modbus TCP and exports it to InfluxDB for time-series analysis and visualization.

## Features

- **Real-time Data Collection**: Collects solar inverter telemetry data via Modbus TCP
- **Batch Upload**: Efficient batch uploading to InfluxDB cloud databases
- **RESTful API**: Provides REST endpoints for device information, telemetry, and control
- **Health Monitoring**: Built-in health checks and status monitoring
- **Configurable**: Environment-based configuration for easy deployment
- **Docker Ready**: Docker and Docker Compose support for easy deployment on Raspberry Pi
- **Simulator Included**: Built-in simulator for testing without physical hardware
- **Error Handling**: Robust error handling with retry logic and connection recovery

## Architecture

```
[Huawei SUN2000 Inverter] <-- Modbus TCP --> [Exporter] <-- HTTPS --> [InfluxDB Cloud]
                                                 |
                                                 v
                                            [REST API] <-- HTTP --> [Dashboard]
```

## Quick Start

### Using Docker Compose (Recommended for Testing)

1. Clone the repository:
```bash
git clone <your-repo>
cd huawei100ktl_exporter
```

2. Start the complete stack with simulator:
```bash
docker-compose up -d
```

3. Check health status:
```bash
curl http://localhost:8080/health
```

4. View telemetry data:
```bash
curl http://localhost:8080/telemetry
```

### Production Deployment on Raspberry Pi

1. Copy `.env.example` to `.env` and configure:
```bash
cp .env.example .env
# Edit .env with your settings
```

2. Build and run:
```bash
docker build -t huawei-exporter .
docker run -d --name huawei-exporter \
  --env-file .env \
  -p 8080:8080 \
  huawei-exporter
```

### Local Development

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Set environment variables or create `.env` file

3. Run the simulator (in separate terminal):
```bash
python iot_driver_copilot/huawei_sun_2000_solar_inverter/simulator.py
```

4. Run the exporter:
```bash
python main.py
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SUN2000_MODBUS_HOST` | `127.0.0.1` | Modbus TCP host (inverter IP) |
| `SUN2000_MODBUS_PORT` | `502` | Modbus TCP port |
| `SUN2000_MODBUS_UNIT_ID` | `1` | Modbus unit ID |
| `SUN2000_MODBUS_TIMEOUT` | `5.0` | Modbus timeout (seconds) |
| `HTTP_HOST` | `0.0.0.0` | HTTP server bind address |
| `HTTP_PORT` | `8080` | HTTP server port |
| `INFLUXDB_URL` | - | InfluxDB URL (required) |
| `INFLUXDB_TOKEN` | - | InfluxDB authentication token (required) |
| `INFLUXDB_ORG` | `solar` | InfluxDB organization |
| `INFLUXDB_BUCKET` | `inverters` | InfluxDB bucket name |
| `INFLUXDB_MEASUREMENT` | `huawei_sun2000` | InfluxDB measurement name |
| `DEVICE_ID` | `inverter_001` | Unique device identifier |
| `SITE_ID` | `site_001` | Site identifier |
| `COLLECTION_INTERVAL` | `60` | Data collection interval (seconds) |
| `BATCH_SIZE` | `10` | Batch size for uploads |
| `RETRY_ATTEMPTS` | `3` | Number of retry attempts |
| `RETRY_DELAY` | `5` | Delay between retries (seconds) |

## API Endpoints

### Core Endpoints

- `GET /health` - Service health check
- `GET /config` - Current configuration (sensitive data redacted)
- `GET /device` - Device information (model, serial, firmware, etc.)
- `GET /telemetry` - Real-time telemetry data
- `PUT /control` - Send control commands to inverter

### Data Collector Management

- `GET /collector/status` - Data collector status and metrics
- `POST /collector/start` - Start data collector
- `POST /collector/stop` - Stop data collector
- `POST /collector/upload` - Force upload buffered data

### Example API Usage

```bash
# Get all telemetry data
curl http://localhost:8080/telemetry

# Get specific metrics
curl "http://localhost:8080/telemetry?metrics=active_power&metrics=voltage_L1"

# Check service health
curl http://localhost:8080/health

# Get data collector status
curl http://localhost:8080/collector/status

# Force upload buffered data
curl -X POST http://localhost:8080/collector/upload
```

## Telemetry Data

The exporter collects the following metrics:

- **Power**: Active power, reactive power, power factor
- **Voltage**: Three-phase voltages (L1, L2, L3)
- **Current**: Three-phase currents (L1, L2, L3)
- **Frequency**: Grid frequency
- **Energy**: Total energy production
- **Alarms**: Alarm codes from inverter

## InfluxDB Data Structure

```
measurement: huawei_sun2000
tags:
  - device_id: inverter_001
  - site_id: site_001
  - device_model: SUN2000-100KTL
  - device_serial_number: ABC123
fields:
  - active_power: 85000.0 (W)
  - reactive_power: 1000.0 (VAR)
  - voltage_L1: 230.5 (V)
  - current_L1: 45.2 (A)
  - frequency: 50.0 (Hz)
  - total_energy: 1234567 (Wh)
  - alarm_codes: 0
timestamp: 2024-10-04T12:00:00Z
```

## Deployment on Raspberry Pi

### Prerequisites

- Raspberry Pi 3B+ or 4 (recommended)
- Raspberry Pi OS (64-bit recommended)
- Docker installed
- Network access to inverter and internet (for InfluxDB Cloud)

### Installation Steps

1. **Install Docker on Raspberry Pi:**
```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER
# Log out and back in
```

2. **Clone and configure:**
```bash
git clone <your-repo>
cd huawei100ktl_exporter
cp .env.example .env
# Edit .env with your configuration
```

3. **Build and run:**
```bash
docker build -t huawei-exporter .
docker run -d --name huawei-exporter \
  --restart unless-stopped \
  --env-file .env \
  -p 8080:8080 \
  huawei-exporter
```

4. **Enable auto-start:**
```bash
docker update --restart unless-stopped huawei-exporter
```

### Monitoring

- Check status: `docker logs huawei-exporter`
- Health check: `curl http://localhost:8080/health`
- Restart: `docker restart huawei-exporter`

## InfluxDB Cloud Setup

1. Sign up for InfluxDB Cloud at https://cloud2.influxdata.com/
2. Create an organization and bucket
3. Generate an API token with write permissions
4. Configure the exporter with your InfluxDB settings

## Grafana Dashboard

Sample Grafana queries for visualization:

```flux
// Active Power over time
from(bucket: "inverters")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "huawei_sun2000")
  |> filter(fn: (r) => r._field == "active_power")
  |> filter(fn: (r) => r.device_id == "inverter_001")

// Daily Energy Production
from(bucket: "inverters")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "huawei_sun2000")
  |> filter(fn: (r) => r._field == "total_energy")
  |> aggregateWindow(every: 1d, fn: max, createEmpty: false)
  |> difference()
```

## Troubleshooting

### Common Issues

1. **Connection to inverter fails**:
   - Check IP address and port
   - Verify Modbus TCP is enabled on inverter
   - Check network connectivity

2. **InfluxDB upload fails**:
   - Verify token and permissions
   - Check internet connectivity
   - Review InfluxDB URL format

3. **High memory usage**:
   - Reduce collection interval
   - Decrease batch size
   - Check for connection issues causing buffer buildup

### Debug Mode

Run with debug logging:
```bash
LOG_LEVEL=DEBUG python main.py
```

## Development

### Running Tests

```bash
# Start simulator
python iot_driver_copilot/huawei_sun_2000_solar_inverter/simulator.py &

# Run exporter in test mode
SUN2000_MODBUS_HOST=localhost python main.py
```

### Contributing

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Submit a pull request

## License

MIT License - see LICENSE file for details

## Support

For issues and questions:
- Check the troubleshooting section
- Review logs: `docker logs huawei-exporter`
- Open an issue on GitHub
