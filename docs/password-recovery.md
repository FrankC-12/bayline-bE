# Recuperación de contraseña y suspensión de organizaciones

## Flujo

- `/recuperar-password`: consulta el correo de acceso. Para usuarios de sucursal,
  indica contactar al administrador de su sucursal. En Usuarios, el administrador
  puede restablecer la contraseña de un usuario activo con el botón de llave,
  confirmando su propia contraseña. Esto revoca las sesiones de la cuenta afectada.
  No permite restablecer cuentas suspendidas. Para usuarios de plataforma,
  indica contactar a la administración de la plataforma.
- Solo cuentas de holding activas, con holding activo, pueden solicitar y usar OTP.
  Se ofrecen correo y SMS. El SMS requiere un teléfono previamente verificado.
- En Holding → Seguridad y recuperación se registra/cambia el teléfono con
  contraseña actual y verificación por SMS. Nunca se acepta un teléfono arbitrario
  desde el formulario público de recuperación.
- Seis dígitos, vigencia predeterminada de 10 minutos, máximo cinco errores,
  espera de 60 segundos para reenvío. Nuevo código invalida al anterior. Solo
  se almacenan HMAC de códigos; no se devuelven OTP por API ni se registran en logs.
- El cambio de contraseña consume los códigos y revoca todas las sesiones. No
  reactiva usuarios ni organizaciones. El usuario debe iniciar sesión nuevamente.
- El bloqueo de holding comprueba su estado en cada petición/login/refresh y
  bloquea también a las cuentas de todas sus sucursales. Al bloquear, se revocan
  tokens y códigos pendientes. Reactivar el holding no revive las sesiones ni
  cambia las suspensiones individuales de usuarios o sucursales.

## Configuración de envío

No se han modificado credenciales ni el `.env` del servidor. Sin proveedor, los
canales aparecen como no disponibles y no se simula un envío exitoso.

Correo SMTP (servidor de correo de tu elección):

```dotenv
SMTP_HOST=smtp.ejemplo.com
SMTP_PORT=587
SMTP_USERNAME=usuario-smtp
SMTP_PASSWORD=credencial-del-proveedor
SMTP_FROM_EMAIL=no-reply@ejemplo.com
SMTP_STARTTLS=true
SMTP_SSL=false
```

Para TLS implícito normalmente se usa puerto 465, `SMTP_SSL=true` y
`SMTP_STARTTLS=false`. En producción se exige TLS. El remitente debe estar
habilitado por tu proveedor SMTP.

SMS con Twilio:

```dotenv
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=credencial-del-proveedor
TWILIO_FROM_NUMBER=+15551234567
```

Configurar el remitente y la cobertura del destino en la cuenta de Twilio.
Adaptador basado en [Messages API](https://www.twilio.com/docs/messaging/api/message-resource).
La recepción real requiere probar los destinos con la cuenta del proveedor;
las pruebas automatizadas utilizan transportes simulados y no envían mensajes.

Opcionales:

```dotenv
RECOVERY_OTP_MINUTES=10
RECOVERY_OTP_ATTEMPTS=5
```

Aplicar configuración al recrear el contenedor del backend. No usar el número
mostrado como ejemplo como remitente real.

## Despliegue

Requiere backend y frontend actualizados, las dependencias de `requirements.txt`
y migración `b5ec48fa6326` aplicada antes de servir tráfico con el nuevo backend:

```bash
docker exec app-backend-1 alembic upgrade head
```

La migración agrega el almacenamiento de desafíos y el teléfono verificado del
usuario. Las cuentas existentes conservan su contraseña y no tienen SMS habilitado
hasta completar la verificación del teléfono.

## API

- POST `/auth/password-recovery/options`: email → acción y canales disponibles.
- POST `/auth/password-recovery/request`: email + channel (`email`/`sms`).
- POST `/auth/password-recovery/reset`: challenge_id + code + new_password.
- GET `/auth/recovery-phone`: estado del teléfono del usuario autenticado.
- POST `/auth/recovery-phone/request`: phone (E.164) + current_password.
- POST `/auth/recovery-phone/verify`: challenge_id + code.
- POST `/users/{id}/reset-password`: current_password del administrador + new_password.

Todos llevan el prefijo `/api/v1`. Los endpoints públicos conservan protección
CSRF, rate limits globales y presupuestos adicionales compartidos por IP y cuenta.
Los contactos de recuperación se muestran enmascarados en el flujo público.
