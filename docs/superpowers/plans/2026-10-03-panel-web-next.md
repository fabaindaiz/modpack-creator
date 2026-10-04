# Panel web, siguiente lote: plan de implementación

> **Para agentes:** SUB-SKILL REQUERIDA: usar superpowers:subagent-driven-development (recomendada)
> o superpowers:executing-plans para implementar este plan tarea por tarea. Los pasos usan casillas
> (`- [ ]`) para seguir el avance.

**Objetivo:**
- detalle del modpack (archivos locales, canales y pins);
- cierre de los pendientes menores;
- vista previa de cambios con changelogs;
- revisión diaria de actualizaciones.

**Arquitectura:**
- La comparación entre dos locks es una función pura en `src/library/changes.py`.
- `ModpackService.change_preview` le suma los changelogs, guardados en el catálogo.
- La revisión de actualizaciones es un servicio nuevo, `UpdateCheckService`, con su base
  (`UpdateCheckDatabase`) y su documento (`UpdateCheck`). Corre como trabajo durable
  `update_check` y la programa el lifespan.
- El panel suma fragmentos y rutas. Todo lo que escribe va a la tabla de roles.

**Tecnologías:** las de la rama, más `zoneinfo` de la biblioteca estándar.

**Spec:** `docs/superpowers/specs/2026-10-03-panel-web-next-design.md`. Hay que leerla junto con este
plan; manda sobre él. Hay una desviación justificada: la spec nombra
`ModpackService.enqueue_update_check`, pero `ModpackService` no recibe la base de trabajos, así que
el encolado vive en `UpdateCheckService.enqueue`, con la misma auditoría (`bundle.update_check`).

## Restricciones globales

Son las del plan `2026-10-02-panel-web.md`, y valen todas:
- Python 3.12; los routers solo inyectan servicios; nada de defaults mutables; todo `Document`
  registrado.
- Tests primero, y vistos fallar.
- La CSP exacta; nada de `<script>` inline, `style=` ni `hx-on`.
- Commits con el dueño como **único autor**, sin atribución.
- **Nunca** leer ni editar `.env.yaml` ni `.env.default`. Las variables nuevas de este lote son
  opcionales (`getenv_opt`).
- **Gate:**
  - `./scripts/test-mongo.sh` (`docker` = podman);
  - mientras falte la línea `OIDC_OPERATOR_GROUPS` en `.env.default`, el único fallo aceptado
    del audit es `env_template`;
  - correr además `hatch run typecheck` y los tests (con
    `scratchpad/mongo/with-mongo.sh hatch run tests`);
  - `hatch run bundle` si se tocan ids.
- **Revisión visual:** `PLAYWRIGHT_CHROMIUM=<headless shell> ./scripts/visual-sweep.sh` tiene que
  terminar sin problemas.
- **Tabla de roles:** todo método de servicio nuevo que escribe o audita lleva su fila en
  `## Acciones y roles` (`docs/decisions.md`) en el mismo commit.
- **Modrinth:** cada llamada nueva o cambiada se contrasta con el openapi v2.7.0 y se prueba en
  `tests/test_api_contracts.py`.
- Comandos sin bloquear stdin, y archivos editados con herramientas de edición.

## Foco de revisión

1. **Changelog enorme o con HTML:** se recorta a 4000 caracteres y se escapa; nunca se renderiza
   como HTML (N3).
2. **Modrinth caído en la vista previa o en la revisión:** un aviso con "changelog no disponible",
   o un `UpdateCheck` con `error`. Nunca un 500 ni un aviso por Discord (N3, N5).
3. **El cambio de horario de Chile** (`America/Santiago`) y un `UPDATE_CHECK_AT` mal formado:
   "ya corrió hoy" por fecha local correcta a ambos lados del cambio; el formato malo hace fallar
   el arranque (N6).
4. **Pin a una versión que no es del proyecto, o un id con `.` o `$`:** 422, sin escribir (N2).
5. **Dos revisiones del mismo modpack a la vez** (botón + programación): la clave
   `bundle:{id}` deja una sola activa; la segunda da 409 legible (N5, N6).

---

### Tarea N1: Pendientes menores (b)

**Archivos:**
- `src/services/update.py`, `src/database/jobs.py` y un predicado compartido, por ejemplo
  `is_real_update(job)` en `src/model/jobs.py`;
- `src/database/jobs.py` y `src/database/audit.py` (para `limit + 1`) con sus routers y plantillas;
- `src/services/modpack.py` (tope de 500 y mensajes en español);
- `scripts/visual_sweep.py` y `scripts/visual-sweep.sh`;
- `docs/runbooks/migrar-mongo-a-volumen.md` y `AGENTS.md`;
- los tests de cada punto.

**Interfaces:**
- `is_real_update(job: Job) -> bool`: `job.kind == "server_update" and job.payload.get("dry_run")
  is False`. Lo usan el worker (`_update`), `enqueue_rollback` y `last_real_update`. La consulta
  pasa a `{"payload.dry_run": False}`.
- **Paginación:**
  - `AuditDatabase.find` y `JobDatabase.find` siguen igual;
  - las rutas piden `limit + 1` y pasan a la plantilla `has_older` y los primeros `limit`
    elementos;
  - el enlace "Ver anteriores" usa el id del último elemento mostrado.
- `ModpackService.decisions_set`: más de 500 decisiones → `HTTP_422` con "Demasiadas decisiones
  de una vez (máximo 500)". Los demás textos de sus 422 pasan al español.
- **Script visual:**
  - `app_env()` suma `MODRINTH_API_URL`, `FABRIC_META_URL` y `QUILT_META_URL` a
    `http://127.0.0.1:1/…`;
  - la espera de Mongo tiene un máximo de 60 intentos y detecta si el contenedor murió;
  - `main()` aborta si el cwd es el repositorio o si existe `./.env.yaml`;
  - se verifica que `page.url` termine en la ruta pedida.
- **Runbook:** el bloque 4.4 usa letras en sus pasos. Las referencias nombran el paso principal
  ("antes del `git pull` de 5. Desplegar"), y el paso de la FCV muestra la línea completa
  `docker exec modpack-mongodb mongo --quiet -u mongoadmin -p "$MONGODB_PASSWORD"
  --authenticationDatabase admin --eval '…'`.
- **`AGENTS.md`:** la cláusula de la tabla de roles termina con "(con los límites de
  `tests/test_routes_roles.py`)".

- [ ] **Paso 1: tests que fallan:**
  - `test_job_without_dry_run_is_a_simulation_everywhere`: el worker no escribe,
    `enqueue_rollback` da 409 y `last_real_update` lo ignora;
  - `test_older_link_only_when_more_exist` (trabajos y auditoría, con exactamente 50 y con 51);
  - `test_bulk_decisions_capped_at_500`;
  - `test_editor_with_mismatched_bundle_is_refused_before_comparing` (vacío y distinto → 403,
    sin llamadas).
- [ ] **Paso 2:** verlos fallar. Implementar. Gate. Correr el script visual: 0 problemas.
- [ ] **Paso 3: commits** `fix: a job without dry_run is a simulation everywhere`, `fix: show older pages only when there are some`, `chore: harden the visual sweep and clarify the 4.4 runbook`.

### Tarea N2: Detalle del modpack (a)

**Archivos:** `src/database/modpack.py`, `src/services/modpack.py`, `src/routers/web/modpacks.py`,
`src/web/templates/modpacks/detail.html` y fragmentos nuevos (`_local_files.html`,
`_channels.html`, `_pins.html`), `docs/decisions.md` (filas), `tests/test_web_modpacks.py` y un
test de servicio con Mongo.

**Interfaces:**
- `ModpackDatabase.bundle_pin_set(bundle_id, project_id, version_id) -> ModpackBundle` y
  `bundle_pin_unset(bundle_id, project_id) -> ModpackBundle`: `$set` o `$unset` de
  `pins.<project_id>`, con 404 como `bundle_get`.
- `ModpackService.pin_set(bundle_id, project_id, version_id, actor)`:
  - valida los dos ids con `^[A-Za-z0-9_-]+$` (si no, 422 sin escribir);
  - exige que `project_id` esté en el modpack (422);
  - exige que la versión sea de ese proyecto: la consulta en el catálogo y, si no está, a Modrinth
    con `version_get`, y compara su `project_id`. Si no coincide, 422 "La versión no es de ese
    proyecto";
  - evento `bundle.pin` con `{"project_id": …, "version_id": …}`.
- `ModpackService.pin_unset(bundle_id, project_id, actor)`: evento `bundle.pin` con
  `{"project_id": …, "version_id": None}`.
- Canales: `POST /modpacks/{id}/channels` con casillas `channel=release|beta|alpha`. Sin ninguna →
  422. Llama `bundle_update` con `ModpackBundlePatch(channels=[…])`, que ya audita.
- **Rutas:**
  - `POST /modpacks/{id}/pins` (campos `project_id`, `version_id`);
  - `DELETE /modpacks/{id}/pins/{project_id}`.

  Las dos devuelven `_pins.html`. Las filas `pin_set` y `pin_unset` van a la tabla de roles
  (editor).
- **Archivos locales:** se muestran el nombre, el tamaño legible (KiB/MiB) y los 12 primeros
  caracteres del sha512. Si no hay, se dice "Sin archivos locales".

- [ ] **Paso 1: tests que fallan:**
  - (Mongo) `test_pins_are_written_one_key_at_a_time` (dos pins concurrentes sobreviven);
  - `test_pin_with_bad_id_or_foreign_version_writes_nothing` (foco 4);
  - (panel) las tres secciones renderizan, sin canales da 422, y agregar o quitar un pin llama al
    servicio con `actor`.
- [ ] **Paso 2:** implementar. `npm run build`. Gate. Script visual.
- [ ] **Paso 3: commit** `feat: show local files, edit channels and pins of a modpack`.

### Tarea N3: Comparación con changelogs y comparar builds (c, parte 1)

**Archivos:**
- crear `src/library/changes.py`, `src/web/templates/modpacks/_changes.html` y
  `modpacks/compare.html`;
- modificar `src/model/modrinth/version.py` (campo `changelog: Optional[str] = None`),
  `src/services/modpack.py`, `src/routers/web/modpacks.py` y `modpacks/_builds.html` (el
  formulario de comparar);
- tests: `tests/test_changes.py`, `tests/test_web_modpacks.py` y uno de contrato en
  `tests/test_api_contracts.py` (`GET /version/{id}` con `changelog`).

**Interfaces:**
- En `src/library/changes.py`:
  ```python
  @dataclass(frozen=True)
  class Change: project_id: str; from_version: Optional[str]; to_version: Optional[str]
  @dataclass(frozen=True)
  class ChangeSet: added: list[Change]; removed: list[Change]; changed: list[Change]; unchanged: int
  def diff_entries(before: Mapping[str, str], after: Mapping[str, str]) -> ChangeSet   # proyecto → versión; listas ordenadas por project_id
  ```
- En `ModpackService`:
  - `async def change_preview(self, before, after) -> ChangePreview`. `ChangePreview` es un
    modelo con el `ChangeSet`, `changelogs: dict[version_id, Optional[str]]` y `failed: set[str]`;
  - los changelogs salen de un `_changelog(version_id)` privado: primero el catálogo
    (`ModrinthVersion.changelog`) y, si no está, `modrinthAPI.version_get` y lo guarda;
  - corre con un `asyncio.Semaphore(8)`. Un fallo marca la versión en `failed`, y nunca lanza;
  - `async def compare_builds(self, bundle_id, from_number, to_number) -> ChangePreview` usa los
    locks de los dos builds, con las entradas que van en el servidor y en el cliente.
- `_changes.html`:
  - las cuatro secciones; por cada cambio, el título del catálogo o el id, "de → a" y el
    changelog;
  - el changelog va recortado a 4000 caracteres, como texto con la clase `whitespace-pre-wrap`,
    o con "sin changelog" o "changelog no disponible";
  - cada uno lleva el enlace `https://modrinth.com/project/<project_id>/changelog`.
- `GET /modpacks/{id}/builds/compare?de=N&a=M` (editor, lectura): con un build inexistente, 404
  como aviso; con `de == a`, todo igual.

- [ ] **Paso 1: tests que fallan:**
  - `diff_entries` (agregados, quitados, cambiados, iguales y orden);
  - `change_preview` con caché (la segunda vez no llama a Modrinth) y con fallo → `failed`;
  - un changelog de 10 000 caracteres con `<script>` se recorta y se escapa (foco 1);
  - la página de comparación;
  - el contrato de `version_get` con `changelog`.
- [ ] **Paso 2:** implementar. `npm run build`. Gate.
- [ ] **Paso 3: commit** `feat: compare two builds with the changelogs of what changes`.

### Tarea N4: La vista previa en la simulación y en "Actualizar…" (c, parte 2)

**Archivos:** `src/services/modpack.py` (o `UpdateService` si queda más natural; decidir y
justificar), `src/routers/web/servers.py`, `src/routers/web/activity.py`,
`servers/_dry_run.html` y `servers/_update.html`, `tests/test_web_servers.py` y
`tests/test_web_activity.py`.

**Interfaces:**
- `async def server_change_preview(self, server_id, build_number) -> ChangePreview`:
  - "antes" son las entradas identificadas del último escaneo (proyecto → versión);
  - "después" es el lock del build, con las entradas que van al servidor;
  - sin escaneo, 409 "Escanea el servidor antes de comparar".
- **En la simulación:** el resultado terminado (`_dry_run.html`) incluye `_changes.html` debajo
  del plan. Las rutas que lo renderizan (`/servers/{id}`, `/jobs/{id}`, `/jobs/{id}/row`) pasan
  la vista previa por el servicio, nunca por la base.
- **En el diálogo "Actualizar…":**
  - un contenedor `#update-preview` precargado con el build por defecto;
  - el `<select name="build">` lleva `hx-get="/servers/{id}/update/preview"`,
    `hx-trigger="change"`, `hx-target="#update-preview"` y `hx-include="this"`;
  - `GET /servers/{id}/update/preview?build=N` (operador o editor, lectura) devuelve
    `_changes.html`.

- [ ] **Paso 1: tests que fallan:**
  - la simulación terminada muestra los cambios;
  - el diálogo trae la vista previa del recomendado;
  - cambiar el build pide la nueva (la ruta devuelve el fragmento);
  - sin escaneo, un aviso 409;
  - Modrinth caído → "changelog no disponible" (foco 2).
- [ ] **Paso 2:** implementar. `npm run build`. Gate. Script visual.
- [ ] **Paso 3: commit** `feat: preview what an update changes, with changelogs, before running it`.

### Tarea N5: Revisión de actualizaciones: cálculo, datos y trabajo (d, parte 1)

**Archivos:**
- crear `src/model/modpack/update_check.py` (o junto a los modelos de modpack),
  `src/database/update_check.py` y `src/services/update_check.py`;
- modificar `src/library/api/client/modrinth.py` (`version_types`), `src/model/__init__.py`
  (registro), `src/database/__init__.py`, `src/services/__init__.py`, `src/dependences.py` y
  `main.py` (sus `handlers()` en el worker);
- `docs/decisions.md` (filas de la tabla de roles);
- tests: `tests/test_update_check.py` (Mongo) y `tests/test_api_contracts.py`.

**Interfaces:**
- `ModrinthAPI.files_update_hash_post_many(..., version_types: Sequence[str] = ())`. Si no está
  vacío, manda `"version_types": [...]`. Sigue con su backoff y en `IDEMPOTENT_POSTS`.
- `UpdateCheck(Document)`:
  - `bundle_id: PydanticObjectId`, `build_number: int` y `checked_at: datetime`;
  - `updates: list[AvailableUpdate]` (`project_id`, `from_version`, `to_version` y `pinned:
    bool`);
  - `error: Optional[str] = None` y `notified_digest: Optional[str] = None`;
  - índice `(bundle_id asc, checked_at desc)`.
- `UpdateCheckDatabase`: `insert`, `latest(bundle_id)` y `latest_many(bundle_ids) -> dict`.
- `UpdateCheckService(modpackDB, modrinthAPI, updateCheckDB, jobDB, audit, notifier)`:
  - `async def compute(self, bundle_id) -> UpdateCheck`:
    - el build recomendado, o el último;
    - agrupa las entradas por los loaders de su tipo, con la misma tabla que la resolución
      (reusarla, no copiarla);
    - por grupo llama al cliente con `game_versions=[bundle.version]` y
      `version_types=bundle.channels`;
    - marca `pinned` según `bundle.pins`.
  - `async def enqueue(self, bundle_id, actor: Optional[Actor]) -> Job`: clave `bundle:{id}` y
    `requested_by`. Audita `bundle.update_check` solo si hay `actor`. 409 legible si ya hay una
    activa (foco 5).
  - `handlers()` → `{"update_check": ...}`. El handler hace en un paso con checkpoint: `compute`,
    guardar el `UpdateCheck` y avisar.
  - **Aviso:**
    - el digest es el sha256 de `"project:from>to"` ordenados, de las no fijadas;
    - si las hay y el digest es distinto del `notified_digest` del último `UpdateCheck` con aviso
      de ese modpack, encola el aviso con el texto de la spec (recortado a 2000) y guarda el
      digest;
    - si Modrinth falla, guarda el `UpdateCheck` con `error` y termina el trabajo fallido, sin
      aviso.
  - `async def latest(self, bundle_id) -> Optional[UpdateCheck]` y `latest_many(...)`.
- Filas en la tabla de roles: `UpdateCheckService.enqueue` (editor). Si `compute` u otros
  escriben o auditan, también van; si no, al allowlist con el motivo.

- [ ] **Paso 1: tests que fallan:**
  - (Mongo, con un Modrinth falso) un build con 3 mods, 1 actualizado y 1 fijado da 2 updates
    (1 pinned);
  - la agrupación por loaders (mods, shader y resourcepack van en llamadas separadas);
  - `version_types` igual a los canales;
  - el digest no repite el aviso, y uno distinto sí avisa;
  - Modrinth caído → `error` y sin aviso (foco 2);
  - dos `enqueue` → el segundo da 409 (foco 5);
  - el contrato con `version_types`.
- [ ] **Paso 2:** implementar. Gate (tests de arranque y cableado, audit de registro, tabla de
  roles).
- [ ] **Paso 3: commit** `feat: check the recommended build of each modpack for newer versions`.

### Tarea N6: Revisión de actualizaciones: programación, página, botón e insignia (d, parte 2)

**Archivos:** `src/services/update_check.py` (el programador), `main.py` (lifespan),
`src/routers/web/modpacks.py`, `src/routers/web/home.py`, crear
`src/web/templates/modpacks/updates.html` y modificar `home.html` y `modpacks/detail.html`,
`scripts/visual_sweep.py` (las páginas nuevas y un `UpdateCheck` sembrado), tests.

**Interfaces:**
- `parse_check_at(value: str) -> time`: `HH:MM` estricto. Si no es válido, `ValueError` con un
  mensaje claro. En el lifespan, si `UPDATE_CHECK_AT` está y es inválida, la app no arranca
  (foco 3).
- `def due_today(now_utc: datetime, at: time, tz: ZoneInfo, last_checked_at: Optional[datetime])
  -> bool`: es verdadero si la hora local actual ya pasó `at` y la fecha local del último check
  (en esa zona) es anterior a hoy. Es pura y se prueba alrededor del cambio de horario de Chile
  (abril y septiembre).
- `async def run_scheduler(self, stop, at, tz, check: float = 300.0)`: cada `check` segundos, para
  cada modpack con build, si `due_today(...)`, encola con `actor=None`. Un 409 por un activo se
  ignora.
- En el lifespan: con `UPDATE_CHECK_AT` puesto, `UPDATE_CHECK_TZ` (por defecto
  `America/Santiago`) y `zoneinfo.ZoneInfo`. Una zona desconocida hace fallar el arranque.
- **Rutas:**
  - `GET /modpacks/{id}/actualizaciones` (editor, lectura): la última revisión, la lista con
    `_changes.html` (reusar la vista previa de N3 sobre `from → to`) y la nota "el build nuevo
    sale de la resolución: puede diferir (pins, reemplazos)";
  - `POST /modpacks/{id}/actualizaciones/buscar` → `UpdateCheckService.enqueue(…, actor)`,
    devuelve una fila de trabajo (el fragmento compartido);
  - `POST /modpacks/{id}/actualizaciones/build` → el `build_create` existente, y después redirige
    o muestra los builds.
- **Tablero:** "Últimos builds" suma, por modpack, la insignia "N actualizaciones", con N sin las
  fijadas y enlace a la página. Una sola consulta con `latest_many`.
- **Detalle del modpack:** un enlace "Actualizaciones".

- [ ] **Paso 1: tests que fallan:**
  - `due_today` (antes y después de la hora, último check ayer u hoy, el cambio de horario);
  - `parse_check_at` (`"4:00"`, `"24:00"` y `"aa"` → error);
  - un arranque con un valor inválido falla;
  - la página (con y sin revisión, con error);
  - el botón encola con `actor`;
  - "Crear build" llama a `build_create`;
  - la insignia cuenta sin las fijadas.
- [ ] **Paso 2:** implementar. `npm run build`. Gate. Script visual (con las páginas nuevas): 0
  problemas.
- [ ] **Paso 3: commit** `feat: schedule update checks daily, and show them in the panel`.

### Tarea N7: Documentación y cierre

**Archivos:** `docs/decisions.md` (decisiones: el cálculo con `version_files/update` que ignora
pins, el changelog en texto plano, y la programación diaria con zona horaria; ids con `bundle.py
id d`), `docs/roadmap.md` (i-7985ee-50d411 y las "Ideas para más adelante" de `plan.md` que se
hicieron: vista previa y revisión nocturna), `docs/runbooks/desplegar-panel.md` (`UPDATE_CHECK_AT`
y `UPDATE_CHECK_TZ` opcionales, el índice nuevo de `UpdateCheck` y el campo `changelog` del
catálogo), `README.md` y `.claude/logs/agent-changelog.md`. Se edita **la entrada de esta sesión**, la de
arriba: es la misma conversación y la regla es una entrada por sesión (decisión del dueño R4). Se le
suman los enlaces a esta spec y a este plan, qué salió mal y qué no se verificó.

- [ ] **Paso 1:** escribir. `hatch run bundle` y el gate.
- [ ] **Paso 2: commit** `docs: record the update checks and change previews`.

## Fuera de alcance

Lo dice la spec: renderizar Markdown, aplicar actualizaciones solas, revisar otros builds,
changelogs intermedios y editar archivos locales.

## Orden

N1 → N2 → N3 → N4 → N5 → N6 → N7.
