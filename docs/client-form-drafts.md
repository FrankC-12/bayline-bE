# Formulario de Clientes: borradores y edición segura

El formulario editable conserva cambios en `sessionStorage` de la pestaña, separados por usuario, filial y cliente (o formulario de cliente nuevo). Se escriben tras 400 ms sin cambios y al confirmar un cierre o desmontar el formulario. No son registros de la base de datos ni se comparten con otro dispositivo.

Al abrir un formulario con borrador, se ofrece Recuperar o Descartar antes de editar. Se valida su estructura y se rechazan borradores de más de 24 horas. Guardar correctamente elimina el borrador; cerrar sesión o expirar la sesión limpia los borradores de esa pestaña y bloquea escrituras durante la limpieza del componente. Una nueva sesión autenticada habilita de nuevo el almacenamiento.

Cerrar el panel con cambios exige confirmación. Recargar o cerrar la pestaña activa el aviso nativo del navegador. Los enlaces internos normales también piden confirmación; abrir enlaces en otra pestaña no interrumpe la edición. La navegación del historial del navegador puede desmontar el formulario sin ese diálogo, pero la limpieza conserva el borrador si el almacenamiento está disponible.

Si el navegador bloquea el almacenamiento o agota su cuota, el formulario lo indica. Un fallo al guardar el borrador al cerrar mantiene el diálogo abierto; el usuario debe confirmar explícitamente Cerrar sin conservar para continuar. Los borradores contienen los datos de contacto y vehículos editados; no se envían al logger ni incluyen credenciales. Cerrar la pestaña normalmente elimina su `sessionStorage`, aunque el navegador puede restaurarlo según sus propias políticas.

Las validaciones muestran errores junto a los campos de cliente o junto al vehículo correspondiente. Los detalles del backend se vinculan a esos campos y se enfoca el primero inválido. Se conserva el mensaje general para fallos de conexión o de concurrencia. Un bloqueo síncrono evita doble envío mientras se guarda.

## Control de versión

`PATCH /api/v1/clients/{id}` acepta `expected_updated_at`, usando exactamente el `updated_at` que se obtuvo al abrir la ficha. El backend bloquea la fila y compara la versión antes de modificar datos. Si cambió, devuelve 409 con código `client_version_conflict`. Los cambios de vehículos también avanzan la versión del cliente.

Al recuperar un borrador se conserva la versión original, para impedir que sobrescriba cambios posteriores. Si aparece el conflicto, recargar la ficha actual y revisar los cambios del borrador antes de volver a guardar. Los clientes móviles anteriores pueden omitir el campo y mantener su contrato; para disponer de la misma protección deben enviarlo.

Esta entrega se aplica al formulario de Clientes. No cambia los otros formularios ni añade una migración de base de datos. Publicar backend y frontend para activar ambas capas.
