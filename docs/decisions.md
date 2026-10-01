# Decisiones

El índice de todo lo que está decidido. Cada fila dice qué la hace cumplir; una fila con `—` en
la última columna se puede romper sin que nada avise, y por eso se ve. La prosa y las mediciones
viven en el documento dueño; esta tabla es lo que las encuentra. Los ids se acuñan una vez con
`python .agents/tools/bundle.py id d "<la decisión>"` y nunca se recalculan.

Una decisión se toma **una vez**: si se reabre sin un hecho nuevo, la respuesta es esta tabla.

## Entorno y gate

| Id | Decisión | Por qué | Se cumple en |
|---|---|---|---|
| d-7985ee-7bc399 | Una sola versión de Python, 3.12, en `docker/Dockerfile`, `.mypy.ini` y el entorno de hatch | `type X = …` (PEP 695) exige 3.12; en 3.12 las anotaciones se evalúan al definir, así que un nombre sin importar rompe el import (en 3.14 no, por PEP 649: probar en otra versión esconde el error) | `scripts/audit.py` `python_versions` |
| d-7985ee-cd8f73 | Cada dependencia de `requirements.txt` lleva versión exacta y aparece una vez | sin versión, CI y la imagen instalan lo más nuevo cada vez y una release externa rompe el gate o el despliegue sin cambiar el repositorio; se sube a propósito, de a una, con CI. Adoptada en S1 siguiendo la recomendación de `plan-de-trabajo.md` §1 (decisión 4) | `scripts/audit.py` `pinned_requirements` |
| d-7985ee-d0f29c | `requirements.txt` es la única lista de dependencias; hatch la lee con `hatch-requirements-txt` | la imagen instala desde ese archivo; una segunda lista en `pyproject.toml` derivaría | `pyproject.toml` (`dynamic = ["dependencies"]`), por construcción |
| d-7985ee-fd2a8d | El gate es `hatch run gate` y corre en CI en cada push y pull request | antes no había ninguno: los defectos de arranque solo aparecían al desplegar | `.github/workflows/check.yml`; `scripts/check.sh` lo corre local |
| d-7985ee-66658e | mypy en cero errores sobre `main.py`, `src`, `tests` y `scripts` | encontró el `NameError` de arranque y un campo inexistente; con 4 errores al empezar, no hace falta baseline | gate, script `typecheck` |

## Arquitectura

| Id | Decisión | Por qué | Se cumple en |
|---|---|---|---|
| d-7985ee-16ed28 | Los routers solo inyectan servicios; un router nunca inyecta una base de datos ni un cliente HTTP | decisión del dueño (2026-10-01): la lógica y los permisos viven en una capa; 14 inyecciones la saltaban | `scripts/audit.py` `layering`, con `LAYERING_BASELINE` que solo se achica |
| d-7985ee-313583 | La app importa y el contenedor de dependencias cablea cada servicio con los tipos de su constructor | `ModrinthService` recibía la base de modpacks y nada lo notaba | `tests/test_startup.py` |
| d-7985ee-8eb944 | Todo `Document` de Beanie está registrado en `init_beanie` | usar uno sin registrar lanza al primer acceso, no al arrancar | `scripts/audit.py` `registration` (baseline: `PterodactylModpack`) |
| d-7985ee-147399 | Ningún argumento por defecto mutable en el código | `headers: dict = {}` en `HttpAPI._request` acumulaba cabeceras entre llamadas y reenviaba el token de Pterodactyl a otros hosts | `scripts/audit.py` `mutable_defaults`; `tests/test_http_client.py` |
| d-7985ee-05d032 | Un POST, PATCH, PUT o DELETE a un servicio externo no se reintenta sobre cualquier excepción | una respuesta perdida no dice si la acción ocurrió: reintentar puede repetir un apagado o un borrado en un servidor real | `scripts/audit.py` `retried_writes`, solo **aviso** (6 hoy; ver roadmap) |

## Seguridad y configuración

| Id | Decisión | Por qué | Se cumple en |
|---|---|---|---|
| d-7985ee-2e4d10 | La configuración obligatoria falla cerrada y toda variable obligatoria está en `.env.default` | un despliegue sin `SESSION_SECRET` o `MONGODB_URI` debe no arrancar, no arrancar con un default | `getenv` en `src/library/utils`; `scripts/audit.py` `env_template` |
| d-7985ee-8befe2 | Solo usa la API una identidad que esté en un grupo permitido del IdP (`OIDC_ALLOWED_GROUPS`, obligatoria) o cuyo email verificado esté en `OIDC_ALLOWED_EMAILS` | antes, cualquier cuenta del IdP entraba y operaba los servidores. El grupo no exige email verificado porque authentik lo manda en `false` por defecto desde 2025.10 (`research/2026-10-arquitectura.md` §3). Decisión 2 del dueño (2026-10-01) | `src/library/auth/authorization.py`; `tests/test_auth.py` |
| d-7985ee-361ed0 | La identidad de un usuario es `iss` + `sub` del ID token, nunca el email | OIDC Core §5.7: son los únicos claims estables; un emisor puede reusar un email. Los usuarios viejos se migran solos en su primer login | `UsersProviderAuthentik.users_validate`; `UsersDatabase.user_from_provider`; `tests/test_auth.py` |
| d-7985ee-358aa6 | La configuración llega al contenedor montando `.env.yaml` de solo lectura | la app ya lo lee con `load_env`, y `env_file` exige otro formato. Decisión 1 del dueño (2026-10-01) | `docker/docker-compose.yaml` (`create_host_path: false`); `scripts/run.sh` |
| d-7985ee-398653 | Los datos de MongoDB viven en un volumen con nombre, y la imagen tiene versión fija, igual o mayor que la que escribió los datos | con un volumen anónimo, `down` dejaba los datos huérfanos; MongoDB no admite bajar de versión y sube de a una versión mayor | `docker/docker-compose.yaml` (`${MONGO_VERSION:?}`); job `image` de CI |
| d-7985ee-57db37 | `.env.yaml` nunca se versiona y ningún agente lo lee | contiene los tokens de Pterodactyl, Modrinth y OIDC | `.gitignore`; `.claude/settings.json` `Read(./.env.yaml)` denegado |
| d-7985ee-4e4aee | Sin CORS salvo orígenes nombrados en `CORS_ALLOW_ORIGINS`; la cookie de sesión es `SameSite=lax`, `https_only` y dura horas | con comodín y credenciales, Starlette devuelve cualquier `Origin`; `strict` rompe el callback OIDC. Desde S3 (antes: comodín) | `main.py` |

## Proceso

| Id | Decisión | Por qué | Se cumple en |
|---|---|---|---|
| d-7985ee-b4786f | `AGENTS.md` es la única fuente de instrucciones; `CLAUDE.md` solo lo importa | dos archivos escritos a mano divergen | — |
| d-7985ee-8b8db2 | Commits con el usuario como único autor, sin trailers, en Conventional Commits | convención del dueño en todos sus repositorios | — |
