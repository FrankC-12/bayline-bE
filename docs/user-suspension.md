# Suspensión de usuarios

En Usuarios, un Súper Administrador puede usar **Suspender** sobre otro usuario,
incluido otro Súper Administrador de su misma filial. Se pide confirmación.
La API verifica tanto la cuenta actual como el rol/filial de destino; no se puede
suspender la propia cuenta. Se conservan los permisos de gestión de holding existentes.

La suspensión se guarda como `inactivo`. En la misma transacción se incrementa
`users.session_version` y se revocan todos los refresh tokens de la cuenta.
Cada petición autenticada consulta el estado y la versión vigentes en la base de
datos, tanto para cookies como para Bearer móvil. Los tokens anteriores no vuelven
a ser válidos al reactivar: el usuario debe iniciar sesión de nuevo.

El portal comprueba `/auth/me` cada 15 segundos mientras está visible, además de
comprobar al volver a la pestaña o ventana. Las peticiones normales se bloquean
inmediatamente después de confirmarse la suspensión. Una petición ya en curso
no se cancela retroactivamente. El login muestra «Tu usuario está suspendido»
cuando las credenciales son correctas. Una contraseña incorrecta conserva el
mensaje genérico de credenciales inválidas.

Desplegar backend y frontend y aplicar `alembic upgrade head` (migración
`a4db37e95215`) antes de servir tráfico con el nuevo backend. No requiere cambios
al `.env`. El cliente móvil recibe HTTP 401; debe manejarlo limpiando su sesión.
