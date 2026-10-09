# Actualizar servidores, versión del loader, lista del panel y paridad de /v1: requests (2026-10-09)

**Fecha:** 2026-10-09 · **Rama:** `fix/tester-feedback` (HEAD d8ae966) · **Método:** lectura del código,
más las fuentes del panel (pterodactyl/panel v1.11.11 y v1.15.1) y de Wings v1.13.3 en GitHub. No se
llamó a ningún panel real, no se arrancó nada y no se midió tráfico.
**Continúa:** `docs/research/2026-10-09-auditoria-requests.md` (R7–R9, decisiones 9 y 10) y la spec
`docs/superpowers/specs/2026-10-09-economia-de-requests-design.md` (E1–E9).

Siglas: P = Pterodactyl (panel o Wings), M = API de Modrinth, C = CDN de Modrinth, L = meta de loaders,
D = Discord. **SUPUESTO** = no verificado en el código ni en una fuente primaria.

Supuestos de los conteos: pack de 150 mods, cambian 20 (`different_version`), nada nuevo ni quitado; el
servidor no cambió desde el último escaneo; jar medio de **~1,5 MB → 20 jars ≈ 30 MB** (SUPUESTO); parar
~30 s, backup 1–5 min, arrancar 1–3 min (SUPUESTO); `poll` = 10 s (`src/services/update.py:51`).

---

## 0. Hallazgos

| # | Hallazgo | Dónde |
|---|---|---|
| H1 | **Bug probable que bloquea la primera actualización real.** El staging lista `/mods.staging/<job>` *antes* de crearla. En Wings, listar un directorio que no existe da `404` (`router/middleware/request_error.go`, `asFilesystemError`: `ErrNotExist` → `404`), y el panel lo reenvía con su mismo código (`DaemonConnectionException`: conserva el status de Wings si es ≥ 400). El cliente lo convierte en `UpstreamError` (`src/library/api/utils.py:83`), el paso falla y el reintento falla igual (la carpeta se crea después, en la línea 271) hasta `max_attempts` = 3. Los tests no lo ven porque el panel falso devuelve `[]` para un directorio inexistente (`tests/_panel.py:41-45`). Verificado leyendo las fuentes, **no** contra un panel corriendo. | `src/services/update.py:261` |
| H2 | La subida usa **2 requests por jar** (URL firmada + POST). Wings acepta **varios archivos en un mismo POST** (`form.File["files"]`, con el tope `upload_limit` aplicado **por archivo**), y el token es de un solo uso (`IsUniqueRequest`). Subir los 20 juntos: 40 → 2 requests. | `src/library/api/client/pterodactyl.py:176-185`; Wings `router/router_server_files.go` (`postServerUploadFiles`) |
| H3 | El reescaneo de después de una actualización real **vuelve a bajar del servidor los 20 jars que la app acaba de subir y verificar**: 40 P + ~30 MB. | `update.py:395-404` → `src/services/pterodactyl.py:163-175` |
| H4 | La prueba en seco baja del CDN cada jar a instalar (20 C, ~30 MB) y la ejecución real los vuelve a bajar. | `update.py:261-268` |
| H5 | `files/pull` existe pero **no sirve aquí**: no verifica ningún hash, el panel lo limita a **5 cada 10 min por servidor** y Wings a 3 descargas simultáneas. Ya lo rechazó d-7985ee-a13b33; las fuentes lo confirman. | §1.4 |
| H6 | `/v1/modpack/{id}/build` sigue síncrono **y salta la clave `build:<id>`**: puede correr a la vez que el trabajo de build del panel; los dos calculan `number = último + 1`, el índice único `(bundle_id, number)` hace fallar a uno **después** de gastar todas sus requests. | `src/routers/v1/modpack.py:107-115`; `src/services/modpack.py:1076-1104`; `src/model/modpack/project.py:111` |
| H7 | `/v1/modpack/{id}/resolve` ya no pide nada a Modrinth: si al catálogo le falta algo encola el trabajo de catálogo y responde **409 sin el id del trabajo** (solo `detail`); su docstring sigue diciendo "Fetches from Modrinth what it needs". | `src/routers/v1/modpack.py:160-171`; `src/exceptions.py:52-57`; `src/services/modpack.py:751-772` |
| H8 | La lista del panel solo trae la **primera página**: `client?type=admin-all` sin `per_page` → 50 servidores (el máximo es 100) (`ClientController::index`: `paginate(min(per_page ?? 50, 100))`). Con más de 50 servidores, `/servers` no ofrece los demás. | `src/library/api/client/pterodactyl.py:60-67` |
| H9 | Las rutas de `/v1` que usan `get_current_user` (no `get_current_actor`) no fijan `CURRENT_USER`: para el limitador son un trabajo de fondo y **esperan dentro de la petición HTTP** en vez de responder 429. Afecta a `POST`/`PATCH /v1/modrinth/project` (1 request a M cada una, sin trocear). | `src/dependences.py:95-108`; `src/library/api/limiter.py:47-61`; `src/routers/v1/modrinth.py:25-49` |

---

## 1. El flujo de actualización: prueba en seco, ejecución real y rollback

### 1.1 Qué hace cada paso (`src/services/update.py`)

`_update` (179-211) corre pasos con checkpoint: `preflight` → `staging` → `save` → `stop` → `backup` →
`swap` → `start` → `verify` → `finish`. En una prueba en seco, `Ops.write` (64-74) solo anota la
escritura; las lecturas sí se hacen.

| Paso | Prueba en seco | Ejecución real |
|---|---|---|
| `preflight` (213-255) | escaneo (`scan`, 224: P 1 listado + 2 por jar cambiado en el servidor; M 0–1 `POST /version_files`), `server_state` (246), `backups` (249) | igual, sin registrar deriva |
| `staging` (257-284) | **baja y verifica cada jar** (266-268: C 1 por jar, tamaño + sha512) y no sube nada | listado de staging (261, **H1**), C 1 por jar, `mkdir` ×2 (271-272), subida 2 P por jar (276-278), listado final (280) |
| `save` (286) | nada | `save-all` (P 1) + 10 s |
| `stop` (304-316) | `resources` (P 1) | `resources`, `power stop`, sondeo cada 10 s; `kill` si no para |
| `backup` (318-335) | `backups` (P 1: lista aunque sea en seco, 322) | lista, `backup_create`, sondeo `backup_get` cada 10 s, `backup_get` final |
| `swap` (343-359) | nada | `mkdir` ×2, listado de `/mods` y de staging, `rename` ×2 (P 6) |
| `start` / `verify` (361-382) | nada | `power start`, sondeo hasta `running`; luego 300 s / 10 s = **30** `resources` |
| `finish` (384-393) | nada | `backup_get` + desbloquear (P 2), D 1, **reescaneo** (P 1 + 2 por jar nuevo + M 1) |

**Por qué baja la prueba en seco.** El docstring del módulo lo dice (14-15): "A dry run reads everything,
verifies the downloads and writes nothing". Comprueba que cada URL del build existe en el CDN y que sus
bytes tienen el **tamaño y el sha512 del lock** (267). El sha512 y el tamaño del lock ya vienen de
Modrinth (`CandidateFile`, `src/library/resolution.py:70-75`), así que la prueba en seco no compara contra
un dato distinto: compara el **archivo servido hoy** con lo que Modrinth declaró al congelar el build.
Lo único que detecta y la metadata no: un archivo borrado o cambiado en el CDN después del build
(SUPUESTO: raro; Modrinth no reemplaza archivos de una versión publicada). Y aunque pasara, la ejecución
real vuelve a verificar **antes de parar** (`staging` va antes que `save`/`stop`): el servidor no se toca.

### 1.2 Conteo: 150 mods, cambian 20

| | P (panel/Wings) | C | M | D | Bytes por la app |
|---|---|---|---|---|---|
| **Prueba en seco** | **5** (listado, estado, backups, resources, backups) | **20** | 0–1 | 0 | **~30 MB** de entrada |
| **Ejecución real** | **~148–184**: preflight 3 · staging 44 (1 + 2 mkdir + 40 subida + 1) · save 1 · stop ~5 · backup ~9–33 · swap 6 · start ~7–19 · verify 30 · finish 2 · reescaneo 41 | **20** | 1–2 | 1 | ~30 MB del CDN + ~30 MB al nodo + **~30 MB del nodo (reescaneo)** = **~90 MB** |
| Rollback A | ~45: stop ~5 · mkdir 1 · listados 2 · rename 2 · start ~7–19 · verify 30 | 0 | 0 | 1 | ~0 |
| Rollback B | ~50–110: stop ~5 · restore 1 · sondeo de estado cada 10 s (SUPUESTO 2–10 min → 12–60) · start · verify 30 | 0 | 0 | 1 | ~0 |

Prueba en seco + ejecución real: **C 40 (~60 MB del CDN)** por 20 jars, más ~30 MB de reescaneo.
Los sondeos son el grueso de P y son inherentes al procedimiento (d-7985ee-5cc6ee, verificar 5 min);
caben de sobra en los 256/min de la Client API (`docs/references.md`).

### 1.3 ¿Se puede verificar solo con los hashes de Modrinth?

Sí, en la prueba en seco. Lo que perdería: saber **antes** de la ejecución real que el CDN sirve hoy el
mismo archivo. Como la real verifica antes de parar, un fallo ahí deja el servidor como estaba y el
trabajo dice qué jar no coincide. Un punto medio barato: un `HEAD` por jar (C 20, 0 bytes) que confirme
que existe y su `Content-Length` (SUPUESTO: el CDN de Modrinth responde `HEAD` con `Content-Length`; hay
que sondearlo antes de escribirlo en `docs/references.md`).

### 1.4 ¿Puede Pterodactyl bajar el archivo él mismo? (`files/pull`)

Sí existe: `POST /api/client/servers/{server}/files/pull` (cuerpo `url`, `directory`/`root`,
`filename`, `use_header`, `foreground`). Fuentes:
- Panel v1.15.1 `routes/api-client.php`: `->middleware([ResourceLimit::FilePull->middleware()])`;
  `app/Enum/ResourceLimit.php`: `FilePull` = `Limit::perMinutes(10, 5)` **por servidor**
  (`by($server->uuid)`). En v1.11.11 era `throttle:10,5` (10 cada 5 min). Depende de la versión del
  panel del dueño (SUPUESTO: v1.15.x, como cita `docs/references.md`).
- Panel `FileController::pull`: reenvía a Wings y registra `server:file.pull`; **no verifica nada**.
- Wings v1.13.3 `postServerPullRemoteFile`: como máximo **3 descargas simultáneas por servidor**,
  `202` en segundo plano / `200` en primer plano; **ninguna verificación de hash**. Rechaza IPs privadas
  (`docs/research/2026-10-operacion-pterodactyl.md` §5).

Con 20 jars: ≥ 4 ventanas de 10 min (**≥ 30–40 min** solo para traerlos), y lo que llega no está
verificado: comprobarlo es bajarlo de vuelta (2 P + los bytes por jar), lo mismo que se ahorró. Ahorraría
el tráfico de la app (~60 MB) y 20 requests, a cambio de la invariante de d-7985ee-a13b33 ("lo instalado
es exactamente lo verificado"). **Recomendación: no.** Es una escritura (pasaría por `_write`).

### 1.5 Reutilizar lo que bajó la prueba en seco

Un caché de jars **por sha512**, llenado solo con bytes ya verificados:

| Dónde | Tamaño | Desalojo | Notas |
|---|---|---|---|
| Disco, volumen propio (`/cache/jars/<sha512>`) | ~30 MB por actualización de 20 jars; ~200–300 MB un pack completo (SUPUESTO) | LRU con tope (p. ej. 1 GB) o "sin uso hace 24 h" | Sin volumen se pierde al reconstruir la imagen (es caché: aceptable). Releer = volver a calcular el sha512 (CPU, sin red). |
| GridFS en MongoDB | igual | índice TTL sobre `uploadDate`/`last_used` en `fs.files` (los `chunks` se borran a mano o con un trabajo) | Crece el volumen `mongodb-data` y sus respaldos; las invariantes de datos de Mongo (runbooks) aplican a un caché que no lo necesita. |

Ganancia: la ejecución real baja **0** del CDN si la prueba en seco corrió dentro del TTL; varios
servidores con el mismo pack bajan cada jar **una vez**. Costo: un volumen o espacio en Mongo, un límite
y su limpieza, y un camino más que testear. Si la prueba en seco deja de bajar (§1.3), el caché pierde
la mitad de su sentido: solo serviría entre servidores y entre reintentos.

### 1.6 Lo que más ahorra sin decisión visible

- **H2, subida en lote**: una URL firmada y un POST con los 20 jars (en tandas por bytes, p. ej.
  ≤ 100 MB) → **P −38** por actualización. La reanudación ya existe (`present`, 261-265).
- **H3, reescaneo sembrado**: tras el `swap`, la app ya sabe sha1/sha512 de cada jar que subió y
  verificó. El reescaneo puede listar `/mods` (P 1) y tomar los hashes del lock para los archivos que
  instaló con el tamaño esperado, bajando solo lo demás → **P −40 y ~30 MB menos**. Pierde: la prueba
  de que los bytes en el nodo son los subidos (hoy solo el tamaño lo dice antes del swap). Es visible en
  cuanto a confianza, así que va a decisiones (§6, D3).
- **H1** se arregla creando la carpeta antes de listar (o tratando 404 como vacía), con un test cuyo panel
  falso responda 404 a un directorio inexistente.

---

## 2. Versión del loader

**Hoy:** `build_create` (`src/services/modpack.py:1076-1104`) llama a
`loaderAPI.loader_version(loader, game_version)` (1092) en **cada build** de Fabric o Quilt sin
`bundle.loader_version`: 1 L. El cliente (`src/library/api/client/loaders.py:21-32`) no pasa por el
limitador (`src/dependences.py:52`: sin `limiter`) y reintenta una vez cualquier excepción. Elige: Fabric,
la primera estable de `/v2/versions/loader/{mc}` (`src/library/mrpack.py:130-132`); Quilt, la mayor sin
`-` de `/v3/versions/loader/{mc}` (139-143). NeoForge/Forge: `None`. Además se llama **después** de
resolver: si la meta falla, el build cae habiendo gastado todas las requests de la revisión.

**Diseño:** `LoaderVersionCache {loader, game_version, version: Optional[str], fetched_at}`, índice único
`(loader, game_version)` y un índice TTL de limpieza (el patrón de `src/model/modrinth/search.py:18`);
el lector compara `fetched_at` él mismo (el TTL de Mongo barre cada ~60 s). Registrado en `init_beanie`
(audit `registration`).

- **Vale:** 6 h (SUPUESTO: Fabric/Quilt sacan loader cada pocas semanas). Un `None` (no hay meta para esa
  versión de Minecraft) vale 1 h: una versión nueva de Minecraft recibe su loader pronto.
- **Lo invalida:** el TTL; «Actualizar desde Modrinth» del pack (E3) lo fuerza para su (loader, versión);
  cambiar `bundle.version` o `bundle.loader` cambia la clave (no hace falta borrar); un
  `bundle.loader_version` fijado lo salta como hoy.
- **Orden:** leerlo (o pedirlo) **antes** de resolver en el trabajo de build, para fallar barato.
- **Si L falla** con una entrada vencida: usar la vencida y decirlo en el progreso del trabajo, en vez de
  fallar el build (stale-if-error). Decisión: D4.

Ahorro: 1 L por build → 0 dentro de las 6 h. Pequeño; su valor es quitar una dependencia externa del
camino del build.

---

## 3. Lista de servidores del panel (`/servers`)

**Hoy:** `server_list` (`src/routers/web/servers.py:79-97`) llama a `server_external()`
(`src/services/pterodactyl.py:55-57` → `server_get_many`, P 1) en **cada carga**, solo para ofrecer los
servidores del panel que aún no están en la app. Si falla, muestra `panel_failed` sin lista. También
`GET /v1/server/external` (`src/routers/v1/pterodactyl.py:14-24`). Solo trae la primera página (**H8**).

**Diseño (una request: no hace falta un trabajo, E7 es para "varias requests"):**
- Documento único `PanelServerList {servers: [{uuid, identifier, name, node}], fetched_at}` en Mongo.
- Cargar `/servers`: con menos de **5 min** → 0 P. Vencida o ausente → pedir en la petición (con un plazo
  corto, p. ej. 3 s), guardar y mostrar; si el plazo vence, mostrar la guardada y terminar de pedir en
  segundo plano (stale-while-revalidate).
- **Lo que se ve mientras está vieja:** la lista guardada con «Lista del panel de hace 12 min ·
  Actualizar». Si el panel no respondió: la guardada + «El panel no respondió; lista de hace 2 h».
  Hoy en ese caso no se ve ninguna.
- Botón «Actualizar lista»: 1 por minuto (global, no por usuario: es un solo recurso).
- Lo invalida además: `POST /servers` (traer un servidor) saca ese uuid de la lista ofrecida al
  momento; no hace falta pedir de nuevo.
- Paginación (H8): pedir `per_page=100` y seguir `meta.pagination` hasta la última página
  (⌈N/100⌉ requests por refresco).

Ahorro: 1 P por carga → ~1 cada 5 min como mucho.

---

## 4. Paridad de `/v1` con el panel

| Acción | Panel | `/v1` | Diferencia |
|---|---|---|---|
| Crear build | `POST /modpacks/{id}/builds` → `build_request` (`modpack.py:1020-1048`): resuelve en local, rechaza al instante si está bloqueado, si no **encola** `build:<id>` (o devuelve el activo) | `POST /v1/modpack/{id}/build` → `build_create` **síncrono** (`v1/modpack.py:107-115`): revisa Modrinth (M ~2–3 + c con el catálogo al día; ~205 en frío), L 1, congela | v1 espera todo, gasta el cupo del usuario (60/min cuando la app pasó la mitad del minuto: puede dar 429 a mitad), **sin clave de trabajo** (H6) |
| Resolver | `resolve` local; si falta algo, encola `catalog:<id>` y la franja lo muestra | misma función: 409 `CatalogPending` **sin id de trabajo** en el cuerpo (H7) | el cliente no puede seguir el trabajo; el docstring miente |
| «Actualizar desde Modrinth» | `POST /modpacks/{id}/catalogo` (`catalog_refresh`, 1/min por pack) | **no existe** | — |
| Importar un modpack (`.mrpack` o de Modrinth) | trabajo (`modpack_import.py:73-99`) | **no existe** | — |
| Importar un servidor | `POST /servers/{id}/import` **síncrono** | `POST /v1/server/{id}/import` **síncrono** | iguales; ambos N+1 (§5) |
| Escanear | trabajo (`servers.py:178-188`) | `POST /v1/server/{id}/scan` síncrono **y** `/scan/job` (202) | v1 ya tiene los dos |
| Actualizar / rollback | trabajo | trabajo (202 + `Job`) | iguales |
| Seguir un trabajo | franja, barra | `GET /v1/job/{id}` (`v1/jobs.py:9`) | existe |

`/v1` ya tiene el patrón: `POST /server/{id}/update`, `/rollback` y `/scan/job` responden **202 con el
`Job`** y se siguen con `GET /v1/job/{id}`.

**Recomendación:** las acciones de varias requests en `/v1` responden **202 + `Job`**, con las mismas
claves del panel (`build:<id>`, `catalog:<id>`): `POST /v1/modpack/{id}/build` → `build_request`;
`POST /v1/modpack/{id}/resolve` → 200 con el lock o **202 con el trabajo de catálogo** (no 409); nuevos
`POST /v1/modpack/{id}/catalog` e importaciones. El 409 de un build bloqueado se queda (d-7985ee-cf8746).
Consecuencias para un cliente: un script que leía el `ModpackBuild` del cuerpo ahora recibe un `Job` y
debe sondear `GET /v1/job/{id}` y luego `GET /v1/modpack/{id}/build/{n}`; el número está en el resultado
del paso `build` (`{"number": n}` o `{"blocked": true, ...}`, `modpack.py:1067-1072`). No hay clientes
conocidos de `/v1` fuera del dueño (SUPUESTO; nada en `docs/` los nombra). Alternativas en D5.

---

## 5. Otras acciones de usuario con más de una request externa síncrona

| Acción | Ruta → servicio | Requests (síncronas) | ¿Trabajo? |
|---|---|---|---|
| Importar un servidor | `POST /servers/{id}/import` (`servers.py:259-274`), `POST /v1/server/{id}/import` → `import_server` (`pterodactyl.py:263-298`) | por jar desconocido, **en secuencia**: P 2 (vuelve a bajar el jar, 282) + M 1 `project_get` + M 1 `search` (`_candidates`, 322-336); luego M 1 `project_get_many` + fill 0–1. **20 desconocidos ≈ 82** | no (R7 pendiente; la spec lo pone en F5) |
| Escanear por v1 | `POST /v1/server/{id}/scan` → `scan` (114-146) | P 1 + 2 por jar cambiado (primer escaneo de 150: **~301**) + M 1 | no (existe `/scan/job`) |
| Crear build por v1 | §4 | M ~2–3 + c (caliente) / ~205 (frío) + L 1 | no |
| Vistas previas con changelogs | `GET /servers/{id}/update/preview` (`intersect once`), `/jobs/{id}/preview` (`load`), `GET /modpacks/{id}/actualizaciones`, `/builds/compare` → `_preview` (`modpack.py:1162-1200`) → `_changelog` (1206-1215) | M 1 `GET /version/{id}` **por versión que llega**, concurrencia 8 (`CHANGELOG_CONCURRENCY`, 82), solo la primera vez; + fill 0–1. **20 cambios ≈ 20–21** | no (R6 pendiente: `GET /versions?ids=` en lote → 1) |
| Página o panel de un mod | `GET /mods/{p}` → `mod_page` (`modpack.py:631-653`) | M 1–3 (proyecto + relacionados) si faltan | no; acotado por plazo |
| `PUT /v1/modpack/{id}/project`, `POST /v1/modpack/{id}/project` | `pack_replace` / `project_add` → `_modrinth_projects` (485) | M 1 (`project_get_many` **sin trocear**: un pack de 300 refs en una URL) | no |
| `POST`/`PATCH /v1/modrinth/project` | `project_create_many` / `project_update_many` (`modrinth.py:124-155`) | M 1, sin trocear, **espera en vez de 429** (H9) | no |

Ya resueltas en esta rama (no cuentan): resolver y decidir (0 desde el catálogo, d-7985ee-cfc81f),
selector de versiones desde el catálogo (`modpack.py:573-595`), búsqueda guardada 10 min
(`modrinth.py:90-99`), build del panel como trabajo.

---

## 6. Decisiones abiertas para el dueño

Cada una: opciones (la recomendada primero), qué ve el usuario, costo.

**D1. La prueba en seco y las descargas del CDN** (decisión 9 de la auditoría)
1. *(recomendada)* **La prueba en seco no baja; verifica con los datos del build** (URL de un dominio
   permitido, sha512 y tamaño presentes). Ve: «Simulación: 20 jars a instalar, 31 MB; se verifican al
   actualizar». Costo: C 20 → 0 y ~30 MB menos por simulación; un archivo que el CDN cambió se detecta
   recién en la ejecución real, antes de parar el servidor.
2. **Punto medio: `HEAD` por jar.** Ve: «20 jars disponibles en Modrinth (31 MB)». Costo: C 20 requests
   sin bytes; requiere sondear el CDN.
3. **Como hoy, más caché por sha512 en disco** (§1.5). Ve: la ejecución real dice «20 jars ya verificados
   en la simulación». Costo: C 20 una sola vez entre simulación y real; un volumen, un tope (1 GB) y su
   limpieza.
4. **Como hoy.** Costo: C 40 y ~60 MB por simular y actualizar.

**D2. Subida en lote a staging (H2)**
1. *(recomendada)* **Una URL firmada y un POST por tanda** (≤ 100 MB). Ve: el progreso dice «Subiendo 20
   jars» en vez de 1 de 20 … 20 de 20. Costo: P −38; una tanda fallida reintenta la tanda entera (la
   reanudación salta lo ya subido).
2. **De a uno, como hoy.** Ve: avance jar por jar. Costo: 2 P por jar.

**D3. El reescaneo después de actualizar (H3)**
1. *(recomendada)* **Sembrado con los hashes verificados**: listar `/mods` y tomar del lock los hashes de
   lo que la app subió con su tamaño exacto; bajar solo lo demás. Ve: lo mismo que hoy («el servidor
   coincide con el build 7»). Costo: P −40 y ~30 MB menos; confía en que lo que el nodo guarda es lo que
   la app subió (tamaño comprobado, no hash).
2. **Como hoy: bajar y hashear los 20.** Costo: 40 P y ~30 MB; prueba bytes reales.
3. **Sembrado, con un escaneo completo programado al otro día** que lo confirme. Costo: el ahorro
   inmediato, más 40 P al día siguiente fuera de la ventana de la actualización.

**D4. Versión del loader en caché** (§2)
1. *(recomendada)* **6 h, `None` 1 h, se fuerza con «Actualizar desde Modrinth», y con la meta caída se
   usa la vencida.** Ve: el build dice «Fabric Loader 0.16.x (consultado hace 2 h)». Costo: 1 L → 0; un
   loader publicado hace menos de 6 h entra en el build siguiente.
2. **Igual pero con la meta caída el build falla.** Ve: «No se pudo consultar la versión de Fabric». Costo:
   un build fallido mientras L esté caído.
3. **Sin caché, pero antes de resolver.** Costo: 1 L por build; falla barato.

**D5. `/v1` y los trabajos** (§4)
1. *(recomendada)* **202 + `Job` en build, resolver incompleto, catálogo e importaciones, con las claves
   del panel.** Ve (cliente): `{"id": "...", "kind": "build_create", "status": "queued"}` y sondea
   `/v1/job/{id}`. Costo: rompe a quien leía el `ModpackBuild` del cuerpo.
2. **Las dos: lo síncrono queda con `?wait=true` (por defecto 202).** Ve: igual que 1, y el script viejo
   agrega `?wait=true`. Costo: dos caminos que testear; el síncrono igual debe tomar la clave
   `build:<id>` (H6) y responder 409 si hay uno activo.
3. **Sigue síncrono**, solo con la clave `build:<id>` y el id del trabajo en el 409 de resolver. Costo:
   requests largas, 429 a mitad de un build; dos semánticas para la misma acción.

**D6. Lista de servidores del panel** (§3; decisión 10 de la auditoría)
1. *(recomendada)* **Guardada 5 min, refresco al cargar si está vieja, botón 1/min, y la guardada si el
   panel falla.** Ve: «Lista del panel de hace 3 min · Actualizar». Costo: 1 P por carga → ≤ 1 cada 5 min.
2. **Solo con el botón** (nunca sola). Ve: «Lista de hace 2 días · Actualizar». Costo: 0 P por carga; un
   servidor nuevo no aparece hasta pulsar.
3. **Como hoy.** Costo: 1 P por carga; con el panel caído no se ofrece ninguno.

**D7. Importar un servidor como trabajo** (R7 + E7; hoy 4 requests por jar desconocido, síncrono)
1. *(recomendada)* **Trabajo con avance (`server:<id>`) que lee la metadata del jar durante el escaneo**
   (los bytes ya están en `_hash`) y busca en lote lo que pueda. Ve: la franja «Importando: 7 de 20
   jars». Costo: 20 desconocidos ≈ 82 → ~22; el escaneo guarda un poco más.
2. **Trabajo, sin cambiar las requests.** Costo: ~82, pero visible y sin duplicarse.
3. **Como hoy.**

---

## 7. No verificado

- Que el panel del dueño reenvía el 404 de Wings al listar un directorio inexistente (H1): leído en las
  fuentes de v1.11.11 y Wings v1.13.3, no probado; la versión del panel del dueño no está en el repo.
- El tamaño medio de un jar, los tiempos de parada, backup, arranque y restauración.
- Que el CDN de Modrinth responde `HEAD` con `Content-Length`, y que sus descargas no cuentan contra los
  300/min de la API.
- Que Modrinth nunca reemplaza el archivo de una versión publicada.
- Que `rename` en Wings conserva el `mtime` (afecta a la clave del escaneo sembrado, D3).
- La cadencia de versiones de Fabric/Quilt Loader (TTL de D4).
- Que no hay clientes de `/v1` fuera del dueño.
- Ningún conteo se midió: salen de leer el código.
