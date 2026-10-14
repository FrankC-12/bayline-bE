# Historial paginado de movimientos

`GET /api/v1/almacen/movements/page` requiere acceso de lectura a Almacén en la filial solicitada.

Parámetros:

- `filial_id`: obligatorio.
- `page`: desde 1; si supera la última página, devuelve la última disponible.
- `page_size`: por defecto 50; desde 1 hasta 200.
- `warehouse_id`, `part_id`: filtros opcionales.
- `movement_type`: `entrada`, `salida`, `devolucion`, `transferencia_entrada`, `transferencia_salida` o `transferencia`. El último incluye ambos sentidos del traslado.
- `search`: hasta 120 caracteres; busca por código/nombre del repuesto, responsable o referencia. `%` y `_` se buscan como caracteres literales.
- `date_from`, `date_to`: fechas `AAAA-MM-DD`, inclusivas según el calendario de Caracas. Una fecha inicial posterior a la final devuelve 400.

La respuesta incluye `items`, `total`, `page`, `page_size` y `total_pages`. Los elementos ya contienen nombres de repuesto, almacén y responsable. El total corresponde a los filtros aplicados, dentro de la filial autorizada. Si no hay resultados, devuelve cero elementos, página 1 y una página total.

El orden usa `created_at DESC, id DESC`, para desempatar registros con la misma fecha. Es paginación por offset: nuevas inserciones entre consultas pueden desplazar filas entre páginas.

El frontend usa 50 filas por defecto, permite elegir 20/50/100 y vuelve a la primera página al cambiar filtros, tamaño o almacén. La búsqueda espera 250 ms antes de consultar. La actualización automática consulta solo la página actual; no descarga los catálogos completos de repuestos y usuarios para completar las etiquetas. Los fallos de carga muestran un estado de error con reintento.

El endpoint anterior `/almacen/movements` conserva su respuesta de lista para mantener compatibilidad con otros consumidores. Esta entrega pagina el historial de movimientos; otras listas conservan sus contratos actuales.

## Publicación

Ejecutar `alembic upgrade head` con el backend actualizado (revisión `f3b7821a9d65`) antes de publicar el frontend. La migración añade índices de orden cronológico por filial y por almacén. No requiere seed.
