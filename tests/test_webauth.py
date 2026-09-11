"""The one-time setup page: single use, expiring, verifying before saving,
and never echoing the token. Runs offline — Sleeper is stubbed."""

import http.client
import json
import os
import stat
import time

import pytest

from sleeper_mcp import client, config, webauth

GOOD = "eyJab.cdefg.hijkl"                  # shape-valid, not a real JWT


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    for k in ("SLEEPER_TOKEN", "SLEEPER_ENABLE_WRITES"):
        monkeypatch.delenv(k, raising=False)
    path = tmp_path / "config.json"
    monkeypatch.setenv("SLEEPER_MCP_CONFIG", str(path))
    monkeypatch.setattr(client, "TOKEN", "")
    monkeypatch.setattr(client, "WRITES_ENABLED", False)
    return path


def _verify_ok(tok):
    return (tok == GOOD, "tester (user 1)" if tok == GOOD else "Sleeper rejected it (401)")


def _req(s, method, path, body=None, ctype="text/plain"):
    c = http.client.HTTPConnection("127.0.0.1", s.port, timeout=5)
    headers = {"Content-Type": ctype} if body is not None else {}
    c.request(method, path, body=body, headers=headers)
    r = c.getresponse()
    data = r.read().decode()
    return r.status, dict(r.getheaders()), data


def test_page_serves_once_and_hides_token(cfg):
    s = webauth.start(verify=_verify_ok, ttl=5)
    status, _, page = _req(s, "GET", f"/setup/{s.nonce}")
    assert status == 200 and "localStorage" in page and "127.0.0.1" in page
    assert _req(s, "GET", "/setup/wrong-nonce")[0] == 404

    status, _, body = _req(s, "POST", f"/token/{s.nonce}", body=GOOD)
    assert status == 200 and json.loads(body)["ok"]
    assert GOOD not in body and "hijkl" not in body            # never echoed
    assert s.done.wait(2) and "tester" in s.result and GOOD not in s.result

    saved = json.loads(cfg.read_text())
    assert saved["token"] == GOOD and "enable_writes" not in saved
    assert stat.S_IMODE(os.stat(cfg).st_mode) == 0o600
    assert client.TOKEN == GOOD and client.WRITES_ENABLED is False


def test_quoted_and_form_and_json_bodies_normalise(cfg):
    for body, ctype in [(json.dumps(GOOD), "text/plain"),
                        ("token=%22" + GOOD + "%22", "application/x-www-form-urlencoded"),
                        (json.dumps({"token": GOOD}), "application/json")]:
        s = webauth.start(verify=_verify_ok, ttl=5)
        status, _, out = _req(s, "POST", f"/token/{s.nonce}", body=body, ctype=ctype)
        assert status == 200, out
        assert json.loads(cfg.read_text())["token"] == GOOD


def test_enable_writes_flag(cfg):
    s = webauth.start(enable_writes=True, verify=_verify_ok, ttl=5)
    _req(s, "POST", f"/token/{s.nonce}", body=GOOD)
    assert json.loads(cfg.read_text())["enable_writes"] == "1"
    assert client.WRITES_ENABLED is True


def test_bad_token_is_not_saved_and_link_stays_open(cfg):
    s = webauth.start(verify=_verify_ok, ttl=5)
    status, _, out = _req(s, "POST", f"/token/{s.nonce}", body="${SLEEPER_TOKEN}")
    assert status == 400 and "did not expand" in out
    status, _, out = _req(s, "POST", f"/token/{s.nonce}", body="eyJxx.yy.zz")
    assert status == 400 and "401" in out                   # shape ok, Sleeper said no
    assert not cfg.exists() and not s.done.is_set()
    assert _req(s, "GET", f"/setup/{s.nonce}")[0] == 200     # can retry


def test_single_use(cfg):
    s = webauth.start(verify=_verify_ok, ttl=5)
    assert _req(s, "POST", f"/token/{s.nonce}", body=GOOD)[0] == 200
    s.done.wait(2)
    time.sleep(0.2)                                          # server shutting down
    with pytest.raises((ConnectionError, OSError)):
        _req(s, "POST", f"/token/{s.nonce}", body=GOOD)


def test_expiry(cfg):
    s = webauth.start(verify=_verify_ok, ttl=0.3)
    assert _req(s, "GET", f"/setup/{s.nonce}")[0] == 200
    assert s.done.wait(3) and "expired" in s.result
    assert not cfg.exists()


def test_cors_preflight_for_sleeper_origin(cfg):
    s = webauth.start(verify=_verify_ok, ttl=5)
    status, headers, _ = _req(s, "OPTIONS", f"/token/{s.nonce}")
    assert status == 204
    assert headers["Access-Control-Allow-Origin"] == "https://sleeper.com"
    assert headers["Access-Control-Allow-Private-Network"] == "true"
    assert _req(s, "OPTIONS", "/token/nope")[0] == 404


def test_normalise():
    assert webauth.normalise(' "abc.def.ghi" ') == "abc.def.ghi"
    assert webauth.normalise("'abc.def.ghi'") == "abc.def.ghi"
    assert webauth.normalise("abc.def.ghi\n") == "abc.def.ghi"
    assert webauth.normalise("") == ""
