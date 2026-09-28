#!/usr/bin/env python3
"""Local, harness-neutral receiver for mc-agent daemon webhook events.

Verifies the daemon's HMAC-SHA256 signature and timestamp, bounds the body
size, de-duplicates the stable event id with a TTL, prints one JSON line per
accepted event, and can forward the verified event to another receiver. An
event is only marked delivered after the downstream receiver accepts it; a
transient downstream failure returns a retryable 503 so the daemon retries the
same event id instead of losing it.

It is a development/verification tool: the product itself has no Python
requirement.

    # 1. Start a receiver with a test secret
    python tools/webhook_receiver.py serve --secret test-secret --port 8645

    # 2. Start the daemon (or a fake daemon) with forwarding enabled
    mc-agent daemon run --fake --webhook-url http://127.0.0.1:8645/hook \
        --webhook-secret test-secret --webhook-events "*"

    # 3. Trigger an event and watch the receiver print it

    # Self-check without a daemon:
    python tools/webhook_receiver.py selftest

Forwarding to a Hermes generic webhook route (HMAC V2 headers, `webhook-id`
de-duplication) is explicit:

    python tools/webhook_receiver.py serve --secret test-secret \
        --forward-url http://127.0.0.1:8644/webhooks/mc-chat \
        --forward-secret <hermes-route-secret> --scheme hermes-v2
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SIGNATURE_HEADER = "X-MC-Agent-Signature"
TIMESTAMP_HEADER = "X-MC-Agent-Timestamp"
EVENT_ID_HEADER = "X-MC-Agent-Event-Id"
EVENT_TYPE_HEADER = "X-MC-Agent-Event-Type"
ATTEMPT_HEADER = "X-MC-Agent-Attempt"

DEFAULT_MAX_SKEW_SECONDS = 300
DEFAULT_PORT = 8645
MAX_BODY_BYTES = 1024 * 1024
DEDUP_TTL_SECONDS = 3600
DEDUP_MAX_ENTRIES = 4096
MAX_EVENT_ID_LENGTH = 200


def sign(secret: str, timestamp: str, body: bytes) -> str:
    message = timestamp.encode("utf-8") + b"." + body
    return "sha256=" + hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify(secret: str, signature: str, timestamp: str, body: bytes,
           max_skew: int = DEFAULT_MAX_SKEW_SECONDS) -> tuple[bool, str]:
    if not signature:
        return False, "missing signature"
    if not timestamp:
        return False, "missing timestamp"
    try:
        skew = abs(time.time() - float(timestamp))
    except ValueError:
        return False, "timestamp is not numeric"
    if skew > max_skew:
        return False, f"timestamp outside +/-{max_skew}s"
    expected = sign(secret, timestamp, body)
    if not hmac.compare_digest(expected, signature):
        return False, "signature mismatch"
    return True, "ok"


class Receiver:
    def __init__(self, secret: str, forward_url: str = "", forward_secret: str = "",
                 scheme: str = "mc-agent", max_skew: int = DEFAULT_MAX_SKEW_SECONDS,
                 log_file: str = "", dedup_ttl: int = DEDUP_TTL_SECONDS,
                 dedup_max: int = DEDUP_MAX_ENTRIES, max_body: int = MAX_BODY_BYTES):
        self.secret = secret
        self.forward_url = forward_url
        self.forward_secret = forward_secret
        self.scheme = scheme
        self.max_skew = max_skew
        self.dedup_ttl = dedup_ttl
        self.dedup_max = dedup_max
        self.max_body = max_body
        self.log_file = log_file
        self.delivered: OrderedDict[str, float] = OrderedDict()
        self.pending: set[str] = set()
        self.lock = threading.Lock()
        self.forward_calls = 0

    def emit(self, text: str) -> None:
        print(text, flush=True)
        if self.log_file:
            with open(self.log_file, "a", encoding="utf-8") as handle:
                handle.write(text + "\n")

    def _prune(self) -> None:
        cutoff = time.time() - self.dedup_ttl
        while self.delivered:
            _, seen_at = next(iter(self.delivered.items()))
            if seen_at >= cutoff and len(self.delivered) <= self.dedup_max:
                break
            self.delivered.popitem(last=False)

    def _mark_delivered(self, event_id: str) -> None:
        with self.lock:
            self.delivered[event_id] = time.time()
            self.delivered.move_to_end(event_id)
            self.pending.discard(event_id)
            self._prune()

    def _claim(self, event_id: str) -> str:
        """Return: delivered | pending | claimed."""
        with self.lock:
            self._prune()
            if event_id in self.delivered:
                return "delivered"
            if event_id in self.pending:
                return "pending"
            self.pending.add(event_id)
            return "claimed"

    def _release(self, event_id: str) -> None:
        with self.lock:
            self.pending.discard(event_id)

    def handle(self, headers, body: bytes) -> tuple[int, dict]:
        ok, reason = verify(
            self.secret,
            headers.get(SIGNATURE_HEADER, ""),
            headers.get(TIMESTAMP_HEADER, ""),
            body,
            self.max_skew,
        )
        if not ok:
            return 401, {"status": "rejected", "reason": reason}
        if len(body) > self.max_body:
            return 413, {"status": "rejected", "reason": "body too large"}
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            return 400, {"status": "rejected", "reason": f"invalid JSON: {error}"}
        if not isinstance(payload, dict):
            return 400, {"status": "rejected", "reason": "body must be a JSON object"}
        event_id = headers.get(EVENT_ID_HEADER, "")
        if not event_id or len(event_id) > MAX_EVENT_ID_LENGTH or any(ord(ch) < 32 for ch in event_id):
            return 400, {"status": "rejected", "reason": "missing or invalid X-MC-Agent-Event-Id"}
        event_type = headers.get(EVENT_TYPE_HEADER, str(payload.get("type", "")))

        claimed = self._claim(event_id)
        if claimed == "delivered":
            return 200, {"status": "duplicate", "eventId": event_id}
        if claimed == "pending":
            return 503, {"status": "busy", "eventId": event_id,
                         "reason": "another delivery of this event is in flight"}

        forwarded = ""
        if self.forward_url:
            with self.lock:
                self.forward_calls += 1
            forwarded = self.forward(payload, event_id)
            if not forwarded.startswith("HTTP 2"):
                self._release(event_id)
                return 503, {"status": "forward_failed", "eventId": event_id, "reason": forwarded}

        self._mark_delivered(event_id)
        entry = {
            "receivedAt": int(time.time() * 1000),
            "eventId": event_id,
            "type": event_type,
            "attempt": headers.get(ATTEMPT_HEADER, ""),
            "category": payload.get("category", ""),
            "forwarded": forwarded,
            "payload": payload,
        }
        self.emit(json.dumps(entry, ensure_ascii=False, sort_keys=True))
        return 202, {"status": "accepted", "eventId": event_id}

    def forward(self, payload: dict, event_id: str) -> str:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": "mc-agent-webhook-receiver"}
        timestamp = str(int(time.time()))
        if self.scheme == "hermes-v2":
            signature = hmac.new(
                self.forward_secret.encode("utf-8"),
                (timestamp + ".").encode("utf-8") + body,
                hashlib.sha256,
            ).hexdigest()
            headers["X-Webhook-Signature-V2"] = signature
            headers["X-Webhook-Timestamp"] = timestamp
            if event_id:
                headers["webhook-id"] = event_id
        else:
            headers[SIGNATURE_HEADER] = sign(self.forward_secret, timestamp, body)
            headers[TIMESTAMP_HEADER] = timestamp
            if event_id:
                headers[EVENT_ID_HEADER] = event_id
        request = urllib.request.Request(self.forward_url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return f"HTTP {response.status}"
        except urllib.error.HTTPError as error:
            return f"HTTP {error.code}"
        except OSError as error:
            return f"error: {error}"


def make_handler(receiver: Receiver):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:  # noqa: N802 - http.server API
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if length < 0:
                status, payload = 400, {"status": "rejected", "reason": "invalid Content-Length"}
            elif length > receiver.max_body:
                status, payload = 413, {"status": "rejected", "reason": "body too large"}
            else:
                body = self.rfile.read(length) if length else b""
                status, payload = receiver.handle(self.headers, body)
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            if getattr(self.server, "verbose", False):
                print("receiver: " + format % args, file=sys.stderr)

    return Handler


def serve(args: argparse.Namespace) -> int:
    receiver = Receiver(args.secret, args.forward_url, args.forward_secret, args.scheme,
                        args.max_skew, args.log_file)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(receiver))
    server.verbose = args.verbose
    print(f"listening on http://{args.host}:{server.server_address[1]} (events: {args.events})",
          file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _signed_headers(secret: str, event_id: str, body: bytes = b'{"category":"mark","type":"mark","data":{"text":"selftest"}}',
                    event_type: str = "mark") -> dict:
    timestamp = str(int(time.time()))
    return {
        SIGNATURE_HEADER: sign(secret, timestamp, body),
        TIMESTAMP_HEADER: timestamp,
        EVENT_ID_HEADER: event_id,
        EVENT_TYPE_HEADER: event_type,
    }


def selftest() -> int:
    failures = 0

    def check(name: str, ok: bool) -> None:
        nonlocal failures
        if ok:
            print(f"PASS  {name}")
        else:
            print(f"FAIL  {name}", file=sys.stderr)
            failures += 1

    body = b'{"category":"mark","type":"mark","data":{"text":"selftest"}}'
    receiver = Receiver("test-secret")
    headers = _signed_headers("test-secret", "evt-selftest-1")
    status, payload = receiver.handle(headers, body)
    check("valid signature accepted", status == 202 and payload.get("status") == "accepted")
    status, payload = receiver.handle(headers, body)
    check("duplicate delivery de-duplicated", status == 200 and payload.get("status") == "duplicate")
    bad = dict(headers)
    bad[SIGNATURE_HEADER] = "sha256=" + "0" * 64
    check("damaged signature rejected", receiver.handle(bad, body)[0] == 401)
    stale = dict(headers)
    stale[TIMESTAMP_HEADER] = str(int(time.time()) - 3600)
    stale[SIGNATURE_HEADER] = sign("test-secret", stale[TIMESTAMP_HEADER], body)
    stale[EVENT_ID_HEADER] = "evt-selftest-2"
    check("stale timestamp rejected with configured skew", receiver.handle(stale, body)[0] == 401)
    tolerant = Receiver("test-secret", max_skew=7200)
    check("max_skew is honored", tolerant.handle(stale, body)[0] == 202)
    check("invalid JSON rejected", receiver.handle(_signed_headers("test-secret", "evt-selftest-badjson", b"not-json"), b"not-json")[0] == 400)
    check("array JSON rejected", receiver.handle(_signed_headers("test-secret", "evt-selftest-array", b"[]"), b"[]")[0] == 400)
    missing = dict(headers)
    missing[EVENT_ID_HEADER] = ""
    check("missing event id rejected", receiver.handle(missing, body)[0] == 400)
    oversized = b"x" * (MAX_BODY_BYTES + 1)
    check("oversized body rejected",
          receiver.handle(_signed_headers("test-secret", "evt-selftest-big", oversized), oversized)[0] == 413)

    # Transient downstream failure: retryable 503, then success with the same
    # event id, then a duplicate without a second downstream call.
    downstream_calls = {"total": 0}

    class Downstream(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            downstream_calls["total"] += 1
            if downstream_calls["total"] == 1:
                response, status = b'{"status":"error"}', 500
            else:
                response, status = b'{"status":"ok"}', 202
            self.send_response(status)
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            return

    downstream = ThreadingHTTPServer(("127.0.0.1", 0), Downstream)
    thread = threading.Thread(target=downstream.serve_forever, daemon=True)
    thread.start()
    forwarding = Receiver(
        "test-secret",
        forward_url=f"http://127.0.0.1:{downstream.server_address[1]}/hook",
        forward_secret="downstream-secret",
    )
    forward_headers = _signed_headers("test-secret", "evt-selftest-forward")
    status, payload = forwarding.handle(forward_headers, body)
    check("transient forward failure returns retryable 503",
          status == 503 and payload.get("status") == "forward_failed")
    status, payload = forwarding.handle(forward_headers, body)
    check("retry after forward failure succeeds", status == 202 and payload.get("status") == "accepted")
    status, payload = forwarding.handle(forward_headers, body)
    check("retry does not mark a failed forward as delivered",
          downstream_calls["total"] == 2)
    check("duplicate after successful forward", status == 200 and payload.get("status") == "duplicate")
    downstream.shutdown()
    downstream.server_close()

    if failures:
        print(f"{failures} selftest check(s) failed", file=sys.stderr)
        return 1
    print("webhook receiver selftest passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    serve_parser = sub.add_parser("serve", help="run the receiver")
    serve_parser.add_argument("--secret", required=True, help="daemon webhook secret")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve_parser.add_argument("--events", default="*", help="informational filter label")
    serve_parser.add_argument("--forward-url", default="", help="forward verified events to this URL")
    serve_parser.add_argument("--forward-secret", default="", help="secret for the forward target")
    serve_parser.add_argument("--scheme", choices=["mc-agent", "hermes-v2"], default="mc-agent",
                              help="forwarding header scheme (hermes-v2 emits X-Webhook-Signature-V2)")
    serve_parser.add_argument("--max-skew", type=int, default=DEFAULT_MAX_SKEW_SECONDS)
    serve_parser.add_argument("--log-file", default="")
    serve_parser.add_argument("--verbose", action="store_true")
    serve_parser.set_defaults(func=serve)

    test_parser = sub.add_parser("selftest", help="verify signing, limits and forwarding without a daemon")
    test_parser.set_defaults(func=lambda _args: selftest())

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
