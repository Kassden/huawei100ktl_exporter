# Huawei SUN2000-100KTL-M2 Exporter

RTU-first Huawei SUN2000 exporter for the `SUN2000-100KTL-M2`, with:

- Modbus `RTU` and `TCP` support
- InfluxDB batch upload
- HTTP health and telemetry endpoints
- full device and telemetry readout
- remote control plus validated remote settings writes

This repo is now documented from the live code rather than the deleted `docs/` bundle.

## Fit for the 100KTL-M2

This exporter is currently aligned to the live register maps in:

- `modbus_client.py`
- `iot_driver_copilot/huawei_sun_2000_solar_inverter/driver.py`
- `config.py`

For the current codebase, that means:

- `81` telemetry fields
- `14` device information fields
- `10` remote control commands
- `63` readable settings

It is suitable for a `SUN2000-100KTL-M2` over `RTU` and keeps `TCP` for simulator use and alternative network paths.

## Why the old docs were removed

The previous documentation set was no longer trustworthy because it mixed older assumptions with a newer exporter:

- it was TCP-first, while the real target here is RTU-first
- it referenced deleted paths like `docs/local-simulator-cloud-test.md`
- it used stale field names such as `voltage_L1`, `frequency`, `total_energy`, and `alarm_codes`
- it spread commissioning guidance across many files that no longer matched the live API surface

This `README.md` is now the single source of truth for operators and integrators.

## Current capabilities

**Read paths**

- `GET /device` reads all `14` device fields
- `GET /telemetry` reads all `81` telemetry fields
- `GET /settings` reads all `63` settings
- `GET /control/catalog` and `GET /settings/catalog` expose live register metadata

**Write paths**

- `PUT /control` supports `10` validated control commands
- `PUT /settings` supports validated settings writes when `EXPORTER_ENABLE_CONTROL=true`
- high-risk grid-profile and raw-curve writes are intentionally blocked by default

**Collector paths**

- `/live`
- `/ready`
- `/health`
- `/collector/status`
- `/collector/start`
- `/collector/stop`
- `/collector/upload`

## Recommended operating mode

Use `RTU` for the real inverter.

Recommended starting point for a `SUN2000-100KTL-M2`:

- Modbus transport: `rtu`
- slave / unit ID: `1`
- baudrate: `9600`
- parity: `N`
- bytesize: `8`
- stopbits: `1`

That profile is captured in `.env.rtu.example`.

If you are using a Raspberry Pi 4B with a Waveshare `RS485 CAN HAT`, treat it as a GPIO-UART device, not a USB serial adapter:

- prefer `SUN2000_SERIAL_PORT=/dev/serial0`
- use `/dev/ttyUSB0` only if you switch back to a USB-RS485 dongle
- free the primary UART from Bluetooth so the HAT gets an OS-visible serial device

## Quick start

### Recommended Pi deployment flow

1. Copy the local Pi template:

```bash
cp .env.pi4b.local.example .env.pi4b.local
```

2. Fill in:

- `SUN2000_SERIAL_PORT`
- `INFLUXDB_URL`
- `INFLUXDB_TOKEN`
- `INFLUXDB_ORG`
- `INFLUXDB_BUCKET`
- `DEVICE_ID`
- `SITE_ID`

3. Copy the repo to the Pi. The installer will automatically use `.env.pi4b.local` if it is present.

4. On the Pi, run:

```bash
cd ~/huawei100ktl_exporter
sudo bash deploy/systemd/install-systemd.sh
```

This installs `/etc/huawei-exporter.env` from `.env.pi4b.local` on first install, so you only need a final review on the Pi instead of retyping everything there.

### RTU quick start for the real inverter

1. Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

2. Create the RTU config:

```bash
cp .env.rtu.example .env
```

3. Edit at least:

- `SUN2000_SERIAL_PORT`
- `INFLUXDB_URL`
- `INFLUXDB_TOKEN`
- `INFLUXDB_ORG`
- `INFLUXDB_BUCKET`
- `DEVICE_ID`
- `SITE_ID`

4. Start the exporter:

```bash
.venv/bin/python main.py
```

5. Verify locally:

```bash
curl http://127.0.0.1:8080/live
curl http://127.0.0.1:8080/ready
curl http://127.0.0.1:8080/collector/status
curl http://127.0.0.1:8080/telemetry
```

### Raspberry Pi 4B + Waveshare `RS485 CAN HAT`

For this HAT, the important bring-up steps are on the Pi side before the exporter starts:

1. Enable the hardware UART and disable the serial login shell:

```bash
sudo raspi-config
```

- `Interface Options` -> `Serial Port`
- answer `No` to the login shell over serial
- answer `Yes` to enable the serial hardware

2. Free the primary UART from Bluetooth and keep UART enabled:

```bash
sudoedit /boot/firmware/config.txt
```

Ensure these lines exist:

```ini
enable_uart=1
dtoverlay=disable-bt
```

3. Disable the Bluetooth UART service and reboot:

```bash
sudo systemctl disable --now hciuart
sudo reboot
```

4. After reboot, verify that the UART path exists:

```bash
ls -l /dev/serial0
ls -l /dev/ttyAMA0 /dev/ttyS0 2>/dev/null
raspi-gpio get 14 15
```

Expected outcome:

- `/dev/serial0` exists
- it resolves to the active primary UART
- GPIO `14` and `15` are in UART mode rather than plain input mode

5. Use the Pi UART path in `.env`:

```bash
SUN2000_MODBUS_TRANSPORT=rtu
SUN2000_MODBUS_UNIT_ID=1
SUN2000_SERIAL_PORT=/dev/serial0
SUN2000_SERIAL_BAUDRATE=9600
SUN2000_SERIAL_PARITY=N
SUN2000_SERIAL_BYTESIZE=8
SUN2000_SERIAL_STOPBITS=1
```

6. If Modbus still does not answer after the UART appears, the likely blockers are now physical rather than application-side:

- inverter `A/B` polarity reversed
- wrong inverter COM terminals
- missing shared ground where required by the inverter side
- wrong slave ID or inverter-side Modbus not enabled
- another process already holding `/dev/serial0`

### TCP quick start for the simulator

1. Create a simulator config from `.env.example`
2. Ensure:

- `SUN2000_MODBUS_TRANSPORT=tcp`
- `SUN2000_MODBUS_HOST=127.0.0.1`
- `SUN2000_MODBUS_PORT=5020`
- `SUN2000_MODBUS_UNIT_ID=0`

3. Run the simulator:

```bash
.venv/bin/python iot_driver_copilot/huawei_sun_2000_solar_inverter/simulator.py
```

4. In another terminal, run the exporter:

```bash
.venv/bin/python main.py
```

## Configuration reference

| Variable | Default | Purpose |
| --- | --- | --- |
| `SUN2000_MODBUS_TRANSPORT` | `tcp` | `rtu` for RS485, `tcp` for simulator or network Modbus |
| `SUN2000_MODBUS_HOST` | `127.0.0.1` | TCP host when `SUN2000_MODBUS_TRANSPORT=tcp` |
| `SUN2000_MODBUS_PORT` | `502` | TCP port when `SUN2000_MODBUS_TRANSPORT=tcp` |
| `SUN2000_MODBUS_UNIT_ID` | `0` for TCP, `1` for RTU | Modbus unit/slave ID |
| `SUN2000_MODBUS_TIMEOUT` | `5.0` | Modbus timeout in seconds |
| `SUN2000_SERIAL_PORT` | unset | Serial device path for RTU, for example `/dev/serial0` on a Pi HAT or `/dev/ttyUSB0` on a USB adapter |
| `SUN2000_SERIAL_BAUDRATE` | `9600` | RTU baud rate |
| `SUN2000_SERIAL_PARITY` | `N` | RTU parity |
| `SUN2000_SERIAL_BYTESIZE` | `8` | RTU data bits |
| `SUN2000_SERIAL_STOPBITS` | `1` | RTU stop bits |
| `HTTP_HOST` | `0.0.0.0` | HTTP bind address |
| `HTTP_PORT` | `8080` | HTTP bind port |
| `INFLUXDB_URL` | `http://localhost:8086` | InfluxDB base URL |
| `INFLUXDB_TOKEN` | empty | InfluxDB auth token |
| `INFLUXDB_ORG` | `solar` | InfluxDB org |
| `INFLUXDB_BUCKET` | `inverters` | InfluxDB bucket |
| `INFLUXDB_MEASUREMENT` | `huawei_sun2000` | Influx measurement |
| `INFLUXDB_TIMEOUT` | `30` | Influx timeout in seconds |
| `DEVICE_ID` | `inverter_001` | Exported device tag |
| `SITE_ID` | `site_001` | Exported site tag |
| `COLLECTION_INTERVAL` | `60` | Telemetry collection interval in seconds |
| `BATCH_SIZE` | `10` | Upload batch size |
| `RETRY_ATTEMPTS` | `3` | Upload retry count |
| `RETRY_DELAY` | `5` | Retry delay in seconds |
| `EXPORTER_ENABLE_CONTROL` | `false` | Enables `PUT /control` and `PUT /settings` |
| `EXPORTER_STALE_AFTER_SECONDS` | `180` | Readiness freshness threshold |

## API reference

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/live` | `GET` | Process liveness |
| `/ready` | `GET` | Readiness, including Modbus + collector + Influx health |
| `/health` | `GET` | Full component health payload |
| `/config` | `GET` | Active config with token redacted |
| `/device` | `GET` | Device information |
| `/telemetry` | `GET` | Current telemetry; optional `metrics=` filter |
| `/control/catalog` | `GET` | Control register metadata plus validation summary |
| `/control` | `PUT` | Remote control writes |
| `/settings/catalog` | `GET` | Settings register metadata plus validation summary |
| `/settings` | `GET` | Read one or more settings; optional `names=` filter |
| `/settings` | `PUT` | Validated settings writes |
| `/collector/status` | `GET` | Collector runtime state |
| `/collector/start` | `POST` | Start collector |
| `/collector/stop` | `POST` | Stop collector |
| `/collector/upload` | `POST` | Force upload buffered points |

### Example calls

```bash
curl http://127.0.0.1:8080/device
curl http://127.0.0.1:8080/telemetry
curl "http://127.0.0.1:8080/telemetry?metrics=active_power&metrics=phase_A_voltage"
curl http://127.0.0.1:8080/control/catalog
curl http://127.0.0.1:8080/settings/catalog
curl "http://127.0.0.1:8080/settings?names=active_power_kw_derating&names=communication_disconnection_detection_time"
```

## Write safety profile

Remote writes are intentionally conservative.

**Allowed with validation**

- power setpoints
- percentage derating values
- power-factor and reactive-power ratios
- timing and delay values
- several binary enable / disable settings

**Blocked by default**

- grid-code changes
- output-mode changes
- voltage / frequency profile changes
- raw `MLD` curve writes such as `Q-U`, `PF-U`, `Q-P`, and `cosphi-P/Pn`

This keeps the exporter usable for real-site operation without pretending that every vendor enum and curve payload is safe for blind remote writes.

## Field definitions

### Device fields

| Name | Register | Count | Type | Scale | Unit |
| --- | --- | --- | --- | --- | --- |
| `model` | 30000 | 15 | `string` | 1 | — |
| `serial_number` | 30015 | 10 | `string` | 1 | — |
| `product_number` | 30025 | 10 | `string` | 1 | — |
| `firmware_version` | 30035 | 15 | `string` | 1 | — |
| `software_version` | 30050 | 15 | `string` | 1 | — |
| `modbus_protocol_version` | 30068 | 2 | `uint32` | 1 | — |
| `model_id` | 30070 | 1 | `uint16` | 1 | — |
| `number_of_strings` | 30071 | 1 | `uint16` | 1 | — |
| `number_of_mppts` | 30072 | 1 | `uint16` | 1 | — |
| `rated_power` | 30073 | 2 | `uint32` | 0.001 | kW |
| `max_active_power` | 30075 | 2 | `uint32` | 0.001 | kW |
| `max_apparent_power` | 30077 | 2 | `uint32` | 0.001 | kVA |
| `max_reactive_power_feed_to_grid` | 30079 | 2 | `int32` | 0.001 | kVar |
| `max_reactive_power_absorb_from_grid` | 30081 | 2 | `int32` | 0.001 | kVar |

### Telemetry fields

| Name | Register | Count | Type | Scale | Unit |
| --- | --- | --- | --- | --- | --- |
| `alarm_1` | 32008 | 1 | `uint16` | 1 | — |
| `alarm_2` | 32009 | 1 | `uint16` | 1 | — |
| `alarm_3` | 32010 | 1 | `uint16` | 1 | — |
| `highest_priority_alarm_code` | 32090 | 1 | `uint16` | 1 | — |
| `startup_time` | 32091 | 2 | `epoch_seconds` | 1 | s |
| `shutdown_time` | 32093 | 2 | `epoch_seconds` | 1 | s |
| `number_of_critical_alarms` | 32151 | 1 | `uint16` | 1 | — |
| `number_of_major_alarms` | 32152 | 1 | `uint16` | 1 | — |
| `number_of_minor_alarms` | 32153 | 1 | `uint16` | 1 | — |
| `number_of_warning_alarms` | 32154 | 1 | `uint16` | 1 | — |
| `dc_power` | 32064 | 2 | `int32` | 0.001 | kW |
| `grid_voltage_L1_L2` | 32066 | 1 | `uint16` | 0.1 | V |
| `grid_voltage_L2_L3` | 32067 | 1 | `uint16` | 0.1 | V |
| `grid_voltage_L3_L1` | 32068 | 1 | `uint16` | 0.1 | V |
| `phase_A_voltage` | 32069 | 1 | `uint16` | 0.1 | V |
| `phase_B_voltage` | 32070 | 1 | `uint16` | 0.1 | V |
| `phase_C_voltage` | 32071 | 1 | `uint16` | 0.1 | V |
| `phase_A_current` | 32072 | 2 | `int32` | 0.001 | A |
| `phase_B_current` | 32074 | 2 | `int32` | 0.001 | A |
| `phase_C_current` | 32076 | 2 | `int32` | 0.001 | A |
| `peak_active_power_of_day` | 32078 | 2 | `int32` | 0.001 | kW |
| `active_power` | 32080 | 2 | `int32` | 0.001 | kW |
| `reactive_power` | 32082 | 2 | `int32` | 0.001 | kVar |
| `power_factor` | 32084 | 1 | `int16` | 0.001 | — |
| `grid_frequency` | 32085 | 1 | `uint16` | 0.01 | Hz |
| `efficiency` | 32086 | 1 | `uint16` | 0.01 | % |
| `cabinet_temperature` | 32087 | 1 | `int16` | 0.1 | °C |
| `insulation_resistance` | 32088 | 1 | `uint16` | 0.001 | MΩ |
| `cumulative_generated_electricity` | 32106 | 2 | `uint32` | 0.01 | kWh |
| `daily_generated_electricity` | 32114 | 2 | `uint32` | 0.01 | kWh |
| `monthly_generated_electricity` | 32116 | 2 | `uint32` | 0.01 | kWh |
| `yearly_generated_electricity` | 32118 | 2 | `uint32` | 0.01 | kWh |
| `electricity_generated_previous_hour` | 32158 | 2 | `uint32` | 0.01 | kWh |
| `electricity_generated_previous_day` | 32162 | 2 | `uint32` | 0.01 | kWh |
| `electricity_generated_previous_month` | 32166 | 2 | `uint32` | 0.01 | kWh |
| `electricity_generated_previous_year` | 32170 | 2 | `uint32` | 0.01 | kWh |
| `inverter_state` | 32000 | 1 | `uint16` | 1 | — |
| `device_state` | 32089 | 1 | `uint16` | 1 | — |

### PV string telemetry

The exporter exposes all string channels for `n = 1..20`:

| Pattern | Register rule | Count | Type | Scale | Unit |
| --- | --- | --- | --- | --- | --- |
| `pv{n}_voltage` | `32016 + 2 * (n - 1)` | 1 | `int16` | 0.1 | V |
| `pv{n}_current` | `32017 + 2 * (n - 1)` | 1 | `int16` | 0.01 | A |

Examples:

- `pv1_voltage`, `pv1_current`
- `pv10_voltage`, `pv10_current`
- `pv20_voltage`, `pv20_current`

### Remote control commands

| Name | Register | Count | Type | Scale | Write rule |
| --- | --- | --- | --- | --- | --- |
| `active_power_kw_derating` | 40120 | 1 | `uint16` | 0.1 | `0..device.max_active_power` kW |
| `power_factor_setting` | 40122 | 1 | `int16` | 0.001 | `-1.0..1.0` |
| `reactive_power_compensation_qs` | 40123 | 1 | `int16` | 0.001 | `-1.0..1.0` |
| `reactive_power_adjustment_time` | 40124 | 1 | `uint16` | 1 | unsigned integer seconds |
| `active_power_percentage_derating` | 40125 | 1 | `int16` | 0.1 | `0..100` % |
| `active_power_fixed_value_derating_w` | 40126 | 2 | `uint32` | 1 | `0..device.max_active_power * 1000` W |
| `active_power_percentage_control` | 40199 | 1 | `int16` | 0.1 | `0..100` % |
| `power_on` | 40200 | 1 | `uint16` | 1 | only value `1` |
| `shutdown` | 40201 | 1 | `uint16` | 1 | only value `1` |
| `reset` | 40205 | 1 | `uint16` | 1 | only value `1` |

### Settings

| Name | Register | Count | Type | Scale | Unit | Description |
| --- | --- | --- | --- | --- | --- | --- |
| `system_time_local_time` | 40000 | 2 | `epoch_seconds` | 1 | s | System time in epoch seconds |
| `q_u_curve_model` | 40037 | 1 | `uint16` | 1 | — | Q-U characteristic curve model |
| `q_u_scheduling_trigger_power_percentage` | 40038 | 1 | `int16` | 1 | % | Q-U scheduling trigger power percentage |
| `active_power_kw_derating` | 40120 | 1 | `uint16` | 0.1 | kW | Active power fixed value derating |
| `power_factor_setting` | 40122 | 1 | `int16` | 0.001 | — | Power factor setpoint |
| `reactive_power_compensation_qs` | 40123 | 1 | `int16` | 0.001 | — | Reactive power compensation (Q/S) |
| `reactive_power_adjustment_time` | 40124 | 1 | `uint16` | 1 | s | Reactive power adjustment time |
| `active_power_percentage_derating` | 40125 | 1 | `int16` | 0.1 | % | Active power percentage derating |
| `active_power_fixed_value_derating_w` | 40126 | 2 | `uint32` | 1 | W | Active power fixed value derating |
| `reactive_power_compensation_at_night_qs` | 40128 | 1 | `int16` | 0.001 | — | Night reactive power compensation (Q/S) |
| `fixed_reactive_power_at_night` | 40129 | 2 | `int32` | 0.001 | kVar | Night fixed reactive power |
| `cosphi_p_pn_characteristic_curve` | 40133 | 21 | `mld` | 1 | — | cosphi-P/Pn characteristic curve raw registers |
| `q_u_characteristic_curve` | 40154 | 21 | `mld` | 1 | — | Q-U characteristic curve raw registers |
| `pf_u_characteristic_curve` | 40175 | 21 | `mld` | 1 | — | PF-U characteristic curve raw registers |
| `characteristic_curve_reactive_power_adjustment_time` | 40196 | 1 | `uint16` | 1 | s | Characteristic curve reactive power adjustment time |
| `percent_apparent_power` | 40197 | 1 | `uint16` | 0.1 | % | Percent apparent power |
| `q_u_scheduling_exit_power_percentage` | 40198 | 1 | `int16` | 1 | % | Q-U scheduling exit power percentage |
| `active_power_percentage_control` | 40199 | 1 | `int16` | 0.1 | % | Active power percentage control |
| `q_p_characteristic_curve` | 40354 | 21 | `mld` | 1 | — | Q-P characteristic curve raw registers |
| `minimum_pf_limit_for_q_u_curve` | 40375 | 1 | `uint16` | 0.001 | — | Minimum PF limit for Q-U curve |
| `q_u_curve_effective_delay_time` | 40376 | 1 | `uint16` | 1 | s | Q-U curve effective delay time |
| `grid_standard_code` | 42000 | 1 | `uint16` | 1 | — | Grid standard code |
| `output_mode` | 42001 | 1 | `uint16` | 1 | — | Output mode |
| `voltage_level` | 42002 | 1 | `uint16` | 1 | V | Voltage level |
| `frequency_level` | 42003 | 1 | `uint16` | 1 | Hz | Frequency level |
| `remote_power_scheduling` | 42014 | 1 | `uint16` | 1 | — | Remote power scheduling enable |
| `reactive_power_variation_gradient` | 42015 | 2 | `uint32` | 0.001 | %/s | Reactive power variation gradient |
| `active_power_gradient` | 42017 | 2 | `uint32` | 0.001 | %/s | Active power gradient |
| `scheduling_instruction_maintenance_time` | 42019 | 2 | `uint32` | 1 | s | Scheduling instruction maintenance time |
| `maximum_apparent_power` | 42021 | 2 | `uint32` | 0.001 | kVA | Maximum apparent power |
| `maximum_active_power` | 42023 | 2 | `uint32` | 0.001 | kW | Maximum active power |
| `apparent_power_reference` | 42025 | 2 | `uint32` | 0.001 | kVar | Apparent power reference |
| `active_power_reference` | 42027 | 2 | `uint32` | 0.001 | kW | Active power reference |
| `power_station_active_power_gradient` | 42029 | 1 | `uint16` | 1 | min/100% | Power station active power gradient |
| `power_station_average_active_power_filtering_time` | 42030 | 2 | `uint32` | 1 | ms | Power station average active power filtering time |
| `pf_u_voltage_detection_filter_time` | 42032 | 1 | `uint16` | 0.1 | s | PF-U voltage detection filter time |
| `frequency_detection_filter_time` | 42037 | 1 | `uint16` | 1 | ms | Frequency detection filter time |
| `frequency_active_derating_recovery_delay_time` | 42040 | 1 | `uint16` | 1 | s | Frequency active derating recovery delay time |
| `effective_delay_time_active_frequency_derating` | 42041 | 1 | `uint16` | 1 | ms | Effective delay time of active frequency derating |
| `frequency_active_derating_hysteresis_loop` | 42042 | 1 | `uint16` | 1 | — | Frequency active derating hysteresis loop |
| `fm_control_response_dead_zone` | 42043 | 1 | `uint16` | 0.001 | Hz | FM control response dead zone |
| `pq_mode` | 42046 | 1 | `uint16` | 1 | — | PQ mode |
| `panel_type` | 42047 | 1 | `uint16` | 1 | — | Panel type |
| `pid_compensation_direction` | 42048 | 1 | `uint16` | 1 | — | PID compensation direction |
| `string_connection_mode` | 42049 | 1 | `uint16` | 1 | — | String connection mode |
| `isolation_settings` | 42050 | 1 | `uint16` | 1 | — | Isolation settings |
| `frequency_modulation_control_power_variation_gradient` | 42051 | 1 | `uint16` | 1 | %/min | Frequency modulation control power variation gradient |
| `fm_control_power_variation_limit` | 42052 | 1 | `uint16` | 0.1 | % | FM control power variation limit |
| `fm_control_delay_response_time` | 42053 | 1 | `uint16` | 1 | ms | FM control delay response time |
| `mppt_multimodal_scanning` | 42054 | 1 | `uint16` | 1 | — | MPPT multimodal scanning |
| `mppt_scanning_interval` | 42055 | 1 | `uint16` | 1 | min | MPPT scanning interval |
| `automatic_power_grid_fault_recovery` | 42061 | 1 | `uint16` | 1 | — | Automatic power grid fault recovery |
| `power_limit_zero_percent_shutdown` | 42062 | 1 | `uint16` | 1 | — | Power limit 0 percent shutdown |
| `automatic_shutoff_communication_link_disconnection` | 42063 | 1 | `uint16` | 1 | — | Automatic shut-off on communication link disconnection |
| `communication_resumes_automatic_power_on` | 42064 | 1 | `uint16` | 1 | — | Communication resumes automatic power-on |
| `power_quality_optimization_mode` | 42065 | 1 | `uint16` | 1 | — | Power quality optimization mode |
| `rcd_enhancement` | 42066 | 1 | `uint16` | 1 | — | RCD enhancement |
| `no_time_work` | 42067 | 1 | `uint16` | 1 | — | No-time work |
| `night_pid_protection` | 42069 | 1 | `uint16` | 1 | — | Night PID protection |
| `reactive_power_parameter_takes_effect_at_night` | 42070 | 1 | `uint16` | 1 | — | Reactive power parameter takes effect at night |
| `communication_disconnection_detection_time` | 42072 | 1 | `uint16` | 1 | s | Communication disconnection detection time |
| `afci` | 42073 | 1 | `uint16` | 1 | — | AFCI enable |
| `afci_detection_adaptation_mode` | 42074 | 1 | `uint16` | 1 | — | AFCI detection adaptation mode |

## Validation and test runbook

### 1. Static config sanity

```bash
python3 -m py_compile config.py main.py data_collector.py modbus_client.py test_influxdb.py \
  iot_driver_copilot/huawei_sun_2000_solar_inverter/driver.py
```

### 2. Influx path

```bash
.venv/bin/python test_influxdb.py
```

This writes a single sample point using the current live field names.

### 3. Local API checks

```bash
curl http://127.0.0.1:8080/live
curl http://127.0.0.1:8080/ready
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8080/collector/status
curl http://127.0.0.1:8080/device
curl http://127.0.0.1:8080/telemetry
```

### 4. Catalog checks

```bash
curl http://127.0.0.1:8080/control/catalog
curl http://127.0.0.1:8080/settings/catalog
```

Confirm the returned metadata includes validation rules.

### 5. Safe write test

First enable writes in `.env`:

```bash
EXPORTER_ENABLE_CONTROL=true
```

Then restart and test a low-risk read / write flow against a commissioning-safe parameter:

```bash
curl "http://127.0.0.1:8080/settings?names=communication_disconnection_detection_time"
curl -X PUT http://127.0.0.1:8080/settings \
  -H 'Content-Type: application/json' \
  -d '{"settings":[{"name":"communication_disconnection_detection_time","value":60}]}'
```

## Acceptance matrix

| Check | Expected result |
| --- | --- |
| RTU config loads | `/config` shows `transport=rtu`, serial port, `unit_id=1` |
| TCP simulator config loads | `/config` shows `transport=tcp`, host `127.0.0.1`, port `5020` |
| Liveness | `/live` returns `alive` |
| Readiness | `/ready` returns `200` when Modbus + Influx + collector are healthy |
| Telemetry surface | `/telemetry` returns the current `81` fields |
| Device surface | `/device` returns the current `14` fields |
| Control catalog | `/control/catalog` returns `10` commands |
| Settings catalog | `/settings/catalog` returns `63` settings |
| Validation metadata | control and settings catalogs include validation summaries |
| Guardrails | blocked settings return per-item error messages instead of blind writes |
| Influx sample write | `test_influxdb.py` completes successfully |

## Edge deployment

For `systemd` deployment on the edge device, use:

- `deploy/systemd/README.md`
- `deploy/systemd/huawei-exporter.env.example`
- `deploy/systemd/preflight-edge-check.sh`
- `deploy/systemd/verify-local.sh`
- `.env.pi4b.local.example`

The systemd bundle is RTU-aware and is the recommended production path.

## Troubleshooting

### `/ready` is failing

Check:

- Modbus wiring / USB adapter path
- `SUN2000_SERIAL_PORT`
- `SUN2000_MODBUS_UNIT_ID`
- Influx credentials
- collector freshness in `/collector/status`

### RTU opens but no data reads

Check:

- baudrate `9600`
- parity `N`
- bytesize `8`
- stopbits `1`
- RS485 A/B polarity
- correct slave ID

### Write request is rejected

That is usually one of:

- `EXPORTER_ENABLE_CONTROL=false`
- value outside the validated range
- value not aligned to the field step, such as a `0.1` or `0.001` increment
- a deliberately blocked grid-code or curve setting

### Stale field names in dashboards

Update dashboards and queries to use live exporter names such as:

- `phase_A_voltage` instead of `voltage_L1`
- `grid_frequency` instead of `frequency`
- `cumulative_generated_electricity` instead of `total_energy`
- `highest_priority_alarm_code` instead of `alarm_codes`

## Reference files

- `Solar Inverter Modbus Interface Definitions (V3.0).pdf`
- `Solar Inverter Modbus Interface Definitions.txt`
- `User Manual_SUN2000-100KTL-115KTL-M2_V13_2024-01-12_EN.pdf`
