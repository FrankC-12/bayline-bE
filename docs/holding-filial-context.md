# Acceso del holding a una filial

Al iniciar sesión, el holding elige entre la vista consolidada y la administración
de una filial activa. El contexto de filial abre los módulos normales del portal
con acceso de administrador, incluidos usuarios, suspensión y permisos. El usuario
real no cambia: las operaciones y el cambio de contexto quedan asociados a su ID.

## API

`POST /api/v1/auth/holding-context` con `{ "filial_id": "UUID" }` selecciona una
filial. Con `{ "filial_id": null }` regresa a la vista general. Devuelve tokens
y actualiza las cookies HttpOnly como login/refresh. Solo usuarios cuyo rol real
sea de ámbito holding pueden utilizarlo. El servidor comprueba holding y estado
activo de la filial tanto al seleccionar como en cada petición y renovación.

Los JWT firmados de acceso y renovación conservan la selección mediante
`holding_context_filial_id`. Los permisos efectivos son los de administración de
la filial seleccionada; no se da acceso simultáneo a otras filiales. No se crean
usuarios duplicados ni se usan credenciales de otro administrador.

`GET /auth/me` informa `holding_context` y `filial_name`. El portal muestra una
franja con la filial actual y «Volver al holding». Los cambios de contexto recargan
el portal para vaciar las vistas y estados anteriores. Otras pestañas sincronizan
su navegación en la comprobación de sesión (15 segundos o al recuperar el foco).
Las peticiones con un ID de filial distinto siempre son rechazadas por el servidor.
La selección es compartida por las pestañas que usan las mismas cookies.

La ruta de cambio de contexto valida la identidad base para permitir regresar al
holding incluso si la filial seleccionada acaba de desactivarse. Todas las demás
rutas verifican el contexto activo. La suspensión del usuario holding conserva el
bloqueo y la invalidación de sesiones existentes.

Desplegar backend y frontend. Este cambio no añade migraciones ni variables de
entorno; presupone las migraciones existentes del proyecto aplicadas.
