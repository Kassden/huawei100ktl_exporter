"""Read back representative original-time points from the cutover backup.

Run on the Pi with its systemd EnvironmentFile; never print credentials.
"""

import argparse
import asyncio
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from influxdb_writer import InfluxDBWriter
from scripts.repair_alarm_spool import repaired_row
from telemetry_spool import SpoolRecord


async def verify(db_path, positions):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    writer = InfluxDBWriter()
    if not await writer.connect():
        raise RuntimeError("Influx connection failed")
    results = []
    try:
        for measurement in ("huawei_sun2000", "alarm_events"):
            count = conn.execute("SELECT count(*) FROM telemetry_spool WHERE measurement=?", (measurement,)).fetchone()[0]
            for position in positions:
                offset = {"earliest": 0, "middle": count // 2, "latest": count - 1}[position]
                row = conn.execute(
                    "SELECT * FROM telemetry_spool WHERE measurement=? ORDER BY source_ts,id LIMIT 1 OFFSET ?",
                    (measurement, offset),
                ).fetchone()
                tags = json.loads(row["tags_json"])
                fields = json.loads(row["fields_json"])
                if measurement == "alarm_events":
                    change = repaired_row(row)
                    if change:
                        tags = json.loads(change[1])
                record = SpoolRecord(row["id"], row["source_ts"], measurement, row["site"], row["device"],
                                     fields, tags, row["line_protocol"], row["payload_hash"], row["attempts"])
                found = await writer.verify_spool_records([record])
                results.append({"measurement": measurement, "position": position, "id": row["id"],
                                "source_ts": row["source_ts"], "verified": found})
    finally:
        await writer.disconnect()
        conn.close()
    print(json.dumps(results, sort_keys=True))
    return all(row["verified"] for row in results)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--positions", nargs="+", choices=("earliest", "middle", "latest"), required=True)
    args = parser.parse_args()
    if not asyncio.run(verify(args.db, args.positions)):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
