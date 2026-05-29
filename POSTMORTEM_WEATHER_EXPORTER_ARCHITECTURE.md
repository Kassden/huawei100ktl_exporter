# Postmortem: Incorrect Weather Enrichment In Edge Exporter

## Summary

On 2026-05-29, weather enrichment was implemented in the Huawei inverter exporter. This was a critical architecture error. The exporter runs on an edge computing device and should stay focused on inverter telemetry, alarms, and explicitly authorized inverter controls/settings. Ambient weather collection is not inverter telemetry and should not have been added to the edge exporter.

The changes were reverted with normal revert commits. No destructive history rewrite was used.

## Impact

- The edge exporter was expanded beyond its intended responsibility.
- The implementation added external internet dependency risk to a device whose primary job is reliable local inverter collection.
- The design increased operational coupling between inverter telemetry and a third-party weather API.
- The dashboard displayed ambient weather as unavailable because live Influx rows did not contain weather fields; this exposed that the persistence architecture had not been validated against the real deployment model.
- The work consumed implementation time on the wrong subsystem.

## Root Cause

The planning assumed that because weather should be persisted with telemetry for analytics, the producer should be the edge exporter. That assumption was wrong.

The missing architectural question was:

> Which system should own non-inverter enrichment?

The correct answer is not the edge exporter. Weather is site-context enrichment and belongs in a cloud-side data job or backend service where internet dependency, retries, caching, observability, and provider changes can be managed without risking inverter collection.

## Contributing Factors

- The requirement "weather should be written in the database for every row" was interpreted as a write-location requirement instead of a data-model requirement.
- The edge runtime constraints were not explicitly confirmed before implementation.
- Regression guardrails focused on additive field safety, but did not sufficiently challenge subsystem ownership.
- Verification proved Open-Meteo and local tests worked, but did not prove that the deployment architecture was appropriate.
- Dashboard fallback work began before resolving the core producer ownership error.

## What Was Reverted

Exporter changes reverted:

- Weather config/env variables.
- Open-Meteo client and cache.
- Telemetry enrichment with `weather_*` fields.
- Solar-position enrichment in the exporter.
- Weather-related exporter tests.
- Weather-related exporter documentation and plan.

Dashboard changes reverted:

- Weather fields in latest telemetry queries.
- Weather insight types/calculations.
- Ambient weather display in the thermal card.
- Weather i18n/test additions.

## Correct Architecture Going Forward

The edge exporter should remain responsible for:

- Inverter Modbus reads.
- Inverter alarm/state event persistence.
- Safe inverter controls/settings when explicitly enabled.
- Local health/readiness for inverter collection.

Weather should be owned by a cloud-side service:

- Fetch Open-Meteo current weather every 15 minutes using site coordinates.
- Write to a separate Influx measurement such as `site_weather`.
- Tag by `site_id`, and optionally `customer_slug` or `location_id`.
- Store observation timestamp, provider interval, and provider name.
- Join weather with inverter telemetry in dashboard/analytics queries by nearest timestamp or aggregation window.
- Keep historical backfill as a cloud job, not an edge job.

## Prevention Rules

- Before adding a new external dependency to an edge service, explicitly confirm it is edge-owned.
- Treat "persisted near telemetry" as a data requirement, not proof that the telemetry producer should collect it.
- For edge devices, default to fewer responsibilities unless the user explicitly asks for edge-side enrichment.
- Add a planning checkpoint for subsystem ownership:
  - source of truth
  - runtime location
  - failure isolation
  - observability
  - deployment cadence
- Do not start dashboard fallbacks that mask missing backend architecture unless explicitly approved.

## Status

- Revert completed.
- Correct next plan should implement weather as a cloud/backend data pipeline, not in `huawei100ktl_exporter`.
