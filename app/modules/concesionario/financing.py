"""Transport-independent snapshots reported by an external financier; no cash postings."""
from datetime import date, datetime, UTC
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, func

from app.core.exceptions import BadRequestError, ConflictError
from app.core.venezuela_time import venezuela_today
from app.modules.concesionario.enums import SaleType
from app.modules.concesionario.models import VehicleSale, DealershipVehicle

Money = Decimal

class FinancingBase(BaseModel):
    model_config=ConfigDict(extra="forbid",str_strip_whitespace=True)

class FinancingInstallment(FinancingBase):
    external_id: str = Field(min_length=1,max_length=150)
    due_date: date
    amount: Money = Field(gt=0,max_digits=14,decimal_places=2)
    paid_amount: Money = Field(default=Decimal('0'),ge=0,max_digits=14,decimal_places=2)

    @model_validator(mode='after')
    def paid_limit(self):
        if self.paid_amount>self.amount:
            raise ValueError('El abono de la cuota no puede superar su importe.')
        return self

class FinancingPayment(FinancingBase):
    external_id: str = Field(min_length=1,max_length=150)
    paid_at: AwareDatetime
    amount: Money = Field(gt=0,max_digits=14,decimal_places=2)
    reference: str | None = Field(None,max_length=250)

class FinancingSnapshot(FinancingBase):
    provider: str = Field(min_length=1,max_length=100)
    external_id: str = Field(min_length=1,max_length=150)
    reported_at: AwareDatetime
    source: Literal['webhook','api','import','manual'] = 'api'
    external_event_id: str | None = Field(None,max_length=150)
    currency: Literal['USD','VES']
    total_amount: Money = Field(gt=0,max_digits=14,decimal_places=2)
    amount_paid: Money = Field(ge=0,max_digits=14,decimal_places=2)
    complete_schedule: bool = False
    installments: list[FinancingInstallment] = Field(default_factory=list,max_length=1000)
    payments: list[FinancingPayment] = Field(default_factory=list,max_length=1000)

    @model_validator(mode='after')
    def consistent(self):
        if not self.provider.strip() or not self.external_id.strip():
            raise ValueError('La financiera y la referencia del contrato son obligatorias.')
        if self.amount_paid>self.total_amount:
            raise ValueError('El monto pagado no puede superar el contrato.')
        for rows in (self.installments,self.payments):
            if len({r.external_id for r in rows})!=len(rows):
                raise ValueError('Las referencias externas deben ser únicas dentro de la actualización.')
        scheduled=sum((i.amount for i in self.installments),Decimal('0'))
        if scheduled>self.total_amount or (self.complete_schedule and scheduled!=self.total_amount):
            raise ValueError('Las cuotas deben coincidir con el importe del contrato completo.')
        if sum((i.paid_amount for i in self.installments),Decimal('0'))>self.amount_paid or sum((p.amount for p in self.payments),Decimal('0'))>self.amount_paid:
            raise ValueError('Los pagos detallados no pueden superar el total pagado reportado.')
        return self


def financing_read(sale,vehicle):
    snapshot=FinancingSnapshot.model_validate(sale.financing_snapshot) if sale.financing_snapshot else None
    balance=snapshot.total_amount-snapshot.amount_paid if snapshot else None
    overdue=any(i.due_date<venezuela_today() and i.paid_amount<i.amount for i in snapshot.installments) if snapshot else False
    return dict(sale_id=sale.id,sale_code=sale.code,client_name=sale.client_name,client_document=sale.client_document,
        vehicle_id=vehicle.id,vehicle_label=f'{vehicle.brand} {vehicle.model} {vehicle.year}',vin=vehicle.vin,
        provider=snapshot.provider if snapshot else vehicle.financing_provider,
        external_id=snapshot.external_id if snapshot else vehicle.financing_external_id,
        sale_price=float(sale.final_price),sale_currency=vehicle.price_currency,
        status='sin_datos' if not snapshot else 'liquidado' if balance==0 else 'vencido' if overdue else 'en_curso',
        balance=float(balance) if balance is not None else None,
        snapshot=snapshot.model_dump(mode='json') if snapshot else None,
        updated_at=sale.financing_updated_at)

class FinancingService:
    def __init__(self,db):self.db=db

    async def list(self,filial_id):
        rows=(await self.db.execute(select(VehicleSale,DealershipVehicle).join(DealershipVehicle,DealershipVehicle.id==VehicleSale.vehicle_id).where(VehicleSale.filial_id==filial_id,VehicleSale.sale_type==SaleType.FINANCIADO).order_by(VehicleSale.created_at.desc()))).all()
        return [financing_read(sale,vehicle) for sale,vehicle in rows]

    async def sync(self,sale_id,payload):
        # Serialize external-reference assignment within a filial as well as each sale.
        from app.modules.filiales.models import Filial
        filial_id=(await self.db.execute(select(VehicleSale.filial_id).where(VehicleSale.id==sale_id))).scalar_one()
        await self.db.execute(select(Filial.id).where(Filial.id==filial_id).with_for_update())
        sale=(await self.db.execute(select(VehicleSale).where(VehicleSale.id==sale_id).with_for_update().execution_options(populate_existing=True))).scalar_one()
        if sale.sale_type!=SaleType.FINANCIADO:
            raise BadRequestError('Solo las ventas financiadas pueden recibir datos de crédito.')
        value=payload.model_dump(mode='json')
        if sale.financing_snapshot:
            previous=FinancingSnapshot.model_validate(sale.financing_snapshot)
            if payload.reported_at<previous.reported_at:
                raise ConflictError('La actualización es anterior a los datos registrados.',error_code='stale_financing_snapshot')
            if payload.reported_at==previous.reported_at:
                if value!=sale.financing_snapshot:
                    raise ConflictError('Ya existe otra actualización con esa fecha.',error_code='conflicting_financing_snapshot')
                return financing_read(sale,await self.db.get(DealershipVehicle,sale.vehicle_id))
        duplicate=(await self.db.execute(select(VehicleSale.id).where(VehicleSale.filial_id==sale.filial_id,VehicleSale.id!=sale.id,func.lower(VehicleSale.financing_snapshot['provider'].as_string())==payload.provider.lower(),VehicleSale.financing_snapshot['external_id'].as_string()==payload.external_id).limit(1))).scalar_one_or_none()
        if duplicate:
            raise ConflictError('Este contrato ya está vinculado a otra venta.')
        sale.financing_snapshot=value
        sale.financing_updated_at=datetime.now(UTC)
        await self.db.commit()
        return financing_read(sale,await self.db.get(DealershipVehicle,sale.vehicle_id))
