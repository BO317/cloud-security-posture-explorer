"""Loopback-only WSGI demo. No SDK, credentials, or live inventory route."""

import argparse
from datetime import datetime, timezone
from html import escape
import json
from wsgiref.simple_server import make_server

from app.checks import check
from app.sample_data import sample_resources


def dashboard(resources):
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
<title>Cloud Security Posture Explorer | Local demo</title>
<style>
body{{font:16px/1.6 system-ui,sans-serif;background:#f3f6fa;color:#14263d;margin:0}}main{{max-width:1250px;margin:auto;padding:32px 24px}}
h1{{line-height:1.2}}.notice{{background:#e1edff;padding:16px;border-left:4px solid #2463ae}}.table{{overflow-x:auto}}
table{{border-collapse:collapse;background:white;width:100%}}th,td{{text-align:left;vertical-align:top;padding:14px;border-bottom:1px solid #cbd5e1}}th{{background:#e5ebf3}}caption{{text-align:left;margin:16px 0;font-weight:600}}
.PASS{{color:#146334;font-weight:700}}.REVIEW{{color:#895000;font-weight:700}}.UNKNOWN{{color:#71439a;font-weight:700}}footer{{margin-top:24px}}code{{background:#e5ebf3;padding:2px 5px}}
</style></head><body><main>
<p>LOCAL MVP · SYNTHETIC DATA · READ ONLY</p><h1>Cloud Security Posture Explorer</h1>
<p class="notice">Educational demonstration, not production ready. No AWS connection or live scan. Scope: invented demo account / simulated region, eight selected resources.</p>
<p>Fixture evaluation: <strong>{completeness}</strong> · {counts['PASS']} PASS · {counts['REVIEW']} REVIEW · {counts['UNKNOWN']} UNKNOWN</p>
<p>Evaluated at {evaluated}. Observation timestamps below are fixed synthetic evidence times, not refresh times. Error rows show the attempted observation time.</p>
<p>PASS means only the named check passed. REVIEW requests human inspection. UNKNOWN means evidence is missing, invalid, or unavailable.</p>
<div class="table"><table><caption>Selected configuration checks</caption><thead><tr><th scope="col">Resource</th><th scope="col">Check</th><th scope="col">Result</th><th scope="col">Reason</th><th scope="col">Observed at (UTC)</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<footer><p>S3 effective exposure remains UNKNOWN: account/organization controls, bucket policies, and ACL context are not evaluated. Security group checks cover worldwide IPv4/IPv6 sources and ports 22/3389 only; they do not establish network reachability or compromise.</p>
<p><a href="/healthz">Application liveness</a> is independent of scan completeness and posture results. A healthy process does not mean a successful scan.</p></footer>
</main></body></html>'''


def application(environ, start_response):
    status, content_type = "200 OK", "text/html; charset=utf-8"
    extra_headers = []
    if environ.get("REQUEST_METHOD") not in ("GET", "HEAD"):
        status, body = "405 Method Not Allowed", "Read-only application."
        extra_headers = [("Allow", "GET, HEAD")]
    elif environ.get("PATH_INFO") == "/healthz":
        content_type = "application/json"
        body = json.dumps({"liveness": "ok", "mode": "synthetic", "posture_scan": "not_evaluated"})
    elif environ.get("PATH_INFO") == "/":
        try:
            body = dashboard(sample_resources())
        except Exception:
            status, body = "503 Service Unavailable", "Posture results UNKNOWN: sample evaluation unavailable."
    else:
        status, body = "404 Not Found", "Not found."
    payload = body.encode("utf-8")
    start_response(status, [("Content-Type", content_type), ("Content-Length", str(len(payload))), ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"), ("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'")] + extra_headers)
    return [b"" if environ.get("REQUEST_METHOD") == "HEAD" else payload]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    with make_server("127.0.0.1", args.port, application) as server:
        print(f"Synthetic demo: http://127.0.0.1:{server.server_port} (Ctrl+C to stop)", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
