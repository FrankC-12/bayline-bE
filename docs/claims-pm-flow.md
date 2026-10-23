# Reclamos: flujo solicitado por el PM (23/10/2026)

## Bandeja y revisión

La bandeja muestra Todos, Esperando aprobación, Aprobados y Rechazados, con contadores, indicadores mensuales y el contador pendiente del menú. La queja se presenta antes que la garantía/ODS de origen. La bandeja se actualiza periódicamente y protege contra respuestas de una filial anterior.

Crear y consultar un reclamo usa panel lateral accesible (foco, Escape y bloqueo del fondo); los demás modales conservan su presentación habitual. El nuevo reclamo tiene tres pasos: vehículo, garantía vigente/campaña, síntoma y evidencias. El tipo, servicio y ODS de origen se derivan de la garantía. Las garantías vencidas permanecen visibles y deshabilitadas.

No se generan nuevos estados “En la marca”. El trámite con el fabricante queda fuera del sistema. Los campos históricos de marca se conservan para no perder trazabilidad. Sus registros pendientes pueden resolverse con la misma revisión local.

La queja y la información del trabajo original encabezan el detalle. Incluye veredicto y barras de tiempo/km, fecha y km de vencimiento, fotos, documentos y seguimiento de usuario/fecha. La cobertura se evalúa en la fecha y con los km del reclamo, sin recalcularla con una visita posterior. Antes de reclamar, las barras de la ficha y del formulario usan el último kilometraje conocido y lo indican expresamente.

## Kilometraje y foto

Los km de hoy empiezan vacíos. Son obligatorios, no negativos y no pueden ser menores al último registro confirmado del vehículo. El backend valida nuevamente; no depende de la restricción visual.

Ambos endpoints de creación (`POST /warranty-claims/from-warranty` y el adaptador móvil legado `POST /warranty-claims`) requieren multipart **`odometer_photo`** (imagen, no PDF), además de `reported_mileage`. Las fotos generales y documentos siguen siendo opcionales y no sustituyen a la foto identificada del tablero.

La fecha de creación se asigna en el servidor con la fecha de Venezuela. El endpoint legado adapta los datos a la misma verificación de garantía vigente. Crear un reclamo de proveedor directamente deja de ser una opción; se inicia desde un comeback autorizado y su lote.

La respuesta incluye `odometer_photo_url`, `warranty_assessment`, fecha/km de salida originales y técnico del trabajo original. Tiempo y kilometraje son límites independientes; vence lo que ocurra primero. Distancia recorrida = lectura del reclamo menos base de salida de la garantía de la ODS original. Las condiciones históricas sin datos suficientes se identifican como no verificables, sin inventar una confirmación.

## Aprobación

Solo el permiso de administración del taller/gerencia autoriza o rechaza. Rechazar exige `rejection_reason` no vacío. Para aprobar un comeback se exige `failure_category` y **`failure_confirmed: true`**, confirmación de la relación entre la falla y el trabajo reclamado. Se guarda esa confirmación junto con el usuario y fecha de aprobación.

Aprobar crea una única ODS en **borrador**, en la misma transacción:

- Comeback → tipo Retrabajo; pagador garantía del taller.
- Fábrica o campaña → tipo Garantía de fábrica; pagador garantía de fábrica.
- Cliente a $0 por las líneas cubiertas; hereda vehículo, síntoma y lectura del reclamo.
- Conserva vínculos al reclamo y a la ODS de origen; el trabajo reclamado mantiene sus snapshots originales aunque cambie después el catálogo.
- El asesor completa la recepción; la inspección de la app se vincula al borrador y confirma/corrige la lectura.

`POST /warranty-claims/{id}/review` es el contrato común. El endpoint legado `/authorize` usa la misma aprobación automática. `/convert-to-order` devuelve la ODS ya creada si existe, para no duplicarla por una llamada móvil anterior. Dos aprobaciones concurrentes se serializan con bloqueo de fila: una crea el borrador y la otra recibe conflicto.

Los comebacks autorizados mantienen la acción de reclamar al proveedor usando las asignaciones del lote de origen. No se añade ese tipo al formulario.

## Publicación

Ejecutar `alembic upgrade head` y desplegar backend y frontend. Nueva migración `b9c264fd5087` (posterior a `a8b153ec4f76`): foto explícita del tablero y confirmación del jefe. Las filas históricas no requieren una foto retroactiva.

**Coordinar con mobile antes de publicar:** debe enviar `odometer_photo` al crear y `failure_confirmed: true` al aprobar un comeback. Sin ellos, el backend rechazará estas operaciones según las reglas nuevas. Las decisiones ya no esperan respuesta de marca ni requieren una segunda conversión.

No se ha modificado producción. Se verifican las migraciones en PostgreSQL desechable, el flujo HTTP de evidencias y permisos, la aprobación concurrente, el precio cero del cliente y el snapshot de la tarea reclamada.
