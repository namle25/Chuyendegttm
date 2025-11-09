"""
Email configuration for Gmail auto-responder.

SECURITY: Do NOT commit real credentials. Prefer environment variables.

Environment variables (take precedence if set):
 - GMAIL_USER
 - GMAIL_APP_PASSWORD
 - EMAIL_ENABLED ("1" or "0")
 - EMAIL_POLL_INTERVAL (seconds)
"""

import os

# Enable/disable email responder (default enabled)
EMAIL_ENABLED = os.environ.get("EMAIL_ENABLED", "1") == "1"

# Gmail credentials (use App Password)
GMAIL_USER = os.environ.get("GMAIL_USER", "namle6247@gmail.com")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "gxde kbfg mptx lida")  # 16-char app password

# Poll interval in seconds
CHECK_INTERVAL_SECONDS = int(os.environ.get("EMAIL_POLL_INTERVAL", "1"))

# Trigger phrases (normalized to ascii when checking)
TRIGGER_PHRASES = [
    "con cho trong khong",
    "con cho trong ko",
    "co cho trong khong",
    "co cho trong ko",
    "cho trong?",
]

# Reply templates (ascii, khong dau)
REPLY_SUBJECT_PREFIX = "Tra loi: "
REPLY_OK = "Tinh trang bai do: Con {free}/{total} cho trong. Cho da do: {occupied}."
REPLY_EMPTY = "Tinh trang bai do: Hien tai khong con cho trong. Tong cho: {total}."
REPLY_ERROR = "Xin loi, he thong khong doc duoc tinh trang hien tai. Vui long thu lai sau."
