# Live repair receipts, Oct 30 2026 HKT

## Data cutover

- Pre-migration live SQLite spool integrity was `ok`; 12,406 rows were present at the stopped-service cutover. The consistent backup is `/var/lib/huawei-exporter/telemetry-spool.pre-repair-20261030.sqlite3`, with integrity `ok` and the same 12,406 rows.
- Migration converted 108 pending `alarm_events` rows from `alarm_code`/`source_bit` tag collisions to an `event_key` tag with numeric alarm fields retained. A repeat migration reported `changed: 0`. No rows were deleted by migration.
- Exporter resumed Modbus collection and Influx cloud writes. `/health` reported `degraded` for the old pending spool while `/ready` stayed true and collection stayed fresh.
- The measured serial drain rate was about 100 points per minute. Bounded concurrent readback and up to five batches per 40-second scheduler run drained 300 points in the first observed run without upload failures. The fair batch selector reserved up to ten alarm rows per batch.
- All alarm rows reached zero pending, and the independent cutover-backup sample verifier found original-time cloud fields for telemetry IDs 472, 6668, 12877 and alarm IDs 473, 7662, 12561. Each returned `verified: true` for earliest, middle, and latest source timestamps.
- At 12:58-12:59, telemetry remained pending (6,943 rows at one check) with zero upload failures, while local collection was fresh. At 13:20 HKT, live `/health` returned `healthy`, with pending=0, acknowledged=0, quarantined=0, no last drain error, and fresh collection. A second independent cloud query verified all six cutover-backup samples at their original source timestamps. Exporter, Tailscale, and watchdog timer were active; watchdog state showed zero network failures.

## Modem power path

- The first single-port `uhubctl -e -l 1-1 -p 1` test disconnected `eth1` but left the modem powered. Timed port-on ran; no USB re-enumeration followed. Onsite physical card cycling did not restore the link, and a Pi power cycle did.
- The second, separately approved full-hub `uhubctl -l 1-1 -a off` test made the modem lights go fully off. The first timed `on` command restored both ganged hubs; kernel re-enumerated the modem at 12:52:32, `eth1` returned at 12:52:37, and SSH/Tailscale returned without reboot. A second timed `on` command ran harmlessly as backup.
- The Pi logged a short undervoltage event during modem power-up. This is an observed power-margin risk; neither supply voltage nor modem current was measured.
- The Pi-specific auto-mode drop-in now opts into guarded full-hub recovery. The watchdog validates exact hub 2109:3431, modem 1286:4e3d on 1-1.1, `eth1` attachment, and absence of other USB devices; requires 12 network-failure runs and a USB reset in the current outage; arms two timed power-on commands before off; skips current undervoltage; and limits to four cycles per day with a one-hour cooldown.
- Local full suite passed 44 tests. Installed watchdog and drop-in SHA-256 matched local code. A natural and a manual systemd watchdog run reported healthy general connectivity, zero network-failure count, and `actions: []`. A no-op runner on the live Pi confirmed the topology validation and exact timer/off command order without cutting power again.
- Automatic recovery from a future real modem wedge remains unobserved. The supervised cycle proves the electrical path and reconnection behavior, not every future carrier or modem fault.

## Earlier cloud-history gap

- A read-only daily `active_power` query against the exporter's configured Influx bucket/site/device found zero points on Oct 10-20 UTC. The configured bucket also returned no measurements of any name in that window and no `active_power` points for other sites. This is a cloud-history gap, separate from today's spool repair.
- The Pi journal nevertheless logged successful one-point uploads on Oct 10 at 01:58-01:59 HKT and Oct 21 at 01:57-01:58 HKT. Those success messages do not establish that the points are still present in the currently configured bucket. The cause of the cloud gap remains unresolved; possible explanations include earlier bucket/configuration or retention changes.
- The consistent SQLite backup begins with Oct 21 data, and a bounded search of the Pi exporter data/code directories found no additional SQLite/CSV archive for Oct 10-20. This repair cannot claim that interval has been recovered. Preserve the journal and investigate historical Influx bucket/configuration and backup records separately.
