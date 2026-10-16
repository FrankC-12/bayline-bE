# Asesores de vehículos y control externo de financiamientos

## Asesores

`GET /api/v1/vehicle-sales/advisors?filial_id=...` devuelve únicamente id, nombre y rol de usuarios activos de esa filial cuyo rol es `vendedor` y cuyo permiso efectivo de `concesionario` es editar. Una restricción por usuario prevalece sobre la plantilla del rol. Requiere lectura de concesionario y respeta la filial del solicitante.

La venta consume ese directorio y la API valida el asesor seleccionado antes de registrar la venta. Técnicos, administradores y vendedores de otra sucursal no son asesores válidos. Se conserva el contrato anterior que permite una venta sin asesor asignado y se respetan el cliente y vendedor de reservas existentes. Una reserva antigua con un responsable que dejó de ser elegible necesita revisarse antes de vender.

## Financiamientos externos

Nueva pantalla: **Concesionario → Financiamientos**. Todas las ventas `financiado` aparecen automáticamente, incluidas las anteriores. Una venta sin información externa muestra saldo desconocido y estado pendiente de sincronización. El precio financiado de venta no se interpreta como saldo del crédito.

El detalle muestra cliente, vehículo, referencia del contrato, importe contratado, pagado a la financiera, saldo, cuotas y pagos reportados. Permite búsqueda y filtros por estado. Las cantidades mantienen su moneda; USD y VES no se suman ni se convierten automáticamente. Los pagos a la financiera no crean ingresos, egresos ni movimientos de caja en Bayline.

### API independiente del transporte

- `GET /api/v1/vehicle-financings?filial_id=...`: lectura con permisos de concesionario.
- `PUT /api/v1/vehicle-sales/{sale_id}/financing`: actualización de una **instantánea completa** normalizada, con autenticación y permiso `concesionario:editar` de la filial. No recibe datos de ventas al contado.

Ejemplo:

```json
{
  "provider": "Financiera externa",
  "external_id": "CONTRATO-001",
  "reported_at": "2026-10-15T15:00:00Z",
  "source": "webhook",
  "external_event_id": "evento-001",
  "currency": "USD",
  "total_amount": "10000.00",
  "amount_paid": "2000.00",
  "complete_schedule": true,
  "installments": [
    {"external_id": "CUOTA-1", "due_date": "2026-10-01", "amount": "5000.00", "paid_amount": "2000.00"},
    {"external_id": "CUOTA-2", "due_date": "2026-11-01", "amount": "5000.00", "paid_amount": "0.00"}
  ],
  "payments": [
    {"external_id": "PAGO-001", "paid_at": "2026-10-10T14:00:00Z", "amount": "2000.00", "reference": "REF-001"}
  ]
}
```

La lista de pagos es detalle informativo, no una lista de movimientos para sumar otra vez al total pagado. Los totales reportados son la autoridad. Las cuotas y pagos pueden venir parcialmente detallados; `complete_schedule=true` exige que el cronograma sume exactamente el contrato. La aplicación no calcula tasas de interés, comisiones o amortizaciones del tercero.

Se validan importes con dos decimales, sobrepagos, referencias duplicadas, fechas con zona horaria y tamaño de las listas. La misma instantánea repetida no cambia la fecha de recepción. Una actualización más antigua o contradictoria con la misma fecha devuelve 409. Los bloqueos de filial y venta evitan carreras al asignar una referencia de contrato a distintas ventas.

### Conexión pendiente

Todavía no se configura un receptor público, firma, secreto ni credenciales de una financiera. Cuando se confirme el proveedor y su protocolo, un adaptador de servidor verificará su autenticidad y traducirá el mensaje a esta instantánea. Si se cambia el hook por API o importación, se reutilizan el modelo y la pantalla. Un evento incremental debe combinarse con el estado previo en el adaptador antes de actualizar la instantánea; no se debe enviar como si fuera un historial completo.

`external_event_id` se conserva como referencia del reporte; el control de orden e idempotencia se basa en `reported_at` y el contenido completo. Un contrato puede cambiar de proveedor/referencia mediante una instantánea posterior válida. El historial técnico de solicitudes permanece en la bitácora HTTP existente; no se crea una tabla de eventos financieros inmutables.

## Migración

La revisión `e2a7619c4b83` añade `vehicle_sales.financing_snapshot` y `financing_updated_at`, ambos nullable, después de `c8e3147f2a90` (imagen opcional de repuestos). Antes de publicar: `venv/bin/python -m alembic upgrade head`. No se modifica la base real durante las pruebas.
