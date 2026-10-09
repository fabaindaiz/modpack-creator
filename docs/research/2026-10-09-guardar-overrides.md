# Overrides en un bundle: configs, scripts y otros archivos que no son mods

**Fecha:** 2026-10-09 · **Método:** documentación y especificaciones oficiales, más el código fuente de las herramientas, leído solo para entender su comportamiento (nada copiado). Una sonda sin credenciales midió nueve archivos `.mrpack` públicos (§3.1). Cada afirmación cita una fuente primaria; lo demás está marcado como **SUPUESTO**.

**Origen:** decisión D4 del importador (2026-10-09): el dueño quiere que la plataforma ayude a crear y exportar modpacks, así que guardar los overrides se investiga aparte.

**Pregunta:** ¿debe un bundle guardar "overrides" (archivos de configuración, scripts de KubeJS, archivos de recursos, opciones por defecto, configs solo de servidor)? Si debe, ¿cómo los guarda, cómo los despliega en un servidor de Pterodactyl sin pisar lo que admins y mods cambiaron allí, y cómo los exporta en un `.mrpack`?

**Punto de partida:** hoy un bundle tiene `projects`, `local_files` (jars por sha512, que se quedan en el servidor y nunca se exportan: d-7985ee-c8a15d) y reglas. Un build congela `lock` y `local_files`. `src/library/mrpack.py` escribe solo `modrinth.index.json`. El roadmap dice que la carpeta `overrides/` no se exporta porque el pack no guarda overrides (`docs/roadmap.md`, i-7985ee-7352b3). La investigación de importación lo deja abierto como **D4** (`docs/research/2026-10-09-importar-modpacks.md`). Toda escritura a Pterodactyl pasa por `PterodactylAPI._write`, detrás de `PTERODACTYL_WRITES` (d-7985ee-5739fc). Hoy la app escribe solo en `mods/`.

## Resumen

- **Toda herramienta guarda los archivos que no son mods como archivos planos bajo la ruta de su instancia, con hash en un índice** (packwiz), o como miembros de un zip bajo `overrides/` (`.mrpack`, CurseForge, Prism, Modrinth App). Solo `.mrpack` tiene una capa por lado (`server-overrides/`, `client-overrides/`), y **ninguno de los nueve packs populares muestreados la usa**: todos ponen todo en `overrides/`. packwiz da un lado solo a los metafiles de mods. Sus archivos planos (configs) van a todos los lados.
- **Las herramientas caen en cuatro políticas de actualización para los archivos que un servidor o usuario cambió:**
  1. **Sobrescribir siempre** que cambia la versión del pack: Prism (lo dice en un TODO), la app de CurseForge (documentado), itzg `MODRINTH`, Modrinth App (salvo `options.txt`).
  2. **Sobrescribir solo si el pack cambió ese archivo** desde la última instalación: packwiz-installer. Compara el hash del índice con el hash en caché, y pierde las ediciones locales cuando el archivo del pack cambia.
  3. **Tres vías con respaldo**: mrpack4server y mrpack-install. Registran el hash que instalaron. Cuando el pack cambia un archivo que el usuario también cambió, apartan la copia del usuario (`old_modified_files/`, `backups/<timestamp>/`) y escriben la nueva.
  4. **Nunca sobrescribir** un archivo marcado para eso, ni ninguna ruta de una lista: `preserve` de packwiz, `CF_OVERRIDES_SKIP_EXISTING` de itzg, y `non_overwritable_paths` y `lockedpaths.txt` de mrpack4server (por defecto: `world`, `server.properties`, `ops.json`, `whitelist.json`, las listas de baneados).
- **Los mods reescriben sus propias configs.** El loader de NeoForge comprueba cada config contra su especificación al cargar. Si la config "no es correcta", respalda el archivo y escribe uno corregido. Copia `defaultconfigs/<file>` solo cuando el archivo de config **no existe**. Así, un hash de bytes tomado al desplegar puede cambiar en el primer arranque aunque nadie haya editado el archivo.
- **Tamaños:** las configs de texto y los scripts suman **0.2–17 MiB** por pack, y el archivo de texto individual más grande fue de 2.5 MiB (sonda de `.mrpack`, §3.1). Lo que hace grandes a los overrides son los recursos binarios: resource packs incluidos (hasta 171 MiB en un archivo), mundos, cachés de estructuras y jars embebidos. Los packs con overrides pesan 20–560 MiB sin comprimir.
- **Pterodactyl tiene lo que el despliegue necesita:** `files/write` (cuerpo crudo; crea carpetas padre y trunca), `files/contents` (lee como máximo 4 MiB), `files/upload` (URL firmada a Wings, 100 MB por defecto) y `files/decompress` (sobrescribe). Cada uno es una escritura que `_write` debe controlar, salvo `contents`.
- **Recomendación:** guardar overrides, empezar solo con texto, y mantenerlos direccionados por contenido (sha512) en MongoDB, congelados en el build. Desplegar con la política de tres vías y una lista de rutas protegidas. Exportar a los jugadores solo las capas común y cliente. Seis decisiones para el dueño, O1–O6 (§6).

---

## 1. Cómo modelan otras herramientas los archivos que no son mods

### packwiz (formato e instalador)

- **Almacenamiento:** el pack es una carpeta. `index.toml` lista cada archivo con `file`, `hash`, `hash-format`, `alias`, `metafile` y `preserve`. Un **metafile** es un `.pw.toml` que apunta a un mod descargable. Toda otra entrada es un **archivo plano en el repositorio** (una config, un script), guardado tal cual y con hash. El índice usa sha256 por defecto (`core/pack.go`), y `.packwizignore` (sintaxis de gitignore) excluye rutas (`core/index.go`). ([referencia de index.toml](https://packwiz.infra.link/reference/pack-format/index-toml/))
- **`preserve`:** "When this is set to true, the file is not overwritten if it already exists, to preserve changes made by a user." El instalador lo comprueba en cada ejecución, no solo en la primera instalación. Si el archivo existe, retorna antes de descargar, sea cual sea su hash (`DownloadTask.kt`, `download()`).
- **Decisión de actualización** (packwiz-installer `DownloadTask.kt`, `UpdateManager.kt`):
  - Mantiene una caché (`packwiz.json`) con el hash que instaló por última vez para cada archivo.
  - Si el hash del índice **es igual al hash en caché**, el archivo está "already up to date" y no se lee del disco. **Las ediciones locales sobreviven** mientras el pack no cambie ese archivo.
  - Si el hash del índice cambió, hashea el archivo en disco. Si coincide con el hash nuevo, lo omite; si no, **descarga y sobrescribe, perdiendo las ediciones locales** (salvo que `preserve` esté activo).
  - Un archivo en la caché que falta en el índice nuevo se **borra**.
  - Un archivo en caché que desapareció del disco se invalida y se descarga otra vez.
  - Si cambia el lado, todo se invalida.
- **Lado:** `side` (`client`, `server`, `both`) existe solo en el metafile del mod ([mod-toml](https://packwiz.infra.link/reference/pack-format/mod-toml/)). El `correctSide()` del instalador devuelve true para una entrada sin metafile vinculado, así que **los archivos planos (configs) se instalan en todos los lados**.
- **Exportación a `.mrpack`:** todo lo que no es metafile va bajo `overrides/` (`AddNonMetafileOverrides` en `cmdshared/downloadutil.go`). Solo los mods que no se pueden referenciar por URL van a `client-overrides/` o `server-overrides/` según su lado (`modrinth/export.go`). La exportación a CurseForge pone todo en `overrides/`.

### Especificación `.mrpack`

- `overrides/`: "Files in this directory will be copied to the root of the Minecraft Instance directory upon installation by the launcher."
- `server-overrides/`: "will be applied after the `overrides` folder, overwriting its contents."
- `client-overrides/`: "functionally equivalent to the server overrides folder (besides only being applied on the client)".
- Árbol efectivo: **servidor = overrides + server-overrides; cliente = overrides + client-overrides**, donde gana la capa posterior.
- Los archivos del índice llevan `env.client` y `env.server` (`required`, `optional`, `unsupported`). Los overrides no llevan `env`: su carpeta es su lado.
- **La especificación no dice nada sobre actualizaciones** ni sobre conservar los cambios de un usuario. ([especificación](https://support.modrinth.com/en/articles/8802351-modrinth-modpack-format-mrpack))

### Modrinth App (`modrinth/code` @ `badbf9f`, solo lectura)

- **Instalación** (`packages/app-lib/src/api/pack/install_mrpack.rs`):
  - Extrae `overrides/` y luego `client-overrides/` en la instancia, e ignora `server-overrides/`.
  - Registra los hashes de los archivos de override para poder distinguir después el contenido del usuario del contenido del pack.
  - Maneja por separado `options.txt` y los archivos de "valor por defecto en el primer arranque" de YOSBR y DefaultOptions (`config/yosbr/options.txt`, `config/defaultoptions/…`). El módulo `synced_options/game_options/pack_updates.rs` se describe a sí mismo como "Keeps game settings intact when a modpack is installed or updated", lo que significa una **fusión a nivel de clave** para ese único archivo.
  - Los demás archivos de override se extraen sin comprobación de "omitir si existe".
- **Exportación** (`api/instance/export_mrpack.rs`):
  - El usuario elige rutas (reglas de inclusión y exclusión).
  - Los mods que puede resolver van al índice, con `env` tomado del `environment` de la versión.
  - Todo otro archivo seleccionado va **solo bajo `overrides/`**.

### Prism Launcher (`develop` @ `e4299e1`, solo lectura)

- **Instalación y actualización** (`modplatform/modrinth/ModrinthInstanceCreationTask.cpp`, líneas ~129–204; `modplatform/helpers/OverrideUtils.cpp`):
  - Escribe una **lista de archivos gestionados**: `mrpack/overrides.txt` y `client-overrides.txt`, cada una con todas las rutas de override que instaló.
  - Al actualizar **borra todos los overrides anteriores** y luego aplica los nuevos. El código lo dice en un comentario: "TODO: Currently 'overrides' will always override the stuff on update. How do we preserve unchanged overrides?"
- **Exportación** (`ModrinthPackExportTask.cpp`):
  - Todo lo que el usuario seleccionó va a `overrides/`.
  - Las exclusiones del usuario persisten en un `.packignore` por instancia (`ExportInstanceDialog.cpp`).
  - Los mods que no puede resolver van en `overrides/` como archivos (investigación previa).

### CurseForge

- **Formato:** "the overrides folder should contain your config folder, scripts folder, and any other folders your pack needs" (artículo de soporte [9000197908](https://support.curseforge.com/support/solutions/articles/9000197908)). Tiene una sola capa y ninguna división cliente/servidor.
- **Actualización:** "Any custom changes you made to the modpack's content will be overwritten when changing versions, unless you select Update to new Profile" (artículo [9000206622](https://support.curseforge.com/support/solutions/articles/9000206622-how-to-update-change-a-modpack-s-version), modificado el 2026-04-01).

### Technic Solder (el análogo que nombra `docs/plan.md`)

- Las configs son solo otro zip de "mod": "Configuration files go inside a `config/` directory. You can include files at other paths too -- everything extracts to the modpack root", y el launcher "extracts the zip contents into the root of the modpack directory" ([documentación de Solder, Adding Mods](https://docs.solder.io/guides/adding-mods/)).
- Versionar las configs como un "mod" separado de tipo "config pack" es el patrón documentado.
- No está documentado si una actualización sobrescribe los archivos editados.

### Instaladores del lado del servidor

| Herramienta | Capas aplicadas | Política para archivos existentes | Lista gestionada / estado |
|---|---|---|---|
| **itzg `MODRINTH`** (mc-image-helper @ `6342639`, `ModrinthPackInstaller.java` `extractOverrides`) | `overrides`, luego `server-overrides` | `REPLACE_EXISTING`, pero solo cuando cambió la versión del pack (`needsInstall` omite el mismo proyecto, versión y archivos presentes salvo `MODRINTH_FORCE_SYNCHRONIZE`) | manifiesto de los archivos que instaló. `Manifests.cleanup` borra los archivos del manifiesto viejo que no están en el nuevo. `MODRINTH_OVERRIDES_EXCLUSIONS` (rutas ant) nunca los extrae ([docs](https://docker-minecraft-server.readthedocs.io/en/latest/types-and-platforms/mod-platforms/modrinth-modpacks/)) |
| **itzg `AUTO_CURSEFORGE`** | `overrides` | `CF_OVERRIDES_SKIP_EXISTING`: "files in the overrides that already exist in the data directory are skipped. NOTE world data is always skipped". Limpia los archivos de versiones anteriores, y "world data is never auto-removed" ([docs](https://docker-minecraft-server.readthedocs.io/en/latest/types-and-platforms/mod-platforms/auto-curseforge/)) | manifiesto |
| **itzg `PACKWIZ_URL`** | packwiz, lado servidor | la de packwiz-installer (arriba). "pre-configured to only download server mods" ([docs](https://docker-minecraft-server.readthedocs.io/en/latest/mods-and-plugins/packwiz/)) | `packwiz.json` |
| **mrpack-install** (`trunk` @ `480907b`, `update/command.go`, `update/packstate`) | `overrides`, `server-overrides` | Mantiene un `packstate` con el sha512 de **cada** descarga y archivo de override. Omite un archivo sin cambios en el pack y sin cambios en disco. Antes de reemplazar un archivo **mueve el existente a `backups/<timestamp>/`**, y luego extrae los overrides (sobrescribiendo). Se niega a actualizar a través de cambios de loader o de versión de juego ("please upgrade manually") | packstate (hashes) |
| **mrpack4server** (`eb631a8`, `MrPackInstaller.java` `extractIncluded`) | `/overrides`, `/server-overrides` | **Tres vías por sha512**: si el pack no cambió el archivo (viejo == nuevo) o el disco ya es igual al archivo nuevo, conserva el archivo. Si el usuario lo cambió (disco != viejo), mueve la copia del usuario a `old_modified_files/` con una línea de log ("File '…' was modified, but modpack required it to be updated!"), y luego escribe el nuevo. Las rutas en `non_overwritable_paths` o `lockedpaths.txt` nunca se tocan si existen (por defecto: `world`, `server.properties`, `whitelist.json`, `banned-*.json`, `ops.json`) ([README](https://github.com/Patbox/mrpack4server)) | hashes guardados |

No investigado: ATLauncher (su formato de pack tiene listas de conservar y de borrar: **SUPUESTO**, no verificado).

## 2. El riesgo: las configs se editan en el lugar

Qué cambia las configs de un servidor entre dos despliegues:

1. **Admins**, en el editor del panel (`server:file.write` en el registro de actividad) o por SFTP (`server:sftp.write`) (`docs/research/2026-10-operacion-pterodactyl.md` §5).
2. **El loader y los propios mods.** NeoForge (FancyModLoader @ `cb29eec`, `fml/config/ConfigTracker.java`, `loadConfig` y `setupConfigFile`):
   - Cuando una config existe pero `isCorrect` falla (una clave nueva tras actualizar un mod, un valor fuera de rango), registra "Configuration file … is not correct. Correcting", escribe un `.bak` (hasta 5) y **reescribe el archivo**.
   - Cuando falta la config, **copia `defaultconfigs/<file>`** si existe, y si no escribe los valores por defecto de la especificación.
   - Las configs de tipo SERVER pueden sobrescribirse por mundo en `world/serverconfig/`, y se sincronizan a los clientes ([documentación de NeoForge, Configuration](https://docs.neoforged.net/docs/misc/config/)).
   - Un observador de archivos recarga las configs editadas mientras el servidor corre.
   - Fabric no tiene un sistema de config común; cada mod (Cloth Config, owo, MidnightLib…) tiene su propio comportamiento de reescritura. **SUPUESTO:** la mayoría escribe las claves faltantes al arrancar, como NeoForge.
3. **El propio juego** escribe estado de ejecución: `server.properties`, `ops.json`, `whitelist.json`, `banned-*.json` y `world/`. Ningún pack debería tocarlos nunca (los valores por defecto de mrpack4server).

**Convenciones que existen para esto:**

- **`defaultconfigs/` (Forge y NeoForge):** se aplica solo en la primera creación, para las configs gestionadas por el loader. BMC2 (Fabric) y BMC4 (Forge) traen ambos 500–670 archivos ahí, que reflejan `config/` (sonda).
- **Mods de valor por defecto en el primer arranque:** YOSBR (`config/yosbr/…`), DefaultOptions (`config/defaultoptions/…`), y `config/modpack_defaults/…` de Fabulously Optimized (sonda). El pack trae una plantilla, y el mod la copia solo si falta el archivo del usuario. Modrinth App conoce estas rutas.
- **Listas de rutas protegidas** (mrpack4server, exclusiones de itzg) y **`preserve`** (packwiz).

**Qué significa esto para una comprobación por hash de "sin cambios desde el despliegue":**

- El hash base debe ser el hash **después de que el servidor se asentó**: releer y rehashear tras la verificación de arranque posterior al despliegue (paso 6 del procedimiento de actualización), **no** el hash de los bytes que la app envió. Si no, cada config que el loader corrigió parece una edición de admin.
- Una reescritura del loader sobre un archivo que el pack no cambió es inocua bajo la política 2 o 3. Solo importa cuando el pack cambia el mismo archivo la próxima vez.
- Una fusión a nivel de clave (Modrinth App para `options.txt`, el mod Config Manager en CurseForge) es la única forma de conservar los cambios de ambos lados. Necesita un parser por cada formato (TOML, JSON5, `.cfg`, `.properties`, SNBT…), lo cual es caro. **SUPUESTO:** no vale la pena para un primer corte.

## 3. Opciones de almacenamiento para esta app

### 3.1 Qué tan grandes son los overrides (sonda, 2026-10-09)

La sonda tomó la última versión de los modpacks más descargados de Modrinth (`/v2/search`, ordenado por descargas), más un pack de KubeJS. Leyó solo el directorio del zip, sin credenciales. El script y los datos están en el directorio temporal, no en el repositorio.

| Pack (loader) | .mrpack | Archivos de override / sin comprimir | Configs de texto y scripts (json, toml, json5, cfg, properties, txt, js, snbt…) | Archivo de texto más grande | Archivos > 4 MiB | Capas usadas |
|---|---|---|---|---|---|---|
| fabulously-optimized (Fabric) | 169 KiB | 51 / 298 KiB | 50 / 210 KiB | 24 KiB | 0 | solo `overrides` |
| cobblemon-fabric | 96 MiB | 2,436 / 106 MiB (un mundo tutorial bajo `config/yosbr/saves`) | 1,754 / 1.5 MiB | 82 KiB | 10 | solo `overrides` |
| cobbleverse (Fabric) | 228 MiB | 1,647 / 253 MiB (resource pack de banda sonora de 171 MiB) | 421 / 16.5 MiB (sobre todo fuentes de shaders) | 2.5 MiB | 5 | solo `overrides` |
| better-mc-fabric-bmc2 | 63 MiB | 4,613 / 90 MiB (caché de estructuras, 20 jars) | 914 / 2.5 MiB | 100 KiB | 1 | solo `overrides` |
| better-mc-forge-bmc4 | 86 MiB | 4,645 / 113 MiB | 1,204 / 3.6 MiB | 156 KiB | 2 | solo `overrides` |
| prominence-2-fabric | 404 MiB | 2,091 / 559 MiB (mundos bajo config, 37 jars / 96 MiB) | 863 / 4.1 MiB | 231 KiB | 19 | solo `overrides` |
| the-pixelmon-modpack (NeoForge) | 44 MiB | 559 / 46 MiB (imágenes de menú) | 22 / 851 KiB | 225 KiB | 1 | solo `overrides` |
| battlearmorytacz (Forge) | 339 MiB | 2,295 / 399 MiB (packs de armas de hasta 118 MiB, partidas guardadas) | 1,809 / 3.5 MiB | 310 KiB | 21 | solo `overrides` |
| stormforge (Forge, KubeJS) | 17 MiB | 926 / 20 MiB | `kubejs/` 361 archivos / 3.9 MiB, `config/` 14 MiB | n/a | n/a | solo `overrides` |

Lo que muestra la sonda:

- Las configs de texto y los scripts ocupan 0.2–17 MiB y hasta ~1,800 archivos.
- Todo archivo de texto está bajo el límite BSON de 16 MiB y el límite de 4 MiB de `files/contents`, y todos menos uno están bajo 310 KiB.
- Los overrides grandes son recursos binarios: resource packs, mundos, cachés de estructuras y jars embebidos, muchos de ellos de más de 16 MiB.
- Los jars embebidos en `overrides/mods/` entran en conflicto con d-7985ee-c8a15d (los jars locales nunca se exportan).
- Ninguno de los packs usa `server-overrides/` ni `client-overrides/`. **SUPUESTO:** esto se generaliza. La muestra es de 9 packs.

### 3.2 Opciones

| Opción | Cómo | Costo / riesgo |
|---|---|---|
| **S1. Documentos de Mongo, direccionados por contenido** | Una colección `blobs` con `_id` = sha512 hex, `size` y `data: BinData`. Un bundle y un build guardan `{path → sha512, side, policy}`. El manual de MongoDB: "if all files smaller than the 16 MiB BSON Document Size limit, consider storing each file in a single document instead of using GridFS. Use the BinData data type" ([GridFS](https://www.mongodb.com/docs/manual/core/gridfs/)) | Un `Document` de Beanie más, registrado en `init_beanie`. Ya está en el volumen `mongodb-data` y en `mongodump` (`docs/runbooks/migrar-mongo-a-volumen.md`). Tope duro de 16 MiB por archivo, así que los recursos grandes se rechazan. Deduplicación entre builds gratis |
| **S2. GridFS, direccionado por contenido** | `fs.files` y `fs.chunks` (trozos de 255 KiB), filename = sha512. PyMongo tiene `AsyncGridFSBucket` (`upload_from_stream`, `open_download_stream`) ([GridFS async de PyMongo](https://pymongo.readthedocs.io/en/stable/api/gridfs/asynchronous/index.html)), y la app ya usa `AsyncMongoClient` (`src/model/__init__.py`). La API async es GA desde PyMongo 4.13 ([changelog](https://pymongo.readthedocs.io/en/stable/changelog.html)). **SUPUESTO:** la versión exacta que agregó GridFS async | Cualquier tamaño, y streaming. El manual advierte: "Do not use GridFS if you need to update the content of the entire file atomically". El direccionamiento por contenido evita el problema porque los blobs nunca se actualizan. No es un modelo de Beanie, así que la regla `registration` del audit no lo ve. Los binarios grandes también agrandan `mongodump` |
| **S3. Volumen de sistema de archivos, direccionado por contenido** | Un nuevo volumen con nombre en `api` (`/data/blobs/ab/cd/<sha512>`) | Un segundo almacén para respaldar y restaurar **junto con** Mongo: restaurar uno sin el otro deja referencias colgantes. Cambian el compose, la comprobación de imagen en CI y un nuevo runbook |
| **S4. No guardar; referenciar un repo git externo** | El bundle nombra una URL de git y una ref. Un build congela el commit y los hashes de los archivos | Las configs viven donde los admins ya las versionan. La app necesita git o pedidos HTTP, una nueva dependencia de red (`@pytest.mark.network`), y credenciales para repos privados |

**Versionado por build:**

- Congelar un **mapa de path → (sha512, side, policy)** en `ModpackBuild`, igual que se congelan `lock` y `local_files`. Los blobs son inmutables y compartidos, así que un build cuesta solo el mapa. El rollback al build N vuelve a desplegar el mapa de N.
- Recolectar como basura los blobs que ningún build referencia, y solo cuando nada los referencia (tarjeta de `.agents` sobre borrar datos guardados: **SUPUESTO** de que esto aplica; revisar `INDEX.md` antes de decidir).
- La superficie de edición (el conjunto de trabajo del bundle) cambia por actualizaciones atómicas, como lo hace hoy `projects`.

## 4. API de cliente de Pterodactyl para escribir configs

Todo de `routes/api-client.php` y `FileController.php` del panel `v1.15.1`, y de Wings `v1.13.3`.

| Endpoint | Cuerpo / límites | Permiso del panel | Semántica | Control |
|---|---|---|---|---|
| `GET /api/client/servers/{s}/files/contents?file=` | devuelve texto crudo, como máximo `PTERODACTYL_FILES_MAX_EDIT_SIZE` = **4 MiB** por defecto (`config/pterodactyl.php`) | `file.read-content` | lectura; registra `server:file.read` | lectura (sin control) |
| `POST …/files/write?file=` | **cuerpo crudo de la request**, `Content-Length` obligatorio (Wings rechaza -1) | `file.create` (`WriteFileContentRequest`) | `Filesystem.Write` de Wings: "create or truncate the file, and create any necessary parent directories"; comprueba espacio libre en disco; registra `server:file.write` | `_write` |
| `GET …/files/upload` → URL firmada de Wings | multipart; cada archivo ≤ `api.upload_limit` de Wings, **100 MB** por defecto (`config/config.go`); query `directory` | crear archivo (**SUPUESTO**: el mismo permiso) | sobrescribe (el mismo `Write`). La URL firmada vale 15 min (investigación del repo) | ya es `_write` (`server_files_upload`) |
| `POST …/files/decompress {root, file}` | archivo comprimido ya en el servidor | `file.create` | `DecompressFile` de Wings escribe cada entrada con `Write`, así que **sobrescribe los archivos existentes**; comprueba antes el espacio libre | `_write` |
| `POST …/files/compress {root, files[]}` | produce un archivo comprimido en el servidor | `file.archive` | útil para una instantánea de `config/` de "leer todo en una request", pero crea un archivo que alguien debe limpiar | `_write` (escribe) |
| `POST …/files/copy {location}`, `PUT …/files/rename`, `POST …/files/delete`, `POST …/files/create-folder` | — | file.create / update / delete | para apartar un archivo editado por un admin antes de reemplazarlo (el patrón de mrpack4server) | `_write` |
| `GET …/files/download?file=` | URL firmada de un solo uso, sin `Range` (investigación del repo) | lectura de archivos | cómo la app ya hashea los jars | lectura |

Notas prácticas:

- **Límite de tasa:** 256 requests/min en la API de cliente (investigación del repo). Un `contents` + `write` por archivo para las ~1,200 configs de texto de BMC4 son ~2,400 requests, unos 10 minutos.
- **La alternativa en bloque:** subir un zip (una request), `decompress`, y luego borrar el zip. Decompress sobrescribe cada entrada que contiene, así que el zip debe contener **solo los archivos que la política decidió escribir**.
- **Lectura en bloque:** `compress` de `config/`, descargar el archivo comprimido, hashear en local, borrar el archivo comprimido. Son tres escrituras para una lectura.
- Las rutas de `.pteroignore` se rechazan (`IsIgnored`) al escribir, subir y descomprimir.
- **El usuario de servicio** (i-7985ee-63226e) necesitaría `file.read-content` (y `file.archive` si se usan lecturas en bloque) además de lo que ya tiene. **SUPUESTO:** su conjunto actual de permisos no se revisó de nuevo aquí.

## 5. Licencias y redistribución (breve)

- "Obtaining modpack permissions" de Modrinth pide permiso o una licencia de redistribución para "mods, resource packs, data packs, and shaders" en un pack, y no menciona los archivos de configuración ([artículo 8797527](https://support.modrinth.com/en/articles/8797527)). Los mods de CurseForge llevan `allowModDistribution` (investigación previa). Ambos aplican a los **recursos binarios en overrides**: un resource pack incluido, un mundo o un jar embebido (la sonda encontró hasta 37 jars y 96 MiB en un solo pack).
- **Configs:** una config por defecto la genera el código de un mod, y una modificada es obra del autor del pack. **SUPUESTO** (no es asesoría legal): los archivos de configuración ordinarios se redistribuyen rutinariamente en cada pack muestreado. Los *scripts* de terceros (KubeJS de otro pack) y los recursos llevan la licencia de su autor.
- **La exposición real son los secretos, no el copyright.** Las configs de servidor pueden contener credenciales. El `luckperms.conf` por defecto de LuckPerms (Fabric y NeoForge) tiene campos `username` y `password` de almacenamiento (LuckPerms `fabric/src/main/resources/luckperms.conf`, líneas ~94–106). **SUPUESTO** (no comprobado contra una fuente primaria): `server.properties` tiene `rcon.password`, y los mods de puente con Discord guardan tokens de bots. Todo lo que esté en `server-overrides/` de un `.mrpack` exportado lo puede leer cualquier jugador que lo descomprima, aunque los launchers no lo apliquen. Por eso **una exportación para jugadores no debe contener la capa de servidor en absoluto.**
- Los overrides importados (de un `.mrpack` de terceros, el D4 del pipeline de importación) deberían conservar su procedencia (pack y versión de origen), para que la exportación pueda advertir cuando los redistribuiría.

## 6. Recomendación y decisiones para el dueño

El camino recomendado, en un párrafo:

- Guardar los overrides de texto (configs, scripts de KubeJS, `defaultconfigs/`, plantillas de primer arranque) por bundle, direccionados por contenido con sha512 en documentos de MongoDB, y congelarlos en cada build.
- Desplegarlos con una política de hash de tres vías que nunca sobrescriba un archivo que un admin cambió: reporta el archivo, o aparta la copia del admin después del respaldo de Pterodactyl.
- Mantener una lista fija de rutas protegidas que la app nunca escribe.
- Exportar las capas común y cliente a los jugadores; la capa de servidor nunca sale de la app.

Las seis decisiones:

**O1. ¿Guardar overrides?**
- **(a, recomendada) Sí, solo texto al principio.** Rutas bajo `config/`, `defaultconfigs/`, `kubejs/`, `scripts/` y `global_packs/` (**SUPUESTO** en las dos últimas), hasta ~8 MiB por archivo y ~64 MiB por bundle.
  - Ejemplo: las 1,204 configs de texto de BMC4 (3.6 MiB) caben; su `loading.png` de 7 MiB se rechaza con un motivo.
  - Costo: un modelo nuevo, una superficie de subida y edición, cambios en el despliegue y en la exportación.
- **(b) Todo, binarios incluidos.**
  - Ejemplo: el resource pack de banda sonora de 171 MiB de cobbleverse entra en el bundle.
  - Costo: GridFS (O2-b), respaldos más grandes, comprobaciones de licencia de los recursos, y el tope de subida de 100 MB de Wings.
- **(c) No, solo listar.** La importación informa "este pack tenía 1,647 archivos de override, no importados".
  - Costo: ninguno, pero la app no ayuda a crear packs más allá de los mods.
- **(d) Término medio: overrides solo de exportación.** Guardados y exportados en el `.mrpack`, nunca desplegados en servidores.
  - Ejemplo: un jugador recibe los ajustes de minimapa y shaders, y el servidor nunca se toca.
  - Costo: la mitad de (a), sin riesgo para los servidores.

**O2. ¿Dónde?**
- **(a, recomendada) Documentos de Mongo, `_id` = sha512, `BinData`, ≤ 16 MiB.**
  - Ejemplo: `blobs/{"_id": "9f3c…", "size": 4210, "data": …}`, y el build guarda `{"config/sodium-options.json": {"sha512": "9f3c…", "side": "client"}}`.
  - Costo: bajo, y los datos ya están en el volumen respaldado.
- **(b) GridFS, mismo direccionamiento.** Necesario solo si O1-b. Costo: medio, fuera de Beanie.
- **(c) Volumen de sistema de archivos.** Costo: un segundo almacén que mantener consistente y respaldar (un nuevo runbook).
- **(d) Repo git externo, congelado por commit.** Costo: una dependencia de red y credenciales, pero las configs quedan donde los admins ya las revisan.

**O3. Política de despliegue de las configs en servidores.**
- **(a, recomendada) Tres vías por sha512 más rutas protegidas.** Es la lógica de mrpack4server sin su sobrescritura silenciosa.
  - La base es el hash que la app registró **después de que el servidor se asentó** (releído tras la verificación de arranque).
  - El pack no cambió el archivo: se deja.
  - Disco = base y el pack lo cambió: se escribe el archivo nuevo.
  - Disco ≠ base y el pack lo cambió: **conflicto**, reportado al estilo "Por decidir". Por defecto se conserva la copia del servidor. Como alternativa, copiarla a `config.old/<job>/` y escribir la nueva después del respaldo previo a la actualización.
  - Falta en el servidor: se escribe.
  - Ejemplo: un admin subió `max_claimed_chunks` en `config/ftbchunks-world.snbt`. El build nuevo cambia el mismo archivo, así que la actualización muestra un conflicto en vez de borrar la edición.
  - Costo: lecturas por archivo (límite de tasa) y una base registrada por servidor, que vive en el manifiesto de `ServerScan` (i-7985ee-5c7506).
- **(b) Estilo packwiz: sobrescribir cuando el pack cambió el archivo.**
  - Ejemplo: se pierde la misma edición del admin, pero el respaldo de Pterodactyl la tiene.
  - Costo: bajo, no hacen falta lecturas, pero destruye trabajo del admin en silencio.
- **(c) Solo primer arranque (`preserve` para todo).** Escribir solo los archivos que no existen; nunca actualizar.
  - Ejemplo: un servidor nuevo recibe las configs, y los builds posteriores nunca las cambian.
  - Costo: el más bajo, pero los cambios de config de un build nunca llegan a los servidores existentes.
- **(d) Política por archivo elegida en el bundle**: `managed` (O3-a), `seed` (O3-c) o `client-only`.
  - Ejemplo: `kubejs/` es `managed`, y `config/luckperms/` es `seed`.
  - Costo: un campo más y un control de UI por ruta.

  Rutas protegidas en todos los casos, que la app nunca escribe: `world/`, `server.properties`, `ops.json`, `whitelist.json`, `banned-*.json` (los valores por defecto de mrpack4server), y `mods/` (propiedad del lock y de `local_files`). Si las escrituras de config necesitan su **propio** opt-in más allá de `PTERODACTYL_WRITES` es parte de esta decisión. Ejemplo: `PTERODACTYL_CONFIG_WRITES: "enabled"`.

**O4. Política de exportación (`.mrpack`).**
- **(a, recomendada) Una exportación para jugadores con solo `overrides/` (común) y `client-overrides/` (cliente), nunca la capa de servidor.** Cada ruta de miembro se comprueba con `_unsafe_path`, y la procedencia de los archivos importados se muestra antes de exportar.
  - Ejemplo: el `.mrpack` del jugador lleva `config/sodium-options.json` pero no `config/luckperms/luckperms.conf`.
- **(b) También una exportación de "server pack"** con `server-overrides/`, solo para el dueño (rol de operador), para usar con mrpack4server o itzg.
  - Costo: un segundo endpoint, y una descarga que puede contener secretos.
- **(c) Aplanar todo en `overrides/`** como hace cada pack muestreado.
  - Costo: los archivos solo de servidor llegan a los jugadores, así que esto necesita O5 para excluirlos.

**O5. Manejo por lado.**
- **(a, recomendada) Tres lados por archivo guardado, `both`, `server` y `client`, mapeados a las tres capas del `.mrpack`.**
  - El despliegue escribe `both` y `server`; la exportación para jugadores escribe `both` y `client`.
  - Por defecto según la ruta: `options.txt` y `config/yosbr/` son `client`, las configs son `both`, y el admin puede cambiar cualquiera.
  - Ejemplo: `config/xaerominimap.txt` → `client`, `config/luckperms/` → `server`, `kubejs/server_scripts/` → `both` (**SUPUESTO**: KubeJS necesita también sus scripts y recursos de cliente en el cliente).
- **(b) Dos lados (`both` y `server`).** Los archivos solo de cliente se despliegan sin daño en el servidor.
  - Costo: menor, pero desorden en los servidores.
- **(c) Sin lado (estilo packwiz).** Todo va a todas partes. Costo: los secretos se filtran a la exportación para jugadores.

**O6. Importar overrides (el D4 de la investigación de importación), una vez decidida O1.**
- **(a, recomendada) Importar los overrides de texto al bundle**, con procedencia y los lados por defecto de O5. Omitir los binarios con un motivo por archivo, y omitir los jars embebidos como ya lo hace d-7985ee-c8a15d.
  - Ejemplo: importar BMC4 trae 1,204 configs e informa 3,256 archivos de caché `.nbt` y 21 jars como no importados.
- **(b) Solo informar.**

**No verificado** (y el gate no lo verificaría):

- Cualquier escritura real a un servidor de Pterodactyl.
- Cómo acepta el panel un cuerpo grande de `files/write`: límites en nginx o PHP (**SUPUESTO**: la configuración de nginx documentada del panel tiene `client_max_body_size 100m`).
- Los permisos actuales del usuario de servicio.
- El comportamiento de reescritura de los mods de Fabric.
- El estatus legal de redistribuir configs.

---

## Fuentes

Especificaciones y documentación oficial
- Formato `.mrpack`: https://support.modrinth.com/en/articles/8802351-modrinth-modpack-format-mrpack
- Modrinth, Obtaining modpack permissions: https://support.modrinth.com/en/articles/8797527
- packwiz index.toml: https://packwiz.infra.link/reference/pack-format/index-toml/
- packwiz mod.toml (`side`): https://packwiz.infra.link/reference/pack-format/mod-toml/
- CurseForge, estructura del zip de un modpack: https://support.curseforge.com/support/solutions/articles/9000197908
- CurseForge, actualizar un modpack (cambios propios sobrescritos): https://support.curseforge.com/support/solutions/articles/9000206622-how-to-update-change-a-modpack-s-version
- Technic Solder, Adding Mods: https://docs.solder.io/guides/adding-mods/
- itzg/docker-minecraft-server, modpacks de Modrinth: https://docker-minecraft-server.readthedocs.io/en/latest/types-and-platforms/mod-platforms/modrinth-modpacks/
- itzg, Auto CurseForge: https://docker-minecraft-server.readthedocs.io/en/latest/types-and-platforms/mod-platforms/auto-curseforge/
- itzg, packwiz: https://docker-minecraft-server.readthedocs.io/en/latest/mods-and-plugins/packwiz/
- README de mrpack4server: https://github.com/Patbox/mrpack4server
- README de mrpack-install: https://github.com/nothub/mrpack-install
- NeoForge, Configuration: https://docs.neoforged.net/docs/misc/config/
- MongoDB, GridFS: https://www.mongodb.com/docs/manual/core/gridfs/
- GridFS async de PyMongo: https://pymongo.readthedocs.io/en/stable/api/gridfs/asynchronous/index.html
- Changelog de PyMongo: https://pymongo.readthedocs.io/en/stable/changelog.html

Código fuente (leído solo por su comportamiento; las licencias varían: Prism GPL-3.0, Modrinth AGPL/GPL/LGPL, packwiz MIT, otros según el repositorio; nada copiado)
- packwiz-installer @ 7420866: `src/main/kotlin/link/infra/packwiz/installer/DownloadTask.kt`, `UpdateManager.kt` — https://github.com/packwiz/packwiz-installer
- packwiz @ ef87d96: `core/index.go`, `core/pack.go`, `modrinth/export.go`, `curseforge/export.go`, `cmdshared/downloadutil.go` — https://github.com/packwiz/packwiz
- Modrinth App (modrinth/code @ badbf9f): `packages/app-lib/src/api/pack/install_mrpack.rs`, `packages/app-lib/src/api/instance/export_mrpack.rs`, `packages/app-lib/src/api/instance/synced_options/game_options/pack_updates.rs` — https://github.com/modrinth/code
- Prism Launcher (develop @ e4299e1): `launcher/modplatform/modrinth/ModrinthInstanceCreationTask.cpp`, `launcher/modplatform/helpers/OverrideUtils.cpp`, `launcher/modplatform/modrinth/ModrinthPackExportTask.cpp`, `launcher/ui/dialogs/ExportInstanceDialog.cpp` — https://github.com/PrismLauncher/PrismLauncher
- itzg/mc-image-helper @ 6342639: `modrinth/ModrinthPackInstaller.java`, `modrinth/InstallModrinthModpackCommand.java`, `modrinth/ModrinthApiPackFetcher.java`, `files/Manifests.java`, `curseforge/CurseForgeInstaller.java` — https://github.com/itzg/mc-image-helper
- mrpack-install (trunk @ 480907b): `update/command.go`, `update/packstate/packstate.go`, `update/backup/backup.go`, `modrinth/mrpack/overrides.go` — https://github.com/nothub/mrpack-install
- mrpack4server @ eb631a8: `installer/MrPackInstaller.java`, `util/Constants.java` — https://github.com/Patbox/mrpack4server
- NeoForge FancyModLoader @ cb29eec: `loader/src/main/java/net/neoforged/fml/config/ConfigTracker.java`, `fml/loading/FMLConfig.java` — https://github.com/neoforged/FancyModLoader
- Panel de Pterodactyl v1.15.1: `routes/api-client.php`, `app/Http/Controllers/Api/Client/Servers/FileController.php`, `app/Http/Requests/Api/Client/Servers/Files/*.php`, `config/pterodactyl.php` — https://github.com/pterodactyl/panel/tree/v1.15.1
- Pterodactyl Wings v1.13.3: `router/router_server_files.go`, `server/filesystem/filesystem.go`, `server/filesystem/compress.go`, `config/config.go` — https://github.com/pterodactyl/wings/tree/v1.13.3
- Config por defecto de LuckPerms: https://github.com/LuckPerms/LuckPerms/blob/master/fabric/src/main/resources/luckperms.conf

Sonda (2026-10-09, sin credenciales)
- `GET https://api.modrinth.com/v2/search?facets=[["project_type:modpack"]]&index=downloads`, luego `GET /v2/project/{slug}/version` y el archivo primario de la última versión de: fabulously-optimized, cobblemon-fabric, cobbleverse, better-mc-fabric-bmc2, better-mc-forge-bmc4, prominence-2-fabric, the-pixelmon-modpack, battlearmorytacz, stormforge (craftoria, create-astral y monifactory: 404 en Modrinth). Solo se leyeron metadatos del zip.

Contexto del repositorio (local)
- `docs/plan.md`, `docs/roadmap.md` (i-7985ee-7352b3, i-7985ee-5c7506), `docs/decisions.md` (d-7985ee-c8a15d, d-7985ee-5739fc), `docs/research/2026-10-09-importar-modpacks.md` (D3, D4), `docs/research/2026-10-resolucion-y-formatos.md`, `docs/research/2026-10-operacion-pterodactyl.md`, `docs/references.md`, `src/library/mrpack.py`, `src/model/modpack/project.py`, `src/library/api/client/pterodactyl.py`, `docker/docker-compose.yaml`.
