# Información externa que vale la pena saber

No es una lista de enlaces: cada entrada dice qué aporta **a este proyecto**, y cuando contradice
algo ya decidido, lo dice. Una fuente entra cuando cambió o confirmó una decisión, no por estar
bien escrita. Todas se leyeron en la fuente primaria el 2026-10-01.

## Las APIs externas

- **[Modrinth API v2 — OpenAPI](https://docs.modrinth.com/openapi.yaml)** (v2.7.0) — los lotes por
  hash son `POST /version_files` y `POST /version_files/update` (plural); el cuerpo lleva `hashes`
  como array JSON de strings y `algorithm` (`sha1` o `sha512`); el de update suma `loaders` y
  `game_versions` como arrays. `/version_file/{hash}` (singular) es para un solo hash. En
  `GET /project/{id}/version`, `loaders` y `game_versions` van en la query como **strings con un
  array JSON** (`'["fabric"]'`).

  **Lo que confirma:** `project_versions` en `src/library/api/client/modrinth.py`.

  **No aplicado aún:** `files_hash_post_many` y `files_update_hash_post_many` usan la ruta singular
  y mandan strings; `files_update_hash_post` manda strings donde van arrays (roadmap).

- **[Pterodactyl panel — rutas del Client API](https://github.com/pterodactyl/panel/blob/develop/routes/api-client.php)**
  y sus requests (`SendCommandRequest`, `DeleteFileRequest`) — enviar un comando es
  `POST /api/client/servers/{server}/command` con `{"command": "..."}`; borrar archivos es
  `POST .../files/delete` con cuerpo JSON `{"root": ..., "files": [...]}` y responde 204.

  **No aplicado aún:** `server_command` usa GET con query y `server_files_delete` manda query
  (roadmap).

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

  **No aplicado aún:** una lista explícita de orígenes en vez de `*`, cuando exista un frontend.

- **[aiohttp — client quickstart](https://github.com/aio-libs/aiohttp/blob/master/docs/client_quickstart.rst)** —
  "Don't create a session per request… making a session for every request is a **very bad** idea."

  **Lo que hacemos distinto, a propósito:** `HttpAPI._request` abre una `ClientSession` por
  request. No se cambió en el bootstrap; queda como deuda en el roadmap.

- **[Beanie — índices](https://github.com/BeanieODM/beanie/blob/main/docs/tutorial/indexes.md)** —
  `Indexed(unique=True)` crea un índice único sobre ese campo al correr `init_beanie`.

  **No aplicado aún:** `ModrinthVersion.project_id` tiene `unique=True`, así que solo cabe una
  versión por proyecto (roadmap).

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

## Qué leer primero

| Si vas a tocar… | Lee | Y cuidado con |
|---|---|---|
| `src/library/api/client/modrinth.py` | la OpenAPI de Modrinth | rutas singulares vs plurales; arrays en el cuerpo, strings JSON en la query |
| `src/library/api/client/pterodactyl.py` | las rutas del Client API | métodos y cuerpos JSON; las acciones sobre el servidor no se reintentan (d-7985ee-05d032) |
| `main.py` (middlewares) | Starlette CORS y sessions | `same_site` de la sesión (d-7985ee-4e4aee) |
| `src/model/*` | Beanie, índices | un índice único cambia lo que la base acepta |
