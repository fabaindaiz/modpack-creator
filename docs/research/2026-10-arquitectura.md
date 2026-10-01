# Arquitectura del servicio: trabajos largos, tests, acceso, avisos, UI y observabilidad

**Fecha:** 2026-10-01 · **Método:** fuentes primarias (docs oficiales, repos, PyPI), versiones
comprobadas ese día. Lo que no se pudo verificar dice **SIN VERIFICAR**. Investigación de solo
lectura: nada de esto está implementado. Los ítems que produce están en `docs/roadmap.md`.

## Resumen

El servicio necesita, para lo que viene (revisar actualizaciones periódicamente y actualizar un
servidor con backup → parar → reemplazar → arrancar → verificar), **trabajos durables** que
sobrevivan a un reinicio. La pila actual (FastAPI + MongoDB) alcanza para eso **sin agregar
Redis**: una colección `jobs` con *leases* reclamados atómicamente con `find_one_and_update`. Antes
de eso hacen falta dos cosas baratas: cerrar los huecos de acceso y despliegue, y tests contra un
MongoDB real.

| # | Movimiento | Costo (estimado) | Ítem |
|---|---|---|---|
| 1 | Cerrar acceso y despliegue: lista permitida/grupo + `email_verified`, sesión `https_only`, sin CORS `*`, volumen de Mongo, puertos cerrados | horas | i-7985ee-c64e06, i-7985ee-a77cdc |
| 2 | Tests contra MongoDB real (servicio en CI) | medio día | i-7985ee-055362 |
| 3 | Colección `jobs` con leases + worker y scheduler en el proceso | 1–2 días | ver `docs/roadmap.md` §Operación |
| 4 | `/health` + healthchecks de Compose + logs JSON | horas | ver roadmap |
| 5 | Avisos por webhook de Discord con outbox | medio día | ver roadmap |

## 1. Trabajos largos y periódicos

| Opción | Estado (2026-10) | Necesita | Durable ante reinicio | Varias réplicas |
|---|---|---|---|---|
| `BackgroundTasks` de FastAPI | estable | nada | **no** | no aplica |
| APScheduler 3.11.3 | estable | — (MongoDBJobStore opcional) | con job store | **no** ("job stores must never be shared between schedulers") |
| APScheduler 4.0.0a6 | **alfa**, "do NOT use this release in production" | — | sí | sí |
| arq 0.28.0 | "maintenance only mode" | Redis | sí | sí |
| Taskiq 0.13 + taskiq-mongodb 0.1.0 | el broker de Mongo salió el 2026-09-17 | proceso worker aparte | sí | sí |
| `beanie[queue]` | última versión 2023-11; compatibilidad con Beanie 2 SIN VERIFICAR | — | sí | sí |
| **Colección propia con leases** | — | nada nuevo | **sí** | **sí** (al menos una vez) |

Fuentes: [FastAPI background tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/) (para
trabajo pesado sugiere herramientas con cola), [APScheduler](https://github.com/agronholm/apscheduler)
y su [guía 3.x](https://apscheduler.readthedocs.io/en/3.x/userguide.html),
[arq](https://github.com/python-arq/arq), [Taskiq brokers](https://taskiq-python.github.io/available-components/brokers.html),
[taskiq-mongodb](https://pypi.org/project/taskiq-mongodb/),
[PyMongo async `find_one_and_update`](https://pymongo.readthedocs.io/en/stable/api/pymongo/asynchronous/collection.html),
[Beanie releases](https://github.com/BeanieODM/beanie/releases) (`get_pymongo_collection()` en 2.0).

**Propuesta.** Un `Document` `Job` (`kind`, `server_id`, `status`, `step`, `attempts`,
`lease_until`, `owner`, `checkpoint`, `error`) y un worker en el `lifespan`:

1. Reclamar en una llamada atómica el trabajo `queued` más viejo, o uno `running` cuyo
   `lease_until` ya pasó: `$set` estado/owner/lease, `$inc` attempts, `ReturnDocument.AFTER`.
2. Renovar el lease con un latido mientras corre.
3. Guardar un checkpoint por paso, y que cada paso compruebe si ya se hizo antes de hacerlo (por
   ejemplo, buscar el backup por nombre antes de crear otro). La entrega es **al menos una vez**.
4. Un índice único parcial sobre `server_id` donde `status ∈ {queued, running}`: nunca dos
   actualizaciones del mismo servidor a la vez.

Tarjetas que aplican (`.agents/knowledge/INDEX.md`): `retry-over-irreversible-effect` (un paso
que apaga o borra no se repite a ciegas), `in-process-guarantees` (el índice único, no un lock en
memoria, es lo que garantiza "uno por servidor"), `order-writes-by-failure-residue` (el orden de
los pasos decide qué queda si el proceso muere entre dos).

## 2. Tests contra MongoDB

- **Beanie 2.x usa el cliente async de PyMongo, no Motor** ([beanie-odm.dev](https://beanie-odm.dev/));
  Motor está deprecado desde 2026-05-14
  ([migración](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/reference/migration/)).
- **mongomock-motor** solo simula `AsyncIOMotorClient`; el soporte del cliente async de PyMongo en
  mongomock sigue abierto sin respuesta ([mongomock#916](https://github.com/mongomock/mongomock/issues/916)).
  Hacerlo andar con Beanie exige parches frágiles y no reproduce fielmente `find_one_and_update` ni
  los índices parciales (tarjeta `test-double-fidelity`).
- **Beanie se prueba a sí mismo contra MongoDB real** (5.0, 7.0, 8.0) en CI
  ([workflow](https://github.com/BeanieODM/beanie/blob/main/.github/workflows/github-actions-tests.yml),
  [conftest](https://github.com/BeanieODM/beanie/blob/main/tests/conftest.py)).
- Los *service containers* de GitHub Actions corren en runners Linux con el puerto mapeado
  ([docs](https://docs.github.com/en/actions/use-cases-and-examples/using-containerized-services/about-service-containers));
  testcontainers 4.15 sirve en local si hay Docker
  ([docs](https://testcontainers-python.readthedocs.io/en/latest/modules/mongodb/README.html)).

**Propuesta.** `services: mongo` (versión fija) en `.github/workflows/check.yml`, un fixture que
hace `init_beanie` sobre una base descartable por sesión de tests, y en local la misma URL o
testcontainers. Es requisito de los trabajos (§1), que dependen de semántica atómica e índices.

## 3. Autorización sobre OIDC

- Authlib 1.8 deja los claims del ID token en `token['userinfo']`; los grupos son código de la app
  ([Authlib + Starlette](https://docs.authlib.org/en/stable/oauth2/client/web/starlette.html)).
- Authentik incluye la pertenencia a grupos en el scope `profile` (claim `groups`)
  ([Authentik OAuth2](https://docs.goauthentik.io/add-secure-apps/providers/oauth2/)).
- `SessionMiddleware`: `max_age` 14 días, `same_site='lax'`, **`https_only=False` por defecto**
  ([Starlette middleware](https://starlette.dev/middleware/)).
- Detrás de un proxy en Docker, uvicorn solo cree `X-Forwarded-Proto` de `--forwarded-allow-ips`
  (por defecto 127.0.0.1); si no se configura, el `redirect_uri` del login sale en `http://`
  ([uvicorn deployment](https://uvicorn.dev/deployment/)).

**Hoy** (`src/database/users.py`): se crea un usuario por cada email que llega del IdP, sin mirar
`email_verified`, y la identidad es el email, no `iss`+`sub`.

**Propuesta** (i-7985ee-c64e06): rechazar el login salvo `email_verified` y (grupo `ADMIN_GROUP` en
`userinfo["groups"]` o email en `ALLOWED_EMAILS`); no crear usuarios desconocidos; identidad por
`iss`+`sub`; `https_only=True`, `same_site="lax"` (strict rompe el callback OIDC), `max_age` de
horas; quitar CORS o listar orígenes (d-7985ee-4e4aee); `FORWARDED_ALLOW_IPS` = la red del proxy.
Tarjetas: `fail-closed-defaults`, `a-default-scope-is-the-widest-one`.

## 4. Avisos: webhook de Discord

`POST /webhooks/{id}/{token}`, cuerpo con `content` (≤ 2000 caracteres) o `embeds` (≤ 10);
`allowed_mentions` controla menciones
([webhook](https://docs.discord.com/developers/resources/webhook)). Límites: respetar los encabezados
`X-RateLimit-*` y `retry_after` en un 429; 10 000 requests inválidos en 10 minutos banean la IP
([rate limits](https://docs.discord.com/developers/topics/rate-limits)). El límite "5 cada 2 s por
webhook" solo aparece en fuentes comunitarias: SIN VERIFICAR.

**Propuesta:** un protocolo `Notifier` con una implementación Discord sobre `HttpAPI`; los avisos se
escriben en una colección *outbox* desde el worker y se entregan con reintentos que respetan
`retry_after` (tarjeta `best-effort-side-channels`: que un aviso fallido nunca tumbe la
actualización). `allowed_mentions: {"parse": []}`; la URL del webhook es un secreto (`.env.yaml`).

## 5. Interfaz para el dueño

- Jinja2 + HTMX sobre FastAPI ([templates](https://fastapi.tiangolo.com/advanced/templates/)):
  mismo origen, reutiliza la cookie de sesión, sin CORS ni build de JS.
- **starlette-admin 1.0.1** soporta Beanie (`starlette_admin.contrib.beanie`) y trae proveedor
  OAuth ([repo](https://github.com/jowilf/starlette-admin),
  [changelog](https://jowilf.github.io/starlette-admin/changelog/)): sirve para navegar documentos
  (podría reemplazar mongo-express), no para flujos como "actualizar y mirar el progreso".

**Propuesta:** páginas Jinja2 + HTMX para operar (lista de trabajos, estado, botón "actualizar
servidor"); starlette-admin opcional detrás de la misma autorización. Sin SPA.

## 6. Observabilidad

- `ping` de MongoDB responde aunque el servidor esté bloqueado para escritura
  ([ping](https://www.mongodb.com/docs/manual/reference/command/ping/)).
- Compose: `healthcheck` + `depends_on: {condition: service_healthy}`
  ([Compose services](https://docs.docker.com/reference/compose-file/services/)).
- structlog 26.1: JSON, contextvars ([docs](https://www.structlog.org/en/stable/)).

**Propuesta:** `/health/live` (sin I/O) y `/health/ready` (ping a Mongo con timeout ~2 s y el último
latido del worker; 503 si falla); healthcheck con `python -c` (la imagen slim no trae curl); logs
JSON con `request_id` y `job_id`.

## Hallazgos del repositorio que cambian prioridades

- `docker/docker-compose.yaml`: el volumen de Mongo está comentado (**los datos se pierden al
  recrear el contenedor**), publica 27017 y 8081 al host, usa `mongo:latest` y no tiene healthchecks.
- `main.py`: CORS comodín con credenciales; sesión sin `https_only`.
