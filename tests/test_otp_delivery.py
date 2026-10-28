"""Delivery adapters use configured transports without exposing OTPs in errors."""

from types import SimpleNamespace
import httpx
import pytest
from app.modules.auth import otp_delivery


def settings(**overrides):
    values = dict(
        smtp_host=None,
        smtp_from_email=None,
        smtp_port=587,
        smtp_username=None,
        smtp_password=None,
        smtp_ssl=False,
        smtp_starttls=True,
        app_env="test",
        twilio_account_sid=None,
        twilio_auth_token=None,
        twilio_from_number=None,
        recovery_otp_minutes=10,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_no_provider_never_claims_to_send(monkeypatch):
    monkeypatch.setattr(otp_delivery, "get_settings", lambda: settings())
    assert not otp_delivery.channel_available("email")
    assert not otp_delivery.channel_available("sms")
    with pytest.raises(otp_delivery.OtpDeliveryError) as failure:
        await otp_delivery.send_otp("email", "user@example.com", "123456")
    assert failure.value.status_code == 503
    assert "123456" not in failure.value.message


@pytest.mark.asyncio
async def test_smtp_uses_tls_and_configured_recipient(monkeypatch):
    monkeypatch.setattr(
        otp_delivery,
        "get_settings",
        lambda: settings(
            smtp_host="smtp.example.com",
            smtp_from_email="noreply@example.com",
            smtp_username="test",
            smtp_password="test-secret",
        ),
    )
    calls = []

    class SMTP:
        def __init__(self, host, port, **kwargs):
            calls.append(("connection", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def starttls(self, **kwargs):
            calls.append(("tls",))

        def login(self, user, password):
            calls.append(("login", user))

        def send_message(self, message):
            calls.append(("send", message["To"], "123456" in message.get_content()))

    monkeypatch.setattr(otp_delivery.smtplib, "SMTP", SMTP)
    await otp_delivery.send_otp("email", "user@example.com", "123456")
    assert calls == [
        ("connection", "smtp.example.com", 587),
        ("tls",),
        ("login", "test"),
        ("send", "user@example.com", True),
    ]


@pytest.mark.asyncio
async def test_sms_provider_and_failures_are_sanitized(monkeypatch):
    sid = "AC" + "a" * 32
    monkeypatch.setattr(
        otp_delivery,
        "get_settings",
        lambda: settings(
            twilio_account_sid=sid,
            twilio_auth_token="test-secret",
            twilio_from_number="+15551234567",
        ),
    )
    seen = []

    def transport(request):
        seen.append(request)
        return httpx.Response(201, json={"status": "queued"})

    client = httpx.AsyncClient
    monkeypatch.setattr(
        otp_delivery.httpx,
        "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(transport)),
    )
    await otp_delivery.send_otp("sms", "+584121234567", "123456")
    assert str(seen[0].url) == f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    assert b"To=%2B584121234567" in seen[0].content

    def failing(request):
        return httpx.Response(400, json={"message": "sensitive provider body 123456"})

    monkeypatch.setattr(
        otp_delivery.httpx,
        "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(failing)),
    )
    with pytest.raises(otp_delivery.OtpDeliveryError) as failure:
        await otp_delivery.send_otp("sms", "+584121234567", "123456")
    assert failure.value.status_code == 503
    assert "123456" not in failure.value.message and "sensitive" not in failure.value.message
