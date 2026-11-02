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


class _Res:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


@pytest.mark.parametrize(
    "hostnames, payload, expected",
    [
        ("www.ashitamo80.com", {"success": True, "hostname": "www.ashitamo80.com"}, True),
        ("www.ashitamo80.com", {"success": True, "hostname": "evil.example"}, False),
        ("", {"success": True, "hostname": "example.com"}, True),
        ("", {"success": False}, False),
    ],
)
def test_verify_turnstile(monkeypatch, hostnames, payload, expected):
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret")
    monkeypatch.setenv("TURNSTILE_HOSTNAMES", hostnames)
    monkeypatch.setattr(mod.httpx, "post", lambda *a, **kw: _Res(payload))
    assert mod.verify_turnstile("token", None) is expected
