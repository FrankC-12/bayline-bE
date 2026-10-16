import uuid
from datetime import UTC, datetime, timedelta
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import select, func
from app.core.database import get_db
from app.core.exception_handlers import register_exception_handlers
from app.core.venezuela_time import venezuela_today
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.concesionario.models import DealershipVehicle, VehicleSale
from app.modules.concesionario.enums import SaleType, VehicleCondition, VehicleStatus
from app.modules.concesionario.router import router
from app.modules.roles.enums import RoleScope, AccessLevel
from app.modules.roles.models import Role, RoleModulePermission
from app.modules.users.models import User
from app.modules.users.enums import UserStatus
from app.modules.administracion.models import IncomeEntry

@pytest.mark.asyncio
async def test_external_financing_snapshot_is_scoped_idempotent_and_does_not_post_income(billing_db):
    ctx=billing_db
    async with ctx.sessions() as db:
        role=Role(name='Finance reader',slug='qa-'+uuid.uuid4().hex,scope=RoleScope.FILIAL);db.add(role);await db.flush()
        db.add(RoleModulePermission(role_id=role.id,module_id='concesionario',access=AccessLevel.VER))
        user=User(full_name='QA',email=f'{uuid.uuid4()}@example.com',role_id=role.id,filial_id=ctx.filial_id,status=UserStatus.ACTIVO);db.add(user)
        vehicle=DealershipVehicle(filial_id=ctx.filial_id,status=VehicleStatus.VENDIDO,condition=VehicleCondition.NUEVO,brand='Toyota',model='Hilux',year=2026,vin=uuid.uuid4().hex[:17],sku=uuid.uuid4().hex,price_cash=100,price_financed=120,financing_provider='Por definir');db.add(vehicle);await db.flush()
        sales=[VehicleSale(filial_id=ctx.filial_id,sequence_number=i+1,vehicle_id=vehicle.id,client_name='Cliente QA',sale_type=kind,final_price=120) for i,kind in enumerate([SaleType.FINANCIADO,SaleType.CONTADO])];db.add_all(sales);await db.commit()
        sale_id=sales[0].id; cash_id=sales[1].id; reader_slug=role.slug
        current=CurrentUser(user_id=user.id,email=user.email,role_id=role.id,role_slug='filial-admin',scope='filial',filial_id=ctx.filial_id,holding_id=None)
        app=FastAPI();register_exception_handlers(app);app.include_router(router,prefix='/api/v1');app.dependency_overrides[get_db]=lambda:db;app.dependency_overrides[get_current_user]=lambda:current
        url=f'/api/v1/vehicle-sales/{sale_id}/financing'
        report=datetime.now(UTC)-timedelta(minutes=1)
        payload=dict(provider='Financiera QA',external_id='C-001',reported_at=report.isoformat(),source='api',external_event_id='event-1',currency='USD',total_amount='100.00',amount_paid='20.00',complete_schedule=True,installments=[dict(external_id='1',due_date=(venezuela_today()-timedelta(days=1)).isoformat(),amount='50.00',paid_amount='20.00'),dict(external_id='2',due_date=(venezuela_today()+timedelta(days=30)).isoformat(),amount='50.00',paid_amount='0.00')],payments=[dict(external_id='P-1',paid_at=report.isoformat(),amount='20.00',reference='ABONO-1')])
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as http:
            rows=(await http.get('/api/v1/vehicle-financings',params={'filial_id':str(ctx.filial_id)})).json()
            assert len(rows)==1 and rows[0]['status']=='sin_datos' and rows[0]['balance'] is None
            first=await http.put(url,json=payload); assert first.status_code==200,first.text
            data=first.json(); assert data['balance']==80 and data['status']=='vencido' and len(data['snapshot']['payments'])==1
            again=await http.put(url,json=payload); assert again.status_code==200 and again.json()['updated_at']==data['updated_at']
            stale={**payload,'reported_at':(report-timedelta(days=1)).isoformat()}
            assert (await http.put(url,json=stale)).status_code==409;await db.rollback()
            assert (await http.put(url,json={**payload,'amount_paid':'200.00'})).status_code==422
            assert (await http.put(url,json={**payload,'installments':payload['installments']*2})).status_code==422
            assert (await http.put(f'/api/v1/vehicle-sales/{cash_id}/financing',json=payload)).status_code==400;await db.rollback()
            current.filial_id=uuid.uuid4();assert (await http.put(url,json=payload)).status_code==403;await db.rollback()
            current.filial_id=ctx.filial_id;current.role_slug=reader_slug
            assert (await http.get('/api/v1/vehicle-financings',params={'filial_id':str(ctx.filial_id)})).status_code==200
            assert (await http.put(url,json=payload)).status_code==403;await db.rollback()
            current.role_slug='filial-admin'
            paid={**payload,'reported_at':(report+timedelta(seconds=1)).isoformat(),'amount_paid':'100.00','source':'webhook','installments':[{**i,'paid_amount':i['amount']} for i in payload['installments']],'payments':payload['payments']+[dict(external_id='P-2',paid_at=report.isoformat(),amount='80.00',reference='ABONO-2')]}
            result=await http.put(url,json=paid); assert result.status_code==200,result.text
            assert result.json()['balance']==0 and result.json()['status']=='liquidado'
            assert (await db.execute(select(func.count()).select_from(IncomeEntry).where(IncomeEntry.filial_id==ctx.filial_id))).scalar()==0
