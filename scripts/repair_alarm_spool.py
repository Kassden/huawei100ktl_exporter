"""One-shot, backed-up repair of legacy alarm rows with colliding tag/field keys.

Stop huawei-exporter.service before --apply. The spool lock rejects concurrent use.
The backup is a consistent SQLite snapshot and is never overwritten.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from influxdb_client import Point, WritePrecision
from influxdb_writer import InfluxDBWriter
from telemetry_spool import TelemetrySpool


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def repaired_row(row):
    fields = json.loads(row["fields_json"])
    tags = json.loads(row["tags_json"])
    if "event_key" in tags and not (set(tags) & set(fields)):
        return None
    if "alarm_code" not in fields:
        raise ValueError(f"alarm row {row['id']} lacks numeric alarm_code field")
    if (set(tags) & set(fields)) - {"alarm_code", "source_bit"}:
        raise ValueError(f"alarm row {row['id']} has an unexpected tag/field collision")
    tags.pop("alarm_code", None)
    tags.pop("source_bit", None)
    bit = fields.get("source_bit")
    tags["event_key"] = InfluxDBWriter.alarm_event_key(int(fields["alarm_code"]), int(bit) if bit is not None else None)
    if set(tags) & set(fields):
        raise ValueError(f"alarm row {row['id']} still has a tag/field collision")
    timestamp = datetime.fromisoformat(row["source_ts"].replace("Z", "+00:00"))
    point = Point(row["measurement"])
    for key, value in tags.items():
        point = point.tag(key, value)
    for key, value in fields.items():
        point = point.field(key, value)
    line = point.time(timestamp, WritePrecision.S).to_line_protocol()
    field_text, tag_text = canonical_json(fields), canonical_json(tags)
    payload_hash = hashlib.sha256("\n".join([
        row["source_ts"], row["measurement"], row["site"], row["device"],
        field_text, tag_text, line,
    ]).encode()).hexdigest()
    identity_hash = hashlib.sha256("\n".join([
        row["source_ts"], row["measurement"], tag_text,
    ]).encode()).hexdigest()
    return field_text, tag_text, line, payload_hash, identity_hash


def repair(spool, *, apply=False, backup=None):
    conn = spool.conn
    rows = conn.execute(
        "SELECT * FROM telemetry_spool WHERE measurement='alarm_events' AND status='pending' ORDER BY id"
    ).fetchall()
    changes = [(row, repaired_row(row)) for row in rows]
    changes = [(row, change) for row, change in changes if change is not None]
    if not apply or not changes:
        return {"pending_alarm_rows": len(rows), "would_change": len(changes), "changed": 0}
    if backup is None:
        raise ValueError("--backup is required when applying a migration")
    backup_path = Path(backup)
    if backup_path.exists():
        raise FileExistsError(f"backup already exists: {backup_path}")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(backup_path), os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    os.close(fd)
    try:
        dest = sqlite3.connect(str(backup_path))
        try:
            conn.backup(dest)
            if dest.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("backup integrity check failed")
            before = conn.execute("SELECT count(*) FROM telemetry_spool").fetchone()[0]
            if dest.execute("SELECT count(*) FROM telemetry_spool").fetchone()[0] != before:
                raise RuntimeError("backup row count mismatch")
        finally:
            dest.close()
        with spool.transaction():
            for row, (field_text, tag_text, line, payload_hash, identity_hash) in changes:
                prior = conn.execute("SELECT payload_hash FROM point_identity WHERE identity_hash=?", (identity_hash,)).fetchone()
                if prior and prior[0] != payload_hash:
                    raise ValueError(f"identity collision for row {row['id']}")
                conn.execute("UPDATE telemetry_spool SET fields_json=?,tags_json=?,line_protocol=?,payload_hash=? WHERE id=? AND status='pending'",
                             (field_text, tag_text, line, payload_hash, row["id"]))
                conn.execute("INSERT OR IGNORE INTO point_identity(identity_hash,payload_hash,first_seen_at) VALUES(?,?,?)",
                             (identity_hash, payload_hash, row["created_at"]))
            after = conn.execute("SELECT count(*) FROM telemetry_spool").fetchone()[0]
            if before != after:
                raise RuntimeError("migration changed total row count")
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("post-migration integrity check failed")
        return {"pending_alarm_rows": len(rows), "would_change": len(changes), "changed": len(changes),
                "backup": str(backup_path), "total_rows": before}
    except BaseException:
        # Preserve the backup for forensic recovery even when migration fails.
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--backup")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    spool = TelemetrySpool(args.db).open()
    try:
        print(json.dumps(repair(spool, apply=args.apply, backup=args.backup), sort_keys=True))
    finally:
        spool.close()


if __name__ == "__main__":
    main()
