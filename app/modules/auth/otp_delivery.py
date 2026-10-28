"""Configurable OTP transports. Never log or return codes to API callers."""

import asyncio
import re
import smtplib
import ssl
from email.message import EmailMessage

import httpx
from app.core.config import get_settings
from app.core.exceptions import DomainError


class OtpDeliveryError(DomainError):
    status_code = 503


def channel_available(channel: str) -> bool:
    settings = get_settings()
    if channel == "email":
        return bool(settings.smtp_host and settings.smtp_from_email)
    return bool(
        settings.twilio_account_sid
        and re.fullmatch(r"AC[0-9a-fA-F]{32}", settings.twilio_account_sid)
        and settings.twilio_auth_token
        and settings.twilio_from_number
    )


async def send_otp(channel: str, destination: str, code: str):
    settings = get_settings()
    if not channel_available(channel):
        raise OtpDeliveryError(
            "Este canal de recuperación no está disponible temporalmente. Contacta al administrador.",
            error_code="recovery_channel_unavailable",
        )
    body = f"Motrix: tu código de verificación es {code}. Vence en {settings.recovery_otp_minutes} minutos. No lo compartas. Si no lo solicitaste, ignora este mensaje."
    try:
        if channel == "email":

            def deliver():
                message = EmailMessage()
                message["Subject"] = "Código de verificación Motrix"
                message["From"] = settings.smtp_from_email
                message["To"] = destination
                message.set_content(body)
                if (
                    not settings.smtp_ssl
                    and not settings.smtp_starttls
                    and settings.app_env in {"production", "staging"}
                ):
                    raise ValueError("TLS required")
                factory = smtplib.SMTP_SSL if settings.smtp_ssl else smtplib.SMTP
                kwargs = {"context": ssl.create_default_context()} if settings.smtp_ssl else {}
                with factory(settings.smtp_host, settings.smtp_port, timeout=10, **kwargs) as smtp:
                    if not settings.smtp_ssl and settings.smtp_starttls:
                        smtp.starttls(context=ssl.create_default_context())
                    if settings.smtp_username:
                        smtp.login(settings.smtp_username, settings.smtp_password or "")
                    smtp.send_message(message)

            await asyncio.to_thread(deliver)
        else:
            async with httpx.AsyncClient(timeout=10) as client:
                result = await client.post(
                    f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json",
                    auth=(settings.twilio_account_sid, settings.twilio_auth_token),
                    data={"From": settings.twilio_from_number, "To": destination, "Body": body},
                )
                result.raise_for_status()
    except Exception:
        raise OtpDeliveryError(
            "No se pudo enviar el código. Intenta nuevamente más tarde.",
            error_code="recovery_delivery_failed",
        ) from None
