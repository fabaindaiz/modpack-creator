# Sistema de instrucciones para IA — evaluación
**Repositorio:** modpack-creator · **Fecha:** 2026-10-01 · **Bundle:** 0.0.25 (`r-7985ee`) · **Id:** s-7985ee-38f8aa

## En un párrafo

modpack-creator es una API FastAPI joven (19 commits, unas 1.900 líneas de Python, el último commit de código es un `wip` de julio de 2025) que hoy recibió el bundle 0.0.25 y un `AGENTS.md` mínimo. Esa superficie es pequeña, barata y casi toda verdadera, pero lo único que se verifica es el propio bundle: no hay tests, ni CI, ni type checker en uso, y la app no puede arrancar en esta máquina (Python 3.9; el código exige 3.12). Esa ceguera ya dejó pasar defectos que impiden arrancar o corrompen datos, encontrados solo leyendo: un `NameError` al importar `src/services/pterodactyl.py`, el servicio de Modrinth cableado a la base de datos equivocada, una imagen Docker sin `main.py`, y un diccionario por defecto compartido que puede reenviar el token de Pterodactyl a Modrinth. El paso más valioso y barato (horas) es una puerta que importe la app completa en Python 3.12 y ejecute mypy; después, seguir el bootstrap en modo adopt desde la Fase 3.

| Dimensión | Nivel | Una línea |
|---|---|---|
| Cobertura | 1 | `AGENTS.md` (51 líneas) describe el proyecto y los comandos; no hay invariantes del dominio ni archivos por área |
| Exactitud | 1 | todos los comandos y rutas existen, pero la línea de capas describe una dirección que 14 de 21 inyecciones en routers se saltan |
| Enforcement | 1 | la única regla ejecutable es `bundle.py verify`; ninguna regla sobre el código tiene enforcer |
| Verificación | 0 | sin tests, sin CI, mypy configurado pero sin instalar ni ejecutar; la app no se puede importar aquí |
| Memoria de decisiones | 0 | no hay registro de decisiones; la historia es casi toda `wip` / `feat` sin cuerpo |
| Captura de procesos | 1 | `scripts/*.sh` capturan build/run/deploy; el método está en `.agents/method/` |
| Costo por sesión | 3 | 52 líneas cargadas en cada petición (`CLAUDE.md` → `AGENTS.md`), muy por debajo de 200 |

Los niveles describen el estado para comparar dos ejecuciones; no son una nota ni una meta.
Se evaluó con `prompt-context.md` presente (las secciones de su línea `Reads:`).

## Qué se leyó

Todo lo versionado fuera de `.agents/`: `main.py`, los 45 `.py` de `src/`, `docker/`, `scripts/`,
`docs/`, `.mypy.ini`, `.dockerignore`, `.gitignore`, `.env.default`, `requirements.txt`, la historia
de git completa, `.claude/` y `AGENTS.md`/`CLAUDE.md`. No se leyó `LICENSE` (GPL, sin efecto aquí).
No hay `.github/`, `.cursor*` ni `copilot-instructions.md`.

## Qué funciona

- **Capas con nombres consistentes**: `src/database/*` (acceso a Mongo), `src/library/api/client/*`
  (HTTP externo), `src/services/*`, `src/routers/v1/*`. Cada clase de base de datos expone la
  misma familia `*_find` / `*_get` / `*_insert` / `*_insert_many` / `*_save`; `src/database/modpack.py`
  es el archivo ejemplar para copiar.
- **Un cliente HTTP propio y pequeño** (`src/library/api/`) con autorización, request y response
  intercambiables; los clientes de Modrinth y Pterodactyl son subclases delgadas.
- **Configuración que falla cerrada**: `getenv` (`src/library/utils/__init__.py`) lanza si falta una
  variable sin default; `SESSION_SECRET`, `MONGODB_URI` y `PTERODACTYL_API_URL` no tienen default.
- **Fuentes discriminadas** en Pydantic (`ProjectSource`, `UsersProvider` con `Field(discriminator=…)`):
  un proyecto o usuario de origen desconocido no se puede escribir con la forma equivocada.
- **Mejor que el default del método:** ninguna convención aquí supera al método todavía; el diseño
  de autorización intercambiable (`SessionAuthorization`) es el candidato si se prueba.

## Qué no funciona

- **No existe ninguna puerta sobre el código** — *evidencia:* `.mypy.ini` existe, `mypy` no está en
  `requirements.txt` ni instalado; no hay `tests/` ni CI — *deja pasar:* todos los defectos de
  la tabla siguiente, que se ven al importar o al primer request.
- **La app no se puede ejecutar donde se escribe** — *evidencia:* `python3 --version` → 3.9.6; el
  código usa `type X = …` (PEP 695, 3.12+) en `src/model/modpack/project.py:23` — *deja pasar:* la
  clase de errores de composición (imports, cableado de DI, arranque); ver la tarjeta
  `unrunnable-system-moves-the-gate`.
- **Autenticarse es estar autorizado** — *evidencia:* `UsersDatabase.user_from_provider` inserta a
  cualquier usuario nuevo que llegue del IdP (`src/database/users.py:41`), y todos los endpoints solo
  piden `get_current_user` — *deja pasar:* cualquier cuenta del proveedor OIDC opera servidores de
  Pterodactyl (`server_get_many` usa `type: admin-all`). **ASSUMPTION:** que el IdP no restringe quién
  puede iniciar sesión.
- **Reintentos amplios sobre efectos externos** — *evidencia:* cada método de cliente lleva
  `@backoff.on_exception(backoff.expo, Exception, max_tries=2)`, incluidos `server_power`,
  `server_files_upload` y `server_files_delete` — *deja pasar:* una acción en un servidor real
  repetida cuando la respuesta se perdió (tarjeta `retry-over-irreversible-effect`). Hoy ningún
  router los llama.
- **Handlers de respuesta compartidos entre requests concurrentes** — *evidencia:*
  `DEFAULT_RESPONSE = JsonResponse()` es un singleton de módulo y `_request` guarda el JSON en él
  y lo devuelve; entre `handle()` y la lectura del llamador hay `await` de cierre de sesión —
  *deja pasar:* que un request lea el cuerpo de otro. **ASSUMPTION** por lectura, no reproducido.

## Ya es falso

| Afirmación | Dónde | Realidad |
|---|---|---|
| La app arranca con `uvicorn main:app` | `docker/Dockerfile:18-20` | `COPY src .` copia el contenido de `src/` a `/app`; `main.py` no entra en la imagen y los imports `src.*` no resuelven |
| El contenedor recibe su configuración | `docker/docker-compose.yaml` | solo define `ENVIRONMENT`; no monta `.env.yaml` ni declara `env_file`, y `getenv("SESSION_SECRET")` lanza al importar. **ASSUMPTION**: no hay otra vía |
| `src/services/pterodactyl.py` se importa | línea 63 | `-> PterodactylVersion` sin importar: `NameError` al definir la clase en 3.12 (comprobado el mismo comportamiento en 3.9); como `main.py` importa todos los routers, la app no arranca |
| `ModrinthService` recibe la base de Modrinth | `src/dependences.py:40` | recibe `modpackDatabase`; `project_create_many` termina insertando proyectos de Modrinth vía `ModpackDatabase` |
| `project_update` actualiza el proyecto | `src/services/modrinth.py:42-44`, igual en `pterodactyl.py:32-34` | guarda el documento viejo con `project.id` de un objeto recién parseado (`None`): el dato nuevo nunca se escribe |
| Un proyecto tiene muchas versiones | `src/model/modrinth/version.py:43` | `project_id` tiene `Indexed(unique=True)`: la segunda versión de un mismo proyecto falla con clave duplicada |
| Las cabeceras son por request | `src/library/api/__init__.py:80,106-109` | `headers: dict = {}` es un default mutable que se modifica en cada llamada: la cabecera `Authorization` de Pterodactyl queda en él y viaja en las llamadas siguientes de Modrinth (si no hay `MODRINTH_TOKEN`) y del `Downloader` |
| Los clientes siguen el contrato de las APIs | `src/library/api/client/modrinth.py:79-120`, `pterodactyl.py:81-94` | Modrinth define los lotes en `/version_files` y `/version_files/update` con `hashes`, `loaders` y `game_versions` como arrays JSON; el código usa `version_file` y manda strings. Pterodactyl espera `POST /command` y `POST /files/delete` con cuerpo JSON; el código usa GET y query. Verificado en `docs.modrinth.com/openapi.yaml` y en el fuente del panel |
| `PterodactylModpack` se persiste | `src/model/pterodactyl/modpack.py:7` | es un `Document` que `configure_beanie` no registra |
| `src.library.auth` exporta `get_current_user` | `src/library/auth/__init__.py:1` | `oidc.py` no lo define; nadie importa el paquete, así que hoy es código muerto |
| Las capas van router → servicio → base de datos | `AGENTS.md` §Qué es | 14 de 21 inyecciones en routers piden `*Database` o `*API` directo; `src/services/modrinth.py` consulta los modelos Beanie en 6 líneas |

Ningún puntero muerto ni comando inventado en `AGENTS.md`; sin referencias por número de línea.

## La clase de bug que nadie aquí puede ver

**La composición al arrancar y el contrato con las APIs reales.** Nadie importa la app completa
antes de construir la imagen, así que un import roto, un cableado de DI equivocado o un Dockerfile
que no copia la entrada solo aparecen en el despliegue. Del mismo modo, la forma de los cuerpos que
se envían a Modrinth y Pterodactyl (`files_hash_post_many` manda `str(hashes)`, `server_command`
usa GET) solo se ve contra el servicio real. Nada lo comprueba antes de publicar.

## Qué hacer después

Esfuerzo: estimación en horas, es una suposición.

| # | Hacer | Esfuerzo | Previene | ¿Decides tú? |
|---|---|---|---|---|
| 1 | Importar `PterodactylVersion` y corregir `modrinthService` en `dependences.py` | 0,25 h | app que no arranca; inserciones en la colección equivocada | no |
| 2 | `headers` nuevo por request en `HttpAPI._request` (y los otros 9 `dict = {}` por defecto) | 0,5 h | fuga del token de Pterodactyl a terceros | **sí**: si la API corrió sin `MODRINTH_TOKEN`, rotar el token de Pterodactyl |
| 3 | Dockerfile que copie `main.py` y `src/`; configuración del contenedor por `env_file` o `.env.yaml` montado | 0,5 h | imagen que no arranca | **sí**: cómo llega la configuración |
| 4 | Puerta: entorno 3.12 + `mypy` + un test que importe `main` con variables de entorno falsas; ejecutarlo desde `scripts/check.sh` | 2–3 h | toda la clase de composición | **sí**: instalar herramientas (venv o uv) |
| 5 | Quitar `unique` de `ModrinthVersion.project_id`; corregir `*_update` para guardar el dato nuevo | 1 h | versiones perdidas; updates que no escriben | **sí**: si producción ya tiene datos, la migración del índice |
| 6 | Decidir quién puede iniciar sesión (lista permitida o rol del IdP) | 1 h | cualquier cuenta del IdP opera servidores | **sí** |
| 7 | Bootstrap en modo adopt, Fases 3–9 | 3–5 h | reglas sin enforcer, decisiones sin registro | **sí**: aprobar la Fase 3 |

## Cómo seguir con el método

**Continuar el bootstrap en modo adopt desde la Fase 3**, sobre la base de hoy. `carrier.toml` ya
existe porque la adopción se hizo en dos tiempos (base primero), no porque se copiaran archivos sin
el trabajo: este informe más la investigación de la Fase 2 cubren las Fases 0–2.

## Sin determinar

- Dónde se despliega y quién lo usa: decide si la puerta 4 basta o hace falta un check previo a publicar.
- Si producción tiene datos en Mongo: decide el costo de la fila 5.
- Si la API corrió alguna vez con `MODRINTH_TOKEN` vacío y el token de Pterodactyl cargado: decide la rotación de la fila 2.
- Si el IdP limita quién inicia sesión: decide la urgencia de la fila 6.
- Si la app arrancó alguna vez en `8acf490` (`wip`): no se puede saber sin un entorno 3.12.
