# Plan: O8 tanda 2, historia y notas

> **Para quien lo ejecute:** test primero, visto fallar, y un commit por tarea encadenado al gate
> (`./scripts/check.sh > $TMPDIR/log 2>&1 && git commit …`). Los tests de base (`@pytest.mark.network`)
> corren con `./scripts/test-mongo.sh`. Se marca cada tarea hecha en «Estado».

**Meta:** la historia de un mod, las notas con veredicto sobre builds y versiones, la marca «estable» de un build,
las sugerencias que salen de ellas, y una página para cada build.

**Arquitectura:** una colección nueva, `VersionNote` (las notas); lo demás se deriva. La historia de
los orígenes reemplazados vive en `ModpackBundle.pin_history` (no en `PinRecord`: quitar un mod borra su registro
de fijación, y la historia sobrevive, P5), escrita en el mismo `update_one` que cada escritura de un registro. La
historia se arma en una función pura de `src/library/history.py` desde lecturas con proyección. Las notas se
escriben en un diálogo fuera de `#mods` (P1).

**Fuentes:** spec `docs/superpowers/specs/2026-10-25-o8-versiones-con-historia-design.md` §3, §8; d-7985ee-a99db7
(D2–D5, D10–D13), d-7985ee-a68b5c (P1–P5, el texto de ayuda), d-7985ee-16be41 (T1–T6); el user-walk
`docs/research/2026-10-26-user-walk-o8-notas.md` (N1–N23). ~4 sesiones.

## Restricciones globales

- Nada reescribe lo guardado: los campos nuevos tienen default (`pin_history = {}`, `ModpackBuild.stable = False`,
  `ServerScan.bundle_id = None`); sin runbook (no se borra ni transforma nada).
- Un build nunca cambia su `lock` ni su `request`: `stable` se escribe con `build_set` (`$set` de ese campo).
- Una nota: veredicto ∈ {`works`, `fails`, `issues`, `untested`}, texto ≤ 4000 caracteres, error ≤ **64 KiB**
  guardado como los primeros **48 KiB** + `\n… {N} KB cortados …\n` + los últimos **16 KiB** (P2). Al guardar se
  enmascaran con `***`: el valor de `--accessToken`, de `token=` y `access_token=`, las URLs
  `https://discord(app)?.com/api/webhooks/…` y las IPv4 (P3). Nunca va a Discord.
- El changelog se pide a Modrinth solo al desplegarlo y queda en el catálogo (P4); dibujar una historia no llama a
  Modrinth (test que cuenta las llamadas, como en O9 D2).
- Roles (D10): escribir una nota, editar y archivar la propia, marcar estable: **editor**; archivar la de otro:
  **operador** (`operator_only`). Autor por `iss` + `sub`. Cada método nuevo que escribe: su fila en
  `## Acciones y roles`, y audita.
- `/v1` expone las notas con los mismos métodos del servicio (D13); su lista no trae `error`.
- Textos nuevos del panel: etiquetas y mensajes, no ayuda. Ningún texto de ayuda nuevo sin el dueño.

## Foco de revisión

1. Un registro de fijación escrito por cada camino (fijar, «Usar esta», quitar fijación, importar, escanear,
   desvincular, quitar el mod): lo que reemplaza va a `pin_history` (Tarea 1, un test por camino).
2. Una sesión vencida o una pestaña cerrada con una nota a medio escribir: el borrador vuelve (Tarea 6).
3. Un error pegado de 400 KB con un token: se guarda cortado, enmascarado y con el aviso (Tarea 4).
4. «Fijar la versión del último build estable» con la versión de Minecraft cambiada desde entonces: no se ofrece
   (Tarea 8).
5. La historia de un pack con 200 builds y escaneos diarios: una consulta por fuente, con proyección (Tarea 7).

---

### Tarea 1: la historia de los orígenes (D2, N1–N3, P5)

**Archivos:** `src/model/modpack/project.py`, `src/database/modpack.py` (`bundle_pin_record_set`,
`bundle_project_remove`), `src/library/pins.py`, `src/services/modpack.py`, `src/services/pterodactyl.py`; tests
`tests/test_pin_history.py` (crear; puros y de base).

**Interfaces:**
- `PastOrigin(BaseModel)`: `origin: PinOrigin`, `until: datetime`, `removed_mod: bool = False`.
- `ModpackBundle.pin_history: dict[str, list[PastOrigin]] = {}`.
- `ModpackDatabase.bundle_pin_record_set(..., before: Optional[PinRecord] = None, at: Optional[datetime] = None)`:
  los orígenes de `before` que no siguen en `record` (por `(source, server_id, version_id)`) se agregan a
  `pin_history.<id>` con `$push` `{$each, $slice: -40}` en el mismo update. Cada llamada de los servicios pasa el
  registro que leyó.
- `bundle_project_remove`: los orígenes del registro que quita van a `pin_history` con `removed_mod=True`.
- `past_origins(history: Sequence[PastOrigin]) -> list[PastOrigin]` (pura, `src/library/pins.py`): sin duplicados
  por `(source, server_id, version_id)`, los 20 más nuevos, y al menos el más nuevo de cada fuente.

- [ ] Tests: uno por camino (`test_pinning_by_hand_keeps_the_replaced_origin`, `…_using_another_origin_…`,
  `…_unpinning_…`, `…_a_scan_replacing_its_server_origin_…`, `…_unlinking_…`, `…_importing_…`,
  `test_removing_the_mod_keeps_its_history_and_marks_it`), `test_past_origins_dedupe_cap_and_keep_one_per_source`.
- [ ] Commit `feat(pins): what a pin's origins replaced is kept, by every path that writes them`.

### Tarea 2: la auditoría dice de qué a cuál; los escaneos dicen su pack (N4, N5)

**Archivos:** `src/services/modpack.py` (`pin_set`, `pin_origin_select`, `pin_unset`), `src/services/pterodactyl.py`
(lo que escribe un escaneo), `src/model/pterodactyl/scan.py`, tests de cada uno.

- `bundle.pin` guarda `from: {version_id, version_number} | None` y `to: {…} | None`; lo que escribe un escaneo se
  audita como `bundle.pin.server` con el servidor. Etiquetas en `src/web/audit_labels.py`.
- `ServerScan.bundle_id: Optional[str] = None`, puesto al escanear un servidor vinculado.

- [ ] Tests: `test_a_pin_change_audits_from_and_to`, `test_a_scan_that_writes_origins_is_audited`,
  `test_a_scan_remembers_its_pack`.
- [ ] Commit `feat(audit): a pin change says from which version to which, and a scan its pack`.

### Tarea 3: «estable» en un build (T3b, T5 a medias)

**Archivos:** `src/model/modpack/project.py`, `src/database/modpack.py` (`build_set` acepta `stable`),
`src/services/modpack.py`, `src/routers/web/modpacks.py`, `src/routers/v1/modpack.py`, `_builds.html`,
`src/library/builds.py`, `docs/decisions.md`.

- `ModpackBuild.stable: bool = False`. `ModpackService.build_stable(bundle_id, number, stable: bool, actor) -> ModpackBuild`:
  una prueba sin aprobar → 409 «Aprueba la prueba antes de marcarla estable.»; audita `build.stable`.
- Web `POST /modpacks/{id}/builds/{n}/estable` (`estado` = `si` | `no`); `/v1` `PATCH …/build/{n}` con `stable`.
- La fila: etiqueta verde «estable» y el botón; `builds_view` nunca pliega un build estable.

- [ ] Tests: `test_a_build_is_marked_stable_and_unmarked`, `test_a_test_is_approved_before_stable`,
  `test_a_stable_build_never_folds`, web de la etiqueta y el botón.
- [ ] Commit `feat(builds): a build is marked stable, apart from its star and the recommended one`.

### Tarea 4: las notas (D3–D5, D10, D11, D13, P2, P3, N14–N16, N18–N22)

**Archivos:** crear `src/model/modpack/note.py` (registrado en `init_beanie`), `src/database/note.py`,
`src/library/notes.py` (puro), `src/services/notes.py` (`NoteService`, en `src/dependences.py`),
`src/routers/v1/notes.py`, `docs/decisions.md`; tests `tests/test_notes.py` (puros), `tests/test_note_service.py` (base).

**Interfaces:**
- `VersionNote(Document)`: `bundle_id: str`, `build_number: Optional[int]`, `project_id: Optional[str]`,
  `version_id: Optional[str]`, `version_number: Optional[str]`, `server_id: Optional[str]`, `server_name: Optional[str]`,
  `verdict: Literal["works","fails","issues","untested"]`, `text: str`, `error: Optional[str]`, `author: ActorRef`,
  `at: datetime`, `edited_at: Optional[datetime]`, `previous: list[NoteVersion]` (texto, veredicto y error
  anteriores), `archived: bool = False`, `rev: int = 0`, `key: str` (idempotencia). Índices
  `(bundle_id, project_id, version_id)`, `(bundle_id, build_number)`, único `key`.
- Puras: `cut_error(text: str) -> tuple[str, int]` (el texto guardado y los KB cortados), `mask_secrets(text: str) -> str`.
- `NoteService.create(bundle_id, target: NoteTarget, verdict, text, error, server_id, key, actor) -> VersionNote`
  (la misma `key` devuelve la misma nota), `edit(note_id, verdict, text, error, rev, actor)` (solo el autor; 409
  «La nota cambió mientras tanto» si `rev` no coincide), `archive(note_id, actor)` (el autor, u `operator_only`),
  `find(bundle_id, *, build_number=None, project_id=None, archived=False, limit=…)`; una nota del pack se archiva
  con el pack al borrarlo.
- `/v1`: `POST/GET /v1/modpack/{id}/notes`, `GET/PATCH/DELETE /v1/note/{id}` (la lista sin `error`).

- [ ] Tests puros: `test_an_error_over_64_kib_keeps_its_head_and_tail`, `test_secrets_are_masked`.
  Base: `test_a_note_on_a_build_and_on_a_mod_version`, `test_the_same_key_is_one_note`,
  `test_only_the_author_edits_and_the_old_text_is_kept`, `test_two_edits_from_one_read_one_is_refused`,
  `test_an_editor_archives_only_their_own`, `test_archived_notes_are_left_out`, `test_a_note_keeps_its_server_name`.
  Contrato de `/v1` y su barrido de roles.
- [ ] Commit `feat(notes): notes with a verdict on a build or a mod's version, edited by their author, archived`.

### Tarea 5: la página del build (T1, T5)

**Archivos:** `src/routers/web/modpacks.py` (`GET /modpacks/{id}/builds/{n}`), crear
`src/web/templates/modpacks/build.html`, `_builds.html` (el número enlaza; «✔ 2» / «✘ 1»), tests web.

- La página: marcas y acciones (las de la fila), «¿Funcionó?» con el conteo por veredicto, las 5 últimas notas
  («Ver todas», «Mostrar archivadas»), «Agregar nota» (Tarea 6), y sus mods con versión. 404 legible si no existe.
- `builds_view` recibe `verdicts: dict[int, tuple[str, int]]` (último veredicto y su cuenta), de una consulta.

- [ ] Tests: `test_the_build_page_shows_its_marks_notes_and_mods`, `test_a_missing_build_is_a_404_page`,
  `test_the_list_shows_each_builds_verdict`.
- [ ] Commit `feat(builds): a page for each build, with what its notes say`.

### Tarea 6: el diálogo de una nota y su borrador (P1, N7, N8)

**Archivos:** crear `src/web/templates/notes/_dialog.html` y `_note.html`; `src/routers/web/notes.py`; `base.html`
(`#note-dialog` fuera de `#main`); `src/web/static/app.js` (el borrador); tests web.

- «Agregar nota» (`hx-get /modpacks/{id}/notas/nueva?build=15` o `?mod=…&version=…`) abre el diálogo en
  `#note-dialog`: veredicto, servidor (los vinculados, opcional), comentario, error con el aviso «No pegues
  contraseñas; los tokens y las IPs se ocultan» y, si pasa de 64 KiB, «Se guardarán el principio y el final».
  Un campo oculto `key` (aleatorio, de la plantilla) evita el doble.
- El borrador: `app.js` guarda los campos de un `form[data-draft="<pack>:<destino>"]` en `localStorage` al
  escribir, los repone al abrir el mismo destino y los borra al guardar bien; todo en try/catch.
- Editar abre el mismo diálogo con la nota; archivar con «Deshacer».

- [ ] Tests: `test_add_note_opens_the_dialog_outside_mods`, `test_saving_answers_the_note_and_closes`,
  `test_the_dialog_warns_about_secrets_and_size`, `test_the_form_carries_a_draft_key_and_an_idempotency_key`; el
  barrido abre el diálogo, escribe, recarga y ve el borrador.
- [ ] Commit `feat(notes): a note is written in a dialog, and a draft survives a reload`.

### Tarea 7: la historia de un mod (D2, D12, T2, T3, N6, N11, N17)

**Archivos:** crear `src/library/history.py` (puro), `src/services/modpack.py` (`mod_history`),
`src/database/modpack.py` y `src/database/pterodactyl.py` (lecturas con proyección), `src/database/audit.py`
(consulta por `details.project_id` con índice), crear `src/web/templates/mods/_history.html`, `_mods.html` (el
desplegable), `mods/page.html` (la sección); rutas `GET /modpacks/{id}/projects/{pid}/historia?antes=` y la ficha.

**Interfaces:**
- `HistoryEntry` (fecha, tipo: `build` | `server` | `pin` | `origin` | `note` | `removed` | `added`, versión, texto,
  enlaces) y `mod_history(builds, scans, events, past, notes) -> list[HistoryEntry]` (pura): solo donde cambió,
  tramos iguales agrupados («Builds 3–14 · 1.6.4»), más nuevo primero.
- `ModpackService.mod_history(bundle_id, project_id, before: Optional[datetime], limit=20) -> HistoryPage`: los
  builds con `lock.entries` filtrado al proyecto (`$elemMatch`), los escaneos de los servidores con `files.$`
  y `bundle_id` igual al pack (o sin él si el servidor sigue vinculado: «servidor vinculado ahora»), los eventos
  de auditoría del proyecto, `pin_history`, las notas. Ninguna llamada a Modrinth.
- Cada versión: lo del catálogo y «▸ Changelog» (`hx-get …/changelog?version=` con `toggle once`), que pide a
  Modrinth con el plazo de la comparación, lo deja en el catálogo y ofrece «Reintentar» si no llegó.

- [ ] Tests puros: `test_history_keeps_only_changes_and_groups_runs`, `test_history_marks_removed_and_added`.
  Base: `test_mod_history_reads_one_query_per_source`, `test_another_packs_scans_are_left_out`,
  `test_drawing_a_history_never_calls_modrinth`. Web: el desplegable carga al abrir; «Ver más»; la ficha;
  `test_a_changelog_is_asked_only_when_unfolded`.
- [ ] Commit `feat(history): a mod's history, only what changed, loaded when opened`.

### Tarea 8: las sugerencias (T4, N13)

**Archivos:** `src/library/history.py` (puro), `src/services/modpack.py`, `_builds.html`, `_mods.html`,
`_pin_confirm.html`, tests.

- `last_stable(builds, target) -> Optional[ModpackBuild]` y `suggested_version(project_id, builds, notes, target, via)
  -> Optional[Suggested]` (versión y su respaldo: un build estable, o una nota «funcionó»): solo el destino actual y la
  misma vía; manda el último veredicto de cada versión; una nota del build cuenta si ninguna del mod la contradice;
  las archivadas no cuentan.
- Builds: «Recomendar el último estable (build N)» si el recomendado no es estable; un diálogo de confirmación que
  dice si es volver atrás.
- Tarjeta: «Fijar la versión del último build estable (Iris 1.6.4)» o «Fijar la última que funcionó»; abre el
  diálogo de fijar siempre (también sin baja), con «Respaldo: build 14 estable · autor · fecha» o la nota.

- [ ] Tests puros: `test_the_suggestion_prefers_the_last_stable_build`, `test_without_stable_builds_the_last_works_note`,
  `test_another_target_or_way_is_never_suggested`, `test_a_later_fails_note_wins`. Web: los dos botones y el
  diálogo con su respaldo.
- [ ] Commit `feat(history): suggest the last stable build, and a mod's version from it or from its notes`.

### Tarea 9: las notas en la comparación (T6, N23)

**Archivos:** `src/services/modpack.py` (`compare_builds`), `compare.html`, `_changes.html`, tests.

- El encabezado suma «estable» y el veredicto de cada build; junto a cada cambio, las 2 últimas notas de esa
  versión en el pack y «Historia →».

- [ ] Tests: `test_compare_shows_each_builds_verdict_and_each_versions_notes`.
- [ ] Commit `feat(builds): the comparison shows what the notes say`.

### Tarea 10: cierre

- [ ] `./scripts/test-mongo.sh` en verde; los tests de base nuevos vistos fallar.
- [ ] El barrido: la página del build, el diálogo de una nota (y su borrador), la historia abierta en la tarjeta y
  en la ficha, a 360, 768 y 1280 px, claro y oscuro; `./scripts/visual-sweep.sh`.
- [ ] Revisión en contexto limpio de la tanda; docs (decisiones, roadmap, registro, «Estado»).

## Estado

Nada hecho todavía.
