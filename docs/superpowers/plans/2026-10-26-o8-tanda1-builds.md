# Plan: O8 tanda 1, los builds

> **Para quien lo ejecute:** test primero, visto fallar, y un commit por tarea encadenado al gate
> (`./scripts/check.sh > $TMPDIR/log 2>&1 && git commit …`). Los tests de base (`@pytest.mark.network`)
> corren solo con `./scripts/test-mongo.sh`. Se marca cada tarea hecha en «Estado».

**Meta:** construir como prueba y aprobar, nombre y estrella en los builds, y una comparación que explica
qué cambió y por qué.

**Arquitectura:** tres campos nuevos en `ModpackBuild` (`test`, `name`, `starred`) que nunca tocan `lock`
ni `request`, escritos con `$set` atómicos. Una prueba nunca mueve `latest_build`, nunca es recomendada y
su instalación en un servidor no es piso hasta aprobarla. La comparación lee el pedido de cada build
(`ResolveRequest`) y el porqué de cada entrada del lock (`LockEntry`). Lo puro va en `src/library/`.

**Stack:** FastAPI, Beanie/MongoDB, Jinja + htmx (CSP: sin script ni `style=` inline).

**Fuentes:** spec `docs/superpowers/specs/2026-10-25-o8-versiones-con-historia-design.md` §8 (D1, B1–B4),
d-7985ee-a99db7, el user-walk `docs/research/2026-10-26-user-walk-o8-builds.md` (W5–W22) y
d-7985ee-3bb53b (H1–H4). ~3 sesiones.

## Restricciones globales

- Un build nunca cambia su `lock` ni su `request`: nombre, estrella y marca se escriben con `$set` de solo
  esos campos, nunca con `save()` del documento (W13).
- Una prueba (`test=True`) nunca mueve `latest_build`, tampoco al reintentar su trabajo (W5); nunca es
  `recommended_build` (H2).
- Nombre: se recorta, tope **60** caracteres, vacío o solo espacios es `None` (W15).
- Textos exactos de Discord (H4): `🧪 {pack}: build de prueba {n}{ «nombre»} creado, {k} archivos.` y
  `📦 {pack}: build {n} aprobado.`
- Roles (D10): crear como prueba, aprobar, aprobar y recomendar, nombrar y destacar son de **editor**. Cada
  método nuevo del servicio que escribe lleva su fila en `## Acciones y roles` de `docs/decisions.md` en
  el mismo commit, y audita.
- `/v1` expone lo mismo con los mismos métodos del servicio (D13).
- Ningún texto de ayuda nuevo sin el dueño: los textos de ayuda son suyos. Si una tarea necesita uno, se
  propone y queda en el roadmap.
- Sin requests nuevos a Modrinth: la comparación sigue pidiendo changelogs como hoy (W19), nada más.

## Foco de revisión

1. Doble clic en «Construir como prueba»: un solo trabajo (la clave lo da), nunca un build normal (Tarea 1).
2. Aprobar dos veces a la vez (dos pestañas): la segunda no hace nada y lo dice; `latest_build` termina igual
   (Tarea 2, `test_approving_twice_is_a_no_op_the_second_time`).
3. Una página vieja que aprueba un build ya aprobado o recomienda una prueba: 409 legible, nunca un 500
   (Tarea 2).
4. Un pack con muchos builds (200): la lista dibuja los visibles y pliega el resto sin leer más que hoy
   (Tarea 5, `test_two_hundred_builds_fold_all_but_the_shown`).
5. Un nombre con HTML o comillas: se escapa en la lista, los selectores, la comparación y Discord (Tarea 4,
   `test_a_name_with_html_is_escaped_everywhere`).

---

### Tarea 1: construir como prueba (B1, W5, W6, H4)

**Archivos:** `src/model/modpack/project.py` (ModpackBuild), `src/services/modpack.py` (`build_request`,
`build_create` y el paso del trabajo `build_create`), `src/routers/v1/modpack.py`, tests
`tests/test_builds.py` (base), `tests/test_web_modpacks.py`.

**Interfaces que produce:**
- `ModpackBuild.test: bool = False`, `ModpackBuild.name: Optional[str] = None`, `ModpackBuild.starred: bool = False`
  (un build guardado antes los lee con su valor por defecto).
- `ModpackService.build_request(bundle_id: str, actor: Actor, test: bool = False) -> Job`: payload
  `{"bundle_id": …, "test": True}` solo si es prueba; si ya hay un trabajo activo en `build:{id}` **de otro
  tipo** (su `payload.get("test", False)` difiere), `HTTP_409_CONFLICT("Ya hay un build normal en curso"
  | "Ya hay un build de prueba en curso")`; del mismo tipo, lo devuelve (como hoy).
- `ModpackService.build_create(bundle_id, actor, report=None, job_id=None, test: bool = False) -> ModpackBuild`.
- `/v1`: `POST /modpack/{id}/build?test=true`.

- [ ] Tests (base, `test_builds.py`):
  - `test_a_test_build_is_numbered_but_never_moves_latest`: build normal 1, prueba 2 → `latest_build == 1`,
    `build_get(2).test is True`.
  - `test_retrying_a_test_builds_job_never_moves_latest` (W5): `build_create(…, job_id="j", test=True)` dos
    veces → un solo build, `latest_build` sin cambiar.
  - `test_a_test_build_is_announced_as_a_test` (H4): el texto exacto con y sin nombre.
  - `test_asking_for_a_test_while_a_normal_build_is_queued_is_refused` (W6) y al revés; del mismo tipo
    devuelve el mismo trabajo.
- [ ] Verlos fallar; implementar; el paso del trabajo lee `payload.get("test", False)`.
- [ ] Web (`test_web_modpacks.py`): `test_build_as_test_queues_a_test_job` (`POST /modpacks/{id}/builds` con
  `prueba=1`).
- [ ] Commit `feat(builds): a test build, numbered like any other, that never moves the latest pointer`.

### Tarea 2: aprobar, y nunca recomendar una prueba (B1, H2, W11, W12)

**Archivos:** `src/database/modpack.py`, `src/services/modpack.py`, `src/routers/web/modpacks.py`,
`src/routers/v1/modpack.py`, `docs/decisions.md` (filas), tests `tests/test_builds.py`,
`tests/test_web_modpacks.py`, `tests/test_routes_roles.py` si hace falta.

**Interfaces que produce:**
- `ModpackDatabase.build_approve(bundle_id: str, number: int) -> bool`: `update_one({bundle_id, number,
  "test": True}, {"$set": {"test": False}})`; `True` si cambió.
- `ModpackService.build_approve(bundle_id: str, number: int, actor: Actor, recommend: bool = False) -> ApproveResult`
  con `ApproveResult(bundle: ModpackBundle, approved: bool, in_force: Optional[int], newer: list[int])`:
  aprobar mueve `latest_build` con `$max`, recomienda si se pide, audita `build.approve` (`{"number", "recommend"}`)
  y avisa `📦 {pack}: build {n} aprobado.` solo si `approved`.
- `ModpackService.build_recommend` rechaza una prueba: `HTTP_409_CONFLICT("Build {n} is a test: approve it first")`.
- Web: `POST /modpacks/{id}/builds/{n}/aprobar` (Form `recomendar` opcional) → `#builds` con un aviso:
  «Build {n} aprobado.» + «El build {in_force} sigue vigente.» si `in_force != n` (W11) + «Hay builds
  posteriores: {newer}.» si los hay (W12).
- `/v1`: `POST /modpack/{id}/build/{number}/approve?recommend=`.

- [ ] Tests: `test_approving_moves_latest_and_only_forward`, `test_approve_and_recommend_does_both`,
  `test_approving_twice_is_a_no_op_the_second_time`, `test_recommending_a_test_is_refused` (servicio y
  `/v1`), web `test_a_test_row_offers_approve_and_approve_and_recommend_never_recommend`,
  `test_approving_an_old_test_says_which_build_stays_in_force`.
- [ ] Filas en «Acciones y roles»: `ModpackService.build_approve` (editor).
- [ ] Commit `feat(builds): approve a test build, alone or with recommending it`.

### Tarea 3: una prueba instalada no es piso hasta aprobarla (H1)

**Archivos:** `src/services/jobs.py` (hacer público `_installed` como `installed_of(last: Optional[Job]) -> Optional[int]`),
`src/services/modpack.py` (`_floor_sources`), test `tests/test_resolution_service.py` (base).

**Interfaz:** `_floor_sources` lee, por cada servidor vinculado, `installed_of(await self.jobDB.last_real_update(f"server:{id}"))`;
si ese número es un build **de este pack** (la actualización tiene `payload.bundle_id == str(bundle.id)`) con
`test=True`, el escaneo de ese servidor no entra en `linked`. Una lectura de builds para todos los números a
la vez (`build_find_numbers`).

- [ ] Tests: `test_a_server_running_an_unapproved_test_is_not_a_floor`,
  `test_once_approved_the_test_servers_scan_is_a_floor_again`,
  `test_a_server_running_a_normal_build_is_still_a_floor`.
- [ ] Commit `fix(resolution): a server running an unapproved test build sets no floor`.

### Tarea 4: nombre y estrella (B2, B3, W13–W15)

**Archivos:** `src/database/modpack.py`, `src/services/modpack.py`, `src/routers/web/modpacks.py`,
`src/routers/v1/modpack.py`, `docs/decisions.md`, tests `tests/test_builds.py`, `tests/test_web_modpacks.py`.

**Interfaces:**
- `ModpackDatabase.build_set(bundle_id: str, number: int, fields: dict[str, Any]) -> ModpackBuild`: `$set` de
  solo `name` o `starred` (cualquier otra clave, `ValueError`); 404 si no existe.
- `ModpackService.build_name(bundle_id, number, name: Optional[str], actor) -> ModpackBuild` (normaliza:
  recorta, tope 60, vacío → `None`; audita `build.name`) y
  `ModpackService.build_star(bundle_id, number, starred: bool, actor) -> ModpackBuild` (audita `build.star`).
- Web: `POST /modpacks/{id}/builds/{n}/nombre` (Form `nombre`) y `POST /modpacks/{id}/builds/{n}/destacar`
  (Form `estado` = `si` | `no`, nunca alternar: W14) → `#builds`.
- `/v1`: `PATCH /modpack/{id}/build/{number}` con `{"name"?, "starred"?}`.

- [ ] Tests: `test_naming_a_build_never_rewrites_its_lock` (un `build_set` con la copia vieja del lock en
  memoria no lo cambia), `test_a_name_is_trimmed_capped_and_empty_is_none`, `test_starring_twice_stays_starred`,
  `test_a_name_with_html_is_escaped_everywhere` (lista, selectores del servidor, comparación, Discord).
- [ ] Filas: `build_name`, `build_star` (editor).
- [ ] Commit `feat(builds): a build's name and star, written apart from what it froze`.

### Tarea 5: la lista de builds (B3, H3, W16, W20)

**Archivos:** crear `src/library/builds.py`; `src/web/templates/modpacks/_builds.html`,
`src/routers/web/modpacks.py` (`_builds` y la página), test crear `tests/test_build_list.py`, y
`tests/test_web_modpacks.py`.

**Interfaces:**
- `builds_view(builds: Sequence[ModpackBuild], recommended: Optional[int], latest: Optional[int],
  installed: set[int], starred_only: bool) -> BuildsView` con `BuildsView(shown: list[ModpackBuild],
  folded: list[ModpackBuild], previous: dict[int, int])`. Puro.
  - `shown`: destacados primero (más nuevo primero), luego los demás más nuevos primero; nunca se pliegan el
    recomendado, el último, uno instalado en un servidor, una prueba que es el build más nuevo, ni los 3
    más nuevos. El resto, en `folded`. Con `starred_only`, `shown` son solo los destacados y el resto va a
    `folded`.
  - `previous[n]` = el número existente anterior a `n` (W20); el primero no tiene.
- Plantilla: «Construir como prueba» junto a «Crear build» (`hx-post` con `prueba=1`); por fila: insignia
  «Prueba», el nombre, la estrella (formulario con `estado`), «Aprobar» y «Aprobar y recomendar» en una
  prueba, «Recomendar» en las demás, «Comparar con el anterior» (`/builds/compare?de={previous}&a={n}`),
  `.mrpack`. Lo plegado en `<details>` «{N} builds anteriores». «Solo destacados» es un `hx-get` con
  `?destacados=1` que no se recuerda.
- Los números instalados: `JobService.installed_build` de cada servidor vinculado (ya se leen en la página).

- [ ] Tests puros: `test_starred_come_first_and_old_unstarred_fold`, `test_what_matters_never_folds`,
  `test_a_failed_test_folds_once_newer_builds_exist` (H3), `test_previous_skips_gaps`,
  `test_two_hundred_builds_fold_all_but_the_shown`. Web: `test_builds_offer_build_as_test`,
  `test_compare_with_previous_links_the_existing_previous`.
- [ ] Commit `feat(builds): stars first, the old ones folded, and each build compares with the one before`.

### Tarea 6: la marca viaja y se nombra (W7, W8, W9, W10, W21)

**Archivos:** `src/web/templates/modpacks/_builds.html` y el diálogo de build (`_decide_dialog.html`),
`src/routers/web/modpacks.py` (`POST /builds`, la ruta de crear desde actualizaciones),
`src/web/templates/servers/_update.html`, `src/routers/web/servers.py:151`, `src/services/update_check.py`
(textos de NO_BUILD), `src/library/onboarding.py`, `src/services/pterodactyl.py` (`drift`, `compare`),
`src/web/templates/activity/_job_parts.html`, tests de cada uno.

- Diálogo de bloqueado y crear desde actualizaciones: campo oculto `prueba` hasta `build_request(test=…)` (W7).
- Selectores de simular e instalar: `Build {n} · prueba · {nombre}`; con solo pruebas, instalar
  preselecciona la más nueva; el diálogo de instalar dice «Es un build de prueba.» (W8).
- Solo pruebas: el servidor dice «Este pack solo tiene builds de prueba: apruébalo para instalarlo por defecto.»;
  la guía detalla «Hay builds de prueba ({n}); aprobar uno lo cuenta.» (W9). El chequeo de actualizaciones
  sigue sin correr (no hay build vigente) con ese texto.
- Un servidor cuyo build instalado es una prueba se compara contra **ese** build, no el vigente (W10):
  `drift` y `compare` sin `build` usan `installed_of(...)` cuando es una prueba de su pack.
- «Mis trabajos» y Trabajos nombran «Build de prueba» (`payload.test`) (W21).

- [ ] Tests: `test_the_blocked_dialog_keeps_the_test_mark`, `test_server_selects_name_test_builds`,
  `test_with_only_test_builds_install_preselects_the_newest`, `test_only_test_builds_say_so_on_the_server`,
  `test_a_server_running_a_test_compares_against_it`, `test_a_test_builds_job_says_so`.
- [ ] Commit `feat(builds): the test mark travels through every way to build, and every place names it`.

### Tarea 7: la comparación explicada (B4, W17, W18)

**Archivos:** `src/library/changes.py`, `src/services/modpack.py` (`compare_builds`),
`src/web/templates/modpacks/compare.html`, `_changes.html`, tests `tests/test_changes.py` (puro),
`tests/test_web_modpacks.py`.

**Interfaces:**
- `request_changes(before: ResolveRequest, after: ResolveRequest) -> list[RequestChange]`, con
  `RequestChange(field: str, label: str, before: str, after: str)`. Compara `target.game_version`
  («Minecraft»), `target.loader` («Loader»), `target.channels` («Canales»), `target.bridges` («Loaders
  secundarios»), `pins` («Fijado», por mod), `replacements` («Reemplazos»), `channel_exceptions`
  («Excepción de canal»), `server_decisions` («En el servidor»), `native_only` («Pasado a nativa»). Nunca
  `floors`, `free`, `unchecked`, `origins` (W18). Un campo que falta en el `model_fields_set` de un lado no
  se compara.
- `change_reason(entry: LockEntry, pins: Mapping[str, str]) -> str`: «fijado» si está en `pins`; «del pack» /
  «agregado a mano» por `origin`; «dependencia de {titles}» por `requested_by`; si no, `entry.reason`.
- `compare_builds` devuelve también `request: list[RequestChange]`, `reasons: dict[str, str]` (por proyecto,
  del lado `after`; los quitados, del `before`) y `summary: ChangeSummary(up: int, down: int, added: int,
  removed: int)`; `pinned` sale de `after.request.pins`, no de los pins actuales del pack (W17).
- Página: el encabezado nombra cada lado («Build 14 · Recomendado» / «Build 15 · prueba · con Iris 1.7»), el
  resumen «{k} cambios: {up} suben, {down} bajan, {added} nuevos, {removed} quitados» (solo las partes que no
  son cero), «En el pedido» si hay cambios, y el porqué junto a cada mod.

- [ ] Tests puros: `test_request_changes_skip_floors_and_missing_fields`,
  `test_request_changes_name_pins_and_channels`, `test_change_reason_prefers_the_pin_then_the_origin`.
  Servicio (base): `test_compare_marks_pins_from_the_build_not_the_pack`. Web:
  `test_compare_shows_summary_request_and_reasons`.
- [ ] Commit `feat(builds): a comparison that says what changed in the request and why each mod moved`.

### Tarea 8: cierre

- [ ] `./scripts/test-mongo.sh` en verde.
- [ ] El barrido visual con chequeos nuevos: la lista con plegados y una prueba, el formulario de nombre a
  360 px, la comparación explicada; claro y oscuro; `./scripts/visual-sweep.sh`.
- [ ] `docs/decisions.md`: d-7985ee-a99db7 y d-7985ee-3bb53b dicen qué se construyó; el roadmap, la tanda 2;
  el registro de la sesión; «Estado» de este plan.
- [ ] Proponer al dueño el texto de ayuda de «Construir como prueba» (los textos de ayuda son suyos).

## Estado

- **Tareas 1–7**, hechas el 2026-10-26 (96965ce..11a2520), cada una con su test visto fallar (los de la web y
  los puros); el gate en verde. Decisiones del ejecutor en el camino: `JobDatabase.last_real_updates` lee la
  última instalación de todos los servidores en una consulta (los tests de lecturas de página contaban una por
  servidor); `ModpackService.installed_builds` da los builds instalados a la lista; `change_reason` devuelve
  un `ChangeReason` para que la página titule a quien pidió el mod; el mensaje programado de deriva
  (`drift`) sigue comparando con el build vigente, solo la página del servidor compara una prueba en curso
  contra sí misma; el aviso del diálogo de instalar es una línea si hay pruebas en la lista.
- **Tarea 8**: a83132a (un build plegado en singular; el barrido pulsa `#build-create`, que ahora tiene un
  vecino, y siembra un build destacado, uno con nombre y pin, y una prueba). Revisión final en contexto
  limpio: la marca de prueba se perdía en un DELETE (htmx manda `hx-vals` en la query), en los diálogos de
  confirmación y en el diálogo de un trabajo bloqueado; un test de base comparaba una fecha con zona contra
  una sin zona; el 409 de recomendar una prueba estaba en inglés; el resumen decía «sube» sin saberlo.
  Arreglados en a10676f. Con la VM recreada por el dueño: test-mongo 1846 verdes, cuatro tests de base vistos
  fallar con su arreglo quitado en una copia de la rama, y el barrido: el selector de simular desbordaba la
  página del servidor 183 px a 360 px (b02a8e9) y la tabla de builds escondía sus acciones a 1280 px tras un
  desplazamiento lateral (dos columnas y un chequeo nuevo, 1a6ad56). Los menores de la revisión están en el
  roadmap.
