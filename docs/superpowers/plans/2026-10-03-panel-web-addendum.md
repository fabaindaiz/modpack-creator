# Panel web, addendum: decisiones del dueño tras la revisión final

> **Para agentes:** SUB-SKILL REQUERIDA: usar superpowers:subagent-driven-development (recomendada)
> o superpowers:executing-plans para implementar este plan tarea por tarea. Los pasos usan casillas
> (`- [ ]`) para seguir el avance.

**Objetivo:** aplicar las decisiones que el dueño tomó el 2026-10-02 y el 2026-10-03, al repasar
una por una las decisiones de la ejecución de `docs/superpowers/plans/2026-10-02-panel-web.md`.

**Arquitectura:** sin cambios de capas. Los cambios tocan:
- las reglas de rollback (`UpdateService` y `JobDatabase`) y su reflejo en el panel;
- el flujo de actualización (vuelven dos botones);
- un barrido de rutas que lee una tabla de roles de `docs/decisions.md`;
- mejoras del panel: búsqueda, auditoría, decisiones en lote y una vista de trabajos con tabla,
  filtros y página propia;
- una herramienta de revisión visual en el repositorio;
- documentación, incluida la subida de MongoDB a 8.0.

**Tecnologías:** las mismas de la rama, más Playwright solo en un entorno de hatch aparte, para
la revisión visual.

**Spec:** `docs/superpowers/specs/2026-10-02-panel-web-design.md`, más las decisiones de esta
lista, que mandan sobre la spec donde la contradicen. Cada una se registra en `docs/decisions.md`.

## Decisiones del dueño que este plan implementa

| Ref | Decisión |
|---|---|
| D1 | Un rollback **fallido** de U no cierra U. Tras un **A exitoso** de U solo se ofrece **B**, con aviso de lo que se pierde y confirmación escribiendo el nombre del servidor. Tras un **B exitoso** de U, nada más. |
| R17 | **Cualquier** actualización real posterior (no simulación) supera a U, haya escrito o no. El dueño aceptó lo que implica: tras un intento rechazado en el preflight, el rollback de una U fallida ya no se ofrece y se restaura a mano en Pterodactyl. |
| R15 | Se mantiene: nunca rollback de una simulación ni de una actualización superada. |
| R14 | Vuelven dos botones independientes: "Simular actualización" y "Actualizar…". El segundo tiene un selector de build (por defecto el recomendado, si no el último), fija el número al confirmar, rotula cada opción con su número y exige escribir el nombre. "Ejecutar de verdad…" sigue también dentro de una simulación terminada, con el build simulado. Esto reemplaza el orden "simular → resultado → ejecutar" de la spec §3. |
| R1+R8 | La tabla de roles vive en `docs/decisions.md`: cada método de servicio que escribe, con su rol (operador o editor). El barrido lee esa tabla; toda ruta que escribe, de `/v1` o del panel, tiene que llegar a un método de la tabla. Las filas de operador = `OPERATOR_METHODS` (de `src.`). Si la tabla no se puede leer o está vacía, el test falla. |
| R2 | Los resultados de búsqueda muestran el autor, las descargas y el entorno (cliente/servidor), con los campos `author`, `downloads` y `environment` de `ProjectResult` (openapi de Modrinth v2.7.0). |
| R3 | `AGENTS.md`: un permiso denegado nunca se esquiva (ni redirección de shell, ni `sed`, ni otra herramienta); se avisa y se sigue con lo demás. |
| R4 | La entrada de changelog de la sesión enlaza la spec y los planes. |
| R5 | La auditoría suma un filtro de fechas desde/hasta, en UTC, sin cambiar el cursor por id. |
| R6 | Un test prueba que mypy rechaza una llamada a un método de operador sin `actor`. |
| R10 | `scripts/visual-sweep.sh`: la revisión visual como herramienta del repositorio. Se corre a mano y no va en CI. |
| R11 | El panel usa la misma regla `LOCAL_PATH` que `/v1` para el `redirect` del login. |
| R12 | Un formulario "Guardar decisiones" con todos los mods sin decidir, escritos en una sola actualización atómica. |
| R13/R16 | `/jobs` y el tablero pasan a tablas alineadas con enlace a una página nueva `/jobs/{id}`, que usa el fragmento compartido. `/jobs` suma filtros (servidor, tipo, estado) y paginación. La página del servidor sigue con el fragmento. |
| D3 | MongoDB 8.0 en todos lados. CI sigue en 8.0, y el runbook documenta cómo subir producción de una versión mayor a la siguiente. |

## Restricciones globales

Son las mismas del plan original (`2026-10-02-panel-web.md`, §Restricciones globales), y valen todas:
- Python 3.12; los routers solo inyectan servicios; nada de argumentos por defecto mutables; todo
  `Document` registrado.
- Tests primero, y vistos fallar.
- La CSP exacta; nada de `<script>` inline, `style=` ni `hx-on`.
- Commits con el dueño como **único autor**: sin `Co-Authored-By` ni ninguna otra atribución.
- **Nunca** leer ni editar `.env.yaml` ni `.env.default`; el dueño agrega
  `OIDC_OPERATOR_GROUPS: ""` a mano.
- **Gate:**
  - `./scripts/check.sh`;
  - con base: `./scripts/test-mongo.sh`. Ya funciona en este Mac: `docker` es un enlace a
    podman 6.1.3, con su VM iniciada;
  - mientras falte la línea de `.env.default`, el único fallo aceptado es `env_template`. Correr
    aparte `hatch run typecheck` y los tests.
- Lo visual se revisa con `scripts/visual-sweep.sh` (Tarea A10). Antes de que exista, con
  `scratchpad/shots/run.sh`.

## Foco de revisión

1. **Doble rollback:** tras un B exitoso, un segundo pedido de A o B por `/v1` tiene que dar 409
   y no dejar evento (Tarea A1).
2. **Botón directo con un build que ya no existe** (la página quedó abierta y alguien borró el
   bundle o cambió de servidor): 404 o 409 legible como aviso, nunca 500 (Tarea A2).
3. **Tabla de roles mal escrita** (una fila con un rol desconocido, o la tabla renombrada): el
   barrido falla nombrando la fila, y nunca pasa con la tabla vacía (Tarea A3).
4. **Fechas del filtro de auditoría:** si "hasta" es anterior a "desde", o una fecha es
   inválida, se muestra el aviso 422 y nunca un 500. "Hasta" incluye ese día completo (Tarea A7).
5. **Decisiones en lote con un id inválido mezclado:** se rechaza todo el lote con 422 y no se
   escribe nada (Tarea A8).

---

### Tarea A1: Reglas de rollback (D1 + R17)

**Archivos:**
- Modificar: `src/database/jobs.py`, `src/services/update.py`, `src/services/jobs.py`,
  `src/routers/web/servers.py`, `src/web/templates/servers/_update.html` (o donde viva el bloque
  de rollback), `docs/decisions.md` (enmendar d-7985ee-5445a0)
- Tests: `tests/test_operator_actions.py`, `tests/test_web_servers.py`

**Interfaces:**
- `JobDatabase.last_real_update(key) -> Optional[Job]`: la actualización real más nueva
  (`kind == "server_update"`, `payload.dry_run` distinto de `True`) **o** el rollback más nuevo de
  esa clave. Se quita la condición de checkpoint `backup`/`swap` que agregó la ronda final (R17).
- Nuevo `JobDatabase.rollbacks_of(update_id: str) -> list[Job]`: los `server_rollback` con
  `payload.update_id == update_id`, del más nuevo al más viejo.
- `UpdateService.enqueue_rollback(server_id, update_id, mode, actor)`. El orden de los rechazos
  es este, todos con 409 y antes de auditar:
  1. U no es una actualización fallida de ese servidor;
  2. U es una simulación;
  3. una actualización real posterior a U existe (R17);
  4. un rollback **B** de U terminó con éxito ("La actualización ya se revirtió con B");
  5. un rollback **A** de U terminó con éxito y `mode == "A"` ("Ya se revirtió con A: solo queda B");
  6. falta el checkpoint que el modo necesita;
  7. el servidor está ocupado.

  Un rollback **fallido** no cuenta para 4 ni 5.
- `JobService.rollback_state(server_id) -> Optional[RollbackState]`, para el panel. Es un modelo
  con `update: Job`, `allowed: list[Literal["A","B"]]` y `reverted_with_a: Optional[Job]`, el A
  exitoso si lo hubo. Devuelve `None` cuando no hay nada que ofrecer. Reemplaza a
  `_rollback_candidate`.
- Panel:
  - con `reverted_with_a`, el bloque dice "Revertido con A el <fecha>. Restaurar el backup previo
    pierde lo jugado desde entonces" y ofrece solo B, con el diálogo de confirmación que exige el
    nombre (el mismo macro que la actualización real);
  - en el caso normal se ofrecen A y B como hoy;
  - `POST /servers/{id}/rollback` exige el campo `confirm` (nombre exacto, `.strip()`) **solo
    cuando** el estado es `reverted_with_a`; en otro caso no cambia.

- [ ] **Paso 1: tests que fallan** (Mongo):
  - `test_b_after_a_done_is_accepted_and_a_is_refused`;
  - `test_nothing_after_b_done` (A y B → 409, 0 eventos);
  - `test_failed_rollback_does_not_close_the_update`;
  - `test_any_later_real_update_supersedes`, también una rechazada en preflight, sin checkpoint;
  - `test_a_later_dry_run_does_not_supersede`.

  En el panel:
  - `test_after_done_a_only_b_with_name_confirmation`;
  - `test_after_done_b_no_rollback_offered`;
  - `test_b_after_a_requires_exact_name` (422 sin llamar al servicio).

  Se reemplazan los tests de la ronda final que afirmaban "una actualización rechazada en
  preflight no supera" (ahora es lo contrario).
- [ ] **Paso 2:** verlos fallar.
- [ ] **Paso 3:** implementar. Enmendar d-7985ee-5445a0 con D1 y R17, y la docstring de
  `enqueue_rollback`.
- [ ] **Paso 4:** gate.
- [ ] **Paso 5: commit** `fix: close an update after a successful rollback, and let B follow a done A`.

### Tarea A2: Dos botones de actualización (R14)

**Archivos:**
- Modificar: `src/routers/web/servers.py`, `src/web/templates/servers/_update.html`,
  `src/web/templates/servers/_dry_run.html`, `docs/decisions.md` (decisión nueva con
  `bundle.py id d`)
- Tests: `tests/test_web_servers.py`

**Interfaces:**
- La página del servidor ofrece a un operador "Simular actualización" y "Actualizar…". Para un
  editor, los dos aparecen deshabilitados con "requiere operador".
- El diálogo de "Actualizar…" tiene:
  - `<select name="build">` con los builds del bundle asociado (`ModpackService.build_find`),
    del más nuevo al más viejo, y seleccionado por defecto el recomendado, si no el último;
  - el título "Actualizar <servidor>". Cada opción del selector dice su número y su papel, por
    ejemplo `Build 5 (último)` o `Build 4 (recomendado)`, así que el build que se instala se lee
    en el selector, sin JS. El botón dice "Actualizar al build elegido";
  - el campo `confirm`.
- Sin bundle o sin builds: el botón deshabilitado con el motivo.
- `POST /servers/{id}/update` no cambia: `build` y `confirm` obligatorios, el chequeo de rol y
  luego el de nombre, y `enqueue_update(..., build=build, dry_run=False)`. Lo usan los dos
  caminos.
- La decisión nueva dice: actualizar no exige una simulación previa; hay dos caminos (directo
  con build elegido, o desde una simulación con su build); los dos fijan el número y piden el
  nombre. Enmienda la spec §3.

- [ ] **Paso 1: tests que fallan:**
  - `test_direct_update_dialog_lists_builds_with_recommended_selected`;
  - `test_direct_update_posts_the_selected_build`;
  - `test_preview_result_still_offers_real_run_with_simulated_build`;
  - `test_direct_update_disabled_without_builds`;
  - `test_stale_build_is_a_notice` (el servicio da 404 o 409 → aviso con su status, foco de
    revisión 2).
- [ ] **Paso 2:** verlos fallar.
- [ ] **Paso 3:** implementar. `npm run build`.
- [ ] **Paso 4:** gate.
- [ ] **Paso 5: commit** `feat: update a server directly to a chosen build, or from its preview`.

### Tarea A3: Tabla de roles en `docs/decisions.md` y barrido de toda ruta que escribe (R1 + R8)

**Archivos:**
- Modificar: `docs/decisions.md` (nueva sección `## Acciones y roles`), `tests/test_routes_roles.py`

**Interfaces:**
- La tabla tiene este formato exacto:
  `| Método | Rol | Qué hace |`, con filas como `` | `UpdateService.enqueue_update` | operador | … | ``.
  - Rol ∈ {`operador`, `editor`}.
  - Las 6 filas de operador son las de `EXPECTED_OPERATOR_METHODS`.
  - Las de editor son todos los métodos de servicio que alguna ruta de escritura llama (los 13
    auditados de la tarea 6, `decision_set`, los de `/v1/modrinth`, `resolve`, etc.). La lista
    final la descubre el test.
- `tests/test_routes_roles.py`:
  - `load_role_table() -> dict[str, Literal["operador","editor"]]` lee la sección por su
    encabezado. Falla con un mensaje claro si falta la sección, si una fila está mal formada o
    tiene un rol desconocido, o si la tabla queda vacía.
  - El conjunto de operadores de la tabla tiene que ser igual a `OPERATOR_METHODS` filtrado a
    `src.`. Reemplaza al `EXPECTED_OPERATOR_METHODS` escrito en el test.
  - **Clasificación (R1 c):** toda ruta de `app.routes` (de `/v1` y del panel) con método
    POST/PUT/PATCH/DELETE tiene que alcanzar, por AST y siguiendo las funciones auxiliares del
    mismo módulo, **al menos un** método de la tabla.
    - Las únicas excepciones van en una lista corta y comentada en el test: las rutas de
      autenticación, si alguna es POST.
    - Si una ruta no alcanza ningún método de la tabla, el test falla nombrando la ruta y el
      módulo.
  - El barrido de 403 (con sesión de editor) no cambia y sigue descubriendo las rutas desde las
    filas de operador.

- [ ] **Paso 1:** escribir los tests nuevos y verlos fallar: no hay sección, y todavía hay rutas
  sin clasificar.
- [ ] **Paso 2:** escribir la tabla en `docs/decisions.md`, con todas las filas que el test pida.
  Ver fallar el chequeo borrando una fila de editor y cambiando un rol a `operadorr`; restaurar.
- [ ] **Paso 3:** gate.
- [ ] **Paso 4: commit** `test: classify every write route against the role table in decisions.md`.

Las tareas siguientes que agreguen métodos de servicio que escriben (A8, A9) agregan su fila en
el mismo commit.

### Tarea A4: Una sola regla de `redirect` (R11)

**Archivos:** modificar `src/web/__init__.py` (`login_url` usa `LOCAL_PATH` de
`src/routers/v1/auth.py`, o una constante compartida en `src/library/auth/`, y se borra
`LOGIN_REDIRECT`); `tests/test_web_base.py`, `tests/test_routes_auth.py`.

- [ ] **Paso 1: tests que fallan:**
  - `test_login_url_and_v1_share_one_rule`: una tabla de casos (`/`, `/modpacks?x=1`, `//h`,
    `/\h`, `/%2F/h`, `/x\n`) da el mismo veredicto en las dos;
  - los valores buenos se aceptan también en `/v1/login` (sin seguir al IdP: basta con que no dé
    422).
- [ ] **Paso 2:** implementar. Gate.
- [ ] **Paso 3: commit** `refactor: one redirect rule for the panel and /v1`.

### Tarea A5: mypy rechaza un método de operador sin `actor` (R6)

**Archivos:** crear `tests/test_operator_typing.py`.

**Interfaces:** el test escribe un snippet en `tmp_path` que importa `UpdateService` y llama
`await s.enqueue_update("x", build=1)` sin `actor`. Corre `mypy.api.run([str(snippet),
"--config-file", ".mypy.ini"])` y exige un error que mencione `actor`. Un segundo snippet correcto,
con `actor=...`, no da errores en esa línea.

- [ ] **Paso 1:** escribir el test. Verlo fallar cambiando temporalmente la firma de
  `operator_only` a `Callable[..., Any]`; restaurar.
- [ ] **Paso 2:** gate (medir cuánto agrega al tiempo de los tests e informarlo).
- [ ] **Paso 3: commit** `test: prove mypy rejects an operator call without its actor`.

### Tarea A6: Más datos en la búsqueda de Modrinth (R2)

**Archivos:** `src/routers/web/modpacks.py` (`_hit`), `src/web/templates/modpacks/_search.html`,
`tests/test_web_modpacks.py`.

**Interfaces:** cada resultado muestra:
- `author`;
- `downloads`, abreviado: `1,2 M`, `34 k`, o el número tal cual si es menor que 1000;
- el entorno a partir de `environment` (lista), con las etiquetas "cliente", "servidor" o
  "cliente y servidor". Si la lista viene vacía o con un valor desconocido, no se muestra
  etiqueta: nunca "ambos" por defecto (`research/…` §5).

Los campos que falten o no tengan el tipo esperado se toleran, como hoy.

- [ ] **Paso 1: tests que fallan:** `test_hit_shows_author_downloads_and_environment`,
  `test_hit_without_environment_shows_no_side`, `test_downloads_are_abbreviated`.
- [ ] **Paso 2:** implementar. `npm run build`. Gate.
- [ ] **Paso 3: commit** `feat: show author, downloads and side in the Modrinth search`.

### Tarea A7: Filtro de fechas en la auditoría (R5)

**Archivos:** `src/database/audit.py`, `src/services/audit.py`, `src/routers/web/activity.py`,
`src/web/templates/activity/audit.html`, `tests/test_audit_log.py`, `tests/test_web_activity.py`.

**Interfaces:**
- `AuditService.find(..., since: Optional[datetime] = None, until: Optional[datetime] = None)`,
  combinado con un AND con los filtros exactos y el cursor. `since` es inclusivo y `until`
  exclusivo, los dos aware en UTC.
- La página tiene `<input type="date" name="desde">` y `name="hasta"`, rotulados "(UTC)".
  - "hasta" se convierte al día siguiente a las 00:00 UTC, así el día completo queda incluido.
  - Si "hasta" es anterior a "desde", o una fecha es inválida, se responde 422 con un aviso.
  - Los enlaces "Ver anteriores" conservan desde y hasta.

- [ ] **Paso 1: tests que fallan** (Mongo y panel): rango inclusivo del día, rango invertido →
  422, y la paginación con rango.
- [ ] **Paso 2:** implementar. Gate.
- [ ] **Paso 3: commit** `feat: filter the audit log by date range`.

### Tarea A8: Decisiones de servidor en lote (R12)

**Archivos:** `src/database/modpack.py`, `src/services/modpack.py`, `src/routers/web/modpacks.py`,
`src/web/templates/modpacks/_lock.html`, `docs/decisions.md` (fila de la tabla de roles),
`tests/test_web_modpacks.py`, `tests/test_builds.py` o el archivo de servicio que corresponda.

**Interfaces:**
- `ModpackDatabase.bundle_decisions_set(bundle_id, decisions: dict[str, bool]) -> ModpackBundle`:
  un solo `update_one` con `$set` de cada clave `server_decisions.<id>`. 404 como `bundle_get`.
- `ModpackService.decisions_set(bundle_id, decisions, actor) -> ModpackBundle`:
  - valida **todos** los ids (`^[A-Za-z0-9_-]+$`) antes de escribir; uno inválido → 422 y no se
    escribe nada (foco de revisión 5);
  - un diccionario vacío → 422;
  - después del éxito, un evento `bundle.decision` con `{"decisions": decisions}`.
- `decision_set` (de a una) se mantiene para el selector individual.
- El formulario en `_lock.html` tiene un selector por mod sin decidir y un botón "Guardar
  decisiones". Hace POST a `/modpacks/{id}/decisions/bulk` con campos `decision.<id>` = `si` o
  `no`; los mods que se dejen "sin decidir" no se envían.

- [ ] **Paso 1: tests que fallan:**
  - (Mongo) `test_bulk_decisions_write_every_key_atomically` (con decisiones previas que no se
    pierden);
  - `test_bulk_with_one_bad_id_writes_nothing`;
  - el panel: el formulario lista los mods sin decidir, y el POST llama al servicio con el
    diccionario correcto.
- [ ] **Paso 2:** implementar (agregar la fila a la tabla de roles). `npm run build`. Gate.
- [ ] **Paso 3: commit** `feat: decide many mods at once, atomically`.

### Tarea A9: Trabajos en tabla, con filtros y página propia (R13/R16)

**Archivos:** `src/database/jobs.py`, `src/services/jobs.py`, `src/model/jobs.py` (índice si hace
falta), `src/routers/web/activity.py`, `src/routers/web/home.py`, crear
`src/web/templates/activity/job.html` y `activity/_job_tr.html`; modificar `activity/jobs.html` y
`home.html`; `tests/test_web_activity.py`, `tests/test_web_base.py`, `tests/test_jobs.py`.

**Interfaces:**
- `JobDatabase.find(*, server_id: Optional[str], kind: Optional[str], status: Optional[str],
  before_id: Optional[str], limit: int) -> list[Job]`:
  - igualdad exacta (`$eq`): `key == f"server:{server_id}"`, `kind` y `status`;
  - orden `created_at` y `_id` descendentes, y cursor por id como en la auditoría;
  - `limit` acotado a 1..200.
- `JobService.find(...)`, que lo envuelve.
- Índice: `(kind, created_at desc)` solo si la consulta lo necesita; el de `(key, created_at)` ya
  existe. Anotarlo en el runbook si se agrega.
- `GET /jobs?servidor=&tipo=&estado=&before=`:
  - una tabla alineada con las columnas Tipo, Servidor (nombre, enlazado a su página), Estado
    (badge), Paso, Pedido por ("programado" si es `None`) y Creado;
  - cada fila enlaza a `/jobs/{id}`;
  - las filas activas se refrescan con `hx-get="/jobs/{id}/tr" hx-trigger="every 2s"
    hx-swap="outerHTML"` sobre el `<tr>` (fragmento `_job_tr.html`), y las terminadas sin
    trigger;
  - 50 por página con "Ver anteriores";
  - los filtros son selects para tipo y estado, con los valores conocidos, y un select de
    servidores.
- `GET /jobs/{id}`: una página con el detalle, que usa el fragmento compartido
  `_job_row.html`, con `actor` y `server_name`. Incluye el plan, la ejecución real para un
  operador y el refresco.
- Tablero: "Trabajos recientes" pasa a la misma tabla (10 filas, sin filtros), con enlace a
  `/jobs`.
- La página del servidor no cambia: sigue con el fragmento.

- [ ] **Paso 1: tests que fallan:**
  - (Mongo) el filtro por servidor, tipo y estado, la paginación y que los valores se comparen
    como literales;
  - el panel:
    - la tabla con su enlace;
    - una fila activa refresca el `<tr>` y una terminada no;
    - `/jobs/{id}` muestra la ejecución real para un operador y el botón deshabilitado para un
      editor;
    - el tablero muestra la tabla;
    - un id inválido da 404 como aviso.
- [ ] **Paso 2:** implementar. `npm run build`. Gate.
- [ ] **Paso 3: commit** `feat: list jobs in a filterable table with a page per job`.

### Tarea A10: `scripts/visual-sweep.sh` (R10)

**Archivos:** crear `scripts/visual-sweep.sh`, `scripts/visual_sweep.py` (siembra, cookies y
capturas en un solo módulo) y la sección "Revisión visual" del README; modificar
`pyproject.toml` (entorno de hatch `visual` con `playwright==<versión exacta vigente>`, fuera de
`requirements.txt`) y `AGENTS.md` (§Verificación: cómo correrla).

**Interfaces:**
- `./scripts/visual-sweep.sh [salida]`:
  1. compila el CSS (`npm run build`);
  2. levanta `mongo:8.0` con `docker`, igual que `scripts/test-mongo.sh`, en un puerto libre;
  3. siembra los datos de demo (3 servidores, uno con nombre de 120 caracteres; un modpack de 300
     mods; trabajos en todos los estados, incluida una simulación con plan y una actualización
     fallida; 60 eventos de auditoría);
  4. corre la app con uvicorn **desde un directorio temporal fuera del repositorio**, con
     configuración falsa (`PYTHONPATH` al repositorio; `SESSION_HTTPS_ONLY=false`;
     Pterodactyl y OIDC en un puerto muerto; `JOBS_WORKER=off`), así `.env.yaml` nunca se carga;
  5. captura las páginas: tablero, modpacks, un modpack, reemplazos, servidores, un servidor,
     trabajos, un trabajo y auditoría;
  6. lo hace a 360, 768 y 1280 px, en claro y oscuro con operador, y en claro a 1280 px con
     editor;
  7. imprime las páginas con status distinto de 200, con desborde horizontal mayor que 0 o con
     errores de consola, y sale con un código distinto de 0 si hay alguna;
  8. deja las capturas en `salida` (por defecto un directorio temporal que imprime) y apaga todo
     al salir.
- Chromium: `playwright install chromium` la primera vez (lo dice el README), o usar el que
  indique `PLAYWRIGHT_CHROMIUM` si ya hay uno.

- [ ] **Paso 1:** escribirlo a partir de `scratchpad/shots/` (run.sh, seed.py, cookie.py,
  shoot.py) y correrlo: tiene que dar 0 problemas.
- [ ] **Paso 2:** ver fallar el chequeo, con una clase que desborde a propósito en una plantilla;
  restaurar.
- [ ] **Paso 3:** gate. No va en `hatch run gate`.
- [ ] **Paso 4: commit** `build: a visual sweep of the panel anyone can run`.

### Tarea A11: Documentación (D3, R3, R4) y cierre

**Archivos:** `docs/runbooks/migrar-mongo-a-volumen.md`, `docs/runbooks/desplegar-panel.md`,
`AGENTS.md`, `docs/decisions.md`, `docs/roadmap.md`, `.claude/logs/agent-changelog.md`, `README.md`.

**Contenido:**
- **D3, en `migrar-mongo-a-volumen.md`, paso 1:** si `db.version()` es menor que 8.0, hay que
  subir antes, de una versión mayor a la siguiente, según la documentación oficial (consultada el
  2026-10-03, *Upgrade a Standalone to 8.0*):
  - "To upgrade an existing MongoDB deployment to 8.0, you must be running a 7.0-series release";
    desde antes de 7.0 se sube de a una mayor (p. ej. 6.0 → 7.0 → 8.0);
  - en cada salto:
    1. respaldar;
    2. comprobar `featureCompatibilityVersion` con
       `db.adminCommand({ getParameter: 1, featureCompatibilityVersion: 1 })`;
    3. cambiar `MONGO_VERSION` a la mayor siguiente y recrear el contenedor sobre el mismo
       volumen;
    4. fijar la FCV de esa versión. Desde 7.0, el comando exige `confirm: true`:
       `db.adminCommand({ setFeatureCompatibilityVersion: "8.0", confirm: true })`.
  - Antes de escribir el paso de cada versión anterior a 7.0, consultar su página oficial;
    citarla.
  - Al final, `MONGO_VERSION=8.0`. CI ya usa 8.0.
  - Anotar la decisión en `docs/decisions.md`.
- **R3:** una invariante en `AGENTS.md`: "Un permiso denegado no se esquiva: ni redirección de
  shell, ni `sed`, ni otra herramienta. Se avisa y se sigue con lo demás." Lo hace cumplir
  `.claude/settings.json`.
- **R4:** la entrada de changelog de la sesión, ya existente, enlaza
  `docs/superpowers/specs/2026-10-02-panel-web-design.md` y los dos planes. Agrega lo hecho en este
  addendum, el repaso de decisiones con el dueño, y que podman local ya permite construir la
  imagen (verificado el 2026-10-03: la imagen arm64 construye, `main` importa, los assets están,
  y el compose valida y rechaza sin `MONGO_VERSION` o `FORWARDED_ALLOW_IPS`).
- `AGENTS.md:18` ("Capas" de "Qué es"): agregar `src/routers/web/`.
- `README.md`: cómo correr la imagen y los tests de base con podman (`docker` como enlace a
  podman).
- Roadmap: actualizar i-7985ee-50d411 con lo nuevo.

- [ ] **Paso 1:** consultar las páginas oficiales de upgrade de MongoDB que hagan falta. Escribir.
- [ ] **Paso 2:** `hatch run bundle` (ids) y el gate.
- [ ] **Paso 3: commit** `docs: upgrade MongoDB to 8.0 step by step, and record the owner's decisions`.

## Fuera de alcance

Son los menores que la revisión final dejó para después y el dueño no eligió en este repaso:
- el enlace de la auditoría por actor;
- archivos locales, canales y pins en el detalle del modpack (anotado en el roadmap);
- `login_callback` con test de grupos;
- el `dry_run` ausente tratado de forma distinta por el worker y el servicio;
- el índice exacto para el desempate por `_id`;
- la readiness tras perder el lease.

Si el dueño los quiere, van a otro plan.

## Orden

A1 → A2 → A3 → A4 → A5 → A6 → A7 → A8 → A9 → A10 → A11. A3 va temprano, para que A8 y A9 tengan
que declarar sus métodos en la tabla.
