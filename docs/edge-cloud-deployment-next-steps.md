# Edge-To-Cloud Deployment And Next Steps Plan

## Goal

Deploy the monitoring system with:

- `huawei100ktl_exporter` running on an edge computing device near the inverter
- InfluxDB running in the cloud
- `sunrya-dashboard` running in the cloud

The immediate objective is not feature expansion. It is to make the system reliable enough for urgent deployment, observable enough to debug remotely, and simple enough to operate without guesswork.

## Recommended Production Topology

### Edge layer

Run the exporter on the edge device because:

- Modbus TCP access to the inverter is local and more reliable on the same LAN
- inverter polling should not depend on WAN stability
- the edge device can buffer short cloud outages better than a cloud service can tolerate inverter network instability

Recommended responsibilities on the edge device:

- poll Huawei inverter over Modbus TCP
- expose local health and readiness endpoints
- batch and send telemetry to cloud InfluxDB
- keep enough local logs and metrics for remote troubleshooting

### Cloud layer

Run these in the cloud:

- InfluxDB
- dashboard
- optional alerting and monitoring

Why:

- dashboard access is easier for remote teams
- time-series storage belongs in a stable, backed-up environment
- cloud dashboards can be updated independently of inverter-side networking

## Strong Recommendation On System Boundaries

Keep the architecture like this:

1. Inverter -> exporter over Modbus TCP
2. Exporter -> InfluxDB over HTTPS
3. Dashboard -> InfluxDB for reads

Avoid making the dashboard depend on direct calls to the edge exporter for primary monitoring views. The exporter should remain the ingestion service, not the main query backend for the web UI.

## Phase 1: Exporter Hardening

### 1. Lock down control writes

- keep `/control` disabled in production unless explicitly needed and verified

### 2. Improve readiness semantics

Recommended health model:

- liveness: process and HTTP server are up
- readiness: Modbus reachable, collector running, Influx write path healthy, last successful collection not stale

### 3. Collect immediately on startup

- run one immediate collection on startup
- run one immediate upload after first successful collection

### 4. Make buffering operationally safe

- at minimum: queue pressure warnings and dropped-sample logs
- preferred: simple disk-backed spool on the edge device

### 5. Reduce Modbus fragility

- batch contiguous register reads where possible

## Phase 2: Deployment Packaging

### Edge exporter packaging

Recommended deployment form:

- Docker container on the edge device if Docker is already part of the operating model
- `systemd` service if lower overhead and fewer moving parts matter more than packaging uniformity

### Docker vs systemd on the edge device

#### `systemd`

What it is:

- the native Linux service manager

Pros:

- lowest overhead
- fewer moving parts
- no container runtime dependency
- simpler failure model
- easy boot-time start and restart policy
- good fit for one Python exporter on one known edge box

Cons:

- weaker environment isolation
- Python/runtime dependencies live closer to the host
- upgrades and dependency pinning need more care
- moving the service to another machine is slightly less uniform

#### Docker

What it is:

- application packaged with its runtime and dependencies into a container

Pros:

- consistent runtime across devices
- easier dependency isolation
- simpler packaging for reproducible deploys
- cleaner rollback if image tags are managed well

Cons:

- more moving parts
- extra runtime layer
- more things to observe when debugging
- slightly higher CPU/RAM/storage overhead than native service execution
- container/network configuration can become its own source of operator error

### My recommendation here

For this specific exporter, on a single edge device, prefer `systemd` unless:

- Docker is already standard on the edge fleet
- you need identical packaging across many devices

This exporter is a single Python process talking to one local inverter and one cloud backend. It does not gain enough from containerization to outweigh Docker’s extra runtime layer if the main goal is urgent, simple, reliable deployment.

### Third alternatives that exist

There are alternatives, but they are usually less attractive here:

- direct manual shell session:
  - not acceptable for production
- `supervisord`:
  - workable, but usually inferior to `systemd` on modern Linux
- process managers like `pm2`:
  - better suited to Node ecosystems than this Python edge service
- container orchestrators such as k3s or Kubernetes:
  - overkill for one exporter on one edge device
- packaged Python artifacts such as `pex`, `shiv`, or PyInstaller:
  - can reduce dependency drift, but they still need something like `systemd` to supervise them

In practice, the real choice here is:

- `systemd` for simplest and leanest operation
- Docker for packaging uniformity

## Recommended Edge Device Setup

- stable Ethernet if possible
- access to inverter LAN
- outbound HTTPS access to cloud InfluxDB
- synchronized time
- persistent storage for logs and optional spool
- automatic restart on crash
- automatic start on reboot

## Deployment Sequence

### Step 1. Stabilize exporter in simulator mode

- confirm exporter starts cleanly
- confirm `/health` and `/collector/status`
- confirm data reaches cloud InfluxDB from simulator

### Step 2. Test exporter on edge against the real inverter

- point exporter to the real inverter IP
- confirm direct `/telemetry` reads are sensible
- confirm timestamps and units are correct
- confirm points appear in InfluxDB under the expected measurement and tags

### Step 3. Validate cloud data contract

- verify actual Influx measurement name
- verify `device_id`
- verify `site_id`
- verify field names used by the dashboard

### Step 4. Deploy dashboard

- point dashboard to production InfluxDB
- verify dashboard API routes return expected data
- verify UI values match raw Influx results for a spot-checked timestamp

### Step 5. Run commissioning checks

- restart exporter and confirm recovery
- briefly interrupt WAN access and confirm backlog behavior
- restore WAN and confirm upload recovery
- verify dashboard reflects resumed telemetry

