# Edge First-Run Command Sheet

Use this on the edge device for the first live install with `systemd`.

Replace the placeholder values before running the `cat <<'EOF'` block.

## 1. Copy repo to edge device

```bash
cd /opt
sudo mkdir -p /opt/huawei100ktl_exporter
sudo chown "$USER":"$USER" /opt/huawei100ktl_exporter
```

Then copy the repo contents into `/opt/huawei100ktl_exporter`.

## 2. Run installer

```bash
cd /opt/huawei100ktl_exporter
sudo bash deploy/systemd/install-systemd.sh
```

## 3. Write production env file

Edit the values below:

- `REPLACE_WITH_INVERTER_IP`
- `REPLACE_WITH_INFLUX_URL`
- `REPLACE_WITH_INFLUX_TOKEN`
- `REPLACE_WITH_INFLUX_ORG`
- `REPLACE_WITH_DEVICE_ID`
- `REPLACE_WITH_SITE_ID`

Then run:

```bash
sudo tee /etc/huawei-exporter.env >/dev/null <<'EOF'
SUN2000_MODBUS_HOST=REPLACE_WITH_INVERTER_IP
SUN2000_MODBUS_PORT=502
SUN2000_MODBUS_UNIT_ID=1
SUN2000_MODBUS_TIMEOUT=5.0

HTTP_HOST=0.0.0.0
HTTP_PORT=8080

INFLUXDB_URL=REPLACE_WITH_INFLUX_URL
INFLUXDB_TOKEN=REPLACE_WITH_INFLUX_TOKEN
INFLUXDB_ORG=REPLACE_WITH_INFLUX_ORG
INFLUXDB_BUCKET=inverters
INFLUXDB_MEASUREMENT=huawei_sun2000
INFLUXDB_TIMEOUT=30

DEVICE_ID=REPLACE_WITH_DEVICE_ID
SITE_ID=REPLACE_WITH_SITE_ID
COLLECTION_INTERVAL=60
BATCH_SIZE=10
RETRY_ATTEMPTS=3
RETRY_DELAY=5
EXPORTER_ENABLE_CONTROL=false
EXPORTER_STALE_AFTER_SECONDS=180
EOF
sudo chmod 600 /etc/huawei-exporter.env
```

## 4. Restart service

```bash
sudo systemctl daemon-reload
sudo systemctl restart huawei-exporter.service
sudo systemctl status huawei-exporter.service
```

## 5. Watch logs

```bash
sudo journalctl -u huawei-exporter.service -f
```

What you want to see:

- Modbus connection success
- collector started
- successful upload to InfluxDB

## 6. Local API verification

```bash
cd /opt/huawei100ktl_exporter
deploy/systemd/verify-local.sh
```

If you want to inspect endpoints individually:

```bash
curl http://127.0.0.1:8080/live | python3 -m json.tool
curl http://127.0.0.1:8080/ready | python3 -m json.tool
curl http://127.0.0.1:8080/health | python3 -m json.tool
curl http://127.0.0.1:8080/collector/status | python3 -m json.tool
curl http://127.0.0.1:8080/device | python3 -m json.tool
curl http://127.0.0.1:8080/telemetry | python3 -m json.tool
```

## 7. Quick sign-off checks

Confirm all of these:

- `/live` returns alive
- `/ready` returns ready
- `/health` does not show ingestion failures
- telemetry values are plausible
- `last_successful_collection_at` is recent
- `last_successful_upload_at` is recent
- `consecutive_collection_failures` is `0`
- `consecutive_upload_failures` is `0`
- `dropped_points` is `0`

## 8. Cloud checks

In InfluxDB, confirm:

- measurement: `huawei_sun2000`
- expected `device_id`
- expected `site_id`
- fields like `active_power`, `grid_frequency`, `cumulative_generated_electricity`
- fresh timestamps

In the dashboard, confirm:

- project status is sensible
- performance and daily generation are non-empty
- one displayed value matches a raw Influx value

## 9. Recovery checks

```bash
sudo systemctl restart huawei-exporter.service
sudo systemctl status huawei-exporter.service
curl http://127.0.0.1:8080/ready | python3 -m json.tool
```

Optional reboot test:

```bash
sudo reboot
```

After reboot:

```bash
sudo systemctl status huawei-exporter.service
curl http://127.0.0.1:8080/live | python3 -m json.tool
curl http://127.0.0.1:8080/ready | python3 -m json.tool
```

