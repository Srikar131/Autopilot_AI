from __future__ import annotations

import email
import imaplib
import os
import re
from typing import Any

from src.config import settings
from src.ingestion.jobspy_worker import normalize_scraped_jobs

JOB_URL_REGEX = re.compile(
    r'https?://(?:www\.)?(?:linkedin\.com/jobs/view/[^\s"<>]+|naukri\.com/job-listings-[^\s"<>]+|boards\.greenhouse\.io/[^\s"<>]+|jobs\.lever\.co/[^\s"<>]+|jobs\.ashbyhq\.com/[^\s"<>]+)',
    re.IGNORECASE,
)


def extract_job_links_from_email_bodies(email_bodies: list[str]) -> list[dict[str, Any]]:
    """Extracts direct job links from native LinkedIn, Naukri, and ATS alert emails (Zero-Scraping Footprint)."""
    raw_items: list[dict[str, Any]] = []
    for body in email_bodies:
        matches = JOB_URL_REGEX.findall(body)
        for url in matches:
            raw_items.append(
                {
                    "job_url": url,
                    "company": "Email Alert Discovery",
                    "title": "Pending JD Fetch",
                    "location": "India",
                    "description": "Extracted via Gmail Job Alert Ingestor",
                }
            )
    return normalize_scraped_jobs(raw_items)


def fetch_unread_gmail_alert_bodies(
    gmail_address: str = "",
    gmail_app_password: str = "",
    max_messages: int = 15,
) -> list[str]:
    """
    Connects to Gmail IMAP in read-only mode to pull recent job alert email bodies
    without mutating mailbox read state. Returns [] when credentials are not configured.
    """
    if "PYTEST_CURRENT_TEST" in os.environ:
        return []
    user = (gmail_address or settings.gmail_address).strip()
    pwd = (gmail_app_password or settings.gmail_app_password).strip()
    if not user or not pwd:
        return []

    bodies: list[str] = []
    try:
        with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
            imap.login(user, pwd)
            imap.select("INBOX", readonly=True)
            status, msg_nums = imap.search(None, "UNSEEN")
            if status != "OK" or not msg_nums or not msg_nums[0]:
                return []
            ids = msg_nums[0].split()[-max_messages:]
            for msg_id in ids:
                fetch_status, msg_data = imap.fetch(msg_id, "(RFC822)")
                if fetch_status != "OK" or not msg_data:
                    continue
                raw_bytes = msg_data[0][1] if isinstance(msg_data[0], tuple) else None
                if not raw_bytes:
                    continue
                msg = email.message_from_bytes(raw_bytes)
                if msg.is_multipart():
                    for part in msg.walk():
                        ctype = part.get_content_type()
                        if ctype in ("text/plain", "text/html"):
                            payload = part.get_payload(decode=True)
                            if payload:
                                bodies.append(payload.decode("utf-8", errors="ignore"))
                else:
                    payload = msg.get_payload(decode=True)
                    if payload:
                        bodies.append(payload.decode("utf-8", errors="ignore"))
    except Exception:
        return []

    return bodies
