# Documentación del backend de Sports App

Este documento describe el contrato público y los flujos que necesitan frontend, QA y
backend. La especificación ejecutable y fuente de verdad se genera en
[docs/openapi.json](docs/openapi.json).

## Arquitectura

```text
Petición HTTP
    ↓
Pydantic v2
    ↓
routes/
    ↓
services/
    ↓
models.py / PostgreSQL
    ↓
respuesta validada por contrato
```

- `schemas/` define cuerpos, parámetros y respuestas públicas.
- `routes/` gestiona HTTP, JWT, estados y traducción de errores.
- `services/` conserva normalización, reglas, consultas y transacciones.
- `models.py` contiene los modelos persistidos y sus restricciones.
- `app.py` configura Flask, OpenAPI, Swagger, CORS y comandos.

Pydantic no consulta la base de datos. Los modelos SQLAlchemy tampoco heredan de los
esquemas Pydantic.

## Direcciones y documentación

- Backend Flask: `http://localhost:5000`
- Frontend Vue: `http://localhost:5173`
- OpenAPI 3.1: `GET /openapi.json`
- Swagger UI: `GET /docs`

`API_DOCS_ENABLED=true` habilita OpenAPI y Swagger en desarrollo y pruebas. En
producción debe configurarse `false`; ambos endpoints responderán `404`.

## Validación de solicitudes

Los objetos de entrada rechazan campos desconocidos.

- `400 Bad Request`: falta el cuerpo, el JSON está mal formado, el `Content-Type` no
  declara JSON o el valor raíz no es un objeto.
- `422 Unprocessable Content`: el JSON es un objeto válido, pero faltan campos o sus
  tipos, valores o nombres no cumplen el esquema.

Ejemplo de `422`:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed.",
    "details": [
      {
        "field": "body.name",
        "message": "Field required",
        "type": "missing"
      }
    ]
  }
}
```

`details` es opcional. Nunca contiene contraseñas, tokens, cuerpos completos, entradas
crudas de Pydantic ni errores internos.

## Modelos actuales

### User

Una cuenta expone `id`, `name`, `birthdate`, `role`, `email` y `creation_date`. La
contraseña se persiste únicamente como hash.

- El email es único y se normaliza a minúsculas.
- La contraseña requiere al menos ocho caracteres.
- `birthdate` se recibe como `YYYY-MM-DD` y se guarda como `DATE`.
- La edad debe estar entre 18 y 100 años inclusive.
- El registro público siempre crea el rol `referee`.
- El rol `administrator` sólo se crea mediante el comando de consola.

### AuthSession

Cada sesión conserva el identificador del refresh token vigente, su vencimiento y su
revocación. El token completo no se almacena.

```text
User 1 ─────── N AuthSession
```

### Sport

`Sport` contiene `id`, `name`, `normalized_name` y `max_players`.

- `normalized_name` ignora mayúsculas y acentos para evitar duplicados.
- El nombre visible se guarda con la primera letra en mayúscula.
- `max_players` representa el máximo por equipo permitido en cancha y admite `1..20`.
- Después de crear el deporte sólo puede modificarse `name`.

## Flujo de autenticación

### Registro

1. Frontend envía `POST /auth/signup`.
2. Pydantic valida forma, tipos, email, contraseña y fecha.
3. El servicio verifica unicidad y guarda el hash.
4. El backend responde `201` con el usuario público, sin tokens.
5. El usuario inicia sesión por separado.

### Login

1. Frontend envía email y contraseña a `POST /auth/login`.
2. El servicio valida las credenciales.
3. Se crea una `AuthSession` persistente.
4. Se devuelve un access token de 15 minutos y un refresh token rotativo de hasta
   30 días.

### Renovación

1. Frontend envía el refresh token vigente a `POST /auth/refresh`.
2. El servidor bloquea la sesión durante la operación y compara su identificador.
3. El token anterior queda reemplazado por un access token y un refresh token nuevos.
4. Si se reutiliza un refresh viejo, se revoca toda la sesión y se exige otro login.

Las aplicaciones cliente deben reemplazar ambos tokens de forma atómica.

### Logout

`DELETE /auth/logout` acepta access o refresh token, revoca la sesión persistida y
responde `204`. Los tokens relacionados dejan de servir.

## Endpoints de autenticación

| Método y ruta | Autorización | Cuerpo | Respuesta exitosa |
| --- | --- | --- | --- |
| `POST /auth/signup` | Pública | `name`, `birthdate`, `email`, `password` | `201`, mensaje y usuario |
| `POST /auth/login` | Pública | `email`, `password` | `200`, ambos tokens |
| `POST /auth/refresh` | Refresh token | Sin cuerpo | `200`, ambos tokens nuevos |
| `DELETE /auth/logout` | Access o refresh token | Sin cuerpo | `204` |
| `GET /auth/me` | Access token | Sin cuerpo | `200`, usuario |

Registro:

```json
{
  "name": "Ana Example",
  "birthdate": "1995-04-20",
  "email": "ana@example.com",
  "password": "example-password"
}
```

Login:

```json
{
  "email": "ana@example.com",
  "password": "example-password"
}
```

Respuesta de login o refresh:

```json
{
  "access_token": "<access-token>",
  "refresh_token": "<refresh-token>",
  "token_type": "Bearer",
  "access_expires_in": 900
}
```

Los tokens se envían así:

```http
Authorization: Bearer TOKEN
```

## Flujo y endpoints de deportes

Todas las operaciones requieren un access token con rol `administrator`. JWT se valida
antes de procesar el cuerpo. No existen filtros de búsqueda.

| Método y ruta | Acción | Respuesta exitosa |
| --- | --- | --- |
| `GET /sports` | Listar deportes por `id` | `200` |
| `POST /sports` | Crear deporte | `201` |
| `GET /sports/{sport_id}` | Consultar deporte | `200` |
| `PUT /sports/{sport_id}` | Modificar sólo el nombre | `200` |
| `DELETE /sports/{sport_id}` | Eliminar deporte | `204` |

Creación:

```json
{
  "name": "FÚTBOL",
  "max_players": 11
}
```

Respuesta:

```json
{
  "sport": {
    "id": 1,
    "name": "Fútbol",
    "max_players": 11
  }
}
```

Actualización:

```json
{
  "name": "Fútbol sala"
}
```

Enviar `max_players` en la actualización conserva el error
`immutable_field` con estado `422`. Un nombre equivalente existente responde `409`.

## Contrato de errores

| Estado | Uso |
| --- | --- |
| `400` | Cuerpo ausente, mal formado, no JSON o no objeto. |
| `401` | Token ausente, inválido, vencido, revocado o refresh reutilizado. |
| `403` | El rol autenticado no tiene permiso. |
| `404` | El recurso no existe. |
| `409` | Email o nombre normalizado en conflicto. |
| `422` | El objeto no cumple el esquema o una regla validable. |
| `503` | Base de datos o autenticación temporalmente no disponible. |

Frontend debe decidir con `error.code` y mostrar `error.message` como texto legible.

## OpenAPI y Hoppscotch

Regenerar el contrato después de cambiar una ruta, esquema o respuesta:

```powershell
python -m flask --app app export-openapi
```

El comando escribe UTF-8 determinista en `docs/openapi.json` sin consultar ni modificar
datos de negocio.

Para importar en Hoppscotch:

1. Abrir `Import`.
2. Elegir `OpenAPI`.
3. Subir `docs/openapi.json`, o importar `http://localhost:5000/openapi.json` con el
   backend iniciado.
4. Configurar `base_url=http://localhost:5000` en el entorno local.
5. Guardar los tokens devueltos por login/refresh en variables locales de Hoppscotch.

No se debe editar `docs/openapi.json` a mano ni mantener otra colección como fuente
paralela.

## Persistencia y migraciones

PostgreSQL es la fuente persistente. `init-db` se usa sólo para una base vacía. Los
cambios de tablas se aplican con Flask-Migrate/Alembic mediante migraciones versionadas.

- `78feb1bb58cd` agrega sesiones rotativas y convierte `birthdate` a `DATE`.
- `3e22b5f59faa` agrega `sports` y sus restricciones.
