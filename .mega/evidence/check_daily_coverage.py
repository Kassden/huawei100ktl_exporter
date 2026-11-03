"""Read-only daily count of active_power telemetry in Influx Cloud."""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config import config
from influxdb_client import InfluxDBClient


def main():
    start = datetime(2026, 9, 27, tzinfo=timezone.utc)
    stop = datetime(2026, 10, 31, tzinfo=timezone.utc)
    query = f'''
      from(bucket: "{config.influxdb.bucket}")
        |> range(start: {start.isoformat().replace('+00:00', 'Z')}, stop: {stop.isoformat().replace('+00:00', 'Z')})
        |> filter(fn: (r) => r._measurement == "{config.influxdb.measurement}")
        |> filter(fn: (r) => r._field == "active_power")
        |> filter(fn: (r) => r.site_id == "{config.exporter.site_id}" and r.device_id == "{config.exporter.device_id}")
        |> aggregateWindow(every: 1d, fn: count, createEmpty: false)
    '''
    counts = {}
    gap_start = "2026-10-10T00:00:00Z"
    gap_stop = "2026-10-21T00:00:00Z"
    with InfluxDBClient(url=config.influxdb.url, token=config.influxdb.token, org=config.influxdb.org) as client:
        for table in client.query_api().query(query=query, org=config.influxdb.org):
            for row in table.records:
                day = (row.get_time() - timedelta(microseconds=1)).date().isoformat()
                counts[day] = counts.get(day, 0) + int(row.get_value())
        schema_query = f'''
          import "influxdata/influxdb/schema"
          schema.measurements(bucket: "{config.influxdb.bucket}", start: {gap_start}, stop: {gap_stop})
        '''
        measurements = sorted({row.get_value() for table in client.query_api().query(query=schema_query, org=config.influxdb.org)
                               for row in table.records})
        alternate_query = f'''
          from(bucket: "{config.influxdb.bucket}")
            |> range(start: {gap_start}, stop: {gap_stop})
            |> filter(fn: (r) => r._measurement == "{config.influxdb.measurement}" and r._field == "active_power")
            |> group()
            |> count()
        '''
        any_site_count = sum(int(row.get_value()) for table in client.query_api().query(query=alternate_query, org=config.influxdb.org)
                             for row in table.records)
    days = {}
    current = start
    while current < stop:
        days[current.date().isoformat()] = counts.get(current.date().isoformat(), 0)
        current += timedelta(days=1)
    print(json.dumps({"daily_site_active_power": days, "gap_measurements": measurements,
                      "gap_any_site_active_power_count": any_site_count}, sort_keys=True))


if __name__ == "__main__":
    main()
