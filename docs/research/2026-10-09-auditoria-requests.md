# Auditoría de requests salientes — modpack-creator (2026-10-09)

**Fecha:** 2026-10-09 · **Método:** lectura del código (sin medir tráfico).

**Origen:** pedido del dueño (2026-10-09): el número de requests es muy importante; limitarlas por usuario, juntar las que se puedan, cachear en la base y no recargar la información de un modpack más de una vez cada 5 minutos, salvo a mano.

Auditoría de solo lectura de la rama `fix/tester-feedback` (HEAD 74fcfbe más los archivos de importación sin versionar). No se cambió nada en el repositorio; no se arrancó ningún servidor ni se usó la red. Los números para "un pack de ~150 mods" suponen **~50 dependencias transitivas y 3 rondas de resolución** (SUPUESTO: pack típico de Fabric; los números reales dependen del pack).

Requisito del dueño que se audita: limitar las requests por usuario; juntar todo lo que se pueda; guardar una copia local (MongoDB) de todo lo posible; no recargar la información de un modpack más de una vez cada 5 minutos, más un refresco manual.

---

## Resumen

- **Cuello de botella principal:** cada resolución parte de un catálogo vacío en memoria y vuelve a pedir a Modrinth las versiones de cada proyecto, una request por proyecto y en secuencia: ~205 requests por resolución en un pack de 150 mods (§0, hallazgo 1).
- **Se multiplica por los clics:** resolver se dispara con muchos clics ordinarios (cada decisión, las acciones de las tarjetas de problema, "Ignorar dependencia" y la creación de un build). Decidir 10 mods uno a uno son ≈ 2,050 requests ≈ 7× el límite de 300/min de Modrinth.
- **No hay ningún limitador:** ni por usuario de entrada, ni presupuesto de salida hacia Modrinth, y las lecturas de Modrinth reintentan cualquier excepción (429 incluido) una vez, sin respetar `Retry-After`.
- **Qué guarda ya MongoDB y qué no** (§3): el catálogo de versiones se escribe pero nunca se lee para resolver; el diseño de `fetched_at` + TTL de `docs/research/2026-10-05-cache-catalogo-modrinth.md` no está implementado.
- **Recomendaciones R1–R9** ordenadas por requests ahorradas (§6); la mayor es resolver desde el catálogo de MongoDB con la ventana de 5 minutos y detección de cambios (R1): ~205 → 0 dentro de la ventana, ~2-3 + c fuera de ella.
- **Diez decisiones abiertas** para el dueño (§7). Nada se midió: los conteos salen de leer el código (§8).

---

## 0. Hallazgos principales

| # | Infractor | Requests por acción (150 mods) | Dónde |
|---|---|---|---|
| 1 | **Cada resolución parte de un catálogo vacío en memoria** y pide a Modrinth otra vez las versiones de cada proyecto, una request por proyecto, en secuencia. | **~205** (150 + ~50 `project_versions` + ~3 `project_get_many` + 0-1 `version_get_many`) | `src/services/modpack.py:745-766` (`catalog = Catalog()` en 746; bucle en 753-756) |
| 2 | La resolución se dispara con muchos clics ordinarios, no solo con "Resolver": cada decisión individual, las decisiones en bloque, cada acción de las tarjetas de problema (`contexto`), "Ignorar dependencia", y **la creación de un build resuelve otra vez**. | ~205 **por clic**. Decidir 10 mods uno a uno ≈ 2,050 requests ≈ 7× los 300/min de Modrinth | `src/routers/web/modpacks.py:205, 528, 545, 564, 588, 654`; `src/services/modpack.py:794` |
| 3 | No hay limitador en ninguna parte: ni límite de entrada por usuario, ni presupuesto de salida para Modrinth, y las lecturas de Modrinth reintentan **cualquier** excepción (429 incluido) una vez tras ~1 s sin respetar `Retry-After` / `X-Ratelimit-Reset`. | Un 429 a mitad de una resolución hace fallar toda la resolución. | `src/library/api/client/modrinth.py:29-130` (`backoff.on_exception(backoff.expo, Exception, max_tries=2)`) |
| 4 | Una resolución que falla a mitad de ronda **tira lo que ya trajo**: las versiones se guardan (upsert) solo al final de cada ronda. | Hasta ~150 requests desperdiciadas por fallo, y otra vez al reintentar | `src/services/modpack.py:762-765` |
| 5 | `fill_catalog` vuelve a pedir **en cada render** cualquier proyecto que no tenga icono en Modrinth, o que Modrinth no conozca (nunca se guarda). Nunca refresca los proyectos guardados con icono, así que sus datos envejecen para siempre. | 1 request extra por render de página, para siempre (en lote, pero perpetua) | `src/services/modrinth.py:28` |
| 6 | El selector de versiones de cada tarjeta de mod siempre pregunta a Modrinth (`hx-trigger="toggle once"`), aunque la resolución acaba de traer exactamente la misma lista filtrada. | 1 por tarjeta abierta | `src/services/modpack.py:541`; `src/web/templates/modpacks/_mods.html:67` |
| 7 | Búsqueda en vivo: un `/search` de Modrinth por cada pausa al teclear (debounce de 300 ms), sin caché. | 1 por pausa al teclear (~3-8 por búsqueda) | `src/web/templates/modpacks/detail.html:22`; `src/routers/web/modpacks.py:363` |
| 8 | Los changelogs se piden de a una versión (`GET /version/{id}`), N+1, concurrencia 8. | 1 por versión que llega (solo la primera vez; luego en caché) | `src/services/modpack.py:885-922` |
| 9 | Importar un servidor descarga otra vez cada jar desconocido (2 requests a Pterodactyl) y ejecuta `project_get` + `search` por jar, en secuencia. | 4 por jar desconocido | `src/services/pterodactyl.py:281-336` |
| 10 | Una prueba en seco descarga del CDN cada jar que la actualización instalaría; la ejecución real los descarga todos otra vez. | 1 descarga del CDN por jar, dos veces | `src/services/update.py:263-266` |

---

## 1. Inventario de llamadas salientes

### 1.1 Capa HTTP compartida
- `HttpAPI` (`src/library/api/__init__.py:30-150`): una `ClientSession` por cliente, reutilizada
  (keep-alive). Sin limitador de tasa, sin caché de respuestas, sin lectura de las cabeceras
  `X-Ratelimit-*`. `validate_results` no hace nada (`src/library/api/utils.py:80`).
- `backoff_on_rate_limit` (`src/library/api/utils.py:27-39`): respeta `Retry-After` (tope 60 s, 4
  intentos). **Solo lo usa el cliente de Pterodactyl.**
- Todos los clientes son `Singleton` de DI (`src/dependences.py:45-48`) y la app corre como un
  solo proceso de uvicorn (`docker/Dockerfile:37`, sin `--workers`), así que un limitador en
  proceso vería todas las requests salientes.

### 1.2 Modrinth (`src/library/api/client/modrinth.py`)

Todas las lecturas: `@backoff.on_exception(backoff.expo, Exception, max_tries=2)`, así que
cualquier error (429 incluido) recibe un reintento rápido. Ninguna respeta `Retry-After`.

| Método (línea) | Endpoint | ¿En lote? | Quién lo llama |
|---|---|---|---|
| `project_get` (30) | `GET /project/{slug}` | individual | `ModrinthService.project_create` (services/modrinth.py:87, sin ruta que lo use), `project_update` (107, sin uso), `PterodactylService._candidates` (services/pterodactyl.py:327, **por jar desconocido**) |
| `project_get_many` (37) | `GET /projects?ids=[…]` | **lote, sin trocear** | `fill_catalog` (services/modrinth.py:31), `project_create_many` (96, `POST /v1/modrinth/project`), `project_update_many` (121, `PATCH /v1/modrinth/project`), `version_dependencies` (140, sin uso), `ModpackService._modrinth_projects` (services/modpack.py:440), `mod_page` (587), `_fetch_types` (773, cada ronda de resolución), `PterodactylService._add_projects` (services/pterodactyl.py:339) |
| `search` (45) | `GET /search` | individual | `ModrinthService.search` (services/modrinth.py:65) ← búsqueda web (routers/web/modpacks.py:363); `_candidates` (services/pterodactyl.py:333, **por jar desconocido**) |
| `game_versions` (56) | `GET /tag/game_version` | individual | no se encontró quien lo llame |
| `project_versions` (64) | `GET /project/{id}/version` | **por proyecto** | `_resolve` (services/modpack.py:755, **bucle secuencial**), selector `project_versions` (541), `mod_versions_all` (692), `project_version_update_filter` (services/modrinth.py:151, sin uso) |
| `version_get` (82) | `GET /version/{id}` | **por versión** | `_version_project` (services/modpack.py:390, ante un pin cuya versión no está en el catálogo), `_changelog` (919, **N+1** en `_preview`) |
| `version_get_many` (89) | `GET /versions?ids=[…]` | lote, sin trocear | `_resolve` (759, versiones fijadas o desaparecidas), `version_dependencies` (services/modrinth.py:135/137, sin uso), `project_version_update_all` (161, sin uso) |
| `files_hash_get` (97) | `GET /version_file/{hash}` | individual | no se encontró quien lo llame |
| `files_hash_post_many` (107) | `POST /version_files` | lote | `PterodactylService._identify` (services/pterodactyl.py:181), una por escaneo; el diseño de importación de mrpack sin versionar prevé 2 por importación |
| `files_update_hash_post` (118) | `POST /version_file/{h}/update` | individual | no se encontró quien lo llame |
| `files_update_hash_post_many` (130) | `POST /version_files/update` | lote | `UpdateCheckService.compute` (services/update_check.py:125), una por grupo de loader |

### 1.3 Pterodactyl (`src/library/api/client/pterodactyl.py`)
Las lecturas van envueltas en `backoff_on_rate_limit` y en un reintento que se rinde ante un 429.
Las escrituras pasan por `_write` y nunca se reintentan.

| Método (línea) | Quién lo llama | Notas |
|---|---|---|
| `server_get_many` (60) | `server_external` (services/pterodactyl.py:57) ← **cada carga de `/servers`** (routers/web/servers.py:93) y `GET /v1/server/external` | 1 por carga de página, sin caché |
| `server_get` (52) | `server_create` (72), `server_update` "refresh" (85) | manual |
| `server_files` (71) | `scan` (130), `/v1/server/{id}/files` (110), staging/swap/rollback de la actualización (update.py:261,280,349,350,433,434) | 1 por listado |
| `server_files_signed_url` (79) + GET al nodo | `server_files_download` (89) ← `_hash` (pterodactyl.py:169), `import_server` (282) | **2 requests por jar** |
| `server_state` (101), `resources` (110), `backups` (118), `backup_get` (128) | trabajo de actualización: preflight (update.py:246,249), sondeo cada `poll`=10 s en `_wait`/`_verify` (292-299, 374-377), sondeo de backup (329-333), restauración (449-450) | sondeo; hasta 30 (verify) + 60 (stop) + 180 (backup) + 180 (restore) lecturas por actualización, en el peor caso |
| escrituras (`backup_create`, `files_*`, `server_files_upload` (URL firmada + POST), `server_command`, `server_power`) | solo el trabajo de actualización | inherentes al procedimiento |

### 1.4 Metas de loaders (`src/library/api/client/loaders.py:26-32`)
`loader_version` → 1 GET a meta.fabricmc.net o meta.quiltmc.org. Lo llama solo `build_create`
(services/modpack.py:803) cuando `bundle.loader_version` no está fijado, en cada build. Sin caché.

### 1.5 Discord (`src/library/api/client/discord.py:14`)
`send` → 1 POST por notificación, enviada desde el trabajo `notify` (services/notify.py:29-50).
Orígenes: build creado (services/modpack.py:813), chequeo de actualizaciones con un digest nuevo
(services/update_check.py:210-213), deriva de escaneo cambiada (services/jobs.py:320) y
resultados de actualización (services/update.py `_notify`). Volumen bajo, ya deduplicado para los
chequeos de actualización.

### 1.6 Descargador (`src/library/api/client/download.py:13`)
`download_file` → 1 GET al CDN por jar, desde `UpdateService._staging` (services/update.py:266),
**también en las pruebas en seco**. Reintenta una vez ante cualquier excepción.

---

## 2. Requests por acción del usuario (pack de ~150 mods)

M = API de Modrinth, P = Pterodactyl, C = CDN de Modrinth, L = meta de loaders, D = Discord.

| Acción del usuario | Ruta | Requests salientes | ¿En lote? |
|---|---|---|---|
| Abrir la página de un modpack / cambiar de vista (`vista`) | `GET /modpacks/{id}` (routers/web/modpacks.py:322) → `_mods_context` → `mod_list` (services/modpack.py:447) | M: 0 en caliente; **1 en cada render mientras algún proyecto no tenga icono o sea desconocido** (fill_catalog línea 28); 1 en frío | sí (un `project_get_many`, sin trocear) |
| Abrir el selector de versiones de una tarjeta | `GET /modpacks/{id}/projects/{p}/versions` (modpacks.py:410) → `project_versions` (modpack.py:528) | M: **1 por tarjeta, cada vez** (nunca desde el catálogo) | por proyecto |
| Buscar | `GET /modpacks/{id}/search` (modpacks.py:346) | M: 1 por pausa al teclear, sin caché ni largo mínimo | n/a |
| Agregar un mod | `POST /modpacks/{id}/projects` (373) → `_modrinth_projects` (modpack.py:440) + `_mods_context` | M: 1 (siempre, aunque el catálogo lo tenga) + 0-1 fill | sí |
| Quitar un mod (simple) | `DELETE …/projects/{p}` (395) | M: 0-1 (fill) | — |
| **Resolver** | `POST /modpacks/{id}/resolve` (516) → `resolve` (modpack.py:700) | M: **~205** (ronda 1: 1 `project_get_many`(150) + 150 `project_versions`; ronda 2: 1 + ~40; ronda 3: 1 + ~10; + 0-1 `version_get_many` para pins) + 0-1 fill | solo la consulta de tipos va en lote; versiones por proyecto, en secuencia |
| Decidir 1 mod | `POST …/decisions` (531): `decision_set` + **resolve** | M: ~205 | igual que arriba |
| Decidir en bloque | `POST …/decisions/bulk` (548) | M: ~205 | igual que arriba |
| Acción de tarjeta de problema (quitar, fijar, desfijar, excepción de canal, "Ignorar dependencia") con `contexto` | `_acted` (195-214), `replacement_set` (712) | M: ~205 (+1 `version_get` para un pin cuya versión no está en el catálogo, modpack.py:390) | igual que arriba |
| Crear build | `POST …/builds` (567) o `POST …/actualizaciones/build` (644) → `build_create` (modpack.py:783) | M: ~205 + L: 1 (fabric/quilt sin loader_version) + D: 1 (trabajo) + 0-1 fill si está bloqueado (`_build_dialog`, 243) | igual que arriba |
| Abrir la página / panel lateral de un mod | `GET /mods/{p}`, `/mods/{p}/panel` (routers/web/mods.py:50, 78) → `mod_page` (modpack.py:551) | M: 0-1 si está guardado (mismo re-pedido por falta de icono); 1-2 si no está guardado (`project_get_many` + fill de relacionados) | sí |
| "Cargar todas desde Modrinth" | `GET /mods/{p}/versiones` (mods.py:105) → `mod_versions_all` (modpack.py:684) | M: 1 (todas las versiones, sin filtro), en cada clic | individual |
| Chequeo de actualizaciones (manual o diario) | `POST …/actualizaciones/buscar` (631) → trabajo → `compute` (update_check.py:109) | M: **1-3** (una por grupo de loader) | **sí** |
| Abrir la página de actualizaciones | `GET …/actualizaciones` (606) → `update_preview` (modpack.py:930) + `projects_filled` | M: 1 `version_get` **por versión actualizada** la primera vez (luego los changelogs quedan guardados), + 0-1 fill. Se recarga sola con `job-finished` (updates.html:14) | changelogs N+1 |
| Comparar builds | `GET …/builds/compare` (669) | M: 1 por versión que llega (primera vez) + 0-1 fill | N+1 |
| Vista previa de actualización de servidor | `GET /servers/{id}/update/preview` (`intersect once`) y `/jobs/{id}/preview` (`load`) → `server_change_preview` | M: 1 por versión que llega (primera vez) + 0-1 fill | N+1 |
| Escanear un servidor (trabajo manual, o cada `SCAN_EVERY_HOURS`) | `PterodactylService.scan` (pterodactyl.py:114) | P: 1 listado + **2 por jar cambiado** (primer escaneo de 150 jars: ~301); M: 1 `POST /version_files` | M en lote; P por jar (concurrencia 4) |
| Importar un servidor | `import_server` (pterodactyl.py:263) | por jar desconocido: **P 2 (re-descarga) + M 1 `project_get` + M 1 `search`**, en secuencia; luego M 1 `project_get_many` + 0-1 fill para los títulos | **N+1** |
| Lista de servidores | `GET /servers` (servers.py:79) | P: 1 `server_get_many` en cada carga | — |
| Detalle de servidor | `GET /servers/{id}` (111) | M: 0-1 fill; P: 0 | — |
| Actualizar un servidor (trabajo) | `UpdateService` | preflight = un escaneo (arriba) + P 2; staging: C 1 + P 2 por jar a instalar (C también en prueba en seco); sondeo de P cada 10 s | inherente |
| Sondeo de la fila de trabajos | `hx-trigger="every 2s"` (_job_row.html:11, _job_tr.html:11) | **0 salientes** (solo MongoDB) | — |

Disparadores de htmx que causan llamadas salientes: búsqueda `input changed delay:300ms`; selector
de versiones `toggle once` por tarjeta; vista previa de actualización `intersect once`; vista
previa de prueba en seco `load`; recargas por `job-finished` de la página de actualizaciones y del
detalle de servidor (cada una vuelve a ejecutar lecturas de fill/changelog, que quedan en caché
tras la primera vez). Ningún sondeo periódico de htmx llama a una API externa.

---

## 3. Qué guarda ya MongoDB, y cuándo la app vuelve a pedir

**Guardado:**
- `ModrinthProject` (src/model/modrinth/project.py:44): el proyecto completo, incluidos
  `versions` (todos los ids de versión) y `updated` (fecha de su última versión). Sin
  `fetched_at`.
- `ModrinthVersion`: toda versión que alguna llamada haya devuelto (upsert; el `changelog` se
  conserva entre refrescos, database/modrinth.py:159-168). Sin marca de pedido, y sin registro de
  qué lista filtrada (proyecto, versión de juego, loaders) se ha traído completa.
- Escaneos con sha1/sha512 por archivo e identificación; chequeos de actualización; resoluciones
  guardadas (`resolved_at`, `request_digest`); builds (locks congelados).

**Cuándo la app vuelve a pedir lo que ya tiene:**

| Dato | Regla hoy |
|---|---|
| Metadatos de proyecto para mostrar (`fill_catalog`) | Nunca se refrescan si están guardados **con** icono (rancios para siempre). Se piden **siempre** de nuevo si están guardados sin icono. Los ids desconocidos nunca se guardan, así que se piden de nuevo en cada render. Un fill que no llega al `FILL_DEADLINE` de 5 s se cancela y su resultado se pierde; el siguiente render vuelve a pedir. |
| Versiones de proyecto **para resolver** | **Siempre se piden de nuevo**: `_resolve` crea `Catalog()` vacío (modpack.py:746) y nunca lee `ModrinthVersion` de MongoDB. Solo *escribe* el catálogo. |
| Tipos de proyecto para resolver | Siempre se piden de nuevo (`_fetch_types`, modpack.py:768). |
| Selector de versiones | Siempre se pide de nuevo (modpack.py:541). |
| Versiones en la página de un mod | Se leen de MongoDB (modpack.py:617); "Cargar todas" siempre vuelve a pedir. |
| Changelogs | Se piden una vez por versión y luego se conservan (modpack.py:913-922). |
| Chequeo de actualizaciones | En lote; horario diario (update_check.py:158-188) + manual. El manual no tiene enfriamiento: solo "un trabajo activo por bundle" (409). |
| Identificación de escaneo | Los jars sin cambios (misma ruta/tamaño/mtime) no se vuelven a hashear; los jars aún desconocidos se vuelven a preguntar en cada escaneo (1 request en lote). |
| Lista de servidores del panel (`/servers`) | Siempre se pide de nuevo. |
| Versión del loader | Siempre se pide de nuevo en cada build salvo que el bundle la fije. |

Entonces, a las preguntas concretas: **sí**, resolver vuelve a pedir las versiones de cada
proyecto cada vez; **abrir una página no**, salvo el re-pedido perpetuo por falta de icono /
id desconocido; **el chequeo de actualizaciones** es barato (1-3 requests en lote) pero siempre
va a Modrinth.

El documento de investigación `docs/research/2026-10-05-cache-catalogo-modrinth.md` ya diseñó
`fetched_at` + TTL (24 h, 7 d para iconos), una caché negativa para ids desconocidos, trozos de
~100 ids, stale-while-revalidate y lectura de `X-Ratelimit-Remaining`. **Nada de eso está
implementado** (la línea 28 de services/modrinth.py no ha cambiado). No cubría la resolución.

---

## 4. Limitación de tasa hoy

- **Modrinth:** 300 requests/min por IP, con o sin token; cabeceras `X-Ratelimit-Limit`,
  `-Remaining`, `-Reset` (docs/research/2026-10-05-cache-catalogo-modrinth.md §1, de
  docs.modrinth.com más una sonda real del 2026-10-05). `docs/references.md` **no** registra este
  límite; solo dice "Modrinth rate-limiting may back off" en un comentario
  (services/modpack.py:42). Una resolución de un pack de 150 mods usa ~68% del presupuesto de un
  minuto.
- **Pterodactyl:** 256 requests/min en la API de cliente (comentario en
  services/pterodactyl.py:30-31; docstring de `tests/test_rate_limit.py`). Se maneja de forma
  reactiva: `backoff_on_rate_limit` espera lo que pide `Retry-After`. Las escrituras nunca se
  reintentan. El escaneo limita la concurrencia a 4.
- **Limitador de salida:** ninguno. Solo semáforos fijos: `SCAN_CONCURRENCY = 4`
  (services/pterodactyl.py:32) y `CHANGELOG_CONCURRENCY = 8` (services/modpack.py:41).
- **Entrada, por usuario:** ninguno. No hay middleware (main.py:91-118 tiene CORS, sesiones,
  mismo origen e id de request, nada más), ni slowapi/limiter en `requirements.txt`. Los únicos
  frenos son "un trabajo activo por bundle/servidor" (clave de trabajos) y `MAX_DECISIONS = 500`.
  Un usuario (o dos pestañas) puede disparar resoluciones, búsquedas y selectores sin límite. La
  identidad del actor (`iss`+`sub`, src/model/users/actor.py) está disponible en cada request y
  podría servir de clave para un limitador.
- **Manejo de 429 de Modrinth:** un 429 se convierte en `HTTP_429_TOO_MANY_REQUESTS`
  (utils.py:70-72) y el `backoff` del método de Modrinth lo reintenta una vez tras ~1 s (expo con
  jitter completo), ignorando `Retry-After`. Luego la acción falla.

---

## 5. Dónde aplica una regla "por modpack, como máximo cada 5 minutos + manual"

Operaciones que piden datos de Modrinth para un modpack:

| Operación | Qué pide | ¿Aplica la regla de 5 min? |
|---|---|---|
| `resolve` / `build_create` (`_resolve`, `_fetch_types`) | metadatos de proyecto (tipo) + listas de versiones por proyecto para el objetivo del pack + versiones fijadas | **Sí: el objetivo principal.** Dentro de la ventana, resolver lee las versiones de MongoDB. Fuera de ella, hace primero un chequeo de cambios barato. Un proyecto o dependencia que el catálogo nunca haya traído para este objetivo igual se debe pedir (en lote); la ventana no puede ocultar lo que nunca se trajo. |
| Fill de `mod_list` (`fill_catalog`) | metadatos de proyecto (título, icono, loaders, versiones de juego) | Sí: se refresca con el mismo refresco por pack; entre refrescos, solo MongoDB. Arregla el bucle de falta de icono. |
| Selector de versiones (`project_versions`) | la misma lista filtrada que pide resolver (filtros idénticos: modpack.py:541 frente a 755) | Sí: se sirve desde MongoDB cuando la lista (proyecto, objetivo) se trajo dentro de la ventana. |
| Página / panel de un mod abierto desde un pack (`mod_page`, `pack=`) | metadatos de proyecto del mod y sus relacionados | Sí, vía `pack`; sin pack, un TTL por proyecto. |
| Chequeo de actualizaciones (`compute`) | últimas versiones por hash | Diario + manual; agregar un piso de 5 min al manual (mostrar el último chequeo si es más reciente). |
| Página de actualizaciones / comparar / vistas previas (changelogs) | inmutables una vez traídos | Ya en caché para siempre; solo hay que agruparlos en lote. |
| Búsqueda | no son datos del modpack | Caché separada de TTL corto con clave (query, versión, loader). |

**Estado necesario:**
1. `ModpackBundle.modrinth_refreshed_at: Optional[datetime]`: cuándo se chequearon por última vez
   contra Modrinth los proyectos y listas de versiones de este pack. Resolver, mod_list y el
   selector lo leen. El botón manual lo ignora (con su propio enfriamiento).
2. **Marca de pedido de lista de versiones**, una por (project_id, game_version, loaders
   ordenados): un nuevo `Document` (p. ej. `ModrinthVersionFetch {project_id, game_version,
   loaders, fetched_at, project_updated, version_ids}`), índice único sobre la clave, registrado
   en `init_beanie` (invariante, audit `registration`). Sin ella, resolver no puede distinguir "el
   catálogo tiene todas las versiones de P para 1.21.1/fabric" de "el catálogo tiene 3 versiones
   de P que un escaneo identificó por casualidad".
3. `ModrinthProject.fetched_at` (el diseño del documento de investigación) para el TTL de
   metadatos fuera del contexto de un pack, más una caché negativa para los ids que Modrinth
   omitió en una respuesta **exitosa**.
4. El detector de cambios: `ModrinthProject.updated` y `versions` (ambos ya guardados). Un
   `project_get_many` por cada 100 ids devuelve, para cada proyecto, la fecha de su última
   versión y su lista de ids de versión. Un proyecto cuyo `updated` no es más nuevo que su marca
   de pedido (o cuyos ids de versión nuevos son ninguno) no necesita request de versiones. Para
   uno que cambió, o bien un `project_versions` con los filtros del objetivo, o bien un
   `version_get_many` solo con los ids nuevos de todos los proyectos cambiados juntos, filtrando
   en local.

---

## 6. Recomendaciones, ordenadas por requests ahorradas

### R1. Resolver desde el catálogo de MongoDB, con la ventana de 5 minutos y detección de cambios
- **Qué:** `_resolve` siembra `Catalog` desde `ModrinthVersion` (por proyecto + objetivo) y los
  tipos desde `ModrinthProject`, para los proyectos cuya marca de pedido está fresca (dentro de 5
  min del `modrinth_refreshed_at` del pack, o sin cambios según `updated`). Pide solo (a)
  proyectos o dependencias sin marca para este objetivo, en lote, y (b) pasada la ventana, el
  lote de detección de cambios más las versiones de los proyectos cambiados. Guarda las versiones
  a medida que responde cada request, no al final de la ronda (arregla el hallazgo 4).
- **Dónde:** services/modpack.py:745-779; database/modrinth.py (una consulta por ids de proyecto
  + game_version + loaders, la colección de marcas); src/model (nuevo Document + `init_beanie`).
- **Ahorro por acción (150 mods + 50 deps):** dentro de la ventana ~205 → **0** (o k para k
  proyectos/deps nuevos, en lotes de ⌈k/100⌉ + k listas de versiones). Fuera de ella: ~205 →
  **~2-3 + c**, donde c = proyectos con una versión nueva desde el último chequeo (SUPUESTO: 0-10
  por día). Decidir 10 mods seguidos: ~2,050 → ~0-5.
- **Riesgos:** una resolución puede usar versiones de hasta 5 minutos de antigüedad; una versión
  publicada o **borrada** en esa ventana se pierde. Una versión borrada en un build fallaría al
  descargar; el trabajo de actualización ya verifica tamaño y sha512 antes de tocar el servidor
  (update.py:266-268), así que falla de forma segura. Una versión editada in situ (versiones de
  juego, loaders, estado cambiado a no listado) no la detecta `updated` / la diferencia de ids de
  versión (SUPUESTO: `updated` solo se mueve con una versión nueva, según la documentación citada
  en el documento de investigación). Hacen falta tests de que resolver con catálogo caliente no
  hace ninguna llamada y con uno frío hace las mismas llamadas que hoy. La corrección del lock
  depende de que la marca sea exacta por objetivo.
- **Decisiones abiertas:** la ventana (¿5 min como se pidió?); si **la creación de un build**
  puede usar la ventana o debe hacer siempre el chequeo de cambios (recomendado: el build hace el
  chequeo de cambios barato, ~2-3 requests, porque un build queda congelado); si detectar cambios
  con `updated` + diferencia de ids de versión, o con `POST /version_files/update` sobre los
  hashes del lock anterior (1-3 requests, pero solo dice "la última por proyecto").

### R2. Límites de entrada por usuario + un presupuesto global de salida para Modrinth
- **Qué:** (a) un token bucket por actor (`iss`+`sub`) en las acciones que pueden llamar hacia
  afuera: búsqueda, selector, acciones tipo resolver, build, "Cargar todas", refresco manual,
  encolar escaneo/importación. 429 con un mensaje claro en el panel. En proceso basta (un solo
  proceso de uvicorn); MongoDB si eso cambia. (b) Un limitador de todo el proceso en
  `ModrinthAPI` (p. ej. 240/min, bajo los 300/min), leyendo `X-Ratelimit-Remaining`/`-Reset`.
  (c) Las lecturas de Modrinth respetan el 429 como Pterodactyl (`backoff_on_rate_limit` +
  `giveup=not_rate_limited`), fuera de los plazos de página. Los fills oportunistas (búsqueda,
  iconos) se omiten cuando el presupuesto es bajo.
- **Dónde:** una dependencia en `src/web` / `src/library/auth` para (a);
  src/library/api/client/modrinth.py y src/library/api/utils.py para (b)/(c); un test como
  `tests/test_rate_limit.py` para Modrinth.
- **Ahorro:** acota el peor caso en vez de reducir el típico; elimina el modo de fallo de las
  tormentas de 429 a mitad de una resolución.
- **Riesgos:** límites demasiado bajos bloquean trabajo masivo legítimo (una importación, una
  primera resolución de un pack grande, que necesita ~205 incluso con R1). Los trabajos de fondo
  (planificador, worker) necesitan su propio presupuesto para que un usuario no los deje sin
  cupo, ni al revés.
- **Decisiones abiertas:** los números (propuesta: búsqueda 30/min, selector 60/min,
  resolver/build 6/min, refresco manual 1 por pack por minuto, por usuario); si los operadores
  tienen límites más altos; si el límite es por usuario, por pack o ambos; presupuesto global de
  Modrinth (¿240/min?).

### R3. Arreglar la frescura de `fill_catalog` (el diseño del documento de investigación)
- **Qué:** `fetched_at` + TTL en vez de "sin icono ⇒ pedir siempre"; una caché negativa para los
  ids omitidos de una respuesta exitosa; trozos de 100 ids; dentro de un pack, el refresco del
  pack reemplaza al TTL.
- **Dónde:** services/modrinth.py:19-37; modelo `ModrinthProject`.
- **Ahorro:** 1 request por render de cada página con un proyecto sin icono o desconocido
  (página del modpack, página de mod, detalle de servidor, actualizaciones, comparar,
  reemplazos). Con un usuario que recarga el pack ~30 veces en una sesión: ~30 → 0-1.
- **Riesgos:** un proyecto aprobado en Modrinth aparece hasta el TTL más tarde (el refresco
  manual lo cubre). Marcar como desconocido solo tras un 200, nunca tras un timeout.
- **Decisiones abiertas:** valores de TTL (la investigación propuso 24 h, 7 d para iconos; con la
  regla de 5 min por pack, fuera de los packs quizá 24 h).

### R4. Selector de versiones desde el catálogo
- **Qué:** `project_versions` (modpack.py:528) lee MongoDB cuando la marca (proyecto, objetivo)
  de R1 está fresca; si no, pide y escribe la marca.
- **Ahorro:** 1 por tarjeta abierta → 0 tras cualquier resolución dentro de la ventana.
- **Riesgo:** la misma antigüedad que R1. **Decisión:** ninguna aparte de la ventana.

### R5. Caché de búsqueda y tecleo más liviano
- **Qué:** cachear las respuestas de `/search` en MongoDB (índice TTL) con clave consulta
  normalizada + versión + loader + límite; mínimo 2-3 caracteres; debounce de 400-500 ms;
  opcionalmente reutilizar los resultados para llenar el catálogo cuando se agrega uno (el
  resultado ya trae título, icono y tipo).
- **Dónde:** services/modrinth.py:51-65; detail.html:22.
- **Ahorro:** ~30-50% de las búsquedas (SUPUESTO: retroceder y consultas repetidas), más el 1
  `project_get_many` al "Agregar" si el agregado usa los datos del resultado.
- **Decisiones abiertas:** TTL (¿10 min? ¿1 h?), largo mínimo, debounce.

### R6. Changelogs en lote
- **Qué:** `_preview` pide `GET /versions?ids=[…]` para todas las versiones que llegan en una
  sola request (trozos de 100) en vez de N × `GET /version/{id}`.
- **Ahorro:** N → ⌈N/100⌉ la primera vez que se muestra una vista previa (p. ej. 40 mods
  actualizados: 40 → 1).
- **Riesgo:** SUPUESTO a verificar contra openapi v2.7.0: que `GET /versions` devuelve
  `changelog` (el esquema `Version` lo tiene; `include_changelog` está documentado solo para
  `/project/{id}/version`). Registrar la respuesta en docs/references.md.

### R7. Importar sin re-descargar y con búsquedas en lote
- **Qué:** leer los metadatos del jar durante el escaneo (los bytes ya están en memoria en
  `_hash`, pterodactyl.py:169) y guardar `mod_id`/`name` en el `ScanFile`; al importar, un
  `project_get_many` para todos los mod ids en vez de N `project_get`; la búsqueda sigue siendo
  por jar (Modrinth no tiene búsqueda en lote), limitada y solo para los que la consulta por slug
  no encontró.
- **Ahorro:** por jar desconocido 4 → ~1 (+1 lote en total). 20 jars desconocidos: 80 → ~21.
- **Riesgo:** el escaneo guarda un poco más de datos; `jarmeta` lee datos no confiables (ya es la
  regla).

### R8. Piso del chequeo de actualizaciones y reutilización resolver/build
- **Qué:** el chequeo manual de actualizaciones se rehúsa (muestra el último resultado) si el
  último chequeo tiene menos de 5 min; `build_create` reutiliza una resolución todavía fresca
  (mismo `request_digest`, dentro de la ventana) en vez de resolver dos veces cuando se hace
  clic en "Resolver" y luego en "Crear build".
- **Ahorro:** 1-3 por chequeo repetido; con R1 la segunda resolución ya es ~0.

### R9. Las pequeñas
- Cachear la versión del loader por (loader, versión de juego) durante p. ej. 1 h (1 por build →
  0).
- Cachear la lista de servidores del panel `/servers` 1-5 min (1 por carga → 0), con refresco
  manual.
- No descargar en una prueba en seco, o conservar los jars verificados por sha512 para la
  ejecución real (C: 1 por jar ahorrada por prueba en seco). Decisión del dueño: hoy la prueba
  en seco verifica las descargas a propósito.
- Trocear `project_get_many` / `version_get_many` / `files_hash_post_many` en ~100 ids (el largo
  de URL no está verificado, investigación §1).
- Quitar, o dejar sin ruta, los métodos de Modrinth sin uso (`version_dependencies`,
  `project_version_update_*`, `project_create`, `project_update`, `game_versions`,
  `files_hash_get`, `files_update_hash_post`) para que el código nuevo no elija los de a un
  elemento.
- Ejecutar las llamadas `project_versions` de resolver con concurrencia acotada solo después de
  R1/R2 (hoy son secuenciales, lo que es lento pero va a un ritmo suave).

---

## 7. Decisiones abiertas para el dueño (todas)

1. **Ventana:** ¿5 minutos por pack, como se dijo? ¿Empieza en el último refresco o en la última
   resolución?
2. **Resolver con versiones en caché:** ¿pueden "Resolver" y cada clic de decisión usar versiones
   de hasta 5 minutos de antigüedad (recomendado: sí)? ¿Puede **"Crear build"** (recomendado: no;
   hace el chequeo de cambios barato, ~2-3 requests)?
3. **Detector de cambios:** `updated` + diferencia de ids de versión desde `GET /projects`
   (recomendado), o `POST /version_files/update` sobre los hashes del último lock?
4. **Botón de refresco manual:** qué refresca: (a) los metadatos de proyecto del pack, (b) las
   listas de versiones de todos sus proyectos y dependencias para su objetivo, (c) también un
   chequeo de actualizaciones? ¿Su enfriamiento (propuesta: 1 por pack por minuto)? ¿Quién puede
   pulsarlo (editor u operador)?
5. **Límites por usuario:** números por acción (propuesta en R2), operadores más altos o no, el
   mensaje que se muestra, y si los trabajos de fondo tienen un presupuesto aparte.
6. **Presupuesto global de Modrinth:** p. ej. 240/min, y qué pasa cuando se agota (esperar, o
   rechazar con "intenta en N s").
7. **TTL de metadatos fuera de un pack:** ¿24 h (7 d para iconos)? ¿Caché negativa para ids
   desconocidos: 24 h?
8. **Búsqueda:** TTL de la caché, caracteres mínimos, debounce.
9. **Descargas en prueba en seco:** ¿seguir verificando cada jar (2× descargas del CDN), o
   confiar en el sha512 del build hasta la ejecución real?
10. **Lecturas de Pterodactyl:** ¿cachear la lista de servidores del panel (y por cuánto)?

## 8. No verificado (SUPUESTOS)
- ~50 dependencias transitivas y 3 rondas para un pack de 150 mods; 0-10 proyectos con una
  versión nueva por día.
- Que `GET /versions?ids=` devuelve `changelog`.
- Que `ModrinthProject.updated` se mueve solo con una versión nueva, y que las ediciones de
  versión in situ (versiones de juego, estado) son raras.
- Que las descargas del CDN no cuentan contra los 300/min de la API.
- El límite real de Modrinth sobre el largo de `ids` (investigación: 1000 ids repetidos
  respondieron 200).
- La latencia real por request (hallazgo: resolver es secuencial, ~200 idas y vueltas).
- No se observó ninguna request: los conteos se leen de las rutas del código, no se midieron. Un
  contador por cliente (log o métrica de requests por acción) los confirmaría.
