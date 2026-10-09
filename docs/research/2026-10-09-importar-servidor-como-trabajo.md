# Investigación: escaneo e importación de un servidor como trabajo, con menos requests

**Fecha:** 2026-10-09 · **Rama:** `fix/tester-feedback` (HEAD d8ae966) · Solo lectura; no se llamó a
ningún panel ni a Modrinth. **Marco:** spec `docs/superpowers/specs/2026-10-09-economia-de-requests-design.md`
(E7: todo lo de varias requests es un trabajo; la pieza 4 ya nombra «la importación de un servidor»
como tipo de trabajo pendiente), d-7985ee-a6a410, d-7985ee-cfc81f, d-7985ee-209120. La auditoría
`docs/research/2026-10-09-auditoria-requests.md` ya propuso R7 (filas 9, 60-62, 81, 124-125, R7 en
332-339); esto la verifica y la convierte en diseño.

Notación: **P** = requests a Pterodactyl (P-panel = las que pasan por `/api/client` y cuentan para su
límite; P-nodo = la descarga a Wings), **M** = Modrinth. N jars en `/mods`, U desconocidos por hash.

---

## 1. Qué hace hoy cada paso

### 1.1 Escaneo (`PterodactylService.scan`, `src/services/pterodactyl.py:114-146`)

1. `server_files` lista el directorio: **1 P-panel** (`pterodactyl.py:129-131`; cliente
   `src/library/api/client/pterodactyl.py:71-75`).
2. `plan_scan` (`src/library/scan.py:43-68`) **ya evita** bajar lo que no cambió: clave
   `(path, size, modified_at)` (`scan.py:30-31`) contra el escaneo anterior; con `full=True` no
   confía en nada. Lo de más de 128 MiB queda `too_large` sin bajar (`pterodactyl.py:29`).
3. `_hash` (`pterodactyl.py:163-175`) baja cada jar a hashear con concurrencia 4:
   `server_files_download` = **1 P-panel** (URL firmada, `client/pterodactyl.py:77-86`, con
   `backoff` de 2 intentos) **+ 1 P-nodo** (GET a Wings, `client/pterodactyl.py:88-97`). Los bytes se
   hashean (sha1, sha512) y **se tiran**: hoy no se lee la metadata aquí.
4. `_identify` (`pterodactyl.py:177-191`): **1 M** `POST /version_files` con todos los sha1 (lo
   recién hasheado + los conservados que eran `unknown`, que se preguntan de nuevo en cada escaneo),
   y guarda las versiones en el catálogo.
5. Guarda `ServerScan` (`src/model/pterodactyl/scan.py:9-19`); `_store_drift` (148-161) compara
   contra el build solo con la base (0 requests).

El trabajo `server_scan` (`src/services/jobs.py:213-222` encola con clave `server:{id}`;
`_server_scan` 328-349) tiene dos pasos, `scan` y `drift`, y **no informa avance**
(`context.progress` nunca se llama). El programador (`jobs.py:282-303`, `main.py:56-62`) encola uno
cada `SCAN_EVERY_HOURS` y uno **completo cada `SCAN_FULL_EVERY_DAYS` (7 por defecto)**, porque
restaurar un respaldo conserva los `modified_at` (`scan.py:1-6`). El preflight y el reescaneo de una
actualización también llaman `scan` (`src/services/update.py:224`, `:401`).

### 1.2 Importación (`import_server`, `pterodactyl.py:262-298`) — **síncrona, dentro de la petición HTTP**

- Lee el último escaneo (`_import_context`, 255-260; 409 si no hay bundle, 404 si no hay escaneo).
- Por cada archivo del escaneo:
  - `identified` cuyo proyecto no está en el bundle → a `added` (sin request).
  - `unknown` con sha512 que el bundle no tiene como local ni reclamado → **lo baja otra vez**
    (`pterodactyl.py:282`: 1 P-panel + 1 P-nodo), lee la metadata (`src/library/jarmeta.py:68-80`)
    y llama `_candidates` (322-336) **en secuencia, jar por jar**:
    - `project_get(mod_id)` (`client/modrinth.py:37-42`): **1 M**; si el slug no existe, Modrinth
      da 404 → `UpstreamError` (`src/library/api/utils.py:83-84`), y como el `backoff` reintenta
      cualquier `Exception` que no sea 429 (`giveup=_rate_limited`, `client/modrinth.py:37`), **un
      slug inexistente cuesta 2 M** (leído en el código; no medido).
    - `search(name or mod_id, project_type:mod, limit=3)`: **1 M**, directo al cliente, **sin** la
      caché de 10 min de `ModrinthService.search` (`src/services/modrinth.py:76-99`).
    - con candidatos → `ImportCandidate`; sin candidatos → archivo local del bundle.
- `_add_projects` (338-341): **1 M** `project_get_many` con todos los `added`, **sin trocear de a
  100** (a diferencia de `fill_catalog`, `services/modrinth.py:45`).
- `_running` (300-320): solo base. Auditoría `server.import`.
- La ruta web (`src/routers/web/servers.py:259-274`) además llama `_import_titles` (67-70) →
  `projects_filled` → `fill_catalog`: **0-1 M** (los candidatos salidos de la búsqueda no están en el
  catálogo, así que casi siempre 1).
- Plantilla `src/web/templates/servers/_import.html`: resumen + una tarjeta por candidato con
  «Es este» (`/import/confirm`) y «Ninguno: conservar como local» (`/import/local`). Los candidatos
  **solo viven en esa respuesta**: si se recarga la página, se pierden y hay que importar de nuevo
  (lo que vuelve a bajar y buscar todo).
- `import_confirm` (388-398): 1 M (`project_get_many` de 1) + `_titles` en la ruta (0-1 M).
  `import_local` (400-407): 0 requests.
- `bundle_for_server` (356-380; ruta `servers.py:211-233`, plantilla `_new_modpack_done.html`): crea
  y vincula (base) y, si `import_mods`, corre `import_server` **en la misma petición**.
- `/v1`: `POST /v1/server/{id}/import` devuelve `ImportResult` (`src/routers/v1/pterodactyl.py:103`).

### 1.3 Conteo hoy, N=150, U=10 (los 10 con metadata legible), bundle nuevo (A=140 añadidos)

| Paso | P-panel | P-nodo | M |
|---|---|---|---|
| Primer escaneo (o completo semanal) | 151 | 150 | 1 |
| Escaneo sin cambios | 1 | 0 | 1 (re-pregunta los 10 desconocidos) |
| Importar | 10 | 10 | 10 `project_get` (+0-10 reintentos de 404) + 10 `search` + 1 `project_get_many` (140 ids, sin trocear) + 0-1 títulos = **21-32** |
| Re-importar sin decidir nada | 10 | 10 | 20-31 otra vez |
| **Escaneo inicial + importar** | **161** | **160** | **22-33**, la importación en secuencia dentro de la petición |

---

## 2. Dónde se quitan o agrupan requests

1. **Leer la metadata durante el escaneo** (R7 de la auditoría). En `_hash` los bytes ya están en
   memoria (`pterodactyl.py:169`): `read_mod_metadata(data)` es solo CPU, con tope de 256 KiB por
   archivo de metadata y sin cargar nada (`jarmeta.py:1-6`). Guardarlo en `ScanFile` como
   `metadata: Optional[ModMetadata]` + `metadata_read: bool` (para distinguir «se leyó y no tiene»
   de «escaneo viejo, no se leyó»). `plan_scan` conserva el `ScanFile` completo, así que la
   metadata pasa de un escaneo al siguiente sin costo. **Ahorra 2 P por jar desconocido en cada
   importación.** Basta guardarla para los que quedan `unknown` tras `_identify` (decidido por mí,
   reversible: el resto no la usa).
2. **Candidatos por slug en lote.** `GET /projects?ids=[...]` acepta «The IDs and/or slugs of the
   projects» (docs.modrinth.com, `getprojects`): **1 M por cada 100** en vez de 1-2 M por jar. El
   repositorio ya asume que un id/slug inexistente simplemente no vuelve (`fill_catalog`,
   `services/modrinth.py:47-48`, con caché negativa de 24 h `unknown_add`). Y se puede **juntar en
   la misma llamada** con los `added` (ids de proyecto): `⌈(A+U)/100⌉` requests en total. Los
   proyectos vueltos se guardan en el catálogo → los títulos de la página y `import_confirm` salen
   de la base (0 M).
3. **Búsqueda.** La API de búsqueda acepta **una sola `query` por request** (docs.modrinth.com,
   `searchprojects`): no hay búsqueda múltiple. Opciones: (a) buscar solo los jars cuyo slug no dio
   un proyecto `mod`; (b) usar la caché de búsqueda de 10 min (`modrinthDB.search_get/search_save`);
   (c) la faceta `title` existe (lista de facetas: `project_type, all_project_types, categories,
   versions, open_source, environment, disclosure_types, title, author, follows, project_id,
   license, downloads, created_timestamp, modified_timestamp`) y los elementos de un mismo arreglo
   se unen con OR: `[["title:Create","title:Sodium",...]]` podría traer en **una** request los
   proyectos cuyo título coincide con el `name` de cada jar. **SUPUESTO**: que `title:` sea igualdad
   exacta e insensible a mayúsculas, y el largo de URL aceptable con 10-50 títulos: requiere un
   spike contra Modrinth antes de decidir (está en las decisiones abiertas).
4. **No volver a bajar lo conocido.** Ya se hace por `(path, size, modified_at)`. Lo que sigue
   costando es el **escaneo completo semanal** (301 P por servidor por semana). Wings entrega
   `created_at` = **ctime** del inodo (`wings/server/filesystem/stat_linux.go`, v1.11.13 y v1.13.3:
   `st.Ctim`, con el comentario «TODO: remove. Ctim is not actually ever been correct…»). El ctime
   no se puede fijar desde el espacio de usuario: extraer un respaldo lo deja en la hora de la
   restauración (**SUPUESTO**: semántica de Linux, no probado sobre una restauración de Wings). Con
   `created_at` en la clave, una restauración se detecta sin escaneo completo. Riesgo: el TODO dice
   que podrían quitar o cambiar ese campo.
5. **Reescaneo tras una actualización propia** (`update.py:401`): baja de nuevo cada jar recién
   subido (su mtime cambió) aunque la app sabe exactamente qué subió (sha512 del lock). Podría
   sembrar el escaneo desde lo subido + el listado (1 P). Fuera del alcance de la importación;
   lo dejo como decisión aparte.
6. **Bug de costo menor**: `project_get` reintenta los 404 (2 M por slug inexistente). Con el lote
   desaparece en la importación, pero el mismo patrón existe en `version_get`, `project_versions`,
   etc. (`giveup` solo para 429). No lo arreglo aquí; lo anoto.
7. **`_add_projects` no trocea de a 100** (`pterodactyl.py:339`): con A>100 manda una URL de >100
   ids. La spec decidió listas de a 100. **SUPUESTO**: que Modrinth falle o trunque sobre 100; no
   verificado.

---

## 3. Hechos de Pterodactyl (fuentes primarias)

- **Límite de la Client API**: `config/http.php` — `'client' => env('APP_API_CLIENT_RATELIMIT', 256)`,
  `'client_period' => 1` (minutos) en **v1.15.1**; en **v1.11.11** el defecto era **720**. Es
  configurable por el dueño del panel. `RouteServiceProvider` (v1.15.1): la clave es
  `optional($request->user())->uuid ?: $request->ip()` y aplica `throttle:api.client` a todo
  `/api/client`. Consecuencia: **el límite es por usuario del panel, no por llave**: si la llave de
  la app es del mismo usuario que navega el panel, el escaneo le come el cupo a su navegación.
  **SUPUESTO**: la versión del panel del dueño (no se puede observar desde aquí).
- **Listar**: `GET /api/client/servers/{s}/files/list?directory=` (`routes/api-client.php`). Cada
  entrada (`FileObjectTransformer`): `name, mode, mode_bits, size, is_file, is_symlink, mimetype,
  created_at, modified_at`. **Sin hash** (pterodactyl/panel#4894, ya en `docs/references.md`).
- **Bajar**: `FileController::download` firma un JWT de 15 min y devuelve
  `{node}/download/file?token=…`; el panel no transmite bytes. Son **2 requests por archivo: 1 al
  panel (cuenta para el límite) + 1 a Wings (no pasa por `throttle:api.client`)**. Wings ignora
  `Range` y cada URL sirve una vez (`docs/references.md`, router_download.go/tokens).
- **¿Muchos archivos en una lectura?** No hay. Los métodos de `FileController` son `directory,
  contents, download, write, create, rename, copy, compress, decompress, delete, chmod, pull`;
  ninguno devuelve varios archivos. `contents` es de uno y hasta 4 MiB. **`compress` es una
  ESCRITURA** (`POST /files/compress`, crea un `.tar.gz` dentro del servidor): exigiría
  `PTERODACTYL_WRITES`, pasar por `_write`, ocupar disco del servidor de juego y borrarlo después
  (otra escritura). No sirve para un escaneo de solo lectura.
- **Metadata del listado utilizable para saltar jars**: `size`, `modified_at` (ya en la clave) y
  `created_at` (= ctime, ver §2.4).

---

## 4. Diseño: «Importar el servidor» como trabajo

### En la petición (nada remoto)
`POST /servers/{id}/import` → `PterodactylService.enqueue_import(server_id, actor)`,
`@operator_only` (nueva fila en `## Acciones y roles` de `docs/decisions.md`; `import_server` deja de
ser pública o queda como el cuerpo del trabajo). Solo base: 409 si no hay bundle; si no hay escaneo,
según D1. Encola `server_import` con clave **`server:{id}`** (la misma de escaneo/actualización:
nunca corren a la vez y el clic repetido muestra el activo), `payload = {server_id, bundle_id,
scan_id?}`, `requested_by = actor.ref()`, `max_attempts = 3` (solo lee el panel; las escrituras en
la base son atómicas e idempotentes: `bundle_local_file_add` con `$ne` sobre sha512,
`bundle_projects_add` por proyecto, `src/database/modpack.py:101-127`). Si la clave ya está ocupada
devuelve el trabajo activo (patrón `_catalog_job`, `services/modpack.py:805-815`); si es un escaneo o
una actualización, se muestra esa fila con «El servidor tiene un trabajo en curso; importa cuando
termine». Auditoría `server.import.queue`. Respuesta: la fila del trabajo (`activity/_job_row.html`)
con `HX-Trigger` de trabajo iniciado (como `modpacks.py:353-377`), que sondea cada 2 s.

### El trabajo (`_server_import`, en `JobService.handlers` o en los de `PterodactylService`)
El actor se reconstruye desde `requested_by` (como `_build_run`, `modpack.py:1059-1065`); el rol se
chequeó al encolar.

| Paso (`context.step`) | Etiqueta de avance (`context.progress`) | Requests |
|---|---|---|
| `scan` (si D1=a) | «Leyendo los jars del servidor: i de K» | P 1 + 2 por jar cambiado; M ≤1 |
| `read` | «Leyendo metadatos de jars: i de k» — solo jars `unknown` sin `metadata_read` (escaneos viejos); escribe la metadata de vuelta en el escaneo | P 2 por jar, una sola vez |
| `candidates` | «Buscando en Modrinth: i de U» (el limitador pinta la espera vía `ON_WAIT`) | M ⌈(A+U)/100⌉ (`/projects` con ids añadidos + slugs) + S búsquedas (cacheadas 10 min) |
| `apply` | «Agregando al modpack» | 0 (los proyectos ya vinieron en `candidates`) |

Al empezar `apply` se comprueba que `server.bundle_id` siga siendo `payload.bundle_id`; si no,
`JobFailed("El servidor cambió de modpack: vuelve a importar")`. `apply` guarda en el checkpoint el
`ImportResult` (añadidos, locales, demasiado grandes, candidatos con título/slug), decisiones y
excepciones de canal de `_running`, y audita `server.import`. 429 de Modrinth: el limitador espera
(trabajo de fondo, no gasta el cupo del usuario, E6). 429 de Pterodactyl: `backoff_on_rate_limit`
(lecturas). Error de red persistente: reintento con checkpoint; los pasos hechos no se repiten.

### Candidatos que esperan al admin, después del trabajo
- La fila terminada incluye `servers/_import.html` con el resultado del checkpoint (como la
  simulación incluye `_dry_run.html`, `_job_row.html:22-24`).
- La sección «Importar el servidor» de `detail.html` muestra **«Pendientes de tu decisión (n)»**
  leyendo el último `server_import` hecho del servidor (consulta nueva por clave + `kind`, porque
  `last_for_key` devuelve el último de cualquier tipo), **filtrado**: se quita el candidato cuyo
  sha512 ya es local o reclamado en el bundle, o que ya no está en el último escaneo. Recargar la
  página ya no pierde los candidatos ni obliga a re-importar.
- `import_confirm` usa el proyecto del catálogo (traído en `candidates`): 0 M; `import_local`, 0.

### `bundle_for_server`
Crea y vincula en la petición (solo base) y, con `import_mods`, **encola** la importación;
`_new_modpack_done.html` dice «Modpack creado y vinculado» + la fila del trabajo. `IMPORT_FAILED`
pasa a ser el error del trabajo, con el botón «Importar» para reintentar.

### `/v1`
`POST /v1/server/{id}/import` devuelve el `Job` con 202 (como `server/{id}/scan/job`,
`routers/v1/pterodactyl.py:169-180`). Cambio de contrato: consultar la tarjeta *evolving-contracts*
de `.agents/knowledge/INDEX.md` antes de decidir (quizá mantener la ruta síncrona un tiempo).

### Cambios al escaneo
`_hash` también lee la metadata; `ScanFile` gana `metadata` y `metadata_read`; `scan()` acepta
`report` y el trabajo `server_scan` informa «Descargando jars: i de K» e «Identificando en
Modrinth». Opcional (D4): `created_at` en la clave de `ListedFile`.

### Conteo antes/después, N=150, U=10, A=140

| | P-panel | P-nodo | M |
|---|---|---|---|
| Hoy: escaneo inicial + importar | 161 | 160 | 22-33 (en la petición) |
| Después, D1=b (importa del último escaneo) | 151 | 150 | 1 + 2 (`/projects` de a 100) + S, S ≤ 10 → **3-13** |
| Después, D1=a (la importación escanea primero; escaneo sin cambios) | 152 | 150 | 4-14 |
| Hoy: re-importar sin decidir | 10 | 10 | 20-31 |
| Después: re-importar sin decidir | 0-1 | 0 | 0-1 (todo de la base y la caché) |
| Escaneo completo semanal, hoy / con D4=a | 151 / 1 | 150 / 0 | 1 / 1 |

Solo la importación: **P 20 → 0, M 21-32 → 3-13** (o 3-4 si el spike de la faceta `title` sale
bien y D2=c). La primera vez el piso sigue siendo bajar cada jar una vez: Pterodactyl no da hashes.

---

## 5. Decisiones abiertas (recomendada primero)

**D1. ¿De qué escaneo parte la importación?**
- **a) La importación escanea primero, incremental.** El botón Importar ya no dice «Escanea
  primero»; la fila muestra «Leyendo los jars del servidor: 3 de 3» y luego «Buscando en
  Modrinth». Costo: +1 P y +0-1 M por importación si nada cambió; 2 P por jar cambiado.
- b) Usa el último escaneo, como hoy. Sin escaneo, el botón queda deshabilitado «sin escaneo».
  Costo 0 extra; puede importar un estado viejo (un jar puesto a mano hace una hora no aparece).
- c) Usa el último si tiene menos de 1 h; si no, escanea primero. Costo intermedio; dos caminos
  que probar.

**D2. ¿Cuánto busca por cada jar desconocido?**
- **a) Slug en lote para todos; búsqueda por texto solo si el slug no dio un `mod`.** El jar
  `create-1.20.1-0.5.1.jar` muestra solo «Create (create)»; uno con id `xaerominimap_fair` muestra
  3 resultados de búsqueda. Costo: M 1 + S, S = jars sin slug, hasta U.
- b) Slug en lote y búsqueda para todos (lo de hoy, agrupado). «Create (create)» + 2 parecidos.
  Costo: M 1 + U. Más opciones, más requests.
- c) Como (a), pero las búsquedas de los que fallan juntas en una con la faceta `title` (OR), y
  solo los que tampoco coinciden por título van a búsqueda libre. Costo: M ≈ 2-3 para U=10.
  Requiere un spike contra Modrinth (igualdad, mayúsculas, largo de URL).

**D3. ¿Dónde viven los candidatos pendientes?**
- **a) En el checkpoint del último trabajo de importación**, filtrados por lo ya decidido; la
  página del servidor muestra «Pendientes de tu decisión (2)» hasta la próxima importación. Sin
  colección nueva.
- b) En el `ServerScan`: desaparecen con el siguiente escaneo (el programado cada X horas los
  borraría de la vista). Más simple de leer, peor para el admin.
- c) En una colección propia por servidor: duran hasta decidirse, sobreviven a cualquier trabajo.
  Más código y un índice nuevo.

**D4. Escaneo completo semanal (301 P por servidor por semana).**
- **a) Agregar `created_at` (ctime de Wings) a la clave y llevar el completo a cada 30 días.** El
  usuario no ve diferencia; una restauración se detecta igual. Riesgo: Wings marcó ese campo como
  «TODO: remove»; si desaparece, la clave cae a la actual.
- b) Dejarlo cada 7 días. Costo: 301 P/semana/servidor.
- c) Completo solo a mano («Escanear todo de nuevo»). Costo mínimo; una restauración no se nota
  hasta que alguien lo pide.

**D5. Escaneos guardados antes del cambio (sin metadata).**
- **a) La importación baja una vez los desconocidos sin `metadata_read` y la guarda en el escaneo.**
  Invisible para el usuario salvo «Leyendo metadatos de jars: 1 de 10». Costo: 2 P por jar, una vez.
- b) Pedir un escaneo completo antes («Escanea de nuevo para importar»). Costo: 2N P y un clic.

**D6. Reescaneo después de una actualización propia (`update.py:401`).**
- **a) Sembrarlo desde lo subido** (sha512 del lock + tamaño y mtime del listado): 1 P en vez de
  2 por jar instalado. El usuario no ve cambios.
- b) Dejarlo bajando cada jar nuevo: verifica que lo que hay en disco es lo subido. Costo: 2 P por
  jar instalado en cada actualización.

**D7. Ruta `/v1/server/{id}/import`.**
- **a) Devuelve el `Job` (202), como el escaneo en trabajo.** Un cliente de `/v1` que esperaba el
  resultado tiene que consultar el trabajo.
- b) Mantener la síncrona un tiempo, marcada como obsoleta, y agregar `/import/job`. Dos caminos
  que mantener; la síncrona sigue haciendo N requests dentro de una petición.

Decidido por mí (reversible, sin cambio visible): la metadata se guarda solo de los `unknown`; clave
`server:{id}`; `max_attempts = 3`; títulos de candidatos desde el catálogo, sin `fill_catalog`.

## Sin verificar
Versión y `APP_API_CLIENT_RATELIMIT` del panel del dueño; que una restauración de Wings cambie el
ctime; semántica de la faceta `title`; si Modrinth acepta más de 100 ids en `/projects`; latencias.
