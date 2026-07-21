#!/usr/bin/env python3
"""
app.py — Hardened stdlib HTTP backend for the 3-tier demo.
Endpoints:
  GET  /health   → {"status":"ok","db":"connected","uptime_seconds":N}
  GET  /data     → {"records":[{"id":N,"key":"…","value":"…","created_at":"…"}]}
  POST /data     → 201: {"id":N,"key":"…","value":"…","created_at":"…"}
                   400: {"error":"…"}   validation failure
                   409: {"error":"…"}   duplicate key (UNIQUE constraint)
"""
from __future__ import annotations
import http.server, json, logging, os, signal, sys, time
from datetime import datetime, timezone
from threading import Event
import psycopg2, psycopg2.errors, psycopg2.extras

# ── OpenTelemetry (Week 2): manual SDK setup ────────────────────────────
# Not opentelemetry-instrument + opentelemetry-distro. Two reasons:
#   1. `pip install --target=...` (this Dockerfile's multi-stage builder)
#      never generates console-script wrappers — long-standing pip
#      limitation (#3813) — so opentelemetry-instrument isn't anywhere in
#      the image, at any path; the entrypoint fails with "executable file
#      not found in $PATH", not a missing-PATH-entry problem.
#   2. There's no official OTel auto-instrumentor for stdlib http.server
#      (only Flask/Django/FastAPI/WSGI/ASGI are supported), so even a
#      working opentelemetry-instrument would trace outbound DB calls but
#      never wrap an inbound request in its own span, and never emit the
#      http.server.duration metric the Sloth SLOs (slo/demo-app-*.slo.yaml)
#      key on. So all three signals are wired up here by hand, reading the
#      same OTEL_* env vars 04-backend.yaml sets.
from opentelemetry import trace, metrics
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import View, ExplicitBucketHistogramAggregation
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor


def _otel_resource() -> Resource:
    attrs = {"service.name": os.environ.get("OTEL_SERVICE_NAME", "backend")}
    for pair in os.environ.get("OTEL_RESOURCE_ATTRIBUTES", "").split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            attrs[k.strip()] = v.strip()
    return Resource.create(attrs)


_OTLP_ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").rstrip("/")
_resource = _otel_resource()

_tracer_provider = TracerProvider(resource=_resource)
_tracer_provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{_OTLP_ENDPOINT}/v1/traces"))
)
trace.set_tracer_provider(_tracer_provider)
tracer = trace.get_tracer("backend")

# Sloth's latency SLO (slo/demo-app-latency.slo.yaml) queries
# http_server_duration_bucket{...,le="0.5"} — seconds, not the OTel SDK's
# millisecond-scale default buckets — so both the unit and the explicit
# bucket boundaries below have to match that (standard Prometheus HTTP
# duration-in-seconds buckets).
_duration_view = View(
    instrument_name="http.server.duration",
    aggregation=ExplicitBucketHistogramAggregation(
        boundaries=(0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0)
    ),
)
_meter_provider = MeterProvider(
    resource=_resource,
    metric_readers=[PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=f"{_OTLP_ENDPOINT}/v1/metrics")
    )],
    views=[_duration_view],
)
metrics.set_meter_provider(_meter_provider)
_http_duration = metrics.get_meter("backend").create_histogram(
    "http.server.duration", unit="s",
    description="Duration of HTTP requests handled by this backend",
)

_logger_provider = LoggerProvider(resource=_resource)
_logger_provider.add_log_record_processor(
    BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{_OTLP_ENDPOINT}/v1/logs"))
)

Psycopg2Instrumentor().instrument()

# ── Structured JSON logger ──────────────────────────────────────────────
class _JsonFormatter(logging.Formatter):
    def format(self, record):
        entry = {"ts": datetime.now(timezone.utc).isoformat(),
                 "level": record.levelname, "msg": record.getMessage()}
        # trace_id/span_id embedded in the body itself (not just as OTLP
        # log-record attributes) so Week 2 Step 10's LogQL
        # `|= "<traceID>"` substring search actually matches the line.
        span_ctx = trace.get_current_span().get_span_context()
        if span_ctx.is_valid:
            entry["trace_id"] = format(span_ctx.trace_id, "032x")
            entry["span_id"] = format(span_ctx.span_id, "016x")
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry)

_json_formatter = _JsonFormatter()
_sh = logging.StreamHandler(sys.stdout)
_sh.setFormatter(_json_formatter)
_otel_log_handler = LoggingHandler(logger_provider=_logger_provider)
_otel_log_handler.setFormatter(_json_formatter)
logging.basicConfig(level=logging.INFO, handlers=[_sh, _otel_log_handler])
log = logging.getLogger("backend")

# ── Graceful shutdown ───────────────────────────────────────────────────
_stop = Event()
def _on_signal(sig, _frame):
    log.info("signal %d received — graceful shutdown initiated", sig)
    _stop.set()
signal.signal(signal.SIGTERM, _on_signal)
signal.signal(signal.SIGINT,  _on_signal)
_start = time.monotonic()

# ── Database connection ─────────────────────────────────────────────────
_conn = None
def _get_conn():
    global _conn
    if _conn is None or _conn.closed:
        log.info("opening database connection to %s", os.environ.get("DB_HOST"))
        _conn = psycopg2.connect(
            host=os.environ["DB_HOST"],
            port=int(os.environ.get("DB_PORT", 5432)),
            dbname=os.environ["DB_NAME"],
            user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"],   # never logged
            sslmode=os.environ.get("DB_SSL_MODE", "prefer"),
            connect_timeout=5,
            options="-c application_name=hardened-backend",
        )
    return _conn

# ── HTTP request handler ─────────────────────────────────────────────────
_MAX_BODY = 64 * 1024   # 64 KB POST body cap — prevents OOM DoS

class _Handler(http.server.BaseHTTPRequestHandler):
    # Version suppression: removes BaseHTTP/x.x Python/3.x from Server: header
    server_version = ""
    sys_version    = ""

    def log_message(self, fmt, *args):
        log.info("http %s", fmt % args)

    def _json(self, code, body, *, extra_headers=None):
        """Send JSON response with mandatory security headers on every response."""
        self._status_code = code
        payload = json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type",           "application/json")
        self.send_header("Content-Length",         str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options",        "DENY")
        self.send_header("Cache-Control",          "no-store")
        self.send_header("Referrer-Policy",        "no-referrer")
        if extra_headers:
            for k, v in extra_headers.items(): self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def _err(self, code, msg): self._json(code, {"error": msg})

    def _read_json(self):
        """Read and parse JSON body. Rejects requests missing Content-Type,
        empty, or exceeding MAX_BODY before calling json.loads()."""
        ct = self.headers.get("Content-Type", "")
        if "application/json" not in ct:
            self._err(415, "Content-Type must be application/json"); return None
        n = int(self.headers.get("Content-Length", 0))
        if n == 0: self._err(400, "empty request body"); return None
        if n > _MAX_BODY: self._err(413, f"body exceeds {_MAX_BODY} bytes"); return None
        try: return json.loads(self.rfile.read(n))
        except json.JSONDecodeError as exc: self._err(400, f"invalid JSON: {exc}"); return None

    def do_GET(self):
        self._traced("GET", self._route_GET)

    def do_POST(self):
        self._traced("POST", self._route_POST)

    def _route_GET(self):
        if   self.path == "/health": self._health()
        elif self.path == "/data":   self._get_data()
        else: self._err(404, "not found")

    def _route_POST(self):
        if self.path == "/data": self._post_data()
        else: self._err(404, "not found")

    def _traced(self, method, route_handler):
        """Wraps each request in a SERVER span (parenting the DB spans
        psycopg2's instrumentation creates) and records the
        http.server.duration histogram the Sloth SLOs read."""
        start = time.monotonic()
        self._status_code = 0
        with tracer.start_as_current_span(
            f"{method} {self.path}",
            kind=trace.SpanKind.SERVER,
            attributes={"http.method": method, "http.target": self.path},
        ) as span:
            try:
                route_handler()
            finally:
                span.set_attribute("http.status_code", self._status_code)
                _http_duration.record(
                    time.monotonic() - start,
                    {"http.method": method, "http.status_code": self._status_code},
                )

    def _health(self):
        """K8s readinessProbe target. SELECT 1 confirms Tier 3 connectivity."""
        db = "disconnected"
        conn = None
        try:
            conn = _get_conn()
            with conn.cursor() as cur: cur.execute("SELECT 1")
            db = "connected"
        except Exception as exc:
            log.warning("health: DB ping failed — %s", exc)
            # Same reasoning as _get_data: without a rollback, one failed
            # ping leaves the cached connection aborted, and every ping
            # after it fails the same way even once the real problem clears.
            if conn:
                try: conn.rollback()
                except Exception: pass
        self._json(200, {"status":"ok","db":db,"uptime_seconds":round(time.monotonic()-_start)})

    def _get_data(self):
        """SELECT all rows from app_data, newest first."""
        conn = None
        try:
            conn = _get_conn()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT id, key, value, created_at FROM app_data ORDER BY id DESC")
                rows = [dict(r) for r in cur.fetchall()]
            self._json(200, {"records": rows})
        except Exception as exc:
            # Roll back so the cached connection isn't left in an aborted
            # transaction — without this, every later query on it (even
            # /health's unrelated SELECT 1) fails with "current transaction
            # is aborted" until the process restarts, masking the real error.
            if conn:
                try: conn.rollback()
                except Exception: pass
            log.error("GET /data: %s", exc, exc_info=True); self._err(500, "database error")

    def _post_data(self):
        """INSERT one record. psycopg2 %s placeholders prevent SQL injection."""
        body = self._read_json()
        if body is None: return
        key   = str(body.get("key",   "")).strip()
        value = str(body.get("value", "")).strip()
        if not key: self._err(400, "'key' is required"); return
        if len(key) > 255: self._err(400, "'key' exceeds 255 characters"); return
        if len(value) > 10_000: self._err(400, "'value' exceeds 10000 characters"); return
        conn = None
        try:
            conn = _get_conn()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "INSERT INTO app_data (key, value) VALUES (%s, %s)"
                    " RETURNING id, key, value, created_at",
                    (key, value))
                row = dict(cur.fetchone())
            conn.commit()
            log.info("inserted key=%r id=%d", key, row["id"])
            self._json(201, row, extra_headers={"Location": f"/data/{row['id']}"})
        except psycopg2.errors.UniqueViolation:
            if conn: conn.rollback()
            self._err(409, f"key '{key}' already exists")
        except Exception as exc:
            if conn:
                try: conn.rollback()
                except Exception: pass
            log.error("POST /data: %s", exc, exc_info=True); self._err(500, "database error")

# ── Entry point ──────────────────────────────────────────────────────────
def main():
    port = int(os.environ.get("PORT", 5000))
    log.info("starting backend port=%d db_host=%s db_name=%s",
             port, os.environ.get("DB_HOST","(not set)"),
             os.environ.get("DB_NAME","(not set)"))  # DB_PASSWORD never logged
    try:
        _get_conn()
        log.info("initial database connection established")
    except Exception as exc:
        log.warning("initial DB connect failed (readinessProbe will retry): %s", exc)
    server = http.server.HTTPServer(("0.0.0.0", port), _Handler)
    server.timeout = 5        # poll interval; controls SIGTERM responsiveness
    log.info("listening on 0.0.0.0:%d", port)
    while not _stop.is_set(): # exit when SIGTERM sets the stop flag
        server.handle_request()
    log.info("shutting down")
    server.server_close()

if __name__ == "__main__": main()
