import os

# CORS の許可オリジンは import 時に読まれるため、api.contact より先に設定する
os.environ.setdefault("ALLOWED_ORIGINS", "https://www.ashitamo80.com")
