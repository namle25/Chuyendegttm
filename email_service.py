import imaplib
import smtplib
import email
from email.message import EmailMessage
import threading
import traceback
import unicodedata
from typing import Optional

import email_config
import runtime_status  # to read current status from main loop


# Track processed message IDs to avoid duplicate replies per session
_processed_ids = set()
_thread: Optional[threading.Thread] = None
_stop_event: Optional[threading.Event] = None


def _to_ascii_lower(text: str) -> str:
    if not isinstance(text, str):
        try:
            text = str(text or "")
        except Exception:
            return ""
    # remove diacritics
    normalized = unicodedata.normalize('NFD', text)
    without_marks = ''.join(ch for ch in normalized if not unicodedata.combining(ch))
    return without_marks.lower()


def _matches_trigger(text: str) -> bool:
    t = _to_ascii_lower(text)
    for phrase in email_config.TRIGGER_PHRASES:
        if phrase in t:
            return True
    return False


def _format_status() -> str:
    try:
        st = runtime_status.get_status()
        total = int(st.get("total", 0))
        free = int(st.get("free", 0))
        occupied = int(st.get("occupied", max(0, total - free)))
        if total <= 0:
            return "He thong chua khoi dong hoac chua co polygon. Vui long them polygon va bat nhan dien."
        if free > 0:
            return email_config.REPLY_OK.format(free=free, total=total, occupied=occupied)
        else:
            return email_config.REPLY_EMPTY.format(total=total)
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")
        return email_config.REPLY_ERROR


def _send_email(to_addr: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg['From'] = email_config.GMAIL_USER
    msg['To'] = to_addr
    msg['Subject'] = subject
    msg.set_content(body)

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(email_config.GMAIL_USER, email_config.GMAIL_APP_PASSWORD)
        smtp.send_message(msg)


def _process_message(imap_conn, mail_id: bytes) -> None:
    # fetch full message
    status, data = imap_conn.fetch(mail_id, '(RFC822)')
    if status != 'OK' or not data or not data[0]:
        return
    raw = data[0][1]
    msg = email.message_from_bytes(raw)

    subject = msg.get('Subject', '')
    from_addr = email.utils.parseaddr(msg.get('From', ''))[1]

    # get text body
    body_text = ''
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == 'text/plain':
                try:
                    body_text = part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', errors='ignore')
                    break
                except Exception:
                    continue
    else:
        try:
            body_text = msg.get_payload(decode=True).decode(msg.get_content_charset() or 'utf-8', errors='ignore')
        except Exception:
            body_text = str(msg.get_payload())

    combined_text = f"{subject}\n{body_text}"
    if not _matches_trigger(combined_text):
        return

    print(f"[EMAIL] Trigger phat hien! Tu: {from_addr}, Subject: {subject}")
    reply_subject = email_config.REPLY_SUBJECT_PREFIX + (subject or "Tinh trang bai do")
    reply_body = _format_status()
    print(f"[EMAIL] Gui tra loi: {reply_body}")
    _send_email(from_addr, reply_subject, reply_body)
    print(f"[EMAIL] Da gui thanh cong den {from_addr}")

    # mark message as seen
    try:
        imap_conn.store(mail_id, '+FLAGS', '\\Seen')
    except Exception as e:
        print(f"[EMAIL] Khong danh dau Seen duoc: {e}")


def _poll_loop(stop_event: threading.Event):
    while not stop_event.is_set():
        try:
            with imaplib.IMAP4_SSL('imap.gmail.com') as imap:
                imap.login(email_config.GMAIL_USER, email_config.GMAIL_APP_PASSWORD)
                imap.select('INBOX')
                # search unseen messages
                status, data = imap.search(None, '(UNSEEN)')
                if status == 'OK':
                    ids = data[0].split()
                    # only process the latest 50 unseen messages
                    ids = ids[-50:]
                    print(f"[EMAIL] Unseen to check: {len(ids)}")
                    for mail_id in ids:
                        if mail_id in _processed_ids:
                            continue
                        try:
                            _process_message(imap, mail_id)
                            _processed_ids.add(mail_id)
                        except Exception:
                            # swallow per-message errors
                            traceback.print_exc()
                try:
                    imap.logout()
                except Exception:
                    pass
        except Exception:
            traceback.print_exc()

        stop_event.wait(email_config.CHECK_INTERVAL_SECONDS)


def start_if_enabled():
    global _thread, _stop_event
    if not email_config.EMAIL_ENABLED:
        return
    if _thread and _thread.is_alive():
        return
    _stop_event = threading.Event()
    _thread = threading.Thread(target=_poll_loop, args=(_stop_event,), daemon=True)
    print(f"[EMAIL] Starting email responder. Poll every {email_config.CHECK_INTERVAL_SECONDS}s as {email_config.GMAIL_USER}")
    _thread.start()


def stop():
    global _thread, _stop_event
    if _stop_event:
        _stop_event.set()
    _thread = None
    _stop_event = None
