# Páginas de Clientes y ODS

Nuevos endpoints autenticados:

- `GET /api/v1/clients/page?filial_id=...`
- `GET /api/v1/service-orders/page?filial_id=...`

Ambos aceptan `page` (desde 1), `page_size` (30 por defecto, máximo 100) y `search` (hasta 150 caracteres). Devuelven `items`, `total`, `page`, `page_size` y `total_pages`. Una página mayor que la última se ajusta a la última disponible. Sin resultados: página 1, una página total y lista vacía. La paginación usa offset; inserciones simultáneas pueden desplazar filas entre consultas.

## Clientes

Busca por nombre sin distinción de mayúsculas o acentos, documento, placa y VIN. Los caracteres `%` y `_` son literales. El nombre normalizado se almacena en `clients.normalized_name`; se actualiza al insertar/modificar un cliente mediante el ORM. La migración completa los nombres existentes en lotes de 500. Si alguien modifica nombres directamente por SQL, también debe mantener este campo.

Cada página conserva vehículos, kilometraje vigente y datos de mantenimiento usados por las tarjetas. El orden utiliza `full_name, id`. La pantalla de Clientes consulta únicamente la página actual; después de crear o editar, recarga esa página para reflejar el orden y los filtros del servidor.

## ODS

Filtros adicionales: `view=active|history|all`, `status`, `date_from` y `date_to`. Las fechas filtran la creación de la ODS por días completos de Caracas; la fecha inicial no puede ser posterior a la final. `status` se intersecta con `view`.

Busca por código ODS, cliente (sin acentos), placa, marca/modelo y técnico. Devuelve además `client_name`, `vehicle_label`, `vehicle_plate` y `technician_name`, para evitar descargar directorios completos y resolver cada etiqueta desde el frontend.

El permiso se verifica antes de consultar. Un técnico sin permiso general de asesor solo recibe sus órdenes asignadas; el total y las páginas se calculan después de aplicar esa restricción. El orden utiliza `created_at DESC, id DESC`.

## Frontend y compatibilidad

Clientes y el historial de ODS permiten 15/30/60 filas por página. Cambiar búsqueda, filtros, filial, usuario o tamaño vuelve a la primera página. La búsqueda espera 250 ms antes de consultar y las respuestas antiguas se descartan mediante el cargador compartido.

Los endpoints anteriores de listas conservan su contrato para selectores y móvil. El tablero de ODS y el calendario conservan su carga actual, evitando ocultar órdenes por paginar una sola columna o un día sin adaptar esas vistas. Esta entrega cambia la pantalla de Clientes y el historial de ODS.

## Publicación

Actualizar el backend y ejecutar `alembic upgrade head` (revisión `a4c93e682f17`) antes de publicar el frontend. Añade el nombre normalizado y los índices de ordenación de Clientes y ODS. No requiere seed.
