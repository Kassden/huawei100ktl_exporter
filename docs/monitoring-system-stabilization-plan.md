# Monitoring System Analysis And Stabilization Plan

## Scope Reviewed

- `huawei100ktl_exporter`: Python/FastAPI service that reads Huawei SUN2000 inverter telemetry over Modbus TCP and writes it to InfluxDB.
- `sunrya-dashboard`: Next.js dashboard that queries InfluxDB and renders the monitoring UI.

## Executive Summary

The system is close to a usable prototype, but it is not yet operationally stable enough for an urgent production deployment without a short hardening pass.

The exporter is the stronger half: it has a coherent Modbus -> collector -> InfluxDB -> FastAPI shape, it compiles successfully, and it already exposes health and collector endpoints. The dashboard was the weaker half: parts of the UI still used placeholder values or random data, and several InfluxDB queries did not match the exporter field names. That meant the UI could build successfully while still showing incorrect or empty production metrics.

The most urgent issue was not cosmetic. The dashboard data contract did not match the exporter schema, so even a healthy exporter and healthy InfluxDB bucket could still produce misleading or blank dashboards.

## Current Architecture

### End-to-end data path

1. Huawei SUN2000 inverter is queried via Modbus TCP by `ModbusTCPClient`.
2. `DataCollector` reads `TELEMETRY_MAP`, buffers points in memory, and uploads them to InfluxDB.
3. FastAPI also exposes direct live-read endpoints such as `/device`, `/telemetry`, `/health`, and collector management endpoints.
4. The Next.js dashboard does not call the exporter directly. It queries InfluxDB using server-side API routes under `src/app/api/solar/*`.
5. The React UI consumes those API routes using hooks in `src/hooks/useSolarData.tsx`.

### Runtime split

- Exporter and dashboard are separate apps with separate repos and separate deployment concerns.
- The exporter has Docker artifacts.
- The dashboard now has a live Influx integration path, but deployment packaging and commissioning still need to be operationalized.

## Findings

### Critical

#### 1. Dashboard queries did not match the exporter schema

The exporter writes fields named:

- `active_power`
- `grid_frequency`
- `highest_priority_alarm_code`
- `cumulative_generated_electricity`

The dashboard previously queried:

- `total_energy`
- `frequency`
- `alarm_codes`

Impact:

- Project metrics could show incorrect status.
- System status could compute availability from fields that did not exist.
- Electricity and production charts could come back empty even when data existed.

### High

#### 2. System Status previously rendered static/demo values

The page passed a hard-coded `status` array into `SystemStatus`, and the screen preferred static values over fetched data.

Impact:

- The dashboard could look alive while not actually representing the site.
- Operators could trust values that were not connected to the inverter.

#### 3. Dashboard unit assumptions distorted performance calculations

The exporter scales `active_power` and `rated_power` to kW, but the dashboard previously treated a 100 kW inverter like a 100000 kW asset in performance logic.

Impact:

- Performance, capacity, and roll-up widgets were understated by roughly 1000x.

#### 4. Exporter control endpoint was unsafe for production

`/control` previously wrote every command using signed 32-bit register serialization regardless of the register spec.

Impact:

- Wrong write shapes for single-register commands.
- Wrong encoding for unsigned and signed control parameters.
- Production risk if this endpoint was reachable on a live inverter.

#### 5. Exporter resilience is still memory-first

The collector buffer is still an in-memory `deque(maxlen=1000)`. Failed uploads remain in memory, and while dropped-point tracking is now present, there is still no disk-backed queue.

Impact:

- If InfluxDB is unreachable for long enough, old telemetry can still be lost when the deque fills.
- No historical recovery after process restart.

## Stabilization Plan

### Phase 0: Production Safety Guardrails

1. Disable or restrict `/control`.
2. Define the authoritative telemetry contract between exporter and dashboard.
3. Decide deployment topology explicitly:
   - Exporter close to inverter and the network edge.
   - Dashboard on a public or internal cloud host.
   - InfluxDB as the shared integration boundary.
4. Freeze non-essential feature work until data correctness is fixed.

### Phase 1: Make The Dashboard Truthful

1. Replace incorrect field names in `queries.ts`.
2. Remove static `status` injection from `src/app/page.tsx`.
3. Remove random or demo data from `SystemStatus`.
4. Rework transformation logic so the UI uses exporter units consistently.
5. Replace guessed business KPIs with clearly derived metrics.

Status:

- This phase has been substantially implemented in the dashboard repo.

### Phase 2: Harden The Exporter For Edge Reliability

1. Add an immediate first collection and upload on startup.
2. Separate liveness from readiness.
3. Log and surface collection freshness.
4. Add buffer pressure alerts and dropped-sample visibility.
5. Consider a disk-backed spool if data loss is unacceptable.
6. Batch contiguous register reads to reduce Modbus round trips.

Status:

- Startup collection/upload, readiness/liveness, and richer collector status have been implemented.
- Disk-backed spooling remains open.

### Phase 3: Deployment Packaging And Ops Readiness

1. Create a real production edge deployment path.
2. Add dashboard deployment packaging/runbook.
3. Add a top-level deployment runbook covering env vars, ports, health URLs, and rollback.
4. Replace generic READMEs with operational documentation.
5. Standardize env var names where practical.

### Phase 4: Verification Gates

1. Add an exporter smoke/integration test path.
2. Add dashboard API route validation against mocked or known Influx results.
3. Add a commissioning script covering exporter health, collector status, sample telemetry, and dashboard API checks.

## Recommended Execution Order

1. Lock down or disable `/control`.
2. Fix dashboard schema mismatches and remove placeholder data.
3. Tighten exporter readiness and startup collection behavior.
4. Create a real deployment runbook and production manifests.
5. Add smoke tests and a clean CI validation path.

