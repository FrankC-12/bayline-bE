# Patrón compartido de modales

El patrón se aplica a consulta, creación y edición de clientes y usuarios. Las demás ventanas de la aplicación se mantienen por ahora.

`src/components/common/Modal.tsx` en el frontend define el contenedor centrado y adaptable, fondo, ancho máximo, encabezado con título/cierre, cuerpo desplazable y pie de acciones. El encabezado y el pie permanecen visibles en formularios largos.

La base incluye semántica de diálogo, título/descripción accesibles, navegación de foco por Tab, restauración del foco al cerrar, cierre mediante Escape y bloqueo del desplazamiento de la página mientras está abierta.

La lógica de negocio sigue en cada formulario. El modal permite suspender la interacción cuando se muestra una confirmación. ClientFormPanel conserva validaciones, borradores y protección ante cierre con cambios sin guardar; mantiene su nombre y sus props para no romper sus usos desde ventas, reservas y repuestos. Guardar se vincula al formulario mediante un id único.

En Clientes:

- Clic en el nombre o Ver información del cliente: abre la consulta, sin campos editables.
- Editar cliente, dentro de la consulta: cierra la consulta y abre la edición centrada; requiere permiso de edición.
- Nuevo cliente: usa la misma base de modal.
- Se retiró el acceso directo a la ficha del vehículo agregado por la interpretación anterior. La ficha de servicio continúa en Asesor → Vehículos e historial.

Validación: TypeScript, ESLint, 41 pruebas Node y Chromium con datos ficticios. La revisión comprobó consulta/edición/creación centradas, acción Guardar desde el pie, errores de validación, cierre con borrador, Escape, apertura por teclado, permisos de solo lectura y diseño móvil.

## Usuarios

El nombre en la tabla abre Información del usuario: cuenta, estado, rol, fechas y permisos efectivos combinando rol y ajustes individuales. No se muestran credenciales. Editar usuario y Nuevo usuario usan el mismo Modal centrado con botones en el pie; mantienen la matriz de permisos y el contrato de guardado existente. El correo se muestra como solo lectura al editar, porque el endpoint de actualización no admite cambiarlo.

Cerrar una edición con cambios solicita confirmación; las contraseñas y los permisos sin guardar no se escriben en almacenamiento del navegador. Inactivar/reactivar usa la confirmación visual compartida, con errores de servidor visibles y acciones deshabilitadas mientras se procesa. Los controles de administración siguen restringidos al administrador de filial y no se ofrece inactivarse a uno mismo.

Validación de Usuarios: TypeScript, ESLint, 41 pruebas Node y Chromium con datos ficticios. Se verificaron consulta sin escrituras, edición con conservación de permisos individuales, confirmación de estado, creación/invitación, cierre con cambios, usuario de solo lectura y móvil.

## Formularios de agregar estandarizados

También usan la misma base los ocho formularios que antes se desplegaban desde la derecha:

| Área | Formulario |
| --- | --- |
| Plataforma | Nuevo/editar holding |
| Plataforma | Nuevo usuario de holding |
| Holding | Nueva/editar filial |
| Holding | Nuevo administrador de filial |
| Asesor de Servicios | Nueva ODS |
| Asesor de Servicios | Nueva inspección |
| Repuestos | Nuevo/editar repuesto |
| Compras | Crear proveedor |

Se conservaron sus props, campos, condiciones para habilitar acciones, mensajes de error, validaciones y manejadores de guardado. Los nombres de archivos Panel se mantienen por compatibilidad con los componentes que los invocan. Los formularios nativos enlazan su botón de envío en el pie mediante un id único; los formularios con acciones directas conservan su manejador. Las ventanas de creación de usuarios de holding/filial conservan su pantalla de éxito.

La base dispone de tamaños md/lg/xl según el contenido, mantiene el encabezado y pie visibles, y desplaza únicamente el cuerpo cuando hace falta. El manejo del foco y Escape respeta el orden de las ventanas superpuestas; el desplazamiento del fondo se restablece cuando se cierra la última. Los editores de daños/fotos anidados conservan su interacción propia.

Verificación de esta conversión: TypeScript, ESLint, 41 pruebas Node y Chromium sobre una página temporal fuera del repositorio con datos ficticios. Se comprobaron los ocho flujos de guardado, éxito de creación de administradores, envío de formularios desde el pie, entrega prometida sin valor automático, tamaños móviles y Escape en ventanas superpuestas. Las pruebas visuales previas de Clientes y Usuarios se repitieron satisfactoriamente.
