"""
IGT Coach mock-up server.

Standard library only (plus numpy and pyyaml, which the model and the content
library need). No FastAPI, no Starlette, nothing to version-conflict with
whatever else is installed -- this has to run on a coordinator's laptop a year
from now without a dependency archaeology session.

Its only two jobs:
  1. serve web/app.html and web/data.json
  2. perform the real Dexcom sandbox OAuth 2.0 flow, so the IRB can see the
     actual authorisation and HIPAA consent surface a participant would meet

Everything the app displays is computed in the browser from data.json, so the
page also works with no server at all.

Run:
    python server/main.py                       # demo only
    DEXCOM_CLIENT_ID=... DEXCOM_CLIENT_SECRET=... python server/main.py
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import synth
import swaps
from dexcom import DexcomClient, DexcomError, SANDBOX_USERS

WEB = HERE.parent / "web"
PORT = int(os.environ.get("IGT_PORT", "8765"))


def load_dotenv(path):
    """Read KEY=value lines from .env into the environment.

    Kept here rather than pulled in as a dependency. The file is gitignored:
    the Dexcom client secret must never reach the repository, and Dexcom
    invalidates a secret the moment it is exposed.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_dotenv(HERE.parent / ".env")

dex = DexcomClient(sandbox=True)

PARTICIPANT = {"id": "P-014", "initials": "A.R.", "week": 28.3, "profile": "igt"}

_cache = {}


def series(profile="igt", days=14):
    key = (profile, days)
    if key not in _cache:
        _cache[key] = synth.generate_series(days=days, profile=profile,
                                            week=PARTICIPANT["week"] - days / 7.0,
                                            seed=11)
    return _cache[key]


def run_in_and_active(profile="igt", days=14, run_in_days=7):
    recs = series(profile, days)
    cut = run_in_days * synth.SAMPLES_PER_DAY
    return recs[:cut], recs[cut:]


SKELETON_HEAD = (
    '<!doctype html><html lang="en"><head>'
    '<meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1,'
    'viewport-fit=cover">'
    '<style>:root{color-scheme:light;'
    'padding-top:env(safe-area-inset-top,0px);'
    'padding-bottom:env(safe-area-inset-bottom,0px)}'
    'body{margin:0;font:14px system-ui}'
    'img{max-width:100%}[hidden]{display:none!important}</style>'
    '</head><body>')


SETUP_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Connect Dexcom &mdash; IGT Coach</title>
<style>
 :root{color-scheme:light dark}
 body{margin:0;min-height:100vh;display:grid;place-items:center;
   background:#F7F3F1;color:#241B23;
   font:15px/1.5 "Public Sans",-apple-system,"Segoe UI",sans-serif}
 @media (prefers-color-scheme:dark){body{background:#17131A;color:#F3EBF0}}
 .card{width:min(520px,92vw);background:#fff;border:1px solid #E2D8D4;
   border-radius:16px;padding:26px 28px}
 @media (prefers-color-scheme:dark){.card{background:#211B23;border-color:#352C38}}
 h1{font:600 21px/1.25 Georgia,serif;margin:0 0 6px}
 p{color:#584B55;margin:0 0 16px}
 @media (prefers-color-scheme:dark){p{color:#C3B5BE}}
 label{display:block;font-size:12px;font-weight:700;letter-spacing:.05em;
   text-transform:uppercase;color:#8A7C85;margin:14px 0 5px}
 input{width:100%;box-sizing:border-box;font:14px ui-monospace,Menlo,monospace;
   padding:10px 12px;border:1px solid #CFC2BC;border-radius:9px;
   background:transparent;color:inherit}
 input:focus{outline:2px solid #8E2F55;outline-offset:1px}
 button{margin-top:18px;width:100%;font:600 15px/1 inherit;padding:12px;
   border:0;border-radius:10px;background:#8E2F55;color:#fff;cursor:pointer}
 .ok{margin-top:14px;padding:11px 13px;border-radius:9px;font-size:14px;
   background:rgba(31,111,107,.12);color:#1F6F6B}
 .err{margin-top:14px;padding:11px 13px;border-radius:9px;font-size:14px;
   background:rgba(176,106,31,.12);color:#B06A1F}
 .note{font-size:12.5px;color:#8A7C85;margin-top:16px}
 a{color:#8E2F55}
</style></head><body>
<div class="card">
  <h1>Connect the Dexcom sandbox</h1>
  <p>Paste the two values from your app at
     <a href="https://developer.dexcom.com/account/apps" target="_blank"
        rel="noopener">developer.dexcom.com</a>. They are written to a
     gitignored <code>.env</code> on this machine and nowhere else.</p>
  <form id="f">
    <label for="cid">Client ID</label>
    <input id="cid" name="cid" autocomplete="off" spellcheck="false" required>
    <label for="sec">Client Secret</label>
    <input id="sec" name="sec" type="password" autocomplete="off"
           spellcheck="false" required>
    <label for="uri">Redirect URI</label>
    <input id="uri" name="uri" value="http://127.0.0.1:__PORT__/auth/callback">
    <button type="submit">Save and connect</button>
  </form>
  <div id="msg"></div>
  <p class="note">Sandbox only. Dexcom invalidates a client secret the moment
     it is exposed, so this page never displays it back and never logs it.</p>
</div>
<script>
document.getElementById('f').addEventListener('submit', async function (e) {
  e.preventDefault();
  var msg = document.getElementById('msg');
  msg.className = ''; msg.textContent = 'Saving...';
  try {
    var r = await fetch('/api/dexcom/configure', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        client_id: document.getElementById('cid').value.trim(),
        client_secret: document.getElementById('sec').value.trim(),
        redirect_uri: document.getElementById('uri').value.trim()
      })
    });
    var j = await r.json();
    if (!r.ok) throw new Error(j.error || 'failed');
    msg.className = 'ok';
    msg.textContent = 'Saved (' + j.client_id_masked + ', secret '
      + j.secret_len + ' chars). Sending you to Dexcom...';
    setTimeout(function () { window.location.href = '/auth/login'; }, 900);
  } catch (err) {
    msg.className = 'err'; msg.textContent = String(err.message || err);
  }
});
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "IGTCoach/1.0"

    # ------------------------------------------------------------ helpers
    def _send(self, code, body, ctype="application/json; charset=utf-8",
              headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, url):
        self.send_response(302)
        self.send_header("Location", url)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))

    def _cors(self, origin="https://developer.dexcom.com"):
        return {"Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type"}

    def do_OPTIONS(self):
        # Preflight for the one cross-origin route: /api/dexcom/configure.
        self.send_response(204)
        for k, v in self._cors().items():
            self.send_header(k, v)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # --------------------------------------------------------------- GET
    def do_GET(self):
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)
        try:
            return self.route(p, q)
        except DexcomError as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:                       # noqa: BLE001
            traceback.print_exc()
            return self._send(500, {"error": repr(e)})

    def route(self, p, q):
        # ---- page -------------------------------------------------------
        if p in ("/", "/index.html"):
            frag = (WEB / "app.html").read_text(encoding="utf-8")
            return self._send(200, SKELETON_HEAD + frag + "</body></html>",
                              "text/html; charset=utf-8")
        if p == "/data.json":
            return self._send(200, (WEB / "data.json").read_text(encoding="utf-8"))
        if p == "/setup":
            # Same-origin setup page. The credentials are pasted here and go
            # straight to a gitignored .env; they never pass through a chat
            # transcript, a shell history or a tool log.
            return self._send(200, SETUP_PAGE.replace("__PORT__", str(PORT)),
                              "text/html; charset=utf-8")

        # ---- health -----------------------------------------------------
        if p == "/api/health":
            return self._send(200, {"ok": True,
                                    "dexcom_configured": dex.configured,
                                    "dexcom_connected": dex.connected})

        # ---- dexcom oauth ----------------------------------------------
        if p == "/api/dexcom/status":
            return self._send(200, dex.status())
        if p == "/auth/login":
            return self._redirect(dex.authorize_url())
        if p == "/auth/callback":
            if q.get("error"):
                return self._redirect("/?dexcom=denied")
            code = (q.get("code") or [None])[0]
            if not code:
                return self._redirect("/?dexcom=nocode")
            dex.exchange_code(code)
            return self._redirect("/?dexcom=connected")
        if p == "/api/dexcom/disconnect":
            dex.disconnect()
            return self._send(200, {"ok": True})

        # ---- live sandbox data -----------------------------------------
        if p == "/api/dexcom/datarange":
            return self._send(200, dex.data_range())
        if p in ("/api/dexcom/egvs", "/api/dexcom/events"):
            return self._send(200, self._sandbox_window(
                p.rsplit("/", 1)[-1], int((q.get("days") or ["7"])[0]),
                (q.get("start") or [None])[0], (q.get("end") or [None])[0]))

        # ---- demo cohort ------------------------------------------------
        if p == "/api/participant":
            run_in, active = run_in_and_active()
            return self._send(200, {
                "participant": PARTICIPANT,
                "baseline": synth.personal_baseline(run_in),
                "run_in_metrics": synth.metrics(run_in),
                "active_metrics": synth.metrics(active),
                "sandbox_users": SANDBOX_USERS,
            })
        if p == "/api/series":
            profile = (q.get("profile") or ["igt"])[0]
            days = int((q.get("days") or ["14"])[0])
            run_in, active = run_in_and_active(profile, days)
            return self._send(200, {
                "run_in": run_in, "active": active,
                "baseline": synth.personal_baseline(run_in),
                "metrics": synth.metrics(active)})
        if p == "/api/foods":
            return self._send(200, {"foods": [
                dict(f, id=fid, blocked_reason=swaps.is_blocked(fid))
                for fid, f in swaps.foods().items()]})

        return self._send(404, {"error": "no route " + p})

    def _sandbox_window(self, kind, days, start=None, end_override=None):
        """Anchor on /dataRange -- each sandbox user covers a different span,
        and guessing dates just returns an empty array.

        endDate is EXCLUSIVE, so the window is pushed a minute past the last
        record; otherwise a user whose only events sit at the very end of the
        range looks like a user with no events at all.
        """
        from datetime import datetime, timedelta
        fmt = "%Y-%m-%dT%H:%M:%S"
        if start and end_override:
            return self._fetch(kind, start, end_override)
        dr = dex.data_range()
        end = ((dr.get(kind) or {}).get("end") or {}).get("systemTime") \
            or ((dr.get("egvs") or {}).get("end") or {}).get("systemTime")
        if not end:
            return {"records": [], "note": "This sandbox user has no %s." % kind}
        e = datetime.strptime(end[:19], fmt) + timedelta(minutes=1)
        s = e - timedelta(days=min(days, 30))
        return self._fetch(kind, s.strftime(fmt), e.strftime(fmt))

    def _fetch(self, kind, start, end):
        data = (dex.egvs if kind == "egvs" else dex.events)(start, end)
        if kind == "events":
            kinds = sorted({r.get("eventType") for r in data.get("records", [])
                            if r.get("eventType")})
            data["eventTypesPresent"] = kinds
            data["carbsPresent"] = "carbs" in kinds
        data["window"] = {"startDate": start, "endDate": end}
        return data

    # -------------------------------------------------------------- POST
    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send(400, {"error": "bad json"})
        if u.path == "/api/dexcom/configure":
            # Takes the credentials straight from the Dexcom portal page into
            # a gitignored .env, so the client secret never passes through a
            # chat transcript, a shell history or a tool log. Returns only a
            # masked confirmation.
            cid = (body.get("client_id") or "").strip()
            sec = (body.get("client_secret") or "").strip()
            uri = (body.get("redirect_uri")
                   or "http://127.0.0.1:%d/auth/callback" % PORT).strip()
            if not cid or not sec:
                return self._send(400, {"error": "client_id and client_secret required"},
                                  headers=self._cors())
            env = HERE.parent / ".env"
            env.write_text(
                "# Written by /api/dexcom/configure. Gitignored. Do not commit.\n"
                "DEXCOM_CLIENT_ID=%s\nDEXCOM_CLIENT_SECRET=%s\n"
                "DEXCOM_REDIRECT_URI=%s\n" % (cid, sec, uri), encoding="utf-8")
            try:
                env.chmod(0o600)
            except Exception:
                pass
            os.environ["DEXCOM_CLIENT_ID"] = cid
            os.environ["DEXCOM_CLIENT_SECRET"] = sec
            os.environ["DEXCOM_REDIRECT_URI"] = uri
            dex.client_id, dex.client_secret, dex.redirect_uri = cid, sec, uri
            return self._send(200, {
                "ok": True,
                "client_id_masked": cid[:4] + "..." + cid[-4:] if len(cid) > 8 else "set",
                "secret_len": len(sec),
                "redirect_uri": uri,
                "configured": dex.configured,
            }, headers=self._cors())

        if u.path != "/api/predict":
            return self._send(404, {"error": "no route " + u.path})
        try:
            run_in, _ = run_in_and_active(body.get("profile", "igt"))
            bl = synth.personal_baseline(run_in)
            items = [(i[0], i[1] if len(i) > 1 else 1) for i in body["items"]]
            bm, base, sugg, day = swaps.propose(
                items, int(body.get("minute", 450)), body.get("profile", "igt"),
                float(body.get("week", 28.3)), bl["fasting_median"],
                bl["own_threshold"],
                float(body.get("carbs_so_far_g", 0)),
                float(body.get("carbs_planned_rest_of_day_g", 135)))
            return self._send(200, {
                "meal": {"carb_g": round(bm.carb_g, 1),
                         "protein_g": round(bm.protein_g, 1),
                         "fat_g": round(bm.fat_g, 1),
                         "fiber_g": round(bm.fiber_g, 1)},
                "projected": base,
                "own_threshold": bl["own_threshold"],
                "crossed": base["peak_mgdl"] > bl["own_threshold"],
                "suggestions": [s.as_dict() for s in sugg],
                "day": day,
                "summary_card": swaps.summary_card(
                    PARTICIPANT["id"], bm, base, sugg, bl["own_threshold"],
                    float(body.get("week", 28.3)), day)})
        except swaps.GuardrailViolation as e:
            return self._send(409, {"guardrail": str(e)})
        except Exception as e:                       # noqa: BLE001
            traceback.print_exc()
            return self._send(500, {"error": repr(e)})


def main():
    if not (WEB / "data.json").exists():
        print("data.json missing -- building it first.")
        import build_data
        build_data.main()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("\n  IGT Coach mock-up")
    print("  http://127.0.0.1:%d" % PORT)
    print("  Dexcom sandbox: %s"
          % ("credentials loaded" if dex.configured
             else "not configured (demo mode only)"))

    # Self-clearing reminder. The marker lives in .env; saving new credentials
    # through /setup rewrites .env without it, so rotating the secret silences
    # this by itself and nobody has to remember to delete anything.
    exposed = os.environ.get("DEXCOM_SECRET_EXPOSED")
    if exposed:
        print("")
        print("  " + "!" * 66)
        print("  CLIENT SECRET NEEDS ROTATING")
        print("  It was pasted into a chat transcript on %s." % exposed)
        print("  Dexcom treats an exposed secret as compromised.")
        print("  Fix: developer.dexcom.com/account/apps -> Regenerate")
        print("       Credentials, then http://127.0.0.1:%d/setup" % PORT)
        print("  This warning disappears once new credentials are saved.")
        print("  " + "!" * 66)

    print("\n  Ctrl-C to stop.\n")
    sys.stdout.flush()      # otherwise the banner is lost when output is piped
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped.")


if __name__ == "__main__":
    main()
