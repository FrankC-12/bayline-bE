# Leads y Oportunidades

- Clientes → Nuevo lead: nombre, cédula/RIF y teléfono; correo opcional. No requiere
  dirección, vehículo, ODS o compra. Incluye origen, interés, responsable, notas y
  próximo contacto. Taller también tiene «Registrar lead del taller».
- Los filtros Todos / Leads / Clientes se calculan con las operaciones reales.
  Una ODS enviada al taller o una compra no cancelada convierte el mismo registro
  en cliente. Se excluyen borradores, cancelaciones y perfiles internos de facturación.
- Los interesados en vehículos generan una oportunidad. Concesionario →
  Oportunidades incluye Prospecto, Cotizada, Reservada, Financiamiento, Facturada,
  Entregada y Perdida. Se puede abrir una oportunidad adicional para un cliente existente.
- Las cotizaciones toman el precio del catálogo. Las reservas y ventas usan sus
  flujos existentes y actualizan la oportunidad automáticamente. Facturada y
  Entregada requieren venta real y constancia de entrega, respectivamente.
- El financiamiento representa gestión comercial, no aprobación de la financiera.
  Una pérdida exige motivo y una unidad reservada debe liberarse antes.
- Los contactos y operaciones están aislados por filial. Vendedores activos con
  permisos pueden ser responsables. Las cotizaciones tienen control de versión.
- Se recuperan las ventas y reservas existentes en la migración. No se modifican
  importes ni documentos históricos.

Desplegar backend y frontend y aplicar las migraciones c6fd59ab7437 y d7ae60bc8548:

```bash
docker exec app-backend-1 alembic upgrade head
```

La inspección minuciosa ahora usa la plantilla versión 3: los neumáticos solo
requieren presión. La profundidad histórica se conserva, pero no se muestra ni se
solicita a la app. Este ajuste no agrega una migración adicional.
