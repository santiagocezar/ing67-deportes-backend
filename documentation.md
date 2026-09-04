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
- Swagger UI: `GET /swagger`

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

`Sport` contiene `id`, `name`, `normalized_name`, `max_players` y
`max_players_in_game`.

- `normalized_name` ignora mayúsculas y acentos para evitar duplicados.
- El nombre visible se guarda con la primera letra en mayúscula.
- `max_players` representa la capacidad máxima del plantel del equipo.
- `max_players_in_game` representa cuántos jugadores del equipo pueden estar en cancha
  simultáneamente y nunca puede superar `max_players`.
- Ambas capacidades son enteros positivos, obligatorios e inmutables.
- Después de crear el deporte sólo puede modificarse `name`.

### Team

Un equipo expone `id`, `name`, el objeto `sport`, `gender_category`, `is_enabled`,
`created_at` y `disabled_at`.

- `gender_category` sólo admite `male` o `female`.
- La identidad única combina nombre normalizado, deporte y categoría de género. Por eso
  el mismo nombre puede existir en otro deporte o categoría.
- La comparación de nombres ignora espacios repetidos, mayúsculas y acentos, pero el
  nombre visible conserva las mayúsculas elegidas por el administrador.
- Al crearse queda habilitado y `disabled_at` es `null`.
- Deshabilitar no elimina el registro ni libera su nombre, pero elimina definitivamente
  todas sus asociaciones actuales con jugadores. Habilitarlo no las restaura.
- Un equipo deshabilitado no puede renombrarse hasta ser habilitado otra vez.
- Deporte y categoría de género son inmutables y no existe eliminación física.

```text
Sport 1 ─────── N Team
```

### Player

Un jugador expone `id`, `name`, el objeto `sport`, `gender`, equipos resumidos,
`is_enabled`, `created_at` y `disabled_at`. `normalized_name` se conserva sólo para
búsqueda y ordenamiento internos; no se acepta ni se devuelve.

- `id` es autogenerado y la única identidad: pueden existir nombres repetidos.
- El nombre es obligatorio, admite hasta 100 caracteres luego de compactar espacios y
  conserva las mayúsculas elegidas para mostrarlo.
- `gender` sólo admite `male` o `female` y el deporte y género no pueden modificarse.
- Puede estar sin equipo o en hasta tres equipos habilitados de su mismo deporte y
  género.
- Cada equipo admite como máximo `sport.max_players` jugadores.
- Deshabilitar un jugador elimina definitivamente sus asociaciones; habilitarlo no las
  restaura. No existe eliminación física.
- El modelo no incluye DNI, nacionalidad, fotos ni datos biométricos.

```text
Sport 1 ─────── N Player
Team N ─────── N Player (mediante team_players)
```

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
  "max_players": 22,
  "max_players_in_game": 11
}
```

Respuesta:

```json
{
  "sport": {
    "id": 1,
    "name": "Fútbol",
    "max_players": 22,
    "max_players_in_game": 11
  }
}
```

Actualización:

```json
{
  "name": "Fútbol sala"
}
```

Enviar cualquiera de las dos capacidades en la actualización produce
`immutable_field` con estado `422`. Un nombre equivalente existente responde `409`.
Eliminar un deporte referenciado por cualquier equipo o jugador, incluso deshabilitado,
responde `409` con `sport_in_use`.

## Flujo y endpoints de equipos

Todas las operaciones requieren un access token activo cuyo claim `role` sea
`administrator`. El frontend obtiene ese token mediante `POST /auth/login` y lo envía
como `Authorization: Bearer TOKEN`.

### Creación

1. El frontend obtiene o lista los deportes para conocer `sport_id`.
2. Envía nombre, deporte y categoría a `POST /teams`.
3. Pydantic valida el cuerpo y el servicio comprueba que el deporte exista.
4. El nombre se compacta para mostrarlo y se normaliza para detectar duplicados.
5. El backend crea el equipo habilitado y responde `201`.

```json
{
  "name": "Águilas FC",
  "sport_id": 1,
  "gender_category": "female"
}
```

El deporte inexistente produce `404 sport_not_found`. Un nombre equivalente dentro del
mismo deporte y categoría produce `409 team_name_conflict`.

### Consulta y listado

| Método y ruta | Acción | Respuesta exitosa |
| --- | --- | --- |
| `GET /teams` | Listar, filtrar, ordenar y paginar | `200` |
| `GET /teams/{team_id}` | Consultar un equipo habilitado o deshabilitado | `200` |
| `POST /teams` | Crear un equipo habilitado | `201` |
| `PUT /teams/{team_id}` | Modificar sólo el nombre | `200` |
| `PATCH /teams/{team_id}/disable` | Deshabilitar sin eliminar | `200` |
| `PATCH /teams/{team_id}/enable` | Volver a habilitar | `200` |

No existe `DELETE /teams/{team_id}`.

`GET /teams` acepta estos parámetros opcionales:

| Parámetro | Valores | Predeterminado | Uso |
| --- | --- | --- | --- |
| `search` | texto | ninguno | Coincidencia parcial de nombre sin distinguir mayúsculas ni acentos |
| `sport_id` | entero positivo | ninguno | Filtra por deporte; uno inexistente responde `404` |
| `gender_category` | `male`, `female` | ninguno | Filtra por categoría |
| `status` | `enabled`, `disabled`, `all` | `enabled` | Selecciona el estado administrativo |
| `sort` | `name_asc`, `created_at_desc` | `name_asc` | Orden estable por nombre o creación |
| `page` | entero positivo | `1` | Página solicitada |

Los filtros se combinan con `AND`. El tamaño es fijo en 25 registros; no se acepta un
parámetro para cambiarlo. Una página fuera de rango responde `200` con `teams: []` y
mantiene los totales reales.

Ejemplo:

```http
GET /teams?search=agui&sport_id=1&gender_category=female&status=enabled&sort=name_asc&page=1
```

Respuesta:

```json
{
  "teams": [
    {
      "id": 1,
      "name": "Águilas FC",
      "sport": {
        "id": 1,
        "name": "Fútbol",
        "max_players": 22,
        "max_players_in_game": 11
      },
      "gender_category": "female",
      "is_enabled": true,
      "created_at": "2026-09-01T12:00:00Z",
      "disabled_at": null
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 25,
    "total_items": 1,
    "total_pages": 1
  }
}
```

### Renombrado y estado

El renombrado recibe únicamente:

```json
{
  "name": "Águilas del Sur"
}
```

Enviar `sport_id`, `gender_category`, estado u otro campo produce `422`. Renombrar un
equipo deshabilitado produce `409 team_disabled`.

Los endpoints `disable` y `enable` no reciben cuerpo. Son idempotentes: repetir la misma
acción responde `200` y conserva un estado consistente. Al deshabilitar,
`disabled_at` recibe la fecha y se eliminan definitivamente las asociaciones con
jugadores; al habilitar vuelve a `null` sin restaurarlas.

## Flujo y endpoints de jugadores

Todas las operaciones requieren un access token activo con rol `administrator`.

| Método y ruta | Acción | Respuesta exitosa |
| --- | --- | --- |
| `POST /players` | Crear un jugador y sus asociaciones | `201` |
| `GET /players` | Listar, filtrar, ordenar y paginar | `200` |
| `GET /players/{player_id}` | Consultar un jugador habilitado o deshabilitado | `200` |
| `PUT /players/{player_id}` | Cambiar nombre y reemplazar todos sus equipos | `200` |
| `PATCH /players/{player_id}/disable` | Deshabilitar sin eliminar | `200` |
| `PATCH /players/{player_id}/enable` | Volver a habilitar | `200` |

No existe `DELETE /players/{player_id}`.

### Creación y asociaciones

`POST /players` acepta exactamente:

```json
{
  "name": "Lionel Messi",
  "sport_id": 1,
  "gender": "male",
  "team_ids": [1]
}
```

`team_ids` es opcional y su valor predeterminado es `[]`. No admite identificadores
repetidos, no positivos ni más de tres elementos. Antes de escribir, el servicio valida
el conjunto completo: el deporte y los equipos deben existir; cada equipo debe estar
habilitado, tener el mismo deporte y género, y conservar una capacidad máxima de
`sport.max_players`. La creación y las asociaciones se confirman una sola vez; ante un
error no queda un jugador parcial.

Las respuestas singulares devuelven directamente el jugador:

```json
{
  "id": 1,
  "name": "Lionel Messi",
  "sport": {
    "id": 1,
    "name": "Fútbol",
    "max_players": 22,
    "max_players_in_game": 11
  },
  "gender": "male",
  "teams": [
    {"id": 1, "name": "Inter Miami"}
  ],
  "is_enabled": true,
  "created_at": "2026-09-03T12:00:00Z",
  "disabled_at": null
}
```

### Consulta y listado

`GET /players` acepta estos parámetros opcionales:

| Parámetro | Valores | Predeterminado | Uso |
| --- | --- | --- | --- |
| `search` | texto | ninguno | Coincidencia parcial de nombre sin distinguir mayúsculas ni acentos |
| `sport_id` | entero positivo | ninguno | Filtra por deporte; uno inexistente responde `404` |
| `gender` | `male`, `female` | ninguno | Filtra por género |
| `team_id` | entero positivo | ninguno | Filtra por equipo; uno inexistente responde `404` |
| `status` | `enabled`, `disabled`, `all` | `enabled` | Selecciona el estado administrativo |
| `sort` | `name_asc`, `created_at_desc` | `name_asc` | Orden estable por nombre o creación |
| `page` | entero positivo | `1` | Página solicitada |

Los filtros se combinan con `AND` y se ejecutan en PostgreSQL. Cada página contiene 25
registros. Una página fuera de rango responde `200` con `players: []` y los totales
reales. Los empates se ordenan de forma determinista por `id`.

```json
{
  "players": [],
  "pagination": {
    "page": 1,
    "per_page": 25,
    "total_items": 0,
    "total_pages": 0
  }
}
```

`GET /players/{player_id}` permite consultar jugadores habilitados o deshabilitados.
Un identificador inexistente responde `404 player_not_found`.

### Actualización y estado

`PUT /players/{player_id}` requiere ambos campos y reemplaza el conjunto completo de
equipos de forma atómica:

```json
{
  "name": "Nombre actualizado",
  "team_ids": [1, 2]
}
```

Una lista vacía elimina todas las asociaciones. `id`, `sport_id`, `gender`, estado y
fechas son inmutables; campos desconocidos producen `422`. Un jugador deshabilitado
puede consultarse, pero no actualizarse ni asociarse hasta rehabilitarlo y responde
`409 player_disabled`.

Los endpoints de estado no reciben cuerpo y son idempotentes. Deshabilitar asigna
`disabled_at` y elimina definitivamente todas las asociaciones en la misma transacción.
Habilitar vuelve `disabled_at` a `null` y no restaura equipos anteriores.

Estas asociaciones representan membresía general de equipos, no planteles de una
competición. Esta funcionalidad no implementa competiciones, DNI, nacionalidad, fotos,
almacenamiento de imágenes, reconocimiento facial ni datos biométricos.

## Contrato de errores

| Estado | Uso |
| --- | --- |
| `400` | Cuerpo ausente, mal formado, no JSON o no objeto. |
| `401` | Token ausente, inválido, vencido, revocado o refresh reutilizado. |
| `403` | El rol autenticado no tiene permiso. |
| `404` | El recurso no existe. |
| `409` | Recurso deshabilitado, asociación incompatible, equipo completo o deporte todavía referenciado. |
| `422` | El objeto no cumple el esquema o una regla validable. |
| `503` | Base de datos o autenticación temporalmente no disponible. |

Frontend debe decidir con `error.code` y mostrar `error.message` como texto legible.
Los códigos de jugadores y asociaciones incluyen `player_not_found`, `sport_not_found`,
`team_not_found`, `player_disabled`, `team_disabled`, `team_sport_mismatch`,
`team_gender_mismatch` y `team_capacity_reached`.

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
- `a8c4e12f6b90` separa las capacidades de deporte y agrega `teams`, estados,
  restricciones y relación con `sports`.
- `b4e6c1d2a9f0` agrega `players`, sus estados y la asociación `team_players`.

El DER de las entidades realmente implementadas se mantiene en `docs/erd.puml`.
