# systemd Deployment

This folder contains a minimal `systemd` deployment bundle for running the exporter on an edge Linux device.

## Files

- `huawei-exporter.service`: service unit
- `huawei-exporter.env.example`: environment file example loaded by `systemd`
- `install-systemd.sh`: root-run installer for the common `/opt` layout
- `verify-local.sh`: quick local API verification helper

## Assumed install layout

- application directory: `/opt/huawei100ktl_exporter`
- service user: `huawei-exporter`
- env file: `/etc/huawei-exporter.env`

Adjust the paths in the unit file if your deployment layout differs.

## Fast path install

On the edge device, from the repo root:

```bash
sudo bash deploy/systemd/install-systemd.sh
sudoedit /etc/huawei-exporter.env
sudo systemctl restart huawei-exporter.service
deploy/systemd/verify-local.sh
```

The installer will:

- create the `huawei-exporter` system user if missing
- copy the repo into `/opt/huawei100ktl_exporter`
- create a Python virtualenv
- install `requirements.txt`
- install `/etc/huawei-exporter.env` if it does not exist
- install and start the `systemd` unit

## Manual install steps

1. Copy the exporter repo to `/opt/huawei100ktl_exporter`.
2. Create a dedicated system user:
   `sudo useradd --system --home /opt/huawei100ktl_exporter --shell /usr/sbin/nologin huawei-exporter`
3. Copy `deploy/systemd/huawei-exporter.env.example` to `/etc/huawei-exporter.env` and fill in real values.
4. Install Python dependencies in a virtual environment, for example under `/opt/huawei100ktl_exporter/.venv`.
5. Copy `deploy/systemd/huawei-exporter.service` to `/etc/systemd/system/huawei-exporter.service`.
6. Reload and enable:
   `sudo systemctl daemon-reload`
   `sudo systemctl enable --now huawei-exporter.service`

## Useful commands

- status:
  `sudo systemctl status huawei-exporter.service`
- logs:
  `sudo journalctl -u huawei-exporter.service -f`
- restart:
  `sudo systemctl restart huawei-exporter.service`
- disable:
  `sudo systemctl disable --now huawei-exporter.service`

## Commissioning checks

- `curl http://127.0.0.1:8080/live`
- `curl http://127.0.0.1:8080/ready`
- `curl http://127.0.0.1:8080/health`
- `curl http://127.0.0.1:8080/collector/status`
- `curl http://127.0.0.1:8080/telemetry`

Or use:

- `deploy/systemd/verify-local.sh`
