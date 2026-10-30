# Oct 30 Pi incident baseline (HKT)

- At 12:17, Pi boot time was Sep 23 09:20:44; `huawei-exporter`, `tailscaled`, and `dtu-watchdog.timer` were active.
- SQLite `PRAGMA integrity_check` was `ok`. At 12:17 the spool contained 12,402 pending rows: 12,294 `huawei_sun2000` and 108 `alarm_events`; source timestamps ranged Oct 21 17:57:14 to Oct 30 12:17:15.
- Old alarm rows stored `alarm_code` as both a string tag and a numeric field, and some stored `source_bit` both ways. The prior Influx HTTP 400 reported `alarm_code` tag conflicting with a float field in `alarm_events`; its response indicated partial write. That allowed fresh telemetry while the batch remained pending.
- The watchdog recorded many NetworkManager reconnects and twelve USB resets across Oct 29 and Oct 30 without restoring general connectivity. The onsite physical 4G switch caused kernel USB disconnect and re-enumeration, then restored network. This supports USB reset insufficiency for that episode, but does not identify whether modem firmware, carrier, or USB power caused the wedge.
- USB topology: Pi 4B onboard VIA 2109:3431 USB2 hub has the Marvell 1286:4e3d mobile composite card on port 1 only. Hub descriptor reports ganged power switching. Actual electrical VBUS removal was unproven at baseline.
- The cutover backup captured 12,406 rows; count rose by four before service stop as collection continued. No rows were deleted by migration.
