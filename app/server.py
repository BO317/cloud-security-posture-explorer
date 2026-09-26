"""Loopback-only read-only AWS dashboard; expose only through protected access."""

import argparse
from datetime import datetime, timezone
from html import escape
import json
import logging
from socketserver import ThreadingMixIn
from threading import Lock
from wsgiref.simple_server import make_server, WSGIServer

from app.checks import check
from app.aws_provider import collect_from_environment
from app.config import ConfigurationError

LOGGER = logging.getLogger(__name__)


class ThreadedWSGIServer(ThreadingMixIn, WSGIServer):
    """Keep health responsive while one posture request waits on AWS."""

    daemon_threads = True


def dashboard(resources, region="unavailable", scan_id="unavailable", started_at="unavailable", completed_at="unavailable"):
    rows = []
    counts = dict.fromkeys(("PASS", "REVIEW", "UNKNOWN"), 0)
    for resource in resources:
        result = check(resource["kind"], resource["observation"])
        counts[result.status] += 1
        cells = (resource["name"], "S3 bucket-level BPA" if resource["kind"] == "s3" else "Security group inbound", result.reason, result.observed_at or "Unavailable — observation time unknown")
        rows.append(f'<tr><td>{escape(cells[0])}</td><td>{escape(cells[1])}</td><td class="{result.status}">{result.status}</td><td>{escape(cells[2])}</td><td>{escape(cells[3])}</td></tr>')
    completeness = "INCOMPLETE" if counts["UNKNOWN"] or not resources else "COMPLETE"
    evaluated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cloud Security Posture Explorer | AWS read-only</title>
<style>
body{{font:16px/1.6 system-ui,sans-serif;background:#f3f6fa;color:#14263d;margin:0}}main{{max-width:1250px;margin:auto;padding:32px 24px}}
h1{{line-height:1.2}}.notice{{background:#e1edff;padding:16px;border-left:4px solid #2463ae}}.table{{overflow-x:auto}}
table{{border-collapse:collapse;background:white;width:100%}}th,td{{text-align:left;vertical-align:top;padding:14px;border-bottom:1px solid #cbd5e1}}th{{background:#e5ebf3}}caption{{text-align:left;margin:16px 0;font-weight:600}}
.PASS{{color:#146334;font-weight:700}}.REVIEW{{color:#895000;font-weight:700}}.UNKNOWN{{color:#71439a;font-weight:700}}footer{{margin-top:24px}}code{{background:#e5ebf3;padding:2px 5px}}
</style></head><body><main>
<p>AWS POSTURE · READ ONLY</p><h1>Cloud Security Posture Explorer</h1>
<p class="notice">Educational portfolio application, not production ready. Scope: configured allowlists only, {len(resources)} resources. AWS region: {escape(region)}. Account scope is the configured workload identity and IAM policy.</p>
<p>Evidence evaluation: <strong>{completeness}</strong> · {counts['PASS']} PASS · {counts['REVIEW']} REVIEW · {counts['UNKNOWN']} UNKNOWN</p>
<p>Scan ID: {escape(scan_id)}. Started: {escape(started_at)}. Completed: {escape(completed_at)}.</p>
<p>Evaluated at {evaluated}. Observation times record collection completion or failed attempts, not resource modification times. This page is a snapshot; reload to collect again. No automatic refresh.</p>
<p>{'UNKNOWN: no resources were observed.' if not resources else ''}</p>
<p>PASS means only the named check passed. REVIEW requests human inspection. UNKNOWN means evidence is missing, invalid, or unavailable.</p>
<div class="table"><table><caption>Selected configuration checks</caption><thead><tr><th scope="col">Resource</th><th scope="col">Check</th><th scope="col">Result</th><th scope="col">Reason</th><th scope="col">Observed at (UTC)</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<footer><p>S3 effective exposure remains UNKNOWN: account/organization controls, bucket policies, and ACL context are not evaluated. Security group checks cover worldwide IPv4/IPv6 sources and TCP ports 22/3389 only; they do not establish network reachability or compromise. Unsupported source references produce UNKNOWN.</p>
<p><a href="/healthz">Application liveness</a> is independent of scan completeness and posture results. A healthy process does not mean a successful scan.</p></footer>
</main></body></html>'''


def respond(environ, start_response, collector, scan_lock):
    status, content_type = "200 OK", "text/html; charset=utf-8"
    extra_headers = []
    if environ.get("REQUEST_METHOD") not in ("GET", "HEAD"):
        status, body = "405 Method Not Allowed", "Read-only application."
        extra_headers = [("Allow", "GET, HEAD")]
    elif environ.get("PATH_INFO") == "/healthz":
        content_type = "application/json"
        body = json.dumps({"liveness": "ok"})
    elif environ.get("PATH_INFO") == "/":
        if not scan_lock.acquire(blocking=False):
            status, body = "503 Service Unavailable", "Posture results UNKNOWN: collection already in progress. Try again after it completes."
            extra_headers = [("Retry-After", "5")]
        else:
            try:
                scan = collector()
                body = dashboard(scan.resources, scan.region, scan.scan_id, scan.started_at, scan.completed_at)
            except ConfigurationError as exc:
                status, body = "503 Service Unavailable", "Posture results UNKNOWN: " + escape(str(exc))
                LOGGER.warning("posture_configuration_invalid")
            except Exception:
                status, body = "503 Service Unavailable", "Posture results UNKNOWN: AWS evaluation unavailable."
                LOGGER.error("posture_evaluation_failed")
            finally:
                scan_lock.release()
    else:
        status, body = "404 Not Found", "Not found."
    payload = body.encode("utf-8")
    start_response(status, [("Content-Type", content_type), ("Content-Length", str(len(payload))), ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"), ("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'")] + extra_headers)
    return [b"" if environ.get("REQUEST_METHOD") == "HEAD" else payload]


def create_application(collector=collect_from_environment):
    scan_lock = Lock()

    def app(environ, start_response):
        return respond(environ, start_response, collector, scan_lock)

    return app


application = create_application()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with make_server("127.0.0.1", args.port, application, server_class=ThreadedWSGIServer) as server:
        print(f"AWS read-only dashboard: http://127.0.0.1:{server.server_port} (Ctrl+C to stop)", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
