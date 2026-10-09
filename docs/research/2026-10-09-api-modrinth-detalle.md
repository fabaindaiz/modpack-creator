# API de Modrinth y metas de loaders: lo que dicen las fuentes primarias, para gastar menos requests

Fecha: 2026-10-09. Solo lectura sobre el repositorio (rama `fix/tester-feedback`, HEAD d8ae966).
Contexto: `docs/superpowers/specs/2026-10-09-economia-de-requests-design.md`,
`docs/research/2026-10-09-auditoria-requests.md` (§8 deja abiertos varios SUPUESTOS que esto cierra).

## Fuentes primarias

- **OpenAPI publicada**: https://docs.modrinth.com/openapi.yaml, `info.version: v2.7.0/366f528`
  (bajada hoy).
- **Código del servidor (labrinth)**, fijado al commit `3323d28377a07dd256033ce1730df88318f3a5bd`
  de `modrinth/code` (main, 2026-10-09T15:55Z). Enlaces con la forma
  `https://github.com/modrinth/code/blob/3323d28/apps/labrinth/src/<ruta>`:
  `util/ratelimit.rs`, `lib.rs`, `main.rs`, `routes/v2/versions.rs`, `routes/v3/versions.rs`,
  `routes/v2/version_file.rs`, `routes/v3/version_file.rs`, `database/models/project_item.rs`,
  `models/v3/projects.rs`.
- **Sondas** (hoy, 17:06–17:07 UTC, sin token, User-Agent
  `fabaindaiz/modpack-creator/research-probe (...)`): 7 a `api.modrinth.com/v2`, 1 HEAD a
  `cdn.modrinth.com`, 1 a `meta.fabricmc.net`, 1 a `meta.quiltmc.org`. Más `gh api` a las releases
  de `FabricMC/fabric-loader` y `QuiltMC/quilt-loader`.

| # | Sonda | Resultado |
|---|---|---|
| S1 | `GET /v2/projects?ids=["AANobbMI"]` (Sodium) | 200; 262 ids en `versions`; `updated` = `2026-09-20T21:27:09.277251Z`; sin `cache-control`; `cf-cache-status: MISS`; `x-ratelimit-limit: 300`, `remaining: 299`, `reset: 0` |
| S2 | `GET /v2/versions?ids=[3 ids]` | 200; **cada versión trae `changelog`** (365–389 caracteres); el `date_published` de la última (`NM9w06Kr`) es **idéntico** al `updated` de S1; sin `cache-control`; `MISS` |
| S3 | `POST /v2/version_files/update_individual`, 2 hashes con filtros distintos | 200; mapa hash → versión. Hash fabric con `version_types:["release"]` → `0.9.2` release (más **vieja** que la alpha `0.9.3-alpha.1` del hash); hash neoforge con `[release,beta,alpha]` → la alpha misma. Las dos traen `changelog`. `cf-cache-status: DYNAMIC` |
| S4 | `GET /v2/search?query=sodium&facets=…&limit=2` | 200; campos del hit abajo (P5); sin `cache-control`; `DYNAMIC` |
| S5 | `HEAD https://cdn.modrinth.com/data/AANobbMI/versions/v4PSXean/…jar` | 200; `via: 1.1 varnish`, `x-served-by: cache-scl…`, `x-cache: HIT`, `etag`, `age: 6618`; **sin cabeceras `x-ratelimit-*`** |
| S6 | `GET /v2/versions?ids=[2000 ids]&include_changelog=false` (262 reales de Sodium + 1738 inventados), URL codificada de **34 003 bytes** | 200 en 0,49 s; 256 versiones; **sin la clave `changelog`**; los inventados se omiten sin error |
| S7 | `GET /v2/version/lzNrocQS` (uno de los 6 ids reales que S6 no devolvió) | **404** |

Las cabeceras de tasa nunca bajaron de 299–300 en estas sondas: con el algoritmo del servidor (P3)
el balde se rellena a 5 por segundo, y las sondas iban separadas por segundos.

---

## P1. ¿`GET /v2/versions?ids=` devuelve `changelog`? ¿Cuántos ids caben?

**Hecho.** Sí, por defecto. La OpenAPI v2.7.0 solo dice que `/versions` devuelve `Version[]`
(cuyo esquema tiene `changelog`) y documenta `include_changelog` únicamente para
`/project/{id}/version`. El código del servidor es más preciso:
`routes/v2/versions.rs` define `VersionIds { ids: String, #[serde(default = "default_true")]
include_changelog: bool }` y lo pasa a `v3::versions::versions_get`, que hace
`if !ids.include_changelog { version.changelog = None }` (`routes/v3/versions.rs`, l. 181–258).
Es decir: **con changelog por defecto, y `include_changelog=false` también funciona en `/versions`**
(no documentado en la OpenAPI). S2 lo confirmó en vivo (changelog presente) y S6 (con `false`, la
clave ni aparece).

**Cuántos ids.** Ningún handler limita la cantidad (`versions_get` deserializa el array y llama a
`DBVersion::get_many`, sin tope; tampoco `projects_get`). El límite práctico es el largo de URL:
S6 mandó 2000 ids en 34 KB y respondió 200 en medio segundo. Con los trozos de 100 que el código ya
usa (`ID_CHUNK = 100`, `src/services/modpack.py:42`), la URL ocupa ~1,7 KB codificada: holgura de
20×. SUPUESTO: el tope real de URL del borde de Cloudflare no está documentado por Modrinth; 34 KB
pasaron, más no se probó.

**Hallazgo lateral (S6+S7).** La lista `versions` de un proyecto puede nombrar ids que
`/versions` no devuelve y que `/version/{id}` da 404 (6 de 262 en Sodium). Causa no verificada
(SUPUESTO: versiones ocultas a anónimos, p. ej. retenidas por atribución —
`update_files_internal` filtra `project_attribution_groups` —, o la lista de proyecto cacheada en
Redis tras un borrado). Para la app: un id ausente en la respuesta de un lote **no es un error**;
`_check` ya lo tolera (lo guarda en `version_ids` y no lo vuelve a pedir), y el camino de
`needs_versions` lo marca `gone` (`modpack.py:955-972`). Conviene un test con un id listado que
el lote no devuelve.

**Lo que pasa hoy** (`src/services/modpack.py`):
- `_preview` (l. ~1160–1200) lanza una tarea por versión que llega, con `CHANGELOG_CONCURRENCY = 8`
  y `CHANGELOG_DEADLINE = 10 s`; cada una llama a `_changelog` (l. 1202–1211), que lee el catálogo
  y, si `changelog is None`, hace **`GET /version/{id}`** (`version_get`) y guarda la versión con
  `changelog or ""`.
- Ya hay versiones que entran al catálogo **con** changelog sin que nadie lo note: `_check`
  (l. 1009) y el camino de `needs_versions` (l. 965) usan `version_get_many` → `/versions?ids=`
  sin `include_changelog`, o sea con changelog, y `version_upsert_many` lo conserva
  (`keep={"changelog"}`, `src/database/modrinth.py:236`). Las que vienen de `project_versions`
  (`include_changelog=false`, la carga en frío) llegan sin él.
- `UpdateCheckService.compute` (`src/services/update_check.py`) recibe de
  `version_files/update` la versión nueva **completa, con changelog** (S3), y la descarta: solo
  guarda número e id en `AvailableUpdate`.

**Diseño: changelogs en lote.**
1. `ModrinthAPI.version_get_many(ids, include_changelog: bool = True)`; la query manda
   `include_changelog` explícito. Contrato en `tests/test_api_contracts.py` y una línea en
   `docs/references.md` (fuente: código de labrinth, la OpenAPI no lo dice).
2. `_preview`: `faltan = [v for v in arriving if catálogo sin changelog]`; **una** tarea que pide
   `version_get_many(trozo)` por cada trozo de 100, con el mismo plazo de 10 s; normaliza
   `changelog None → ""` antes de `version_upsert_many` (si no, una versión sin texto se vuelve a
   pedir siempre). Un id pedido y no devuelto va a `failed`; un trozo que no llegó a tiempo, todos
   sus ids a `timed_out`. `CHANGELOG_CONCURRENCY` desaparece.
3. `compute` del chequeo diario: `version_upsert_many(found.values())` (con `changelog or ""`).
   Así la página de actualizaciones (`update_preview`) no hace **ninguna** request.

**Cuentas, vista previa de 30 mods cambiados, la primera vez:**

| Camino | Hoy | Con el lote | Con el lote + guardar lo del chequeo |
|---|---|---|---|
| Página de actualizaciones (`update_preview`) | hasta 30 × `GET /version/{id}` | 1 | **0** |
| Comparar builds / vista previa de servidor | hasta 30 | 1 (⌈30/100⌉) | 1 (0 si el catálogo ya los trajo con `_check`) |
| Con 150 cambiados (tras meses sin actualizar) | 150, y a 8 en paralelo el plazo de 10 s corta | 2 | 0–2 |

Además desaparece el riesgo de que 30 tareas en paralelo agoten el cupo del usuario (E4) a mitad
de la página.

## P2. `POST /v2/version_files/update`: ¿`version_types` por hash o uno para toda la llamada?

**Hecho.** Uno para toda la llamada. OpenAPI v2.7.0, `GetLatestVersionsFromHashesBody`: `hashes`,
`algorithm`, y `loaders`, `game_versions`, `version_types` como arrays a nivel del cuerpo. El
servidor (`routes/v2/version_file.rs` `ManyUpdateData`, `routes/v3/version_file.rs`
`update_files_internal`) aplica `version_types` a todos los proyectos en una sola consulta SQL.

**Pero existe una variante por hash**, no publicada en la OpenAPI v2.7.0: `POST
/v2/version_files/update_individual` (`#[post("/update_individual")]` en el scope
`/version_files`, `operation_id = "getLatestVersionsFromHashesIndividual"`, cuerpo
`ManyFileUpdateData { algorithm, hashes: [{hash, loaders, game_versions, version_types}] }`,
`routes/v2/version_file.rs` l. 519–606). S3 la probó: dos hashes con filtros distintos en una
llamada, cada uno respondió según los suyos.

**Cómo elige Modrinth "la más nueva"** (`update_files_internal`, SQL): de las versiones del
proyecto que cumplen los filtros, `ORDER BY v.date_published DESC LIMIT 1`, entre las de estado
`listed`, `archived` **y `unlisted`** (todo lo que no es `is_hidden`). Tres consecuencias:
- Devuelve la más nueva **por fecha**, aunque sea más vieja que la instalada (S3: con
  `["release"]`, una alpha `0.9.3-alpha.1` recibe la release `0.9.2`). Hoy `compute` anuncia
  cualquier `version_id` distinto: un mod con excepción a beta cuya release es más vieja aparece
  como "actualización" hacia atrás.
- Con canales `[release, beta]` devuelve la beta si es más nueva; el resolvedor, en cambio,
  prefiere el canal más estable y luego la fecha (`_preference`, `src/library/resolution.py:221`).
  El aviso puede anunciar una versión que el build no tomaría.
- Puede devolver una versión `unlisted`, que la lista `versions` del proyecto no incluye (P4): el
  catálogo nunca la usa, pero el aviso la anuncia.
- Un hash es una clave del mapa: una llamada da **una** respuesta por hash. "La beta más nueva" y
  "¿cabe ya una release?" para el mismo archivo son dos preguntas, dos llamadas.

**Lo que pasa hoy** (`compute`): una llamada por grupo de loaders (mods, shaders, resource packs:
K = 1–3), siempre con `bundle.channels`. No conoce `channel_exceptions`
(`src/model/modpack/project.py:85`): para un mod con excepción, mientras no haya release no
devuelve nada (la beta nueva nunca se anuncia), y cuando aparece una release la anuncia como una
actualización cualquiera (sin decir que anula la excepción, y quizá hacia atrás).

**Diseño: avisar la beta nueva y la release que anula la excepción.**

Opción A (recomendada, 1–2 llamadas en total): `update_individual`.
- Llamada 1, todos los hashes de todos los grupos: cada hash con sus loaders y `[bundle.version]`;
  `version_types` = `bundle.channels` para un mod normal, `bundle.channels + canales hasta la
  excepción` para uno con excepción (beta → `[release, beta]`; alpha → `[release, beta, alpha]`).
- Llamada 2, solo si hay mods con excepción: esos hashes con `bundle.channels` solamente.
- Lectura: si la llamada 2 devuelve algo → "cabe la estable X: la excepción de M queda sin efecto
  y el próximo build vuelve a release" (sin importar si su número es menor). Si no, y la llamada 1
  devuelve una versión de otro id con `date_published` posterior a la del lock → "beta nueva".
- Para todos: descartar respuestas con `date_published` anterior a la versión del lock (la fecha
  está en el catálogo por `version_id`; SUPUESTO: está para toda versión de un lock, porque el
  build la resolvió desde el catálogo), y guardar las versiones devueltas (P1).
- Costo hoy → después: K (1–3) llamadas y la beta no se anuncia → **1, o 2 con excepciones**, y se
  anuncian las dos cosas.
- Riesgo: endpoint fuera de la OpenAPI publicada; la invariante "contrasta cada llamada con su
  especificación" (AGENTS.md) pide una fuente: aquí es el código de labrinth fijado a un commit
  más la sonda S3. Decisión del dueño (D1).

Opción B (solo endpoints documentados, K + Kₑ llamadas): las K de hoy con `bundle.channels`
(dan la release que anula) más una por cada grupo que tenga mods con excepción, solo con esos
hashes y los canales ampliados (dan la beta nueva). Típico: 1 + 1 = 2; peor caso 3 + 3 = 6.

Opción C (1 llamada + catálogo): la llamada 1 de A, y "¿cabe ya una release?" se contesta desde el
catálogo de MongoDB (versiones guardadas del proyecto para el objetivo). 0 requests extra, pero el
chequeo diario no refresca el catálogo: la respuesta puede tener días (SUPUESTO sobre el uso real).

## P3. Límite de tasa: ¿300/min por IP? ¿Cuenta el CDN? ¿Se pueden leer las cabeceras?

**Hecho, documentado.** OpenAPI v2.7.0, sección "Ratelimits": "The API has a ratelimit defined per
IP. Limits and remaining amounts are given in the response headers. `X-Ratelimit-Limit`: the
maximum number of requests that can be made in a minute; `X-Ratelimit-Remaining`: the number of
requests remaining in the current ratelimit window; `X-Ratelimit-Reset`: the time in seconds until
the ratelimit window resets. Ratelimits are the same no matter whether you use a token or not. The
ratelimit is currently 300 requests per minute."

**Hecho, en el código** (`util/ratelimit.rs`, `lib.rs` l. 131–134): no es una ventana de un
minuto sino **GCRA** con `GCRAParameters::new(300, 300)`: intervalo de emisión 200 ms, ráfaga de
300. En la práctica, un balde de 300 que se rellena a **5 por segundo**; la clave es la IP
(`CF-Connecting-IP` detrás de Cloudflare). `remaining` = fichas que quedan; `reset` = segundos
hasta que el balde esté **lleno** de nuevo (no hasta poder mandar una). Al rechazar, responde 429
con `x-ratelimit-limit/remaining(0)/reset` y un cuerpo "rate limit exceeded; retry after N
milliseconds" (N = hasta la próxima ficha, ~200 ms), **sin cabecera `Retry-After`**. Las sondas
S1–S6 mostraron `remaining` 299–300 y `reset` 0.

**El CDN no cuenta.** El limitador es un middleware de labrinth (`main.rs` l. 288,
`.wrap(from_fn(rate_limit_middleware))`); `cdn.modrinth.com` lo sirve otro sistema (S5: Varnish de
Fastly, `x-cache: HIT`, sin ninguna cabecera `x-ratelimit-*`). Una respuesta que Cloudflare sirviera
desde su caché tampoco llegaría a labrinth; en las sondas la API respondió siempre `MISS` o
`DYNAMIC`. SUPUESTO: el CDN puede tener límites propios no documentados.

**Lo que pasa hoy.** `RateLimiter` (`src/library/api/limiter.py`): ventana deslizante de 60 s con
tope 240 y 60 por usuario. Ante un 429, `src/library/api/utils.py:78-81` lee `Retry-After`, que
**Modrinth no manda**, y cae al valor por defecto: **pausa a todos 60 s**. Con GCRA, a los 200 ms ya
hay una ficha, y a los 10 s hay 50.

**Cambio sugerido.**
1. Leer `X-Ratelimit-Remaining` de cada respuesta de Modrinth y guardarlo en el limitador
   (`limiter.observe(remaining, at)`). Si `remaining` < un piso (p. ej. 30), la próxima request
   espera `(piso − remaining) × 0,2 s`. Así la app se adapta también a **otros consumidores de la
   misma IP** (SUPUESTO a confirmar con el dueño: si los servidores de juego o la Modrinth App
   salen por la IP del panel, hoy la app no los ve y su 240 no protege).
2. Ante un 429 sin `Retry-After`: pausar ~1–2 s (o leer los ms del cuerpo), no 60 s.
3. Mantener el tope fijo como techo (240 o 300): las cabeceras solo frenan, nunca permiten más.
   Ahorro: no de requests, sino de esperas: hoy un 429 congela todo un minuto.

## P4. `GET /v2/projects?ids=`: tope de ids; ¿`versions` incluye unlisted/archivadas?; ¿`updated` se mueve?

**Tope de ids.** Ni la OpenAPI (`MultipleProjectIdentifier`: "The IDs and/or slugs of the
projects", sin máximo) ni el handler ponen tope; la investigación del 2026-10-05 mandó 1000 ids
(~20 KB) con 200, y S6 mandó 2000 a `/versions` (34 KB) con 200. Trozos de 100: holgados.

**`versions`** (`database/models/project_item.rs` l. 651–679): `INNER JOIN versions v ON … AND
v.status = ANY($3)` con los estados `is_listed()`, que en `models/v3/projects.rs` l. 961–963 son
**`Listed | Archived`**. Incluye archivadas; **excluye** `unlisted`, `draft` y `scheduled`.

**`updated`** (l. 963): `versions.iter().map(|x| x.1).next_back().unwrap_or(m.published)`, la
`date_published` más reciente de esas mismas versiones (S1 vs S2: idénticas al microsegundo).
Se mueve al publicar una versión listada, o al borrar/ocultar la más reciente; **no** al borrar
una más vieja, y **nunca** al editar una versión en su sitio. `EditVersion`
(`routes/v3/versions.rs` l. 367–403) permite cambiar `version_type`, `loaders`, `dependencies`,
`fields` (versiones de juego), `featured`, `status`, `changelog`, `file_types`, `ordering`, sin
tocar `date_published`.

**¿Basta con comparar listas en `_check`?** Para altas, bajas y cambios listed↔unlisted, sí, y es
**estrictamente mejor** que `updated` (que se deriva de la misma lista y pierde las bajas no
recientes), al mismo precio: una request por 100 proyectos. No hay señal más barata: sin `ETag`
(investigación 2026-10-05), `last-modified` es la hora de la respuesta (S1), y el
`date_modified` de la búsqueda es la misma fecha. Lo que **no** detecta ninguna señal de proyecto:
una versión editada en su sitio (un autor marca su versión de 1.21.1 también para 1.21.2, o la pasa
de beta a release). Para eso solo sirve releer las versiones: `version_get_many(ids,
include_changelog=false)`, 1 request por 100 versiones. Decisión del dueño (D3).

## P5. ¿La búsqueda alcanza para pintar una tarjeta? ¿Cache-control?

**Hecho.** OpenAPI `ProjectResult` (requeridos): `project_id`, `project_type`, `all_project_types`,
`title`, `description`, `author`, `categories` (los loaders van mezclados aquí),
`display_categories`, `versions` (versiones de **Minecraft**, no ids), `downloads`, `follows`,
`icon_url`, `date_created`, `date_modified`, `latest_version`, `license`, `environment`,
`disclosure_types`, `gallery`, `client_side`, `server_side`; además `slug`, `author_id`,
`organization`, `color`. S4 trajo todos. Alcanza para una tarjeta (título, icono, descripción,
autor, descargas, lado). **No** trae la lista de ids de versión ni el cuerpo: no sirve para llenar
el catálogo de versiones. La app ya pinta desde el hit sin pedir el proyecto
(`src/routers/web/modpacks.py:445-447`) y guarda la búsqueda 10 minutos
(`src/services/modrinth.py:90-98`): nada que cambiar.

**Cache-control.** La API no manda `cache-control` ni `ETag` (S1, S2, S4: `MISS`/`DYNAMIC`). La
investigación del 2026-10-05 vio `public, max-age=2678400` en una respuesta; hoy no aparece en
ninguna: no hay nada estable que honrar, y la app debe seguir con sus propias ventanas (5 min por
pack, 10 min la búsqueda, 24 h fuera del pack). La documentación no da guía de caché.

## P6. Metas de Fabric y Quilt

**Hechos.**
- `meta.fabricmc.net/v2/versions/loader/26.3`: `cache-control: public, max-age=1800` (30 min),
  `cf-cache-status: HIT`, `age: 46`. 253 entradas, la primera estable `0.19.5`.
- `meta.quiltmc.org/v3/versions/loader/26.3`: **724 117 bytes**, `cache-control: no-store`, pero con
  `etag` y `last-modified: Sun, 20 Sep 2026` (`age` de 19 días en Cloudflare). SUPUESTO: un
  `If-None-Match` daría 304 (no se probó).
- Frecuencia: `FabricMC/fabric-loader` publicó 17 versiones en los últimos 12 meses (≈1,4 al mes,
  a veces dos en dos días: 0.19.4 el 2026-08-26 y 0.19.5 el 2026-08-28). `QuiltMC/quilt-loader` no
  publica releases en GitHub; su meta cambió por última vez el 2026-09-20.

**Lo que pasa hoy.** `LoaderMetaAPI.loader_version` (`src/library/api/client/loaders.py`) se llama
una vez por build (`src/services/modpack.py:1092`, si el pack no fija `loader_version`), sin caché.

**Cambio sugerido.** Guardar en MongoDB `(loader, game_version) → versión, fetched_at` y reusarla
**24 h** (Fabric publica ~1 vez al mes; un día de atraso en el loader estable no rompe nada, y el
build congela lo que eligió). Ahorro: 1 request por build → 1 por día y objetivo (y 724 KB menos
por build de Quilt). Respetar el `max-age=1800` de Fabric sería el mínimo; 24 h es una decisión
(D4).

---

## Decisiones abiertas para el dueño

**D1. Chequeo diario y excepciones de canal: ¿qué endpoint?**
1. *(recomendada)* `update_individual`: 1 llamada (2 con excepciones) para todo el pack; anuncia
   "beta nueva de M" y "cabe la estable de M: su excepción queda sin efecto". Costo: un endpoint que
   la OpenAPI no publica (fuente: código de labrinth + sonda); test de contrato propio.
2. Solo documentados: K + Kₑ llamadas (2 típico, 6 peor caso), mismo resultado.
3. Una llamada + catálogo para "¿cabe la estable?": 0 extra, pero puede estar desactualizado días.
4. No anunciar betas: dejar como hoy y solo marcar la release que anula la excepción. 0 cambios en
   requests.

**D2. Qué versión anuncia el chequeo.** Hoy anuncia cualquier id distinto; Modrinth elige por fecha.
1. *(recomendada)* Anunciar solo si es posterior a la del lock **y** la tomaría el resolvedor
   (canal más estable primero, no `unlisted`); excepción: la release que anula una excepción se
   anuncia aunque sea más vieja, con ese texto. Costo: leer fechas del catálogo; 0 requests.
2. Solo filtrar las más viejas (no las `unlisted` ni el orden por canal). Más simple; quedan avisos
   que el build no sigue.
3. Dejar como hoy.

**D3. Ediciones de versión en su sitio** (versiones de juego, canal, dependencias).
1. *(recomendada)* Al crear un build, releer en lote las versiones que el lock va a usar
   (`include_changelog=false`): 1 request por 100 entradas, ~2 para un pack de 150.
2. Además, una vez al día releer todas las versiones guardadas de los proyectos del pack: ~30
   requests por pack grande y día.
3. No hacerlo: la lista de ids basta para altas y bajas; una edición en su sitio se ve recién al
   "Actualizar desde Modrinth" (que hoy tampoco la ve).

**D4. Caché de la versión del loader.**
1. *(recomendada)* 24 h por (loader, versión de MC), en MongoDB.
2. 30 min (el `max-age` de Fabric): casi siempre 1 request por build igual.
3. 7 días con botón para forzar.

**D5. El limitador y las cabeceras de Modrinth.**
1. *(recomendada)* Leer `X-Ratelimit-Remaining` para frenar bajo un piso de 30, y ante un 429 sin
   `Retry-After` pausar 2 s en vez de 60. El tope de 240 sigue como techo.
2. Solo arreglar la pausa del 429 (2 s); sin leer cabeceras.
3. Subir el techo a 300 y confiar en las cabeceras (más rápido, pero sin margen si la IP se
   comparte).

**D6. Changelogs en lote** (si el dueño quiere decidirlo y no dejarlo como reversible):
1. *(recomendada)* Lote de `/versions?ids=` + guardar lo que trae el chequeo diario: 30 → 0–1.
2. Solo el lote: 30 → 1.

## No verificado (SUPUESTOS)
- El tope real de URL de Cloudflare para la API (34 KB pasaron).
- Por qué 6 de 262 ids de la lista de Sodium dan 404 (S7).
- Que el CDN no tenga un límite propio; que la IP del panel se comparta con otros consumidores.
- Que `If-None-Match` contra la meta de Quilt devuelva 304.
- Que toda versión de un lock tenga su `date_published` en el catálogo (D2, A).
- El conteo de llamadas "hoy" sale de leer el código, no de medirlo.
