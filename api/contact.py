"""コーポレートサイトのお問い合わせフォーム受付 API（POST /api/contact）。

Vercel は api/ 配下の各ファイルを関数として公開する。このファイルは
FastAPI の `app` を公開し、Turnstile 検証のあと Resend で社内宛てに通知メールを送る。
"""

import logging
import os
from typing import Annotated

import httpx
import resend
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field, StringConstraints

logger = logging.getLogger(__name__)

TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
MAIL_FROM = os.environ.get("MAIL_FROM", "アシタモハレ お問い合わせ <noreply@ashitamo80.com>")


def _env_list(name: str) -> list[str]:
    return [v.strip() for v in os.environ.get(name, "").split(",") if v.strip()]


# 件名やヘッダーに入る項目は改行を禁止する
OneLine = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[^\r\n]*$")]


class Contact(BaseModel):
    company: Annotated[OneLine, StringConstraints(min_length=1, max_length=100)]
    name: Annotated[OneLine, StringConstraints(min_length=1, max_length=50)]
    email: EmailStr
    size: Annotated[OneLine, StringConstraints(max_length=30)] = ""
    topic: Annotated[OneLine, StringConstraints(max_length=60)] = ""
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    website: str = Field(default="", max_length=200)  # honeypot（人間には見えない入力欄）
    turnstile_token: str = Field(min_length=1, max_length=2048)


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_env_list("ALLOWED_ORIGINS"),
    allow_methods=["POST"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(RequestValidationError)
def _invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
    # 入力値をそのまま返さないよう、項目名だけ返す
    fields = sorted({str(e["loc"][-1]) for e in exc.errors() if e.get("loc")})
    return JSONResponse({"ok": False, "error": "invalid", "fields": fields}, status_code=400)


def verify_turnstile(token: str, remote_ip: str | None) -> bool:
    data = {"secret": os.environ["TURNSTILE_SECRET_KEY"], "response": token}
    if remote_ip:
        data["remoteip"] = remote_ip
    try:
        res = httpx.post(TURNSTILE_VERIFY_URL, data=data, timeout=5).json()
    except (httpx.HTTPError, ValueError):
        logger.exception("turnstile verify failed")
        return False
    if not res.get("success"):
        return False
    # 空なら hostname は確認しない（Preview 環境でテスト用キーを使う場合）
    hostnames = _env_list("TURNSTILE_HOSTNAMES")
    return not hostnames or res.get("hostname") in hostnames


def send_mail(c: Contact) -> None:
    resend.api_key = os.environ["RESEND_API_KEY"]
    text = "\n".join(
        [
            "コーポレートサイトからお問い合わせがありました。",
            "",
            f"会社名・団体名: {c.company}",
            f"お名前: {c.name}",
            f"メールアドレス: {c.email}",
            f"従業員数: {c.size or '-'}",
            f"ご相談内容: {c.topic or '-'}",
            "",
            "ご相談の概要:",
            c.body,
        ]
    )
    resend.Emails.send(
        {
            "from": MAIL_FROM,
            "to": _env_list("CONTACT_TO"),
            "reply_to": str(c.email),
            "subject": f"【お問い合わせ】{c.company} {c.name} 様",
            "text": text,
        }
    )


@app.post("/api/contact")
def contact(c: Contact, request: Request) -> JSONResponse:
    if c.website:
        # ボットには成功したように見せる
        return JSONResponse({"ok": True})

    remote_ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or None
    if not verify_turnstile(c.turnstile_token, remote_ip):
        return JSONResponse({"ok": False, "error": "captcha"}, status_code=403)

    try:
        send_mail(c)
    except Exception as e:
        # 例外メッセージに宛先などが含まれうるため、種類だけ記録する
        logger.error("send mail failed: %s", type(e).__name__)
        return JSONResponse({"ok": False, "error": "send_failed"}, status_code=502)

    return JSONResponse({"ok": True})
