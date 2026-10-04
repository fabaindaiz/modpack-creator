# Información externa que vale la pena saber

No es una lista de enlaces: cada entrada dice qué aporta **a este proyecto**, y cuando contradice
algo ya decidido, lo dice. Una fuente entra cuando cambió o confirmó una decisión, no por estar
bien escrita. Todas se leyeron en la fuente primaria el 2026-10-01.

## Las APIs externas

- **[Modrinth API v2 — OpenAPI](https://docs.modrinth.com/openapi.yaml)** (v2.7.0) — los lotes por
  hash son `POST /version_files` y `POST /version_files/update` (plural); el cuerpo lleva `hashes`
  como array JSON de strings y `algorithm` (`sha1` o `sha512`); el de update suma `loaders` y
  `game_versions` como arrays obligatorios, y `version_types` como array **opcional** de
  `release`, `beta` o `alpha` (`GetLatestVersionsFromHashesBody`). `/version_file/{hash}`
  (singular) es para un solo hash. En `GET /project/{id}/version`, `loaders` y `game_versions` van
  en la query como **strings con un array JSON** (`'["fabric"]'`), e `include_changelog` (por
  defecto `true`; la spec recomienda `false`) decide si cada versión trae `changelog`.
  `GET /version/{id}` devuelve `Version`, con `changelog` (string, `nullable`) siempre presente.
  Releído en la spec v2.7.0 el 2026-10-04.

  **Lo que confirma:** `project_versions` en `src/library/api/client/modrinth.py`, que pide
  `include_changelog=false`: una versión del listado llega **sin** `changelog`, y la vista previa
  de cambios lo pide con `GET /version/{id}` (`version_get`). `files_update_hash_post_many` manda
  `version_types` solo si hay canales (los del modpack).

  **Lo que confirma:** los tres POST por hash de `src/library/api/client/modrinth.py`, con
  `tests/test_api_contracts.py` (S6). Las respuestas de los lotes son un mapa hash → versión
  (`HashVersionMap`), no una lista.

- **Modrinth en vivo, grabado el 2026-10-01** (`tests/fixtures/modrinth/`). `environment` es una
  lista en el proyecto (`["client_only"]`) y un string en la versión. En el proyecto, la spec
  marca `client_side`/`server_side` como deprecados pero todavía obligatorios. Un shader trae
  `environment: []` y loaders `iris`/`optifine`. `/tag/game_version` lista de la más nueva a la
  más vieja: `26.4-snapshot-2`, …, `26.3`, `1.21.11`.

  **Lo que confirma:** `src/model/modrinth/` y `newest_first`, con `tests/test_modrinth_catalog.py`.

- **Metas de Fabric y Quilt, en vivo el 2026-10-01.** `meta.fabricmc.net/v2/versions/loader/26.3`
  lista de la más nueva a la más vieja con `loader.stable` (`0.19.5` estable).
  `meta.quiltmc.org/v3/versions/loader/26.3` viene desordenada y con betas mezcladas
  (`0.20.0-beta.9`, …, `0.24.0`).

  **Lo que confirma:** `fabric_loader_version` y `quilt_loader_version` en `src/library/mrpack.py`.

- **`.mrpack` contra descargas reales (2026-10-01):** cada archivo de un build exportado, bajado
  de su URL de `cdn.modrinth.com`, coincidió en sha1, sha512 y tamaño con el índice. packwiz mapea
  `environment` a lados en `getSide` (`modrinth/modrinth.go`). Allí
  `server_only_client_optional` cae en un `case` vacío y queda sin lado, aparentemente un
  descuido; aquí cada valor tiene su mapeo (d-7985ee-53a818).

- **[Discord — Execute Webhook](https://docs.discord.com/developers/resources/webhook)**,
  **[rate limits](https://docs.discord.com/developers/topics/rate-limits)** y
  **[allowed mentions](https://docs.discord.com/developers/resources/message)**: POST a la URL del
  webhook, que responde 204 sin `wait`; `content` de hasta 2000 caracteres; un 429 trae
  `Retry-After` en segundos (y `retry_after` en el cuerpo); `allowed_mentions: {"parse": []}` evita
  toda mención, incluido `@everyone`.

  **Lo que confirma:** `src/library/api/client/discord.py` y `src/services/notify.py`.

- **Pterodactyl para operar (código del panel, 2026-10-01).**
  - **Backups:** `BackupController` crea con `{name, is_locked}` y **no reemplaza** un backup
    viejo cuando el cupo está lleno (`InitiateBackupService` lanza `TooManyBackupsException`),
    además de limitar la frecuencia con 429. `index` trae `meta.backup_count` (los no fallidos)
    contra `feature_limits.backups` del servidor. `restore` exige el servidor sin estado y el
    backup exitoso, y responde 204.
  - **Archivos:** `FileController::rename` (PUT, `{root, files: [{from, to}]}`) y `create`
    (POST, `{root, name}`) responden 204.
  - **Estado:** `ResourceUtilizationController` cachea 20 s y da `current_state` y
    `resources.uptime`.

  **Lo que confirma:** `src/library/api/client/pterodactyl.py`, con `tests/test_api_contracts.py`
  y `tests/test_update_http.py`.

  **Lo que hacemos distinto, a propósito:** `FormData(quote_fields=False)` al subir a Wings. Por
  defecto aiohttp codifica en porcentaje el nombre del archivo, y Wings lo guardaría así.

- **[Pterodactyl panel — rutas del Client API](https://github.com/pterodactyl/panel/blob/develop/routes/api-client.php)**
  y sus requests (`SendCommandRequest`, `DeleteFileRequest`) — enviar un comando es
  `POST /api/client/servers/{server}/command` con `{"command": "..."}`; borrar archivos es
  `POST .../files/delete` con cuerpo JSON `{"root": ..., "files": [...]}` y responde 204.

  Subir y bajar son dos pasos (`FileUploadController`, `FileController::download`): un GET al panel
  devuelve `{"object": "signed_url", "attributes": {"url": ...}}`, una URL de Wings con un JWT que
  vence en 15 minutos. Wings recibe la subida como multipart, campo `files`, con `directory` en la
  query (`router_server_files.go`).

  **Lo que confirma:** `src/library/api/client/pterodactyl.py`, con `tests/test_api_contracts.py`
  (S6). La clave del panel nunca va a la URL de Wings.

## Python y el framework

- **[What's New in Python 3.14 — PEP 649/749](https://docs.python.org/3.14/whatsnew/3.14.html)** —
  desde 3.14 las anotaciones ya no se evalúan al definir la función; antes, incluido 3.12, sí.

  **Lo que confirma:** el `NameError` de `src/services/pterodactyl.py` solo aparece en 3.12; probar
  con 3.14 lo esconde. Produjo: d-7985ee-7bc399.

- **[Starlette CORSMiddleware](https://github.com/encode/starlette/blob/master/starlette/middleware/cors.py)**
  y **[SessionMiddleware](https://github.com/encode/starlette/blob/master/starlette/middleware/sessions.py)** —
  con `allow_origins=["*"]` y `allow_credentials=True`, la respuesta devuelve el `Origin` del
  pedido (no `*`), haya o no cookie. La cookie de sesión es `samesite=lax` por defecto.

  **Lo que confirma:** la configuración de `main.py` es segura solo mientras la cookie sea `lax`.
  Produjo: d-7985ee-4e4aee.

  **Aplicado en S3:** sin CORS salvo los orígenes de `CORS_ALLOW_ORIGINS`.

- **[aiohttp — client quickstart](https://github.com/aio-libs/aiohttp/blob/master/docs/client_quickstart.rst)** —
  "Don't create a session per request… making a session for every request is a **very bad** idea."

  **Lo que confirma:** desde i-7985ee-57034c, `HttpAPI` tiene una sesión por cliente, que el
  lifespan cierra (`tests/test_http_client.py`).

- **[Beanie — índices](https://github.com/BeanieODM/beanie/blob/main/docs/tutorial/indexes.md)** —
  `Indexed(unique=True)` crea un índice único sobre ese campo al correr `init_beanie`.

  Beanie no borra ni cambia un índice existente: con el mismo nombre y otras opciones,
  `init_beanie` falla con `OperationFailure` (medido en S5, `mongo:8.0`). Por eso
  `drop_unique_index` borra el `unique` viejo de `ModrinthVersion.project_id` al arrancar.

## Seguimiento de versiones de mods (i-7985ee-43df2f)

- **[pterodactyl/panel#4894 — Include the hash of a file in its api response](https://github.com/pterodactyl/panel/issues/4894)** —
  lo pidieron para este mismo caso. El mantenedor contestó que un hash solo vendría de un endpoint
  por archivo, nunca en el listado, porque calcularlo es caro. Se cerró sin implementar
  (2026-01). Wings solo hashea backups y transferencias
  ([router.go](https://github.com/pterodactyl/wings/blob/v1.13.3/router/router.go)); Pelican es
  igual ([stat.go](https://github.com/pelican/wings/blob/v1.0.0-beta29/server/filesystem/stat.go)).

  **Lo que confirma:** la limitación original no se va a resolver en el panel. El hash hay que
  calcularlo leyendo los bytes, o hacer que la app sea quien instala.

- **[Wings — download handler](https://github.com/pterodactyl/wings/blob/v1.13.3/router/router_download.go)**
  y **[tokens](https://github.com/pterodactyl/wings/blob/v1.13.3/router/tokens/file.go)** — la
  descarga ignora `Range` (siempre manda el archivo entero) y cada URL firmada sirve una sola vez.
  `files/contents` acepta hasta 4 MiB y `files/pull` trae una URL remota sin verificar ningún hash
  (como máximo 5 cada 10 minutos por servidor). `files/compress` genera un `.tar.gz` dentro del
  servidor. La Client API permite 256 requests por minuto
  ([config/http.php](https://github.com/pterodactyl/panel/blob/v1.15.1/config/http.php)).

  **Lo que hacemos distinto, a propósito:** leer el central directory del jar con requests por
  rango no sirve por HTTP en Wings; solo funcionaría por SFTP.

- **[Modrinth App — caché de hashes](https://github.com/modrinth/code/blob/main/packages/app-lib/src/state/cache.rs)**,
  **[ferium/libium scan](https://github.com/gorilla-devs/libium/blob/main/src/scan.rs)** y
  **Prism Launcher** (`EnsureMetadataTask.cpp`) — las tres identifican por sha1 sobre el archivo
  completo contra `/version_files`. La Modrinth App cachea `{path, size, modified_at, hash}`. Ninguna
  identifica por metadata del jar: Prism la lee solo para mostrarla.

  **Lo que confirma:** la opción 1 de i-7985ee-43df2f. Una prueba con 9 jars reales: por hash
  acertó 9 de 9; por id y versión de la metadata, 4 de 9, y una vez devolvió el proyecto equivocado.

- **[packwiz-installer](https://packwiz.infra.link/tutorials/installing/packwiz-installer/)**
  ([UpdateManager.kt](https://github.com/packwiz/packwiz-installer/blob/main/src/main/kotlin/link/infra/packwiz/installer/UpdateManager.kt))
  y el **[formato de packwiz](https://packwiz.infra.link/reference/pack-format/pack-toml/)** — al
  arrancar, el servidor sincroniza contra un `pack.toml` servido por HTTP, baja solo lo que cambió,
  borra lo que salió y deja en `packwiz.json` el hash de cada archivo.

  **No aplicado aún:** es la opción 2 de i-7985ee-43df2f. Que el `STARTUP` de un egg pueda correrlo
  antes de Java lo sugiere el entrypoint de yolks
  ([entrypoint.sh](https://github.com/ptero-eggs/yolks/blob/master/java/entrypoint.sh)), pero no
  está probado.

- **[Formato `.mrpack`](https://support.modrinth.com/en/articles/8802351-modrinth-modpack-format-mrpack)** —
  exige sha1 y sha512 por archivo, y `downloads[]` solo desde dominios permitidos (cdn.modrinth.com,
  github.com…). El egg oficial "Modrinth Generic" lo instala solo al reinstalar, sin verificar
  hashes ni borrar mods viejos.

  **Lo que confirma:** para sincronizar un servidor en marcha, packwiz encaja mejor que `.mrpack`.

- **[CurseForge — fingerprints](https://docs.curseforge.com/rest-api/)** — `POST /v1/fingerprints`
  usa murmur2 y exige una API key aprobada. Solo importa si aparecen mods que no están en Modrinth.

## Investigaciones completas

Las fuentes de estos informes no se repiten aquí: cada uno cita las suyas y dice qué cambió.

| Informe | Qué cambió |
|---|---|
| [`research/2026-10-plataformas-similares.md`](research/2026-10-plataformas-similares.md) | el modelo objetivo de `plan.md` (builds inmutables, manifiesto por servidor, contenido del admin aparte); i-7985ee-098611, i-7985ee-5c7506, i-7985ee-063a3a |
| [`research/2026-10-resolucion-y-formatos.md`](research/2026-10-resolucion-y-formatos.md) | versiones por año, `environment`, `/project/{id}/dependencies` inservible para resolver; i-7985ee-f4ba63, i-7985ee-7f735a |
| [`research/2026-10-operacion-pterodactyl.md`](research/2026-10-operacion-pterodactyl.md) | el procedimiento de actualización y el usuario de servicio; i-7985ee-495708, i-7985ee-63226e |
| [`research/2026-10-arquitectura.md`](research/2026-10-arquitectura.md) | trabajos en Mongo sin Redis, tests con Mongo real, el volumen de Mongo; i-7985ee-0ec295, i-7985ee-ade111, i-7985ee-e21d5d |

## Qué leer primero

| Si vas a tocar… | Lee | Y cuidado con |
|---|---|---|
| `src/library/api/client/modrinth.py` | la OpenAPI de Modrinth | rutas singulares vs plurales; arrays en el cuerpo, strings JSON en la query |
| `src/library/api/client/pterodactyl.py` | las rutas del Client API | métodos y cuerpos JSON; las acciones sobre el servidor no se reintentan (d-7985ee-05d032) |
| `main.py` (middlewares) | Starlette CORS y sessions | `same_site` de la sesión (d-7985ee-4e4aee) |
| `src/model/*` | Beanie, índices | un índice único cambia lo que la base acepta |
| la resolución de dependencias o `game_versions` | `research/2026-10-resolucion-y-formatos.md` | versiones por año (`26.x`); no usar `/project/{id}/dependencies` |
| cualquier acción sobre un servidor (energía, archivos, backups) | `research/2026-10-operacion-pterodactyl.md` | un 204 no prueba nada; `restore` trunca sin parar |
| trabajos en segundo plano, tests con Mongo, login | `research/2026-10-arquitectura.md` | Beanie 2 no usa Motor: mongomock-motor no sirve |
