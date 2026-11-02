import httpx
import pytest
from fastapi.testclient import TestClient

from api import contact as mod

ORIGIN = "https://www.ashitamo80.com"

VALID = {
    "company": "株式会社テスト",
    "name": "山田 太郎",
    "email": "taro@example.com",
    "size": "〜50名",
    "topic": "協業・新規事業のご相談",
    "body": "相談したいことがあります。",
    "website": "",
    "turnstile_token": "token",
}


@pytest.fixture
def client():
    return TestClient(mod.app)


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(mod, "verify_turnstile", lambda token, ip: token == "token")
    monkeypatch.setattr(mod, "send_mail", calls.append)
    return calls


def test_success(client, sent):
    res = client.post("/api/contact", json=VALID, headers={"Origin": ORIGIN})
    assert res.status_code == 200
    assert res.json() == {"ok": True}
    assert res.headers["access-control-allow-origin"] == ORIGIN
    assert len(sent) == 1
    assert sent[0].company == "株式会社テスト"


def test_preflight_allowed_origin(client):
    res = client.options(
        "/api/contact",
        headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"},
    )
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == ORIGIN


def test_preflight_other_origin(client):
    res = client.options(
        "/api/contact",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in res.headers


@pytest.mark.parametrize(
    "patch, field",
    [
        ({"company": ""}, "company"),
        ({"email": "not-an-email"}, "email"),
        ({"body": "   "}, "body"),
        ({"name": "山田\r\nBcc: x@example.com"}, "name"),
        ({"body": "a" * 4001}, "body"),
        ({"company": "テスト\u2028Bcc: x@example.com"}, "company"),
        ({"topic": "相談\t内容"}, "topic"),
        ({"name": "\u202eevil"}, "name"),
        ({"body": "本文\x00"}, "body"),
    ],
)
def test_invalid(client, sent, patch, field):
    res = client.post("/api/contact", json={**VALID, **patch})
    assert res.status_code == 400
    assert res.json()["fields"] == [field]
    assert sent == []


def test_honeypot_is_silently_dropped(client, sent):
    res = client.post("/api/contact", json={**VALID, "website": "http://spam.example"})
    assert res.status_code == 200
    assert sent == []


def test_captcha_failed(client, sent):
    res = client.post("/api/contact", json={**VALID, "turnstile_token": "bad"})
    assert res.status_code == 403
    assert res.json()["error"] == "captcha"
    assert sent == []


def test_send_failed(client, monkeypatch):
    monkeypatch.setattr(mod, "verify_turnstile", lambda token, ip: True)

    def boom(_):
        raise RuntimeError("resend down")

    monkeypatch.setattr(mod, "send_mail", boom)
    res = client.post("/api/contact", json=VALID)
    assert res.status_code == 502
    assert res.json()["error"] == "send_failed"


def test_get_not_allowed(client):
    assert client.get("/api/contact").status_code == 405


def test_multiline_body_allowed(client, sent):
    res = client.post("/api/contact", json={**VALID, "body": "1行目\r\n\t2行目"})
    assert res.status_code == 200
    assert sent[0].body == "1行目\r\n\t2行目"


def test_long_honeypot_is_silently_dropped(client, sent):
    res = client.post("/api/contact", json={**VALID, "website": "x" * 1000})
    assert res.status_code == 200
    assert sent == []


def test_too_large(client, sent):
    res = client.post("/api/contact", json={**VALID, "body": "a" * (mod.MAX_BODY_BYTES + 1)})
    assert res.status_code == 413
    assert sent == []


def test_security_headers(client, sent):
    res = client.post("/api/contact", json=VALID)
    assert res.headers["cache-control"] == "no-store"
    assert res.headers["x-content-type-options"] == "nosniff"


def test_client_ip_prefers_real_ip(client, monkeypatch):
    seen = []
    monkeypatch.setattr(mod, "verify_turnstile", lambda token, ip: seen.append(ip))
    client.post(
        "/api/contact",
        json=VALID,
        headers={"x-real-ip": "203.0.113.1", "x-forwarded-for": "198.51.100.1, 10.0.0.1"},
    )
    assert seen == ["203.0.113.1"]


def test_missing_turnstile_secret(client, monkeypatch):
    monkeypatch.delenv("TURNSTILE_SECRET_KEY", raising=False)
    res = client.post("/api/contact", json=VALID)
    assert res.status_code == 500
    assert res.json() == {"ok": False, "error": "server_error"}


@pytest.fixture
def resend_send(monkeypatch):
    calls = []

    def fake_send(params):
        calls.append(params)
        return {"id": "email-id"}

    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("CONTACT_TO", "a@example.com, b@example.com")
    monkeypatch.setattr(mod.resend.Emails, "send", fake_send)
    monkeypatch.setattr(mod, "verify_turnstile", lambda token, ip: True)
    return calls


def test_send_mail_via_resend(client, resend_send):
    res = client.post("/api/contact", json=VALID)
    assert res.status_code == 200
    assert mod.resend.api_key == "re_test"
    (params,) = resend_send
    assert params["from"] == mod.MAIL_FROM
    assert params["to"] == ["a@example.com", "b@example.com"]
    assert params["reply_to"] == "taro@example.com"
    assert params["subject"] == "【お問い合わせ】株式会社テスト 山田 太郎 様"
    assert "相談したいことがあります。" in params["text"]
    assert "html" not in params


def test_send_mail_missing_contact_to(client, resend_send, monkeypatch):
    monkeypatch.delenv("CONTACT_TO")
    res = client.post("/api/contact", json=VALID)
    assert res.status_code == 500
    assert resend_send == []


def test_resend_error(client, resend_send, monkeypatch):
    def fail(_):
        raise mod.resend.exceptions.ResendError(
            code=422, error_type="validation_error", message="bad", suggested_action=""
        )

    monkeypatch.setattr(mod.resend.Emails, "send", fail)
    res = client.post("/api/contact", json=VALID)
    assert res.status_code == 502
    assert res.json()["error"] == "send_failed"


class _Res:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)

    def json(self):
        return self.payload


@pytest.mark.parametrize(
    "hostnames, payload, expected",
    [
        ("www.ashitamo80.com", {"success": True, "hostname": "www.ashitamo80.com"}, True),
        ("www.ashitamo80.com", {"success": True, "hostname": "evil.example"}, False),
        ("", {"success": True, "hostname": "example.com"}, True),
        ("", {"success": False}, False),
        ("", {"success": "true"}, False),
    ],
)
def test_verify_turnstile(monkeypatch, hostnames, payload, expected):
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret")
    monkeypatch.setenv("TURNSTILE_HOSTNAMES", hostnames)
    monkeypatch.setattr(mod.httpx, "post", lambda *a, **kw: _Res(payload))
    assert mod.verify_turnstile("token", None) is expected


def test_verify_turnstile_http_error(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret")
    monkeypatch.setattr(mod.httpx, "post", lambda *a, **kw: _Res({"success": True}, 500))
    assert mod.verify_turnstile("token", None) is False
