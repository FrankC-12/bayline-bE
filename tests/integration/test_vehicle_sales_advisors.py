import uuid
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from app.core.database import get_db
from app.core.exception_handlers import register_exception_handlers
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.concesionario.router import router
from app.modules.concesionario.service import ConcesionarioService
from app.modules.concesionario.schemas import VehicleSaleInput, VehicleUpdate
from app.modules.concesionario.models import DealershipVehicle
from app.modules.concesionario.enums import VehicleStatus, VehicleCondition
from app.modules.filiales.models import Filial
from app.modules.roles.models import Role, RoleModulePermission
from app.modules.roles.enums import RoleScope, AccessLevel
from app.modules.users.models import User, UserModulePermission
from app.modules.users.enums import UserStatus

@pytest.mark.asyncio
async def test_vehicle_advisors_are_active_salespeople_of_current_filial(billing_db):
    ctx=billing_db
    async with ctx.sessions() as db:
        filial=await db.get(Filial,ctx.filial_id)
        role=(await db.execute(select(Role).where(Role.slug=='vendedor'))).scalar_one_or_none()
        if role is None:
            role=Role(name='Vendedor',slug='vendedor',scope=RoleScope.FILIAL);db.add(role);await db.flush()
            db.add(RoleModulePermission(role_id=role.id,module_id='concesionario',access=AccessLevel.EDITAR))
        other_role=Role(name='Técnico',slug='qa-'+uuid.uuid4().hex,scope=RoleScope.FILIAL);db.add(other_role);await db.flush()
        db.add(RoleModulePermission(role_id=other_role.id,module_id='concesionario',access=AccessLevel.EDITAR))
        foreign=Filial(name='Otra filial',slug=uuid.uuid4().hex,holding_id=filial.holding_id);db.add(foreign);await db.flush()
        people=[]
        for name,status,role_id,filial_id in [('Asesor activo',UserStatus.ACTIVO,role.id,ctx.filial_id),('Asesor inactivo',UserStatus.INACTIVO,role.id,ctx.filial_id),('Invitado',UserStatus.INVITADO,role.id,ctx.filial_id),('Técnico con acceso',UserStatus.ACTIVO,other_role.id,ctx.filial_id),('Otra sucursal',UserStatus.ACTIVO,role.id,foreign.id),('Asesor solo lectura',UserStatus.ACTIVO,role.id,ctx.filial_id)]:
            person=User(full_name=name,email=f'{uuid.uuid4()}@example.com',role_id=role_id,filial_id=filial_id,status=status);db.add(person);people.append(person)
        await db.flush();db.add(UserModulePermission(user_id=people[-1].id,module_id='concesionario',access=AccessLevel.VER))
        vehicle=DealershipVehicle(filial_id=ctx.filial_id,status=VehicleStatus.DISPONIBLE,condition=VehicleCondition.NUEVO,brand='Toyota',model='Corolla',year=2026,vin=uuid.uuid4().hex[:17],sku=uuid.uuid4().hex,price_cash=10000,price_financed=12000)
        db.add(vehicle);await db.commit()
        current=CurrentUser(user_id=people[0].id,email=people[0].email,role_id=role.id,role_slug='filial-admin',scope='filial',filial_id=ctx.filial_id,holding_id=None)
        app=FastAPI();register_exception_handlers(app);app.include_router(router,prefix='/api/v1');app.dependency_overrides[get_db]=lambda:db;app.dependency_overrides[get_current_user]=lambda:current
        active_id=people[0].id; invalid_ids=[person.id for person in people[1:]]; vehicle_id=vehicle.id
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as http:
            response=await http.get('/api/v1/vehicle-sales/advisors',params={'filial_id':str(ctx.filial_id)})
            assert response.status_code==200,response.text
            assert [r['id'] for r in response.json()]==[str(active_id)]
            assert set(response.json()[0])=={'id','full_name','role_id'}
            assert (await http.get('/api/v1/vehicle-sales/advisors',params={'filial_id':str(foreign.id)})).status_code==403
            for invalid_id in invalid_ids:
                result=await http.patch(f'/api/v1/dealership-vehicles/{vehicle_id}',json={'status':'vendido','sale':{'client_name':'Cliente QA','sale_type':'financiado','advisor_user_id':str(invalid_id)}})
                assert result.status_code==400,result.text
                assert result.json()['errorCode']=='invalid_vehicle_sales_advisor'
                await db.rollback()
            await ConcesionarioService(db)._validate_sale_advisor(ctx.filial_id,active_id)
