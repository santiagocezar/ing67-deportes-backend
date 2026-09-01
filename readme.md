# Backend de Sports App

## Requisitos

- Python 3
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

Copiar `app/.env.example` como `app/.env` y completar la configuración local:

```env
SQLALCHEMY_DATABASE_URI=postgresql://USER:PASSWORD@HOST:PORT/DATABASE
JWT_SECRET_KEY=REEMPLAZAR_POR_UNA_CLAVE_ALEATORIA_DE_AL_MENOS_32_BYTES
CORS_ORIGINS=http://localhost:5173
```

Generar una clave JWT segura:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

El archivo `app/.env` contiene valores locales y no debe agregarse a Git.

## Preparación de la base de datos

Para una base nueva y vacía:

```powershell
python -m flask --app app init-db
```

Para aplicar las migraciones pendientes sobre una base existente:

```powershell
python -m flask --app app upgrade-db
```

Después de modificar un modelo de SQLAlchemy, generar y revisar una migración antes de
aplicarla:

```powershell
python -m flask --app app db migrate -m "descripción del cambio"
python -m flask --app app upgrade-db
```

`init-db` rechaza bases que ya contienen tablas de la aplicación. Las migraciones
autogeneradas siempre deben revisarse antes de ejecutarse.

## Carga de deportes iniciales

Después de crear o actualizar las tablas, ejecutar el script SQL desde la raíz del
repositorio:

```powershell
psql -U USER -p PORT -d DATABASE -f scripts/initialize_sports.sql
```

Por ejemplo, si PostgreSQL utiliza el usuario `postgres`, el puerto `5433` y la base
`sportsapp_db`:

```powershell
psql -U postgres -p 5433 -d sportsapp_db -f scripts/initialize_sports.sql
```

El script precarga Fútbol con 11 jugadores y Básquet con 5. Puede ejecutarse más de una
vez porque ignora los nombres normalizados que ya existen.

## Creación del primer administrador

Crear una cuenta administradora desde la consola:

```powershell
python -m flask --app app create-admin
```

El comando solicita nombre, fecha de nacimiento, email y contraseña. La contraseña se
ingresa de forma oculta y requiere confirmación. Después, el administrador obtiene sus
tokens usando el endpoint normal de login.

## Ejecución

Iniciar el servidor de desarrollo:

```powershell
python -m flask --app app run --debug
```

La API quedará disponible en:

```text
http://localhost:5000
```

El frontend Vue utiliza normalmente:

```text
http://localhost:5173
```

## Pruebas

Ejecutar las pruebas unitarias:

```powershell
python -m unittest discover -s tests -v
```

## Documentación

El contrato de la API, las relaciones del modelo, el flujo de autenticación y los
errores HTTP se encuentran en [documentation.md](documentation.md).
