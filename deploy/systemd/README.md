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
- `huawei-exporter.env.example`
- `install-systemd.sh`
- `push-to-pi.sh`
- `preflight-edge-check.sh`
- `verify-local.sh`

## Default install layout

- app directory: `/opt/huawei100ktl_exporter`
- service user: `huawei-exporter`
- env file: `/etc/huawei-exporter.env`

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

## Verification

Use:

- `deploy/systemd/preflight-edge-check.sh`
- `deploy/systemd/verify-local.sh`

The preflight script is RTU-aware:

- for `rtu`, it checks the serial device path and local API
- for `tcp`, it checks TCP reachability and local API
