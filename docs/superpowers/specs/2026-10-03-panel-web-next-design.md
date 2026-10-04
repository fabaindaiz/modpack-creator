# Panel web, siguiente lote: detalle del modpack, pendientes, vista previa y revisión de actualizaciones

El dueño aprobó este diseño el 2026-10-03 en el chat. Se apoya en
`2026-10-02-panel-web-design.md` y en las decisiones del addendum (`docs/decisions.md`). Donde
contradice a la spec anterior, manda esta.

## Qué se quiere

- **(a)** El detalle de un modpack muestra los archivos locales, los canales y los pins (la spec
  anterior lo pedía en §3.2 y quedó fuera). Los canales y los pins se editan desde el panel.
- **(b)** Cerrar los pendientes menores que dejaron las revisiones.
- **(c)** Antes de actualizar, ver qué cambia de cada mod y el changelog de su versión nueva, con
  un enlace al historial completo en Modrinth.
- **(d)** Cada día, y cuando se pida con un botón, revisar si los mods del build recomendado de
  cada modpack tienen versiones más nuevas. Se avisa por Discord, en una página del panel y con
  una insignia en el tablero, y se ofrece crear un build nuevo. Nada se aplica solo.

**Éxito:**
- un editor ve y edita los canales y los pins, y ve los archivos locales que dejó una
  importación;
- la simulación, el diálogo "Actualizar…" y la comparación de dos builds muestran el mismo bloque
  de cambios con changelogs;
- con `UPDATE_CHECK_AT` puesto, la revisión corre una vez al día y avisa solo si los cambios son
  distintos del último aviso;
- el gate pasa.

## Fuentes consultadas (2026-10-03)

- **openapi de Modrinth v2.7.0:**
  - `POST /version_files/update` recibe `hashes`, `algorithm`, `loaders` y `game_versions`
    (obligatorios) y `version_types` (opcional: release, beta, alpha). Devuelve un mapa hash →
    la versión más nueva que cumple esos filtros. **Ignora pins, reemplazos y decisiones de un
    modpack.**
  - `GET /version/{id}` incluye `changelog` (Markdown). `GET /project/{id}/version` lo omite con
    `include_changelog=false`, como ya hace el cliente.

## (a) Detalle del modpack

- **Archivos locales** (`ModpackBundle.local_files`): solo lectura. Se muestran el nombre, el
  tamaño y un hash corto (sha512, 12 caracteres). Si no hay ninguno, se dice.
- **Canales** (`ModpackBundle.channels`, valores release/beta/alpha): se editan con casillas y se
  guardan con `bundle_update`, que audita `bundle.update`. Como mínimo debe quedar uno marcado
  (422 si no).
- **Pins** (`ModpackBundle.pins`, proyecto → versión de Modrinth): una tabla con el proyecto (su
  título del catálogo, o el id) y la versión fijada, con botón para quitar, y un formulario para
  agregar (proyecto del modpack + id de versión).
  - Se escriben de forma atómica por clave, como las decisiones: `$set` o `$unset` de
    `pins.<project_id>`.
  - Los ids se validan con `^[A-Za-z0-9_-]+$` antes de escribir.
  - Cada cambio deja un evento `bundle.pin`.
  - Los métodos nuevos que escriben van a la tabla de roles de `docs/decisions.md`.

## (b) Pendientes menores

1. **Runbook de Mongo, bloque 4.4:** los pasos internos van con letras, y las referencias a los
   pasos principales los nombran ("antes del `git pull` de 5. Desplegar"). El paso de la FCV
   muestra la línea `docker exec modpack-mongodb mongo …` completa.
2. **Test faltante:** un editor que manda un `bundle_id` distinto o vacío recibe 403 antes de
   cualquier comparación.
3. **"Ver anteriores"** en trabajos y en auditoría se pide con `limit + 1`, y el enlace sale solo
   si existe un elemento más.
4. **Script visual:**
   - `MODRINTH_API_URL`, `FABRIC_META_URL` y `QUILT_META_URL` apuntan a un puerto muerto;
   - el loop de espera de Mongo tiene tope y detecta un contenedor muerto;
   - `main()` se niega a correr si el cwd es el repositorio o si existe `.env.yaml` en el cwd;
   - se verifica que la URL final sea la pedida.
5. **`dry_run` ausente:** un trabajo `server_update` sin `payload.dry_run` cuenta como
   **simulación** en todos lados (falla cerrada): en el worker, en `enqueue_rollback` y en
   `last_real_update`. Usa un solo predicado compartido, real solo si `payload.get("dry_run") is
   False`.
6. **Tope de lote:** `decisions_set` rechaza más de 500 decisiones (422), y el mensaje del
   servicio pasa al español.
7. **`AGENTS.md`:** la cláusula de la tabla de roles dice "(con los límites de
   `tests/test_routes_roles.py`)".

## (c) Vista previa con changelogs

**Unidad compartida.** `ModpackService.change_preview(from_lock, to_lock) -> ChangePreview`
compara dos conjuntos de entradas (proyecto → versión) y devuelve cuatro listas: `added`,
`removed`, `changed` (versión de y a) y `unchanged` (solo cuántas).
- De cada versión **nueva** (agregada o cambiada) trae su changelog con un método privado de
  `ModpackService`, que ya recibe `modrinthDB` y `modrinthAPI`.
  - Ese método usa el catálogo como caché: `ModrinthVersion.changelog`, un campo opcional nuevo,
    para lo guardado antes.
  - Si no lo tiene, lo pide con `GET /version/{id}` (el cliente ya tiene `version_get`) y lo
    guarda.
- La comparación misma (sin changelogs) es una función pura en `src/library/`, testeable sola.
  - Si Modrinth falla, el bloque dice "changelog no disponible" y nunca da 500.
- El enlace al historial es `https://modrinth.com/project/<project_id>/changelog`.
- **El changelog se muestra como texto plano** (`white-space: pre-wrap` vía clase de Tailwind,
  autoescape), no como Markdown renderizado. Va recortado a 4000 caracteres, con "… ver en
  Modrinth".

**Dónde aparece:**
1. **Comparar dos builds:** `GET /modpacks/{id}/builds/compare?de=N&a=M` (editor, lectura). Desde
   la lista de builds, un formulario con dos selectores.
2. **Resultado de una simulación** terminada: compara lo instalado según el último escaneo
   (entradas identificadas: proyecto → versión) con el lock del build simulado. Va debajo del plan.
3. **Diálogo "Actualizar…"**: al cambiar el selector de build, htmx pide
   `GET /servers/{id}/update/preview?build=N` y pone el bloque dentro del diálogo, con el
   recomendado precargado. Usa `hx-trigger="change"` en el `<select>`, sin JS propio.

Los changelogs se piden en paralelo con un tope de 8 a la vez. Una versión sin changelog dice "sin
changelog".

## (d) Revisión de actualizaciones

**Cálculo** (decisión del dueño: `version_files/update`, más rápido aunque ignore pins):
1. Para el build recomendado del modpack, o el último si no hay recomendado, se toman los sha512
   de sus entradas.
2. Se agrupan por loaders del tipo, como la resolución: mods con el loader del modpack; shaders
   con iris/optifine/canvas/vanilla; resourcepacks con minecraft; datapacks con datapack.
3. Por grupo se llama `files_update_hash_post_many(hashes, loaders=…, game_versions=[bundle.version],
   version_types=bundle.channels)`. El método del cliente suma el parámetro `version_types`;
   sigue en `IDEMPOTENT_POSTS`.
4. Una entrada tiene actualización si la versión devuelta es distinta de la del build.
5. Las entradas **fijadas** (`bundle.pins`) se marcan "fijado: no entra en un build nuevo" y no
   cuentan en la insignia ni en el aviso.

**Datos.** Un `Document` nuevo, `UpdateCheck`, en la base `{prefix}modpack` y registrado en
`init_beanie`. Tiene:
- `bundle_id`, `build_number` y `checked_at`;
- `updates`: lista de `{project_id, from_version, to_version, pinned}`;
- `error`: opcional;
- `notified_digest`: el hash de los cambios avisados.

Se guarda un documento por revisión. La página y la insignia leen el más nuevo de cada modpack,
con un índice `(bundle_id, checked_at desc)`.

**Trabajo.**
- `update_check` corre con el worker durable, con clave `bundle:{id}` (uno activo por modpack) y
  `requested_by` cuando lo pide una persona.
- El botón "Buscar actualizaciones" en el modpack (editor) lo encola con
  `ModpackService.enqueue_update_check(bundle_id, actor)`, auditado como `bundle.update_check` y
  con su fila en la tabla de roles.
- Si falla Modrinth, el trabajo termina fallido con el error guardado. No avisa por Discord.

**Programación.**
- `UPDATE_CHECK_AT` (`"HH:MM"`) y `UPDATE_CHECK_TZ` (por defecto `America/Santiago`, con
  `zoneinfo`). Sin `UPDATE_CHECK_AT` no hay programación.
- El programador del lifespan encola un `update_check` por modpack una vez por día local, cuando
  pasa esa hora.
- "Ya corrió hoy" se decide por el `UpdateCheck` más nuevo: se compara con la fecha local y nada
  se guarda en memoria, así que funciona con varios procesos y tras reinicios.
- Un `UPDATE_CHECK_AT` mal formado hace fallar el arranque con un mensaje claro (falla cerrada).

**Aviso por Discord.** Solo si hay actualizaciones no fijadas y su digest es distinto del
`notified_digest` del último aviso de ese modpack. El texto es:
"🔎 <modpack> (build N): K mods con actualización — A x→y, B x→y, …", recortado a 2000 caracteres.
Sin `DISCORD_WEBHOOK_URL` no hace nada, igual que hoy.

**Página.** `GET /modpacks/{id}/actualizaciones` (editor, lectura): la última revisión (cuándo,
qué build, error si lo hubo) y la lista con el bloque de changelogs de (c).
- Tiene el botón "Buscar actualizaciones" y el botón **"Crear build"**, que llama al
  `build_create` existente: resuelve con pins y canales, se audita y **no** recomienda ni
  despliega.
- La página advierte que el build nuevo sale de la resolución, así que puede diferir de la lista
  (pins, reemplazos).

**Insignia.** En el tablero, junto a cada modpack de "Últimos builds", "N actualizaciones" si la
última revisión las tiene (N sin contar las fijadas). Enlaza a la página.

## Seguridad y errores

- Toda ruta nueva que escribe pasa la tabla de roles. Las de lectura son de editor.
- El texto que viene de Modrinth (changelogs, títulos) se escapa siempre. Los enlaces a Modrinth
  se arman con ids validados.
- Los errores de Modrinth en la vista previa o en la página se muestran como aviso, nunca como
  500.

## Tests

- Unidades puras:
  - `change_preview` con agregados, quitados, cambiados e iguales;
  - la agrupación por loaders de (d);
  - el digest;
  - el cálculo de "ya corrió hoy" con zona horaria y cambio de día.
- Mongo: `UpdateCheck` (guardar y leer el más nuevo); pins atómicos con escritura concurrente.
- Contrato: `files_update_hash_post_many` manda `version_types`, y `version_get` lee `changelog`.
  Se prueba contra el servidor local de `tests/test_api_contracts.py`.
- Panel: (a), (c) en sus tres lugares, (d) página, botón e insignia; y las cadenas de Modrinth
  escapadas.
- El barrido de roles y el de autenticación cubren solos las rutas nuevas.
- La revisión visual (`scripts/visual-sweep.sh`) suma la comparación de builds y la página de
  actualizaciones, y tiene que terminar sin problemas.

## Fuera de alcance

- Renderizar Markdown.
- Aplicar actualizaciones automáticamente.
- Revisar builds que no sean el recomendado (o el último).
- Changelogs de versiones intermedias en la página (solo el enlace).
- Editar archivos locales desde el panel.
