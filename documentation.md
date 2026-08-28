# Documentación del backend de Sports App

Este documento centraliza el contrato de la API, las reglas implementadas, las
relaciones actuales y las decisiones técnicas relevantes. Debe actualizarse cuando
cambie el comportamiento público del backend.

## Arquitectura

La aplicación sigue este flujo:

```text
Petición HTTP
    ↓
routes/
    ↓
services/
    ↓
models.py / PostgreSQL
```

- `routes/` recibe solicitudes y construye respuestas HTTP.
- `services/` contiene validaciones, reglas y transacciones.
- `models.py` define las entidades persistidas y sus relaciones.
- `extensions.py` contiene las extensiones compartidas de Flask.
- `app.py` configura la aplicación, registra rutas y expone comandos.

## Direcciones locales

- Backend Flask: `http://localhost:5000`
- Frontend Vue: `http://localhost:5173`

Los endpoints pertenecen al backend. El origen del frontend se configura en
`CORS_ORIGINS`. CORS utiliza orígenes explícitos y no habilita el comodín `*`.

## Modelo de usuarios y sesiones

### `User`

Representa una cuenta de usuario. Sus datos públicos son `id`, `name`, `birthdate`,
`role`, `email` y `creation_date`. La contraseña se guarda únicamente como hash y no se
incluye en las respuestas.

Reglas actuales:

- `email` debe ser único.
- La contraseña debe tener al menos ocho caracteres.
- `birthdate` se almacena como `DATE`.
- La edad al registrarse debe estar entre 18 y 100 años inclusive.
- Un registro público recibe el rol `referee`.

### `AuthSession`

Representa una sesión autenticada y conserva el identificador del refresh token vigente,
su vencimiento y el momento de revocación. El refresh token completo no se guarda en la
base de datos.

Relación actual:

```text
User 1 ─────── N AuthSession
```

Un usuario puede iniciar varias sesiones. Cada sesión pertenece a un solo usuario. Al
eliminar un usuario, sus sesiones se eliminan mediante la relación configurada con
`ON DELETE CASCADE`.

## Autenticación

- Access token: duración de 15 minutos.
- Refresh token: duración máxima de 30 días.
- Transporte: encabezado `Authorization` con esquema `Bearer`.

```http
Authorization: Bearer TOKEN
```

Cada rotación invalida el refresh token anterior y entrega un par nuevo. Si se reutiliza
un refresh token anterior, el servidor interpreta la situación como posible robo,
revoca la sesión persistida y rechaza todos sus access y refresh tokens.

## Endpoints

La URL base local es `http://localhost:5000`.

### `POST /auth/signup`

Registra un usuario.

```json
{
  "name": "Ana Example",
  "birthdate": "1995-04-20",
  "email": "ana@example.com",
  "password": "una-contraseña-segura"
}
```

Respuesta exitosa: `201 Created` con los datos públicos del usuario.

### `POST /auth/login`

Valida las credenciales, crea una sesión persistente y devuelve los dos tokens.

```json
{
  "email": "ana@example.com",
  "password": "una-contraseña-segura"
}
```

Respuesta exitosa: `200 OK`.

```json
{
  "access_token": "...",
  "refresh_token": "...",
  "token_type": "Bearer",
  "access_expires_in": 900
}
```

### `POST /auth/refresh`

Requiere el refresh token vigente en `Authorization`. Devuelve un access token y un
refresh token nuevos. El cliente debe reemplazar ambos valores y descartar el refresh
anterior.

Respuesta exitosa: `200 OK`.

### `DELETE /auth/logout`

Acepta un access o refresh token y revoca la sesión completa asociada.

Respuesta exitosa: `204 No Content`.

### `GET /auth/me`

Ruta protegida que requiere un access token válido y devuelve los datos públicos del
usuario autenticado.

Respuesta exitosa: `200 OK`.

## Contrato de errores

Todas las respuestas de error utilizan esta estructura:

```json
{
  "error": {
    "code": "validation_error",
    "message": "birthdate must be a valid date in YYYY-MM-DD format."
  }
}
```

El frontend debe usar `error.code` para tomar decisiones. `error.message` es un mensaje
legible y no debe utilizarse como identificador estable.

Los estados siguen la
[referencia HTTP de MDN](https://developer.mozilla.org/es/docs/Web/HTTP/Reference/Status):

| Estado | Uso en la API |
| --- | --- |
| `400 Bad Request` | El cuerpo falta o está mal formado. |
| `401 Unauthorized` | La autenticación falta, venció, es inválida o la sesión fue revocada. |
| `403 Forbidden` | El usuario está autenticado, pero no tiene permiso. Reservado para operaciones futuras. |
| `404 Not Found` | El recurso solicitado no existe. |
| `409 Conflict` | La solicitud entra en conflicto con el estado actual, por ejemplo un email duplicado. |
| `422 Unprocessable Content` | El JSON es válido, pero uno de sus datos no cumple las reglas. |
| `503 Service Unavailable` | La autenticación o la base de datos no está disponible temporalmente. |

## Persistencia y migraciones

PostgreSQL es la fuente persistente. `db.create_all()` se utiliza únicamente para crear
una base vacía mediante `init-db`. Los cambios sobre tablas existentes se realizan con
migraciones versionadas de Flask-Migrate/Alembic.

La migración inicial registrada agrega `auth_sessions` y convierte `users.birthdate` de
texto a `DATE`.
