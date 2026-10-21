# `systemd` deployment

This folder is the production deployment path for an edge Linux host connected to the inverter over RS485.

Use the top-level `README.md` for:

- field definitions
- API details
- validation rules
- commissioning runbook

Use this document only for the service layout and install flow.

## Files

- `huawei-exporter.service`
- `dtu-watchdog.service`
- `dtu-watchdog.timer`
- `huawei-exporter.env.example`
- `install-systemd.sh`
- `push-to-pi.sh`
- `preflight-edge-check.sh`
- `verify-local.sh`

## Default install layout

- app directory: `/opt/huawei100ktl_exporter`
- service user: `huawei-exporter`
- env file: `/etc/huawei-exporter.env`
- durable spool: `/var/lib/huawei-exporter/telemetry-spool.sqlite3`
- watchdog state: `/var/lib/huawei-exporter/dtu-watchdog-state.json`

## Fast path

From the exporter repo root on the edge device:

```bash
sudo bash deploy/systemd/install-systemd.sh
sudoedit /etc/huawei-exporter.env
sudo systemctl restart huawei-exporter.service
deploy/systemd/preflight-edge-check.sh
deploy/systemd/verify-local.sh
```

## Local-first Pi flow

If you want to prepare the RTU and Influx values on your Mac before copying to the Pi:

1. In the repo root, create `.env.pi4b.local` from `.env.pi4b.local.example`
2. Fill in the real Influx values and the intended RTU serial path
3. `rsync` the repo to the Pi
4. Run `sudo bash deploy/systemd/install-systemd.sh` on the Pi

On first install, the installer will prefer `.env.pi4b.local` and copy it to `/etc/huawei-exporter.env`. If `/etc/huawei-exporter.env` already exists, it leaves the existing file in place.

## Automatic local-to-Pi deploy

From your Mac, run:

```bash
bash deploy/systemd/push-to-pi.sh
```

By default it assumes:

- `PI_USER=sunrya888`
- `PI_HOST=100.104.99.55`
- remote staging directory: `/home/sunrya888/huawei100ktl_exporter`
- installed app directory: `/opt/huawei100ktl_exporter`

What it does:

1. `rsync`s the local repo to the Pi staging directory
2. runs `sudo bash deploy/systemd/install-systemd.sh` on the Pi
3. restarts `huawei-exporter.service`
4. runs `verify-local.sh` on the Pi

You can override the target at runtime:

```bash
PI_USER=myuser PI_HOST=pi4b.tailnet-name.ts.net bash deploy/systemd/push-to-pi.sh
```

Because `install-systemd.sh` preserves `/etc/huawei-exporter.env` once it already exists, repeated deploys update code and dependencies without overwriting the live InfluxDB or RTU settings.

## What the installer does

- creates the `huawei-exporter` system user if needed
- copies the repo into `/opt/huawei100ktl_exporter`
- creates `.venv`
- installs `requirements.txt`
- installs `/etc/huawei-exporter.env` if it does not already exist
- prefers `/opt/huawei100ktl_exporter/.env.pi4b.local` over the generic example on first install
- installs and starts `huawei-exporter.service`
- provides `dtu-watchdog.service` and `dtu-watchdog.timer`; enable the timer only after reviewing the mode and local network connection ID

## Manual install

1. Copy the repo to `/opt/huawei100ktl_exporter`
2. Create the service user:

```bash
sudo useradd --system --home /opt/huawei100ktl_exporter --shell /usr/sbin/nologin huawei-exporter
```

3. Create the environment file:

```bash
sudo install -m 600 deploy/systemd/huawei-exporter.env.example /etc/huawei-exporter.env
sudoedit /etc/huawei-exporter.env
```

4. Create the virtualenv and install dependencies:

```bash
python3 -m venv /opt/huawei100ktl_exporter/.venv
/opt/huawei100ktl_exporter/.venv/bin/pip install -r /opt/huawei100ktl_exporter/requirements.txt
```

5. Install the service unit and start it:

```bash
sudo cp deploy/systemd/huawei-exporter.service /etc/systemd/system/huawei-exporter.service
sudo systemctl daemon-reload
sudo systemctl enable --now huawei-exporter.service
```

## RTU notes

For a real `SUN2000-100KTL-M2`, set:

- `SUN2000_MODBUS_TRANSPORT=rtu`
- `SUN2000_MODBUS_UNIT_ID=1`
- `SUN2000_SERIAL_PORT=/dev/serial0` for a Pi GPIO-UART HAT, or `/dev/ttyUSB0` for a USB adapter
- `SUN2000_SERIAL_BAUDRATE=9600`
- `SUN2000_SERIAL_PARITY=N`
- `SUN2000_SERIAL_BYTESIZE=8`
- `SUN2000_SERIAL_STOPBITS=1`

## Service commands

```bash
sudo systemctl status huawei-exporter.service
sudo systemctl restart huawei-exporter.service
sudo journalctl -u huawei-exporter.service -f
sudo systemctl disable --now huawei-exporter.service
```

## Durable spool

The exporter uses a SQLite WAL spool when `SPOOL_ENABLED=true` is set in `/etc/huawei-exporter.env`. Existing installations default to disabled until explicitly commissioned:

- database: `/var/lib/huawei-exporter/telemetry-spool.sqlite3`
- one row per emitted Influx point
- telemetry and `alarm_events` both pass through the same queue
- rows are deleted only after synchronous write acknowledgement and cloud readback verification

The systemd unit owns `/var/lib/huawei-exporter` as the `huawei-exporter` user with mode 0700. Confirm at least 512 MiB free beyond the 1 GiB spool budget before enabling. After restart, inspect `/ready`, `/collector/status`, the SQLite row counts, and a fresh cloud source timestamp. A deployment or merge alone does not enable the spool in an existing env file.

Do not put the spool inside `/opt/huawei100ktl_exporter`, `/tmp`, or a release directory. On rollback, preserve `/var/lib/huawei-exporter/telemetry-spool.sqlite3` and its WAL/SHM files. Older memory-only exporter versions cannot drain this database, so rollback is a degraded durability state unless forward-compatible code is restored.

Before live deployment, snapshot with the SQLite backup API or stop the service and copy all three WAL files together. Copying only the main database file while WAL mode is active is not a valid backup.

## DTU watchdog

The watchdog is installed as an optional systemd timer and defaults to observe-only mode:

```bash
sudo cp deploy/systemd/dtu-watchdog.service /etc/systemd/system/dtu-watchdog.service
sudo cp deploy/systemd/dtu-watchdog.timer /etc/systemd/system/dtu-watchdog.timer
sudo systemctl daemon-reload
sudo systemctl enable --now dtu-watchdog.timer
```

Observe mode reports general connectivity, local exporter health, and `tailscaled` state but does not reconnect the modem or restart services.

Automatic recovery requires a site-specific systemd drop-in after confirming the exact NetworkManager connection ID, USB topology, vendor/product ID, and `eth1` attachment. The checked-in Pi 4B example is `deploy/systemd/dtu-watchdog-pi4b-auto.conf`; the base unit remains observe-only. Install the drop-in under `/etc/systemd/system/dtu-watchdog.service.d/` and reload systemd only on that verified Pi.

The auto command needs:

```text
--mode auto --networkmanager-connection <connection-id> --usb-device <usb-path> --usb-vendor <id> --usb-product <id> --modem-interface <interface>
```

Automatic mode treats general connectivity as healthy if either the IP probe or an HTTP connection to `INFLUXDB_URL` succeeds. After three consecutive failures it reconnects NetworkManager, at most once per 15 minutes and three times per hour. If connectivity is still down after six checks and at least three minutes after the reconnect, it validates the USB device's identity and interface, then invokes `usbreset` on that device. USB resets are limited to once per hour and twice per day. The attempt and cooldown are persisted before the reset. Tailscale restarts are allowed only when general connectivity is healthy and `tailscaled` itself is failed.

`usbreset` resets USB enumeration; it is not a guaranteed electrical power cycle of the 4G radio. If the modem only recovers after removing its power, fit a controllable USB power switch or modem reset line and validate that hardware before substituting a power-cycle command. Watch `journalctl -u dtu-watchdog.service`, `/collector/status`, and a cloud readback after commissioning.

## Verification

Use:

- `deploy/systemd/preflight-edge-check.sh`
- `deploy/systemd/verify-local.sh`

The preflight script is RTU-aware:

- for `rtu`, it checks the serial device path and local API
- for `tcp`, it checks TCP reachability and local API
