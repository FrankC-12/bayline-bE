# Pruebas de backend

## Pruebas rápidas

```sh
DEBUG=false PYTHONPATH=. venv/bin/python -m pytest -q
```

Sin `TEST_DATABASE_URL`, las pruebas de integración se omiten explícitamente.
Las pruebas con SQLite siguen verificando reglas de negocio; no comprueban
bloqueos, enums ni restricciones propias de PostgreSQL.

## PostgreSQL real

Con Docker iniciado y las dependencias de `requirements.txt` y
`requirements-dev.txt` instaladas:

```sh
venv/bin/python scripts/test_postgres.py
```

El comando crea un proyecto Docker con nombre único, una base `bayline_test`,
un puerto local libre y almacenamiento temporal. Aplica todas las migraciones,
verifica el rollback y la reaplicación de la última migración,
ejecuta `tests/integration` con sesiones asíncronas reales y elimina los recursos
al terminar. No utiliza las credenciales ni la base de datos del `.env`.
Para seleccionar un caso, agrega opciones de pytest, por ejemplo `-k simultaneous`.

Los casos cubren USD/Bs/mixto, emisión repetida, emisión simultánea con la misma
solicitud y con solicitudes diferentes, abonos concurrentes, rechazo de sobrepagos
y rollback después de insertar los registros. Cada operación concurrente usa
una sesión y conexión independientes. Las pruebas también comprueban que repetir
un abono con su `request_id` devuelve el recibo original sin crear otro ingreso,
y que reutilizar ese identificador con otros datos produce conflicto.

En CI, `.github/workflows/backend-tests.yml` inicia PostgreSQL 16, aplica las
migraciones desde cero y ejecuta ambas suites. Las migraciones se confirman por
revisión para que PostgreSQL permita usar en la siguiente revisión los valores
de enums recién agregados.

## Reintentos de abonos

`POST /receivables/{invoice_id}/collect` acepta `request_id` (UUID).
El frontend conserva el identificador mientras se reintenta el mismo formulario.
La respuesta se guarda en la misma transacción que el ingreso, y se devuelve
cuando se repite la solicitud. Una solicitud con otros importes o cuenta debe
usar un identificador nuevo. Los clientes anteriores que omitan `request_id`
conservan su comportamiento; no obtienen esta protección de reintentos.

Antes de desplegar este cambio, ejecutar `alembic upgrade head` para crear
`service_order_collection_requests`. La verificación local usa solo bases
desechables y no aplica migraciones al entorno de la aplicación.
