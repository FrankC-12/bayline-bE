"""Single-use, rate-limited recovery for active holding accounts only."""

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from sqlalchemy import select, update
from app.core.config import get_settings
from app.core.exceptions import BadRequestError, DomainError
from app.core.security import hash_password, verify_password
from app.modules.auth.models import RecoveryChallenge
from app.modules.auth.organization_access import ensure_account_available, revoke_accounts
from app.modules.auth import otp_delivery
from app.modules.roles.enums import RoleScope
from app.modules.roles.models import Role
from app.modules.users.models import User


def mask_destination(channel, value):
    if channel == "sms":
        return "•••• " + value[-4:]
    local, _, domain = value.partition("@")
    return local[:1] + "•••@" + domain


def code_digest(challenge_id, purpose, code):
    return hmac.new(
        get_settings().secret_key.encode(),
        f"{challenge_id}:{purpose}:{code}".encode(),
        hashlib.sha256,
    ).hexdigest()


def as_utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class RecoveryService:
    def __init__(self, db):
        self.db = db

    async def holding_user(self, user_id):
        user = (
            await self.db.execute(
                select(User)
                .where(User.id == user_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if user is None:
            raise BadRequestError(
                "No se puede recuperar esta cuenta.", error_code="recovery_unavailable"
            )
        await ensure_account_available(self.db, user)
        role = (
            await self.db.execute(select(Role).where(Role.id == user.role_id))
        ).scalar_one_or_none()
        if role is None or role.scope != RoleScope.HOLDING:
            raise BadRequestError(
                "Comunícate con el administrador de tu sucursal para restablecer tu contraseña.",
                error_code="contact_branch_admin",
            )
        return user

    async def options(self, email):
        user = (await self.db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            return {
                "action": "unavailable",
                "message": "No es posible recuperar esta cuenta. Verifica el correo o contacta al administrador.",
                "channels": [],
            }
        try:
            await ensure_account_available(self.db, user)
        except DomainError:
            return {
                "action": "unavailable",
                "message": "Esta cuenta o su organización no está habilitada. Contacta al administrador.",
                "channels": [],
            }
        role = (
            await self.db.execute(select(Role).where(Role.id == user.role_id))
        ).scalar_one_or_none()
        if role is None or role.scope != RoleScope.HOLDING:
            return {
                "action": "contact_admin",
                "message": (
                    "Comunícate con el administrador de tu sucursal para restablecer tu contraseña."
                    if role and role.scope == RoleScope.FILIAL
                    else "Comunícate con la administración de la plataforma para recuperar tu cuenta."
                ),
                "channels": [],
            }
        phone = user.recovery_phone if user.recovery_phone_verified_at else None
        return {
            "action": "otp",
            "message": "Elige dónde recibir tu código de verificación.",
            "channels": [
                {
                    "channel": "email",
                    "destination": mask_destination("email", user.email),
                    "available": otp_delivery.channel_available("email"),
                },
                {
                    "channel": "sms",
                    "destination": (
                        mask_destination("sms", phone) if phone else "Sin teléfono verificado"
                    ),
                    "available": bool(phone) and otp_delivery.channel_available("sms"),
                },
            ],
        }

    async def issue(self, user, purpose, channel, destination):
        if not otp_delivery.channel_available(channel):
            raise BadRequestError(
                "Este canal no está disponible. Elige otro o contacta al administrador.",
                error_code="recovery_channel_unavailable",
            )
        now = datetime.now(UTC)
        recent = (
            await self.db.execute(
                select(RecoveryChallenge.id)
                .where(
                    RecoveryChallenge.user_id == user.id,
                    RecoveryChallenge.purpose == purpose,
                    RecoveryChallenge.expires_at
                    > now
                    + timedelta(minutes=get_settings().recovery_otp_minutes)
                    - timedelta(seconds=60),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if recent:
            raise BadRequestError(
                "Espera un minuto antes de solicitar otro código.", error_code="otp_resend_wait"
            )
        await self.db.execute(
            update(RecoveryChallenge)
            .where(
                RecoveryChallenge.user_id == user.id,
                RecoveryChallenge.purpose == purpose,
                RecoveryChallenge.consumed_at.is_(None),
            )
            .values(consumed_at=now)
        )
        code = f"{secrets.randbelow(1000000):06d}"
        challenge_id = uuid.uuid4()
        challenge = RecoveryChallenge(
            id=challenge_id,
            user_id=user.id,
            purpose=purpose,
            channel=channel,
            destination=destination,
            code_hash=code_digest(challenge_id, purpose, code),
            expires_at=now + timedelta(minutes=get_settings().recovery_otp_minutes),
            attempts=0,
        )
        self.db.add(challenge)
        try:
            await self.db.flush()
            await otp_delivery.send_otp(channel, destination, code)
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise
        return {
            "challenge_id": str(challenge_id),
            "destination": mask_destination(channel, destination),
            "expires_in": get_settings().recovery_otp_minutes * 60,
        }

    async def request_reset(self, email, channel):
        user = (await self.db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        user = await self.holding_user(user.id if user else uuid.uuid4())
        destination = (
            user.email
            if channel == "email"
            else (user.recovery_phone if user.recovery_phone_verified_at else None)
        )
        if not destination:
            raise BadRequestError(
                "No hay un teléfono verificado para esta cuenta.",
                error_code="recovery_phone_unavailable",
            )
        return await self.issue(user, "password_reset", channel, destination)

    async def verify(self, challenge_id, code, purpose, owner_id=None):
        # Always lock the account before the challenge, matching suspension/reset order.
        user_id = (
            await self.db.execute(
                select(RecoveryChallenge.user_id).where(RecoveryChallenge.id == challenge_id)
            )
        ).scalar_one_or_none()
        if user_id is None or (owner_id is not None and owner_id != user_id):
            raise BadRequestError("El código es incorrecto o ha vencido.", error_code="invalid_otp")
        user = await self.holding_user(user_id)
        challenge = (
            await self.db.execute(
                select(RecoveryChallenge)
                .where(RecoveryChallenge.id == challenge_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        now = datetime.now(UTC)
        if (
            challenge.purpose != purpose
            or challenge.consumed_at
            or as_utc(challenge.expires_at) <= now
            or challenge.attempts >= get_settings().recovery_otp_attempts
        ):
            raise BadRequestError("El código es incorrecto o ha vencido.", error_code="invalid_otp")
        if not hmac.compare_digest(challenge.code_hash, code_digest(challenge.id, purpose, code)):
            challenge.attempts += 1
            await self.db.commit()
            raise BadRequestError("El código es incorrecto o ha vencido.", error_code="invalid_otp")
        return user, challenge

    async def reset(self, challenge_id, code, password):
        user, challenge = await self.verify(challenge_id, code, "password_reset")
        from app.core.audit_logging import set_actor
        from app.modules.holdings.control_models import HoldingAuditEvent

        set_actor(user.id, user.filial_id, user.holding_id)
        self.db.add(
            HoldingAuditEvent(
                holding_id=user.holding_id,
                user_id=user.id,
                action="password_reset",
                entity="users",
                resource_id=str(user.id),
                changed_fields=["password"],
            )
        )
        user.password_hash = hash_password(password)
        await self.db.flush()
        await revoke_accounts(self.db, select(User.id).where(User.id == user.id))
        await self.db.commit()

    async def phone_status(self, user_id):
        user = await self.holding_user(user_id)
        return {
            "phone": user.recovery_phone,
            "verified": bool(user.recovery_phone_verified_at),
            "sms_available": otp_delivery.channel_available("sms"),
        }

    async def request_phone(self, user_id, phone, current_password):
        user = await self.holding_user(user_id)
        if not user.password_hash or not verify_password(current_password, user.password_hash):
            raise BadRequestError(
                "La contraseña actual es incorrecta.", error_code="invalid_current_password"
            )
        return await self.issue(user, "phone_enroll", "sms", phone)

    async def verify_phone(self, user_id, challenge_id, code):
        user, challenge = await self.verify(challenge_id, code, "phone_enroll", user_id)
        user.recovery_phone = challenge.destination
        user.recovery_phone_verified_at = datetime.now(UTC)
        challenge.consumed_at = datetime.now(UTC)
        await self.db.commit()
