# Edge Live Commissioning Checklist

Use this checklist for the first real deployment on the edge device against the live Huawei inverter and cloud InfluxDB.

## 1. Pre-flight

- Confirm the edge device can reach the inverter IP on the local network.
- Confirm the edge device has outbound HTTPS access to the InfluxDB cloud host.
- Confirm the edge device time is correct:
  - `timedatectl status`
- Confirm Python 3 is available:
  - `python3 --version`
- Confirm you have the production values ready:
  - inverter IP
  - Modbus port
  - InfluxDB URL
  - InfluxDB token
  - InfluxDB org
  - InfluxDB bucket
  - device ID
  - site ID

## 2. Install

- Run the installer:
  - `sudo bash deploy/systemd/install-systemd.sh`
- Edit the environment file:
  - `sudoedit /etc/huawei-exporter.env`
- Verify these production-safe defaults:
  - `EXPORTER_ENABLE_CONTROL=false`
  - `HTTP_PORT=8080`
- Restart the service:
  - `sudo systemctl restart huawei-exporter.service`

## 3. Local Service Checks

- Check service status:
  - `sudo systemctl status huawei-exporter.service`
- Tail logs:
  - `sudo journalctl -u huawei-exporter.service -f`
- Run the local verification helper:
  - `deploy/systemd/verify-local.sh`

Expected result:

- `/live` returns alive
- `/ready` returns ready
- `/health` shows healthy or at least clearly identifies the failing component
- `/collector/status` shows the collector running and fresh

## 4. Inverter Read Checks

- Fetch live telemetry:
  - `curl http://127.0.0.1:8080/telemetry | python3 -m json.tool`
- Fetch device metadata:
  - `curl http://127.0.0.1:8080/device | python3 -m json.tool`

Check that these values are plausible:

- `active_power`
- `grid_frequency`
- `cumulative_generated_electricity`
- `daily_generated_electricity`
- `highest_priority_alarm_code`
- `cabinet_temperature`
- device model and serial number

If any of these are missing or obviously wrong, stop and fix the Modbus-side problem before proceeding.

## 5. InfluxDB Write Checks

- Check collector status:
  - `curl http://127.0.0.1:8080/collector/status | python3 -m json.tool`

Confirm:

- `last_successful_collection_at` is recent
- `last_successful_upload_at` is recent
- `consecutive_collection_failures` is `0`
- `consecutive_upload_failures` is `0`
- `dropped_points` is `0`

Then verify in InfluxDB directly that:

- the measurement exists
- the expected `device_id` tag is present
- the expected `site_id` tag is present
- fields such as `active_power`, `grid_frequency`, and `cumulative_generated_electricity` are present
- timestamps are current and in UTC

## 6. Dashboard Checks

After the dashboard is pointed at the production bucket:

- open the dashboard
- check project status
- check system status cards
- check the production charts

Spot-check one real value:

1. read a recent point from InfluxDB
2. compare it to the dashboard display
3. confirm units match

Do not sign off until at least one dashboard value has been reconciled against raw Influx data.

## 7. Failure Checks

Run these small failure tests before sign-off:

### A. Service restart

- `sudo systemctl restart huawei-exporter.service`

Confirm:

- service comes back cleanly
- `/ready` becomes healthy again
- fresh data resumes

### B. Temporary WAN interruption

If safe to do:

- temporarily block outbound internet from the edge device

Confirm:

- local service stays up
- collector shows upload failures clearly
- once WAN is restored, uploads resume

### C. Device reboot

- reboot the edge device

Confirm after boot:

- service starts automatically
- `/live` and `/ready` recover
- telemetry resumes

## 8. Sign-off Criteria

Only call the edge deployment commissioned when all are true:

- service auto-starts on boot
- service restarts cleanly
- local telemetry reads are plausible
- InfluxDB receives current points
- dashboard matches stored data
- control remains disabled in production
- no ongoing collection or upload failure loop exists

## 9. Useful Commands

- service status:
  - `sudo systemctl status huawei-exporter.service`
- service logs:
  - `sudo journalctl -u huawei-exporter.service -f`
- restart:
  - `sudo systemctl restart huawei-exporter.service`
- local verification:
  - `deploy/systemd/verify-local.sh`
- health:
  - `curl http://127.0.0.1:8080/health | python3 -m json.tool`
- collector status:
  - `curl http://127.0.0.1:8080/collector/status | python3 -m json.tool`

