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

## Contrato general para el frontend

### URL, JSON y encabezados

El frontend debe configurar una única URL base:

```text
http://localhost:5000
```

Las rutas que reciben datos esperan un objeto JSON. En esos casos se debe enviar:

```http
Content-Type: application/json
```

Las rutas autenticadas reciben el token exclusivamente en el encabezado:

```http
Authorization: Bearer ACCESS_TOKEN
```

`POST /auth/refresh` es la excepción: utiliza el refresh token como Bearer. La API no
crea cookies ni recibe tokens en parámetros o cuerpos JSON.

Las fechas se envían como `YYYY-MM-DD`. Los timestamps devueltos por la API usan formato
ISO 8601. Las respuestas `204 No Content` no incluyen JSON y el frontend no debe intentar
parsearlas.

### Resumen de endpoints implementados

| Método | Ruta | Token | Rol/estado requerido | Cuerpo |
| --- | --- | --- | --- | --- |
| `POST` | `/auth/signup` | No | Público | Datos personales y `requested_role` |
| `POST` | `/auth/login` | No | Público | Email y contraseña |
| `POST` | `/auth/refresh` | Refresh | Cualquier cuenta existente | Sin cuerpo |
| `DELETE` | `/auth/logout` | Access o refresh | Cualquier cuenta existente | Sin cuerpo |
| `GET` | `/auth/me` | Access | Cualquier cuenta existente | Sin cuerpo |
| `GET` | `/users` | Access | Administrador activo | Sin cuerpo; filtros opcionales |
| `POST` | `/users/{id}/approve` | Access | Administrador activo | Sin cuerpo |
| `DELETE` | `/users/{id}` | Access | Administrador activo | Sin cuerpo |
| `POST` | `/users/{id}/disable` | Access | Administrador activo | Sin cuerpo |
| `POST` | `/users/{id}/enable` | Access | Administrador activo | Sin cuerpo |
| `GET` | `/sports` | Access | Administrador activo | Sin cuerpo |
| `POST` | `/sports` | Access | Administrador activo | Configuración completa del deporte |
| `GET` | `/sports/{id}` | Access | Administrador activo | Sin cuerpo |
| `PUT` | `/sports/{id}` | Access | Administrador activo | Únicamente `name` |
| `DELETE` | `/sports/{id}` | Access | Administrador activo | Sin cuerpo |

### Matriz de permisos actual

| Acción | `user` | `referee` | `federation_delegate` | `administrator` |
| --- | --- | --- | --- | --- |
| Autenticación propia | Sí | Sí | Sí | Sí |
| Ver estado en `/auth/me` | Sí | Sí | Sí | Sí |
| Revisar y administrar cuentas | No | No | No | Sí, si está activo |
| CRUD de deportes | No | No | No | Sí, si está activo |

`user` significa cuenta pendiente. `is_active = false` bloquea todos los recursos de
negocio aunque el rol sea correcto. En esta versión no existen endpoints de negocio para
árbitros ni delegados federativos.

### Tipos de respuesta recomendados para TypeScript

```typescript
type UserRole =
  | "user"
  | "referee"
  | "federation_delegate"
  | "administrator";

type RequestedRole = "referee" | "federation_delegate";

interface User {
  id: number;
  name: string;
  birthdate: string;
  role: UserRole;
  requested_role: RequestedRole | null;
  is_active: boolean;
  email: string;
  creation_date: string | null;
}

interface ResolutionMethod {
  code: string;
  name: string;
}

interface Sport {
  id: number;
  name: string;
  max_players: number;
  match_duration: number;
  resolution_methods: ResolutionMethod[];
}

interface ApiError {
  error: {
    code: string;
    message: string;
  };
}
```

## Modelos y relaciones

### `User`

Representa una cuenta. Sus datos públicos son `id`, `name`, `birthdate`, `role`,
`requested_role`, `is_active`, `email` y `creation_date`. La contraseña se guarda
únicamente como hash.

Reglas actuales:

- `email` debe ser único.
- La contraseña debe tener al menos ocho caracteres.
- `birthdate` se almacena como `DATE`.
- La edad al registrarse debe estar entre 18 y 100 años inclusive.
- Los roles válidos son `user`, `referee`, `federation_delegate` y `administrator`.
- Un registro público siempre recibe `role = user`.
- El registro debe solicitar `referee` o `federation_delegate` mediante
  `requested_role`.
- `is_active` permite deshabilitar una cuenta aprobada sin perder su rol.
- La aprobación conserva `requested_role` como historial de la solicitud.

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
- `match_duration`: duración reglamentaria del partido en minutos enteros positivos.
- `resolution_methods`: lista JSONB ordenada de métodos permitidos para resolver empates.

`normalized_name` tiene una restricción única. Por eso `Fútbol`, `FUTBOL`, `futBol` y
`fútbol` se consideran el mismo deporte incluso ante solicitudes concurrentes.

`max_players`, `match_duration` y `resolution_methods` son inmutables desde la creación.
Cada método de resolución contiene únicamente `code` y `name`; el código usa
`snake_case` y no puede repetirse, y los nombres tampoco pueden duplicarse ignorando
mayúsculas y espacios.

`Sport` no tiene relación con `User` ni `AuthSession`. La autorización determina quién
puede ejecutar sus operaciones HTTP.

## Autenticación

- Access token: duración de 15 minutos.
- Refresh token: duración máxima de 30 días.
- Transporte: encabezado `Authorization` con esquema `Bearer`.

```http
Authorization: Bearer TOKEN
```

Cada rotación invalida el refresh token anterior. Si se reutiliza, el servidor revoca la
sesión persistida y rechaza todos sus tokens.

El claim `role` del access token se conserva por compatibilidad, pero no autoriza por sí
solo. En cada endpoint de negocio se consulta el rol y `is_active` actuales en
PostgreSQL. Una aprobación, reducción de permisos o deshabilitación afecta de inmediato
a tokens ya emitidos.

### Manejo de tokens en el frontend

El login y el refresh devuelven siempre un par formado por `access_token` y
`refresh_token`. El frontend debe tratarlos como secretos: no mostrarlos, registrarlos en
logs ni incluirlos en URLs. La API los transporta como JSON y no impone una tecnología
de almacenamiento del lado del cliente.

Reglas obligatorias para la renovación:

1. Usar el access token para las solicitudes normales.
2. Cuando una solicitud falle con `401 token_expired`, realizar una sola llamada a
   `POST /auth/refresh` con el refresh token actual.
3. Reemplazar conjuntamente ambos tokens por el nuevo par antes de repetir solicitudes.
4. No volver a utilizar el refresh token anterior: el servidor lo considera un posible
   robo y revoca toda la sesión.
5. Evitar refresh paralelos para una misma sesión. Las solicitudes simultáneas deben
   esperar una única renovación compartida.
6. Si el refresh responde `401`, eliminar ambos tokens y enviar al usuario al login.
7. Un `403` no se soluciona renovando tokens: representa falta de aprobación, cuenta
   deshabilitada o rol insuficiente.

El backend permite varias sesiones independientes por usuario. Cerrar una sesión no
revoca automáticamente las demás.

## Flujos que debe implementar el frontend

### Registro y espera de aprobación

```text
Formulario de registro
        ↓
POST /auth/signup
        ↓ 201
POST /auth/login
        ↓ tokens
GET /auth/me
        ↓
role = user ──→ pantalla de solicitud pendiente
```

1. El formulario ofrece únicamente `referee` y `federation_delegate` como
   `requested_role`.
2. El frontend nunca envía el campo `role`.
3. Después del registro puede iniciar sesión con las credenciales recién creadas.
4. Como el login solo devuelve tokens, debe consultar `/auth/me` para obtener el estado.
5. Mientras `role = user`, debe mostrar la pantalla de espera y no ofrecer recursos de
   negocio.
6. El frontend puede volver a consultar `/auth/me` para actualizar la interfaz. Una
   aprobación tiene efecto sobre la sesión existente y no exige un nuevo login.

### Inicio y restauración de una sesión

```text
POST /auth/login → guardar tokens → GET /auth/me → construir sesión de UI
```

La decisión de interfaz se toma en este orden:

1. Si no hay tokens válidos, mostrar login.
2. Si `role = user`, mostrar estado pendiente.
3. Si `is_active = false`, mostrar estado de cuenta deshabilitada.
4. Si la cuenta está activa, habilitar las pantallas correspondientes a `role`.

El frontend puede ocultar menús por experiencia de usuario, pero el backend siempre
vuelve a validar el rol y el estado.

### Aprobación administrativa

```text
GET /users?role=user
        ↓
seleccionar solicitud
        ↓
POST /users/{id}/approve
        ↓
actualizar o quitar la fila pendiente
```

El administrador no elige el rol durante la aprobación. El backend utiliza el
`requested_role` guardado. Si otro administrador ya revisó la misma cuenta, la segunda
operación recibe `409 user_not_pending` y la tabla debe recargarse.

### Rechazo, deshabilitación y rehabilitación

- Para rechazar una solicitud todavía pendiente, usar `DELETE /users/{id}`.
- Para bloquear un árbitro conservando su identidad histórica, usar
  `POST /users/{id}/disable`.
- Para restaurar una cuenta aprobada, usar `POST /users/{id}/enable`.
- No intentar eliminar árbitros: el backend responde
  `409 active_user_delete_forbidden`.
- La política actual permite eliminar o deshabilitar delegados federativos y
  administradores, por lo que la interfaz debe pedir confirmación explícita.

### Gestión de deportes

1. Consultar `GET /sports` al abrir la pantalla administrativa.
2. Crear enviando juntos `name`, `max_players`, `match_duration` y
   `resolution_methods`.
3. Conservar el orden de `resolution_methods` para mostrarlo como fue configurado.
4. Al editar, enviar únicamente `name`; los otros campos son inmutables.
5. Tras crear, editar o eliminar, actualizar el estado local o volver a consultar la
   lista.

### Cierre de sesión

1. Enviar el access token o refresh token actual a `DELETE /auth/logout`.
2. Al recibir `204`, eliminar ambos tokens y todos los datos de sesión del frontend.
3. Si el token ya está vencido o revocado y el backend responde `401`, limpiar igualmente
   el estado local.

## API de autenticación

### `POST /auth/signup`

Registra una cuenta pendiente. El backend asigna siempre `role = user`; el cliente solo
puede solicitar `referee` o `federation_delegate`.

- Autorización: ninguna.
- Parámetros: ninguno.
- Encabezado: `Content-Type: application/json`.

```json
{
  "name": "Ana Example",
  "birthdate": "1995-04-20",
  "email": "ana@example.com",
  "password": "una-contraseña-segura",
  "requested_role": "referee"
}
```

Respuesta exitosa: `201 Created`.
Enviar `role`, omitir `requested_role` o solicitar otro rol devuelve `422`.

```json
{
  "message": "User created successfully.",
  "user": {
    "id": 12,
    "name": "Ana Example",
    "birthdate": "1995-04-20",
    "role": "user",
    "requested_role": "referee",
    "is_active": true,
    "email": "ana@example.com",
    "creation_date": "2026-08-31T18:30:00+00:00"
  }
}
```

### `POST /auth/login`

Valida credenciales, crea una sesión y devuelve ambos tokens.

- Autorización: ninguna.
- Parámetros: ninguno.
- Encabezado: `Content-Type: application/json`.

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

- Autorización: Bearer con `refresh_token`.
- Parámetros: ninguno.
- Cuerpo: ninguno.

Respuesta exitosa: `200 OK`.

```json
{
  "access_token": "nuevo-access-token",
  "refresh_token": "nuevo-refresh-token",
  "token_type": "Bearer",
  "access_expires_in": 900
}
```

El cliente debe reemplazar los dos tokens anteriores. No debe mezclar un access token
nuevo con un refresh token viejo.

### `DELETE /auth/logout`

Acepta un access o refresh token, revoca la sesión y responde `204 No Content`.

- Autorización: Bearer con access o refresh token.
- Parámetros: ninguno.
- Cuerpo: ninguno.
- Respuesta exitosa: `204`, sin cuerpo.

### `GET /auth/me`

Requiere un access token y devuelve el usuario autenticado, incluyendo `role`,
`requested_role` e `is_active` para que el frontend represente los estados pendiente o
deshabilitado.

- Autorización: Bearer con `access_token`.
- Parámetros: ninguno.
- Cuerpo: ninguno.

Respuesta exitosa: `200 OK`.

```json
{
  "user": {
    "id": 12,
    "name": "Ana Example",
    "birthdate": "1995-04-20",
    "role": "user",
    "requested_role": "referee",
    "is_active": true,
    "email": "ana@example.com",
    "creation_date": "2026-08-31T18:30:00+00:00"
  }
}
```

Los usuarios pendientes o deshabilitados pueden usar estos endpoints de autenticación,
pero reciben `403` al intentar acceder a recursos de negocio.

## API de administración de cuentas

Todos estos endpoints requieren el rol actual `administrator`.

### `GET /users`

Lista cuentas sin exponer hashes ni datos de sesión. Acepta los filtros opcionales
`role` y `requested_role`.

- Autorización: Bearer con access token de administrador activo.
- Cuerpo: ninguno.
- `role`: opcional; acepta `user`, `referee`, `federation_delegate` o `administrator`.
- `requested_role`: opcional; acepta `referee` o `federation_delegate`.

Para revisar solicitudes de árbitros pendientes:

```http
GET /users?role=user&requested_role=referee
```

Respuesta exitosa: `200 OK`.

```json
{
  "users": [
    {
      "id": 12,
      "name": "Ana Example",
      "birthdate": "1995-04-20",
      "role": "user",
      "requested_role": "referee",
      "is_active": true,
      "email": "ana@example.com",
      "creation_date": "2026-08-31T18:30:00+00:00"
    }
  ]
}
```

### `POST /users/{id}/approve`

Aprueba una cuenta con `role = user` y le asigna exactamente su `requested_role`. Una
segunda aprobación o una cuenta ya revisada devuelve `409 user_not_pending`.

- Autorización: Bearer con access token de administrador activo.
- Parámetro de ruta: `id`, entero del usuario.
- Cuerpo: ninguno; no enviar un rol.
- Respuesta exitosa: `200 OK` con `{ "user": User }` ya actualizado.

### `DELETE /users/{id}`

Elimina una cuenta pendiente. También permite eliminar delegados federativos y
administradores según la política vigente. Los árbitros no pueden eliminarse; deben
deshabilitarse para preservar su identidad histórica.

- Autorización: Bearer con access token de administrador activo.
- Parámetro de ruta: `id`, entero del usuario.
- Cuerpo: ninguno.
- Respuesta exitosa: `204 No Content`.

### `POST /users/{id}/disable`

Deshabilita una cuenta aprobada conservando su rol. Sus sesiones pueden seguir usando
autogestión de autenticación, pero los recursos de negocio devuelven
`403 account_disabled`.

- Autorización: Bearer con access token de administrador activo.
- Parámetro de ruta: `id`, entero del usuario.
- Cuerpo: ninguno.
- Respuesta exitosa: `200 OK` con `{ "user": User }` e `is_active = false`.

### `POST /users/{id}/enable`

Rehabilita una cuenta aprobada previamente deshabilitada y restaura sus permisos según
el rol actual.

- Autorización: Bearer con access token de administrador activo.
- Parámetro de ruta: `id`, entero del usuario.
- Cuerpo: ninguno.
- Respuesta exitosa: `200 OK` con `{ "user": User }` e `is_active = true`.

## API de deportes — HU01

Todos los endpoints requieren un access token y que el usuario actual en PostgreSQL sea
un `administrator` activo. Un usuario sin token recibe `401 Unauthorized`; uno pendiente,
deshabilitado o con otro rol recibe `403 Forbidden`. No existen filtros de búsqueda.

### `POST /sports`

Crea un deporte.

- Autorización: Bearer con access token de administrador activo.
- Parámetros: ninguno.
- Encabezado: `Content-Type: application/json`.

```json
{
  "name": "FÚTBOL",
  "max_players": 11,
  "match_duration": 90,
  "resolution_methods": [
    {"code": "penalty", "name": "penales"},
    {"code": "overtime", "name": "tiempo extra"}
  ]
}
```

Respuesta exitosa: `201 Created`.

```json
{
  "sport": {
    "id": 1,
    "name": "Fútbol",
    "max_players": 11,
    "match_duration": 90,
    "resolution_methods": [
      {"code": "penalty", "name": "penales"},
      {"code": "overtime", "name": "tiempo extra"}
    ]
  }
}
```

Un nombre equivalente devuelve `409 Conflict`. Un nombre inválido o `max_players`
fuera de `1..20`, una duración no positiva o una configuración de resolución inválida
devuelve `422 Unprocessable Content`.

### `GET /sports`

Devuelve todos los deportes ordenados por `id`.

- Autorización: Bearer con access token de administrador activo.
- Parámetros, filtros y cuerpo: ninguno.

```json
{
  "sports": [
    {
      "id": 1,
      "name": "Fútbol",
      "max_players": 11,
      "match_duration": 90,
      "resolution_methods": [
        {"code": "penalty", "name": "penales"},
        {"code": "overtime", "name": "tiempo extra"}
      ]
    }
  ]
}
```

Respuesta exitosa: `200 OK`.

### `GET /sports/{id}`

Devuelve un deporte. Si no existe, responde `404 Not Found`.

- Autorización: Bearer con access token de administrador activo.
- Parámetro de ruta: `id`, entero del deporte.
- Cuerpo: ninguno.
- Respuesta exitosa: `200 OK` con `{ "sport": Sport }`.

### `PUT /sports/{id}`

Modifica únicamente el nombre.

- Autorización: Bearer con access token de administrador activo.
- Parámetro de ruta: `id`, entero del deporte.
- Encabezado: `Content-Type: application/json`.

```json
{
  "name": "Fútbol sala"
}
```

`max_players`, `match_duration` y `resolution_methods` son inmutables después de la
creación. Intentar modificar cualquiera devuelve `422 Unprocessable Content`. Una
modificación válida del nombre responde `200 OK`.

### `DELETE /sports/{id}`

Elimina un deporte y responde `204 No Content`. Si no existe, responde `404 Not Found`.

- Autorización: Bearer con access token de administrador activo.
- Parámetro de ruta: `id`, entero del deporte.
- Cuerpo: ninguno.

### Contrato futuro de resultados empatados

No se implementaron partidos ni competiciones en este cambio. El contrato acordado para
una futura entidad `Match` es que `resolution_method` será un string nullable y, cuando
haya empate, deberá contener un `code` incluido en
`Match.competition.sport.resolution_methods`. No habrá una tabla adicional de métodos.

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

### Códigos de error que debe interpretar el frontend

| `error.code` | Estado | Acción recomendada |
| --- | --- | --- |
| `invalid_request` | `400` | Corregir el JSON o enviar el cuerpo requerido. |
| `authentication_required` | `401` | Solicitar login si no existe una sesión recuperable. |
| `invalid_token` | `401` | Limpiar la sesión y solicitar login. |
| `token_expired` | `401` | Intentar una única rotación con el refresh token. |
| `session_revoked` | `401` | Limpiar tokens y solicitar login. |
| `refresh_token_reused` | `401` | Limpiar tokens; toda la sesión fue revocada. |
| `invalid_credentials` | `401` | Mostrar credenciales incorrectas sin revelar cuál campo falló. |
| `approval_required` | `403` | Mostrar la pantalla de solicitud pendiente. |
| `account_disabled` | `403` | Mostrar la pantalla de cuenta deshabilitada. |
| `role_forbidden` | `403` | Ocultar el recurso o mostrar acceso denegado. |
| `user_not_found` | `401` o `404` | Limpiar sesión si era el usuario actual; recargar si era administrado. |
| `sport_not_found` | `404` | Quitar o recargar el recurso solicitado. |
| `email_conflict` | `409` | Informar que el email ya está registrado. |
| `sport_name_conflict` | `409` | Informar que ya existe un nombre equivalente. |
| `user_not_pending` | `409` | Recargar la lista de solicitudes. |
| `active_user_delete_forbidden` | `409` | Ofrecer deshabilitar al árbitro. |
| `account_state_conflict` | `409` | Recargar el estado de la cuenta. |
| `invalid_requested_role` | `422` | Limitar la selección a los dos roles solicitables. |
| `validation_error` | `422` | Mostrar el mensaje junto al formulario correspondiente. |
| `immutable_field` | `422` | No enviar campos deportivos inmutables al editar. |
| `service_unavailable` | `503` | Mostrar un error temporal y permitir reintentar. |
| `authentication_unavailable` | `503` | Informar que el login no está disponible temporalmente. |

Los mensajes del backend están en inglés y sirven como ayuda legible. La lógica del
frontend y sus traducciones deben depender de `error.code`, no de comparar el texto de
`error.message`.

Los estados siguen la
[referencia HTTP de MDN](https://developer.mozilla.org/es/docs/Web/HTTP/Reference/Status).

## Funcionalidades todavía no implementadas

Esta versión no expone endpoints de competiciones, equipos, jugadores, planteles,
partidos, sanciones ni reconocimiento facial. Tampoco existe una ruta pública para crear
administradores o modificar libremente roles. El primer administrador se crea mediante
el comando CLI documentado en el README.

## Persistencia y migraciones

PostgreSQL es la fuente persistente. `db.create_all()` se usa únicamente para una base
vacía mediante `init-db`. Las tablas existentes cambian mediante migraciones versionadas.

- `78feb1bb58cd`: agrega sesiones rotativas y convierte `birthdate` a `DATE`.
- `3e22b5f59faa`: agrega `sports` y sus restricciones de unicidad y rango.
- `a6c8f4d2190e`: agrega aprobación de cuentas, estado activo, roles vigentes, duración
  de partidos y métodos JSONB de resolución.
