# Importar CurseForge y Prism (segunda vuelta) y deuda del panel

**Fecha:** 2026-10-09 · **Rama leída:** `fix/tester-feedback` · **Método:** documentación oficial,
términos, código fuente de herramientas y launchers (solo lectura, para entender su comportamiento:
Prism es GPL-3.0, packwiz MIT, ferium MPL-2.0, mc-image-helper MIT; **no se copió nada**) y lectura
del código del repositorio. Sin credenciales, sin sondas nuevas. Lo no contrastado con una fuente
primaria va marcado **SUPUESTO**.

Extiende `docs/research/2026-10-09-importar-modpacks.md` (no repite lo que ya dice: formato del
manifiesto de CF, estructura del zip, `allowModDistribution`, cláusula "save or cache", detección
de formatos de Prism, `mmc-pack.json`, `.pw.toml`, seguridad de zips).

## Resumen

- **Hecho nuevo y grande:** desde el **2026-07-16** CurseForge exige una API key también para bajar
  archivos de su CDN (`edge.forgecdn.net`); sin clave, 401. El motivo declarado es atribuir cada
  descarga a "each integration [...] with its own key" (blog de CurseForge, 2026-06-10).
- **Patrón de las herramientas autoalojadas:** Prism, packwiz, ferium y (desde 2026-07-16)
  itzg/docker-minecraft-server **traen una clave del proyecto incrustada** y permiten que el
  usuario la reemplace por la suya. Prism y packwiz piden explícitamente que un fork o derivado
  pida su propia clave. No se encontró ninguna objeción pública de CurseForge a ese patrón; sí hay
  usuarios de itzg cuya clave propia, sacada de la consola de Studios, "keeps getting invalidated".
- **Resolver un `manifest.json` de 150 archivos** cuesta 1 request a CurseForge
  (`POST /v1/mods/files` con los 150 `fileIds`; +1 opcional `POST /v1/mods` para nombres/tipo) y
  2–3 a Modrinth (`POST /version_files` con sha1 y los proyectos de a 100). Total 3–5.
- **Tasa de acierto CF→Modrinth por sha1:** no hay ninguna medición publicada. **SUPUESTO**; se
  propone medirla con un pack real antes de comprometer la UI.
- **Zip de Prism:** sin carpeta de nivel superior (`instance.cfg`, `mmc-pack.json`,
  `minecraft/...`); los mods desactivados son `*.jar.disabled`; los mundos se saltan por nombre sin
  descomprimirlos, leyendo el zip desde el archivo temporal de la subida (no desde `bytes`). Se
  puede reutilizar `_import` casi sin cambios: el lector devuelve un `MrpackContent` con los jars
  como `override_jars`.
- **Deuda del panel:** las seis causas están localizadas con su arreglo y su test (Parte B). Dos
  hallazgos de la misma clase: el informe de importar un `.mrpack` tampoco muestra decisiones ni
  excepciones, y agregar un mod desde la búsqueda también redibuja `#mods` en «Todos».
- **Decisiones abiertas:** C1–C5 (CurseForge), P1–P4 (Prism), U1 (pestaña). Ver §A5 y §B1.

---

## Parte A — CurseForge y Prism

### A1. La API key de CurseForge en herramientas autoalojadas

| Herramienta | Qué hace con la clave | Qué documenta | Fuente |
|---|---|---|---|
| **itzg/docker-minecraft-server** | Hasta 2026-07 el usuario traía su clave (`CF_API_KEY` o `CF_API_KEY_FILE`), generada en `console.curseforge.com`. Desde el PR #4172 (fusionado 2026-07-16, "CF_API_KEY is now baked in and available by default") la imagen trae una clave **ofuscada** horneada en `mc-image-helper` (PR #811 "Bake in obfuscated CF_API_KEY", 2026-07-15; PR #815 la usa también para las descargas del CDN). El usuario puede seguir poniendo la suya. La imagen `java8` no la trae. | "A CurseForge API key is _now_ included by this image on Java 17 and newer; however, you can always supply your own instead." Instrucciones de escapar `$` en compose. **No** cita los términos. El PR #4172 se apoya en el blog de CurseForge del 2026-06-10. | `docs/types-and-platforms/mod-platforms/auto-curseforge.md`; PRs itzg/docker-minecraft-server#4172, itzg/mc-image-helper#811, #815 |
| **packwiz** | Clave del proyecto en el código (codificada en base64) y un parámetro de build para reemplazarla. | Comentario en el código: "If you fork/derive from packwiz, I request that you obtain your own API key." | `curseforge/request.go` |
| **Prism Launcher** | Clave del proyecto en `CMakeLists.txt` (`Launcher_CURSEFORGE_API_KEY`), y un ajuste de usuario `FlameKeyOverride` en la página de APIs. | Comentarios: "By using this key in your builds you accept the terms and conditions laid down in [3rd Party API Terms]"; "NOTE: CurseForge requires you to change this if you make any kind of derivative work."; "This key was issued specifically for Prism Launcher". En la UI: "you probably don't need to set this if CurseForge already works." | `CMakeLists.txt`, `launcher/Application.cpp` (`getFlameAPIKey`), `launcher/ui/pages/global/APIPage.ui` |
| **ferium** | Clave del proyecto en `libium/src/lib.rs`; `CURSEFORGE_API_KEY` o `--curseforge-api-key` la reemplazan. | README: "You can also set a custom CurseForge API key [...]". | `libium/src/lib.rs`, README |
| **mrpack-install** | No usa CurseForge: solo Modrinth ("Modrinth Modpack server deployment"). | — | README, descripción del repositorio |

**¿Cada usuario trae su propia clave?** No en las herramientas grandes: cada **proyecto** tiene una
clave emitida para él, y el usuario final puede reemplazarla. itzg hizo el recorrido inverso: pasó
de "cada usuario trae la suya" a una clave del proyecto en cuanto CurseForge exigió clave en el CDN.

**Qué dice CurseForge.**
- Blog "Introducing API Key Authentication for CurseForge File Downloads" (2026-06-10): "Direct
  CDN downloads now support an API key. It's optional now, required mid-July." "On July 16th, a
  valid key will be required, and any request without one will be rejected." "By having each
  integration authenticate with its own key, we can attribute downloads to their source." "This is
  about attribution and analytics for creators, not gatekeeping content." Dirigido a "launchers,
  modpack installers, server tools, and similar integrations". La clave va en `x-api-key` (o
  `?api-key=`, desaconsejado). Una petición sin clave: `401 {"error": "Unauthorized", "message":
  "A valid api-key is required."}`.
- "About the CurseForge API and How to Apply for a Key" (soporte, modificado 2024-12-03): se pide
  con el formulario (contacto, descripción, enlaces, uso esperado, aceptar los términos); revisión
  del equipo de Overwolf; criterios: "How it impacts on Authors' earnings", "How it affects
  CurseForge's servers and CDN", "How it takes Authors' consent when it comes to third party
  distribution of their creations". No dice plazo ni nada sobre claves por usuario u open source.
- Términos de terceros (modificados 2024-08-14), además de lo ya citado en la investigación previa:
  - la clave "is non-transferable and may not be shared with any third party" (salvo empleados con
    confidencialidad);
  - **cuota:** "Upon the External App exceeding a certain quota, to be decided [...] at Overwolf's
    sole discretion", puede exigir un contrato con pago, o negar el acceso;
  - **competencia:** "Developer shall not use the API [...] in order build any product or service
    that competes, directly or indirectly, with CF, CurseForge for Studios, or the Platform." Un
    importador que convierte un pack de CF en uno de Modrinth podría leerse como competencia
    indirecta. **SUPUESTO / lectura legal no verificada**: la investigación previa no citaba esta
    cláusula y es la más delicada para este uso.
- Documentación de la Core API: "Any registered account which is not a game developer and did not
  request a key, will be deleted" (ya citado antes). Es justo la vía que itzg documentó durante
  años (`console.curseforge.com`).

**¿Evidencia de que CurseForge se oponga al patrón?** Ninguna pública encontrada contra las claves
del proyecto incrustadas: Prism dice que su clave "was issued specifically" para él. Contra las
claves que el usuario se genera en la consola de Studios hay un indicio: itzg/docker-minecraft-server#3796
(2025-12): "my API key keeps getting invalidated"; un usuario lo "resolvió" "creating a new
Curseforge organization under an alternative email address". La causa no está dicha; que sea la
limpieza de cuentas que no son estudios es **SUPUESTO**. Lo que no se buscó a fondo: los Discord de
CurseForge y de los launchers (no son fuentes leíbles sin cuenta).

**Implicación para modpack-creator** (autoalojado, una instalación por dueño): el encaje honesto
con los términos es que **el dueño pida una clave para su instalación** con el formulario (no la
consola de Studios), la ponga en `.env.yaml` y nunca se versione. Una clave "del proyecto"
incrustada no aplica: el repositorio no se distribuye a terceros como Prism.

### A2. Las llamadas exactas para resolver un `manifest.json` de N archivos

Fuentes: docs.curseforge.com/rest-api; cómo lo hacen Prism (`launcher/modplatform/flame/FileResolvingTask.cpp`,
`FlameAPI.cpp`) y packwiz (`curseforge/import.go`, `request.go`).

| Paso | Llamada | Cuerpo | Qué da | Necesario |
|---|---|---|---|---|
| 1 | `POST https://api.curseforge.com/v1/mods/files` | `{"fileIds": [N enteros]}` | por archivo: `id`, `modId`, `fileName`, `fileLength`, `hashes[{value, algo}]` (1 = Sha1, 2 = Md5), `fileFingerprint` (murmur2), `downloadUrl` (vacía si la distribución está bloqueada), `isAvailable`, `fileStatus`, `gameVersions` | **sí**: es lo único que da el sha1 |
| 2 | `POST https://api.modrinth.com/v2/version_files` | `{"hashes": [sha1…], "algorithm": "sha1"}` | hash → versión de Modrinth (con sus sha512) | **sí** |
| 3 | `POST https://api.curseforge.com/v1/mods` | `{"modIds": [...], "filterPcOnly": true}` | `name`, `slug`, `classId` (carpeta: 6 mods, 12 resource packs, 6552 shaders…), `allowModDistribution`, `links.websiteUrl` | **opcional**: solo para nombrar en el informe lo que no está en Modrinth |
| 4 | proyectos de Modrinth (`GET /projects?ids=`) | de a 100 (`project_get_many`, ya en lotes desde i-7985ee-456f19) | proyectos para `pack_replace` | sí (ya existe) |
| — | `POST /v1/fingerprints/432` (o `/v1/fingerprints`) | fingerprints murmur2 | identifica jars sueltos **en CurseForge** | **no** para un manifiesto; solo para un jar de Prism que Modrinth no conoce |

- **Lotes:** la documentación no publica un máximo de ids por request. Prism manda el manifiesto
  entero en una sola `POST /v1/mods/files` y una sola `POST /v1/mods`; packwiz igual (primero
  `/v1/mods` y mira `latestFiles`, después `/v1/mods/files` solo con los que faltan). Que 150 ids
  entren en una request es **SUPUESTO** apoyado en que las dos herramientas lo hacen sin trocear.
- **sha1:** `hashes` trae sha1 y md5, nunca sha512. Prism solo usa el hash si `hashType == "sha1"`
  y no está vacío; packwiz tiene un "TODO: check if the hash is invalid (e.g. 0)". O sea, puede
  faltar o venir vacío en algún archivo (**SUPUESTO** sobre la frecuencia: rara). Un archivo sin
  sha1 cae a "no resuelto".
- **Límites de tasa:** CurseForge no publica números. Los términos hablan de una cuota a
  discreción de Overwolf. El cliente de itzg trata 403 como "forbidden or rate-limit has been
  exceeded" (itzg/docker-minecraft-server#3591). Diseño: tratar 403/429 como "CurseForge rechazó o
  limitó" sin reintentar en bucle; con 1–2 requests por importación no debería importar.
- **Reintentos:** los dos POST de CurseForge son lecturas: irían en una lista del tipo
  `IDEMPOTENT_POSTS` para el audit `retried_writes` (d-7985ee-05d032).

**Cuenta para N = 150** (más, digamos, 5 jars sueltos en `overrides/mods/`):

| Sistema | Requests | Detalle |
|---|---|---|
| CurseForge | **1** (+1 opcional) | `/v1/mods/files` con 150 ids; `/v1/mods` solo si el informe nombra lo no resuelto |
| Modrinth | **2–3** | 1 `POST /version_files` sha1 con 155 hashes (los jars de overrides se hashean localmente y entran en la misma llamada, en sha1); `project_get_many` de a 100 → 2 |
| **Total** | **3–5** | Prism hace 3 (files, Modrinth solo para los bloqueados, mods); packwiz 2 |

Comparado con lo que el repositorio ya gasta en un `.mrpack` del mismo tamaño (1 `/version_files`
sha512 + proyectos de a 100), la vía CF agrega 1–2 requests, y solo a CurseForge.

### A3. CF → Modrinth por sha1

- Mecanismo verificado: Modrinth `POST /version_files` con `algorithm: "sha1"` (docs.modrinth.com,
  "Get versions from hashes"); es lo que hace Prism con `FallbackMRBlockedMods`, solo para los
  archivos sin `downloadUrl`. El repositorio ya lo usa con sha1 para los escaneos
  (`src/services/pterodactyl.py:178-190`).
- Solo coincide si el autor subió **el mismo jar byte a byte** a los dos sitios. Herramientas
  conversoras de terceros (p. ej. "curseforge-to-modrinth-cli") describen el mismo método y meten
  en el pack los que no coinciden; ninguna publica una tasa de acierto.
- **Tasa esperada: SUPUESTO.** No hay ninguna cifra publicada. Intuición (no evidencia): alta para
  los mods populares de Fabric/NeoForge recientes, que suelen publicarse en los dos sitios con el
  mismo build; baja para mods solo-CF, packs viejos de Forge, y librerías que el autor recompila
  por plataforma.
- **Cómo medirla sin gastar diseño:** un spike con la clave del dueño sobre 2–3 packs reales de CF
  (los de sus servidores): contar `found / total` en el paso 2. El propio escaneo de servidores ya
  da una cota: los jars de un servidor montado desde un pack de CF que el escaneo identifica por
  sha1 son exactamente los que esta vía encontraría. **Se puede medir hoy, sin clave**, mirando los
  `ServerScan` de un servidor del dueño que corra un pack de CF (`identified / (identified + unknown)`).

### A4. El zip de instancia de Prism

**Disposición concreta** (de `ExportInstanceDialog.cpp` y `archive/ExportToZipTask.cpp`):
- Las rutas van relativas a la raíz de la instancia, con prefijo vacío: **no hay carpeta de nivel
  superior** en las exportaciones de Prism. (Las de MultiMC viejas sí podían tenerla: el importador
  de Prism busca `instance.cfg` en cualquier carpeta y la toma como raíz; hacer lo mismo.)
- Raíz: `instance.cfg`, `mmc-pack.json`, quizá `patches/`, el ícono, y `minecraft/` (o `.minecraft/`
  heredado) con `mods/`, `config/`, `resourcepacks/`, `shaderpacks/`, `saves/`, `options.txt`…
- La exportación sigue enlaces simbólicos (`followSymlinks = true`): en el zip llegan como
  archivos normales. El lector existente rechaza miembros symlink, lo que está bien.
- Ignorados por defecto: `logs`, `crash-reports`, `.cache`, `.fabric`, `.quilt`, `.DS_Store`,
  `Thumbs.db`, más lo que el usuario desmarcó (se recuerda en `.packignore`). `saves/` va salvo que
  se desmarque.

**Mods sin `.pw.toml`:** un jar puesto a mano (arrastrado a la carpeta) es solo
`minecraft/mods/<nombre>.jar`, sin entrada en `minecraft/mods/.index/`. Un mod **desactivado** en
Prism se renombra a `<nombre>.jar.disabled` (`launcher/minecraft/mod/Resource.cpp`). La carpeta de
mods también puede tener `.zip`, `.litemod` o carpetas; solo `.jar` y `.jar.disabled` cuentan.
`mmc-pack.json` puede traer `net.fabricmc.intermediary` además del loader (el UID existe en el
código de Prism: `ComponentUpdateTask.cpp`); se ignora lo desconocido.

**Identificar cada jar con el código existente:**
- Hash: el mismo par sha1 + sha512 que hacen `mrpack_import._overrides` (`src/library/mrpack_import.py:173-176`)
  y el escaneo (`src/services/pterodactyl.py:166-173`).
- Identificación: `ModpackImportService._identify` (`src/services/modpack_import.py:245-258`):
  catálogo por sha512 y una sola `POST /version_files` sha512 para lo que falte.
- Lo no identificado: hoy el importador de `.mrpack` lo informa como "No están en Modrinth" (D3).
  El escaneo, en cambio, lee la metadata del jar (`src/library/jarmeta.py:read_mod_metadata`) y
  busca candidatos (`PterodactylService._candidates`, 2 requests por jar). Para Prism los bytes
  están en la subida, así que la metadata se puede leer sin requests (P3).
- El `.pw.toml` como pista: `update.modrinth.version` identifica un mod aunque el jar falte (si el
  usuario desmarcó `mods/`); `update.curseforge` lo marca "de CurseForge" en el informe.

**Tamaño y mundos.** Un pack de 150 mods pesa del orden de 200–600 MiB solo en jars (**SUPUESTO**,
sin medir), muy por encima de los 50 MiB de hoy (`IMPORT_MAX_BYTES`, `src/routers/web/modpacks.py:339`),
y un mundo puede pesar varios GiB.
- La ruta hoy lee la subida a memoria: `data = await upload.read(IMPORT_MAX_BYTES + 1)`
  (`src/routers/web/modpacks.py:369`). Para Prism no: `UploadFile.file` es un
  `SpooledTemporaryFile` (Starlette vuelca a disco pasado 1 MiB), y `zipfile.ZipFile(upload.file)`
  lee el directorio central **sin descomprimir nada**. Se recorren los nombres, se descartan por
  prefijo `saves/`, `resourcepacks/`, `shaderpacks/`, `screenshots/`, `backups/`… sin abrirlos, y
  solo se abren en streaming los `minecraft/mods/*.jar(.disabled)`, `mmc-pack.json`,
  `instance.cfg` y `mods/.index/*.pw.toml`, con el `_Budget` existente contando solo lo que se lee.
  Un mundo así cuesta subida y disco temporal, no CPU ni memoria.
- El hash de ~400 MiB (sha1 + sha512) es del orden de 1–3 s de CPU (**SUPUESTO**); va en
  `asyncio.to_thread` o en el trabajo.
- Lo que no se arregla en el código: el tope del cuerpo en el proxy inverso del dueño y el chequeo
  de `content-length` de la ruta (`src/routers/web/modpacks.py:360-362`) tienen que subir para Prism.
- Alternativa sin subir jars: elegir la carpeta `mods/` en el navegador y calcular sha1/sha512 con
  `crypto.subtle.digest` en `app.js` (sin script inline, compatible con la CSP); solo se suben
  hashes y nombres. La metadata de un jar desconocido necesitaría subir ese jar. Ver P1.

**Requests para 150 jars:** CurseForge 0; Modrinth 1 `/version_files` (lo que el catálogo no tenga)
+ 2 de proyectos = **~3**. Si además se buscan candidatos para los desconocidos al estilo del
escaneo: +2 por jar desconocido (10 desconocidos → +20), o +1 `POST /v1/fingerprints/432` a CF por
todos juntos si hay clave (solo para decir "está en CurseForge").

### A5. Diseño de los dos importadores

**Lo común (refactor pequeño de `src/services/modpack_import.py`).** `_import` (líneas 216-243) hace
tres cosas: identificar (`_identify`, sha512), y luego aplicar (crear o validar el pack,
`pack_replace`, decisiones, excepciones, informe). Partirlo en:
1. `identify_*` por formato → `(found: dict[clave, ModrinthVersion], unknown: list[str], server_env: dict[project_id, bool])`;
2. `_apply(content_meta, found, unknown, overrides, actor, into, source)` = las líneas 222-243 sin cambios.

El `.mrpack` sigue igual por fuera; Prism puede incluso ir **sin** el refactor (abajo).

**Importador de Prism** (`src/library/prism_import.py`, puro, junto a `mrpack_import.py`):
- `read_prism(file: BinaryIO, limits) -> MrpackContent`: reutiliza `_Budget`, `_unsafe_path`, el
  rechazo de symlinks y los topes; localiza la raíz por `instance.cfg`; MC y loader de
  `mmc-pack.json` (uids → `MinecraftLoaders`); `name` de `instance.cfg`; cada jar de
  `minecraft/mods/` como `OverrideJar(side="both")`; el resto contado en `overrides` por carpeta
  (`config/`, `kubejs/`… para el informe de D4), y `saves/` contado sin leerse.
- Entra en la vía actual: `import_mrpack` hoy recibe `bytes`; se agrega `import_prism(upload_file, …)`
  que llama a `read_prism` y luego al mismo `_job` con el `MrpackContent` serializado. El trabajo
  (`_run` → `run_content` → `_import`) no cambia: los `override_jars` ya se identifican por sha512.
- Tests: `tests/test_prism_import.py` con zips construidos en el test (raíz plana y raíz en
  subcarpeta, `.jar.disabled`, `.pw.toml` sin jar, un `saves/` grande que no se lee: el `_Budget`
  no debe bajar, symlink rechazado, `..` rechazado); `tests/test_web_import.py` para el tope de
  tamaño propio de Prism. Fila nueva en `## Acciones y roles` si es un método de servicio nuevo
  (`ModpackImportService.import_prism`, editor, como `import_mrpack`).

**Importador de CurseForge:**
- `src/library/cf_import.py` (puro): `read_cf_zip(data) -> CfContent` con `manifestType ==
  "minecraftModpack"`, `manifestVersion == 1`, `minecraft.version`, el loader de
  `modLoaders[primary]` (`forge-47.2.0` → forge/47.2.0; quitar el `1.20.1-` de NeoForge en 1.20.1),
  `files[{projectID, fileID, required}]`, y `overrides/` con la misma lógica que `_overrides`
  (la raíz se llama como diga `overrides`, validada con `_unsafe_path`).
- `src/library/api/client/curseforge.py`: `CurseForgeAPI.files_get_many(file_ids)` (y quizá
  `mods_get_many`) sobre el cliente HTTP común, cabecera `x-api-key`, un `User-Agent` propio;
  contrato en `docs/references.md` y en `tests/test_api_contracts.py`; dos POST de lectura en la
  lista de idempotentes del audit.
- Configuración: `CURSEFORGE_API_KEY` **opcional** (`getenv_opt`): sin ella el formulario no ofrece
  CurseForge y la ruta da 422 "Esta instalación no tiene clave de CurseForge". `.env.default` está
  bloqueado para los agentes: la línea la agrega el dueño.
- Servicio: `import_curseforge(data, actor, into)` lee el zip al instante (422 si no es válido) y
  deja el mismo trabajo `modpack_import`; en el trabajo: 1 llamada a CF → sha1 por `fileID` →
  `files_hash_post_many(sha1s, "sha1")` (+ los jars de overrides por sha1) → `_apply`. Lo de CF
  vive solo en memoria durante el trabajo.
- Términos: lo que se guarda (bundle, catálogo, el informe en `job.checkpoint`) sale del archivo del
  usuario (`projectID`/`fileID`) o de Modrinth. El nombre de archivo de CF **no** se guarda en el
  informe (C3).
- Tests: lector con manifiestos construidos; servicio con un `FakeCurseForge`; contrato del cliente;
  `tests/test_startup.py` para el cableado con y sin clave.

**Decisiones abiertas** (recomendada primero en cada una):

- **C1. ¿CurseForge sí o no?**
  - (a) **Sí, opcional con la clave del dueño pedida por formulario.** Ej.: el dueño pide la clave,
    la pone en `.env.yaml`; el panel muestra «Zip de CurseForge». Costo: cliente nuevo + lector +
    ~1 día; riesgo legal de la cláusula de competencia (A1) que solo Overwolf puede despejar al
    aprobar la solicitud (la solicitud describe el uso: "convertir packs propios a Modrinth para un
    servidor propio").
  - (b) No por ahora; primero medir la tasa de acierto (A3) con los escaneos ya guardados. Costo:
    cero código; el tester sigue sin poder subir un zip de CF.
  - (c) Sin clave: aceptar el zip de CF pero exigir que el usuario lo convierta antes (p. ej.
    exportarlo como `.mrpack` desde Prism, que ya resuelve por sha1). Costo: solo texto en el panel.
  - Punto medio: (c) ya, (a) cuando llegue la clave.
- **C2. ¿Qué hace con lo que no está en Modrinth?**
  - (a) **Informarlo y no bajarlo** (como el `.mrpack`, D3). Ej.: «Solo en CurseForge (12):
    proyecto 238222, archivo 4567890 → curseforge.com». Costo: nada nuevo.
  - (b) Bajarlo del CDN de CF con la clave y guardarlo como archivo local. Costo: almacenamiento de
    jars (no existe) y choca de frente con "save or cache".
- **C3. ¿El informe nombra los no resueltos?**
  - (a) **Solo con los ids del manifiesto** y un enlace a CurseForge (cero datos de CF guardados).
  - (b) Con el nombre de CF (+1 request `/v1/mods`), mostrado al terminar y no guardado: el informe
    se vería distinto al reabrir el trabajo. Costo: 1 request y un informe en dos partes.
  - (c) Guardar el nombre en el informe: lectura laxa de "save or cache".
- **C4. ¿Los archivos `required: false` del manifiesto?** (a) **Importarlos con decisión «Por
  decidir»**; (b) saltarlos e informarlos; (c) tratarlos como requeridos.
- **C5. ¿Códigos de perfil de CF (endpoint no documentado)?** (a) **No** (ya recomendado antes);
  (b) sí, detrás de la misma clave.

- **P1. ¿Qué sube el usuario de Prism?**
  - (a) **El zip de la instancia, leído desde el archivo temporal sin abrir los mundos**, con un tope
    propio (p. ej. 1 GiB). Ej.: exporta desde Prism con todo marcado y funciona. Costo: tope del
    proxy, disco temporal del contenedor.
  - (b) El zip, pero pedir en el panel exportarlo sin `saves/` y mantener un tope bajo (200 MiB).
    Costo: un paso más para el usuario; un zip con mundos se rechaza con un mensaje claro.
  - (c) Elegir la carpeta `mods/` en el navegador y mandar solo hashes (SubtleCrypto). Ej.: nada
    pesado sube; los desconocidos se suben uno por uno si se quieren candidatos. Costo: JS nuevo,
    tests de navegador, y sin `mmc-pack.json` hay que pedir MC y loader a mano.
  - Punto medio: (b) ya; (a) si el tester lo pide.
- **P2. ¿Un `.jar.disabled`?** (a) **Importarlo como «Desactivado»** (decisión `false` donde no hay
  ninguna); (b) ignorarlo; (c) importarlo como cualquier otro.
- **P3. ¿Los jars desconocidos?** (a) **Informarlos como en el `.mrpack`** y nada más; (b) leer su
  metadata (sin requests) y buscar candidatos (+2 requests por jar), como el escaneo: mejor
  resultado, lo que i-7985ee-27db6c también quiere para servidores; (c) además, preguntar a CF por
  fingerprint si hay clave.
- **P4. ¿Los `.pw.toml`?** (a) **Solo como pistas** (versión de Modrinth cuando falta el jar; marca
  «de CurseForge» en el informe); (b) ignorarlos.

---

## Parte B — Deuda del panel

### B1. `#mods` detrás del diálogo de build se redibuja siempre en «Todos»

**Causa.** Una acción desde una tarjeta de problema del diálogo (`contexto == "dialogo"`) llega a
`_acted` (`src/routers/web/modpacks.py:210-229`), que arma el `#mods` fuera de banda con
`_mods_context(modpackService, updateCheckService, bundle_id)` **sin `vista`**
(`src/routers/web/modpacks.py:224`), así que vale el default `"todos"` (líneas 151-160). El diálogo
no sabe en qué pestaña estaba la página: nada de la petición lo dice. El comportamiento está fijado
por un test: `tests/test_web_modpacks.py:883` (`assert 'aria-current="page"' in tab(behind, "todos")`,
dentro de `test_a_dialog_action_also_redraws_the_mods_and_builds_behind_it`, línea 872) y documentado
en `_decide_dialog.html:7-8`.

**De la misma clase:** `project_add` (agregar desde la búsqueda) también llama a `_mods_context` sin
`vista` (`src/routers/web/modpacks.py:468`), y `_acted` sin `contexto` va a `_mods(...)` sin vista
(línea 219).

**Arreglo recomendado (U1-a).** La vista sale de la URL de la página: las pestañas usan
`hx-push-url="true"` (`src/web/templates/_macros.html:190-193`), así que la página está en
`/modpacks/{id}?vista=problemas`, y htmx manda esa URL en la cabecera `HX-Current-URL` de toda
petición. Un helper `_vista_from(request)` = `_vista(parse_qs(urlsplit(request.headers.get("HX-Current-URL", "")).query).get("vista", ["todos"])[0])`;
`_acted` lo pasa a `_mods_context`. Sin plantillas nuevas, y `_vista` ya rechaza cualquier valor
desconocido. Corregir el comentario de `_decide_dialog.html` y el docstring de `_acted`.

**Test** (antes que el código, en `tests/test_web_modpacks.py`):
`test_a_dialog_action_redraws_the_mods_in_the_tab_the_page_was_on`: con
`headers={**HTMX, "HX-Current-URL": f"http://testserver/modpacks/{BUNDLE_ID}?vista=problemas"}`,
`client.delete(.../projects/P2, params={"contexto": "dialogo"})` → `aria-current="page"` en
`tab(behind, "problemas")` y `data-vista="problemas"` en `behind`; con `?vista=xyz` o sin cabecera,
«Todos» (el test existente de la línea 872 sigue valiendo para ese caso).

**Decisión U1** (es lo que el usuario ve): (a) **la pestaña de la página** (arriba); (b) siempre
«Con problemas», porque el diálogo trata problemas; (c) dejar «Todos» y corregir solo el roadmap.
¿Y el `project_add` de la misma clase? Recomendado: el mismo helper, preguntando antes de ampliar.

### B2. `projects_by_id` y `project_version_update_filter` sin uso

**Confirmado con `git grep`** (2026-10-09):
- `ModrinthService.projects_by_id` (`src/services/modrinth.py:101-104`): ningún llamador en `src/`.
  Solo lo usan `tests/test_web_modpacks.py:1099-1114` (`test_projects_by_id_finds_them_by_their_modrinth_id`,
  que prueba el método mismo) y un doble en `tests/test_web_updates.py:280-282` (clase `Titles` de
  `test_the_detail_links_to_the_page`, que la ruta ya no llama: el test pasa igual).
- `ModrinthService.project_version_update_filter` (`src/services/modrinth.py:178-186`): ningún
  llamador en `src/` ni en `tests/`. La auditoría de requests ya lo marcaba "sin uso".
- Ninguno tiene fila en `## Acciones y roles` de `docs/decisions.md`, así que el barrido de
  `tests/test_routes_roles.py` no cambia.

**Arreglo.** Borrar los dos métodos; borrar el test de `projects_by_id`; en
`tests/test_web_updates.py` quitar el doble y su override del contenedor (o dejar el override con
una clase vacía si se quiere afirmar que la ruta no consulta Modrinth). Quitar `Sequence` del
`from typing import` de `src/services/modrinth.py:5` (queda sin uso). `project_find_by_modrinth_id`
de la base sigue en uso (seis llamadores).

**Test.** Es una eliminación: el gate (typecheck + tests) es la prueba. Opcional, un chequeo de
código muerto no existe en `scripts/audit.py`, y agregarlo sería ampliar.

**Visto de paso, sin tocar:** `project_version_update_all` (`src/services/modrinth.py:188-195`)
tampoco tiene llamador en `src/`; solo `tests/test_database.py:159-161`. No está en el roadmap:
preguntar antes de borrarlo.

### B3. El 404 del panel lateral va a `#notices`

**Causa.** `mod_panel` (`src/routers/web/mods.py:78-102`) solo atrapa `UpstreamError` y
`TimeoutError` (línea 96). Cuando Modrinth no conoce el mod, `ModpackService.mod_page` lanza
`HTTP_404_NOT_FOUND("Modrinth no conoce el mod «…»")` (`src/services/modpack.py:641-642`); el
`WebRoute` lo convierte en un aviso `_notice.html` con estado 404 (`src/web/__init__.py:240-249`), y
`app.js` manda todo 404 HTML a `noticeTarget(elt)` (`src/web/static/app.js:54-65`): el enlace del
mod no está en un `<form>`, así que va a `#notices`. El panel no se abre (el `afterSwap` solo lo
abre si el destino es `#mod-drawer-body`, líneas 169-182); si el clic vino **desde dentro del
panel** (un enlace de dependencia), el aviso cae detrás del modal, en la página inerte.

**Arreglo recomendado.** En `mod_panel`, atrapar `HTTPException` con `status_code == 404` y responder
el mismo `_panel.html` con `failure=str(error.detail)` y estado 200, sin «Reintentar» (reintentar no
cambia nada): agregar `retry` al contexto (`True` para Modrinth caído, `False` para el 404) y
condicionar el botón en `src/web/templates/mods/_panel.html:13-20`. La página completa
(`/mods/{id}`) sigue siendo 404 (`tests/test_web_mods.py:168`).
Alternativa en `app.js`: si `requestConfig.target` es `#mod-drawer-body`, mandar el aviso ahí; más
general pero cambia la regla de avisos para todos.

**Test** (`tests/test_web_mods.py`): `test_a_mod_modrinth_does_not_know_says_so_inside_the_panel`:
`fake.error = HTTP_404_NOT_FOUND("Modrinth no conoce el mod NOPE")`;
`client.get("/mods/NOPE/panel", headers=HTMX)` → 200, `role="alert"` y el mensaje en el texto,
`"Reintentar" not in text`; y con `?desde=PREV` conserva «← volver».

### B4. La ficha de una dependencia que solo figura como problema dice «Ningún modpack lo usa»

**Causa.** «Dónde se usa» sale de `_mod_local` (`src/services/modpack.py:665-698`): los packs que
tienen el proyecto (`bundle_find_with_project`) más las resoluciones que lo trajeron como
dependencia, `resolution_find_with_dependency` (`src/database/modpack.py:304-308`), que solo busca
en `lock.entries` (`{"lock.entries": {"$elemMatch": {"project_id": …, "origin": None}}}`). Una
dependencia **sin versión** para el pack no tiene `LockEntry`: solo existe como `Problem` con
`project_id` y `requested_by` (`src/library/resolution.py:234-243`, creado en 369-376). Así
`page.uses` queda vacía y la plantilla dice «Ningún modpack lo usa»
(`src/web/templates/mods/_card.html:107`).

**Arreglo.**
1. La consulta busca en los dos: `{"$or": [{"lock.entries": {"$elemMatch": {...}}}, {"lock.problems.project_id": project_id}]}`.
2. En `_mod_local`, `requested[owner]` suma el `requested_by` de los problemas con ese
   `project_id` (los del admin ya vienen sin `ADMIN`: `_asked`, `resolution.py:217-218`), y marca
   esos packs en un `problem: set[str]`.
3. `ModUse` gana `problem: bool = False` (`src/services/modpack.py:166-178`); la tarjeta dice
   «dependencia de X · con problema» con el enlace al pack en la pestaña
   `/modpacks/{id}?vista=problemas`.

**Tests.** `tests/test_mod_page.py` (base real, `@pytest.mark.network`):
`test_a_dependency_that_is_only_a_problem_is_used_by_its_pack`: un pack B con `X`, resolución
guardada sin entrada para `P` y `problems=[Problem(kind="unresolvable", project_id="P",
requested_by=["X"], detail="sin versión")]` → `[(u.bundle_name, u.origin, u.dependency_of, u.problem)
for u in page.uses] == [("B", None, ["X title"], True)]`. Y en `tests/test_web_mods.py`, con un
`ModUse(problem=True)` en el doble: el texto dice «con problema» y no «Ningún modpack lo usa».

### B5. El aviso de importar un servidor no dice cuántas decisiones y excepciones dejó (d-7985ee-58801d)

**Causa.** `import_server` calcula `on_server, exceptions = await self._running(bundle, scan)`
(`src/services/pterodactyl.py:293`) y solo los manda a la auditoría (294-297); `ImportResult`
(`src/model/pterodactyl/scan.py:36-42`) no tiene dónde llevarlos, y `servers/_import.html`
(líneas 7-13) solo cuenta agregados, locales y pendientes.

**Arreglo.** `ImportResult` gana `on_server: list[str] = []` y `channel_exceptions: dict[str, str] = {}`
(con default: `BundleForServer` y los tests existentes los construyen sin ellos); `import_server` los
llena; `_import.html` agrega «Marcados «En el servidor»: N» y «Aceptan beta o alpha: N (Sodium:
beta, …)» con `ui.mod_link`, y una línea «nada nuevo» cuando los dos son 0 en una reimportación.
`_import_titles` (en `src/routers/web/servers.py`) tiene que pedir también esos ids. Como
`_new_modpack_done.html` incluye `_import.html`, el pack nuevo desde un servidor lo hereda.

**Tests.** `tests/test_import.py`: en `test_importing_records_that_every_identified_mod_runs_on_the_server`
y `test_a_beta_running_on_the_server_becomes_a_channel_exception`, afirmar `result.on_server` y
`result.channel_exceptions`; en `test_importing_never_overrides_a_decision_the_admin_made`, que lo
ya decidido no se cuenta. `tests/test_web_servers.py`: con
`ImportResult(..., on_server=["A1"], channel_exceptions={"A1": "beta"})` el HTML dice
«En el servidor» con 1 y «beta».

**De la misma clase (no pedido):** el informe de importar un `.mrpack`
(`src/web/templates/modpacks/_import_result.html`) tampoco muestra `report.decisions` ni
`report.channel_exceptions`, aunque `ImportReport` los trae (`src/services/modpack_import.py:50-51`).
Mismo arreglo de plantilla; preguntar antes de ampliar.

### B6. La revisión de actualizaciones no avisa betas más nuevas de un mod con excepción

**Causa.** `UpdateCheckService.compute` (`src/services/update_check.py:107-139`) agrupa por loaders y
pregunta a Modrinth `files_update_hash_post_many(..., [bundle.version], bundle.channels)`
(línea 125-126): los canales **del pack** para todos los archivos. Para un mod cuya versión del
build es beta por una excepción (d-7985ee-b1ce87), Modrinth solo puede contestar una estable más
nueva, que es justo la que anula la excepción; una beta más nueva nunca aparece. Que Modrinth omita
el hash cuando no hay ninguna estable es **SUPUESTO** (la lógica actual ya depende de eso).

**Arreglo.** El build guarda qué proyectos entraron por excepción: `Lock.excepted`
(`src/library/resolution.py:261`, llenado en 382-390 y 513). Tras la primera llamada, para las
entradas de `build.lock.excepted` sin actualización estable, una segunda llamada por grupo de
loaders con los canales del pack más el de la excepción (`bundle.channel_exceptions[pid]` y los
más estables, como `_excepted`). La estable sigue ganando: si la primera llamada trae una, esa es
la actualización. `AvailableUpdate` gana `channel: Optional[str] = None` para que la página diga
«2.1.0-beta.3 (beta)». `digest_of` no cambia (un aviso nuevo si aparece otra beta).
Costo: +1 request por grupo de loaders que tenga mods con excepción (normalmente 0 o 1).

**Tests** (`tests/test_update_check.py`, base real):
- `test_a_mod_with_a_beta_exception_is_told_of_a_newer_beta`: build con `B` en `lock.excepted`,
  versión `b1` beta; `FakeModrinth` sin respuesta para canales `["release"]` y con `b2` beta para
  `["release", "beta"]` → `updates == [AvailableUpdate(project_id="B", ..., to_version_id="b2", channel="beta")]`
  y `api.calls` = la llamada normal más una con `{"hashes": ["h-B"], "version_types": ["release", "beta"]}`.
- `test_a_release_that_cancels_the_exception_is_the_update_and_asks_once`: con estable disponible,
  una sola llamada y `channel` `None`/`"release"`.
- `test_without_exceptions_there_is_no_second_call`: el test de la línea 96 sigue con una llamada.

---

## Decisiones abiertas (resumen)

| Id | Tema | Recomendada |
|---|---|---|
| C1 | CurseForge sí/no | opcional con clave del dueño por formulario; mientras, "expórtalo como .mrpack" |
| C2 | Lo que no está en Modrinth | informarlo, no bajarlo |
| C3 | Nombres de CF en el informe | solo ids del manifiesto + enlace |
| C4 | `required: false` | importar como «Por decidir» |
| C5 | Códigos de perfil de CF | no |
| P1 | Qué sube el usuario de Prism | zip sin `saves/` con tope bajo; zip completo leído del temporal si hace falta |
| P2 | `.jar.disabled` | «Desactivado» |
| P3 | Jars desconocidos | informarlos (candidatos después, con i-7985ee-27db6c) |
| P4 | `.pw.toml` | solo pistas |
| U1 | Pestaña tras una acción del diálogo | la de la página (`HX-Current-URL`) |

## Lo que no se verificó

- La tasa de acierto CF→Modrinth por sha1 (A3) y el tamaño típico de un zip de Prism (A4).
- Que `POST /v1/mods/files` acepte 150 ids en una request (solo que Prism y packwiz lo hacen).
- La lectura legal de "save or cache" y de la cláusula de competencia.
- Nada de esto contactó la API de CurseForge: no se usó ninguna clave.

## Fuentes

CurseForge
- Blog, "Introducing API Key Authentication for CurseForge File Downloads" (2026-06-10): https://blog.curseforge.com/introducing-api-key-authentication-for-curseforge-file-downloads/
- "About the CurseForge API and How to Apply for a Key" (modificado 2024-12-03): https://support.curseforge.com/support/solutions/articles/9000208346
- 3rd Party API Terms and Conditions (modificados 2024-08-14): https://support.curseforge.com/en/support/solutions/articles/9000207405-curse-forge-3rd-party-api-terms-and-conditions
- Core API: https://docs.curseforge.com/rest-api/

Herramientas (código leído, nada copiado)
- itzg/docker-minecraft-server: `docs/types-and-platforms/mod-platforms/auto-curseforge.md`; PR #4172; issues #3591, #3796 — https://github.com/itzg/docker-minecraft-server
- itzg/mc-image-helper PRs #811, #815 — https://github.com/itzg/mc-image-helper
- packwiz: `curseforge/request.go`, `curseforge/import.go` — https://github.com/packwiz/packwiz
- ferium: `libium/src/lib.rs`, README — https://github.com/gorilla-devs/ferium
- mrpack-install — https://github.com/nothub/mrpack-install
- Prism Launcher (GPL-3.0): `CMakeLists.txt`, `launcher/Application.cpp`, `launcher/ui/pages/global/APIPage.ui`,
  `launcher/modplatform/flame/{FileResolvingTask,FlameAPI}.cpp`, `launcher/ui/dialogs/ExportInstanceDialog.cpp`,
  `launcher/archive/ExportToZipTask.cpp`, `launcher/InstanceImportTask.cpp`, `launcher/minecraft/mod/Resource.cpp`,
  `launcher/minecraft/ComponentUpdateTask.cpp` — https://github.com/PrismLauncher/PrismLauncher/tree/develop/launcher

Modrinth
- Get versions from hashes: https://docs.modrinth.com/api/operations/versionsfromhashes/
