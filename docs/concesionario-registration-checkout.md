# Concesionario: registro y venta de vehículos

## Registro y edición

- Motor (`engine_type`), asientos (`seats`) y tapicería (`upholstery`) están disponibles para todas las unidades.
- Los usados requieren kilometraje y cantidad de dueños anteriores. El valor cero es válido.
- Título, experticia y certificado se adjuntan como PDF o imagen mediante `POST /dealership-vehicles/{id}/documents/{titulo|experticia|certificado}`; campo multipart `file`.
- La descarga usa la misma ruta con GET y valida el acceso a la filial. Los archivos se guardan en almacenamiento privado; la API no expone referencias internas.
- No se permite vender un usado hasta completar dueños y los tres documentos. La carga es posterior al alta: una carga fallida puede reintentarse sobre la unidad ya creada.
- La edición permite identificadores y características, además de los precios y tributos. Los cambios financieros conservan el permiso de administración y requieren motivo. Estado, reserva, ubicación y fotos mantienen sus acciones propias.
- Ubicación sigue siendo un dato y filtro; se retira exclusivamente de la tarjeta del catálogo.

## Venta y factura

- `advisor_user_id` es obligatorio; solo se acepta un vendedor activo con permiso de edición de concesionario en la filial. Las reservas conservan su asesor y cliente.
- Contado requiere `payment_method`: `usd`, `bs` o `mixed`.
- USD exige `usd_payment_channel`: `cash`, `binance`, `paypal` o `zelle`.
- Bs exige `bs_payment_channel`: `pago_movil` o `transferencia`.
- Mixto exige ambos canales y el monto USD existente. El backend continúa calculando IGTF y total; no acepta un total del navegador.
- La financiación externa conserva su flujo separado; estos canales corresponden al cobro de contado del concesionario.
- Al confirmar, la descripción se copia a `VehicleSale.vehicle_snapshot`. La factura usa esa copia y el kilometraje de venta. Editar luego la ficha no modifica la descripción de esa venta.
- La pantalla de éxito permite descargar la factura. También permanece disponible desde el historial de ventas.
- `GET /dealership-vehicles/{id}/sale` obtiene la venta de una unidad sin descargar todo el historial.
- Las ventas anteriores mantienen canales y descripción congelada nulos: no se inventa el método de pago histórico. Para esas facturas la descripción procede de la ficha actual, salvo el kilometraje ya congelado.

## Borrador

- El formulario conserva un borrador durante 24 horas en localStorage, por usuario, filial y vehículo, en ese navegador.
- Guarda identificadores y elecciones, no nombres/documentos del cliente ni un precio congelado. Puede retomarse al cerrar el formulario o el navegador.
- No reserva la unidad ni registra ingresos. Al confirmar se vuelven a validar disponibilidad, asesor, cliente, precios y documentos en el servidor.
- Al vender correctamente se elimina el borrador. Un navegador con almacenamiento bloqueado muestra que no puede conservarlo.
- No sincroniza borradores entre dispositivos.

## Despliegue

Desplegar frontend y backend coordinadamente y ejecutar `alembic upgrade head` con el backend actualizado. Migración: `e2b915cf7303`, después de `d1a804be6202`. No necesita variables de entorno nuevas; reutiliza el almacenamiento privado existente.
