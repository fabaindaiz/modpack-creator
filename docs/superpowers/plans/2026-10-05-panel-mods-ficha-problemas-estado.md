# Estado al pausar (2026-10-05): acciones para problemas, ficha local de mod y pestañas

La sesión se pausó a pedido del dueño ("cerrar la sesión para actualizar el equipo") en la **Tarea 8
de 8**. Este archivo es lo que hace falta para retomar desde otra máquina: el ledger de la ejecución
(`.superpowers/sdd/…`) está ignorado por git y no viaja; su contenido va copiado al final.

- **Rama:** el trabajo se hizo en `panel-mods-ficha` (sobre `8baecf7`) y, a pedido del dueño al
  cerrar, se **mergeó a `main` y se empujó**. Tenía las Tareas 1 a 7 revisadas una por una y el gate
  verde (942), pero sin la revisión final de toda la rama. **Esa revisión se hizo después, el 2026-10-05:** ver "Revisión final".
- **Spec:** `docs/superpowers/specs/2026-10-05-panel-mods-ficha-problemas-design.md`, aprobada y
  revisada por temas con el dueño.
- **Plan:** `docs/superpowers/plans/2026-10-05-panel-mods-ficha-problemas.md`, revisado con el
  dueño. Se ejecuta con subagentes y una revisión por tarea.
- **Gate en la punta (`a1d32ab`):** 942 tests aprobados contra un MongoDB con
  `--wiredTigerCacheSizeGB 0.25` (ver R4). `./scripts/test-mongo.sh` tal como está muere a mitad
  de camino en la VM de 2 GiB.

## Tareas

| Tarea | Estado | Commits |
|---|---|---|
| 1. Problemas con datos estructurados | hecha, revisada | `aaf00a3` |
| 2. Todas las versiones desde Modrinth | hecha, revisada | `e982c87` |
| 3. Ficha de mod: servicio y rutas | hecha, revisada | `5094b40` |
| 4. Panel lateral y `ui.mod_link` | hecha, revisada | `c60ae7c` |
| 5. Nombres clicables, ningún mod sin ficha | hecha, revisada, 1 ronda de arreglo | `c5b8bf8`, `ec1d950` |
| 6. Pestañas y tarjetas de problema | hecha, revisada | `1efa7e0` |
| 7. Diálogo de build con problemas (modal) | hecha, revisada, 1 ronda de arreglo | `8bab64d`, `a1d32ab` |
| 8. Barrido visual, script del gate y documentos | **pendiente**: se detuvo al empezar, sin cambios | — |

Fuera del plan, en la misma rama: las cinco investigaciones de `docs/research/2026-10-05-*.md`
(`19eab09`).

## Cómo retomar

1. `git switch main && git pull` y crear una rama nueva para seguir (por ejemplo
   `panel-mods-ficha-cierre`).
2. Recrear el espacio de trabajo con la skill subagent-driven-development, con este plan. Las
   Tareas 1 a 7 están hechas (no se vuelven a despachar). El brief de la Tarea 8 sale del plan.
3. **Tarea 8.** Además de su brief, lleva lo que las rulings le asignaron:
   - **R4:** en `scripts/test-mongo.sh`, que el `docker stop` del trap no pise el código de salida
     de `check.sh` (`… || true`), y mongod con `--wiredTigerCacheSizeGB 0.25`.
   - **R7:** `tests/test_operator_typing.py` independiente de `FORCE_COLOR`. Una terminal con
     `FORCE_COLOR=3` lo rompe; mientras tanto, correr con `env -u FORCE_COLOR`.
   - **Referencias:** `docs/references.md` dice que los filtros de `GET /project/{id}/version`
     "no se releyeron". Quedaron verificados el 2026-10-05 en
     https://docs.modrinth.com/api/operations/getprojectversions/: son opcionales, y omitirlos
     significa sin filtro.
   - **Verificaciones del barrido en Chromium:**
     - el panel abre `:modal`; "← volver"; Escape con retorno del foco;
     - Ctrl/Cmd/clic central abren `/mods/{id}` y no el panel;
     - las pestañas cambian `#mods` y la URL (`?vista=`);
     - "Todos al servidor" no hace ninguna petición;
     - el diálogo de build abre `:modal`, se recalcula tras una acción y redibuja `#mods` detrás;
       Escape deja el foco en `#build-create`;
     - cero violaciones de CSP y cero errores de consola;
     - capturas a 1280 y 360 en claro, y a 1280 en oscuro.
   - **Documentos:**
     - en `docs/decisions.md`, las decisiones del dueño de la spec y de la revisión del plan, con
       sus tests;
     - en el roadmap, i-7985ee-593c92 con lo hecho, y la ronda 2 (i-7985ee-1bb2ea) apuntando a
       cada investigación nueva.
4. **Revisión final de todo el trabajo** (`8baecf7..` hasta la punta, ya en `main`) con el
   modelo más capaz y una sola tanda de arreglos. Se puede hacer antes de la Tarea 8. Hay
   que triar los menores diferidos (lista abajo). Uno pesa más: la búsqueda completa el catálogo
   en cada tecla (Tarea 5), y la investigación de caché recomienda usar los datos del propio
   resultado.
5. **Ronda 3 de pruebas reales** (decisiones del dueño), en la instancia descartable:
   - Modrinth real: un mod sin release pero con beta, y una dependencia sin versiones;
   - **los mods de 2 o 3 servidores reales del dueño**: hay que pedirle los nombres o slugs, y
     de su panel no se lee nada;
   - móvil a 360 px.

   Los hallazgos van a `docs/research/2026-10-04-usabilidad-panel.md` como ronda 3. El arnés de
   exploración (simulador de Pterodactyl y guiones de Playwright) no está en el repositorio: hay
   que rehacerlo con el método de la ronda 2 de ese documento, o decidir llevarlo al repo
   (pendiente del dueño).
6. Cerrar: changelog y roadmap; el merge a `main` y el push, solo cuando el dueño lo pida.

## Revisión final (2026-10-05, rama `release-1-prep`)

Hecha sobre `8baecf7..ec48e37`, de solo lectura, en un contexto limpio. No encontró nada crítico:
1 importante y 3 menores.

- **Autorización, validación, escape, CSP y swaps OOB:** sin hallazgos. Toda ruta que escribe
  llega a un método de editor con su fila, y las cadenas de Modrinth salen escapadas.
- **F1 (importante), arreglado en esta rama.** Una dependencia requerida sin proyecto ni versión
  daba un `Problem` sin `requested_by`: su tarjeta no ofrecía ninguna acción y el problema
  bloqueaba todo build. Ahora nombra a quien la pide y la tarjeta ofrece "Quitar A". Tests en
  `tests/test_resolution.py` y `tests/test_web_modpacks.py`.
- **F2 (menor), pendiente.** Si una dependencia pide una versión exacta de un mod fijado, la
  tarjeta la presenta como problema del pin ("La versión fijada ya no está…"). Además ofrece solo
  acciones que no lo resuelven y no ofrece "Quitar A". El arreglo es una marca `pinned` en
  `Problem`, que solo pone la rama del pin de `_choose`.
- **F3 (menor), pendiente.** "Fijar otra" no hace nada en el diálogo cuando la URL ya es
  `?vista=todos`: solo cambia el hash y `app.js` escucha solo `load`. El arreglo es escuchar
  también `hashchange`.
- **F4 (menor), pendiente.** Cada ficha hace un `scan_latest()` por servidor (N+1). El arreglo es
  una agregación o una proyección con `$elemMatch`.

Triage de los menores diferidos:
- **Antes del release:**
  - la búsqueda deja de completar el catálogo en cada tecla (`modpack_search`): el resultado ya
    trae título e ícono;
  - "Usar la beta" para una dependencia, si el dueño lo confirma;
  - la línea de `docs/references.md` (Tarea 8);
  - el barrido en Chromium (Tarea 8).
- **Al roadmap:**
  - el `#mods` que se redibuja en "Todos";
  - partir en lotes de 100 los ids que van a `project_get_many`;
  - el diseño `fetched_at` de la caché;
  - `projects_by_id` y `project_version_update_filter`, que quedaron sin uso;
  - el 404 del panel lateral, que va a `#notices`.
- **Se descartan** los demás, cada uno con su razón en el informe de la sesión.

## Preguntas abiertas para el dueño

- **Ronda 3:** qué servidores o qué mods usar.
- **Tablero "necesita atención"** (ronda 2): las 4 preguntas del final de
  `docs/research/2026-10-05-tablero-necesita-atencion.md`.
- **Arnés de exploración:** si va al repositorio como comando.

## Rulings del controlador (decisiones tomadas en nombre del dueño)

- **R1.** La Tarea 6 hace `contexto=problemas` y la 7 agrega `contexto=dialogo`. Costo si es
  errada: ninguno.
- **R2.** La ficha pide su proyecto directo (si Modrinth falla, "Reintentar"; si no lo conoce,
  404) y a sus relacionados con best effort. Costo: ninguno.
- **R3.** Marcar la versión de MC del pack en la ficha pasó a la Tarea 5 (`?pack=`), junto con
  dos arreglos de la revisión de la Tarea 3. Costo: un parámetro más.
- **R4.** El script del gate: código de salida y tope de caché de Mongo (Tarea 8). Costo:
  ninguno; un Mongo que muere sigue viéndose como fallos de test.
- **R5.** El texto del botón: "Usar la beta/alpha {n}", si no "Usar la versión {n} ({canal})";
  el panel de revisión conserva las sugerencias. Costo: redacción.
- **R6.** El diálogo de build se renderiza cerrado con `data-autoopen` y se abre con
  `showModal()` (según la investigación de diálogos). Costo: un cambio chico de `app.js`.
- **R7.** El test de tipado, independiente de `FORCE_COLOR` (Tarea 8). Costo: ninguno.
- **R8.** Las tarjetas de problema ofrecen exactamente las acciones de la tabla de la spec.
  Costo: menos botones.

## Ledger de la ejecución (copia al pausar)

### SDD ledger — plan: docs/superpowers/plans/2026-10-05-panel-mods-ficha-problemas.md

Spec: docs/superpowers/specs/2026-10-05-panel-mods-ficha-problemas-design.md (approved by owner 2026-10-05, reviewed by topics). Branch panel-mods-ficha, from main 8baecf7. Plan committed fff29fe.

#### Pre-flight scan

| Pair / task | Produces → consumes | Found |
|---|---|---|
| T1 → T6, T7 | Problem.requested_by/alternative/conflict_with | consistent |
| T2 → T3 | project_versions without filters → mod_versions_all | consistent |
| T3 → T4 | /mods/{id}, /panel, /versiones routes → ui.mod_link | consistent |
| T3 ↔ T5 | T3 creates mods/_card.html with plain names; T5 converts them to mod_link | consistent (order respected) |
| T4 → T5, T6 | ui.mod_link | consistent |
| T5 ↔ T6 ↔ T7 | all edit _mods.html/_resolution.html/_decide_dialog.html | sequential, fine |
| T6 → T7 | _problem.html + contexto | CONFLICT: T6 text says routes answer contexto=dialogo with "el diálogo de la Tarea 7" before T7 exists → ruling R1 |
| T1..T8 self | tests vs code | consistent |

Ruling: R1 — Task 6 implements `contexto=problemas` (and no contexto = today's behaviour); Task 7 adds `contexto=dialogo` — T6 cannot render a dialog T7 hasn't built — cost if wrong: none

#### Progress
Task 1: dispatched (base fff29fe, sonnet)
Task 1: minor (deferred): alternative.channel can be "release" when the pack excludes release (e.g. channels {beta}) — Task 6 text must say "la {canal}" generically (carried into T6 dispatch); no Lock-level test of an old stored Problem; inline fitting/best_fit could be a helper
Task 1: complete (commits fff29fe..aaf00a3, review clean)
Task 2: dispatched (base aaf00a3, sonnet)
Task 2: controller verified 2026-10-05 via docs.modrinth.com/api/operations/getprojectversions: loaders and game_versions optional, omitted = no filtering (no API version shown on the page)
Task 2: minor (deferred): docs/references.md entry says "per the published docs, not re-read" — controller verified on docs.modrinth.com 2026-10-05; Task 8 updates the wording; no test pins that filtered callers never pass empty filters; dead project_version_update_filter now fetches everything with defaults
Task 2: complete (commits aaf00a3..e982c87, review clean)
Task 3: dispatched (base e982c87, opus). Ruling: R2 — mod_page's own project fetched directly (errors propagate: UpstreamError/timeout → retry notice; empty → 404); fill_catalog stays best-effort for relatives — fill_catalog swallows errors so it can't tell 'unknown' from 'down' — cost if wrong: none
Task 3: implementer stalled (watchdog 600s) at roles table step; work staged; resumed same agent
Task 3: reviewer (opus) stalled twice; fresh sonnet reviewer dispatched, writing progressively to task-3-review.md
Ruling: R3 — the spec's "pack MC version marked when arriving from a pack" moves to Task 5 (links gain a validated `?pack={bundle_id}`; page/panel mark bundle.version); Task 5 also fixes T3 minor 1 (a project fetched by slug leaves its dependencies/requesters unfetched → ids as titles) and minor 2 (tautological status assertion in tests/test_web_mods.py) — the gap needs pack context that only Task 5 adds — cost if wrong: one more param on two routes
Task 3: minor (deferred): unbounded `missing` ids to project_get_many (check client batching); no test pins Mongo errors as 500; panel 404 goes to #notices
Task 3: complete (commits e982c87..5094b40, review clean; implementer and first reviewer stalled and were resumed/replaced)
Task 4: dispatched (base 5094b40, sonnet)
Task 4: implementer's Mongo gate flaky (ServerSelectionTimeout, VM); controller re-ran ./scripts/test-mongo.sh at c60ae7c: 858 passed
Task 4: minor (deferred → folded into Task 5): mods.py imports MODRINTH_REF via modpacks re-export and servers.py keeps a duplicate — import from src.web
Task 4: minor (deferred): app.js behaviour tested only by string markers (Task 8 Playwright must cover modifier/middle click, Escape, focus); a form inside #mod-drawer-body would be closed by the afterRequest handler (the drawer stays read-only, so not hit)
Task 4: complete (commits 5094b40..c60ae7c, review clean)
Task 5: dispatched (base c60ae7c, opus) with R3 extras (pack param, slug-fetch relatives, exact status asserts, MODRINTH_REF import)
Task 5: controller re-ran ./scripts/test-mongo.sh at c5b8bf8: 884 passed, exit 125
Ruling: R4 — Task 8 also fixes scripts/test-mongo.sh: the EXIT trap's docker stop must not override check.sh's exit code when the container is already gone (|| true), and mongod runs with --wiredTigerCacheSizeGB 0.25 to fit the 2 GiB VM next to the owner's deploy — seen 3 times (T4 timeouts, T5 exit 125 with 884 passed) — cost if wrong: a dead Mongo mid-run still shows as test failures, so nothing is hidden
Task 5: minor (deferred): update_page fill unguarded (Mongo-only risk); projects_by_id has no route caller (stale fake + its test remain); BUNDLE_ID/BUNDLE_REF regex duplicated across layers; brittle whitespace-dependent "Mods agregados" assertion; live search waits up to the 5 s fill (accepted by brief, user-visible)
Task 5: fix round 1 dispatched (FIX_BASE c5b8bf8): _import_done bare id → mod_link
Task 5: fix round 1/5 (2 addressed, 0 open — import confirmation link; duplicate fake; commits c5b8bf8..ec1d950)
Task 5: complete (commits c60ae7c..ec1d950, review clean)
Task 6: dispatched (base ec1d950, opus). Ruling: R5 — 'Usar la beta/alpha {n}', else 'Usar la versión {n} ({canal})' (alternative can be release when the pack excludes it); POST /replacements accepts optional bundle_id+contexto; review panel keeps suggestions, drops problems/bulk — cost if wrong: wording/layout
Task 6: implementer stalled (watchdog) at 'macro and templates'; work uncommitted; resumed same agent (told to stop modpack-t6-mongo before the gate)
Research (cache-catalogo-modrinth, 2026-10-05): fill_catalog re-fetches iconless/unknown ids every render and never refreshes iconed ones; search now fills per keystroke (Task 5) — recommendation: hits already carry icon_url, fill on selection or with a needs_fetch rule; fetched_at TTL design. Candidate for the final review / roadmap.
Ruling: R6 — Task 7 renders the build dialog closed with data-autoopen and app.js opens it with showModal() on htmx:afterSwap (one global handler; opener remembered; focus to a fixed tabindex=-1 target if the opener was swapped away; aria-labelledby) — per research dialogos-accesibles-htmx (static open = no inert/backdrop/Escape; showModal on an already-open dialog throws InvalidStateError); also fixes the round-1 deferred 'non-modal build dialog' — cost if wrong: small app.js change
Research: 5 reports done in /private/tmp/claude-501/modpack-research/ (versiones-mc-y-loaders, bajas-de-version, cache-catalogo-modrinth, dialogos-accesibles-htmx, tablero-necesita-atencion); to be copied into docs/research/2026-10-05-*.md and committed once Task 6 has committed (avoid touching the tree mid-task)
Task 6: controller re-ran the Mongo gate at 1efa7e0 (env -u FORCE_COLOR): 924 passed; script exit 125 from the cleanup (R4)
Ruling: R7 — Task 8 also makes tests/test_operator_typing.py independent of FORCE_COLOR (pass --no-color-output to mypy or unset it for the subprocess): the controller's shell has FORCE_COLOR=3 and the test then fails — cost if wrong: none
Task 6: complete (commits ec1d950..1efa7e0, review clean)
Ruling: R8 — Task 7 (which reuses _problem.html) also tightens it to the spec table: "Crear un reemplazo"/"Ignorar esta dependencia" only for an unresolvable dependency without alternative; no_file → only "Quitar del modpack"; "Fijar otra" only when the mod is in the pack — Task 6 review minors 1 and 3 — cost if wrong: fewer buttons
Task 6: minor (deferred): no "Usar la beta" for a dependency (pin_set rejects ids outside the pack; spec silent); ignore with a valid-format nonexistent bundle_id saves the global rule then 404s on resolve; no test of an old detail with a markup title; mods_view_link attrs={} default (harmless in Jinja)
Task 7: dispatched (base 19eab09 incl. research docs; task code base 1efa7e0; opus) with R1 dialogo, R6 showModal/data-autoopen, R8 problem-card tightening
Task 7: implementer reports the owner's modpack-mongodb health check flapped during gate runs (VM memory) — R4's wiredTiger cache cap matters for the owner's deploy too
Task 7: minor (deferred): _build_dialog repeats the _titles fallback; no test that pin_mismatch/pin_conflict with an alternative no longer offers "Usar la …"
Task 7: fix round 1 dispatched (FIX_BASE 8bab64d): #mods/#builds OOB after a dialog action; error closes a dialog only for its own write; drawer focus return via requestConfig.elt
Task 7: controller gate at a1d32ab: test-mongo.sh died at 6 min (28 connection failures); same gate against a mongod with --wiredTigerCacheSizeGB 0.25 on 27019: 942 passed — confirms R4
Task 7: fix round 1/5 (3 addressed, 0 open — #mods/#builds OOB; error-close scope; drawer focus; commits 8bab64d..a1d32ab)
Task 7: minor (deferred): the #mods OOB after a dialog action always shows tab "Todos" (open details/tab lost)
Task 7: complete (commits 19eab09..a1d32ab, review clean)
