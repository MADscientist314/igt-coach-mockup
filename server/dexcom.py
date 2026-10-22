"""
Dexcom API v3 client, pointed at the sandbox by default.

Endpoints verified against developer.dexcom.com on 2026-10-22:
    login  GET  {base}/v3/oauth2/login?client_id=&redirect_uri=&response_type=code&scope=offline_access&state=
    token  POST {base}/v3/oauth2/token          (application/x-www-form-urlencoded)
    data   GET  {base}/v3/users/self/{egvs|events|calibrations|devices|dataRange}

Things the docs make explicit, which the study needs to know:

  * Tokens must be stored SERVER-SIDE. Dexcom requires it; mobile apps must
    not hold them.
  * Scopes are all-or-nothing. offline_access is the only acceptable scope
    value, and the HIPAA statement grants EGVs, calibrations, events and
    devices together. A participant cannot consent to a subset. That belongs
    in the consent form verbatim.
  * startDate/endDate are required on everything except /dataRange, and the
    window may not exceed 30 days.
  * Mobile-app data arrives with a ONE HOUR delay in the US (three hours
    elsewhere). This API cannot drive a real-time alert.
  * /events carries carbs only from the G6 mobile app and the receiver
    interfaces. The G7 mobile app records bloodGlucose and insulin only.
  * v2 endpoints are withdrawn in October 2026. This is v3 only.
"""
from __future__ import annotations

import os
import time
from urllib.parse import urlencode

import json as _json
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

SANDBOX_BASE = "https://sandbox-api.dexcom.com"
PRODUCTION_BASE = "https://api.dexcom.com"

MAX_WINDOW_DAYS = 30
TIME_FMT = "%Y-%m-%dT%H:%M:%S"

# The four simulated accounts, from the Sandbox Data page. G6 is the one to
# use for anything that shows a meal, because the G7 app does not log carbs.
SANDBOX_USERS = [
    {"id": "SandboxUser1", "label": "Sandbox User - G6", "app": "G6 Mobile App",
     "logs_carbs": True,
     "note": "Use this one for meal screens. The G6 app records carbs, "
             "exercise, health and insulin."},
    {"id": "SandboxUser2", "label": "Sandbox User - G7", "app": "G7 Mobile App",
     "logs_carbs": False,
     "note": "G7 records bloodGlucose and insulin only. No carbs."},
    {"id": "SandboxUser6", "label": "Sandbox User - D1", "app": "D1 Mobile App",
     "logs_carbs": False,
     "note": "Dexcom ONE does not support /events at all; returns []."},
    {"id": "SandboxUser7", "label": "Sandbox User - G715DAY", "app": "G7 15-day",
     "logs_carbs": False,
     "note": "G7 15-day. Same event limitation as G7."},
]


class DexcomError(RuntimeError):
    pass


def _http(method, url, params=None, form=None, headers=None, timeout=60):
    """Minimal stdlib HTTP. Returns (status_code, body_text)."""
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    data = urllib.parse.urlencode(form).encode() if form else None
    h = dict(headers or {})
    if form:
        h["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.getcode(), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except urllib.error.URLError as e:
        raise DexcomError("Could not reach %s: %s" % (url, e.reason))


class DexcomClient:
    def __init__(self, client_id=None, client_secret=None, redirect_uri=None,
                 sandbox=True):
        self.client_id = client_id or os.environ.get("DEXCOM_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("DEXCOM_CLIENT_SECRET", "")
        self.redirect_uri = redirect_uri or os.environ.get(
            "DEXCOM_REDIRECT_URI",
            "http://127.0.0.1:%s/auth/callback" % os.environ.get("IGT_PORT", "8765"))
        self.base = SANDBOX_BASE if sandbox else PRODUCTION_BASE
        self.sandbox = sandbox
        # Dexcom requires tokens to be held server-side. For this mock-up that
        # is a gitignored file beside the code, so a restart does not mean
        # redoing the consent flow. A real deployment would encrypt this per
        # participant; the protocol's data-security section should say so.
        self.token_path = Path(__file__).resolve().parent.parent / ".token.json"
        self._token = None
        self._load_token()

    def _load_token(self):
        try:
            if self.token_path.exists():
                tok = _json.loads(self.token_path.read_text(encoding="utf-8"))
                if tok.get("access_token"):
                    self._token = tok
        except Exception:
            self._token = None

    def _save_token(self):
        try:
            self.token_path.write_text(_json.dumps(self._token), encoding="utf-8")
            self.token_path.chmod(0o600)
        except Exception:
            pass

    # ------------------------------------------------------------ config
    @property
    def configured(self):
        return bool(self.client_id and self.client_secret)

    @property
    def connected(self):
        return bool(self._token and self._token.get("access_token"))

    def status(self):
        return {
            "configured": self.configured,
            "connected": self.connected,
            "sandbox": self.sandbox,
            "base": self.base,
            "redirect_uri": self.redirect_uri,
            "expires_in": max(0, int(self._token["expires_at"] - time.time()))
            if self._token else 0,
            "sandbox_users": SANDBOX_USERS if self.sandbox else [],
        }

    # -------------------------------------------------------------- oauth
    def authorize_url(self, state="igt-coach"):
        if not self.configured:
            raise DexcomError(
                "No client credentials. Create an app at "
                "developer.dexcom.com/account/apps, then set DEXCOM_CLIENT_ID, "
                "DEXCOM_CLIENT_SECRET and DEXCOM_REDIRECT_URI.")
        q = urlencode({
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": "offline_access",       # the only acceptable value
            "state": state,
        })
        return "%s/v3/oauth2/login?%s" % (self.base, q)

    def exchange_code(self, code):
        return self._token_request({
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri,
        })

    def refresh(self):
        if not self._token or not self._token.get("refresh_token"):
            raise DexcomError("No refresh token held.")
        return self._token_request({
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self._token["refresh_token"],
            "grant_type": "refresh_token",
            "redirect_uri": self.redirect_uri,
        })

    def _token_request(self, form):
        code, text = _http("POST", "%s/v3/oauth2/token" % self.base, form=form)
        if code != 200:
            raise DexcomError("Token exchange failed (%d): %s" % (code, text[:300]))
        tok = _json.loads(text)
        tok["expires_at"] = time.time() + int(tok.get("expires_in", 7200)) - 60
        self._token = tok
        self._save_token()
        return {"ok": True, "expires_in": tok.get("expires_in")}

    def disconnect(self):
        self._token = None
        try:
            self.token_path.unlink()
        except Exception:
            pass

    # --------------------------------------------------------------- data
    def _get(self, path, params=None):
        if not self.connected:
            raise DexcomError("Not connected. Complete the OAuth flow first.")
        if self._token["expires_at"] <= time.time():
            self.refresh()
        url = "%s/v3/users/self/%s" % (self.base, path)
        hdr = {"Authorization": "Bearer %s" % self._token["access_token"]}
        code, text = _http("GET", url, params=params, headers=hdr)
        if code == 401:
            self.refresh()
            hdr = {"Authorization": "Bearer %s" % self._token["access_token"]}
            code, text = _http("GET", url, params=params, headers=hdr)
        if code != 200:
            raise DexcomError("GET /%s failed (%d): %s" % (path, code, text[:300]))
        return _json.loads(text)

    def data_range(self):
        """The only endpoint that takes no date window. Call it first --
        each sandbox user covers a different span."""
        return self._get("dataRange")

    def egvs(self, start, end):
        self._check_window(start, end)
        return self._get("egvs", {"startDate": start, "endDate": end})

    def events(self, start, end):
        self._check_window(start, end)
        return self._get("events", {"startDate": start, "endDate": end})

    def devices(self, start, end):
        self._check_window(start, end)
        return self._get("devices", {"startDate": start, "endDate": end})

    def calibrations(self, start, end):
        self._check_window(start, end)
        return self._get("calibrations", {"startDate": start, "endDate": end})

    @staticmethod
    def _check_window(start, end):
        from datetime import datetime
        a = datetime.strptime(start[:19], TIME_FMT)
        b = datetime.strptime(end[:19], TIME_FMT)
        if (b - a).days > MAX_WINDOW_DAYS:
            raise DexcomError(
                "Window is %d days. The API rejects anything over %d."
                % ((b - a).days, MAX_WINDOW_DAYS))
