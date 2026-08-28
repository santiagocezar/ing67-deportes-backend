# Documentación del backend de Sports App

Este documento centraliza el contrato de la API, las reglas implementadas, las
relaciones actuales y las decisiones técnicas relevantes. Debe actualizarse cuando
cambie el comportamiento público del backend.

## Arquitectura

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

## Modelos y relaciones

### `User`

Representa una cuenta. Sus datos públicos son `id`, `name`, `birthdate`, `role`, `email`
y `creation_date`. La contraseña se guarda únicamente como hash.

Reglas actuales:

- `email` debe ser único.
- La contraseña debe tener al menos ocho caracteres.
- `birthdate` se almacena como `DATE`.
- La edad al registrarse debe estar entre 18 y 100 años inclusive.
- Un registro público recibe el rol `referee`.

### `AuthSession`

Representa una sesión autenticada y conserva el identificador del refresh token vigente,
su vencimiento y el momento de revocación. El token completo no se guarda.

```text
User 1 ─────── N AuthSession
```

Un usuario puede iniciar varias sesiones. Cada sesión pertenece a un solo usuario. Al
eliminar un usuario, sus sesiones se eliminan mediante `ON DELETE CASCADE`.

### `Sport`

Representa un deporte administrable y contiene:

- `id`: identificador.
- `name`: nombre visible con la primera letra en mayúscula y el resto en minúscula.
- `normalized_name`: nombre interno sin diferencias de mayúsculas ni acentos.
- `max_players`: cantidad máxima de jugadores, entre 1 y 20 inclusive.

`normalized_name` tiene una restricción única. Por eso `Fútbol`, `FUTBOL`, `futBol` y
`fútbol` se consideran el mismo deporte incluso ante solicitudes concurrentes.

`Sport` no tiene relación con `User` ni `AuthSession`. La autenticación solamente
determina quién puede ejecutar sus operaciones HTTP.

## Autenticación

- Access token: duración de 15 minutos.
- Refresh token: duración máxima de 30 días.
- Transporte: encabezado `Authorization` con esquema `Bearer`.

```http
Authorization: Bearer TOKEN
```

Cada rotación invalida el refresh token anterior. Si se reutiliza, el servidor revoca la
sesión persistida y rechaza todos sus tokens.

## API de autenticación

### `POST /auth/signup`

Registra un usuario con rol `referee`.

```json
{
  "name": "Ana Example",
  "birthdate": "1995-04-20",
  "email": "ana@example.com",
  "password": "una-contraseña-segura"
}
```

Respuesta exitosa: `201 Created`.

### `POST /auth/login`

Valida credenciales, crea una sesión y devuelve ambos tokens.

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

Recibe el refresh token vigente en `Authorization` y devuelve un par nuevo.

### `DELETE /auth/logout`

Acepta un access o refresh token, revoca la sesión y responde `204 No Content`.

### `GET /auth/me`

Requiere un access token y devuelve el usuario autenticado.

## API de deportes — HU01

Todos los endpoints requieren un access token con rol `administrator`. Un usuario sin
token recibe `401 Unauthorized`; un usuario autenticado sin ese rol recibe
`403 Forbidden`. No existen filtros de búsqueda.

### `POST /sports`

Crea un deporte.

```json
{
  "name": "FÚTBOL",
  "max_players": 11
}
```

Respuesta exitosa: `201 Created`.

```json
{
  "sport": {
    "id": 1,
    "name": "Fútbol",
    "max_players": 11
  }
}
```

Un nombre equivalente devuelve `409 Conflict`. Un nombre inválido o `max_players`
fuera de `1..20` devuelve `422 Unprocessable Content`.

### `GET /sports`

Devuelve todos los deportes ordenados por `id`.

```json
{
  "sports": [
    {
      "id": 1,
      "name": "Fútbol",
      "max_players": 11
    }
  ]
}
```

Respuesta exitosa: `200 OK`.

### `GET /sports/{id}`

Devuelve un deporte. Si no existe, responde `404 Not Found`.

### `PUT /sports/{id}`

Modifica únicamente el nombre.

```json
{
  "name": "Fútbol sala"
}
```

`max_players` es inmutable después de la creación. Intentar modificarlo devuelve
`422 Unprocessable Content`. Una modificación exitosa responde `200 OK`.

### `DELETE /sports/{id}`

Elimina un deporte y responde `204 No Content`. Si no existe, responde `404 Not Found`.

## Contrato de errores

```json
{
  "error": {
    "code": "validation_error",
    "message": "max_players must be between 1 and 20."
  }
}
```

El frontend debe usar `error.code` para tomar decisiones y `error.message` solamente
como mensaje legible.

| Estado | Uso en la API |
| --- | --- |
| `400 Bad Request` | El cuerpo falta o está mal formado. |
| `401 Unauthorized` | La autenticación falta, venció, es inválida o fue revocada. |
| `403 Forbidden` | El usuario no tiene el rol necesario. |
| `404 Not Found` | El recurso no existe. |
| `409 Conflict` | La solicitud entra en conflicto con el estado actual. |
| `422 Unprocessable Content` | Un dato no cumple las reglas del negocio. |
| `503 Service Unavailable` | La base o autenticación no está disponible temporalmente. |

Los estados siguen la
[referencia HTTP de MDN](https://developer.mozilla.org/es/docs/Web/HTTP/Reference/Status).

## Persistencia y migraciones

PostgreSQL es la fuente persistente. `db.create_all()` se usa únicamente para una base
vacía mediante `init-db`. Las tablas existentes cambian mediante migraciones versionadas.

- `78feb1bb58cd`: agrega sesiones rotativas y convierte `birthdate` a `DATE`.
- `3e22b5f59faa`: agrega `sports` y sus restricciones de unicidad y rango.
