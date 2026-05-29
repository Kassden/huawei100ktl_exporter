# Current Weather Persistence Plan

## GOAL
- Persist current ambient weather and solar-position fields onto every inverter telemetry row while keeping inverter polling at 60 seconds and weather polling at Open-Meteo current resolution, default 900 seconds.

## Research
- Built-in options: enrich the existing `TelemetryPoint.measurements` dict before Influx write, because the writer already persists arbitrary non-null measurement fields.
- Off-the-shelf options: Open-Meteo current weather endpoint via the existing `requests` dependency; no new runtime dependency is required.
- Academic / standards / official sources: Open-Meteo current weather documentation states current conditions include an `interval` and are based on 15-minute model data.

## Regression Guardrail
- Planned edit surface: exporter weather config/client/cache/enrichment/tests/docs; dashboard read-only weather fields if consumed.
- Protected behavior: Modbus polling cadence, Influx measurement/tags, alarm event writes, controls/settings safety, cabinet temperature semantics.
- Likely consumers: Influx analytics, dashboard insights, operators, future weather/production analysis.
- Damage radius: Large, additive data-surface change written on every telemetry row.
- Proof: unit tests for weather config/client/cache/enrichment, dashboard tests/build if dashboard is touched, real Influx readback when credentials are available.

## Phase 1: Exporter Weather Producer

### Subphase 1.1: Config And Client
- Commit: `feat(weather): add Open-Meteo current weather client`
- Tests: `python -m unittest test_weather_client.py`
- Success Criteria: weather config parses safely and Open-Meteo current responses normalize without touching telemetry collection.
- Checklist:
  - [x] Add disabled-by-default weather/site config.
  - [x] Add Open-Meteo current weather client.
  - [x] Parse `current.time`, `current.interval`, and current variables.
  - [x] Handle provider failures as unavailable state.

### Subphase 1.2: Cache And Telemetry Enrichment
- Commit: `feat(weather): enrich inverter telemetry with cached current weather`
- Tests: `python -m unittest test_weather_enrichment.py test_alarm_events.py`
- Success Criteria: 60-second telemetry rows receive cached weather fields while provider fetch cadence remains 15 minutes by default.
- Checklist:
  - [x] Add collector-owned weather cache.
  - [x] Refresh weather only when cache is due.
  - [x] Merge weather fields into `TelemetryPoint.measurements`.
  - [x] Preserve alarm events and existing telemetry collection behavior.

### Subphase 1.3: Solar Position Fields
- Commit: `feat(weather): add solar position fields to telemetry`
- Tests: `python -m unittest test_solar_position.py test_weather_enrichment.py`
- Success Criteria: telemetry rows get deterministic solar geometry fields independent of weather API availability.
- Checklist:
  - [x] Add local solar-position helper.
  - [x] Compute azimuth/elevation/zenith/cos zenith/daylight.
  - [x] Enrich rows using telemetry timestamp and site coordinates.

### Subphase 1.4: Exporter Docs
- Commit: `docs(weather): document current weather telemetry enrichment`
- Tests: README/env review.
- Success Criteria: operators can enable weather safely and understand polling/staleness behavior.
- Checklist:
  - [x] Document env vars.
  - [x] Document 15-minute weather refresh.
  - [x] Document failure/staleness behavior.

## Phase 2: Dashboard Weather Consumer

### Subphase 2.1: Read Weather Fields
- Commit: `feat(dashboard): read ambient weather telemetry fields`
- Tests: `npm test`, `npm run build`
- Success Criteria: dashboard insight data can read latest ambient weather additively.
- Checklist:
  - [x] Extend latest telemetry field list.
  - [x] Extend insight types/calculation output.
  - [x] Keep cabinet temperature semantics unchanged.

### Subphase 2.2: Display Ambient Weather Separately
- Commit: `feat(dashboard): show ambient weather beside cabinet temperature`
- Tests: `npm test`, `npm run build`
- Success Criteria: UI distinguishes ambient weather from inverter cabinet temperature and handles stale/unavailable state.
- Checklist:
  - [x] Add ambient weather display.
  - [x] Add stale/unavailable state.
  - [x] Add i18n keys if user-facing text is introduced.

## Phase 3: Verification

### Subphase 3.1: Local Verification
- Commit: no commit unless fixes are required.
- Tests: exporter unit tests, dashboard `npm test`, dashboard `npm run build`.
- Success Criteria: local test/build evidence covers touched contracts.
- Checklist:
  - [x] Run exporter tests.
  - [x] Run dashboard tests if touched.
  - [x] Run dashboard build if touched.
  - [x] Inspect scope stayed within this plan.

### Subphase 3.2: Runtime Smoke
- Commit: no commit unless fixes are required.
- Tests: live exporter smoke and Influx readback when credentials/environment are available.
- Success Criteria: weather fields appear in real telemetry rows and weather API calls do not happen every 60 seconds.
- Checklist:
  - [x] Confirm weather fetch cadence.
  - [ ] Confirm telemetry write cadence.
  - [ ] Query latest Influx row for weather fields.
