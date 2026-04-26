# Local Simulator To Cloud Test

Date verified: April 26, 2026

## Purpose

Use this flow to validate the exporter locally without Docker and without a real inverter.

This path was verified on April 26, 2026 with:

- local Python virtualenv
- simulator process on the host
- exporter process on the host
- real InfluxDB cloud connection from `.env`

## What This Tests

This flow validates:

- local Modbus polling against the built-in simulator
- exporter startup and FastAPI endpoints
- immediate collector run on startup
- upload path to the configured InfluxDB cloud bucket

This does not prove:

- real inverter register fidelity
- edge boot behavior
- `systemd` deployment behavior
- WAN outage recovery on the edge device

## Prerequisites

- Python 3 available locally
- valid InfluxDB settings in `.env`
- `.env` configured for simulator mode:
  - `SUN2000_MODBUS_HOST=127.0.0.1`
  - `SUN2000_MODBUS_PORT=5020`

The repo already uses `.env` for local development through `python-dotenv`.

## One-Time Setup

From the exporter repo root:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Start The Simulator

In terminal 1:

```bash
.venv/bin/python iot_driver_copilot/huawei_sun_2000_solar_inverter/simulator.py
```

Expected signal:

- `Starting Huawei inverter simulator on port 5020...`

## Start The Exporter

In terminal 2:

```bash
.venv/bin/python main.py
```

Expected signals on startup:

- Modbus host is `127.0.0.1:5020`
- InfluxDB connection succeeds
- device info is collected
- one batch upload succeeds immediately

Typical healthy startup log lines:

- `Connected to Modbus device`
- `Connected to InfluxDB`
- `Successfully wrote 1 points to InfluxDB`
- `Data collector started successfully`

## Verify The Exporter

Run these from a third terminal:

```bash
curl http://127.0.0.1:8080/live
curl http://127.0.0.1:8080/ready
curl http://127.0.0.1:8080/collector/status
curl "http://127.0.0.1:8080/telemetry?metrics=active_power&metrics=grid_frequency&metrics=cumulative_generated_electricity"
```

Healthy signals:

- `/live` returns `status=alive`
- `/ready` returns HTTP `200`
- `/collector/status` shows:
  - `running=true`
  - `ready=true`
  - recent `last_successful_collection_at`
  - recent `last_successful_upload_at`
  - `consecutive_collection_failures=0`
  - `consecutive_upload_failures=0`
- `/telemetry` returns plausible non-null values

## What Was Verified In This Repo

This exact host-native path was verified on April 26, 2026:

- simulator stayed up on port `5020`
- exporter connected to simulator successfully
- exporter connected to configured InfluxDB cloud successfully
- exporter uploaded at least one startup batch successfully
- `/ready` returned healthy
- `/collector/status` showed successful collection and upload

Example telemetry returned during verification:

- `active_power`: about `72-79 kW`
- `grid_frequency`: about `49.8-49.9 Hz`
- `cumulative_generated_electricity`: about `5234.56 kWh`

## Known Limitation

The simulator is good enough for exporter-to-cloud validation, but it is not a perfect inverter emulation.

During verification:

- collector-side device metadata decoded correctly
- direct `/device` model string still showed a simulator formatting artifact

That means:

- use this simulator for ingestion-path validation
- do not use it as the final authority for protocol-perfect string fields
- still perform real inverter validation before deployment sign-off

## Fastest Test Sequence

If you only want the shortest proof:

1. start simulator
2. start exporter
3. call `/ready`
4. call `/collector/status`
5. confirm exporter logs show a successful Influx write

## If It Fails

Check in this order:

1. `.venv` exists and dependencies installed
2. `.env` exists and points Modbus to `127.0.0.1:5020`
3. simulator is running
4. InfluxDB URL, token, org, and bucket are valid
5. nothing else is already bound to port `8080` or `5020`

Useful checks:

```bash
lsof -nP -iTCP:5020 -sTCP:LISTEN
lsof -nP -iTCP:8080 -sTCP:LISTEN
```

## Recommended Use

Use this path for:

- pre-deployment exporter smoke tests
- schema and dashboard-contract validation
- cloud write-path regression checks

Do not treat it as a substitute for:

- live inverter commissioning
- edge power-cycle testing
- cellular backhaul validation
