# Cómo se construyen y mantienen modpacks de Minecraft

**Fecha:** 2026-10-12 · **Método:** especificaciones y documentación oficial, código fuente de las
herramientas (leído para entender su comportamiento, sin copiar nada) y notas de autores de packs.
Fuentes leídas el 2026-10-12. Lo que no se contrastó con una fuente primaria dice **SUPUESTO**.
Las páginas web se leyeron con un extractor que resume: las citas entre comillas son lo que
devolvió, no una copia literal verificada. El código se leyó en crudo (`curl`/`gh`).

**Licencias de lo leído (solo ideas, nada copiado):** packwiz MIT, mrpack-install MIT, Sinytra
Connector MIT, ferium MPL-2.0, Prism Launcher GPL-3.0, ATLauncher GPL-3.0, NeoForge LGPL-2.1,
ServerPackCreator LGPL-2.1, itzg/docker-minecraft-server Apache-2.0, Fabric API Apache-2.0,
Fabulously Optimized BSD-3-Clause. **modrinth/code** (Labrinth y Modrinth App) no declara una
licencia única en la API de GitHub (`NOASSERTION`): tratarlo como solo ideas.

**Prefijos:** **[PW]** packwiz `main`, **[MI]** mrpack-install `trunk`, **[FE]** ferium `main`,
**[PR]** Prism Launcher `develop`, **[MA]** Modrinth App (`modrinth/code`, `packages/app-lib`),
**[LB]** Labrinth (`modrinth/code`, `apps/labrinth`), **[AT]** ATLauncher, **[FO]** Fabulously
Optimized (repositorio y wiki), **[IZ]** itzg/docker-minecraft-server.

Complementa (no repite) a: `docs/research/2026-10-05-bajas-de-version.md` (señales para marcar una
baja), `2026-10-resolucion-y-formatos.md` (API de Modrinth y formatos), `2026-10-09-importar-modpacks.md`
y `2026-10-09-curseforge-prism-y-deuda-panel.md` (formatos de importación),
`2026-10-09-guardar-overrides.md` (overrides) y `2026-10-operacion-pterodactyl.md` (backups y
rollback en el panel).

---

## Resumen

- **Nadie compara números de versión para decidir "más nueva": todos usan la fecha de
  publicación** (Modrinth, Modrinth App, Prism, ferium) o el id de archivo creciente (packwiz con
  CurseForge). packwiz calcula también el orden por número (FlexVer), pero **usa la fecha** y solo
  avisa si discrepan. Corrige al documento del 2026-10-05, que decía que packwiz prefiere FlexVer.
- **Solo la Modrinth App protege de verdad contra bajar.** Para un modpack, solo ofrece versiones
  con `date_published` **posterior** a la instalada; y para cada mod, el canal efectivo es el
  **menos estable entre el preferido y el de la versión instalada**: si tienes una beta instalada,
  sigue buscando entre release y beta, así que nunca te "devuelve" a una release más vieja.
  Prism, ferium y packwiz cambian a cualquier versión distinta de la instalada, aunque sea más
  vieja. itzg lo documenta como función ("upgrading (and downgrading)").
- **Bajar un mod (o el pack) en un mundo ya cargado es la regla que los autores repiten**: "Never
  downgrade to an older version, this could corrupt your worlds and settings!" y "Never update to
  an alpha or beta from a release version" (wiki de Fabulously Optimized). Los mecanismos son
  concretos: Forge/NeoForge borran del guardado las entradas de registro que faltan; NeoForge
  "corrige" a sus valores por defecto las claves de config que no reconoce; las conversiones de
  datos (DataFixerUpper de Mojang, y las de cada mod) van en un solo sentido.
- **Volver de beta a estable sin retroceder**: quedarse en el canal de la versión instalada hasta
  que exista una release **más nueva** que ella (regla de la Modrinth App; también `alpha+` de
  itzg como opción explícita). La regla actual de la app (d-7985ee-b1ce87: la excepción "queda sin
  efecto sola" cuando cabe cualquier release) **puede bajar** un mod: es el caso JEI.
- **Reproducibilidad = lock con hashes** (packwiz `index.toml`, `.mrpack` con sha1+sha512
  obligatorios). Desplegar bien = **guardar el manifiesto de lo desplegado** y comparar el nuevo
  contra él: mrpack-install (`packstate.json`) y Prism (`mrpack/modrinth.index.json` en la
  instancia) lo hacen; borrar lo que el pack viejo tenía y el nuevo no; respaldar lo que se va a
  pisar. mrpack-install **se niega** a actualizar si cambian Minecraft o el loader.
- **Pruebas reales**: arrancar un servidor dedicado con el pack hasta que carga el mundo
  (ServerPackCreator: "TEST YOUR SERVER PACKS"; tiene un modo que arranca un servidor para ver si
  un mod es solo de cliente), revisar crash reports y logs de mixin, y retener actualizaciones
  problemáticas mod por mod (el changelog de FO está lleno de "update skipped").

---

## 1. Ciclo de vida de un modpack

### 1.1 Elegir la base: Minecraft y loader

- El autor fija **una** versión de Minecraft y **un** loader con su versión. El `.mrpack` lo
  declara en `dependencies` (`minecraft`, `forge`, `neoforge`, `fabric-loader`, `quilt-loader`)
  ([spec mrpack](https://support.modrinth.com/en/articles/8802351-modrinth-modpack-format-mrpack));
  packwiz en `pack.toml` → `versions` (`minecraft` obligatorio, `fabric`/`forge`/`quilt`…)
  ([pack.toml](https://packwiz.infra.link/reference/pack-format/pack-toml/)); CurseForge en
  `manifest.json` → `minecraft.version` y `modLoaders[]` (sin esquema oficial; ver
  `2026-10-09-importar-modpacks.md`).
- Cambiar de versión de Minecraft es un **cambio mayor** del pack y no tiene vuelta para los
  mundos. FO numera así: cada salto de MC abre una versión mayor (13.0.0 para 26.1, 14.0.0, 15.0.0
  para 26.3), primero como `-alpha.N`, y en el changelog repite "Any worlds that are upgraded to
  26.1 cannot be downgraded - backups are recommended" ([FO] `CHANGELOG.md`).
- Versiones de MC "aceptables" adicionales: packwiz acepta mods marcados para otras versiones con
  `acceptable-game-versions`; la versión principal del pack queda como la más preferida
  ([PW] `core/pack.go`, `GetSupportedMCVersions`). ferium tiene `GameVersionMinor`: acepta
  versiones "relacionadas que se consideran sin cambios incompatibles", agrupadas con la lista de
  tags de versiones de juego de Modrinth ([FE] `libium/src/config/filters.rs`, `upgrade/check.rs`).

**Para la app:** el pack ya fija MC y loader. Un cambio de MC o loader no es una "actualización"
de servidor sino una migración: la app debería tratarlo aparte (ver §4 y recomendaciones).

### 1.2 Agregar mods y sus dependencias

- **Metadatos de dependencia en Modrinth:** `dependency_type` ∈ `required`, `optional`,
  `incompatible`, `embedded` ([API, list versions](https://docs.modrinth.com/api/operations/getprojectversions/)).
  Casi siempre apuntan solo al proyecto, sin versión (ver `2026-10-resolucion-y-formatos.md`).
- **Lo que el loader hace cumplir al arrancar** está en el jar, no en Modrinth:
  - Fabric (`fabric.mod.json`): `depends` (fallo duro), `recommends` (aviso), `suggests` (solo
    metadato), `conflicts` (aviso si coincide), `breaks` (fallo duro si coincide); rangos de
    versión estilo semver (`>=`, `^`, `~`, `1.x`, `*`)
    ([spec fabric.mod.json](https://wiki.fabricmc.net/documentation:fabric_mod_json_spec)).
  - NeoForge (`neoforge.mods.toml`): `type` = `required` (por defecto), `optional` (valida la
    versión si está), `incompatible` (bloquea), `discouraged` (aviso); `versionRange` en notación
    de rangos de Maven (`[1,2)`); `side` = `BOTH`/`CLIENT`/`SERVER`; `ordering`
    ([NeoForge, mod files](https://docs.neoforged.net/docs/gettingstarted/modfiles)).
  - Consecuencia: una versión que Modrinth declara compatible puede no arrancar si su jar exige
    otra versión de MC o de una librería. La declaración de Modrinth (`game_versions`, `loaders`)
    la escribe el autor a mano.
- **Ningún resolvedor abierto usa `incompatible`** (ya documentado en `2026-10-resolucion-y-formatos.md`;
  ferium tiene el issue abierto [#399](https://github.com/gorilla-devs/ferium/issues/399)).
- **Reemplazos fijos conocidos:** packwiz transforma Fabric API → QFAPI y Fabric Language Kotlin →
  QKL cuando el pack es Quilt ([PW] `modrinth/modrinth.go`, `mapDepOverride`). Ver §6: QFAPI ya no
  existe para MC nuevos.

### 1.3 Cliente, servidor o ambos

- **`.mrpack`:** cada archivo trae `env.client` y `env.server` ∈ `required`, `optional`,
  `unsupported`; "Server refers to the _dedicated_ server [...] if something is marked server only,
  it should not be installed on the client" ([spec mrpack](https://support.modrinth.com/en/articles/8802351-modrinth-modpack-format-mrpack)).
- **Modrinth (proyecto/versión):** `environment` con 10 valores (`client_and_server`,
  `client_only`, `client_only_server_optional`, `singleplayer_only`, `server_only`,
  `server_only_client_optional`, `dedicated_server_only`, `client_or_server`,
  `client_or_server_prefers_both`, `unknown`) ([API](https://docs.modrinth.com/api/operations/getprojectversions/)).
  Según resúmenes de búsqueda, Modrinth rehízo estos metadatos en agosto de 2025 y pidió a los
  autores revisarlos porque las etiquetas viejas no distinguían "solo servidor" de "ambos"
  (**SUPUESTO**: no leí el anuncio).
- **packwiz:** `side` = `both`/`client`/`server` en cada `.pw.toml`
  ([mod.pw.toml](https://packwiz.infra.link/reference/pack-format/mod-toml/)).
- **Los metadatos fallan en la práctica.** itzg mantiene una lista por defecto de **104** mods de
  cliente que excluye aunque el pack los declare para servidor, y variables para excluir o forzar
  archivos: "To exclude client mods that are incorrectly declared by the modpack as
  server-compatible, set `MODRINTH_EXCLUDE_FILES`" ([IZ] `docs/types-and-platforms/mod-platforms/modrinth-modpacks.md`,
  `files/modrinth-exclude-include.json`). ServerPackCreator detecta los mods de cliente "scanning
  the mods of your modpack for their sideness" y tiene `-verifyclientside`, que "boots a server
  with the mod force-included, to detect crashes" ([README](https://github.com/Griefed/ServerPackCreator)).

### 1.4 Configs y overrides

- **`.mrpack`:** tres carpetas en capas: `overrides/` (base), luego `server-overrides/` o
  `client-overrides/`, que sobrescriben ([spec](https://support.modrinth.com/en/articles/8802351-modrinth-modpack-format-mrpack)).
  En la muestra de nueve packs populares ninguno usa las capas por lado (`2026-10-09-guardar-overrides.md`).
- **Qué va en overrides:** `config/` (configs de mods), `kubejs/` (scripts), `defaultconfigs/`
  (Forge/NeoForge), opciones por defecto, a veces `resourcepacks/`, `shaderpacks/`, mundos y jars
  que no están en Modrinth. CurseForge pide para el server pack "mods", "config" y "kubejs (if
  relevant)", más scripts de arranque y `server.properties` opcionales
  ([blog de CurseForge, server packs](https://blog.curseforge.com/server-packs-tutorial/)).
- **Configs de servidor por mundo (NeoForge):** las configs de tipo `SERVER` "Can be overridden for
  each world by adding a config to [...] `<server_folder>/world/serverconfig`"
  ([NeoForge, configs](https://docs.neoforged.net/docs/misc/config)). Es decir, parte de la
  configuración vive **dentro del mundo**. Que `defaultconfigs/` se copie a cada mundo nuevo como
  plantilla es lo que hace Forge según la comunidad (**SUPUESTO**: la página de NeoForge no lo
  menciona).
- **Opciones del jugador:** los packs de cliente usan mods como YOSBR o Config Manager para dar
  valores por defecto sin pisar los del jugador: FO cambió YOSBR por "Config Manager - like YOSBR,
  but also has reset options" ([FO] `CHANGELOG.md`, 12.0.0-beta.3). La Modrinth App trata
  `options.txt` aparte: no lo borra al actualizar ([MA] `install_mrpack.rs`, `remove_all_related_files`).

### 1.5 Versionado del pack

- `.mrpack` → `versionId`: "A unique identifier for this specific version of the modpack" (texto
  libre). packwiz → `version` opcional en `pack.toml`. CurseForge → `version` en el manifiesto.
- Práctica observada (FO): semver del pack, mayor por salto de MC, `-alpha.N`/`-beta.N` para lo no
  estable, y el canal de Modrinth/CurseForge del archivo del pack acompaña al sufijo
  ([FO] `CHANGELOG.md`).
- Technic Solder (y ATLauncher al importar de Solder) usa punteros `recommended`/`latest` sobre
  builds numerados ([AT] `InstanceInstallerDialog.java`): el mismo modelo que la app ya tiene.

---

## 2. Actualizaciones: qué versión se toma

### 2.1 Qué hace cada herramienta

| Herramienta | "Más nueva" = | Canales | ¿Puede bajar? | Fuente |
|---|---|---|---|---|
| **Modrinth (servidor)** | `POST /version_files/update`: `ORDER BY date_published DESC, id DESC`. Otra consulta de actualización individual ordena primero por un campo `ordering` del autor y luego por fecha | filtro `version_types` | responde "la más nueva compatible", sin mirar la instalada | [LB] `routes/v3/version_file.rs` (`update_files_internal`, `get_latest_matching_versions`) |
| **Modrinth App, modpack** | solo versiones con `date_published` **mayor** que la instalada, la más nueva por fecha | canal efectivo = el menos estable entre el preferido de la instancia y el de la versión instalada; si en ese conjunto no hay nada, cae a beta y luego alpha | **No** (filtra por fecha) | [MA] `state/instances/commands/list_content.rs` (`check_modpack_update`) |
| **Modrinth App, mods** | lo que responde `update_many` (fecha) | mismo canal efectivo por archivo; cascada release → beta → alpha si no hay nada | En la práctica no, porque el canal de la instalada siempre se incluye (**SUPUESTO** el caso borde: la instalada no está etiquetada para la versión de MC actual) | [MA] `state/cache.rs` (`ReleaseChannel::version_type_fallbacks`, `least_stable`) |
| **Prism Launcher** | lo que responde `/version_files/update` con los tipos elegidos | `releaseTypes` del usuario | **Sí**: actualiza si "the hash differs" y el archivo es el preferido; no compara fecha ni número | [PR] `modplatform/modrinth/ModrinthCheckUpdate.cpp` |
| **packwiz (Modrinth)** | por **fecha** (con desempate por versión de MC y loader preferidos); calcula también el orden FlexVer y solo **avisa**: "inconsistent between latest version number and newest release date" | **ninguno**: no filtra por tipo, toma alphas si son las más nuevas (issue [#87](https://github.com/packwiz/packwiz/issues/87), abierto desde 2022) | **Sí**: actualiza si el id de la elegida es distinto del instalado | [PW] `modrinth/modrinth.go` (`getLatestVersion`, `findLatestVersion`), `modrinth/updater.go` |
| **packwiz (CurseForge)** | por versión de MC, loader y luego **id de archivo** mayor | ninguno ("TODO: manage alpha/beta/release correctly") | Sí, por el mismo criterio | [PW] `curseforge/curseforge.go` |
| **ferium** | el primero de la lista "sorted in the order of preference (e.g. chronological)" que pasa los filtros | filtro `ReleaseChannel`: "matching the channel provided or more stable channels"; filtros por mod (canal, versión de juego, regex de nombre de archivo) | **Sí**: no guarda la versión instalada; mueve lo que sobra a `mods/.old` | [FE] `libium/src/upgrade/check.rs`, `config/filters.rs`, `src/download.rs` |
| **itzg (proyectos sueltos)** | "the newest version that is the same or better release type"; `tipo+` = preferir la más estable | `release` (defecto), `beta`, `alpha`, y `alpha+` | Sí, es explícito: el ejemplo de `c2me-fabric:alpha+` vuelve a la release 0.3.6.0.0 aunque haya alphas 0.3.7 | [IZ] `docs/mods-and-plugins/modrinth.md` |
| **itzg (modpack)** | `MODRINTH_MODPACK_VERSION_TYPE` | release/beta/alpha | Sí: "Upgrading (and downgrading) takes care of cleaning up old files and upgrading (and downgrading) the mod loader" | [IZ] `modrinth-modpacks.md` |
| **CurseForge App** | el autor del pack sube versiones; el usuario elige "Update to the latest version" **o cualquier versión del desplegable** | — | Sí, sin aviso de baja en el artículo; advierte que "Any custom changes you made to the modpack's content will be overwritten when changing versions, unless you select Update to new Profile" | [soporte CurseForge 9000206622](https://support.curseforge.com/support/solutions/articles/9000206622-how-to-update-change-a-modpack-s-version) |
| **ATLauncher** | el autor del pack publica versiones; reinstalar/actualizar borra `mods/`, `configs/` y `bin/`, más lo que el autor liste en `<delete>`; y conserva lo que el autor liste en `keeps` | — | Se puede reinstalar una versión anterior (**SUPUESTO**: no encontré un aviso de baja) | [wiki ATLauncher, delete](https://wiki.atlauncher.com/pack-admin/xml/delete), [AT] `workers/InstanceInstaller.java` |

**Conclusión:** la fecha es el orden de facto; el número solo sirve como segunda señal (packwiz
avisa cuando discrepan). Solo la Modrinth App tiene una regla explícita de "no retroceder", y es
doble: **fecha posterior** y **canal pegajoso** (el de la versión instalada).

### 2.2 Por qué bajar de versión rompe mundos y configs

1. **Datos del mundo convertidos hacia adelante.** Minecraft guarda una *data version* creciente
   en cada mundo y al abrir uno más nuevo que el cliente advierte que "their world may become
   corrupted" ([Minecraft Wiki, Data version](https://minecraft.wiki/w/Data_version)). La
   conversión (DataFixerUpper) va en un solo sentido. Los mods con migraciones propias de NBT
   hacen lo mismo (**SUPUESTO** como regla general; depende de cada mod). Los autores lo dicen sin
   matices: "Any worlds that are upgraded to 26.1 cannot be downgraded" ([FO] `CHANGELOG.md`).
2. **Registros (Forge/NeoForge).** Cuando faltan entradas de registro (un bloque o ítem que la
   versión vieja no tiene), Forge dispara `MissingMappingsEvent`; si ningún mod la reasigna, "the
   default action will occur by notifying the user about the missing entry and whether they still
   would like to load the world" ([Forge docs, registries 1.20.x](https://docs.minecraftforge.net/en/1.20.x/concepts/registries/)).
   Si se acepta, esos bloques e ítems se pierden en el siguiente guardado (según guías de hosting;
   **SUPUESTO** el detalle exacto). **En un servidor dedicado no hay nadie que vea ese diálogo**:
   cómo responde el servidor sin interfaz es **SUPUESTO**.
3. **Configs.** NeoForge comprueba cada config contra su especificación y, si no cuadra, registra
   "Configuration {} is not correct. Correcting" y "Incorrect key {} was corrected from {} to its
   default" ([NeoForge] `src/main/java/net/neoforged/neoforge/common/ModConfigSpec.java`, rama
   `26.3.x`). Una versión vieja que no conoce una clave nueva, o que la valida distinto, puede
   devolver a sus valores por defecto lo que el admin había ajustado.
4. **Contenido nuevo que desaparece.** Un ítem, receta, estructura o dimensión que existía en la
   versión nueva no existe en la vieja: inventarios, máquinas y chunks que lo usan pierden datos.
5. **Mismatch cliente-servidor.** Los jugadores que ya actualizaron su cliente no entran a un
   servidor que bajó (ver "Mismatched Mod Channel" en guías de hosting; **SUPUESTO** el mensaje
   exacto por loader).

FO lo resume en sus instrucciones de actualización: "Never downgrade to an older version, this
could corrupt your worlds and settings!" y "Never update to an alpha or beta from a release
version, install a separate instance for testing" ([FO wiki, Modrinth App](https://wiki.download.fo/update-instructions/modrinth-app),
[CurseForge App](https://wiki.download.fo/update-instructions/curseforge-app)).

**Matiz que importa:** los autores **sí** bajan versiones **antes de publicar**, en el pack, de
mods de cliente o cosméticos: "Downgraded Entity Model Features due to some issues with resource
packs" ([FO] 12.0.3). Lo que nunca se hace es bajar sobre un mundo existente algo que guarda datos
en él. La distinción útil para la app: **mods que guardan datos en el mundo** (bloques, ítems,
entidades, dimensiones, capacidades) contra **mods sin estado** (rendimiento, UI, cliente).
Clasificar eso automáticamente no tiene fuente: **SUPUESTO** (los `environment` de solo cliente
son una aproximación barata).

### 2.3 Prácticas para no retroceder nunca

- **No actualizar a algo más viejo**: comparar contra lo instalado, no contra "la mejor del
  catálogo". Modrinth App: fecha posterior a la instalada (§2.1).
- **El canal de lo instalado manda** hasta que el canal preferido tenga algo más nuevo
  (Modrinth App, `least_stable`).
- **Retener actualizaciones malas en lugar de bajar**: FO las salta en el pack ("Sodium update
  skipped as it is an alpha", "Skipped Fabric API update as it prevented users from joining
  worlds", "Skipped Skyboxify update due to a crash"). packwiz lo formaliza con `pin`/`hold`:
  "Pin a file so it does not get updated automatically" ([PW] `cmd/pin.go`); `update --all`
  imprime "Update skipped for pinned mod" ([PW] `cmd/update.go`).
- **Backup antes de cada cambio y mundo de prueba aparte** (instrucciones de FO; Data version de
  Mojang ofrece backup al abrir un mundo viejo).
- **Rollback = restaurar el backup completo**, no reinstalar jars viejos sobre el mundo nuevo
  (ver §4.3).

### 2.4 Canales beta/alpha y la vuelta a estable

- **Cómo se manejan:** como umbral ("este canal o más estables": ferium, itzg, Modrinth App) y
  con excepción por mod (filtros por mod en ferium, `proyecto:beta` en itzg).
- **Los autores tratan a las alphas con desconfianza explícita**: FO salta "Sodium [...] as it is
  an alpha" incluso en su propia rama alpha.
- **El problema de volver:** un mod en beta 19.57 cuya última release es 19.51 (el caso JEI de
  `2026-10-05-bajas-de-version.md`). Una regla "si hay release, usa release" lo **baja**. Opciones
  reales:
  - **Canal pegajoso con fecha** (Modrinth App): mientras lo instalado sea beta, se elige entre
    release y beta la más nueva **posterior** a la instalada. Cuando sale una release más nueva,
    se toma y desde ahí el canal efectivo vuelve a ser release. Nunca baja.
  - **Preferir la estable aunque sea más vieja** (`alpha+` de itzg): bajar a propósito. Sirve para
    un servidor nuevo, no para uno con mundo.
- **Implicación directa para la app:** la regla de d-7985ee-b1ce87 (la excepción de canal "solo
  cuenta mientras ninguna versión suya quepa en los canales del pack") es la segunda opción: en
  cuanto aparece **cualquier** release que cabe, la resolución la prefiere (`_preference` ordena
  primero por canal) y un servidor que corría la beta baja. Ver recomendaciones R1–R2.

---

## 3. Pins, lockfiles y reproducibilidad

- **packwiz:** `index.toml` lista cada archivo con su hash ("for integrity checking"),
  `hash-format` por defecto del índice (sha256, sha512, sha1, md5, murmur2), y `preserve`: "the
  file is not overwritten if it already exists, to preserve changes made by a user"
  ([index.toml](https://packwiz.infra.link/reference/pack-format/index-toml/)). Cada mod tiene su
  `.pw.toml` con URL, hash, `side` y bloque `update` (ids del proyecto y versión instalada). El
  `pin` es un booleano en el metafile ([PW] `cmd/pin.go`). Recomiendan versionar con git
  ([getting started](https://packwiz.infra.link/tutorials/creating/getting-started/)).
- **`.mrpack`:** el índice es el lock: cada archivo con ruta, `hashes` ("MUST contain the SHA1 hash
  and the SHA512 hash"), `env`, URLs y tamaño. Los overrides no traen hash en el índice: hay que
  hashearlos del zip (la Modrinth App y mrpack-install lo hacen: [MA] `install_mrpack.rs`,
  [MI] `update/packstate/packstate.go`).
- **CurseForge:** `projectID` + `fileID` (inmutables), sin hash en el manifiesto.
- **Lo que no es reproducible:** resolver "la más nueva" en el momento del despliegue (itzg con
  `MODRINTH_PROJECTS` sin versión, ferium, packwiz-installer contra un `pack.toml` que se edita).
  Un build congelado con hashes sí lo es.
- **Versiones retiradas:** una versión borrada de Modrinth deja un lock que no se puede descargar
  (ya anotado en `2026-10-05-bajas-de-version.md`). Mitigación habitual: guardar los jars del build
  o re-resolver. **SUPUESTO** que los autores grandes guarden copias; no lo encontré documentado.

---

## 4. Servidores

### 4.1 Cómo se despliega

| Vía | Qué hace | Fuente |
|---|---|---|
| **mrpack-install** | Instala servidor + pack desde Modrinth. `update` guarda un `packstate.json` con slug, versión, dependencias (MC/loader) y hashes de **todos** los archivos y overrides del lado servidor. Al actualizar: **aborta** si cambian MC o loader ("mismatched versions, please upgrade manually"); ignora los archivos cuyo hash no cambia en el pack y el local coincide; **mueve a `backups/<fecha>/`** cada archivo que va a reemplazar; descarga solo lo `env.server` `required` (u `optional` según filtros); extrae overrides; guarda el nuevo estado. En el código leído, un archivo que el pack viejo tenía y el nuevo no **no se toca** (**SUPUESTO** que sea intencional; no lo probé). | [MI] README, `update/command.go`, `update/backup/backup.go`, `update/packstate/packstate.go` |
| **packwiz-installer** | `java -jar packwiz-installer-bootstrap.jar -g -s server <url>/pack.toml`: sin GUI, solo lo `both` o `server`. itzg lo integra con `PACKWIZ_URL`. Respeta `preserve` | [packwiz-installer](https://packwiz.infra.link/tutorials/installing/packwiz-installer/) |
| **Server pack de CurseForge** | Zip aparte que el autor sube como "Additional File" del archivo del pack, con `mods`, `config`, `kubejs` y scripts; "The pack and server should match pack versions" | [soporte 9000197279](https://support.curseforge.com/support/solutions/articles/9000197279), [blog](https://blog.curseforge.com/server-packs-tutorial/) |
| **itzg** | Instala/actualiza el pack con el loader, excluye mods de cliente con una lista propia, `MODRINTH_OVERRIDES_EXCLUSIONS` para no extraer ciertos overrides | [IZ] docs citadas |
| **Prism (cliente)** | Guarda el índice de la versión instalada en `mrpack/modrinth.index.json`; al actualizar no descarga lo idéntico, **borra lo que el índice viejo tenía y el nuevo no** y borra todos los overrides viejos ("TODO: Currently 'overrides' will always override the stuff on update. How do we preserve unchanged overrides?") | [PR] `modplatform/modrinth/ModrinthInstanceCreationTask.cpp` |
| **Modrinth App (cliente)** | Al actualizar un pack borra los proyectos del pack viejo (por hash) y **cada archivo de overrides del pack viejo**, salvo `options.txt` | [MA] `install_mrpack.rs` (`remove_all_related_files`) |

### 4.2 Qué tocar y qué no

- **Nunca:** `world/` (incluye `world/serverconfig`), `server.properties`, `ops.json`,
  `whitelist.json`, listas de baneos, `eula.txt` (listas de mrpack4server y guías, recogidas en
  `2026-10-09-guardar-overrides.md`; guías de hosting coinciden).
- **Sí, con cuidado:** `mods/` (lo gestionado por el pack), `config/`, `kubejs/`,
  `defaultconfigs/`. Las configs que el admin editó son el punto débil de **todas** las
  herramientas: Prism y la Modrinth App las pisan; CurseForge App avisa que se sobrescriben;
  ATLauncher borra `configs/` entero; packwiz tiene `preserve` por archivo; mrpack-install las
  respalda antes de pisarlas.
- **Diferenciar "del pack" y "del admin"**: solo se puede si se guarda lo desplegado (hash por
  ruta). Si el hash local ≠ hash que se desplegó, el admin lo cambió (idea de mrpack-install).

### 4.3 Backups y rollback

- Backup **antes** de tocar nada; el detalle de Pterodactyl (throttle, cupo, restore que trunca)
  está en `2026-10-operacion-pterodactyl.md`.
- **Rollback de verdad = restaurar el backup del mundo y de los archivos juntos.** Volver a poner
  los jars viejos sobre un mundo que ya corrió con los nuevos es una baja (§2.2). Las guías de
  hosting para FTB/CurseForge dicen lo mismo: leer el changelog, respaldar, y algunos cambios
  "may require a world reset" (guías de terceros; **SUPUESTO** como práctica general).
- El rollback barato (solo jars) solo es seguro si el servidor **no llegó a cargar el mundo** con
  la versión nueva (falló al arrancar). **SUPUESTO** razonado, sin fuente que lo diga así.

---

## 5. Cómo se prueba un pack

- **Arrancar un servidor dedicado** con exactamente lo que se va a desplegar y esperar a que cargue
  el mundo. ServerPackCreator: "FOR THE LOVE OF GODS, TEST YOUR SERVER PACKS BEFORE YOU SHIP THEM!",
  "is not a guarantee for working server packs", y ofrece instalar el loader "for immediate
  testing" ([README](https://github.com/Griefed/ServerPackCreator)).
- **Cliente sin pantalla en CI:** HeadlessMc "can run the Minecraft client in headless mode, e.g.
  enabling you to run it on CI/CD runners", y el proyecto hermano `mc-runtime-test`
  ([headlessmc](https://github.com/headlesshq/headlessmc)).
- **Instancia de prueba separada** para alphas y betas (instrucciones de FO).
- **Crash reports y logs:** FO incluye Crash Assistant en el pack ([FO] `CHANGELOG.md`). Para
  conflictos de mixins, las propiedades de Mixin: `mixin.debug.export` (vuelca el bytecode final a
  `.mixin.out`), `mixin.debug.verify`, `mixin.debug.countInjections` ("Elevates failed injections
  to an error condition"), `mixin.checks.interfaces` y `mixin.dumpTargetOnFailure`
  ([Mixin wiki](https://github.com/SpongePowered/Mixin/wiki/Mixin-Java-System-Properties)).
- **Incompatibilidades conocidas:** declaradas en el jar (`breaks`/`incompatible`), en Modrinth
  (`incompatible`) y, sobre todo, en el conocimiento del autor: FO retiene versiones por
  "known incompatibilities" y "Temporarily removed" mods que aún no se actualizaron.
- **Proceso real de un pack grande** (FO): bajar la versión anterior en un launcher, hacer
  cambios, **probar**, exportar, convertir a los otros formatos con scripts, **probar de nuevo** en
  el otro launcher y publicar ([FO] `DEVELOPER-README.md`).

---

## 6. Compatibilidad entre loaders

- **Quilt:** el loader carga mods de Fabric. Su capa de compatibilidad de API, QFAPI/QSL, **se
  retiró**: "We [...] have decided to officially retire all three of these libraries in Minecraft
  26.1" ([blog de QuiltMC, 2026-02-03](https://quiltmc.org/en/blog/2026-02-03-non-obfuscated-updates/)).
  En Modrinth, QFAPI llega hasta MC 1.21, con última actualización el 2024-12-02 (API de Modrinth,
  consultada el 2026-10-12). La meta de Quilt sí lista 26.3, con loaders `0.31.0-beta.N`. El
  reemplazo fijo Fabric API → QFAPI de packwiz no sirve para MC nuevos; qué API usa un pack Quilt
  en 26.x (¿Fabric API directa?) es **SUPUESTO**: no lo encontré documentado.
- **Fabric en NeoForge:** Sinytra Connector, "a translation/compatibility layer that allows running
  Fabric mods on NeoForge", con Forgified Fabric API y Launchpad como dependencias; la lista de
  mods compatibles está en connector.sinytra.org ([README](https://github.com/Sinytra/Connector)).
  En Modrinth, Connector declara `forge`/`neoforge` y MC hasta 26.1.2; Forgified Fabric API hasta
  26.2 (API de Modrinth, 2026-10-12). Un mod de Fabric en un pack NeoForge **no** es "compatible"
  según los metadatos de Modrinth: hay que pedirlo como Fabric y resolverlo aparte.
- **Versiones de MC "compatibles":** el autor del mod las declara en Modrinth; el jar las impone con
  su rango (`depends.minecraft` en Fabric, `versionRange` en NeoForge). Pueden diferir. ferium usa
  los grupos de versiones de Modrinth para aceptar menores sin cambios incompatibles; packwiz usa
  una lista manual (`acceptable-game-versions`).

---

## 7. Errores comunes y lecciones de autores

1. **Bajar sobre un mundo existente** (§2.2). Lección: nunca; retener en vez de bajar.
2. **Pasar de release a alpha/beta en la instancia de juego.** FO: nunca; instancia aparte.
3. **Tomar la "última" sin mirar el canal:** packwiz toma alphas; ferium/itzg sin filtro también.
4. **Confiar en los metadatos de lado:** listas de exclusión de itzg y ServerPackCreator.
5. **Pisar configs editadas al actualizar** (Prism, Modrinth App, CurseForge App, ATLauncher).
6. **Archivos huérfanos:** mods que el pack quitó y quedan en `mods/` (mrpack-install, según el
   código leído); o un jar duplicado de dos versiones (ferium avisa: "duplicate files were found").
7. **Actualizar todo de golpe**: FO actualiza por lotes y retiene lo que rompe; ferium recibió el
   pedido de "partial upgrades" ([#176](https://github.com/gorilla-devs/ferium/issues/176)).
8. **Cambiar MC "en caliente":** mrpack-install lo rechaza; FO lo trata como versión mayor.
9. **Cliente y servidor con versiones distintas del pack:** "The pack and server should match pack
   versions" (CurseForge).
10. **Sin backup antes de actualizar** (todas las guías).

---

## Qué implica para esta app

**Nunca bajar de versión (lo central)**

- **R1. Regla de no retroceder en la resolución, contra lo que corre el servidor.** Para cada mod
  que el servidor ya tiene (manifiesto observado) o que estaba en el build anterior, un candidato
  solo vale si su `date_published` es **mayor o igual** que la de la versión instalada (regla de la
  Modrinth App). Si no existe ninguno, se **mantiene la versión instalada** (equivale a un pin
  automático), no se elige otra más vieja. La segunda señal (número FlexVer y canal) solo marca
  "posible baja" para revisión, como ya propone `2026-10-05-bajas-de-version.md`.
- **R2. Canal pegajoso.** El canal efectivo de un mod = el menos estable entre los canales del pack
  (con su excepción) y el canal de la versión instalada. Concretamente: cambiar
  d-7985ee-b1ce87 para que la excepción de canal **no caduque** mientras la versión que corre sea
  beta/alpha y no exista una release **más nueva** que ella. Hoy caduca en cuanto cabe cualquier
  release: es el caso JEI (beta 19.57 → release 19.51). Esto es una decisión del dueño, porque
  cambia una decisión registrada.
- **R3. La baja deja de ser solo un aviso cuando hay mundo.** F27 propone "marcar y advertir, no
  bloquear". Con lo de §2.2, recomendaría: una baja **segura** en un mod que no es de solo cliente
  exige confirmación explícita y un backup completo hecho en ese mismo trabajo; una baja en un mod
  de solo cliente (o en un servidor sin mundo) solo avisa. Es decisión del dueño (F27 está abierto).
- **R4. Rollback = restaurar backup.** "Volver atrás es mover un puntero" (plan) solo es seguro
  para la **autoría**. Para un **servidor** que ya cargó el mundo con el build nuevo, el rollback
  debe ser restaurar el backup previo al despliegue (archivos + mundo), no desplegar el build viejo
  encima. Desplegar un build más viejo a un servidor que ya corrió uno más nuevo debería pasar por
  R3. El rollback barato (solo jars) solo si el servidor no llegó a "Done" con el build nuevo.
- **R5. Cambio de MC o de loader = migración, no actualización.** Como mrpack-install: un
  despliegue que cambia `minecraft` o el loader respecto de lo que corre se rechaza en el flujo
  normal y va por un flujo propio (backup obligatorio, aviso de que no hay vuelta).

**Despliegue**

- **R6. Guardar el manifiesto desplegado por servidor** (ruta → sha512, incluyendo overrides) y
  calcular el plan contra él: no tocar lo idéntico, **borrar lo que el build viejo tenía y el nuevo
  no** (evita huérfanos), y **no pisar un archivo cuyo hash local difiere del desplegado** (lo
  editó el admin): respaldarlo y avisar, o saltarlo (`preserve`). La app ya identifica jars por
  hash (manifiesto observado); falta extenderlo a overrides cuando existan (D4).
- **R7. Lista fija de rutas intocables**: `world*/`, `server.properties`, `ops.json`,
  `whitelist.json`, baneos, `eula.txt`, y cualquier `*/serverconfig/`.
- **R8. Filtro de mods de cliente con lista propia además de `environment`**, porque los metadatos
  fallan (itzg excluye 104 por defecto). Mostrarlo como "excluido por ser de cliente" con opción de
  forzar.

**Resolución**

- **R9. Leer las dependencias del jar** (`fabric.mod.json`, `neoforge.mods.toml`, ya hay
  `src/library/jarmeta.py`) para validar el rango de MC y de librerías que el loader va a imponer,
  además de lo que declara Modrinth. Lo que el jar prohíbe (`breaks`, `incompatible`) es un problema
  del build.
- **R10. Retener por mod con motivo** (pin con nota): la práctica de FO ("skipped due to a crash").
  La app ya tiene pins; sumar un motivo y que "actualizar todo" los respete y los liste.
- **R11. Quilt:** quitar o limitar el reemplazo Fabric API → QFAPI a MC ≤ 1.21 (QFAPI no existe
  después). **Connector:** si se soporta, tratarlo como regla explícita del pack (mods Fabric en un
  pack NeoForge), nunca inferido.

**Pruebas**

- **R12. Prueba de arranque antes de recomendar un build:** un servidor de prueba (otro servidor
  de Pterodactyl o un contenedor) que arranca el build y espera la línea de mundo cargado; guardar
  el crash report si falla. Opcional: arrancar con `-Dmixin.debug.countInjections=true` para que
  un mixin que no se aplicó falle en la prueba. Es costosa (minutos por build): decisión del dueño.
- **R13. Los builds beta/alpha del pack van a un servidor de prueba primero** (FO: instancia
  aparte), nunca directo al servidor con el mundo principal.

## No verificado (SUPUESTOS)

- Que los mods con migraciones propias de datos no tengan vuelta en general (depende de cada mod).
- Que al aceptar entradas de registro faltantes se borren en el siguiente guardado, y cómo responde
  un **servidor dedicado** sin interfaz a ese diálogo de Forge/NeoForge; el comportamiento de
  Fabric con entradas faltantes.
- Que `defaultconfigs/` se copie a cada mundo nuevo (Forge/NeoForge).
- El mensaje exacto del mismatch cliente-servidor por loader.
- El caso borde de la Modrinth App en que la versión instalada no está etiquetada para la versión de
  MC actual (podría ofrecer una más vieja).
- Que mrpack-install deje a propósito los archivos que el pack nuevo quitó (lo leí en el código; no
  lo ejecuté).
- Que ATLauncher no avise al reinstalar una versión anterior del pack.
- La fecha y el texto del anuncio de Modrinth sobre `environment` (agosto de 2025, según
  resúmenes de búsqueda).
- Qué API usa un pack Quilt en MC 26.x tras el retiro de QFAPI.
- Que el rollback de solo jars sea seguro si el servidor no llegó a cargar el mundo.
- Que la clasificación "mod con datos en el mundo" vs "sin estado" pueda aproximarse con
  `environment` (solo cliente ⇒ sin datos en el mundo del servidor).
- Que los autores grandes guarden copias de jars por si Modrinth retira una versión.

## Fuentes (leídas el 2026-10-12)

- Modrinth: [formato .mrpack](https://support.modrinth.com/en/articles/8802351-modrinth-modpack-format-mrpack);
  [API, list project versions](https://docs.modrinth.com/api/operations/getprojectversions/);
  API pública `GET /v2/project/{connector,qsl,fabric-api,forgified-fabric-api}`.
- modrinth/code: `apps/labrinth/src/routes/v3/version_file.rs`;
  `packages/app-lib/src/state/cache.rs`; `packages/app-lib/src/state/instances/commands/list_content.rs`;
  `packages/app-lib/src/api/pack/install_mrpack.rs` (rama `main`).
- packwiz: [getting started](https://packwiz.infra.link/tutorials/creating/getting-started/),
  [index.toml](https://packwiz.infra.link/reference/pack-format/index-toml/),
  [pack.toml](https://packwiz.infra.link/reference/pack-format/pack-toml/),
  [mod.pw.toml](https://packwiz.infra.link/reference/pack-format/mod-toml/),
  [packwiz-installer](https://packwiz.infra.link/tutorials/installing/packwiz-installer/);
  código `modrinth/modrinth.go`, `modrinth/updater.go`, `curseforge/curseforge.go`, `cmd/pin.go`,
  `cmd/update.go`, `core/pack.go`; issues [#87](https://github.com/packwiz/packwiz/issues/87),
  [#164](https://github.com/packwiz/packwiz/issues/164).
- mrpack-install ([nothub/mrpack-install](https://github.com/nothub/mrpack-install), rama `trunk`):
  README, `update/command.go`, `update/backup/backup.go`, `update/packstate/packstate.go`.
- ferium ([gorilla-devs/ferium](https://github.com/gorilla-devs/ferium)): `libium/src/config/filters.rs`,
  `libium/src/upgrade/check.rs`, `libium/src/config/structs.rs`, `src/download.rs`; issues
  [#176](https://github.com/gorilla-devs/ferium/issues/176), [#399](https://github.com/gorilla-devs/ferium/issues/399).
- Prism Launcher: `launcher/modplatform/modrinth/ModrinthCheckUpdate.cpp`,
  `launcher/modplatform/modrinth/ModrinthInstanceCreationTask.cpp`,
  `launcher/modplatform/flame/FlameCheckUpdate.cpp`.
- ATLauncher: [wiki, delete](https://wiki.atlauncher.com/pack-admin/xml/delete);
  `src/main/java/com/atlauncher/workers/InstanceInstaller.java`, `gui/dialogs/InstanceInstallerDialog.java`.
- CurseForge: [cambiar la versión de un modpack](https://support.curseforge.com/support/solutions/articles/9000206622-how-to-update-change-a-modpack-s-version);
  [moderación, server packs](https://support.curseforge.com/support/solutions/articles/9000197279);
  [tutorial de server packs](https://blog.curseforge.com/server-packs-tutorial/).
- itzg/docker-minecraft-server: `docs/mods-and-plugins/modrinth.md`,
  `docs/types-and-platforms/mod-platforms/modrinth-modpacks.md`, `files/modrinth-exclude-include.json`
  ([docs](https://docker-minecraft-server.readthedocs.io/en/latest/)).
- ServerPackCreator: [README](https://github.com/Griefed/ServerPackCreator).
- HeadlessMc: [README](https://github.com/headlesshq/headlessmc).
- Fabric: [fabric.mod.json spec](https://wiki.fabricmc.net/documentation:fabric_mod_json_spec).
- NeoForge: [mod files](https://docs.neoforged.net/docs/gettingstarted/modfiles),
  [configs](https://docs.neoforged.net/docs/misc/config),
  [registries](https://docs.neoforged.net/docs/concepts/registries); código
  `src/main/java/net/neoforged/neoforge/common/ModConfigSpec.java` (rama `26.3.x`).
- Forge: [registries 1.20.x](https://docs.minecraftforge.net/en/1.20.x/concepts/registries/).
- Mixin: [Java system properties](https://github.com/SpongePowered/Mixin/wiki/Mixin-Java-System-Properties).
- Minecraft Wiki: [Data version](https://minecraft.wiki/w/Data_version).
- QuiltMC: [Bringing Quilt into the non-obfuscated era](https://quiltmc.org/en/blog/2026-02-03-non-obfuscated-updates/);
  `https://meta.quiltmc.org/v3/versions/{game,loader}`.
- Sinytra Connector: [README](https://github.com/Sinytra/Connector).
- Fabulously Optimized: repositorio (`CHANGELOG.md`, `DEVELOPER-README.md`),
  [wiki: actualizar](https://wiki.download.fo/update-instructions),
  [Modrinth App](https://wiki.download.fo/update-instructions/modrinth-app),
  [CurseForge App](https://wiki.download.fo/update-instructions/curseforge-app).
- Guías de hosting (solo para contrastar prácticas, no como fuente primaria): resultados de
  búsqueda de ggservers, xgamingserver, shockbyte y gameserverkings sobre actualizar packs y
  "missing mods in world".
- Repositorio local (solo lectura): `src/library/resolution.py` (`_preference`, `_excepted`),
  `docs/decisions.md` (d-7985ee-b1ce87, d-7985ee-58801d), `docs/plan.md`, `docs/roadmap.md` (F27).
