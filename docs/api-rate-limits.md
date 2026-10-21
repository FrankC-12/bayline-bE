# Límites de solicitudes de la API

La API limita las solicitudes antes de leer el cuerpo o ejecutar el endpoint. Los contadores son atómicos y compartidos en PostgreSQL: agregar workers, reiniciar la app o renovar el token no reinicia el cupo del usuario.

## Valores iniciales

| Variable | Solicitudes por minuto | Alcance |
| --- | ---: | --- |
| `RATE_LIMIT_IP_PER_MINUTE` | 1200 | Total por IP, incluyendo usuarios autenticados |
| `RATE_LIMIT_USER_PER_MINUTE` | 300 | Total por usuario con JWT válido, entre todas sus sesiones e IP |
| `RATE_LIMIT_ANONYMOUS_PER_MINUTE` | 120 | Total por IP sin JWT válido |
| `RATE_LIMIT_READ_PER_MINUTE` | 120 | Lecturas por identidad, método y recurso principal de la API |
| `RATE_LIMIT_WRITE_PER_MINUTE` | 60 | Escrituras totales por identidad; también por método/recurso |
| `RATE_LIMIT_EXPENSIVE_PER_MINUTE` | 20 | Total de multipart, cargas de fotos/anexos, exportaciones y operaciones bulk |
| `RATE_LIMIT_AUTH_PER_MINUTE` | 30 | Solicitudes `/auth/*` por IP, incluso con token |

Todos los límites aplicables se acumulan. Cambiar un ID, los filtros o un endpoint anidado del mismo recurso no evita el cupo: por ejemplo, las consultas de `/service-orders/{id}` y `/service-orders/{id}/summary` comparten el de lectura. Los tokens inválidos/expirados usan el presupuesto anónimo; la autenticación y los permisos originales siguen siendo obligatorios. El login mantiene además sus límites existentes por correo y por IP de 15 minutos.

Las ventanas son minutos fijos UTC. En el cambio de minuto puede haber una ráfaga de hasta dos cupos consecutivos; no es una ventana móvil. Los servidores deben mantener sus relojes sincronizados. Las filas vencidas se limpian en lotes de hasta 1000 una vez por minuto por instancia, sin bloquear filas ocupadas. Las claves se guardan como hashes, sin JWT ni IP en texto plano.

Cada instancia conserva hasta 4096 bloqueos confirmados en PostgreSQL para rechazar reintentos sin otra consulta. Esta caché solo deniega: nunca autoriza solicitudes ni guarda cupos disponibles. Los bloqueos dejan de aplicar al cambiar de minuto o de límite configurado; si una entrada se expulsa por falta de espacio, se vuelve a consultar el contador compartido. Los reintentos rechazados desde caché no incrementan contadores en PostgreSQL.

## Respuestas y clientes

- `429`, `errorCode=rate_limited`, cuerpo `retryAfter` y cabecera `Retry-After`: esperar los segundos indicados antes de reintentar. Web puede leer la cabecera mediante CORS. Evitar bucles de reintentos inmediatos en web/mobile.
- `503`, `errorCode=rate_limit_unavailable`, `Retry-After: 5`: el almacén de contadores falló o no respondió en tres segundos; el endpoint no se ejecuta y la protección no se desactiva silenciosamente.
- `OPTIONS`, `GET/HEAD /api/v1/health` y rutas fuera del prefijo API no consumen estos presupuestos. Los errores de autenticación, validación y rutas desconocidas dentro de la API sí consumen cupo.

Los valores iniciales permiten el polling habitual, pero deben ajustarse observando el tráfico real, especialmente clientes con varias pestañas o muchos usuarios detrás de una IP. `RATE_LIMIT_ENABLED=false` permite una desactivación explícita para diagnóstico; por defecto está habilitado.

## Despliegue

1. Ejecutar `alembic upgrade head` antes de iniciar la nueva versión. La revisión `e6f931ca2d54` crea `api_rate_buckets`; no modifica datos comerciales.
2. Desplegar el backend. Los ejemplos de entorno y Compose incluyen las variables anteriores. No requiere Redis ni nuevas dependencias.
3. Configurar `FORWARDED_ALLOW_IPS` exclusivamente con las IP de los proxies confiables. La aplicación usa `request.client.host` resuelto por Uvicorn y no confía directamente en `X-Forwarded-For`. El Compose existente confía únicamente en Caddy y no publica el puerto del backend.

Este control reduce el abuso de la API; la protección frente a saturación de red debe aplicarse también en el proxy o proveedor de infraestructura. Referencia: [OWASP API4: consumo de recursos sin restricciones](https://owasp.org/API-Security/editions/2023/en/0xa4-unrestricted-resource-consumption/).
