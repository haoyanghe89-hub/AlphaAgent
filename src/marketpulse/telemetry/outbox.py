"""Durable, project-scoped OTLP delivery. Acknowledged batches retain their identity."""

import hashlib
import sqlite3
import time
from contextlib import contextmanager

import httpx
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult


class Outbox(SpanExporter):
    def __init__(self, config):
        self.export_errors = 0
        self._closed = False
        self.config = config
        self.transport = httpx.Client(
            auth=(config.public_key, config.secret_key),
            timeout=config.timeout,
            follow_redirects=False,
        )
        self.path = config.journal_path
        self.project = hashlib.sha256(
            (config.base_url + "|" + config.public_key).encode()
        ).hexdigest()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY, project TEXT NOT NULL, path TEXT NOT NULL,
                    content_type TEXT NOT NULL, body BLOB, state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
                    lease_until REAL NOT NULL DEFAULT 0, last_error TEXT, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (
                    project TEXT NOT NULL, run_id TEXT NOT NULL, segment INTEGER NOT NULL,
                    trace_id TEXT NOT NULL, observation_id TEXT NOT NULL, session_id TEXT NOT NULL,
                    parent_run_id TEXT, resumed_from_trace_id TEXT, state TEXT NOT NULL,
                    started REAL NOT NULL, finished REAL, PRIMARY KEY(project,run_id,segment));
                CREATE TABLE IF NOT EXISTS calls (
                    project TEXT NOT NULL, run_id TEXT NOT NULL, call_index INTEGER NOT NULL,
                    kind TEXT NOT NULL, trace_id TEXT, observation_id TEXT, request_ref TEXT,
                    state TEXT NOT NULL, PRIMARY KEY(project,run_id,call_index,kind));
                CREATE TABLE IF NOT EXISTS health (
                    project TEXT PRIMARY KEY, dropped INTEGER DEFAULT 0);
            """)
            db.execute("BEGIN IMMEDIATE")
            if "delivery_errors" not in {r[1] for r in db.execute("PRAGMA table_info(health)")}:
                db.execute("ALTER TABLE health ADD COLUMN delivery_errors INTEGER DEFAULT 0")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def enqueue(self, path, content_type, body):
        key = hashlib.sha256(self.project.encode() + path.encode() + body).hexdigest()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            size = db.execute(
                "SELECT COUNT(*),COALESCE(SUM(LENGTH(body)),0) "
                "FROM batches WHERE project=? AND state!='SENT'",
                (self.project,),
            ).fetchone()
            if size[0] >= 10000 or size[1] + len(body) > 128 * 1024 * 1024:
                db.execute(
                    (
                        "INSERT INTO health(project,dropped) VALUES(?,1) ON CONFLICT(project) DO "
                        "UPDATE SET dropped=dropped+1"
                    ),
                    (self.project,),
                )
                return False
            db.execute(
                (
                    "INSERT OR IGNORE INTO "
                    "batches(id,project,path,content_type,body,state,created) "
                    "VALUES(?,?,?,?,?,?,?)"
                ),
                (key, self.project, path, content_type, body, "PENDING", time.time()),
            )
        return True

    def export(self, spans):
        try:
            packet = encode_spans(spans)
            for resource in packet.resource_spans:
                for scope in resource.scope_spans:
                    for span in scope.spans:
                        # Automatic exception events/status can contain raw private inputs.
                        span.ClearField("events")
                        span.status.message = ""
            ok = self.enqueue(
                "/api/public/otel/v1/traces",
                "application/x-protobuf",
                packet.SerializeToString(),
            )
            return SpanExportResult.SUCCESS if ok else SpanExportResult.FAILURE
        except (OSError, sqlite3.Error):
            self.export_errors += 1
            return SpanExportResult.FAILURE

    def drain(self, *, limit=1, force=True):
        if not self.config.configured or self._closed:
            return 0
        sent = 0
        deadline = time.monotonic() + self.config.timeout
        for _ in range(limit):
            if time.monotonic() >= deadline:
                break
            now = time.time()
            with self.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    (
                        "SELECT * FROM batches WHERE project=? AND state!='SENT' "
                        "AND lease_until<=? "
                        "AND (next_attempt<=? OR ?) ORDER BY created LIMIT 1"
                    ),
                    (self.project, now, now, force),
                ).fetchone()
                if row is None:
                    break
                db.execute(
                    "UPDATE batches SET state='INFLIGHT',lease_until=?,"
                    "attempts=attempts+1 WHERE id=?",
                    (now + 4 * self.config.timeout + 5, row["id"]),
                )
            error = None
            try:
                response = self.transport.post(
                    self.config.base_url + row["path"],
                    content=row["body"],
                    headers={
                        "Content-Type": row["content_type"],
                        "x-langfuse-ingestion-version": "4",
                    },
                )
                if response.status_code not in (200, 201, 202):
                    error = "HTTP_" + str(response.status_code)
                elif (
                    row["content_type"] == "application/x-protobuf"
                    and response.content
                    and "protobuf" in response.headers.get("content-type", "")
                ):
                    packet = ExportTraceServiceResponse.FromString(response.content)
                    if packet.partial_success.rejected_spans:
                        error = "OTLP_PARTIAL_REJECTION"
            except Exception as exception:
                error = type(exception).__name__
            with self.connection() as db:
                if error:
                    db.execute(
                        "INSERT INTO health(project,delivery_errors) VALUES(?,1) "
                        "ON CONFLICT(project) DO UPDATE SET delivery_errors=delivery_errors+1",
                        (self.project,),
                    )
                    db.execute(
                        "UPDATE batches SET state='PENDING',lease_until=0,"
                        "last_error=?,next_attempt=? WHERE id=?",
                        (
                            error,
                            time.time() + min(300, 2 ** min(row["attempts"] + 1, 8)),
                            row["id"],
                        ),
                    )
                else:
                    db.execute(
                        "UPDATE batches SET state='SENT',body=NULL,lease_until=0,"
                        "last_error=NULL WHERE id=?",
                        (row["id"],),
                    )
                    sent += 1
            if error:
                break
        return sent

    def start_run(self, run_id, trace_id, observation_id, *, session_id, parent_run_id=None):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT * FROM runs WHERE project=? AND run_id=? ORDER BY segment DESC LIMIT 1",
                (self.project, run_id),
            ).fetchone()
            segment = prior["segment"] + 1 if prior else 1
            db.execute(
                "INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    self.project,
                    run_id,
                    segment,
                    trace_id,
                    observation_id,
                    session_id,
                    parent_run_id,
                    prior["trace_id"] if prior else None,
                    "RUNNING",
                    time.time(),
                    None,
                ),
            )
        return segment, prior["trace_id"] if prior else None

    def record_call(self, run_id, call_index, kind, trace_id, observation_id, request_ref, state):
        with self.connection() as db:
            db.execute(
                "INSERT INTO calls VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(project,run_id,call_index,kind) DO UPDATE SET state=excluded.state",
                (
                    self.project,
                    run_id,
                    call_index,
                    kind,
                    trace_id,
                    observation_id,
                    request_ref,
                    state,
                ),
            )

    def finish_run(self, run_id, segment, state):
        with self.connection() as db:
            db.execute(
                "UPDATE runs SET state=?,finished=? WHERE project=? AND run_id=? AND segment=?",
                (state, time.time(), self.project, run_id, segment),
            )

    def runs(self, run_id):
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM runs WHERE project=? AND run_id=? ORDER BY segment",
                    (self.project, run_id),
                )
            ]

    def status(self):
        with self.connection() as db:
            row = db.execute(
                (
                    "SELECT COUNT(*) AS pending,COALESCE(SUM(LENGTH(body)),0) AS "
                    "bytes,MIN(created) AS oldest FROM batches WHERE project=? AND state!='SENT'"
                ),
                (self.project,),
            ).fetchone()
            dropped = db.execute(
                "SELECT dropped,delivery_errors FROM health WHERE project=?", (self.project,)
            ).fetchone()
            return dict(
                row,
                export_errors=self.export_errors,
                configured=self.config.configured,
                dropped=dropped[0] if dropped else 0,
                delivery_errors=dropped[1] if dropped else 0,
            )

    def shutdown(self):
        # TracerProvider shuts down before Runtime's final journal drain.
        pass

    def close(self):
        if not self._closed:
            self._closed = True
            self.transport.close()
