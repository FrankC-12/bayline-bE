# HTTPS y sesión por cookies

El código queda preparado sin adquirir certificados de antemano. La activación pública requiere un dominio/subdominio propio, DNS A/AAAA apuntando al servidor y puertos 80/443 accesibles. Caddy emite y renueva el certificado automáticamente y redirige HTTP a HTTPS. No desplegar públicamente el modo development.

## Desarrollo sin certificados

Backend: `APP_ENV=development`, orígenes exactos en `ALLOWED_ORIGINS`, por defecto localhost:3000 y 127.0.0.1:3000. Ejecutar Uvicorn en 127.0.0.1:8000 y Next en localhost:3000. Next redirige internamente `/api/v1` al backend; `API_INTERNAL_URL` es una variable del servidor. Ya no se utiliza `NEXT_PUBLIC_API_URL`.

Las cookies son HttpOnly y SameSite=Lax también en desarrollo. Solo en development/test omiten Secure para permitir HTTP local. En production/staging Secure es obligatorio por código, los orígenes deben ser HTTPS y DEBUG debe ser false. La API rechaza HTTP en esos entornos, salvo el healthcheck sin datos de usuario.

## Preparar producción

Mantener `bayline-backend` y `bayline-frontend` como carpetas hermanas. Desde bayline-backend:

```sh
cp deploy/.env.production.example deploy/.env.production
chmod 600 deploy/.env.production
```

Editar dominio, correo ACME y credenciales reales. Generar un SECRET_KEY nuevo (por ejemplo `openssl rand -hex 32`); cambiarlo invalida los JWT anteriores que pudieron circular por HTTP/localStorage. No reutilizar las credenciales de ejemplo. El archivo `.env.production` no se versiona ni se copia a las imágenes.

La composición suministrada crea una base de datos nueva en un volumen nuevo. Para una instalación existente, configurar DATABASE_URL hacia la base correspondiente y conservar/migrar sus datos; no asumir que esta composición reutiliza automáticamente el volumen de desarrollo. Hacer respaldo antes de aplicar migraciones.

```sh
docker compose --env-file deploy/.env.production -f deploy/compose.production.yml build
docker compose --env-file deploy/.env.production -f deploy/compose.production.yml up -d postgres
docker compose --env-file deploy/.env.production -f deploy/compose.production.yml run --rm backend alembic upgrade head
docker compose --env-file deploy/.env.production -f deploy/compose.production.yml up -d
```

Solo Caddy publica puertos. API, Next y PostgreSQL quedan dentro de Docker. Uvicorn confía en cabeceras de proxy únicamente desde la IP fija de Caddy (172.30.40.10). Si se cambia la subred por conflicto con la infraestructura existente, actualizar también FORWARDED_ALLOW_IPS. Mantener los volúmenes caddy_data/caddy_config para conservar certificados y renovaciones. La configuración no publica la documentación interna del backend.

## Sesión y solicitudes

Login/refresh ya no devuelven JWT en JSON. El backend establece cookies host-only, HttpOnly, SameSite=Lax y Secure en producción. El access token tiene Path=/api/v1; el refresh, Path=/api/v1/auth. `/auth/me` entrega solo los datos del usuario; logout borra ambas cookies desde el servidor. El frontend elimina las claves antiguas de localStorage sin leer sus valores; los usuarios deben iniciar sesión de nuevo tras desplegar.

Todas las mutaciones requieren un Origin incluido exactamente en ALLOWED_ORIGINS y `X-CSRF-Protection: 1`. Esto incluye login, refresh y logout. El cliente web añade la cabecera; herramientas externas también deben enviarla junto con su almacén de cookies. No se admite autenticación Bearer en los endpoints de la aplicación. Las respuestas de API no se almacenan en caché.

Logout elimina las cookies del navegador. Los JWT siguen siendo stateless: esta entrega no agrega una lista de revocación por dispositivo. HttpOnly limita la extracción de tokens por JavaScript; no reemplaza la prevención de XSS.

## Verificación antes de habilitar usuarios

- `curl -I http://TU_DOMINIO` redirige a HTTPS; `curl -I https://TU_DOMINIO` valida el certificado sin `-k` y devuelve HSTS.
- Login devuelve únicamente `authenticated`; Set-Cookie muestra HttpOnly, Secure y SameSite=Lax. No hay JWT en localStorage ni en respuestas JSON.
- Recargar mantiene la sesión; al expirar el access token, refresh la renueva; logout cierra la sesión y `/auth/me` devuelve 401.
- Una petición POST con Origin ajeno o sin X-CSRF-Protection devuelve 403.
- 3000, 8000 y 5432 no están expuestos al exterior.

No se ha emitido un certificado ni se ha modificado DNS como parte de estos cambios: eso requiere el dominio y el servidor definitivos.

Referencia: [HTTPS automático de Caddy](https://caddyserver.com/docs/automatic-https).
