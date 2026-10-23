"""Reviewed, atomic CSV catalog onboarding; identifiers stay server-owned."""

import csv
import hashlib
import hmac
import io
import json
import re
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.exceptions import BadRequestError, ConflictError
from app.modules.filiales.models import Filial
from app.modules.holdings.models import Holding
from app.modules.parts.models import Part, PartCategory, PartMeasure
from app.modules.post_ventas.enums import TemparioCategory
from app.modules.post_ventas.models import Tempario, TemparioPart
from app.modules.post_ventas.schemas import TemparioCreate
from app.modules.service_orders.models import Bay
from app.modules.vehicle_catalog.models import VehicleBrand, VehicleModel
from app.modules.vehicle_catalog.schemas import VehicleModelCreate
from app.modules.warehouse.models import StockInReason, StockOutReason


class CatalogKind(StrEnum):
    BRANDS = "marcas"
    MODELS = "modelos"
    CATEGORIES = "categorias"
    MEASURES = "medidas"
    INBOUND = "entradas"
    OUTBOUND = "salidas"
    BAYS = "bahias"
    SERVICES = "temparios"


SIMPLE = {
    CatalogKind.BRANDS: VehicleBrand,
    CatalogKind.CATEGORIES: PartCategory,
    CatalogKind.MEASURES: PartMeasure,
    CatalogKind.INBOUND: StockInReason,
    CatalogKind.OUTBOUND: StockOutReason,
    CatalogKind.BAYS: Bay,
}
HEADERS = {
    **{kind: ["nombre", "activo"] for kind in SIMPLE},
    CatalogKind.MODELS: ["marca", "nombre", "tipo_vehiculo", "activo"],
    CatalogKind.SERVICES: [
        "categoria",
        "numero",
        "nombre",
        "horas",
        "garantia",
        "dias",
        "kilometros",
        "ano_desde",
        "ano_hasta",
        "compatibilidad",
        "herramientas",
        "repuestos",
        "activo",
    ],
}
EXAMPLES = {
    CatalogKind.BRANDS: ["Toyota", "si"],
    CatalogKind.MODELS: ["Toyota", "Corolla", "Sedán", "si"],
    CatalogKind.CATEGORIES: ["Filtros", "si"],
    CatalogKind.MEASURES: ["Litro", "si"],
    CatalogKind.INBOUND: ["Ajuste de inventario", "si"],
    CatalogKind.OUTBOUND: ["Consumo interno", "si"],
    CatalogKind.BAYS: ["Bahía 1", "si"],
    CatalogKind.SERVICES: [
        "mantenimiento_preventivo",
        "",
        "Cambio de aceite",
        "0,5",
        "standard",
        "",
        "",
        "",
        "",
        "Toyota:Corolla",
        "Llave de filtro",
        "QA-001:1|QA-002:6",
        "si",
    ],
}


class BulkInput(BaseModel):
    rows: list[dict[str, str]] = Field(min_length=1, max_length=1000)
    review_hash: str | None = Field(default=None, max_length=64)


def template_csv(kind):
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";")
    writer.writerow(HEADERS[kind])
    # Keep examples in documentation, so importing a blank template cannot
    # accidentally create sample dealership data.
    return "\ufeff" + output.getvalue()


def number(value, *, integer=False, default=None):
    text = value.strip()
    if not text:
        return default
    # CSV uses semicolons: 1.234,56 and 1234.56 are both accepted.
    if "," in text:
        if not re.fullmatch(r"(?:\d+|[1-9]\d{0,2}(?:\.\d{3})+),\d+", text):
            raise ValueError("Usa números como 1.234,56 o 1234.56.")
        normalized = text.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"[1-9]\d{0,2}(?:\.\d{3})+", text):
        normalized = text.replace(".", "")
    else:
        normalized = text
    try:
        result = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError(f"Número inválido: {value}") from exc
    if not result.is_finite() or result < 0 or (integer and result != result.to_integral_value()):
        raise ValueError(f"Número inválido: {value}")
    if integer and result > 2147483647:
        raise ValueError("El entero excede el máximo admitido por la base de datos.")
    return int(result) if integer else float(result)


def active(value):
    normalized = value.strip().casefold()
    if normalized in {"", "si", "sí", "true", "1"}:
        return True
    if normalized in {"no", "false", "0"}:
        return False
    raise ValueError("Activo debe ser si o no.")


def review_digest(kind, filial_id, actor_id, rows):
    content = json.dumps(
        [str(kind), str(filial_id), str(actor_id), rows], sort_keys=True, ensure_ascii=False
    )
    return hmac.new(
        get_settings().secret_key.encode(), content.encode(), hashlib.sha256
    ).hexdigest()


class CatalogBulkService:
    def __init__(self, db):
        self.db = db

    async def prepare(self, kind, filial_id, actor_id, rows, *, commit=False, review_hash=None):
        if commit and not hmac.compare_digest(
            review_hash or "", review_digest(kind, filial_id, actor_id, rows)
        ):
            raise ConflictError(
                "Revisa el archivo antes de confirmar la carga.", error_code="bulk_review_required"
            )
        filial = await self.db.get(Filial, filial_id)
        if filial is None:
            raise BadRequestError("La filial no existe.")
        if commit:
            await self.db.execute(
                select(Holding.id).where(Holding.id == filial.holding_id).with_for_update()
            )
            await self.db.execute(select(Filial.id).where(Filial.id == filial_id).with_for_update())
        model = SIMPLE.get(kind)
        if model:
            scope = (
                model.filial_id == filial_id
                if kind == CatalogKind.BAYS
                else model.holding_id == filial.holding_id
            )
            existing = {
                name.strip().casefold()
                for name in (await self.db.execute(select(model.name).where(scope))).scalars()
            }
        else:
            existing = set()
        brands = list(
            (
                await self.db.execute(
                    select(VehicleBrand).where(VehicleBrand.holding_id == filial.holding_id)
                )
            ).scalars()
        )
        by_brand = {brand.name.strip().casefold(): brand for brand in brands}
        models = list(
            (
                await self.db.execute(
                    select(VehicleModel).where(VehicleModel.brand_id.in_([b.id for b in brands]))
                )
            ).scalars()
        )
        model_keys = {(m.brand_id, m.name.strip().casefold()) for m in models}
        parts = (
            list((await self.db.execute(select(Part).where(Part.filial_id == filial_id))).scalars())
            if kind == CatalogKind.SERVICES
            else []
        )
        by_part = {part.code.casefold(): part for part in parts if part.is_active}
        sequence_rows = (
            (
                await self.db.execute(
                    select(Tempario.category, Tempario.sequence_number).where(
                        Tempario.filial_id == filial_id
                    )
                )
            ).all()
            if kind == CatalogKind.SERVICES
            else []
        )
        codes = set(sequence_rows)
        maxima = {
            category: max((seq for cat, seq in sequence_rows if cat == category), default=500)
            for category in TemparioCategory
        }
        planned, errors = [], []
        for index, raw in enumerate(rows, 2):
            try:
                unknown = set(raw) - set(HEADERS[kind])
                if unknown:
                    raise ValueError(f"Columnas desconocidas: {', '.join(sorted(unknown))}")
                row = {key: raw.get(key, "").strip() for key in HEADERS[kind]}
                name = row.get("nombre", "")
                entity = model or (VehicleModel if kind == CatalogKind.MODELS else Tempario)
                max_name = entity.__table__.c.name.type.length
                if not name or len(name) > max_name:
                    raise ValueError(f"Nombre obligatorio, máximo {max_name} caracteres.")
                is_active = active(row["activo"])
                if model:
                    key = name.casefold()
                    if key in existing:
                        raise ValueError("El nombre ya existe o está repetido en el archivo.")
                    existing.add(key)
                    scope_values = (
                        {"filial_id": filial_id}
                        if kind == CatalogKind.BAYS
                        else {"holding_id": filial.holding_id}
                    )
                    planned.append(model(name=name, is_active=is_active, **scope_values))
                elif kind == CatalogKind.MODELS:
                    brand = by_brand.get(row["marca"].casefold())
                    if not brand or not brand.is_active:
                        raise ValueError(
                            "La marca no existe o está inactiva. Importa las marcas primero."
                        )
                    data = VehicleModelCreate(name=name, vehicle_type=row["tipo_vehiculo"])
                    key = (brand.id, name.casefold())
                    if key in model_keys:
                        raise ValueError("El modelo ya existe para esta marca o está repetido.")
                    model_keys.add(key)
                    planned.append(
                        VehicleModel(brand_id=brand.id, **data.model_dump(), is_active=is_active)
                    )
                else:
                    if row["categoria"] not in {value.value for value in TemparioCategory}:
                        raise ValueError(
                            "Categoría inválida. Usa uno de los códigos de categoría indicados en la guía de temparios."
                        )
                    category = TemparioCategory(row["categoria"])
                    sequence = number(row["numero"], integer=True, default=maxima[category] + 1)
                    if sequence < 1 or (category, sequence) in codes:
                        raise ValueError(
                            "El número debe ser positivo y no estar usado en esa categoría."
                        )
                    codes.add((category, sequence))
                    maxima[category] = max(maxima[category], sequence)
                    linked = []
                    for entry in filter(None, row["repuestos"].split("|")):
                        fields = entry.split(":")
                        code = fields[0]
                        quantity = fields[1] if len(fields) in {2, 3} else ""
                        part = by_part.get(code.strip().casefold()) if quantity else None
                        if not part:
                            raise ValueError(
                                f"Repuesto no encontrado en esta filial: {entry}. Usa CODIGO:CANTIDAD o CODIGO:CANTIDAD:COSTO_ESTIMADO."
                            )
                        if any(p["part_id"] == part.id for p in linked):
                            raise ValueError(f"Repuesto repetido: {code}.")
                        estimate = number(fields[2], default=0) if len(fields) == 3 else 0
                        if (
                            estimate > 99999999.99
                            or Decimal(str(estimate)).as_tuple().exponent < -2
                        ):
                            raise ValueError(
                                "El costo estimado admite hasta 99.999.999,99 y dos decimales."
                            )
                        linked.append(
                            {
                                "part_id": part.id,
                                "name": part.name,
                                "quantity": number(quantity, integer=True),
                                "unit_cost": estimate,
                            }
                        )
                    compatible = []
                    for entry in filter(None, row["compatibilidad"].split("|")):
                        brand_name, separator, model_name = entry.partition(":")
                        brand = by_brand.get(brand_name.strip().casefold())
                        match = next(
                            (
                                m
                                for m in models
                                if brand
                                and m.brand_id == brand.id
                                and m.name.casefold() == model_name.strip().casefold()
                                and m.is_active
                            ),
                            None,
                        )
                        if not separator or not brand or not brand.is_active or not match:
                            raise ValueError(
                                f"Compatibilidad no encontrada: {entry}. Usa Marca:Modelo."
                            )
                        compatible.append({"brand": brand.name, "model": match.name})
                    data = TemparioCreate(
                        filial_id=filial_id,
                        category=category,
                        sequence_number=sequence,
                        name=name,
                        estimated_hours=number(row["horas"], default=0),
                        warranty_mode=row["garantia"] or "standard",
                        warranty_days=number(row["dias"], integer=True),
                        warranty_km=number(row["kilometros"], integer=True),
                        year_from=number(row["ano_desde"], integer=True),
                        year_to=number(row["ano_hasta"], integer=True),
                        compatible_vehicles=compatible,
                        tools=[s.strip() for s in row["herramientas"].split("|") if s.strip()],
                        requires_parts=bool(linked),
                        parts=linked,
                    )
                    if (
                        data.estimated_hours > 9999.99
                        or Decimal(str(data.estimated_hours)).as_tuple().exponent < -2
                    ):
                        raise ValueError(
                            "Horas admite hasta 9.999,99 y dos decimales, como la base de datos."
                        )
                    if any(
                        year is not None and not 1900 <= year <= 2100
                        for year in (data.year_from, data.year_to)
                    ):
                        raise ValueError("Los años deben estar entre 1900 y 2100.")
                    from app.modules.post_ventas.service import PostVentasService

                    PostVentasService(self.db)._validate_single_warranty(data)
                    values = data.model_dump(exclude={"parts", "compatible_vehicles"})
                    planned.append(
                        Tempario(
                            **values,
                            compatible_vehicles=compatible,
                            is_active=is_active,
                            updated_by_user_id=actor_id,
                            parts=[TemparioPart(**p.model_dump()) for p in data.parts],
                        )
                    )
            except ValidationError as exc:
                errors.append(
                    {
                        "row": index,
                        "message": "Valores inválidos en: "
                        + ", ".join(
                            ".".join(str(value) for value in error["loc"]) for error in exc.errors()
                        )
                        + ". Revisa las columnas y el ejemplo de la plantilla.",
                    }
                )
            except (ValueError, TypeError) as exc:
                errors.append({"row": index, "message": str(exc)[:600]})
        report = {
            "total": len(rows),
            "valid": len(planned),
            "errors": errors,
            "review_hash": review_digest(kind, filial_id, actor_id, rows),
            "imported": 0,
        }
        if commit:
            if errors:
                raise BadRequestError(
                    "El archivo contiene errores; no se importó ninguna fila.", details=errors
                )
            for record in planned:
                self.db.add(record)
            try:
                await self.db.commit()
            except IntegrityError as exc:
                await self.db.rollback()
                raise ConflictError(
                    "El catálogo cambió durante la carga. Revisa el archivo nuevamente."
                ) from exc
            report["imported"] = len(planned)
        return report
