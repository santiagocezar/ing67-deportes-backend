# Backend de Sports App

## Requisitos

- Python 3.10 o posterior
- PostgreSQL
- Una base de datos vacía para la primera instalación

## Instalación local

Crear y activar un entorno virtual:

```powershell
python -m venv flask-env
.\flask-env\Scripts\Activate.ps1
```

Instalar las dependencias:

```powershell
python -m pip install -r requirements.txt
```

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

## Documentación funcional

Los flujos, endpoints, esquemas y errores públicos se explican en
[documentation.md](documentation.md).
