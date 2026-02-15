from bid_rss_mailer.mailer import SmtpConfig, send_text_email


class _FlakySMTP:
    attempts = 0

    def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return None

    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):  # type: ignore[no-untyped-def]
        return False

    def ehlo(self) -> None:
        return None

    def starttls(self) -> None:
        return None

    def login(self, user: str, password: str) -> None:
        return None

    def send_message(self, message) -> None:  # type: ignore[no-untyped-def]
        _FlakySMTP.attempts += 1
        if _FlakySMTP.attempts == 1:
            raise OSError("temporary network error")


def test_send_text_email_retries_once(monkeypatch) -> None:
    _FlakySMTP.attempts = 0
    monkeypatch.setattr("bid_rss_mailer.mailer.smtplib.SMTP", _FlakySMTP)

    smtp_config = SmtpConfig(
        host="127.0.0.1",
        port=1025,
        user="",
        password="",
        from_address="noreply@example.local",
        starttls=False,
        use_ssl=False,
    )

    send_text_email(
        smtp_config=smtp_config,
        to_address="admin@example.local",
        subject="subject",
        body="body",
        max_attempts=3,
        retry_wait_sec=0,
    )

    assert _FlakySMTP.attempts == 2

