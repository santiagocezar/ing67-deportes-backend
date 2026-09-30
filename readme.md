# Backend de Sports App

## Requisitos

- Python 3.10 o posterior
- PostgreSQL
- Una base de datos vacía para la primera instalación

## Instalación local

Crear y activar un entorno virtual:

```powershell
$backendEnv = "E:\INGENIERIA EN SISTEMAS\PROJECTS\ing67-deportes-backend-env"
python -m venv $backendEnv
& "$backendEnv\Scripts\Activate.ps1"
```

En Windows, mantener el entorno fuera de rutas con caracteres no ASCII. La ubicación
anterior es el entorno local aprobado para este proyecto; en otro equipo debe elegirse
una ruta ASCII equivalente fuera del repositorio.

Instalar las dependencias:

```powershell
python -m pip install -r requirements.txt
```

La validación de fotos usa `face_recognition`, que depende de `dlib`. En Windows,
`face_recognition` no ofrece soporte oficial y la distribución oficial actual de
`dlib` puede requerir CMake y Visual Studio con las herramientas de C++ para compilarse.
No sustituir `dlib` por wheels de terceros sin una aprobación explícita del equipo.
`setuptools` se mantiene por debajo de la versión 81 porque `face_recognition_models`
todavía carga sus modelos mediante la API heredada `pkg_resources`. En Windows, crear
el entorno virtual en una ruta que sólo contenga caracteres ASCII: la carga nativa de
los archivos de modelo de `dlib` puede fallar si `site-packages` contiene caracteres
como `Ñ`.

Copiar `app/.env.example` como `app/.env` y completar la configuración:

```env
SQLALCHEMY_DATABASE_URI=postgresql://USER:PASSWORD@HOST:PORT/DATABASE
JWT_SECRET_KEY=REEMPLAZAR_POR_UNA_CLAVE_ALEATORIA_DE_AL_MENOS_32_BYTES
CORS_ORIGINS=http://localhost:5173
API_DOCS_ENABLED=true
```

Generar una clave JWT segura:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

`app/.env` es local y nunca debe agregarse a Git. En producción se debe configurar
`API_DOCS_ENABLED=false` para no publicar OpenAPI ni Swagger UI.

Las fotos de jugadores se guardan como archivos en `instance/player_photos`, dentro del
backend y fuera de Git. Para usar otra carpeta, definir `PLAYER_PHOTOS_DIR` en
`app/.env` con una ruta absoluta. Esas fotos son datos biométricos: no deben
versionarse ni compartirse, y deben respaldarse junto con la base porque PostgreSQL
sólo guarda su referencia.

El almacenamiento local está aprobado únicamente para una instancia persistente del
backend; no funciona correctamente con réplicas que no compartan el mismo filesystem ni
con discos efímeros. Cada jugador habilitado conserva como máximo tres fotos y al
deshabilitarlo se eliminan de forma permanente.

Si el proceso se interrumpe durante una escritura o eliminación, detener todas las
instancias del backend y ejecutar:

```powershell
python -m flask --app app reconcile-player-photos
```

El comando es idempotente: promueve archivos `.pending` con fila confirmada, restaura
`.deleting` cuya fila todavía existe, elimina estados y archivos huérfanos e informa
filas sin archivo. Debe ejecutarse con el mismo `PLAYER_PHOTOS_DIR` y la misma base que
la aplicación.

## Preparación de la base de datos

Para una base nueva y vacía:

```powershell
python -m flask --app app init-db
```

Para aplicar migraciones pendientes sobre una base existente:

```powershell
python -m flask --app app upgrade-db
```

Después de modificar un modelo SQLAlchemy, generar y revisar una migración antes de
aplicarla:

```powershell
python -m flask --app app db migrate -m "descripción del cambio"
python -m flask --app app upgrade-db
```

`init-db` rechaza bases que ya contienen tablas de la aplicación. Las migraciones
autogeneradas siempre deben revisarse antes de ejecutarse.

## Carga de deportes iniciales

Ejecutar el script desde la raíz del repositorio:

```powershell
psql -U USER -p PORT -d DATABASE -f scripts/initialize_sports.sql
```

El script precarga Fútbol con un plantel máximo de 22 jugadores y 11 simultáneos en
cancha, y Básquet con 15 y 5 respectivamente. Puede ejecutarse más de una vez porque
ignora nombres normalizados existentes. Primero deben estar aplicadas todas las
migraciones.

## Creación del primer administrador

```powershell
python -m flask --app app create-admin
```

El comando aplica el mismo esquema Pydantic que el registro HTTP y solicita nombre,
fecha de nacimiento, email y contraseña.

## Ejecución

```powershell
python -m flask --app app run --debug
```

- Backend: `http://localhost:5000`
- Frontend Vue: `http://localhost:5173`
- Contrato OpenAPI: `http://localhost:5000/openapi.json`
- Swagger UI: `http://localhost:5000/swagger`

## Exportación de OpenAPI y Hoppscotch

Regenerar el contrato versionado cada vez que cambie la API pública:

```powershell
python -m flask --app app export-openapi
```

El comando actualiza únicamente `docs/openapi.json` con formato determinista. En
Hoppscotch se puede importar ese archivo desde `Import > OpenAPI`. Durante el desarrollo
también se puede importar `http://localhost:5000/openapi.json` con el backend iniciado y
la documentación habilitada.

No se mantiene una colección manual paralela: el contrato OpenAPI generado es la fuente
de verdad para Hoppscotch.

## Diagrama de base de datos

El DER actual está definido en `docs/erd.puml`. Si PlantUML está instalado, se puede
renderizar desde la raíz del repositorio con:

```powershell
plantuml docs/erd.puml
```

El archivo se actualiza junto con cada cambio de modelos o relaciones implementadas.

## Pruebas

```powershell
python -m unittest discover -s tests -v
```

Las pruebas de integración de fotos requieren una base PostgreSQL desechable y no se
ejecutan contra la base de desarrollo por defecto. Configurar una URL exclusiva de
pruebas y ejecutar:

```powershell
$env:PLAYER_PHOTO_TEST_DATABASE_URL = "postgresql://USER:PASSWORD@HOST:PORT/DISPOSABLE_TEST_DATABASE"
python -m unittest discover -s tests -p "test_player_photos_postgresql.py" -v
Remove-Item Env:PLAYER_PHOTO_TEST_DATABASE_URL
```

La suite crea y elimina un esquema aleatorio dentro de esa base. No usar una base
compartida ni de producción.

## Documentación funcional

Los flujos, endpoints, esquemas y errores públicos se explican en
[documentation.md](documentation.md).
