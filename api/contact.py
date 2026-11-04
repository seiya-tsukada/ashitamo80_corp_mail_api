"""コーポレートサイトのお問い合わせフォーム受付 API（POST /api/contact）。

Vercel は api/ 配下の各ファイルを関数として公開する。このファイルは
FastAPI の `app` を公開し、ハニーポットで bot を除いたうえで Resend で社内宛てに通知メールを送る。
"""

import logging
import os
from typing import Annotated

import resend
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, StringConstraints

logger = logging.getLogger(__name__)

# 送信元。社内宛ての通知だけなので当面変わらず、コードで持つ（返信は reply_to で問い合わせ元に届く）
MAIL_FROM = "アシタモハレ お問い合わせ <info@ashitamo80.com>"
# フォームの JSON はこれで十分収まる。大きな本文でメモリや Resend の上限を圧迫させない
MAX_BODY_BYTES = 32 * 1024

# CORS で許可する送信元。本番サイトだけで当面変わらないのでコードで持つ
ALLOWED_ORIGINS = ["https://www.ashitamo80.com"]

# Resend SDK の既定タイムアウト（30 秒）は関数の実行時間に対して長いので短くする
resend.default_http_client = resend.RequestsClient(timeout=10)


class ConfigError(Exception):
    """必須の環境変数が設定されていない。"""


def _env_list(name: str) -> list[str]:
    return [v.strip() for v in os.environ.get(name, "").split(",") if v.strip()]


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(name)
    return value


# DEL / C1 制御文字・Unicode の改行・表示順を入れ替えて件名などを偽装できる bidi 制御文字
_CONTROL = r"\x7f-\x9f  ‪-‮⁦-⁩"
# 件名やヘッダーに入る項目は改行・タブを含む制御文字をすべて禁止する
OneLine = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=rf"^[^\x00-\x1f{_CONTROL}]*$")
]
# 本文は改行（\n, \r）とタブだけ許可する
MultiLine = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, pattern=rf"^[^\x00-\x08\x0b\x0c\x0e-\x1f{_CONTROL}]*$"
    ),
]


class Contact(BaseModel):
    company: Annotated[OneLine, StringConstraints(min_length=1, max_length=100)]
    name: Annotated[OneLine, StringConstraints(min_length=1, max_length=50)]
    email: EmailStr
    size: Annotated[OneLine, StringConstraints(max_length=30)] = ""
    topic: Annotated[OneLine, StringConstraints(max_length=60)] = ""
    body: Annotated[MultiLine, StringConstraints(min_length=1, max_length=4000)]
    # honeypot（人間には見えない入力欄）。長さで 400 を返すとボットに気付かれるので上限を設けない
    website: str = ""


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    CORSMiddleware,
    # EXTRA_ORIGINS はローカル確認用（本番の Vercel には設定しない）
    allow_origins=ALLOWED_ORIGINS + _env_list("EXTRA_ORIGINS"),
    allow_methods=["POST"],
    allow_headers=["Content-Type"],
)


@app.middleware("http")
async def _guard(request: Request, call_next):
    length = request.headers.get("content-length")
    if length and (not length.isdigit() or int(length) > MAX_BODY_BYTES):
        return JSONResponse({"ok": False, "error": "too_large"}, status_code=413)
    response = await call_next(request)
    # 問い合わせ内容を含みうる応答をキャッシュさせない
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.exception_handler(RequestValidationError)
def _invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
    # 入力値をそのまま返さないよう、項目名だけ返す
    fields = sorted({str(e["loc"][-1]) for e in exc.errors() if e.get("loc")})
    return JSONResponse({"ok": False, "error": "invalid", "fields": fields}, status_code=400)


@app.exception_handler(ConfigError)
def _misconfigured(_: Request, exc: ConfigError) -> JSONResponse:
    # 変数名だけ記録し、利用者には詳細を返さない
    logger.error("missing environment variable: %s", exc)
    return JSONResponse({"ok": False, "error": "server_error"}, status_code=500)


def build_email(c: Contact, to: list[str]) -> resend.Emails.SendParams:
    # HTML は使わずテキストのみで送る（入力値による HTML インジェクションを避ける）
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
    return {
        "from": MAIL_FROM,
        "to": to,
        "reply_to": str(c.email),
        "subject": f"【お問い合わせ】{c.company} {c.name} 様",
        "text": text,
    }


def send_mail(c: Contact) -> str:
    """Resend で通知メールを送り、メール ID を返す。"""
    resend.api_key = _require_env("RESEND_API_KEY")
    to = _env_list("CONTACT_TO")
    if not to:
        raise ConfigError("CONTACT_TO")
    return resend.Emails.send(build_email(c, to))["id"]


@app.post("/api/contact")
def contact(c: Contact) -> JSONResponse:
    if c.website:
        # ボットには成功したように見せる
        return JSONResponse({"ok": True})

    try:
        email_id = send_mail(c)
    except ConfigError:
        raise
    except resend.exceptions.ResendError as e:
        # 例外メッセージに宛先などが含まれうるため、コードと種類だけ記録する
        logger.error("resend error: code=%s type=%s", e.code, e.error_type)
        return JSONResponse({"ok": False, "error": "send_failed"}, status_code=502)
    except Exception as e:
        logger.error("send mail failed: %s", type(e).__name__)
        return JSONResponse({"ok": False, "error": "send_failed"}, status_code=502)

    logger.info("contact mail sent: id=%s", email_id)
    return JSONResponse({"ok": True})
