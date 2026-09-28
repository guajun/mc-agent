#!/usr/bin/env python3
"""Local, harness-neutral receiver for mc-agent daemon webhook events.

Verifies the daemon's HMAC-SHA256 signature and timestamp, de-duplicates the
stable event id, prints one JSON line per accepted event, and can forward the
verified event to another receiver. It is a development/verification tool: the
product itself has no Python requirement.

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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SIGNATURE_HEADER = "X-MC-Agent-Signature"
TIMESTAMP_HEADER = "X-MC-Agent-Timestamp"
EVENT_ID_HEADER = "X-MC-Agent-Event-Id"
EVENT_TYPE_HEADER = "X-MC-Agent-Event-Type"
ATTEMPT_HEADER = "X-MC-Agent-Attempt"

DEFAULT_MAX_SKEW_SECONDS = 300
DEFAULT_PORT = 8645


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
                 log_file: str = ""):
        self.secret = secret
        self.forward_url = forward_url
        self.forward_secret = forward_secret
        self.scheme = scheme
        self.max_skew = max_skew
        self.seen: set[str] = set()
        self.lock = threading.Lock()
        self.log_file = log_file

    def emit(self, text: str) -> None:
        print(text, flush=True)
        if self.log_file:
            with open(self.log_file, "a", encoding="utf-8") as handle:
                handle.write(text + "\n")

    def handle(self, headers, body: bytes) -> tuple[int, dict]:
        ok, reason = verify(
            self.secret,
            headers.get(SIGNATURE_HEADER, ""),
            headers.get(TIMESTAMP_HEADER, ""),
            body,
        )
        if not ok:
            return 401, {"status": "rejected", "reason": reason}
        event_id = headers.get(EVENT_ID_HEADER, "")
        with self.lock:
            if event_id and event_id in self.seen:
                return 200, {"status": "duplicate", "eventId": event_id}
            if event_id:
                self.seen.add(event_id)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            return 400, {"status": "rejected", "reason": f"invalid JSON: {error}"}
        entry = {
            "receivedAt": int(time.time() * 1000),
            "eventId": event_id,
            "type": headers.get(EVENT_TYPE_HEADER, payload.get("type", "")),
            "attempt": headers.get(ATTEMPT_HEADER, ""),
            "category": payload.get("category", ""),
            "payload": payload,
        }
        self.emit(json.dumps(entry, ensure_ascii=False, sort_keys=True))
        if self.forward_url:
            forwarded = self.forward(payload, event_id)
            entry["forwarded"] = forwarded
            self.emit(json.dumps({"forwarded": forwarded, "eventId": event_id}, sort_keys=True))
        return 202, {"status": "accepted", "eventId": event_id}

    def forward(self, payload: dict, event_id: str) -> str:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": "mc-agent-webhook-receiver"}
        if self.scheme == "hermes-v2":
            timestamp = str(int(time.time()))
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
            timestamp = str(int(time.time()))
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
            length = int(self.headers.get("Content-Length", "0"))
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


def selftest() -> int:
    receiver = Receiver("test-secret")
    body = b'{"type":"mark","category":"mark","data":{"text":"selftest"}}'
    timestamp = str(int(time.time()))
    headers = {
        SIGNATURE_HEADER: sign("test-secret", timestamp, body),
        TIMESTAMP_HEADER: timestamp,
        EVENT_ID_HEADER: "evt-selftest-1",
        EVENT_TYPE_HEADER: "mark",
    }
    status, payload = receiver.handle(headers, body)
    if status != 202 or payload.get("status") != "accepted":
        print(f"FAIL  valid signature accepted: {status} {payload}", file=sys.stderr)
        return 1
    status, payload = receiver.handle(headers, body)
    if status != 200 or payload.get("status") != "duplicate":
        print(f"FAIL  duplicate delivery de-duplicated: {status} {payload}", file=sys.stderr)
        return 1
    bad = dict(headers)
    bad[SIGNATURE_HEADER] = "sha256=" + "0" * 64
    status, _ = receiver.handle(bad, body)
    if status != 401:
        print(f"FAIL  damaged signature rejected: {status}", file=sys.stderr)
        return 1
    stale = dict(headers)
    stale[TIMESTAMP_HEADER] = str(int(time.time()) - 3600)
    stale[SIGNATURE_HEADER] = sign("test-secret", stale[TIMESTAMP_HEADER], body)
    stale[EVENT_ID_HEADER] = "evt-selftest-2"
    status, _ = receiver.handle(stale, body)
    if status != 401:
        print(f"FAIL  stale timestamp rejected: {status}", file=sys.stderr)
        return 1
    print("webhook receiver selftest passed (accept, deduplicate, reject bad signature, reject stale)")
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

    test_parser = sub.add_parser("selftest", help="verify signing/verification without a daemon")
    test_parser.set_defaults(func=lambda _args: selftest())

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
