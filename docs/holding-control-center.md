# Motrix: plataforma y centro de control del holding

## Comparación con el módulo anterior

El módulo anterior ofrecía administración de filiales y usuarios, conteos de ODS/ODT, ventas históricas por filial, clientes, almacenes y garantías consolidadas. Se conserva esa navegación y se añaden seis áreas de control.

| Área solicitada | Antes | Implementación nueva |
| --- | --- | --- |
| Administración Motrix | Existían listado/alta/edición de holdings y creación de su administrador | Vista identificada como Motrix, indicadores de holdings activos/inactivos y acceso reservado `motrixadmin`; bootstrap idempotente desde variables de entorno |
| Tesorería | Sin panel consolidado de cuentas | Saldos actuales por moneda, detalle de cuentas por filial, flujo del período, movimientos recientes, arqueos con diferencia contra saldo registrado, calendario de compras conciliadas y obligaciones fiscales |
| Transferencias de fondos | Transferencias dentro de una filial | Registro entre cuentas de distintas filiales del mismo holding y en la misma moneda, con salida/entrada atómicas, saldo suficiente y protección contra reintentos |
| Comercial | Totales históricos de vehículos/repuestos | Filtros de fechas y filial; ventas netas, meta diaria y cumplimiento, ticket, margen bruto, unidades nuevas/usadas y oportunidades con etapas e historial |
| Inventario | Cantidad de almacenes | Stock de vehículos por estado y filial; entrega confirmada diferenciada de venta; repuestos por código/fabricante, físico/reservado/disponible, valor FIFO, stock bajo y sin salidas durante 90 días |
| Traslados de piezas | Circuito dentro de cada filial | Circuito adicional entre filiales: solicitud, aprobación, despacho FIFO y recepción; conserva costos y referencias a lotes de origen |
| Taller | Conteos de ODS por estado | Ocupación actual de bahías, tablero de fases, permanencia y ciclo promedio de órdenes entregadas |
| Contabilidad y fiscal | Rentabilidad consolidada disponible como servicio | Pantalla de resultados operativos y ajustes; libro contable USD con asientos equilibrados, comprobación, estado de resultados y balance; eliminaciones solo consolidadas; obligaciones fiscales y pagos |
| Actividad | Logs técnicos | Auditoría transaccional consultable por holding/filial/fecha, usuarios e inicios de sesión; alertas de ventas bajo costo, descuentos sobre límite configurable y permanencia en taller |

## Pantallas

- `/platform/holdings`: administración de Motrix. Exclusiva de usuarios con alcance `platform`.
- `/holding`: resumen y accesos a las nuevas áreas.
- `/holding/tesoreria`
- `/holding/comercial`
- `/holding/inventario`
- `/holding/taller`
- `/holding/contabilidad`
- `/holding/actividad`

Los reportes permiten filtrar filial y fechas y buscar dentro de los resultados. Se actualizan cada 30 segundos mientras la pestaña está visible; no son un stream por WebSocket. Los formularios usan el modal central compartido.

## Activación y administrador Motrix

1. Desplegar backend y frontend de esta versión.
2. Ejecutar las migraciones antes de iniciar el backend nuevo:

   ```sh
   python -m alembic upgrade head
   ```

3. Configurar en el entorno del backend:

   ```dotenv
   PLATFORM_ADMIN_EMAIL=tu-correo-administrativo@tu-empresa.com
   PLATFORM_ADMIN_PASSWORD=TU_CLAVE_ELEGIDA
   ```

   La clave se define fuera del repositorio. Debe tener al menos 12 caracteres y un máximo de 72 bytes. No se necesita agregar una variable con el nombre de usuario: `motrixadmin` es el alias reservado de ese correo.

4. En una instalación nueva, cargar primero el catálogo de roles y crear el administrador:

   ```sh
   python -m app.scripts.seed_roles
   python -m app.scripts.seed_platform_user
   ```

   Si los roles ya existen basta con el segundo comando. En Docker se ejecutan dentro del contenedor del backend. El script puede crear el rol `platform` si falta.

5. Iniciar sesión con `motrixadmin` o con el correo configurado, y la contraseña elegida. Desde Holdings crear el grupo y su usuario administrador.

Se corrigió además una entrada inválida del catálogo de roles que impedía ejecutar `seed_roles` en una instalación nueva. No se agregó un permiso desconocido para sustituirla.

El script no modifica una contraseña existente y rechaza un correo que ya pertenece a un usuario del holding o de una filial. Cambiar la variable después de crear el usuario no restablece su contraseña. No se ha ejecutado este bootstrap sobre la base de datos de la aplicación ni se ha elegido una contraseña por el usuario.

## Contratos

`GET /api/v1/holdings/{holding_id}/control/{section}`

- `section`: `tesoreria`, `comercial`, `inventario`, `taller`, `contabilidad`, `actividad`.
- `date_from`, `date_to`: por defecto hoy en Caracas; máximo 366 días por consulta.
- `filial_id`: opcional; debe pertenecer al holding autenticado.
- Respuesta: indicadores, tablas, notas y hora de generación.

`POST /api/v1/holdings/{holding_id}/registers`

```json
{
  "request_id": "UUID-nuevo-para-la-operacion",
  "entry_date": "2026-10-26",
  "record": {
    "kind": "target",
    "filial_id": "UUID-de-la-filial",
    "amount": "10000.00"
  }
}
```

Tipos de registro: `target`, `opportunity`, `cash_count`, `tax`, `tax_payment`, `funds_transfer`, `stock_transfer`, `stock_transfer_action`, `vehicle_delivery`, `journal`, `risk_policy`. El esquema discriminado completo se publica en OpenAPI.

La misma referencia y el mismo contenido devuelven la operación original. Reutilizarla con contenido distinto produce conflicto. El bloqueo por holding serializa los registros y evita dobles pagos o recepciones concurrentes. Las cuentas y los lotes se bloquean adicionalmente al afectar saldos o inventario. Un traslado no reserva inventario durante la aprobación: al despacharlo se valida el disponible real, descontando reservas del taller.

## Criterios de datos y límites explícitos

- **Rangos diarios:** el cálculo compartido de rentabilidad usa días completos de Caracas con fin exclusivo del día siguiente. Se corrigió el filtro anterior que comparaba contra la medianoche de la fecha final y omitía sus ventas. Los márgenes del holding se convierten de proporción a porcentaje antes de mostrarse.
- **Dinero:** no se suman USD, Bs y EUR como si fueran equivalentes. Los saldos de tesorería son actuales; el filtro de fechas corresponde a movimientos, flujos y registros. Los resultados comerciales reutilizan el cálculo existente de ventas netas y costos; no confunden cobranza con facturación.
- **Compras por pagar:** el calendario automático utiliza compras de repuestos conciliadas, su vencimiento y pagos parciales. Las facturas de compra de vehículos todavía no disponen del mismo registro de vencimientos/pagos; no se inventan deudas o fechas para ellas.
- **Metas:** se capturan por día y filial. Una nueva meta para la misma fecha conserva la anterior en el historial y sustituye su valor en el reporte.
- **Oportunidades:** se registran y actualizan desde Comercial; no hay integración con un CRM externo. Cambiar etapa conserva el historial y mantiene una sola oportunidad en la vista.
- **Arqueos:** comparan conteo con saldo registrado hasta la fecha. No ajustan silenciosamente el saldo de caja.
- **Impuestos:** las obligaciones se registran con moneda y vencimiento. Los pagos generan egreso, validan período abierto, tasa histórica cuando corresponde y saldo de la cuenta; no permiten pagar más que el saldo de la obligación. No se calculan automáticamente declaraciones tributarias.
- **Contabilidad:** el resultado operativo sigue alimentándose de documentos reales. El libro contable es separado, en USD, y requiere saldos de apertura y asientos de contabilización. No existe sincronización automática de documentos al libro. Sus balances corresponden únicamente a asientos registrados; no deben confundirse con un balance completo importado del contador.
- **Eliminaciones:** son asientos equilibrados del libro consolidado. No modifican las filiales ni alteran automáticamente el informe operativo. Para corregir un asiento se registra uno inverso, sin borrar el original.
- **Traslados:** entre filiales requieren repuesto equivalente en ambos catálogos, por código o número de fabricante y unidad. Salida al despachar; entrada al recibir. Cada lote recibido conserva su costo y referencia de origen. Las acciones se validan en el servidor y quedan registradas con usuario y fecha.
- **Vehículos entregados:** un vehículo vendido permanece como vendido pendiente de entrega hasta registrar su entrega. No se supone que una venta implica entrega física.
- **Fases del taller:** se derivan de los estados existentes. «Control de calidad» agrupa trabajos completados sin facturar; no acredita una inspección física adicional. La ocupación cuenta bahías con un vehículo recibido y trabajo activo.
- **Alertas:** la permanencia usa por defecto 48 horas, configurable. El límite de descuento está desactivado hasta que el holding lo define. Se compara el descuento real sobre PVP normal (costo + 30 %); una alerta no modifica precios ni revoca autorizaciones anteriores.
- **Auditoría:** registra cambios ORM autenticados e inicios de sesión a partir de esta versión. No reconstruye eventos históricos ni incluye contraseñas, tokens o cuerpos de solicitudes. Los cambios y su auditoría se confirman o revierten juntos. Las escrituras SQL masivas/directas siguen en la bitácora técnica existente y no se reconstruyen como cambios individuales en esta pantalla.
- **Volumen visible:** movimientos y eventos recientes tienen límites explícitos (150/200); no son exportaciones completas de toda la historia. Las alertas de permanencia muestran las primeras 100 órdenes antiguas.

## Verificación

- Pruebas de pertenencia al holding, roles y rechazo de filiales/cuentas ajenas.
- Registro de dinero idempotente, pagos parciales, arqueos sin ajustes y asientos equilibrados.
- Ciclo entre filiales con recepción única y conservación de costos FIFO por lote.
- Prueba real PostgreSQL de dos reintentos simultáneos: una sola salida y entrada.
- Migración nueva probada con upgrade, downgrade y upgrade en una base desechable.
- Comprobaciones TypeScript, ESLint, pruebas de formatos y compilación de producción del frontend.
