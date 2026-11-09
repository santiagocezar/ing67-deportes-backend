# HU07 — Importación de planteles de temporadas anteriores

## Objetivo y alcance

Permitir que un administrador, al editar los participantes de una competición existente, sustituya el plantel seleccionado de uno o más equipos ya inscritos por el plantel histórico de esos mismos equipos en una competición finalizada. La importación prepara cambios en el formulario; la persistencia ocurre al guardar todos los participantes de la competición destino.

Esta HU no crea competiciones, equipos, jugadores ni árbitros. Reutiliza los identificadores existentes y no modifica el plantel histórico de la competición de origen. No copia sanciones: las sanciones vigentes ya son globales y conservan su estado y sus reglas de cumplimiento.

## Decisiones de negocio confirmadas

- El administrador parte de una competición destino existente y habilitada. La importación solo se ofrece antes de que se genere cualquier partido.
- Se elige una competición de origen finalizada. El deporte y el género deben coincidir con el destino.
- Solo se ofrecen planteles históricos de equipos que ya están inscritos en el destino. La cantidad y la identidad de los equipos del destino no cambian.
- Un plantel es elegible únicamente si **todos** sus jugadores históricos siguen habilitados, pertenecen actualmente al mismo equipo, cumplen el deporte y género del destino y no tienen sanciones globales activas al evaluar la opción. También debe cumplir el mínimo y máximo de jugadores del deporte. Si falla cualquiera de estas condiciones, el plantel completo no se ofrece; no se importan subconjuntos ni se informa individualmente qué jugador lo volvió inelegible.
- Los árbitros del destino permanecen intactos. La relación actual es con la competición, no con cada equipo o plantel.
- Puede haber selecciones parciales o vacías de jugadores en el formulario antes de guardar. Una competición persistida sigue exigiendo planteles completos y válidos.
- Reimportar un plantel para el mismo equipo sustituye la selección temporal de ese equipo; no agrega una segunda inscripción ni duplica jugadores.

## Flujo de usuario

1. En la edición de participantes del destino, el administrador elige **Importar planteles de temporada anterior**.
2. El sistema muestra competiciones de origen finalizadas compatibles. Tras elegir una, muestra solo los planteles elegibles de equipos que también están en el destino. Cada opción identifica competición, equipo y jugadores que reemplazarán la selección actual.
3. El administrador selecciona uno o varios equipos y confirma la importación. El formulario sustituye la selección de jugadores únicamente para esos equipos. Los demás equipos y los árbitros conservan sus selecciones actuales. La acción informa qué equipos se prepararon para importar.
4. El administrador revisa los participantes y guarda. Se envía el conjunto completo mediante `PUT /competitions/{id}/participants`; el backend valida de nuevo todas las reglas y persiste la sustitución en una sola transacción. Si falla, no se guarda ninguna parte y el formulario conserva los cambios para corregirlos.

La disponibilidad mostrada es una vista del momento de consulta. Si cambian membresías, habilitaciones o sanciones antes de guardar, la validación del guardado prevalece. La interfaz debe explicar el rechazo sin afirmar que hubo una importación parcial.

## Contrato técnico propuesto

### Consulta de orígenes

Reutilizar `GET /competitions` con `lifecycle_status=finished`, `sport_id` y `gender`; mostrar solo competiciones compatibles con el destino. No considerar el destino como origen. La lista se pagina con el contrato actual.

### Vista de planteles importables

Agregar una consulta exclusiva para administradores:

```http
GET /competitions/{competition_id}/roster-import-options?source_competition_id={source_id}
```

`competition_id` identifica el destino. La respuesta `200` devuelve los identificadores y nombres de origen y destino y una lista `teams` de opciones elegibles; cada opción contiene `team_id`, nombre del equipo y la lista de `player_id` y nombre de los jugadores del plantel histórico. La lista vacía es un estado válido. No devolver sanciones ni sus detalles en esta respuesta.

La consulta verifica que el destino existe, está habilitado y no tiene partidos; que el origen es distinto, existe, está finalizado y tiene el mismo deporte y género; y que cada opción satisface la elegibilidad descrita arriba. La comprobación de sanciones debe usar la reconciliación vigente del backend, sin depender de datos en caché del navegador. Debe procesar jugadores por lotes para evitar una consulta por jugador.

Usar contratos Pydantic v2 para ruta, query y respuesta, rechazar parámetros desconocidos y representar el endpoint en OpenAPI 3.1. Aplicar el esquema común de errores: `401` sin autenticación, `403` sin rol administrador, `404` para identificadores inexistentes, `409` si el destino está deshabilitado o ya tiene partidos o si el origen no cumple el estado requerido, y `422` para parámetros válidos pero incompatibles o fuera de contrato. Conservar errores seguros, sin detalles internos de base de datos.

### Guardado

Reutilizar `PUT /competitions/{id}/participants`. El frontend envía todos los equipos del destino con las selecciones de jugadores vigentes en el formulario y los `referee_ids` originales. El backend conserva sus validaciones actuales: mismo número de equipos, entre 4 y 16 y par, un árbitro distinto por cada dos equipos, planteles dentro del tamaño permitido, membresía vigente, deporte, género, habilitación, sanciones activas y un solo equipo por jugador en el destino. No se introduce una operación de guardado parcial ni una tabla de importaciones.

Si el formulario deja un plantel incompleto o cualquier otra regla falla, se rechaza el guardado completo. Si un jugador ya aparece en el plantel de otro equipo del destino, no se omite silenciosamente: se muestra el conflicto y se permite corregir la selección antes de guardar.

## Estados de interfaz

- **Carga:** mostrar progreso al buscar orígenes y opciones; impedir una confirmación con datos incompletos.
- **Sin opciones:** explicar que no hay competiciones finalizadas compatibles o que ningún plantel del origen elegido reúne las condiciones actuales.
- **Éxito local:** mostrar los equipos cuyas selecciones fueron sustituidas en el formulario y dejar visible que falta guardar.
- **Guardado exitoso:** refrescar el detalle del destino para mostrar los nuevos snapshots persistidos.
- **Error:** mostrar el motivo del rechazo del backend y conservar las selecciones no guardadas. Si la consulta de opciones caduca por cambios de estado, permitir recargarla.

## Criterios de aceptación

1. **Origen válido.** Dado un administrador que edita una competición sin partidos, cuando elige una competición finalizada del mismo deporte y género, entonces ve únicamente planteles históricos completos cuyos jugadores siguen siendo elegibles y cuyos equipos ya están inscritos en el destino.
2. **Origen inválido.** Dado un origen no finalizado, incompatible o igual al destino, cuando se consultan opciones, entonces la consulta se rechaza y no cambia ninguna competición.
3. **Sustitución.** Dado un equipo del destino con una selección temporal de jugadores, cuando se importa su plantel elegible, entonces su selección se reemplaza por los jugadores históricos elegidos. Los demás equipos y los árbitros quedan como estaban.
4. **Repetición.** Dado un plantel ya seleccionado en el formulario, cuando se importa de nuevo para el mismo equipo, entonces existe una única selección para ese equipo, sin jugadores duplicados.
5. **Inelegibilidad.** Dado un plantel histórico con al menos un jugador deshabilitado, desvinculado del equipo o con sanción global activa, cuando se consultan las opciones, entonces ese plantel completo no aparece. La sanción original conserva su estado y continúa cumpliéndose conforme a sus reglas globales.
6. **Persistencia válida.** Dado un formulario que cumple todas las reglas de participantes, cuando el administrador guarda, entonces el backend sustituye atómicamente los planteles del destino y conserva intactos origen, equipos, jugadores, sanciones y árbitros.
7. **Persistencia inválida.** Dado un formulario con plantel insuficiente, jugador repetido entre equipos, sanción activa sobreviniente u otra infracción vigente, cuando se guarda, entonces el backend rechaza toda la operación, explica el motivo y no persiste cambios parciales.
8. **Bloqueo por partidos.** Dado un destino con al menos un partido generado, cuando se intenta consultar o guardar la importación, entonces se rechaza sin alterar participantes históricos.
9. **Autorización.** Dado un usuario sin rol administrador, cuando intenta consultar opciones o guardar participantes, entonces recibe el rechazo de autorización existente.

## Implementación y verificación esperadas

- Backend: consulta de opciones en `routes/`, `schemas/` y `services/` de competiciones; reutilizar la reconciliación de sanciones y las validaciones de participantes. No cambiar el esquema de PostgreSQL.
- Frontend: incorporar el selector y la sustitución del borrador en la edición de participantes, mediante la capa API existente.
- Pruebas: opciones elegibles e inelegibles, estado y compatibilidad del origen, destino bloqueado, permisos, sustitución sin duplicados, cambio de elegibilidad entre consulta y guardado y rollback del guardado fallido.
- Documentación de la implementación: actualizar `documentation.md` y regenerar `docs/openapi.json` al agregar el endpoint. Mantener el DER sin cambios si no cambia el modelo persistente.

## Relación con la redacción original

Los casos de «clonar equipos», agregar equipos al destino y arrastrar/copiar sanciones quedan sustituidos por las decisiones anteriores: HU07 importa **planteles de equipos ya inscritos** y conserva sanciones globales. La cantidad par de equipos y la proporción de árbitros siguen siendo invariantes del destino y se verifican al guardar, sin alterar el número de equipos.
