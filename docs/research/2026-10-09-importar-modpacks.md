# Importar modpacks existentes a un bundle: formatos, "importar por código" y factibilidad

**Fecha:** 2026-10-09 · **Método:** lectura de documentación oficial, términos y código fuente de los launchers (solo para entender su comportamiento), más dos sondas reales sin credenciales.

**Origen:** observaciones de un tester del panel (2026-10-08), que pidió importar modpacks como CurseForge, Modrinth y Prism.

Alcance: qué necesitaría modpack-creator para importar un `.mrpack` de Modrinth, un "código" de Modrinth, un `.zip` de modpack de CurseForge y su código para compartir, y una exportación de instancia de Prism Launcher (y el "importar por código" de Prism). Cada afirmación cita una fuente primaria (documentación oficial, términos o el código fuente de los propios launchers). Lo que no se contrastó con una de ellas está marcado como **SUPUESTO**. El código de los launchers (Prism es GPL-3.0; el monorepo de Modrinth mezcla AGPL/GPL/LGPL) se **leyó solo para entender su comportamiento**; nada de él se debe copiar.

Se hicieron dos sondas reales el 2026-10-09 con identificadores inventados y sin credenciales (resultados en §2). No se usó ningún secreto.

## Resumen

- Factible hoy sin credenciales nuevas: la subida de un `.mrpack`, un modpack de Modrinth por URL/slug/ID de versión (el único "importar por código" estable) y un `.zip` de instancia de Prism.
- Un `.zip` de CurseForge necesita una clave de la API de CurseForge (solicitud a Overwolf, por dueño, nunca compartida), y sus términos prohíben "save or cache any data obtained through the API".
- El código de perfil de CurseForge y el enlace de instancia compartida de Modrinth dependen de endpoints no documentados; el "importar por código" de Prism no existe como tal.
- Recomendación: un solo pipeline de importación con un lector por formato; primer paso `.mrpack` + URL/ID de Modrinth.
- Quedan seis decisiones abiertas para el dueño (D1–D6, en §6).

---

## 0. Qué tiene ya el repositorio (reutilizable)

| Pieza | Dónde | Reutilización para importar |
|---|---|---|
| Constantes y chequeos de `.mrpack`: `INDEX`, `ALLOWED_DOMAINS` (= los cuatro de la especificación), `DEPENDENCY_KEYS` (loader → `fabric-loader`/`quilt-loader`/`forge`/`neoforge`), `FOLDERS`, `_unsafe_path` (rechaza `..`, `/` o `\` iniciales y letras de unidad: exactamente la regla de la especificación), `validate_index` (exige sha1+sha512, dominios, valores de `env`) | `src/library/mrpack.py` | Validar un índice subido con la misma función que valida las exportaciones; invertir `DEPENDENCY_KEYS` para leer el loader; `_unsafe_path` para cada ruta de `files[]` y cada miembro de `overrides/` |
| Búsqueda por hash en Modrinth: `files_hash_post_many(hashes, algorithm)` → `POST /version_files` (sha1 o sha512) | `src/library/api/client/modrinth.py` | El núcleo de todo importador: el mrpack da sha512, CurseForge da sha1, los jars de Prism se hashean localmente |
| Pipeline de importación de servidor: identificado por hash → el proyecto se agrega como del admin; desconocido → `read_mod_metadata` (`fabric.mod.json`/`mods.toml`… del jar) → `_candidates` (primero slug, luego búsqueda) → el admin confirma; nada encontrado → `LocalFile` | `src/services/pterodactyl.py` `import_server`, `src/library/jarmeta.py`, `src/library/scan.py` | El mismo resultado de tres vías para los archivos importados. Extraer de `import_server` el paso "hash → identificado / candidatos / local" para que lo alimente una fuente de archivos distinta de un escaneo de Pterodactyl |
| `jarmeta._read_capped` | `src/library/jarmeta.py` | El mismo patrón de lectura con tope para leer miembros de un zip subido |
| `LocalFile(filename, sha512, sha1, size)`: **solo metadatos**; d-7985ee-c8a15d: "una actualización lo deja intacto en el servidor, y nunca va en un .mrpack" | `src/library/scan.py`, `docs/decisions.md` | Sirve para un jar que ya está en un servidor. **No** sirve para un jar que solo existe dentro de un zip subido: habría que guardar los bytes en alguna parte para que lleguen alguna vez a un servidor (decisión abierta D3) |

Nada en el modelo de bundle guarda archivos de configuración ("overrides"); `grep` de `overrides`/GridFS en `src/` no encuentra nada (decisión abierta D4).

---

## 1. El formato `.mrpack` (Modrinth)

Fuente: la especificación de Modrinth, artículo 8802351 de support.modrinth.com ("Updated yesterday" al leerlo).

**Contenedor.** "When stored on disk, the modpack MUST be in ZIP format (MIME type `application/x-modrinth-modpack+zip`), using the `.mrpack` extension. The main metadata of the modpack MUST be stored with UTF-8 encoding at `modrinth.index.json` in the root of the zip."

**Campos de `modrinth.index.json`.**

| Campo | Especificación |
|---|---|
| `formatVersion` | número; "The current value at the time of writing is `1`" |
| `game` | "The only available type is `minecraft`" |
| `versionId` | identificador único de esta versión del pack (cadena) |
| `name` | título legible |
| `summary` | descripción corta, opcional |
| `files[]` | lista de archivos a descargar (abajo) |
| `dependencies` | ids → cadenas de versión; ids soportados: `minecraft`, `forge`, `neoforge`, `fabric-loader`, `quilt-loader`. "We may add other supported dependency IDs at any time. Please ensure your application handles this." |

**Entradas de `files[]`.**

| Campo | Especificación |
|---|---|
| `path` | "destination path of this file, relative to the Minecraft instance directory", p. ej. `mods/MyMod.jar`. Seguridad: "If you implement an 'import ZIP' feature or similar, make sure this field doesn't exit the Minecraft instance directory … make sure it doesn't contain `..` or start with a drive name (i.e., `[A-Z]:/`, `[A-Z]:\`, `/`, and `\`)." |
| `hashes` | "This MUST contain the SHA1 hash and the SHA512 hash. Other hashes are optional." |
| `env` | opcional; `client` y `server`, cada uno `required` / `optional` / `unsupported`. La especificación no dice qué significa su ausencia (el repositorio ya trata la ausencia como "instalar", `client_env`). |
| `downloads` | "An array containing HTTPS URLs where this file may be downloaded." Las redirecciones 3xx "MUST be followed … (we recommend three minimum)" |
| `fileSize` | entero, bytes |

**Overrides.** Carpetas opcionales en la raíz del zip: `overrides/` (capa base), `server-overrides/` (se aplica después de `overrides`, sobrescribe conflictos), `client-overrides/` (igual, para clientes). Para una herramienta de servidor, el árbol efectivo del servidor es `overrides` y luego `server-overrides`; `client-overrides` solo importa para volver a exportar un `.mrpack`.

**Dominios de descarga permitidos.** "When uploading to Modrinth, the pack is validated so that only URIs from the following domains are allowed": `cdn.modrinth.com`, `github.com`, `raw.githubusercontent.com`, `gitlab.com`. "Other platforms implementing the specification SHOULD have a defined set of whitelisted domains." Por tanto, un `.mrpack` que **no** se subió a Modrinth (p. ej. exportado por un launcher) puede traer otros dominios. Desde 2022-05-28 `edge.forgecdn.net` y `media.forgecdn.net` están explícitamente fuera de la especificación (noticia de Modrinth "modpack-changes"); los mods que solo están en CurseForge van a `overrides/` como jars, con una licencia de redistribución o el permiso del autor. La exportación `.mrpack` de Prism hace lo mismo: todo lo que no puede resolver en Modrinth por sha512 se escribe bajo `overrides/` (Prism `ModrinthPackExportTask.cpp`, solo lectura).

**Mapear cada archivo a un proyecto/versión de Modrinth.** El índice ya trae sha1 y sha512, así que no hace falta descargar ningún jar: un `POST /version_files` con `{"hashes": [...sha512...], "algorithm": "sha512"}` devuelve "a map from hashes to versions" (docs.modrinth.com, "Get versions from hashes"; `algorithm` es `sha1` o `sha512`). Cada versión trae `project_id` y `files[]`; se empareja el archivo por hash. Los archivos que no están en el mapa están alojados en GitHub/GitLab (URL permitida, válida en una reexportación) o, en un pack que no es de Modrinth, en cualquier otro host. Los jars bajo `overrides/mods/` se deben hashear desde el zip y buscar igual (bien pueden estar en Modrinth; Prism pone ahí los que no resolvió). Límite de tasa: 300 peticiones/min por IP y se exige un `User-Agent` único (docs.modrinth.com/api).

---

## 2. "Importar por código"

### 2.1 Modrinth

Pueden significar dos cosas distintas:

**(a) Un modpack publicado, por URL / slug / ID: estable, público, documentado.** Un proyecto modpack de Modrinth lista versiones (`GET /project/{id|slug}/version`, ya envuelto como `project_versions`), cada una con un `.mrpack` primario en `cdn.modrinth.com`. Importar "por código" aquí es: resolver el ID del proyecto o de la versión, descargar el `.mrpack` y aplicar §1. Sin credenciales. Los packs no listados funcionan por ID de versión (**SUPUESTO**: una versión no listada se obtiene por ID sin autenticación, como los proyectos no listados; verificar con la documentación de la API antes de confiar en ello).

**(b) "Shared instances" (instancias compartidas) de la Modrinth App: reales, recientes, no es una API pública.**
- Lanzadas el 2026-07-24 (noticia de Modrinth "Share instances with friends"; PR modrinth/code#6569 "feat: instance sharing thru shared-instances service", fusionado el 2026-07-24; cierra el issue #3259). Requiere iniciar sesión en una cuenta de Modrinth; se invita por nombre de usuario o por enlace. "Shared instances are not reviewed like published Modrinth projects."
- Formato del enlace, según el código de la app: `https://modrinth.com/share/{inviteId}` y enlace profundo `modrinth://share/{inviteId}` (`apps/app-frontend/.../use-shared-instance-invite-link.ts`, `apps/frontend/.../SharedInstanceInviteOpenInAppModal.vue`).
- Backend: un servicio aparte, `https://shared-instances.modrinth.com` (`apps/app-frontend/src/config.ts`), cuyo código de servidor **no** está en el monorepo público (solo lo referencian clientes). El api-client LGPL (`packages/api-client/src/modules/shared-instances/`) llama a `GET /v1/invites/{id}` con `skipAuth: true`, y a `GET /v1/instances/{id}`, `/v1/instances/{id}/versions[/{n}]` con autenticación. Una invitación devuelve nombre, icono, `game_version`, `loader_version`, managers; una versión de instancia devuelve `modrinth_ids`, `external_files[{file_name, file_type, url, file_size, metadata[{path, hash}]}]`, `modpack_id`, `game_version`, `loader`, `loader_version`.
- Los tokens de acceso personal tienen scopes `SHARED_INSTANCE_READ` etc. (`apps/labrinth/src/models/v3/pats.rs`).
- Sonda 2026-10-09 (ids falsos, sin autenticación): `GET /v1/invites/AAAAAAAA` → 404 (la ruta responde sin autenticación); `GET /v1/instances/AAAAAAAA/versions` → **401** (el contenido exige un login de Modrinth).
- No está en docs.modrinth.com. **No es una API pública estable**: sin documentar, de tres meses de antigüedad, necesita el token de Modrinth del usuario que importa y (**SUPUESTO**) ese usuario debe haberse unido a la instancia mediante la invitación.

### 2.2 CurseForge

- Códigos de perfil: "In order to generate a profile code please verify that you are logged in to your CurseForge account"; "the code is only available for 7 days"; los destinatarios importan "using either … the ZIP file directly or by entering the profile code" (soporte de CurseForge, "Sharing Modpacks/Custom Profiles", modificado el 2026-07-07).
- Forma del enlace `https://www.curseforge.com/minecraft/share/{code}`; la CurseForge App lo resuelve contra `https://api.curseforge.com/v1/shared-profile/{code}`, que devuelve el perfil como un zip de modpack de CurseForge (descripción del PR #5741 de Prism, 2026-06-28: "The link even ended up working without an API key, and is a direct download to the custom modpack ZIP").
- El endpoint **no** está en la documentación de la Core API de CurseForge (docs.curseforge.com/rest-api: no hay endpoint shared-profile ni de códigos para compartir). Mantenedor de Prism en #5741: "It does not appear that the API is documented, therefore using it may be a grey area"; otro mantenedor aprobó el PR como "gray as it is just an API call to download the file"; antes, en #3649: "The Curseforge doesn't provide an API for them. We will not consider adding them if they are behind an API that is not public."
- Sonda 2026-10-09 (sin clave): `GET /v1/shared-profile/AAAAAAAA` → **404 application/json**, mientras que `GET /v1/mods/238222` → **403**. Coherente con que el endpoint de compartir no exige clave.
- Aun con el zip en la mano, su `manifest.json` lista pares `projectID`/`fileID` que todavía necesitan la Core API con clave para resolverse (§3). **Neto: los códigos de CF no añaden ninguna capacidad más allá del zip de CF, y añaden una dependencia no documentada.**

### 2.3 Prism Launcher

- Prism no tiene un sistema propio de códigos para compartir. El "importar por código en desarrollo" es el **PR PrismLauncher/PrismLauncher#5741 "Add support for importing CurseForge's 7-day share links"**: abierto, etiquetas `status: needs discussion`, `priority: low`, última actualización 2026-08-04; solo reescribe el enlace de compartir a la URL `shared-profile` de arriba e importa el zip.
- Las peticiones de importar códigos de CF se cerraron: #3416 (not planned, 2025-02-13), #3858 (duplicado), #3649 (cerrado 2025-04-17, comentario arriba), #6188 (not planned, 2026-09-28). Relacionado y abierto: #882 "Unlisted Modrinth pack support".
- Así que el "importar por código" de Prism = el endpoint no documentado de CurseForge; no es una API de Prism.

**Conclusión sobre los códigos:** el único "código" estable y público es el ID o la URL de un proyecto/versión de Modrinth publicado. Los enlaces de shared instances de Modrinth y los códigos de perfil de CurseForge son APIs de servicios privados, sin documentar; Prism no tiene nada propio.

---

## 3. `.zip` de modpack de CurseForge

**Estructura.** Oficial: "Directly inside the .zip file is the *manifest.json* and a folder labeled *overrides*"; las configuraciones, scripts, mundos, recursos y mods colocados a mano van dentro de `overrides` (artículo 9000197908 del soporte de CurseForge, modificado el 2026-02-03). **No hay un esquema oficial para `manifest.json`**; los campos de abajo son como los lee el cargador de Prism (`launcher/modplatform/flame/PackManifest.cpp`) y como los emite el exportador de Prism (`FlamePackExportTask.cpp`):

| Campo | Significado |
|---|---|
| `manifestType` | debe ser `"minecraftModpack"` |
| `manifestVersion` | debe ser `1` |
| `minecraft.version` | versión de Minecraft |
| `minecraft.modLoaders[]` | `{id, primary}`; `id` como `forge-47.2.0`, `fabric-0.15.x`, `quilt-…`, `neoforge-21.1.x`. Prism quita el prefijo; para NeoForge en 1.20.1 también quita un `1.20.1-` |
| `minecraft.recommendedRam` | opcional |
| `name`, `version`, `author` | metadatos |
| `files[]` | `{projectID, fileID, required}` (`required` por defecto es true) |
| `overrides` | nombre de carpeta, por defecto `"overrides"` (Prism rechaza una que se salga del directorio de preparación) |

`files[]` **no trae hashes, nombres ni URL**, así que nada se puede resolver sin la Core API. La carpeta de destino también sale de la API: el `classId` del proyecto (6 mods, 12 paquetes de recursos, 17 mundos, 6552 shaders, 6945 datapacks, 4471 modpacks; Prism `FlameAPI.cpp`).

**Resolver con la Core API.**
- Base `https://api.curseforge.com`; cabecera de autenticación `x-api-key`. "The API key can be generated in the CurseForge for Studios developer console", pensada para estudios de juegos. Terceros: "If you're running a 3rd party modding service … Click here to apply for a key" (un formulario de monday.com enlazado desde docs.curseforge.com/rest-api); "Any registered account which is not a game developer and did not request a key, will be deleted". La aprobación es manual (**SUPUESTO** sobre el plazo; no se indica).
- `POST /v1/mods/files` con cuerpo `{"fileIds": [...]}` → objetos `File`: `modId`, `fileName`, `fileLength`, `hashes[{value, algo}]` con **HashAlgo `1=Sha1, 2=Md5`** (sin sha512), `fileFingerprint` (la variante murmur2 de CF), `downloadUrl` (cadena; vacía para mods con distribución bloqueada, ver abajo), `gameVersions`, `isServerPack`, `serverPackFileId`. `POST /v1/mods` `{"modIds": [...]}` → `Mod` con `classId`, `allowModDistribution`. Ambos son POST de solo lectura (irían en una lista del estilo `IDEMPOTENT_POSTS`, según d-7985ee-05d032).
- `POST /v1/fingerprints` (y `/matches`, difuso) emparejan fingerprints murmur2 de archivos locales; solo hace falta para identificar jars sueltos en CurseForge, no para resolver un manifiesto.

**sha1 → Modrinth.** Sí, funciona y es lo que hace Prism: cuando un archivo de CF no tiene `downloadUrl`, Prism toma su sha1 de la API de CF y le pregunta a Modrinth `POST /version_files` con `algorithm: "sha1"`, usando el archivo de Modrinth si lo encuentra (`FileResolvingTask.cpp`, ajuste `FallbackMRBlockedMods`). Solo coincide cuando el autor subió jars idénticos byte a byte a ambos sitios. Los archivos sin coincidencia recaen en el flujo existente metadatos→candidatos (que necesita los bytes del jar: o `downloadUrl` o una subida del usuario).

**`allowModDistribution`.** Texto del esquema: "Is mod allowed to be distributed" (`boolean | null`). Introducido con la API de 2022 como la exclusión voluntaria del autor de la distribución por terceros (la noticia de Modrinth del 2022-05-28 cuenta la disputa; el anuncio original de CurseForge/Overwolf en Medium devolvió 403 al descargador, no se verificó). Efecto en la práctica: `downloadUrl` está vacía para esos archivos y los launchers mandan al usuario al sitio web a descargar a mano (Prism arma un diálogo de "Blocked mods" con `{websiteUrl}/download/{fileId}` cuando `downloadUrl` está vacía; `FlameInstanceCreationTask.cpp`). Implicación aquí: un mod así solo puede convertirse en (1) un proyecto de Modrinth por la coincidencia de sha1, o (2) un jar que el admin sube a mano.

**Términos (CurseForge 3rd Party API Terms and Conditions, actualizados el 2024-08-14).**
- Clave: "unique API Key … non-transferable and may not be shared with any third party". Por tanto nunca se puede versionar ni empaquetar; cada dueño que autoaloje tendría que solicitar y configurar la suya.
- Las restricciones incluyen "(c) disassemble, reverse engineer …", "(e) conceal your identity or geographic location when accessing the API, including … proxy server or VPN", y "**(e) save or cache any data obtained through the API or SDK**".
- "External App: … provides substantial functionality above and beyond the functionality and materials provided through the Platform API": un gestor de modpacks plausiblemente califica (**SUPUESTO**: Overwolf decide al evaluar la solicitud).
- Impacto: guardar en MongoDB datos derivados de CF (nombres de archivo, sha1, `downloadUrl`, metadatos de proyecto) parece "save or cache". Un diseño conforme usa los datos de CF solo de forma transitoria, para encontrar el equivalente en Modrinth, y persiste únicamente lo que vino del archivo del usuario (projectID/fileID) y de Modrinth. Si incluso la resolución transitoria más conservar los IDs de CF es aceptable es una **lectura legal, no verificada** (decisión abierta D2).

**Mods que solo están en CurseForge.** Opciones, ninguna automática: (a) el admin sube el jar → archivo local (necesita almacenar bytes, D3); (b) si hay `downloadUrl` el servidor podría descargarlo, pero persistir la URL o el archivo es la cuestión de "save or cache", y nunca puede aparecer en un `.mrpack` exportado (forgecdn no está permitido); (c) informarlos como no resueltos y detenerse. Recomendación: (c) por defecto, (a) como vía manual.

---

## 4. Exportación de instancia de Prism Launcher (`.zip`)

Del código fuente de Prism (solo lectura):
- **Detección** (`InstanceImportTask.cpp`): un zip es un pack de Modrinth si tiene `modrinth.index.json`, de CurseForge si tiene `manifest.json`, de Technic si tiene `bin/modpack.jar`/`bin/version.json`, y una instancia de MultiMC/Prism si algún miembro se llama `instance.cfg` (su carpeta pasa a ser la raíz: las exportaciones suelen tener una carpeta de nivel superior). Nótese el comentario: `manifest.json` es común y puede aparecer dentro de overrides, así que hay que comprobar primero los demás.
- **`instance.cfg`**: ajustes INI (nombre, Java, memoria…). No hace falta más que el nombre.
- **`mmc-pack.json`**: `{"formatVersion": 1, "components": [{uid, version, cachedVersion, important, …}]}` (`PackProfile.cpp`). UIDs de componentes: `net.minecraft` (versión del juego), `net.fabricmc.fabric-loader`, `org.quiltmc.quilt-loader`, `net.minecraftforge`, `net.neoforged` (`Component.cpp`; `net.fabricmc.intermediary` también aparece para Fabric: **SUPUESTO** de memoria, ignorar los UIDs desconocidos).
- **Directorio del juego**: `minecraft/` o el heredado `.minecraft/` (`MinecraftInstance::gameRoot`: `.minecraft` solo si existe y `minecraft` no).
- **Valores por defecto de la exportación** (`ExportInstanceDialog.cpp`): ignora `logs`, `crash-reports`, `.cache`, `.fabric`, `.quilt`, `.DS_Store`, `Thumbs.db`, más lo que el usuario haya desmarcado (`.packignore`). Los jars de mods **sí** se incluyen; los mundos (`saves/`) también salvo que se desmarquen: las exportaciones pueden ser grandes.
- **Metadatos** `mods/.index/<slug>.pw.toml` (también `resourcepacks/.index`; los shaderpacks dejaron de usar `.index`, PR #4658). Formato packwiz (referencia packwiz.infra.link): `name`, `filename`, `side`, `download{url, hash-format, hash}`, opcional `option{optional, default, description}`, `update.modrinth{mod-id, version}` o `update.curseforge{file-id, project-id}`; `hash-format` ∈ `sha256 | sha512 | sha1 | md5 | murmur2`. Prism añade `download.mode` (`"url"`, o `"metadata:curseforge"` sin URL) y claves `x-prismlauncher-*` (loaders, mc-versions, release-type, version-number, dependencies, lock-update) (`Packwiz.cpp`). Prism guarda sha512 para los mods de Modrinth y sha1/md5/murmur2 para los de CurseForge (`ModIndex.cpp`).
- El `tomllib` de la biblioteca estándar de Python 3.12 los parsea; ninguna dependencia nueva.

**Mejor mapeo:** ignorar el `.pw.toml` para identificar y hashear los propios jars (sha512 → `/version_files`), exactamente como hace el escaneo del servidor. Usar el `.pw.toml` solo como pistas: un `update.modrinth.version` confirma una coincidencia; una entrada `update.curseforge` marca un jar como "de CurseForge" para el informe. Una exportación de Prism puede carecer de jars que el usuario excluyó; entonces el `.pw.toml` con una `version` de Modrinth (o un sha512) sigue bastando para identificarlo sin bytes.

Prism también puede exportar `.mrpack` (PR #904, fusionado; jars sin resolver en `overrides/`) y zip de CurseForge (`FlamePackExportTask.cpp`, usa fingerprints de CF, lo que necesita una clave al exportar). Esos casos caen en §1 y §3.

---

## 5. Seguridad de los zips subidos por el usuario

- **Path traversal ("zip slip")**: la propia especificación de `.mrpack` exige rechazar `..`, letras de unidad y `/`/`\` iniciales en `files[].path`; lo mismo vale para cada miembro bajo `overrides/` y para el valor `overrides` de un manifiesto de CF. El `zipfile.extract` de Python sanea los nombres, pero su documentación advierte: "Never extract archives from untrusted sources without prior inspection" (docs.python.org 3.12 zipfile). Lo más simple: no extraer nunca a disco; leer los miembros en memoria con topes y reutilizar `_unsafe_path`.
- **Bombas zip**: la documentación de Python lista "decompression bombs (aka ZIP bomb) … can cause disk volume exhaustion" en *Decompression pitfalls*. `ZipInfo.file_size` es solo "Size of the uncompressed file", declarado por el propio archivo, así que se debe poner el tope sobre los bytes realmente leídos (como hace `jarmeta._read_capped`), más topes en cantidad de miembros, tamaño por miembro, tamaño descomprimido total y razón de compresión. Los jars anidados (abiertos por `read_mod_metadata`) necesitan los mismos topes (ya los tienen).
- **Tamaño de la subida**: OWASP File Upload Cheat Sheet: "ZIP files are not recommended since they can contain all types of files"; fijar límites de tamaño que consideren el contenido descomprimido; generar nombres en el servidor; guardar fuera del webroot. `request.form()` de Starlette tiene por defecto `max_files=1000, max_fields=1000, max_part_size=1 MiB` (solo campos: "uploaded files are spooled to temporary storage and are not limited by max_part_size"); un tope sobre el cuerpo completo necesita `max_body_size` / `RequestBodyLimitMiddleware` (starlette.dev/requests). **SUPUESTO**: el Starlette fijado por fastapi 0.142.2 tiene `max_body_size`; comprobarlo antes de confiar en ello, y si no, poner el tope en el proxy inverso y durante la lectura en flujo.
- Punto de referencia: la Modrinth App limita un archivo externo de instancia compartida a 500 MiB (`MAX_SHARED_INSTANCE_EXTERNAL_FILE_SIZE` en `packages/app-lib/src/install/shared_instance.rs`).
- Otros: se deben rechazar los miembros que sean enlaces simbólicos (atributos externos del zip); JSON/TOML se parsean con topes de tamaño; todo texto importado que se muestre en el panel es texto de terceros (escaparlo, como los changelogs, d-7985ee-d88e90). La importación debe ser solo para operadores mediante `operator_only` y tener su fila en `## Acciones y roles`.

---

## 6. Recomendación

| Fuente | ¿Factible ahora, sin credenciales nuevas? | Notas |
|---|---|---|
| Subida de `.mrpack` | **Sí** | El índice ya trae sha1+sha512 → un `POST /version_files` por cada ≤N hashes; el loader sale de `dependencies` (inverso de `DEPENDENCY_KEYS`); los jars de overrides se hashean localmente. Mejor primer paso. |
| Modpack de Modrinth por URL/slug/ID de versión | **Sí** | API pública documentada + CDN; luego la vía del `.mrpack`. Es el único "importar por código" estable. |
| `.zip` de instancia de Prism | **Sí** | Hashear los jars localmente (igual que el escaneo del servidor); `mmc-pack.json` da MC + loader; `.pw.toml` como pistas. Las subidas más grandes (jars, quizá mundos). |
| `.mrpack` / zip de CF exportado por Prism | Igual que sus formatos | — |
| `.zip` de CurseForge | **Necesita una clave de la API de CurseForge** (solicitud a Overwolf, por dueño, nunca compartida) | fileID → sha1 (API) → Modrinth por sha1. Los términos prohíben "save or cache any data obtained through the API": mantener los datos de CF transitorios. Mods solo en CF o con distribución bloqueada: informar / subida manual. |
| Código de perfil de CurseForge | Técnicamente sí (no se observó clave para la descarga), **no recomendado** | Endpoint no documentado; los códigos caducan en 7 días; el zip igual necesita la clave para resolverse. |
| Enlace de shared instance de Modrinth | **No (no es una API estable)** | Servicio privado no documentado, necesita el token de Modrinth del usuario y (supuesta) membresía. Podría reconsiderarse si Modrinth lo documenta. |
| "Importar por código" de Prism | **No existe** | Es el PR #5741 envolviendo el endpoint no documentado de CurseForge; abierto, prioridad baja. |

**Cómo encaja en el código.** Un solo pipeline de importación, varios lectores:
1. Un lector por formato convierte la subida en `(minecraft_version, loader, loader_version, files[path, sha1?, sha512?, size, bytes?, hints], overrides[path → bytes])`: funciones puras en `src/library/` junto a `mrpack.py`, con los topes del zip en un único helper compartido.
2. Un paso de servicio (extraído de `import_server`) resuelve los archivos: `files_hash_post_many` por lotes (sha512 cuando existe, si no sha1); identificado → `PackProject(origin=admin)`; desconocido con bytes → `read_mod_metadata` → `_candidates` → el admin confirma; desconocido sin candidatos → archivo local.
3. Se crea un bundle nuevo a partir del MC/loader del pack (como la deducción de `bundle_for_server`), o la importación se fusiona en uno existente.

**Decisiones abiertas para el dueño** (cada una cambia el modelo o la superficie legal):
- **D1. Alcance del primer corte**: solo `.mrpack` + URL/ID de Modrinth (recomendado), o también el zip de Prism.
- **D2. CurseForge**: omitirlo; o agregar una `CURSEFORGE_API_KEY` opcional (función apagada sin ella, coherente con que la configuración falle cerrada solo para las variables obligatorias) tras leer los términos sobre "save or cache"; y si alguna vez se aceptan códigos para compartir.
- **D3. Bytes de los jars desconocidos**: hoy `LocalFile` son metadatos de un jar que ya está en un servidor. Un jar importado no tiene dónde vivir. Opciones: rechazarlos/informarlos; guardar solo metadatos y exigir que el jar se ponga a mano en el servidor; o agregar almacenamiento de archivos (GridFS / volumen) con soporte de despliegue.
- **D4. Overrides (configs, kubejs, defaultconfigs…)**: ignorarlos e informar; guardarlos como "overrides" del bundle para desplegarlos y reexportarlos; o solo listarlos.
- **D5. `env` de un `.mrpack`**: la build es dueña del lado servidor (d-7985ee-f0c7a6); decidir si `env.server: unsupported` pasa a ser la elección inicial "excluir del servidor" o se ignora.
- **D6. Límites de subida**: p. ej. 50 MiB para `.mrpack`/zip de CF (no se esperan jars), más para los zips de Prism (jars; omitir `saves/`), además de topes de cantidad de miembros y de razón de compresión.

---

## Fuentes

Modrinth
- Especificación de `.mrpack`: https://support.modrinth.com/en/articles/8802351-modrinth-modpack-format-mrpack
- Get versions from hashes (`POST /version_files`): https://docs.modrinth.com/api/operations/versionsfromhashes/
- Visión general de la API (límite de tasa, User-Agent): https://docs.modrinth.com/api/
- Modpack changes / retiro del CDN de CurseForge (2022-05-28): https://modrinth.com/news/article/modpack-changes
- Anuncio de shared instances (2026-07-24): https://modrinth.com/news/article/shared-instances/
- Issue "Shareable instances/profiles": https://github.com/modrinth/code/issues/3259
- PR "instance sharing thru shared-instances service": https://github.com/modrinth/code/pull/6569
- Código (solo lectura): https://github.com/modrinth/code/tree/main/packages/api-client/src/modules/shared-instances ,
  https://github.com/modrinth/code/blob/main/apps/labrinth/src/models/v3/pats.rs ,
  https://github.com/modrinth/code/blob/main/packages/app-lib/src/install/shared_instance.rs ,
  https://github.com/modrinth/code/blob/main/apps/app-frontend/src/config.ts

CurseForge
- Documentación de la Core API (autenticación, endpoints, esquemas, HashAlgo, allowModDistribution): https://docs.curseforge.com/rest-api/
- Formulario de solicitud de clave para terceros (enlazado desde la documentación): https://forms.monday.com/forms/dce5ccb7afda9a1c21dab1a1aa1d84eb?r=use1
- 3rd Party API Terms and Conditions (actualizados 2024-08-14): https://support.curseforge.com/en/support/solutions/articles/9000207405-curse-forge-3rd-party-api-terms-and-conditions
- Estructura del zip de modpack: https://support.curseforge.com/support/solutions/articles/9000197908
- Compartir modpacks / códigos de perfil (7 días): https://support.curseforge.com/en/support/solutions/articles/9000197912
- Términos de CurseForge for Studios: https://docs.curseforge.com/docs/legal/terms-of-use/

Prism Launcher (GPL-3.0, solo lectura)
- PR #5741 enlaces para compartir de CurseForge: https://github.com/PrismLauncher/PrismLauncher/pull/5741
- Issues #3649, #6188, #3416, #3858, #882: https://github.com/PrismLauncher/PrismLauncher/issues/3649 ,
  https://github.com/PrismLauncher/PrismLauncher/issues/6188 , https://github.com/PrismLauncher/PrismLauncher/issues/3416 ,
  https://github.com/PrismLauncher/PrismLauncher/issues/3858 , https://github.com/PrismLauncher/PrismLauncher/issues/882
- Código: `launcher/InstanceImportTask.cpp`, `launcher/modplatform/flame/{PackManifest,FileResolvingTask,FlameInstanceCreationTask,FlameAPI,FlamePackExportTask}.cpp`,
  `launcher/modplatform/modrinth/{ModrinthAPI,ModrinthPackExportTask}.cpp`, `launcher/modplatform/packwiz/Packwiz.cpp`,
  `launcher/modplatform/ModIndex.cpp`, `launcher/minecraft/{PackProfile,MinecraftInstance,Component}.cpp`,
  `launcher/ui/dialogs/ExportInstanceDialog.cpp` — https://github.com/PrismLauncher/PrismLauncher/tree/develop/launcher

Packwiz
- Referencia del `.pw.toml` de un mod: https://packwiz.infra.link/reference/pack-format/mod-toml/

Seguridad
- `zipfile` de Python 3.12 (advertencias de extract, decompression pitfalls): https://docs.python.org/3.12/library/zipfile.html
- OWASP File Upload Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html
- Requests de Starlette (límites de form, `max_body_size`): https://starlette.dev/requests/

Sondas reales (2026-10-09, ids falsos, sin credenciales)
- `GET https://api.curseforge.com/v1/shared-profile/AAAAAAAA` → 404 JSON; `GET https://api.curseforge.com/v1/mods/238222` → 403.
- `GET https://shared-instances.modrinth.com/v1/invites/AAAAAAAA` → 404; `GET …/v1/instances/AAAAAAAA/versions` → 401.
