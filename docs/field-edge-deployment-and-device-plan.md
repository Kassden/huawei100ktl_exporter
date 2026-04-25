# Field Edge Deployment And Device Plan

Date reviewed: April 26, 2026

## Executive Recommendation

For this exporter, the best practical production shape is:

1. inverter LAN stays local at the site
2. exporter runs on an industrial edge computer under `systemd`
3. cellular backhaul is handled by a dedicated industrial router
4. InfluxDB and the dashboard stay in the cloud

That gives you the cleanest fault boundaries:

- the exporter keeps talking Modbus locally even if the WAN is unstable
- the router owns SIM, antenna, APN, VPN, and carrier problems
- the cloud owns storage, dashboards, and remote access

## What Language This Runs In

The exporter is not C++.

It is a Python service:

- FastAPI HTTP API
- Modbus polling logic
- InfluxDB upload logic

The dashboard is a Next.js application written in TypeScript.

## Best Hardware Shape

## Preferred topology: two-box field setup

This is the setup I would choose for urgent production deployment.

### Box 1: edge compute

Recommended:

- **Seeed reComputer R1100**

Why this is a better fit than a plain Raspberry Pi in this environment:

- industrial enclosure
- DIN rail / wall mounting
- fanless design
- `9V to 36V` DC input
- `-30C to 70C` operating temperature
- hardware watchdog
- RTC
- optional NVMe SSD
- low power draw for an always-on edge process

Relevant official specs from Seeed:

- Raspberry Pi CM4 based
- `9V to 36V` DC input
- PoE as an option
- `-30C to 70C` operating temperature
- idle power around `2.88W`, full load around `5.52W`
- Mini-PCIe support for optional `4G LTE`

### Box 2: cellular router

Recommended:

- **Teltonika RUT956** if you want the safer, more common field choice
- **Teltonika RUT986** if you want newer global LTE and eSIM support

Why a dedicated router is the right split:

- LTE antennas and carrier behavior are their own operational problem
- remote recovery is much easier on a dedicated router than on a Linux box with a USB modem
- dual SIM, GNSS, VPN, industrial power, watchdogs, and management are already solved at the router layer
- you can replace the compute box or reimage it without touching the cellular edge design

Relevant official Teltonika characteristics:

- industrial LTE routers
- `9V to 30V` DC power input on the RUT956
- `-40C to 75C` operating temperature on the RUT956
- serial and digital I/O available if needed later
- the RUT986 adds dual SIM and eSIM support, which is useful for fleet rollout and carrier provisioning

## If You Really Want One Box

This is the acceptable compromise, not the first choice:

- **Seeed reComputer R1100**
- regional **Quectel EC25 Mini-PCIe LTE module**
- proper LTE antenna kit

Use this only if:

- cabinet space is tight
- BOM count must be minimized
- you accept that modem issues and Linux host issues are now on the same box

Important limitation from Seeed documentation:

- on the R1100, `4G` uses Mini-PCIe slot 1 and is region-specific
- Seeed documents EC25 variants by region, including a North America SKU
- Seeed also notes PoE may be insufficient with higher-power peripherals such as SSD plus 4G, so DC terminal power is preferred in this configuration

## Exact Buy Direction

## Option A: most robust field setup

- 1 x Seeed reComputer R1100
- 1 x NVMe SSD, `128GB` or `256GB` is enough
- 1 x Teltonika RUT956
- 2 x LTE antennas matched to the router
- 1 x SIM from the carrier with the best coverage at the exact inverter site
- proper DIN rail mounting and cabinet power wiring

Choose this when:

- uptime matters more than BOM minimalism
- you expect poor or variable signal conditions
- you want easier remote debugging and easier future rollout

## Option B: better fleet-oriented cellular router setup

- 1 x Seeed reComputer R1100
- 1 x NVMe SSD, `128GB` or `256GB`
- 1 x Teltonika RUT986
- LTE antennas
- SIM or eSIM profile based on carrier strategy

Choose this when:

- you want newer router platforming
- you care about eSIM bootstrap / provisioning
- you may manage multiple deployments remotely

## Option C: one-box minimized setup

- 1 x Seeed reComputer R1100
- 1 x regional Quectel EC25 Mini-PCIe LTE module
- 1 x matching 4G antenna kit
- 1 x NVMe SSD

Choose this only when:

- you absolutely need to reduce device count
- you can tolerate tighter integration risk

## Deployment Recommendation

## Use `systemd` on the edge compute

For this exporter, `systemd` is the right default.

Why:

- the exporter is one small Python process
- it needs restart-on-failure, start-on-boot, logs, and health checks
- it does not need container orchestration
- you already have a `systemd` service file and install scripts in this repo

What `systemd` is:

- the native Linux service manager and process supervisor

What it gives you:

- automatic startup at boot
- automatic restart on crash
- service logs in `journalctl`
- dependency ordering
- watchdog and timeout semantics

## Docker vs `systemd`

These are not direct substitutes.

### `systemd`

Primary job:

- keep a process alive on a Linux machine

Best when:

- you have one service on one box
- you want the smallest number of moving parts
- you care about simple debugging in the field

### Docker

Primary job:

- package an application with its runtime into a container image

Useful when:

- you need highly repeatable packaging across many devices
- you want stricter dependency isolation
- your team already operates containers everywhere

Costs:

- extra runtime layer
- extra logs and networking layer to debug
- more storage usage
- more operator knowledge required

Your instinct is broadly correct:

- Docker is not usually "too heavy" for modern hardware
- but for a single edge Python exporter it is still an extra failure surface you may not need

## Third alternatives

Real alternatives exist, but they are weaker here:

- `supervisord`
  - workable, but usually worse than `systemd` on a modern Linux box
- `pm2`
  - more natural for Node services than Python edge collectors
- PyInstaller / PEX / Shiv packaging
  - can package Python more tightly, but you still normally run the result under `systemd`
- balena / k3s / Kubernetes class tooling
  - too much complexity for one exporter

So the practical answer is:

- use `systemd` for the exporter
- use an industrial LTE router for WAN
- keep Docker out unless fleet packaging becomes the dominant concern

## Cloud Layout

Keep this split:

1. inverter -> exporter over local Modbus TCP
2. exporter -> cloud InfluxDB over outbound HTTPS
3. dashboard -> cloud InfluxDB

Do not make the dashboard depend on querying the edge device directly for primary monitoring views.

## Test And Commissioning Plan

## Stage 1: bench validation before site install

Run this on a bench before touching the live site:

1. boot the edge device on stable DC power
2. install the exporter with the existing `systemd` unit
3. verify `/live`, `/ready`, `/health`, and `/collector/status`
4. verify write path to cloud InfluxDB
5. if using a router, verify cellular registration, APN, outbound HTTPS, and remote access path

Pass criteria:

- exporter starts on boot
- exporter restarts after manual kill
- timestamps in InfluxDB are current
- no repeated Modbus or upload failures

## Stage 2: inverter-side LAN validation

At site, before relying on cellular:

1. connect the edge compute to the inverter LAN
2. confirm TCP reachability to inverter port `502`
3. call `/device`
4. call `/telemetry`
5. compare a small set of values against inverter HMI or known expected values

Pass criteria:

- telemetry looks plausible
- units are sane
- no register read loops or timeout storms

## Stage 3: WAN failure testing

This is mandatory for a real field deployment.

1. disconnect cellular WAN for several minutes
2. confirm exporter stays healthy locally
3. restore WAN
4. confirm uploads resume cleanly
5. inspect for dropped points or long stale windows

Pass criteria:

- exporter process remains healthy during WAN loss
- service does not require manual restart after WAN restore
- backlog behavior is understandable and acceptable

## Stage 4: power-cycle testing

Do this before handover:

1. hard reboot the compute box
2. hard reboot the router
3. if possible, cold power-cycle the cabinet
4. confirm the entire chain recovers automatically

Pass criteria:

- exporter returns without manual login
- cellular link returns without manual intervention
- fresh points appear in InfluxDB after recovery
- dashboard resumes without special handling

## Stage 5: soak test

Before considering it stable, run at least `48 to 72 hours` continuously.

Watch:

- repeated collector failures
- memory growth
- stale uploads
- router reconnection behavior
- clock drift

## Operational Notes

The router and exporter should have different responsibilities.

### Router responsibilities

- SIM and carrier management
- APN
- VPN
- firewall
- remote management path
- signal quality monitoring

### Exporter responsibilities

- inverter polling
- local health endpoints
- buffering and upload
- structured logs

That separation is one of the main reasons the two-box design is more robust.

## My Bottom-Line Recommendation

If you need to deploy urgently and want the least risky field shape:

- run the exporter on **Seeed reComputer R1100**
- supervise it with **`systemd`**
- use a **Teltonika RUT956** or **RUT986** for cellular backhaul
- keep InfluxDB and the dashboard in the cloud

If you want one sentence:

- use `systemd` for the Python exporter, not Docker, and do not combine compute and cellular unless you have a strong reason to reduce hardware count

## Source Links

Official vendor pages used for this note:

- Seeed reComputer R1100: https://wiki.seeedstudio.com/recomputer_r1100_intro/
- Seeed reComputer R1000: https://wiki.seeedstudio.com/recomputer_r/
- Teltonika RUT956 product page: https://teltonika-networks.com/products/routers/rut956
- Teltonika RUT956 wiki / safety information: https://wiki.teltonika-networks.com/view/RUT956_Safety_Information
- Teltonika RUT986 product page: https://www.teltonika-networks.com/products/routers/rut986
- Teltonika RUT986 manual: https://wiki.teltonika-networks.com/view/RUT986_Manual
