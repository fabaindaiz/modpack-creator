# Roadmap

Ideas aceptadas, todavía no construidas. **No es una promesa ni una orden de trabajo**: es dónde
va a chocar cada una, escrito ahora que está claro. Ninguna entrada se borra; cambia de estado
(Planeada · A medias · Hecha · Cerrada por medición · Bloqueada afuera). Terminar un ítem edita
este archivo en el mismo cambio que lo termina. Los ids se acuñan con
`python .agents/tools/bundle.py id i "<idea>"` y no cambian.

## Dónde estamos

Al cierre de la sesión de desarrollo del 2026-10-01 (detalle en
`.claude/logs/agent-changelog.md`, s-7985ee-9b0d95 y las entradas de abajo). **Todo está en
`main`; nada está desplegado.**

- **Fase 0, código hecho:**
  - dependencias fijas, volumen de Mongo e imagen que arranca, login con autorización;
  - tests contra MongoDB real en CI y bloqueo de red;
  - los updates escriben;
  - contratos y escrituras sin reintentos;
  - `/health`, capas en 0 y una `ClientSession` por cliente.
- **Fase 1, hecha:**
  - el modelo nuevo de Modrinth (i-7985ee-f4ba63);
  - la resolución (i-7985ee-7f735a, i-7985ee-60822c);
  - el contenido con origen (i-7985ee-063a3a);
  - builds inmutables con punteros (i-7985ee-098611).
- **Fase 2, hecha en solo lectura:**
  - Pterodactyl no recibe escrituras sin `PTERODACTYL_WRITES` (d-7985ee-5739fc);
  - escaneo por hash (i-7985ee-43df2f, opción 1) y comparación con un build (i-7985ee-4364a3);
  - el manifiesto, solo en Mongo (i-7985ee-5c7506 a medias).
- **Lo que bloquea desplegar:** seguir `docs/runbooks/migrar-mongo-a-volumen.md`. Hace falta
  respaldo, `docker/.env` con `MONGO_VERSION`, `OIDC_ALLOWED_GROUPS` en `.env.yaml` y restaurar.
  Sin eso el volumen nuevo arranca vacío y la API no arranca.
- **Lo siguiente, todo del dueño, en orden:**
  1. desplegar con el runbook;
  2. S7 operación: un usuario de servicio no admin, una clave `ptlc_` con `allowed_ips` y revocar
     la vieja (i-7985ee-63226e);
  3. un login real con una cuenta fuera del grupo;
  4. los puntos de control de las fases 1 y 2, con `PTERODACTYL_WRITES` vacío: armar el pack de
     un servidor real, escanearlo y comparar. Con eso se decide si se suma packwiz y se abre la
     fase 3, que escribe en servidores.
- **Hecho después del cierre** (s-7985ee-bcd5de):
  - la exportación `.mrpack` verificada con descargas reales (i-7985ee-7352b3);
  - logs JSON con `request_id` (i-7985ee-90f944);
  - trabajos durables con un primer tipo de solo lectura (i-7985ee-0ec295);
  - esperar un 429 en las lecturas;
  - sugerir Iris para un shader;
  - borrar `Settings.collection` (i-7985ee-c1de10).
- **Pendiente de código, sin decisiones del dueño:**
  - el scheduler de escaneos periódicos;
  - el primer escaneo por `files/compress` (escribe: fase 3);
  - pasar `httpx` a `httpx2`;
  - igualar `mongo:8.0` de CI con la versión de producción;
  - el panel del dueño (i-7985ee-50d411) y los avisos por Discord (i-7985ee-34933b), que
    conviene definir con el dueño.

## Modpacks y versiones

### Allow to keep mods versions — Hecha la opción 1 (2026-10-01) · i-7985ee-43df2f
- **En qué quedó (la app observa):**
  - `POST /v1/server/{id}/scan` lista `mods/`, baja por URL firmada solo los jars nuevos o
    cambiados (ruta, tamaño, fecha) y los hashea sin cargarlos, con un límite de 128 MiB.
  - Identifica por sha1 con `POST /version_files`. Una versión cuenta solo si uno de sus archivos
    tiene ese sha1.
  - Guarda un `ServerScan` en Mongo. `full=true` vuelve a hashear todo (restaurar un backup
    conserva fechas viejas). Un jar desconocido conserva su hash y se vuelve a preguntar sin
    bajarlo.
  - Nada escribe en el servidor: lo garantiza la puerta d-7985ee-5739fc.
- **Chequeo:** nueve tests puros en `tests/test_scan.py` y nueve del servicio en
  `tests/test_server_scan.py`.
- **Con Modrinth real:** tres jars reales bajados del CDN se identificaron con su versión exacta
  (`xS0Q8LSi`, `d5ddUdiB`, `bNnaTiuM`), y uno hecho a mano quedó como desconocido.
- **Encontrado:** MongoDB devuelve las fechas sin zona y truncadas a milisegundos, y la caché no
  coincidía nunca. Ahora se normalizan a UTC y milisegundos.
- **Lo que falta:**
  - el primer escaneo de un servidor grande por `files/compress`, que escribe;
  - ~~pausar ante un 429 del panel~~: hecho. Las lecturas esperan `Retry-After`, como máximo
    60 s, y lo intentan 4 veces; las escrituras nunca (`tests/test_rate_limit.py`);
  - **probar contra el panel real**, que es el punto de control de la fase 2 y le toca al dueño;
  - la opción 2 (packwiz), si se decide en ese punto de control.
Saber qué versión de cada mod tiene instalada cada servidor, para poder actualizarla.

**El problema medido.**
- Ni Pterodactyl (panel 1.15.1, Wings 1.13.3) ni Pelican exponen hashes de archivos. Los
  mantenedores rechazaron ponerlos en el listado (pterodactyl/panel#4894, cerrado en 2026-01).
- `files/list` da nombre, tamaño y `modified_at`.
- Identificar un jar por su metadata (`fabric.mod.json`, `mods.toml`, `plugin.yml`) acertó en 4 de
  9 jars reales, y en un caso devolvió un proyecto equivocado. Por hash sha1 (`POST /version_files`)
  acertó 9 de 9 (`docs/references.md` §Seguimiento de versiones).

**Opciones, y lo que cada una pide del panel.**
1. **Leer el estado del servidor.** Es lo que hace la Modrinth App.
   - Se lista `mods/` y se usa `(ruta, tamaño, modified_at)` como clave de caché.
   - Solo se descargan los jars nuevos o cambiados: una URL firmada de un solo uso por jar, a 256
     requests/min.
   - Se calcula el sha1 y se resuelve con `POST /version_files`.
   - El primer escaneo cabe en `files/compress` → una descarga → `files/delete`, hasheando cada
     miembro del tar.
   - Funciona hoy, sin tocar el servidor.
2. **Empujar un manifiesto** (packwiz). La app es la fuente de verdad.
   - La app sirve `pack.toml`, `index.toml` y los `.pw.toml`.
   - Al arrancar, el servidor corre `packwiz-installer-bootstrap -g -s server <url>`, que solo baja
     lo que cambió y borra lo que salió.
   - Lo instalado se lee de `packwiz.json` con `files/contents`, sin descargar ningún jar.
   - Pide un cambio único de startup (egg o variable), que el nodo alcance la URL de la app, y
     `--bootstrap-no-update` para no depender de GitHub.
3. **Instalar con `files/pull`** desde el CDN de Modrinth y guardar el sha512 al instalar.
   - Es confiar en la descarga: Wings no verifica el hash.
   - Tiene un límite de 5 pulls cada 10 minutos por servidor.

**Con qué choca.**
- La opción 1 cachea un valor derivado con clave de tiempo: restaurar un backup o descomprimir
  conserva el mtime viejo (tarjeta `derived-copy-goes-stale-silently`). Necesita un re-hash completo
  periódico.
- La 2 y la 3 hacen escrituras sobre servidores reales: no se reintentan (d-7985ee-05d032).
- Un jar del servidor es un archivo no confiable: se parsea con límites de tamaño y nunca se carga
  (tarjeta `untrusted-package-is-parsed-never-loaded`).
- Un índice nuevo sobre versiones toca i-7985ee-be55dd.

**Lo que ya está a favor.**
- `ModrinthVersion` guarda `files[].hashes` con sha1 y sha512.
- `PterodactylVersion.hashes` existe y está vacío.
- El cliente ya sabe descargar (`server_files_download`), listar y subir.

**Lo que hacen los demás** (`research/2026-10-plataformas-similares.md`): todas las herramientas
que actualizan con seguridad escriben un manifiesto de lo instalado. Un plugin de Pelican ya hace
la opción 1: hash por archivo y re-hash solo si cambió el tamaño o la fecha. Esa es la base de
i-7985ee-5c7506.

**Qué decidir primero.**
- ¿El modelo es "la app manda" (2), "la app observa" (1), o las dos: la 1 para importar servidores
  existentes y detectar cambios manuales, y la 2 para instalar? `plan.md` recomienda la 1 en la
  fase 2 y deja la 2 para la fase 4.
- ¿El nodo de Pterodactyl puede alcanzar la API?

### Resolver un modpack: lock con dependencias, reemplazos e incompatibilidades — Hecha (2026-10-01) para mods · i-7985ee-7f735a
- **En qué quedó:**
  - `src/library/resolution.py` es una función pura de pedido y catálogo a lock, que dice qué le
    falta (`needs_projects`, `needs_versions`).
  - `ModpackService.resolve` arma el pedido desde el bundle, trae de Modrinth lo que el lock pide
    hasta completarlo y guarda en el catálogo lo traído.
  - `POST /v1/modpack/{id}/resolve`, y las reglas globales en `/v1/replacement`.
  - El bundle suma `channels`, `pins`, `replacements` y `server_decisions`, con defaults: los
    guardados antes cargan igual.
  - Las decisiones d-7985ee-86fffa, d-7985ee-20b06b y d-7985ee-f0c7a6.
- **Chequeo:**
  - 22 tests puros en `tests/test_resolution.py`, cuatro de ellos sobre respuestas grabadas, y
    cuatro del servicio contra Mongo. Se vieron fallar plantando dos errores: un conflicto ignorado
    y un `environment` ausente leído como "ambos".
  - **Contra Modrinth real** (2026-10-01, Mongo descartable): un pack de 6 proyectos para 26.3
    resolvió 8 entradas. Iris trajo la Sodium exacta que fija; Mod Menu, Fabric API y otra
    dependencia; tres mods ambiguos quedaron para el admin.
- **Encontrado en el camino:**
  - `project_versions` siempre mandaba `featured`, que filtra a *solo* destacadas o *solo* no
    destacadas: nunca llegaban todas. Ahora lo omite y pide `include_changelog=false`.
  - `GET /v1/modpack/{id}/project` devolvía siempre `[]`: con `fetch_links`, Beanie filtra
    después del `$lookup`, y `modpack.$id` ya no existe.
- **Lo que falta:**
  - el lock no se guarda; congelarlo es un build (i-7985ee-098611);
  - shaders y resourcepacks (i-7985ee-60822c).
Requisitos 2 y 3 de `docs/requirements.md`. Dada una versión de juego, un loader, proyectos, reglas
de reemplazo, pins y canales, produce un lock con la razón de cada entrada y los conflictos
reportados. El algoritmo está en `research/2026-10-resolucion-y-formatos.md` §5.
- **Lo que choca:** depende de i-7985ee-f4ba63. Las respuestas de Modrinth cambian con el tiempo:
  el resultado se congela en un build (i-7985ee-098611), nunca se recalcula en el despliegue.
- **A favor:** `ModrinthVersion.dependencies` ya modela `dependency_type`.
- **Qué decidir primero:** ¿los reemplazos son por pack o globales? ¿Hace falta backtracking, o
  basta con reportar el conflicto?

### Seguir el modelo nuevo de Modrinth: environment, versiones por año y dependencias por versión — Hecha (2026-10-01) · i-7985ee-f4ba63
- **En qué quedó:**
  - `environment`: en el proyecto, una lista; en la versión, un valor. `client_side` y
    `server_side` pasan a opcionales. Lo guardado antes carga, y un proyecto sin los lados viejos
    también.
  - `CustomEnum` lee como `unknown` un valor nuevo, si el enum tiene `unknown`.
  - Los loaders de Modrinth son `list[str]`, porque son un conjunto abierto: un shader trae `iris`
    y `optifine`, y con el enum el parseo fallaba.
  - `newest_first` ordena versiones de juego por el tag de Modrinth (`game_versions()` en el
    cliente). Una versión desconocida es un error, no una suposición.
  - `version_dependencies(version_id)` reemplaza a `project_dependencies` y guarda la versión, las
    que fija y los proyectos que nombra.
- **Chequeo:** nueve tests en `tests/test_modrinth_catalog.py`, sobre respuestas reales grabadas
  en `tests/fixtures/modrinth/` por `scripts/record_modrinth_fixtures.py`. Los de `environment`
  desconocido y del shader se vieron fallar quitando cada arreglo.
- **Lo que queda para i-7985ee-7f735a:** qué `environment` va en un pack de servidor. Un
  `environment` vacío (el shader) no significa "ambos" (tarjeta en `research/` §5).
- `client_side` y `server_side` están deprecados por `environment`, y `ModrinthProject` los exige.
- Minecraft numera por año (`26.3`): ninguna comparación de versiones puede suponer `1.x.y`.
- `ModrinthService.project_dependencies` usa `/project/{id}/dependencies`, que mezcla todas las
  versiones.
- **Chequeo:** tests con respuestas reales grabadas de la API (tarjeta `test-double-fidelity`); el
  campo nuevo y el viejo conviven (tarjeta `no-simultaneous-deploy`).

### Contenido del pack separado del contenido agregado por el admin — Hecha (2026-10-01) · i-7985ee-063a3a
- **En qué quedó:**
  - `ModpackBundle.projects` es una lista de `PackProject` (`project_id` de Modrinth y `origin`
    pack o admin).
  - Lo del admin pasa por la misma resolución (d-7985ee-b15dd4) y sobrevive a
    `PUT /v1/modpack/{id}/project`, que reemplaza el contenido del pack.
  - Agregar, quitar y reemplazar son actualizaciones atómicas de MongoDB: dos agregados al mismo
    tiempo se conservan.
  - El lock dice el origen de cada entrada.
- **Chequeo:** seis tests en `tests/test_builds.py`. Se vieron fallar plantando un
  leer-modificar-escribir y un reemplazo que no conserva lo del admin.
Requisito 4. Modrinth Hosting y exaroton separan "lo que trae el pack" de "lo que agregó el
dueño", y eso sobrevive a actualizar el pack.
- **Qué decidir primero:** ¿lo agregado por el admin pasa por la misma resolución, o se toma tal
  cual?

### Shaders, resourcepacks y datapacks en la resolución — Hecha (2026-10-01) · i-7985ee-60822c
- **En qué quedó:** cada tipo se resuelve con sus loaders (shader: `iris`, `optifine`, `canvas`,
  `vanilla`; resourcepack: `minecraft`; datapack: `datapack`). Shaders y resourcepacks nunca van
  al servidor y los datapacks siempre (d-7985ee-9170b8). Un tipo desconocido se pide
  (`needs_types`), y un modpack o plugin es un problema. El shader real de las respuestas
  grabadas (`environment: unknown`) resuelve y queda fuera del servidor.
- **También:** un shader para Iris en un pack sin Iris deja una sugerencia de Iris en el lock (no bloquea).
La resolución filtra por el loader de mods del bundle. Un shader trae loaders `iris`/`optifine`,
un resourcepack `minecraft`, un datapack `datapack`: con Modrinth real, un shader del pack salió
`unresolvable`.
- **Qué decidir primero:** ¿un pack de servidor lleva shaders o resourcepacks? Si no, basta con
  separarlos por `project_type` y que no bloqueen el lock. Si sí, cada tipo necesita su loader
  objetivo (el `iris` de un shader depende de que Iris esté en el pack).

### ModpackProject guarda una copia del proyecto de Modrinth, no una referencia — Hecha (2026-10-01) · i-7985ee-a17c2c
- **En qué quedó:** `ModpackProject` desapareció; el bundle guarda el `project_id` de Modrinth.
  Los documentos viejos quedan sin leer en su colección: no hay datos que conservar
  (d-7985ee-30fe05).
`ProjectSourceModrinth.project` es un `Link`, pero dentro de una unión discriminada Beanie no lo
trata como tal: guarda **el documento completo** del proyecto (medido en S5/7f735a), que queda
viejo cuando el catálogo se refresca. La resolución solo lee el `project_id`, que no cambia.
- **Con qué choca:** con i-7985ee-098611, que reemplaza este modelo. Conviene resolverlo ahí:
  guardar el `project_id` de Modrinth, no un `Link`.

### Builds inmutables de un modpack con punteros recomendado y último — Hecha (2026-10-01) · i-7985ee-098611
- **En qué quedó:**
  - `POST /v1/modpack/{id}/build` resuelve y congela un `ModpackBuild` (número, fecha, pedido y
    lock). Nunca se edita.
  - Se rechaza con 409 si el lock tiene problemas, mods sin decidir o no terminó.
  - Un índice único `(bundle_id, number)` impide dos builds con el mismo número.
  - `latest_build` avanza con `$max`. `PUT /v1/modpack/{id}/recommended/{n}` mueve
    `recommended_build`: volver atrás es moverlo a uno anterior.
  - `POST /v1/modpack` acepta solo los campos editables (`ModpackBundleCreate`), no el contenido
    ni los punteros.
- **Chequeo:**
  - cinco tests en `tests/test_builds.py`;
  - **prueba de humo HTTP sobre la imagen, contra Modrinth real:** crear, agregar (404 si el
    proyecto no existe), resolver, 409 con mods sin decidir, decidir, builds 1 y 2, recomendar
    (404 si el build no existe) y 404 para un id inválido.
- **También:** `bundle_get` y `server_get` lanzaban `ValueError`, que daba un 500 ante un id
  inexistente o inválido; ahora dan 404.
Requisito 5, al estilo de Technic Solder: cada resolución congela un build numerado, que nunca se
edita. Los punteros `recomendado` y `último` deciden qué se despliega, así que volver atrás es mover
un puntero.
- **Lo que choca:** reemplaza el modelo actual de `ModpackProject` (un proyecto por fila, sin
  versión).
- **Qué decidir primero:** ¿qué pasa con los bundles existentes en Mongo?

### Manifiesto de lo instalado en cada servidor — A medias (2026-10-01) · i-7985ee-5c7506
- **En qué quedó:** la copia en Mongo existe: es el `ServerScan`, con ruta, tamaño, fecha, sha1,
  sha512 y versión de cada jar.
- **Lo que falta:** escribir el archivo en el servidor y anotar el build de origen. Es una
  escritura (`files/write`): va con la fase 3 y la puerta d-7985ee-5739fc abierta, después del
  punto de control en solo lectura.
Un archivo de estado en `mods/` del servidor (escrito con `files/write`) y su copia en Mongo:
proyecto, versión, ruta, sha512 y el build de origen. Al escanear, solo los jars que el manifiesto
no explica se reconcilian por hash (i-7985ee-43df2f). Los desconocidos van a cuarentena; nunca se
borran.
- **Lo que choca:** el archivo en el servidor puede quedar viejo si alguien edita a mano; el log de
  actividad lo detecta solo en parte (`research/2026-10-operacion-pterodactyl.md` §5).

### Exportar el pack para jugadores — Hecha para .mrpack (2026-10-01) · i-7985ee-7352b3
- **En qué quedó:**
  - `GET /v1/modpack/{id}/build/{n}/mrpack` sirve el build como `.mrpack`, validado contra la
    spec antes de servirse: dominios, HTTPS, rutas, hashes, claves de dependencias y `env`.
  - Cada tipo va a su carpeta. El lado servidor es la decisión del build y el lado cliente sale
    del `environment` (d-7985ee-53a818).
  - El build congela la versión del loader: la del admin (`loader_version`), o la estable de la
    meta de Fabric o la más nueva sin beta de Quilt. Sin versión de loader (NeoForge y Forge sin
    fijar), exportar da 409.
  - Cada entrada del lock guarda su tipo.
- **Chequeo:** 18 tests puros en `tests/test_mrpack.py`, tres del servicio y el contrato de las
  metas.
- **Con datos reales:** un build de Iris, Lithium, Fabric API y un shader para 26.3 se exportó, y
  **cada archivo bajado de su URL coincidió** en sha1, sha512 y tamaño con el índice.
- **Lo que falta:** packwiz, si se decide en el punto de control de la fase 2. Las `overrides/`
  (configuraciones) no se exportan: el pack no las guarda todavía.
`.mrpack` (y packwiz si se elige la opción 2) desde el mismo build que corre en el servidor, para
que los jugadores coincidan. Solo URLs de los cuatro dominios permitidos; nunca embeber jars de
terceros (CurseForge `allowModDistribution`).
- **Chequeo:** validar cada export contra su spec antes de servirlo (tarjeta
  `validate-each-transformation-run`).
- Posiblemente es i-7985ee-1ba498.

### Versiones de servidores de Pterodactyl — Hecha (2026-10-01) · i-7985ee-4364a3
- **En qué quedó:**
  - `GET /v1/server/{id}/compare?bundle_id=…[&build=n]` compara el último escaneo con un build:
    por defecto el recomendado, y si no hay, el último.
  - Dice qué coincide, qué tiene otra versión, qué falta, qué sobra y qué es desconocido. Solo
    cuentan las entradas del build que van en el servidor. Desconocido o sobrante se reporta;
    nunca se borra.
  - `PterodactylVersion` y `PterodactylModpack` desaparecieron (d-7985ee-0277b6), y la baseline de
    documentos sin registrar quedó vacía.
El trabajo de `wip pterodactyl version`.
- **Mecanismo:** `version_create` guarda un `PterodactylVersion` por archivo de un directorio.
- **Contenido que falta:** no hay hash (depende de i-7985ee-43df2f).
- `version_create` está anotado como `PterodactylServer`, pero devuelve una lista.
- `version_update` no existe.
- `PterodactylModpack` no está registrado en Beanie (`UNREGISTERED_BASELINE` en `scripts/audit.py`).

### Una versión de Modrinth por fila, muchas por proyecto — Hecha (2026-10-01), sin desplegar · i-7985ee-be55dd
- **En qué quedó:**
  - `ModrinthVersion.project_id` es `Indexed()` sin `unique`.
  - En el arranque, `drop_unique_index` (`src/model/__init__.py`) borra el índice viejo si sigue
    siendo `unique`. Es idempotente y no toca datos. Medido: con el índice viejo, `init_beanie`
    no arranca (`OperationFailure`, mismo nombre con otras opciones). Se descartó
    `allow_index_dropping`, que borraría cualquier índice ajeno a los modelos.
  - Los lotes de Modrinth son `project_upsert_many` y `version_upsert_many`: un upsert por clave
    natural (`project_id`, `version_id`), deduplicado. Un proyecto ya guardado se refresca en vez
    de cortar el lote. Reemplazan a los `insert_many` de Modrinth.
  - Los `find_one({"id": …})` desaparecieron. Además, el `upsert` sin actualización lanzaba
    `TypeError`: `project_version_update_*` y `project_dependencies` no funcionaron nunca.
- **Lo que queda:** los `insert_many` de modpack y Pterodactyl siguen con `ordered=True`; hoy
  no les afecta, porque esos modelos no tienen índices `unique`.
`ModrinthVersion.project_id` tiene `Indexed(unique=True)`, así que la segunda versión de un mismo
proyecto falla al insertar.
- **Qué decidir primero:** si producción ya tiene datos, el índice existente se borra a mano; Beanie
  no lo quita.
- En el mismo cambio: los `find_one({"id": version.id})` de `src/services/modrinth.py` buscan un
  campo que no existe (`id` en vez de `version_id`).
- **Los `*_insert_many` pierden filas en silencio.** Los cinco de `src/database/` llaman a
  `insert_many` con `ordered=True`, el default de PyMongo, que ante un error "aborta las
  inserciones restantes". Un lote con un proyecto ya guardado (un `unique` de `project_id`,
  `slug` o `version_id`) inserta los anteriores, pierde los siguientes y lanza
  `BulkWriteError` sin decir qué quedó. Así perdía filas una migración en otro repositorio. Qué
  decidir: upsert por la clave natural, o `ordered=False` y reportar los duplicados. El test:
  un lote con un duplicado en el medio, contra el fixture `mongo`.

### Los updates escriben el dato nuevo — Hecha (2026-10-01), sin desplegar · i-7985ee-055362
- **En qué quedó:** `project_update` y `server_update` guardan lo que devuelve la API, con el `_id`
  del documento guardado (los argumentos de `*_save` estaban al revés). `project_update_many`
  empareja por `project_id`, no por el orden en que contesta Modrinth, que no lo promete. Cinco
  tests en `tests/test_database.py`, vistos fallar antes.
`ModrinthService.project_update(_many)` y `PterodactylService.server_update` guardan el documento
viejo con el `id` de un objeto recién parseado (`None`), así que el dato nuevo nunca se escribe.
- **Chequeo:** un test con una base Mongo real o un doble fiel (tarjeta `test-double-fidelity`).
- **Qué decidir primero:** ¿se acepta `mongomock-motor`, o un MongoDB en CI (servicio de GitHub
  Actions)?

### API handler for generators — Planeada, sin definir · i-7985ee-1ba498
Del roadmap original.
- **Qué decidir primero:** qué es un "generator" aquí. ¿Generar el modpack (`.mrpack` o packwiz) a
  partir de un bundle? Si es eso, es la mitad de la opción 2 de i-7985ee-43df2f.

## Integraciones externas

### Los clientes siguen el contrato de Modrinth y Pterodactyl — Hecha (2026-10-01) · i-7985ee-fb00ca
- **En qué quedó:** nueve tests en `tests/test_api_contracts.py`, contra un servidor aiohttp local
  que valida método, ruta, query y cuerpo según la especificación. Todos se vieron fallar antes.
  - Modrinth: `version_files` y `version_files/update` en plural, con arrays, y la respuesta como
    mapa hash → versión (`HashVersionMap`, leído del `openapi.yaml` v2.7.0).
  - Pterodactyl: `command` y `files/delete` por POST con cuerpo JSON. `files/upload` y
    `files/download` son **dos pasos**: un GET al panel devuelve una URL firmada de Wings, válida
    15 minutos, y el archivo va a esa URL **sin la clave del panel**. Antes, `upload` hacía POST
    al panel (405) y `download` devolvía el JSON de la URL en vez del archivo.
  - Un 204 ya no falla: `JsonResponse` no decodifica un cuerpo vacío. Antes `power`, `command` y
    `delete` daban error en cada éxito real y, con `backoff`, **repetían la acción**.
  - `server_files_upload` cambió de firma: `(server_id, directory, filename, fileraw)`. Nadie la
    llamaba.
Modrinth:
- los lotes por hash usan `version_file` en vez de `version_files`;
- mandan strings donde van arrays.

Pterodactyl:
- `server_command` usa GET con query; tiene que ser POST con cuerpo JSON;
- `server_files_delete` manda query en vez de cuerpo.

Hoy ningún router los llama. i-7985ee-43df2f depende de `POST /version_files`.
- **Chequeo:** un test por llamada, contra un servidor aiohttp local que valide método, ruta y
  cuerpo según `docs/references.md`.

### Las escrituras externas no se reintentan — Hecha (2026-10-01) · i-7985ee-05c4a0
- **En qué quedó:** sin `backoff` en las escrituras de Pterodactyl. `retried_writes` es FALLO,
  con `IDEMPOTENT_POSTS` para los tres POST de Modrinth que solo leen; una entrada que ya no
  reintenta también falla. Se vio fallar plantando un `backoff` en `server_power`.
- **También:** `handle_errors` (`src/library/api/utils.py`) llevaba un `backoff` sobre
  `ClientConnectionError` que nunca actuaba (el wrapper convertía toda excepción antes). No
  reintentaba, pero aparentaba hacerlo, y reordenar los decoradores lo habría hecho reintentar
  escrituras. Se borró; el audit no lo veía porque está fuera de los clientes.
Los 6 AVISOS del audit (`server_power`, `server_files_upload`, `server_files_delete` y los POST de
hashes de Modrinth).
- Los POST de Modrinth son lecturas idempotentes por diseño: se pueden marcar como tales.
- Los de Pterodactyl no.
- **Al terminar:** el aviso pasa a FALLO en `scripts/audit.py`.

### Una ClientSession por cliente, no por request — Hecha (2026-10-01) · i-7985ee-57034c
- **En qué quedó:** `HttpAPI` crea su sesión en el primer request y la reusa; un cliente usado
  desde otro event loop recibe una nueva. `close()` la cierra, y el lifespan cierra las de los
  tres clientes al apagar (`close_clients`). El test ve una sola conexión para tres requests
  seguidos; con una sesión por request vio tres.
aiohttp lo desaconseja explícitamente (`docs/references.md`). Requiere cerrar las sesiones en el
`lifespan`.

### Los handlers de respuesta no se comparten entre requests — Hecha (2026-10-01) · i-7985ee-236b45
- **En qué quedó:** `_request` trata los handlers que recibe como prototipos y trabaja sobre una
  copia por llamada. Se reprodujo con dos requests concurrentes en un mismo cliente: el primero
  leía el cuerpo del segundo (`test_concurrent_requests_on_one_client_each_get_their_own_response`).
`DEFAULT_RESPONSE` y los `streamResponse` de clase son instancias únicas que guardan el cuerpo de la
última respuesta. Entre `handle()` y la lectura del llamador hay `await`, así que un request
concurrente puede pisar el cuerpo de otro (por lectura, sin reproducir).
- **Chequeo:** un test con dos requests concurrentes contra un servidor local.

## Operación

### Actualizar los mods de un servidor con backup, verificación y rollback — Planeada · i-7985ee-495708
El procedimiento de 8 pasos de `research/2026-10-operacion-pterodactyl.md`: preflight, parar,
backup, staging, swap a `mods.old/`, arrancar, verificar, rollback A (devolver `mods.old`) o B
(restaurar el backup).
- **Lo que choca:** son escrituras sobre servidores reales. Antes, d-7985ee-05d032 tiene que pasar
  de aviso a fallo (i-7985ee-05c4a0). Un 204 de `power` no prueba nada: hay que volver a leer el
  estado. `restore` trunca sin parar el servidor.
- **A favor:** el cliente ya sabe listar, subir, renombrar y borrar.
- **Depende de:** i-7985ee-0ec295, i-7985ee-63226e, i-7985ee-098611.
- **Qué decidir primero:** el horario y si se avisa a los jugadores antes de parar.

### Trabajos durables en MongoDB con leases — Hecha (2026-10-01), falta el scheduler · i-7985ee-0ec295
- **En qué quedó:** `Job` y `JobDatabase`, con:
  - reclamo atómico con `find_one_and_update`;
  - lease, latido, checkpoint y fin condicionados a seguir siendo el dueño;
  - reintentos con tope, y espera que se duplica entre intentos (30 s a 10 min);
  - un trabajo activo por clave, por índice único parcial sobre `active` (sin `$in`, que exige
    MongoDB 6.0).

  `JobWorker` corre en el lifespan (`JOBS_WORKER: "off"` lo apaga), y `JobContext.step` no repite
  un paso que ya tiene checkpoint. El primer tipo es `server_scan`, de solo lectura:
  `POST /v1/server/{id}/scan/job` y `GET /v1/job/{id}` (d-7985ee-536c67).
- **Chequeo:** once tests contra Mongo real en `tests/test_jobs.py`. Se vieron fallar plantando
  tres errores: reclamar en dos pasos, escribir sin ser el dueño y quitar el índice.
- **Prueba de humo en la imagen:** encolar da 202 y otra vez 409; contra un panel caído, el
  trabajo espera unos 30 s entre intentos.
- **Lo que falta:** el scheduler (escaneos periódicos), y los trabajos que escriben (fase 3,
  i-7985ee-495708).
Una colección `jobs` reclamada con `find_one_and_update`, con latidos, un checkpoint por paso y un
índice único parcial de un trabajo activo por servidor. El worker y el scheduler corren en el
`lifespan` (`research/2026-10-arquitectura.md` §1).
- **Lo que choca:** la entrega es al menos una vez, así que cada paso tiene que ser idempotente
  (tarjeta `retry-over-irreversible-effect`).
- **Depende de:** i-7985ee-ade111.

### Avisos por webhook de Discord — Planeada · i-7985ee-34933b
Un protocolo `Notifier` y una outbox entregada con reintentos que respetan `retry_after`. Un aviso
que falla nunca tumba una actualización (tarjeta `best-effort-side-channels`).

### Las colecciones se llaman como la clase, no como dice `Settings.collection` — Hecha (2026-10-01) · i-7985ee-c1de10
- **En qué quedó:** se borró el `collection` muerto de los seis modelos, sin renombrar nada.
  `test_every_collection_is_named_after_its_class` fija los nombres actuales; se vio fallar
  agregando `name = "project"`. `UsersInternal` declara `use_cache = False` explícito.
Los modelos declaran `class Settings: collection = "project"`, pero Beanie 2 lee `name`. Las
colecciones reales son `ModrinthProject`, `ModrinthVersion`, `UsersInternal`, etc. (medido en S5).
- **Con qué choca:** con los datos de producción, que viven con esos nombres. Cambiar a `name`
  sin migrar los deja huérfanos.
- **Qué decidir:** borrar el `collection` muerto (lo más barato), o renombrar las colecciones con
  un paso del runbook.

### Los usuarios no se leen de una caché que no se invalida — Hecha (2026-10-01), sin desplegar · i-7985ee-c7b593
- **En qué quedó:** `UsersInternal` sin `use_cache`, el único modelo que lo tenía.
  `test_a_user_read_after_a_change_is_the_changed_user` se vio fallar con la caché (leía el
  nombre viejo) y pasa sin ella.
`UsersInternal` tiene `use_cache = True`. La caché de Beanie es un LRU por clase y por proceso
(32 consultas, 10 minutos por defecto). La clave es el texto de la consulta, y **una escritura
no la invalida** (`beanie/odm/cache.py`). Una consulta vacía no queda guardada, así que hoy no
duplica usuarios.
- **Con qué choca:** el día que el usuario tenga roles o permisos, revocar uno tardaría hasta 10
  minutos, y cada worker de uvicorn vería otra versión.
- **Recomendación:** quitar `use_cache` de `UsersInternal`. El costo es una consulta por request
  autenticado, sobre un índice. Ninguna caché en un modelo del que dependa un permiso.
- **Chequeo:** un test con el fixture `mongo` que cambia un usuario y lo vuelve a leer por id.

### Salud y logs estructurados — Hecha (2026-10-01), salvo el latido del worker en /health · i-7985ee-90f944
- **Logs:**
  - todo sale en JSON (`src/library/logging.py`), también las advertencias de Python y uvicorn;
  - `RequestIdMiddleware` acepta un `X-Request-ID` seguro o genera uno, lo devuelve y escribe una
    línea por request sin la query;
  - el access log de uvicorn está apagado.
- **Chequeo:** cinco tests en `tests/test_logging.py`. En la imagen, cero líneas fuera de JSON.
- **En qué quedó:**
  - `/health/live` y `/health/ready`, sin sesión (`src/routers/health.py` → `HealthService` →
    `HealthDatabase.ping`, con 2 s de límite); `ready` da 503 si Mongo no contesta.
  - El compose tiene un healthcheck de la API que usa `ready`, con `urllib`, porque la imagen no
    trae curl.
  - Cuatro tests en `tests/test_health.py`.
  - Probado con podman sobre la imagen construida: 200, y 503 con Mongo detenido; el healthcheck
    sigue al estado real y `/v1` sin sesión da 401.
- **Lo que falta:** los logs JSON con `request_id`, y el latido del worker cuando exista uno
  (fase 3).
`/health/live` y `/health/ready` (ping a Mongo y latido del worker), healthchecks en Compose y logs
JSON con `request_id` y `job_id`.

### Panel del dueño con Jinja2 y HTMX — Planeada · i-7985ee-50d411
Mismo origen y misma sesión, sin CORS ni build de JS. starlette-admin es opcional, para navegar
documentos (podría reemplazar mongo-express).

## Seguridad y despliegue

### Fijar las versiones de las dependencias — Hecha (2026-10-01) · i-7985ee-bb2fde
`requirements.txt` no fijaba ninguna versión y repetía `fastapi`: CI y la imagen instalaban lo más
nuevo cada vez. Es S1 de `plan-de-trabajo.md`.
- **En qué quedó:** las 12 dependencias con `==`, en las versiones con las que el gate ya pasaba;
  `fastapi` una sola vez. La regla `pinned_requirements` del audit lo exige (d-7985ee-cd8f73) y se
  vio fallar sobre el archivo anterior (14 fallos).
- **Lo que falta:** las dependencias transitivas siguen sin fijar (no hay lockfile; decisión
  consciente, como en `bluetooth-sync`). Authlib avisa que su integración con `httpx` está
  deprecada en favor de `httpx2`. **`httpx` no sobra** (revisado el 2026-10-01): el código no lo
  importa, pero `authlib.integrations.starlette_client` sí, y Authlib no lo declara como
  dependencia. Pasar a `httpx2` es una subida de dependencia aparte.

### Los datos de MongoDB sobreviven y sus puertos no se publican — A medias (2026-10-01): en `main`, falta desplegar · i-7985ee-e21d5d
- **En qué quedó:** volúmenes con nombre `mongodb-data` y `mongodb-config`; `mongo:${MONGO_VERSION:?}` sin
  default; 27017 y 8081 atados a `127.0.0.1`; healthcheck con `mongosh` y `service_healthy`. El
  despliegue sigue `docs/runbooks/migrar-mongo-a-volumen.md` (respaldo, versión, restaurar).
- **Lo que falta:** desplegarlo en el servidor siguiendo el runbook.
`docker/docker-compose.yaml` tiene el volumen de Mongo **comentado**: recrear el contenedor borra la
base. Además publica 27017 y 8081 al host, usa `mongo:latest` y no tiene healthchecks.
- **Qué decidir primero:** si producción ya corre así, respaldar antes de tocar el compose.

### Usuario de servicio dedicado en Pterodactyl — Planeada · i-7985ee-63226e
Una clave `ptlc_` de un usuario **no admin**, subusuario de cada servidor con los permisos justos y
`allowed_ips`. La clave de un root admin pasa también la Application API
(`research/2026-10-operacion-pterodactyl.md` §1).

### Tests contra un MongoDB real en CI — Hecha (2026-10-01) · i-7985ee-ade111
- **En qué quedó:** `services: mongo` (`mongo:8.0`) en el job `gate` y el fixture `mongo` de
  `tests/conftest.py`, que inicializa Beanie sobre bases con prefijo propio y las borra
  (`init_databases` en `src/model/__init__.py`). Sin `TEST_MONGODB_URI` se salta en local y falla
  en CI. En local: `./scripts/test-mongo.sh` (d-7985ee-1a2f53).
- **Lo que encontró:** la migración de usuarios antiguos de S3 reventaba (ver i-7985ee-c64e06).
- **Lo que falta:** igualar la versión de CI con la de producción cuando se conozca (runbook §1).
`services: mongo` con versión fija en `check.yml` y una base descartable por sesión. mongomock-motor
no sirve con el cliente async de PyMongo que usa Beanie 2 (`research/2026-10-arquitectura.md` §2).

### La imagen Docker arranca: copia main.py y recibe su configuración — A medias (2026-10-01): en `main`, falta desplegar · i-7985ee-a77cdc
- **En qué quedó:** el Dockerfile copia `main.py` y `src/`; `.env.yaml` se monta de solo lectura
  (d-7985ee-358aa6); `run.sh` falla si falta. El job `image` de CI construye la imagen, importa
  `main` dentro y valida el compose (también que rechace un `MONGO_VERSION` vacío).
- **Lo que falta:** desplegar. El healthcheck de la API ya usa `/health/ready` (i-7985ee-90f944).
`COPY src .` deja `main.py` fuera y aplana `src/`. `docker-compose.yaml` no entrega `.env.yaml` ni
un `env_file`.
- **Qué decidir primero:** ¿la configuración llega montando `.env.yaml` o con `env_file`?
- **Chequeo:** construir la imagen en CI e importar `main` dentro de ella.

### Quién puede iniciar sesión — A medias (2026-10-01): en `main`, falta desplegar · i-7985ee-c64e06
- **En qué quedó:** `authorize` (grupo de `OIDC_ALLOWED_GROUPS`, o email verificado de
  `OIDC_ALLOWED_EMAILS`; d-7985ee-8befe2); identidad `iss` + `sub` con migración en el primer
  login (d-7985ee-361ed0); sesión `https_only` y 12 h; CORS solo con orígenes nombrados; una sesión
  sin usuario da 401. Diez tests en `tests/test_auth.py`.
- **Lo que falta:** crear el grupo en authentik y la variable en `.env.yaml` (runbook §4).
- **Corregido en S4 (sin desplegar todavía):** la migración nunca funcionó. Un usuario guardado
  antes de S3 no tiene `provider.sub`, Beanie no lo podía validar y su login daba 500. Ahora se
  busca y migra en crudo con un `find_one_and_update`. Tres tests en `tests/test_database.py`.
`user_from_provider` crea a cualquier usuario que llegue del IdP, y todos los endpoints solo piden
sesión. Cualquier cuenta del proveedor opera los servidores de Pterodactyl.
- **Qué decidir primero:** una lista permitida, un grupo o rol del IdP, o que el IdP ya restrinja el
  login.

### Los routers pasan por un servicio — Hecha (2026-10-01) · i-7985ee-2bbbfd
- **En qué quedó:** las 14 inyecciones pasaron a métodos de `ModpackService`, `ModrinthService`,
  `PterodactylService` y `UsersService`, sin cambiar lo que devuelve cada endpoint.
  `LAYERING_BASELINE` está vacía. Antes del cambio se escribió `tests/test_routes_auth.py`: toda
  ruta de `/v1` sin sesión da 401 (se vio fallar abriendo una ruta).
- **Mecanismo:** la regla y el audit existen (d-7985ee-16ed28).
- **Contenido que falta:** 14 inyecciones en `LAYERING_BASELINE`.
- Cada una se mueve a su servicio y sale de la lista en el mismo cambio.

## Proceso y herramientas

Fricción vista al menos dos veces, con su costo. Vacío por ahora.

## Cerradas por medición

Ninguna todavía.

## Lo que cada una le cuesta a una invariante

| ¿Rompe alguna invariante? | Idea |
|---|---|
| toca escrituras sobre servidores reales: d-7985ee-05d032 debe pasar de aviso a fallo antes | i-7985ee-43df2f opción 2/3 |
| cambia un índice con datos en producción: no se puede probar solo con el gate | i-7985ee-be55dd |
| cambia el ciclo de vida de los clientes: el test de arranque (d-7985ee-313583) debe cubrir el cierre | i-7985ee-57034c |
| escribe sobre servidores reales: exige d-7985ee-05d032 como fallo, no como aviso | i-7985ee-495708 |
| cambia el modelo de datos de modpacks: los bundles existentes necesitan migración | i-7985ee-098611 |
| toca el compose de producción: respaldar la base antes | i-7985ee-e21d5d |
