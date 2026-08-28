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
flask --app app init-db
```

Para aplicar las migraciones pendientes sobre una base existente:

```powershell
flask --app app upgrade-db
```

Después de modificar un modelo de SQLAlchemy, generar y revisar una migración antes de
aplicarla:

```powershell
flask --app app db migrate -m "descripción del cambio"
flask --app app upgrade-db
```

`init-db` rechaza bases que ya contienen tablas de la aplicación. Las migraciones
autogeneradas siempre deben revisarse antes de ejecutarse.

## Ejecución

Iniciar el servidor de desarrollo:

```powershell
flask --app app run --debug
```

La API quedará disponible en:

```text
http://localhost:5000
```

El frontend Vue utiliza normalmente:

```text
http://localhost:5173
```

## Documentación

El contrato de la API, las relaciones del modelo, el flujo de autenticación y los
errores HTTP se encuentran en [documentation.md](documentation.md).
