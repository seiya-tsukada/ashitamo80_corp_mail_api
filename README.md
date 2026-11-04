# ashitamo80_corp_mail_api

コーポレートサイト（[ashitamo80_corp](https://github.com/seiya-tsukada/ashitamo80_corp)、S3 / CloudFront）のお問い合わせフォームを受け付ける API です。
Vercel 上の Python 関数（FastAPI）として動き、ハニーポット（`website` 項目）でボットを除いたうえで、Resend で社内宛てに通知メールを送ります。

```
www.ashitamo80.com（S3 / CloudFront）
  └─ fetch POST ─▶ api.ashitamo80.com/api/contact（Vercel・このリポジトリ）
                     └─ Resend ─▶ CONTACT_TO
```

## API 仕様

`POST /api/contact`（`Content-Type: application/json`）

| 項目 | 必須 | 内容 |
| --- | --- | --- |
| `company` | ○ | 会社名・団体名（100文字まで、改行不可） |
| `name` | ○ | お名前（50文字まで、改行不可） |
| `email` | ○ | メールアドレス（通知メールの Reply-To になる） |
| `size` | | 従業員数（30文字まで） |
| `topic` | | ご相談内容（60文字まで） |
| `body` | ○ | ご相談の概要（4000文字まで） |
| `website` | | honeypot。人間には見えない入力欄で、値があれば送信せずに成功を返す |

上記以外の項目（旧フロントの `turnstile_token` など）は無視します。

| ステータス | レスポンス | 意味 |
| --- | --- | --- |
| 200 | `{"ok": true}` | 受付完了 |
| 400 | `{"ok": false, "error": "invalid", "fields": [...]}` | 入力エラー（`fields` は該当する項目名） |
| 413 | `{"ok": false, "error": "too_large"}` | リクエストが 32KB を超えている |
| 500 | `{"ok": false, "error": "server_error"}` | 必須の環境変数が未設定（ログに変数名を出力） |
| 502 | `{"ok": false, "error": "send_failed"}` | メール送信に失敗 |

`company` / `name` / `size` / `topic` は改行・タブ・制御文字・bidi 制御文字を、`body` は改行とタブ以外の制御文字を受け付けません。

項目を変更するときは、**API を先にデプロイしてからフロントを変更**します。新旧どちらの形式でも受け付ける状態を経由してください。

## 環境変数

Vercel の Settings → Environment Variables で、Production と Preview / Development を分けて設定します。

| 変数 | Production | Preview / Development |
| --- | --- | --- |
| `RESEND_API_KEY` | 本番キー | 同じキーでも可 |
| `CONTACT_TO` | 受信アドレス（カンマ区切りで複数可） | 開発者のアドレス |
| `EXTRA_ORIGINS` | 設定しない | 省略可（ローカル確認用。例: `http://localhost:8000`、カンマ区切りで複数可） |

## ローカル開発

[uv](https://docs.astral.sh/uv/) を使います。

```bash
uv sync
cp .env.example .env   # 値を埋める
uv run --env-file .env uvicorn api.contact:app --reload --port 8001
```

フロント（ashitamo80_corp）は `python3 -m http.server 8000` で起動し、送信先を `http://localhost:8001/api/contact` にして確認します。

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest
```

依存関係を変えたら、Vercel 用の `requirements.txt` も更新してください。

```bash
uv export --frozen --no-dev --no-hashes --no-emit-project -o requirements.txt
```

## デプロイ

Vercel の Git 連携でデプロイします（Framework Preset は「Other」）。

- PR を作るとプレビュー環境にデプロイ
- main にマージすると本番にデプロイ
- 独自ドメイン `api.ashitamo80.com` を Vercel に追加し、Google Workspace の DNS に CNAME を登録

## 初期設定チェックリスト

- [ ] Resend: `ashitamo80.com` を登録し、DKIM / SPF / MX レコードを DNS に追加、API キーを発行
- [ ] Vercel: リポジトリをインポートし、環境変数とドメインを設定
- [ ] main ブランチを保護（PR を必須にする）
