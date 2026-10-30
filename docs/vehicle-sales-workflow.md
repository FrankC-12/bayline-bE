# Oportunidades: reserva, crédito, PDI y entrega

## Despliegue

Desplegar backend y frontend y ejecutar `alembic upgrade head`. Nueva revisión:
`f9c082de076a` después de `e8bf71cd9659`.

Las oportunidades anteriores mantienen `workflow_version=0` y su flujo previo.
Las nuevas oportunidades de leads/clientes usan el flujo documentado aquí. Las ventas
directas existentes del catálogo conservan su comportamiento; el flujo nuevo parte de
Oportunidades. Los documentos se guardan en almacenamiento privado existente, no en uploads públicos.

## Operación web

1. Cotizar una unidad: se congelan importe, descuento, moneda y vigencia.
2. Reservar: seleccionar cuenta USD, medio y referencia de un abono efectivamente recibido.
   El backend valida el mínimo/plazo y registra el ingreso de forma atómica con la reserva.
   La retención aplicable se congela en ese momento. Los abonos de este flujo están en USD;
   el saldo de una venta al contado puede cobrarse en USD, Bs o mixto.
3. Si es financiada: adjuntar los nueve recaudos privados. La proforma se genera del precio
   cotizado. Enviar la documentación por el canal acordado con la financiera y registrar la
   referencia externa. Registrar el envío en Motrix **no realiza un envío externo**.
4. La respuesta entra por integración firmada o la registra un usuario con edición de
   Administración y Concesionario, indicando su constancia. Un vendedor sin acceso a
   Administración no puede aprobar su propio crédito.
5. Tras aprobar: adjuntar contrato tripartito y póliza, guardar aseguradora y número de póliza.
   Sin estos requisitos el backend rechaza la venta financiada.
6. Facturar desde la oportunidad: se conserva el precio cotizado; se descuenta el abono de los
   nuevos ingresos. En contado se seleccionan cuentas de las monedas realmente cobradas.
   Para pagos mixtos el aporte USD total incluye el abono previo. El crédito aprobado no se
   contabiliza como dinero recibido de la financiera.
7. Al facturar se vincula el VIN al vehículo del cliente y se crea una única ODS de PDI en
   borrador, con tarea interna sin cargo al cliente. Taller asigna técnico, ajusta el tempario
   de PDI si necesita estimación y envía el borrador por el flujo habitual. Su estimación inicial
   es cero hasta configurarla; no se inventa un tiempo de trabajo.
8. La entrega exige PDI efectivamente completada con una tarea completada, documentos,
   llaves, explicación de funciones, constancia de invitación a la app y acta firmada privada.
   La invitación se confirma después de enviarla por el canal habitual: no crea ni envía una
   cuenta de cliente automáticamente.
9. Confirmar entrega cierra la oportunidad y activa la garantía de fábrica para vehículos nuevos.
   Repetir la confirmación no duplica la garantía ni la entrega.

La interfaz consulta cambios externos cada 20 segundos cuando no se están editando formularios.
Las mutaciones usan versión optimista y las operaciones de stock/dinero comparten transacción.
Un VIN registrado a otro titular de la sucursal exige resolver explícitamente la titularidad
antes de facturar; no se traspasa el historial a otra persona silenciosamente.

## Cancelación y Finanzas

Desde la oportunidad, un usuario autorizado en Administración registra la cuenta USD y la
referencia de la devolución. Se conserva el abono original, se registra un egreso por el
reembolso y una liquidación única con la retención acordada; después se libera la unidad.
La cuenta debe tener saldo suficiente. No hay desembolso bancario automático: esta acción
registra una devolución realmente realizada, igual que un registro de pago en Finanzas.

El ingreso y egreso quedan enlazados con la oportunidad. La devolución no se computa como
un gasto operativo; la retención se reconoce una vez como ajuste de rentabilidad.
Los movimientos enlazados no se revierten aisladamente y la PDI facturada no se elimina ni
se cancela desde el taller. Una oportunidad liquidada requiere otra negociación nueva.

## Integración de respuesta de crédito (opcional)

Sin configurar la siguiente variable, el endpoint devuelve 503:

```env
VEHICLE_FINANCING_WEBHOOK_SECRET=un-secreto-de-al-menos-32-caracteres
```

`POST /api/v1/integrations/vehicle-financing/decision`

Cabeceras:

- `Content-Type: application/json`
- `X-CSRF-Protection: 1` (requisito general de la API)
- `X-Motrix-Timestamp`: segundos Unix, tolerancia de cinco minutos.
- `X-Motrix-Signature`: HMAC-SHA256 hexadecimal de `timestamp + "." + cuerpo_json_original`,
  con el secreto configurado. Se firma el cuerpo exacto, sin volver a serializarlo.

```json
{
  "opportunity_id": "UUID",
  "filial_id": "UUID",
  "event_id": "UUID estable para reintentos",
  "provider": "Financiera externa",
  "external_reference": "SOL-001",
  "approved": true,
  "reference": "Constancia externa de aprobación"
}
```

El proveedor y referencia deben coincidir con una solicitud enviada. Se rechazan firmas
inválidas o vencidas y organizaciones suspendidas. El mismo evento no se procesa dos veces.
Esto registra decisiones; las cuotas y pagos posteriores conservan la integración existente
`PUT /vehicle-sales/{sale_id}/financing`. El adaptador del proveedor puede cambiar sin
alterar el modelo del flujo. No hay una conexión activa con una financiera real hasta
configurar el proveedor o su adaptador.
