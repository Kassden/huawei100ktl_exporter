import json
import os
import shutil
import sqlite3
import hashlib
import math
import fcntl
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class SpoolRecord:
    id: int
    source_ts: str
    measurement: str
    site: str
    device: str
    fields: Dict[str, Any]
    tags: Dict[str, str]
    line_protocol: str
    payload_hash: str
    attempts: int


class TelemetrySpool:
    def __init__(
        self,
        path: str,
        max_bytes: int = 1_073_741_824,
        min_free_bytes: int = 536_870_912,
        batch_points: int = 100,
        batch_bytes: int = 262_144,
    ):
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.min_free_bytes = min_free_bytes
        self.batch_points = batch_points
        self.batch_bytes = batch_bytes
        self.conn: Optional[sqlite3.Connection] = None
        self.lock_file = None
        self.rejected_points = 0

    def open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        self.lock_file = open(f"{self.path}.lock", "a+")
        os.chmod(f"{self.path}.lock", 0o600)
        try:
            fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock_file.close()
            self.lock_file = None
            raise RuntimeError(f"Telemetry spool is already in use: {self.path}")
        self.conn = sqlite3.connect(str(self.path), timeout=5, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, SCHEMA_VERSION):
            self.close()
            raise RuntimeError(f"Unsupported telemetry spool schema version: {version}")
        if self.conn.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() != "wal":
            self.close()
            raise RuntimeError("Telemetry spool WAL mode unavailable")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS telemetry_spool (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              source_ts TEXT NOT NULL,
              measurement TEXT NOT NULL,
              site TEXT NOT NULL,
              device TEXT NOT NULL,
              fields_json TEXT NOT NULL,
              tags_json TEXT NOT NULL,
              line_protocol TEXT NOT NULL,
              payload_hash TEXT NOT NULL UNIQUE,
              schema_version INTEGER NOT NULL DEFAULT 1,
              status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','acknowledged','quarantined')),
              attempts INTEGER NOT NULL DEFAULT 0,
              next_attempt_at TEXT,
              last_error TEXT,
              created_at TEXT NOT NULL,
              acknowledged_at TEXT,
              drained_at TEXT
            );
            CREATE INDEX IF NOT EXISTS telemetry_spool_pending
              ON telemetry_spool(status,next_attempt_at,source_ts,id);
            CREATE TABLE IF NOT EXISTS collector_state (
              key TEXT PRIMARY KEY,
              value_json TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS point_identity (
              identity_hash TEXT PRIMARY KEY,
              payload_hash TEXT NOT NULL,
              first_seen_at TEXT NOT NULL
            );
            """
        )
        self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        os.chmod(self.path, 0o600)
        if self.conn.execute("PRAGMA synchronous").fetchone()[0] != 2:
            self.close()
            raise RuntimeError("Telemetry spool FULL synchronous mode unavailable")
        return self

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None
        if self.lock_file:
            fcntl.flock(self.lock_file, fcntl.LOCK_UN)
            self.lock_file.close()
            self.lock_file = None

    @contextmanager
    def transaction(self):
        self._require_conn()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    def enqueue_collection(self, points: Iterable[Dict[str, Any]], alarm_snapshot: Dict[str, Any]):
        with self.transaction():
            for point in points:
                self.enqueue(**point)
            self.set_state("last_alarm_snapshot", alarm_snapshot)

    def enqueue(
        self,
        *,
        source_ts: datetime,
        measurement: str,
        site: str,
        device: str,
        fields: Dict[str, Any],
        tags: Dict[str, str],
        line_protocol: str,
    ) -> bool:
        self._require_conn()
        self._check_capacity()
        if not fields or any(not self._valid_field(value) for value in fields.values()):
            raise ValueError("spool point must contain at least one finite field")
        if source_ts.tzinfo is None or source_ts.utcoffset() is None:
            raise ValueError("spool source timestamp must have a timezone")
        if tags.get("site_id") != site or tags.get("device_id") != device:
            raise ValueError("spool site/device do not match point tags")
        canonical_fields = json.dumps(fields, sort_keys=True, separators=(",", ":"), allow_nan=False)
        canonical_tags = json.dumps(tags, sort_keys=True, separators=(",", ":"))
        source_text = source_ts.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        payload_hash = hashlib.sha256(
            "\n".join([source_text, measurement, site, device, canonical_fields, canonical_tags, line_protocol]).encode("utf-8")
        ).hexdigest()
        identity_hash = hashlib.sha256(
            "\n".join([source_text, measurement, canonical_tags]).encode("utf-8")
        ).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        prior = self.conn.execute(
            "SELECT payload_hash FROM point_identity WHERE identity_hash = ?",
            (identity_hash,),
        ).fetchone()
        if prior:
            if prior["payload_hash"] == payload_hash:
                return False
            raise ValueError("Conflicting telemetry payload for an existing Influx point identity")
        self.conn.execute("SAVEPOINT enqueue_point")
        try:
            self.conn.execute(
                "INSERT INTO point_identity(identity_hash, payload_hash, first_seen_at) VALUES(?, ?, ?)",
                (identity_hash, payload_hash, now),
            )
            self.conn.execute(
                """
                INSERT INTO telemetry_spool(
                  source_ts, measurement, site, device, fields_json, tags_json,
                  line_protocol, payload_hash, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_text,
                    measurement,
                    site,
                    device,
                    canonical_fields,
                    canonical_tags,
                    line_protocol,
                    payload_hash,
                    now,
                ),
            )
            self.conn.execute("RELEASE enqueue_point")
            return True
        except Exception:
            self.conn.execute("ROLLBACK TO enqueue_point")
            self.conn.execute("RELEASE enqueue_point")
            raise

    def acknowledged_batch(self) -> List[SpoolRecord]:
        self._require_conn()
        rows = self.conn.execute(
            """SELECT * FROM telemetry_spool WHERE status = 'acknowledged'
               AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
               ORDER BY source_ts ASC, id ASC LIMIT ?""",
            (datetime.now(timezone.utc).isoformat(), self.batch_points),
        ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def pending_batch(self) -> List[SpoolRecord]:
        self._require_conn()
        rows = self.conn.execute(
            """
            SELECT * FROM telemetry_spool
            WHERE status = 'pending'
              AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
            ORDER BY source_ts DESC, id DESC
            LIMIT 10
            """,
            (datetime.now(timezone.utc).isoformat(),),
        ).fetchall()
        newest_ids = {row["id"] for row in rows}
        if len(rows) < self.batch_points:
            older = self.conn.execute(
                """
                SELECT * FROM telemetry_spool
                WHERE status = 'pending'
                  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                ORDER BY source_ts ASC, id ASC
                LIMIT ?
                """,
                (datetime.now(timezone.utc).isoformat(), self.batch_points - len(rows)),
            ).fetchall()
            rows.extend(row for row in older if row["id"] not in newest_ids)

        records: List[SpoolRecord] = []
        bytes_used = 0
        for row in rows:
            row_bytes = len(row["line_protocol"].encode("utf-8"))
            if records and bytes_used + row_bytes > self.batch_bytes:
                break
            bytes_used += row_bytes
            records.append(self._row_to_record(row))
        return records

    def mark_acknowledged(self, records: Iterable[SpoolRecord]):
        self._require_conn()
        now = datetime.now(timezone.utc).isoformat()
        self.conn.executemany(
            "UPDATE telemetry_spool SET status = 'acknowledged', acknowledged_at = ?, next_attempt_at = NULL, attempts = attempts + 1 WHERE id = ?",
            [(now, record.id) for record in records],
        )

    def mark_verification_error(self, records: Iterable[SpoolRecord], error: str):
        self._require_conn()
        retry_at = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + 60, tz=timezone.utc).isoformat()
        self.conn.executemany(
            "UPDATE telemetry_spool SET next_attempt_at = ?, last_error = ? WHERE id = ? AND status = 'acknowledged'",
            [(retry_at, error[:500], record.id) for record in records],
        )

    def mark_pending_error(self, records: Iterable[SpoolRecord], error: str):
        self._require_conn()
        now = datetime.now(timezone.utc)
        retry_at = now.timestamp() + 5
        retry_text = datetime.fromtimestamp(retry_at, tz=timezone.utc).isoformat()
        self.conn.executemany(
            """
            UPDATE telemetry_spool
            SET status = 'pending', attempts = attempts + 1, next_attempt_at = ?, last_error = ?
            WHERE id = ?
            """,
            [(retry_text, error[:500], record.id) for record in records],
        )

    def mark_drained(self, records: Iterable[SpoolRecord]):
        self._require_conn()
        self.conn.executemany(
            "DELETE FROM telemetry_spool WHERE id = ? AND status = 'acknowledged'",
            [(record.id,) for record in records],
        )
        wal_path = Path(f"{self.path}-wal")
        if wal_path.exists() and wal_path.stat().st_size > 64 * 1024 * 1024:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def quarantine(self, record: SpoolRecord, error: str):
        self._require_conn()
        self.conn.execute(
            "UPDATE telemetry_spool SET status = 'quarantined', last_error = ? WHERE id = ?",
            (error[:500], record.id),
        )

    def get_state(self, key: str) -> Optional[Dict[str, Any]]:
        self._require_conn()
        row = self.conn.execute("SELECT value_json FROM collector_state WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value_json"]) if row else None

    def set_state(self, key: str, value: Dict[str, Any]):
        self._require_conn()
        self.conn.execute(
            """
            INSERT INTO collector_state(key, value_json, updated_at)
            VALUES(?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
            """,
            (
                key,
                json.dumps(value, sort_keys=True, separators=(",", ":")),
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    def stats(self) -> Dict[str, Any]:
        self._require_conn()
        counts = {
            row["status"]: row["count"]
            for row in self.conn.execute("SELECT status, count(*) AS count FROM telemetry_spool GROUP BY status")
        }
        oldest = self.conn.execute(
            "SELECT source_ts FROM telemetry_spool WHERE status = 'pending' ORDER BY source_ts ASC LIMIT 1"
        ).fetchone()
        return {
            "spool_pending": counts.get("pending", 0),
            "spool_acknowledged": counts.get("acknowledged", 0),
            "spool_quarantined": counts.get("quarantined", 0),
            "spool_oldest_at": oldest["source_ts"] if oldest else None,
            "spool_bytes": self._db_bytes(),
            "disk_free_bytes": shutil.disk_usage(self.path.parent).free,
            "spool_rejected_points": self.rejected_points,
        }

    def backup(self, destination: str):
        self._require_conn()
        target = sqlite3.connect(destination)
        try:
            self.conn.backup(target)
        finally:
            target.close()

    def _row_to_record(self, row: sqlite3.Row) -> SpoolRecord:
        return SpoolRecord(
            id=row["id"],
            source_ts=row["source_ts"],
            measurement=row["measurement"],
            site=row["site"],
            device=row["device"],
            fields=json.loads(row["fields_json"]),
            tags=json.loads(row["tags_json"]),
            line_protocol=row["line_protocol"],
            payload_hash=row["payload_hash"],
            attempts=row["attempts"],
        )

    def _require_conn(self):
        if self.conn is None:
            raise RuntimeError("Telemetry spool is not open")

    def _check_capacity(self):
        if self._db_bytes() >= self.max_bytes or shutil.disk_usage(self.path.parent).free <= self.min_free_bytes:
            self.rejected_points += 1
            raise RuntimeError("Telemetry spool capacity guard rejected new point")

    def _db_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            file_path = Path(f"{self.path}{suffix}")
            if file_path.exists():
                total += file_path.stat().st_size
        return total

    @staticmethod
    def _valid_field(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, bool):
            return True
        if isinstance(value, (int, float)):
            return math.isfinite(value)
        return isinstance(value, str)
