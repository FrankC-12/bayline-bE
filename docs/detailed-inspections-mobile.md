# Inspección minuciosa — API móvil

Tag en Swagger/Scalar: **Inspecciones minuciosas**. Todos los endpoints requieren autenticación. Esta inspección es independiente de la inspección preliminar de recepción y pertenece a una ODS (una por orden).

Base: `/api/v1/detailed-inspections`.

| Método | Ruta | Uso |
| --- | --- | --- |
| GET | `/template` | Secciones, opciones, unidades y JSON Schema del formulario. |
| GET | `/by-order/{order_id}` | Estado, vehículo, datos, progreso y anexos. Devuelve `null` si todavía no se guardó; valida acceso a la ODS aun en ese caso. |
| PATCH | `/by-order/{order_id}` | Crea el borrador o combina los campos enviados con lo guardado. |
| POST | `/by-order/{order_id}/complete` | Valida y completa la inspección; no cambia el estado de la ODS. |
| POST | `/by-order/{order_id}/annexes` | Adjunta PDF multipart con campo `file`. |
| DELETE | `/by-order/{order_id}/annexes/{annex_id}` | Quita el anexo de la inspección. |

## Campos según las capturas

- `engine`: `leaks` (fugas), `accessory_belt` (correa de accesorio), `air_filter` (filtro de aire), `battery` y `battery_voltage` (voltios).
- `underbody`: `steering_box` (cajetín de dirección), `tie_rod_ends` (terminales), `dust_boots` (guardapolvos), `pipes` (tuberías), `leaks`, `exhaust_pipe`, `suspension` y `tripod_joints` (tripoides).
- `tires`: `front_left`, `front_right`, `rear_left`, `rear_right` y `spare`. Cada posición tiene `tread_depth` en mm y `pressure` en psi; ambos admiten cero y rechazan números negativos o no finitos.
- `brakes`: `front_pads`, `front_shoes`, `rear_pads` y `rear_shoes`.
- `observations`: observaciones opcionales del mecánico, hasta 5000 caracteres.

Condición: `bien`, `regular`, `defectuoso`. Solo frenos admite además `no_aplica`. Batería: `cargada` o `descargada`.

Ejemplo de guardado parcial:

```json
{
  "engine": {"leaks": "bien", "battery": "cargada", "battery_voltage": 12.6},
  "tires": {"front_left": {"tread_depth": 6.5, "pressure": 32}},
  "observations": "Revisar desgaste del neumático delantero derecho."
}
```

Los campos omitidos conservan sus valores. Enviar `null` limpia un campo individual. Se rechazan nombres desconocidos y condiciones inválidas. Se puede guardar una sección o un campo a la vez; las solicitudes simultáneas se combinan bajo bloqueo de la ODS.

## Progreso y completar

La respuesta incluye `sections_complete`, `completed_steps`, `total_steps: 5` y `missing_fields`. Los cuatro grupos de revisión aportan un paso cada uno al estar completos. La culminación aporta el quinto al llamar a `/complete`. Los anexos no cuentan para el progreso. Las observaciones son opcionales.

Completar exige todos los campos de los cuatro grupos, incluidas las cinco posiciones de neumáticos. Si faltan datos responde 422, código `detailed_inspection_incomplete`, con `details` que identifica los campos pendientes. Repetir `/complete` devuelve la misma fecha y el mismo usuario responsable. Una inspección completada rechaza modificaciones a la revisión con 409; permite agregar o quitar PDFs mientras la ODS siga editable.

Los PDFs se reciben al subirlos al endpoint de anexos, no mediante un proceso automático de scanner. Se valida el tipo PDF, la cabecera y el tamaño máximo configurado. Quitar un anexo elimina su asociación; conserva el archivo en el almacenamiento.

## Acceso y trazabilidad

El asesor con acceso a la ODS y el técnico asignado pueden consultar. Guardar, completar y gestionar anexos requieren permiso de edición en su módulo. Un técnico no asignado o un usuario de otra filial no tiene acceso. Una ODS cancelada, cerrada o facturada no admite estas modificaciones.

Se registran usuarios de creación, modificación y culminación; cada anexo incluye usuario, fecha, nombre y URL. No se modifica el frontend web ni el móvil.

## Despliegue

Ejecutar `alembic upgrade head` con el backend actualizado: revisión `d1e84a790b62`, tabla `detailed_inspections`. No requiere seed ni permisos nuevos; reutiliza los permisos existentes de asesor y técnico.
