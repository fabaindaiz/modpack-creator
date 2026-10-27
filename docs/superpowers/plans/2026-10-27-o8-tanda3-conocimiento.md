# Plan: O8 tanda 3, la base de conocimiento

> **Para quien lo ejecute:** test primero, visto fallar, y un commit por tarea encadenado al gate
> (`./scripts/check.sh > $TMPDIR/log 2>&1 && git commit …`). Los tests de base (`@pytest.mark.network`)
> corren con `./scripts/test-mongo.sh`. Se marca cada tarea hecha en «Estado».

**Meta:** una base global de combinaciones que fallan (dos mods, o una versión sola), marcada como sospecha desde la
comparación de builds, confirmada o descartada por un operador, que avisa (nunca bloquea) en los packs, los builds,
el tablero y donde se actualiza.

**Arquitectura:** una colección nueva, `KnownIssue`, con cada extremo de un rango guardado entero (id, número, fecha,
Minecraft y loaders) para que dibujar un aviso no lea Modrinth. La regla del rango es una función pura en
`src/library/knowledge.py`, por fecha de publicación y solo dentro de la misma rama (como `direction` en
`src/library/changes.py`). Los avisos se calculan con una sola función compartida (`warnings_for`, en
`src/services/knowledge.py`) que usan `ModpackService` (`_preview`, la página del pack, la del build, el tablero) y
`UpdateCheckService` (el aviso del chequeo diario). Un aviso nunca entra en `lock.problems` (K1).

**Fuentes:** spec `docs/superpowers/specs/2026-10-25-o8-versiones-con-historia-design.md` §3.3, §4, §8 y su
enmienda; d-7985ee-a99db7 (D6–D10, D13), d-7985ee-57d185 (K-Q1–K-Q5, el texto de ayuda); el user-walk
`docs/research/2026-10-27-user-walk-o8-conocimiento.md` (K1–K27). ~3 sesiones.

## Restricciones globales

- Nada reescribe lo guardado: colección nueva, empieza vacía; sin runbook. `KnownIssue` registrado en `init_beanie`.
- **Solo avisa** (D8): ningún aviso entra en `lock.problems`, ni cambia una resolución, un pin o una actualización.
- Estados: `suspected`, `confirmed`, `rejected` (K-Q2). Arreglarse es `fixed_in` de cada lado, la primera versión
  buena (exclusiva); `broken_from` es la primera rota (inclusiva); vacío es «cualquiera» / «aún no se sabe».
- El segundo mod es opcional (K-Q4): sin él, la entrada es «Versión rota».
- Los pares se guardan en orden fijo (`a.project_id < b.project_id`); una versión rota sola va en `a`.
- Una sospecha avisa solo en la comparación de builds, en «Conocimiento» y como «N sospechas por revisar» en el
  tablero de un operador; los packs avisan solo lo `confirmed` (K-Q1).
- Roles (D10, K22, K23): marcar, sumarse y editar una sospecha propia, y retirar la propia: **editor**; confirmar,
  descartar y editar una confirmada o ajena: **operador** (`operator_only` en el servicio, o chequeo inline como
  `NoteService.archive` donde el autor también puede). Crear siempre deja `suspected`. Cada método nuevo que escribe:
  su fila en `## Acciones y roles`, audita, y su etiqueta en `src/web/audit_labels.py` (área «Packs»).
- Dibujar un aviso no llama a Modrinth (test que cuenta las llamadas, como en O9 D2). Escribir una marca puede leer
  una versión de Modrinth (una lectura), nunca escribir.
- «Conocimiento» nunca muestra el `error` de una nota (K21): veredicto, texto, pack y autor.
- `/v1` usa los mismos métodos del servicio (D13).
- Textos nuevos: etiquetas y mensajes. El único texto de ayuda es el de K-Q5, tal cual está en el walk.

## Foco de revisión

1. Un pack 1.20.1 con Sodium 0.5.11 (publicada después de 0.6.0 para 1.21) y una entrada «Sodium desde 0.6.0» sin
   Minecraft: no avisa (Tarea 1, `test_a_backport_on_another_branch_is_not_in_range`).
2. Una entrada confirmada con el build en curso: el build se construye igual (Tarea 4,
   `test_a_confirmed_issue_never_blocks_a_build`).
3. Un editor que manda `status=confirmed` por `/v1`, o edita una confirmada: 403 (Tarea 2 y 3).
4. El tablero con diez packs y una base con cien entradas: una lectura de la base, y fechas del catálogo solo de los
   mods que coinciden; ninguna llamada a Modrinth (Tarea 4, `test_warnings_read_the_base_once_and_never_modrinth`).
5. «Arreglado en 1.7.3»: el pack en 1.7.2 sigue avisado, el que está en 1.7.3 no (Tarea 1).

---

### Tarea 1: el modelo y la regla del rango (K3, K4, K14, K18, K-Q2, K-Q4)

**Archivos:** `src/model/modpack/known_issue.py` (crear), `src/model/__init__.py` (`init_beanie`),
`src/library/knowledge.py` (crear); tests `tests/test_knowledge_range.py` (crear, puros).

**Interfaces:**
- `IssueVersion(BaseModel)`: `version_id`, `version_number`, `published: datetime`, `game_versions: list[str]`,
  `loaders: list[str]`.
- `IssueSide(BaseModel)`: `project_id`, `title`, `broken_from: Optional[IssueVersion] = None`,
  `fixed_in: Optional[IssueVersion] = None`.
- `KnownIssue(Document)`: `a: IssueSide`, `b: Optional[IssueSide] = None`, `game_version: Optional[str]`,
  `loader: Optional[str]`, `status: Literal["suspected", "confirmed", "rejected"]`, `summary: str` (≤ 200),
  `note_ids: list[str]`, `seen_in: list[str]` (bundle ids), `author: ActorRef`, `at`, `updated_at`, `rev: int = 0`,
  `key: str`. Índices: `a.project_id`, `b.project_id`, `key` único.
- `side_state(side: IssueSide, version: VersionFacts) -> Literal["in", "out", "branch"]`: «branch» si la versión no
  comparte Minecraft o loader con el extremo puesto (`broken_from`, si no `fixed_in`); «out» si se publicó antes de
  `broken_from` o en o después de `fixed_in`; si no, «in». Sin extremos, «in».
- `issue_applies(issue: KnownIssue, versions: Mapping[str, VersionFacts], game_version: str, loader: str) -> bool`:
  `game_version`/`loader` de la entrada, si están, iguales a los del pack o (loader) entre los `loaders` de la
  versión de `a` (K18, puentes); cada lado presente en `versions` y «in».
- `KnownWarning(BaseModel)`: `issue_id`, `status`, `summary`, `project_ids: list[str]`, `text: str` («Iris 1.7.0+ ✕
  Sodium 0.6» o «Versión rota: Iris 1.7.2»), `broken: bool`.

- [ ] Tests: `test_a_version_before_broken_from_is_out`, `test_fixed_in_is_the_first_good_version` (1.7.2 in, 1.7.3
  out), `test_a_backport_on_another_branch_is_not_in_range`, `test_no_range_is_any_version`,
  `test_a_pair_applies_only_when_both_mods_are_in`, `test_a_broken_version_needs_no_second_mod`,
  `test_the_loader_is_the_versions_not_the_packs` (Fabric vía Connector en NeoForge),
  `test_minecraft_of_the_issue_must_match_when_set`.
- [ ] Commit `feat(knowledge): a known issue and when a version falls in its range`.

### Tarea 2: escribir en la base (K10, K11, K15, K22, K23, D10)

**Archivos:** `src/database/knowledge.py` (crear), `src/services/knowledge.py` (crear), `src/dependences.py`
(`knowledgeService`), `src/web/audit_labels.py`, `docs/decisions.md` (Acciones y roles); tests
`tests/test_knowledge.py` (crear; de base), `tests/test_routes_roles.py` si hace falta.

**Interfaces:**
- `SideInput(BaseModel)`: `project_id`, `broken_from: Optional[str]` (version id), `fixed_in: Optional[str]`.
- `KnowledgeService(knowledgeDB, modrinthDB, modrinthAPI, noteDB, audit, clock)`:
  - `mark(bundle_id, a: SideInput, b: Optional[SideInput], game_version, loader, summary, note_ids, key, actor)
    -> KnownIssue` (editor): ordena el par; cada id de versión debe ser de su mod (catálogo, y si falta, una lectura de
    Modrinth; desconocido, 422) y `broken_from` < `fixed_in`; la misma `key`, la misma entrada; un par que ya existe
    sin descartar, 409 con su id (`ISSUE_EXISTS`). Siempre `suspected`, `seen_in=[bundle_id]`. Audita
    `knowledge.mark`.
  - `join(issue_id, bundle_id, note_ids, actor)` (editor): suma el pack y las notas, sin duplicar. `knowledge.join`.
  - `edit(issue_id, rev, a, b, game_version, loader, summary, actor)`: una sospecha, su autor u operador; una
    confirmada o descartada, operador (403); `rev` distinto, 409. `knowledge.edit`.
  - `set_status(issue_id, rev, status: Literal["confirmed", "rejected", "suspected"], actor)` (`operator_only`).
    `knowledge.status`.
  - `withdraw(issue_id, actor)`: el autor, solo una sospecha propia; pasa a `rejected`. `knowledge.withdraw`.
  - Lecturas: `get(issue_id)`, `find(project: Optional[str], status: Optional[str], game_version: Optional[str],
    before: Optional[datetime], limit=20) -> list[KnownIssue]`, `suspected_count() -> int`.

- [ ] Tests: `test_marking_orders_the_pair_and_is_suspected`, `test_the_same_key_is_one_entry`,
  `test_marking_an_existing_pair_says_which`, `test_joining_adds_the_pack_and_notes_once`,
  `test_a_version_of_another_mod_is_refused`, `test_an_inverted_range_is_refused`,
  `test_an_editor_cannot_confirm_or_edit_a_confirmed_entry`, `test_the_author_withdraws_their_suspicion`,
  `test_an_edit_from_an_old_revision_is_a_conflict`, y el barrido de roles en verde.
- [ ] Commit `feat(knowledge): mark, join, edit, confirm, reject and withdraw an entry, each with its role`.

### Tarea 3: `/v1` (D13)

**Archivos:** `src/routers/v1/knowledge.py` (crear), su registro en `main.py`; tests `tests/test_v1_knowledge.py`.

- `GET /v1/knowledge` (filtros `project`, `status`, `game_version`), `GET /v1/knowledge/{id}`, `POST /v1/knowledge`
  (mark; un `status` en el cuerpo se ignora), `POST /v1/knowledge/{id}/join`, `PATCH /v1/knowledge/{id}` (edit),
  `POST /v1/knowledge/{id}/status`, `POST /v1/knowledge/{id}/withdraw`. Ninguna respuesta trae errores de notas.

- [ ] Tests: `test_v1_marks_and_reads_back`, `test_v1_status_in_the_body_is_ignored_on_create`,
  `test_v1_editor_status_change_is_403`.
- [ ] Commit `feat(v1): the knowledge base, with the service's roles`.

### Tarea 4: calcular los avisos (K1, K5, K6)

**Archivos:** `src/services/knowledge.py` (`warnings_for`), `src/database/knowledge.py`
(`find_for_projects`); tests `tests/test_knowledge_warnings.py`.

**Interfaces:**
- `warnings_for(knowledgeDB, modrinthDB, versions: Mapping[str, str], game_version: str, loader: str, *,
  suspected: bool = False) -> dict[str, list[KnownWarning]]` (función del módulo): una lectura de la base por los
  proyectos de `versions` (`confirmed`, y `suspected` si se pide); fechas del catálogo solo de las versiones de los
  mods que coinciden; la clave es cada proyecto afectado. Una versión que el catálogo no tiene no avisa.

- [ ] Tests: `test_warnings_read_the_base_once_and_never_modrinth` (cuenta consultas y llamadas),
  `test_a_suspicion_is_left_out_unless_asked`, `test_a_rejected_entry_never_warns`,
  `test_a_confirmed_issue_never_blocks_a_build` (`build_create` con la entrada confirmada).
- [ ] Commit `feat(knowledge): the warnings of a set of versions, from one read of the base`.

### Tarea 5: la comparación y «Marcar como sospechoso» (D6, D7, K2, K17, K-Q1)

**Archivos:** `src/services/modpack.py` (`ChangePreview.known`, `_preview`, `compare_builds`),
`src/routers/web/knowledge.py` (crear), `src/web/templates/knowledge/_mark_dialog.html`, `_warning.html` (crear),
`src/web/templates/modpacks/_changes.html`; tests `tests/test_web_knowledge.py` (crear), `tests/test_builds.py`.

- `ChangePreview.known: dict[str, list[KnownWarning]] = {}`, de todo el lock de llegada; en `compare_builds` con
  sospechas (K-Q1). `_warning.html` es su propia plantilla («⚠ Conocida» / «? Sospecha» / «Versión rota»), nunca la
  de `Problem` (K2).
- Junto a cada mod que cambió: «Marcar como sospechoso» → `GET /modpacks/{id}/conocimiento/marcar?de=&a=&mod=` abre
  el diálogo en `#note-dialog`: el mod fijo; «Rota desde» con «cualquier versión» marcada y «la que llegó en el build
  {a} ({número})» a un clic (D7: nunca llena sola); «Con» elige entre los mods del build `a` o «ninguno: falla
  sola», con su versión de ese build a un clic (K17); «Solo en Minecraft {mc}» y «Solo en {loader}» sin marcar; el
  resumen; las notas de los dos builds para respaldar. Un par que ya existe: el diálogo dice cuál y ofrece
  «Sumarse a la existente». `data-draft` como las notas.

- [ ] Tests: `test_the_comparison_shows_known_and_suspected`, `test_the_mark_dialog_suggests_and_never_fills`,
  `test_the_second_mod_comes_from_the_failing_build`, `test_marking_an_existing_pair_offers_to_join`,
  `test_a_known_warning_is_not_drawn_as_a_problem`.
- [ ] Commit `feat(builds): the comparison shows what the base knows, and a change can be marked as suspect`.

### Tarea 6: la tarjeta, el build y el tablero (K7, K24, K-Q1, K-Q4)

**Archivos:** `src/services/modpack.py` (`pack_warnings`, `build_warnings`), `src/routers/web/modpacks.py`,
`src/routers/web/home.py`, `src/library/attention.py`, `src/web/templates/modpacks/_mods.html`, `build.html`,
`home.html`; tests `tests/test_web_modpacks.py`, `tests/test_attention.py`, `tests/test_web_home.py`.

- `pack_warnings(bundle) -> dict[str, list[KnownWarning]]`: del build vigente (recomendado, si no el último no de
  prueba) y del último escaneo de cada servidor vinculado, cada aviso con dónde («build 14», «Survival»). Solo
  confirmadas. En la tarjeta del mod; una «Versión rota» enlaza la sugerencia de la tanda 2 (`/sugerencia`).
- `build_warnings(bundle_id, number)`: los del lock de ese build (K24), en su página.
- Tablero: `PackFact.known: int` → una tarjeta «⚠ N combinaciones conocidas» que no bloquea (severidad de aviso, no
  la de problema); al operador, «N sospechas por revisar» → `/conocimiento?estado=suspected`.

- [ ] Tests: `test_a_card_warns_of_a_confirmed_pair`, `test_a_card_never_shows_a_suspicion`,
  `test_a_warning_from_a_server_scan_says_which_server`, `test_an_old_build_page_keeps_its_warning`,
  `test_the_dashboard_counts_known_without_blocking`, `test_only_an_operator_sees_suspicions_to_review`,
  `test_a_broken_version_links_the_suggestion`.
- [ ] Commit `feat(knowledge): a pack's cards, a build's page and the dashboard warn of what the base confirmed`.

### Tarea 7: donde se actualiza (K8, K-Q3)

**Archivos:** `src/services/modpack.py` (`update_preview` recibe el lock del build revisado para avisar de todo el
pack, `server_change_preview`), `src/services/update_check.py` (`_message`), `src/dependences.py`; tests
`tests/test_web_updates.py`, `tests/test_web_servers.py`, `tests/test_update_check.py`.

- «Actualizaciones» y la vista previa de un servidor muestran `preview.known` (solo confirmadas) junto a cada mod
  que llega. El mensaje del chequeo diario agrega `⚠ conocida: {text}` por cada aviso de una versión que llega,
  dentro de `DISCORD_LIMIT`. Nada se detiene ni se salta (D8).

- [ ] Tests: `test_the_updates_page_warns_of_a_known_pair`, `test_a_server_preview_warns_of_a_known_pair`,
  `test_the_daily_message_names_a_known_pair_and_still_updates`.
- [ ] Commit `feat(updates): updating warns of a known pair, in the panel and in the daily message`.

### Tarea 8: la página «Conocimiento», el menú y la ayuda (K20, K21, K26, K27, K-Q5)

**Archivos:** `src/routers/web/knowledge.py`, `src/web/templates/knowledge/list.html`, `entry.html`,
`_edit_dialog.html`, `src/web/templates/base.html` (NAV), `src/web/templates/help.html`; tests
`tests/test_web_knowledge.py`, `tests/test_web_help.py`.

- `/conocimiento`: filtros mod, estado y Minecraft; de a 20 con «Ver más». `/conocimiento/{id}`: los dos lados con
  sus rangos, Minecraft y loader, estado, resumen, «visto en» (un pack borrado dice «un pack borrado», sin enlace),
  sus notas (veredicto, texto, pack, autor; nunca el error; «sin notas vigentes» si todas están archivadas), y las
  acciones de cada rol: «Confirmar», «Descartar», «Editar» (con «Arreglado en» elegido entre las versiones del mod
  que tiene el catálogo, de la misma rama), «Retirar mi sospecha».
- NAV: `("knowledge", "/conocimiento", "Conocimiento", <ícono de libro>)` después de Servidores.
- `help.html`: la sección «Qué es la base de conocimiento» (`id="conocimiento"`) después de «Qué es un build», con el
  texto del walk tal cual.

- [ ] Tests: `test_knowledge_lists_filters_and_pages`, `test_an_entry_never_shows_a_notes_error`,
  `test_an_entry_says_when_no_note_backs_it`, `test_a_deleted_pack_shows_without_a_link`,
  `test_actions_follow_the_role`, `test_the_menu_has_knowledge_after_servers`,
  `test_how_it_works_says_what_the_knowledge_base_is`.
- [ ] Commit `feat(knowledge): its page, its place in the menu and what it is in the help`.

### Tarea 9: verificar y documentar

- [ ] `./scripts/test-mongo.sh` en verde.
- [ ] El barrido visual con chequeos nuevos: `/conocimiento`, una entrada, el diálogo «Marcar como sospechoso» desde
  la comparación, y un aviso en una tarjeta (los datos de demo del barrido suman una entrada confirmada y una
  sospecha).
- [ ] Revisión del branch en contexto limpio; los críticos e importantes, con su test visto fallar.
- [ ] Docs: la fila de d-7985ee-a99db7 y la de d-7985ee-57d185 dicen dónde se cumple; roadmap; «Estado»; la entrada
  del registro con lo que salió mal.

## Estado

Nada hecho todavía.
