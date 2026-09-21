from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.exceptions import DomainError

# Pydantic v2's built-in messages (Field(min_length=...), type coercion,
# missing fields, etc.) come out in English — this app has no other English
# left anywhere in its error responses (every DomainError subclass already
# raises Spanish messages), so this is the one remaining gap. Custom
# field_validator/model_validator messages don't need translation: Pydantic
# wraps them as type "value_error" with the ORIGINAL message preserved in
# ctx["error"] (the "Value error, " prefix only shows up in `msg`, which we
# never use) — and every validator in this codebase already raises Spanish.
_TYPE_MESSAGES: dict[str, Callable[[dict[str, Any]], str]] = {
    "missing": lambda ctx: "Este campo es obligatorio.",
    "string_too_short": lambda ctx: f"Debe tener al menos {ctx.get('min_length')} caracteres.",
    "string_too_long": lambda ctx: f"Debe tener como máximo {ctx.get('max_length')} caracteres.",
    "too_short": lambda ctx: f"Debe tener al menos {ctx.get('min_length')} elementos.",
    "too_long": lambda ctx: f"Debe tener como máximo {ctx.get('max_length')} elementos.",
    "greater_than_equal": lambda ctx: f"Debe ser mayor o igual a {ctx.get('ge')}.",
    "less_than_equal": lambda ctx: f"Debe ser menor o igual a {ctx.get('le')}.",
    "greater_than": lambda ctx: f"Debe ser mayor a {ctx.get('gt')}.",
    "less_than": lambda ctx: f"Debe ser menor a {ctx.get('lt')}.",
    "int_parsing": lambda ctx: "Debe ser un número entero.",
    "int_type": lambda ctx: "Debe ser un número entero.",
    "float_parsing": lambda ctx: "Debe ser un número.",
    "float_type": lambda ctx: "Debe ser un número.",
    "decimal_parsing": lambda ctx: "Debe ser un número válido.",
    "string_type": lambda ctx: "Debe ser un texto.",
    "bool_parsing": lambda ctx: "Debe ser verdadero o falso.",
    "bool_type": lambda ctx: "Debe ser verdadero o falso.",
    "uuid_parsing": lambda ctx: "Debe ser un identificador válido.",
    "uuid_type": lambda ctx: "Debe ser un identificador válido.",
    "date_parsing": lambda ctx: "Debe ser una fecha válida (AAAA-MM-DD).",
    "date_from_datetime_parsing": lambda ctx: "Debe ser una fecha válida.",
    "datetime_parsing": lambda ctx: "Debe ser una fecha y hora válidas.",
    "time_parsing": lambda ctx: "Debe ser una hora válida.",
    "enum": lambda ctx: "El valor elegido no es una opción válida.",
    "literal_error": lambda ctx: "El valor elegido no es una opción válida.",
    "json_invalid": lambda ctx: "El formato enviado no es válido.",
    "extra_forbidden": lambda ctx: "Este campo no es reconocido.",
    "string_pattern_mismatch": lambda ctx: "El formato no es válido.",
    "list_type": lambda ctx: "Debe ser una lista de valores.",
    "dict_type": lambda ctx: "El formato enviado no es válido.",
    "value_error": lambda ctx: ctx.get("error") or "El valor ingresado no es válido.",
}


def _translate_pydantic_error(error: dict[str, Any]) -> str:
    builder = _TYPE_MESSAGES.get(error.get("type", ""))
    if builder:
        try:
            return str(builder(error.get("ctx") or {}))
        except Exception:
            pass
    return "El valor ingresado no es válido."


def _field_path(loc: tuple) -> str | None:
    """Drops the request-part prefix (body/query/path/header) so the
    frontend gets a plain field name (or dotted path for nested fields) it
    can match against its own form state — e.g. ("body", "vin") -> "vin"."""
    parts = [str(p) for p in loc if p not in ("body", "query", "path", "header")]
    return ".".join(parts) if parts else None


def _build_error_response(
    status_code: int,
    error_code: str,
    message: str,
    path: str,
    details: list | None = None,
) -> JSONResponse:
    content = {
        "statusCode": status_code,
        "errorCode": error_code,
        "message": message,
        "path": path,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if details:
        content["details"] = details
    return JSONResponse(status_code=status_code, content=content)


def register_exception_handlers(app: FastAPI) -> None:
    """Register global exception handlers so every error response follows the same shape."""

    @app.exception_handler(DomainError)
    async def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return _build_error_response(
            exc.status_code, exc.error_code, exc.message, request.url.path, details=exc.details
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        field_errors = [
            {"field": _field_path(error.get("loc", ())), "message": _translate_pydantic_error(error)}
            for error in exc.errors()
        ]
        if len(field_errors) == 1:
            single = field_errors[0]
            message = f"{single['field']}: {single['message']}" if single["field"] else single["message"]
        else:
            message = "Hay campos inválidos o incompletos — revisa los detalles marcados."
        return _build_error_response(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "validation_error",
            message,
            request.url.path,
            details=field_errors,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        return _build_error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "internal_error",
            "Ocurrió un error inesperado. Intenta de nuevo.",
            request.url.path,
        )
