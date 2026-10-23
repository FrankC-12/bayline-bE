# Precios, evidencias y carga inicial de catálogos

## Precios coherentes

El catálogo de temparios y la cotización para una ODS utilizan las capas FIFO disponibles, descontando reservas. El costo manual deja de sustituir al costo real de compras. Sin historial de compras se conserva el costo estimado del catálogo y se indica que es una estimación; con existencia insuficiente se informa el uso del último costo conocido para el faltante.

El cálculo agrupa cantidades del mismo repuesto, aplica el margen/descuento de repuestos y redondea a centavos. La mano de obra se redondea por tarea antes de sumarse; el IVA se redondea sobre la base total. Las tareas canceladas no generan mano de obra ni comisión. Las facturas ya congeladas conservan su resumen.

El catálogo utiliza ajustes actuales; la cotización de una ODS respeta sus ajustes guardados y el descuento seleccionado. Por eso una orden antigua puede diferir legítimamente del catálogo actual. El portal muestra la cotización específica antes de agregar la tarea.

Endpoints bajo `/api/v1`:

- `POST /temparios/quote`: vista previa sin guardar, con `filial_id`, `estimated_hours` y `parts`.
- `GET /service-orders/{order_id}/temparios/{tempario_id}/quote`: precio contextual de la ODS.
- Agregar tarea acepta `expected_total` opcional. Una diferencia devuelve 409 `task_quote_changed` y revierte la operación completa.
- Cotizar venta de repuestos devuelve `quote_hash`; crear venta acepta este valor opcional. Si cambia la cotización, devuelve 409 `sale_quote_changed` antes de afectar inventario. El portal obliga a revisar el nuevo precio.

Los campos opcionales conservan compatibilidad con clientes móviles anteriores. No sustituyen controles de acceso. Las cifras visibles del portal usan formato venezolano, por ejemplo `12.700,00`.

## Evidencias de inspección

La consulta de registros de la ODS muestra inspección preliminar y minuciosa, fotografías, daños, anexos, lecturas de fluidos y datos anidados. Se conservan valores cero. El portal actualiza los registros periódicamente y al abrirlos.

La inspección preliminar admite `fluids`; los datos de minuciosa admiten `fluids` y `photo_urls`. Ejemplo de fluidos:

```json
{"aceite_motor":{"level":0,"condition":"bajo","notes":"Revisar fuga","photo_urls":["/api/v1/uploads/ejemplo.png"]}}
```

También se admiten valores de texto por fluido. La plantilla de minuciosa es versión 2; estos campos son opcionales y se conservan los cinco pasos requeridos existentes.

`POST /detailed-inspections/by-order/{order_id}/photos` recibe multipart `photos` (1 a 20 imágenes), aplica permisos de edición de la orden y registra las imágenes como anexos. El endpoint existente de documentos sigue disponible. El portal admite enlaces de archivos y fotografías con URLs antiguas `/uploads/`.

La app debe enviar los datos para que puedan consultarse; esta actualización no reconstruye fotos o lecturas que nunca llegaron al servidor.

## Cargas masivas desde Ajustes

Catálogos: marcas, modelos, categorías, medidas, motivos de entrada, motivos de salida, bahías y temparios. Se carga un archivo por catálogo. Los repuestos se cargan con su flujo existente antes de los temparios.

- `GET /settings/bulk/templates?filial_id=...`: columnas, ejemplos y opciones válidas.
- `GET /settings/bulk/templates/{kind}`: CSV UTF-8 BOM separado por punto y coma, solo encabezados para evitar importar ejemplos accidentalmente.
- `POST /settings/bulk/{kind}/preview`: `{ "rows": [...] }` valida y devuelve errores por fila o `review_hash`.
- `POST /settings/bulk/{kind}/import`: mismas filas y `review_hash`; vuelve a validar y guarda atómicamente.

Se requiere permiso de Ajustes para importar. La filial se determina mediante el contexto autorizado; no se acepta una filial introducida en las columnas. Marcas y otros catálogos compartidos respetan el holding; bahías y temparios respetan la filial. No se guardan filas parciales si alguna falla. Los duplicados o cambios concurrentes impiden confirmar una revisión obsoleta.

El portal permite CSV o Excel de una sola hoja, máximo 2 MB y 1.000 filas. Se deben conservar los encabezados descargados. Importar primero marcas, luego modelos y finalmente temparios; los códigos de repuestos y modelos referenciados deben existir.

Los temparios incluyen categoría, número opcional, nombre, horas, garantía, días/km, años, compatibilidad, herramientas, repuestos y activo. El número omitido se asigna con la secuencia de su categoría (501 si no existe ninguna). Compatibilidad: `Toyota:Corolla|Toyota:Hilux`; herramientas separadas por `|`; repuestos: `QA-001:1|QA-002:6`, o `QA-001:1:12,70` para un costo estimado sin compras. Los costos de compras prevalecen cuando hay lotes. Garantía: `standard`, `custom` o `none`; personalizada exige días o kilómetros.

Se aceptan decimales venezolanos y números sin agrupación con punto decimal. Las combinaciones ambiguas de separadores se rechazan para evitar alterar montos.

## Publicación

Aplicar `alembic upgrade head` (nueva migración `f7a042db3e65`, fluidos de inspección preliminar), publicar backend y frontend juntos. No se ha modificado producción ni recalculado órdenes históricas mediante esta tarea. Las pruebas PostgreSQL usan una base Docker desechable.

## Ampliaciones del listado PM

Se añadieron kilómetros configurables a la garantía automática de fábrica (`vehicle_warranty_default_km`) e interior opcional a ambas inspecciones (`interior`). La migración adicional es `a8b153ec4f76`.

El editor de temparios muestra costo como texto y el servidor determina el costo al crear/editar. El estimado inicial existente se conserva cuando no hay compras; las cargas de incorporación inicial pueden proporcionar estimados explícitos. El portal ya admite TXT tabulado o delimitado por punto y coma junto con CSV/Excel.

Consultar `pm-review-2026-10-22.md` para cobertura por ticket y los contratos de consulta de temparios desde Repuestos y cobranza en Bs.
