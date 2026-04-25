# Edge Install Pack Summary

This is the shortest handoff for the first live edge install.

## Files To Use

- [deploy/systemd/install-systemd.sh](../deploy/systemd/install-systemd.sh)
- [deploy/systemd/huawei-exporter.service](../deploy/systemd/huawei-exporter.service)
- [deploy/systemd/huawei-exporter.env.example](../deploy/systemd/huawei-exporter.env.example)
- [deploy/systemd/preflight-edge-check.sh](../deploy/systemd/preflight-edge-check.sh)
- [deploy/systemd/verify-local.sh](../deploy/systemd/verify-local.sh)
- [docs/edge-live-commissioning-checklist.md](./edge-live-commissioning-checklist.md)

## Commands To Run

### 1. Install

```bash
cd /opt/huawei100ktl_exporter
sudo bash deploy/systemd/install-systemd.sh
```

### 2. Configure

```bash
sudoedit /etc/huawei-exporter.env
```

Required values to set:

- `SUN2000_MODBUS_HOST`
- `INFLUXDB_URL`
- `INFLUXDB_TOKEN`
- `INFLUXDB_ORG`
- `DEVICE_ID`
- `SITE_ID`

Keep this as-is for production:

- `EXPORTER_ENABLE_CONTROL=false`

### 3. Restart

```bash
sudo systemctl daemon-reload
sudo systemctl restart huawei-exporter.service
sudo systemctl status huawei-exporter.service
```

### 4. Preflight

```bash
cd /opt/huawei100ktl_exporter
deploy/systemd/preflight-edge-check.sh
```

### 5. Local verification

```bash
cd /opt/huawei100ktl_exporter
deploy/systemd/verify-local.sh
```

### 6. Logs

```bash
sudo journalctl -u huawei-exporter.service -f
```

## Success Signals

You want to see all of these:

- `systemctl status` shows the service as active
- `/live` returns alive
- `/ready` returns ready
- `/health` does not show Modbus or Influx failure loops
- `/collector/status` shows:
  - recent `last_successful_collection_at`
  - recent `last_successful_upload_at`
  - `consecutive_collection_failures = 0`
  - `consecutive_upload_failures = 0`
  - `dropped_points = 0`
- `/device` returns plausible inverter identity
- `/telemetry` returns plausible live values
- InfluxDB shows fresh points for:
  - `active_power`
  - `grid_frequency`
  - `cumulative_generated_electricity`
- Dashboard values match at least one raw Influx point

## If It Fails

Check in this order:

1. `/etc/huawei-exporter.env` values
2. local network reachability to inverter IP:502
3. outbound HTTPS reachability to InfluxDB host
4. service logs:
   - `sudo journalctl -u huawei-exporter.service -f`
5. `/health` and `/collector/status`

## Final Sign-off

Do not sign off until all of these are true:

- service restarts cleanly
- service starts on boot
- live telemetry is plausible
- InfluxDB receives fresh points
- dashboard matches stored data
- control writes remain disabled

