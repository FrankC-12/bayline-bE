"""Optional part images persist, remain scoped, and validate uploads before storage."""
import base64
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db
from app.core.exception_handlers import register_exception_handlers
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.filiales.models import Filial
from app.modules.parts.models import Part, PartCategory
from app.modules.parts.router import router
from app.modules.roles.enums import AccessLevel, RoleScope
from app.modules.roles.models import Role, RoleModulePermission
from app.modules.users.models import User

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')

@pytest.mark.asyncio
async def test_optional_part_image_http_upload_replace_delete_and_access(billing_db, tmp_path, monkeypatch):
    ctx = billing_db
    settings = SimpleNamespace(uploads_dir=str(tmp_path), api_v1_prefix='/api/v1', s3_bucket_name=None)
    monkeypatch.setattr('app.modules.parts.router.get_settings', lambda: settings)
    monkeypatch.setattr('app.core.storage.get_settings', lambda: settings)
    async with ctx.sessions() as db:
        filial = await db.get(Filial, ctx.filial_id)
        category = PartCategory(holding_id=filial.holding_id, name='Imagen QA')
        role = Role(name='Catalog reader', slug=f'qa-{uuid.uuid4().hex[:8]}', scope=RoleScope.FILIAL)
        db.add_all([category, role]); await db.flush()
        db.add(RoleModulePermission(role_id=role.id, module_id='repuestos', access=AccessLevel.VER))
        user = User(full_name='QA', email=f'{uuid.uuid4()}@example.com', role_id=role.id, filial_id=ctx.filial_id)
        part = Part(filial_id=ctx.filial_id, category_id=category.id, code='IMG', name='Filtro', price=0)
        db.add_all([user, part]); await db.commit()
        current = CurrentUser(user_id=user.id, email=user.email, role_id=role.id, role_slug='filial-admin', scope='filial', filial_id=ctx.filial_id, holding_id=None)
        app = FastAPI(); register_exception_handlers(app); app.include_router(router, prefix='/api/v1')
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: current
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as http:
            url=f'/api/v1/parts/{part.id}/image'
            listed=await http.get('/api/v1/parts', params={'filial_id':str(ctx.filial_id)})
            assert listed.status_code == 200 and listed.json()[0]['image_url'] is None
            for content, mime in [(b'<svg/>','image/svg+xml'),(b'fake png','image/png'),(b'\x89PNG\r\n\x1a\n'+b'x'*(5*1024*1024),'image/png')]:
                rejected=await http.post(url, files={'image':('file.png',content,mime)})
                assert rejected.status_code == 400
            assert not list(tmp_path.rglob('*'))
            uploaded=await http.post(url, files={'image':('photo.png',PNG,'image/png')})
            assert uploaded.status_code == 200, uploaded.text
            first=uploaded.json()['image_url']; assert first.startswith('/api/v1/uploads/parts/')
            assert (tmp_path/'parts'/first.rsplit('/',1)[1]).read_bytes() == PNG
            replaced=await http.post(url, files={'image':('photo.png',PNG,'image/png')})
            assert replaced.status_code == 200 and replaced.json()['image_url'] != first
            listed=await http.get('/api/v1/parts', params={'filial_id':str(ctx.filial_id)})
            assert listed.json()[0]['image_url'] == replaced.json()['image_url']
            current.role_slug=role.slug
            assert (await http.post(url, files={'image':('photo.png',PNG,'image/png')})).status_code == 403
            assert (await http.delete(url)).status_code == 403
            current.role_slug='filial-admin'; current.filial_id=uuid.uuid4()
            assert (await http.delete(url)).status_code == 403
            current.filial_id=ctx.filial_id
            removed=await http.delete(url)
            assert removed.status_code == 200 and removed.json()['image_url'] is None
            assert (await http.delete(url)).json()['image_url'] is None
