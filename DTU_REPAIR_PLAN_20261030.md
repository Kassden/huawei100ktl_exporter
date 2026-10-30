# Pi spool and 4G recovery repair

## GOAL
- Restore verified telemetry and alarm replay from the Pi SQLite spool, expose delivery failure in health, and establish whether the Pi can remotely power-cycle the 4G modem.

## Acceptance Criteria
- [ ] Existing spool rows and alarm state are backed up before mutation; row counts, source timestamps, and SQLite integrity are preserved.
- [ ] New `alarm_events` contain no tag/field name collision and remain distinguishable when two events share a second.
- [ ] Old pending alarm rows are converted transactionally without deleting data; a repeat migration is a no-op.
- [ ] Pi exporter collects through the cutover; the backlog drains to zero pending/acknowledged/quarantined, and cloud readback verifies the earliest, middle, latest, and alarm source points.
- [ ] `/health` reports cloud-delivery degradation while keeping local collection readiness distinct; tests cover a stuck spool.
- [ ] The modem power path is verified against actual Pi hub topology. Perform one supervised VBUS drill only if supported and authorized; otherwise record the hardware prerequisite, leaving USB reset labeled as such.
- [ ] Code, migration, tests, and operational evidence are committed and pushed on the repair branch. No unrelated dashboard or vendor files change.

## Runtime Contract
- Mode: execute.
- Task ID and session binding: `pi-spool-4g-repair-20261030`, `01a0c7dc-234a-7802-98a4-5877bf78a21f`.
- Proof artifacts: `.mega/evidence/` baseline, migration and live readback reports, plus exact test outputs; register acceptance before edits.
- Child cleanup: no delegation; live Pi operations and code review remain with the parent.

## Decision Record Policy
- Record schema-key, migration/backup, health semantics, and USB power decisions with alternatives, disconfirming tests, and reversibility before their consequential edits.
- Promote durable accepted schema/power decisions to ADRs if their contract spans deployments; ledger `.mega/decisions.jsonl`.

## Research
- Built-in options: existing `TelemetrySpool`, Influx `Point`, FastAPI health payload, `dtu_watchdog.py`, SQLite `Connection.backup()`.
- Off-the-shelf options: `uhubctl` only for supported physical VBUS switching; no custom USB control protocol.
- Official sources: [Influx schema restrictions](https://docs.influxdata.com/influxdb3/cloud-serverless/write-data/best-practices/schema-design/), [partial-write behavior](https://docs.influxdata.com/influxdb3/cloud-serverless/write-data/troubleshoot/), [SQLite backup API](https://www.sqlite.org/backup.html), [Python sqlite3 backup](https://docs.python.org/3/library/sqlite3.html), [uhubctl Pi 4B topology](https://github.com/mvp/uhubctl/blob/master/README.md).

## Root-Cause Baseline
- Trigger scope: production-adjacent telemetry failure and remote 4G recovery failure.
- Evidence inventory: Pi journal first HTTP 400 at 2026-10-21 17:58 HKT; Influx says `alarm_code` tag conflicts with existing float field in `alarm_events`. Local writer also emits `source_bit` as both tag and field. Spool had 12,390 pending at 2026-10-30 12:05 HKT; cloud ordinary telemetry was fresh because Influx accepted partial writes. `/ready` reported healthy while queue was stuck. Watchdog logged general connectivity degraded continuously from 2026-10-29 02:19 until 2026-10-30 11:59 HKT, with six USB resets on each day and many NetworkManager reconnects; none restored connectivity. At 11:59 physical modem power-off/on produced USB disconnect (device 8), re-enumeration (device 9), then healthy paths. Pi boot remained 2026-09-23.
- Proven causes: schema collision caused persistent 400 and prevented spool acknowledgement; software USB reset did not recover the observed 4G failure, while physical modem power cycle did.
- Likely hypotheses: modem radio/firmware or carrier state wedged beyond USB enumeration reset; no evidence yet distinguishing those. One historical undervoltage event coincided with physical modem cycling, not outage onset.
- Possible causes: Pi supply/USB power margin, carrier/session behavior, modem firmware. Disproven: Pi reboot and Tailscale-only outage for this Oct29-30 window.
- Missing evidence: modem-internal logs and measured VBUS/current; no claim that software power switching works until VBUS-off is observed. Cloud partial writes mean cloud freshness does not prove queue health.
- Mutation boundary: capture read-only baseline first; backup live SQLite with supported backup API and code/unit before restart; never delete or purge pending rows. A failed migration or readback rolls back from the preserved backup. USB power-off requires the separate onsite approval already obtained and capability check.
- Remediation mapping: rename conflicting alarm tags and migrate pending points for proven 400; expose delivery state for proven health blind spot; investigate VBUS switch for proven insufficiency of USB reset. More generic power hardening remains hypothesis-driven.
- Not-done conditions: claiming all data drained from fresh cloud telemetry alone, clearing queue without readback, equating `usbreset` to electrical power removal, or enabling untested ganged-hub power control unattended.

## SOTA, Standards, And Best Practices
- Current SOTA / prior art: maintain distinct tag and field keys; treat Influx partial-write 400 as a batch failure with durable retry; backup WAL databases via SQLite backup rather than raw file copy.
- Official docs, standards, specs: links in Research; Influx explicitly rejects duplicate tag/field names; SQLite online backup yields a consistent snapshot; Pi 4B onboard USB hubs may have ganged switching.
- Mature mechanisms: Influx `Point` serializer for canonical line protocol; SQLite transaction and backup APIs; existing spool lock and systemd timer.
- Constraints: preserve source second timestamps, every field, tags needed for event identity, 0600 DB/backup, and no secret output. Keep collection working while cloud delivery is degraded.
- Rejected: delete bad rows, overwrite cloud measurement, string-replace line protocol, assume USB reset removes power, or change dashboard query contract while dirty unrelated work exists.
- Implementation: add one nonconflicting `event_key` tag derived from alarm code and source bit; keep numeric `alarm_code`/`source_bit` fields. Use a one-shot guarded migration script to rewrite old alarm rows, hashes, and identity ledger transactionally after a consistent backup. Reflect delivery degradation in health without disabling local collection.

## Dependency and Tooling Preflight
- Required: exporter `.venv` Python, unittest, SQLite, `influxdb_client`, SSH/Tailscale, Pi systemd/journal, cloud readback script, `uhubctl` capability query if available.
- Found: local `.venv`, tests, Pi SSH/root sudo, 4G and exporter active, 19+ GB free; exporter branch `repair/2026-10-30-pi-spool-and-4g`.
- Install or repair commands: no Python install needed because `.venv` already imports `influxdb_client` and tests run. If `uhubctl` is needed for read-only capability inspection, use `sudo apt-get install uhubctl` only after confirming the package candidate and scope; do not install speculative drivers.
- Runtime: Debian Pi 4B, systemd 252, Python 3, Influx Cloud, SQLite WAL.
- Blockers: true independent VBUS switching may not exist on current hub; if power-off drill fails or strands Pi, onsite user restores the modem. Cloud outage or retention limit can block full replay and must be reported, not hidden.

## Deterministic Real-User Test
- Entry point: exporter `/collector/status`, `/health`, `/ready`, `POST /collector/upload`, cloud Influx query.
- Workflow: enqueue two same-second alarms and telemetry, simulate partial write and restart, repair stored alarms, drain, and verify identical source fields/time in cloud.
- Fixtures: temp SQLite WAL DB with real legacy tag/field collision, mixed pending rows, repeat migration, invalid backup path, and identity collision cases.
- Assertions: no row loss, unique alarm identities, old fields intact, no bad tag/field collision, delivery status degraded on stuck backlog and healthy once verified.
- Command: `.venv/bin/python -m unittest discover -s tests -p 'test_*.py'` plus `test_alarm_events.py`; Pi readback and cloud sample query for live outcome.
- Evidence: unit output, SQLite counts/hashes/integrity, HTTP status JSON, cloud query outputs.

## Fulfillment and Readback Proof
- Requested result: functioning live Pi spool and alarm history with recoverable 4G management path.
- Mutation: scoped code/unit deployment, safe SQLite migration and replay; optional one supervised power-cycle drill only after capability proof.
- Readback: SQLite pending/ack/quarantine counts zero, cloud alarm rows and representative telemetry at original source timestamps, fresh cloud telemetry, exporter delivery health, watchdog journal and USB kernel events.
- Not done: fresh cloud telemetry alone, pending queue still blocked, unverified alarm fields, unbacked migration, no post-cutover collection, or calling USB enumeration reset a power cycle.

## Armageddon Mode
- Damage: systemic live telemetry and recovery path.
- Edge cases: duplicate same-second alarms, field/tag conflicts, mixed partial writes, replay after restart, duplicate migration, corrupt row, disk-full backup, disconnected Influx, USB identity mismatch, power-off command losing SSH.
- Failure simulations: HTTP 400/partial write, readback mismatch, SQLite rollback, stale backlog, transient 4G drop.
- Optimization: bound cloud verification concurrency and replay time per scheduler run; inspect batch behavior with mixed measurements before high-volume drain.
- Must-fix: any data loss, inconsistent row identity, silent health success with stuck queue, or uncontrolled USB power action.
- Evidence: focused tests, backup integrity, live source timestamp readback, kernel/journal receipts.

## Generality Guardrail
- Reuse existing spool, writer, health payload, and watchdog; no parallel queue or new monitoring service.
- Similar use cases: all exporter alarms and telemetry use the same spool/drain path.
- Recurrence: high for schema changes and intermittent network; migrate via explicit versioned one-shot tool, not arbitrary string rewrites.
- General mechanism: single alarm identity builder and shared serializer for current/future events; migration uses these functions.
- One-off: Pi-specific power topology and supervised drill are hardware-specific, not generalized into all deployments.

## Regression Guardrails
- Planned edit surface: `influxdb_writer.py`, `telemetry_spool.py` only if identity helper needed, `data_collector.py`, `iot_driver_copilot/huawei_sun_2000_solar_inverter/driver.py`, `scripts/repair_alarm_spool.py`, `test_alarm_events.py`, `tests/test_spool_drain.py`, `tests/test_alarm_spool_repair.py`, `tests/test_health_delivery.py`, this plan, `.mega/` proof and decisions. Pi: scoped exporter code, SQLite, systemd service, read-only USB capability check. Dashboard repo remains untouched.
- Protected: collection and Modbus, exact timestamps/fields/tags, alarm history query compatibility, SQLite durability, existing watchdog cadence and bounded reset limits.
- Consumers: Pi exporter, dashboard alarm query, Influx bucket, watchdog, operators reading `/ready` and `/health`.
- Damage radius: systemic; data shape and live deploy could silently lose or duplicate data.
- Branch strategy: use a separate branch, `repair/2026-10-30-pi-spool-and-4g`, based on `dtu-resilience-rest-remediation`, required for systemic live data/deploy risk.
- Proof: focused and full tests, migration dry run, live backup/integrity, scoped deployment, zero backlog and cloud source readback, watchdog/action evidence.

## Phase 1: Baseline

### Subphase 1.1: Preserve incident and data baseline
- Objective: capture exact source/timeline and queue composition before any mutation.
- Inspect: Pi exporter/watchdog journals, SQLite schema/counts, USB topology, cloud readback, local writer and dashboard contract.
- Allowed touch: plan and `.mega/` evidence only; no Pi mutation.
- Commit: canonical plan and sanitized baseline report.
- Tests: `mega_plan_check.py DTU_REPAIR_PLAN_20261030.md`; SQLite read-only integrity/count; cloud latest and alarm query.
- Success Criteria: evidence links prove 400 cause, queue count, latest source time, reset insufficiency; uncertainty documented.
- Planned Touch Files:
  - `DTU_REPAIR_PLAN_20261030.md`
  - `.mega/`
- Checklist:
  - [ ] Record SQLite/Influx/USB baseline with no secrets.
  - [ ] Verify plan and register outcome assertions.

## Phase 2: Schema repair

### Subphase 2.1: Correct new alarm serialization
- Objective: retain alarm numeric fields while adding collision-free event identity tag.
- Inspect: `influxdb_writer.py`, `test_alarm_events.py`, dashboard `docs/contracts/influx-measurements.json` and alarm queries.
- Allowed touch: writer and focused tests only.
- Commit: alarm writer serialization fix.
- Tests: `.venv/bin/python -m unittest test_alarm_events tests.test_spool_drain`.
- Success Criteria: exact line protocol has `event_key` and numeric fields, no duplicate tag/field names, same-second alarms remain distinct.
- Planned Touch Files:
  - `influxdb_writer.py`
  - `test_alarm_events.py`
  - `tests/test_spool_drain.py`
- Checklist:
  - [ ] Implement shared alarm identity and serializer.
  - [ ] Test collisions and query compatibility.

## Phase 3: Spool migration and replay

### Subphase 3.1: Migrate persisted alarm rows without loss
- Objective: one-shot, transactional, backed-up migration of existing bad alarm rows.
- Inspect: `telemetry_spool.py`, live SQLite schema and row samples, Influx serializer.
- Allowed touch: guarded repair script and tests; spool helper only if needed to avoid hash drift.
- Commit: repair tool and migration tests.
- Tests: `.venv/bin/python -m unittest tests.test_alarm_spool_repair tests.test_telemetry_spool`.
- Success Criteria: backup integrity and row-count equivalence, idempotent conversion, identity/hash consistency, no delete, failure rollback.
- Planned Touch Files:
  - `scripts/repair_alarm_spool.py`
  - `tests/test_alarm_spool_repair.py`
  - `telemetry_spool.py`
- Checklist:
  - [ ] Implement backup, dry-run, locked migration and tests.
  - [ ] Stop exporter briefly, backup and migrate Pi DB, install code, restart and verify collection.

### Subphase 3.2: Drain and read back the live backlog
- Objective: replay all pending telemetry and alarm rows at original source times.
- Inspect: Pi `/collector/status`, SQLite counts, cloud `alarm_events` and telemetry, Influx write/partial-write logs.
- Allowed touch: `data_collector.py` bounded replay loop, `influxdb_writer.py` bounded parallel readback, `telemetry_spool.py` alarm fairness, focused tests, live operational calls, and `.mega/` proof. This expansion is based on the measured 100 rows/minute recovery rate with 12,000 pending rows and independent readback showing alarm starvation.
- Commit: bounded replay change, tests, evidence and runbook note if necessary.
- Tests: verify batch concurrency cap, stop-on-error, and alarm selection amid telemetry backlog; full suite, zero pending/ack/quarantine; readback earliest/middle/latest telemetry and all alarm event fields; post-restart fresh sample.
- Success Criteria: cloud and queue both confirm completion; no 400 or hidden partial writes.
- Planned Touch Files:
  - `data_collector.py`
  - `influxdb_writer.py`
  - `telemetry_spool.py`
  - `tests/test_spool_drain.py`
  - `tests/test_telemetry_spool.py`
  - `.mega/`
- Checklist:
  - [ ] Bounded replay with four concurrent readback queries, a time budget per scheduler run, periodic counts and error stop condition.
  - [ ] Reserve up to ten alarm rows per batch while retaining newest telemetry and oldest-first replay.
  - [ ] Verify source timestamps and fields, then record final counts.

## Phase 4: Health verification

### Subphase 4.1: Expose delivery degradation separately from collection
- Objective: health must not claim all-green when uploads fail and spool ages, while collection can remain ready.
- Inspect: `data_collector.get_status`, `build_health_payload`, existing health consumers.
- Allowed touch: collector/driver health and focused tests.
- Commit: cloud-delivery health signal and tests.
- Tests: `.venv/bin/python -m unittest tests.test_health_delivery`; full suite.
- Success Criteria: stuck queue yields degraded overall status and explicit backlog reason; local readiness remains true; cleared verified queue returns healthy.
- Planned Touch Files:
  - `data_collector.py`
  - `iot_driver_copilot/huawei_sun_2000_solar_inverter/driver.py`
  - `tests/test_health_delivery.py`
- Checklist:
  - [ ] Implement explicit delivery component with age/count/failure.
  - [ ] Verify both degraded and recovered responses live.

## Phase 5: Power recovery assessment

### Subphase 5.1: Verify remote electrical power control or name hardware block
- Objective: distinguish USB enumeration reset from real modem VBUS power removal.
- Inspect: `lsusb -t`, hub descriptors, `uhubctl` support, Pi USB topology, kernel events; no code change unless a safe supported power path is proven.
- Allowed touch: `.mega/` evidence and Pi package/tool setup only if required; any watchdog change is a new reviewed subphase, not silent scope expansion.
- Commit: capability report; separate guarded watchdog commit only if true VBUS behavior is proven.
- Tests: read-only topology and hub power status; one onsite-authorized supervised VBUS off/on drill if supported; Tailscale/exporter/cloud recovery readback.
- Success Criteria: either verified VBUS power cycle with rollback/reconnect evidence or explicit unsupported-hardware blocker and specified switched USB hub/reset-line requirement.
- Planned Touch Files:
  - `.mega/`
- Checklist:
  - [ ] Check hub support without interrupting the Pi.
  - [ ] Execute at most one supervised drill if technically supported; otherwise document hardware prerequisite.
