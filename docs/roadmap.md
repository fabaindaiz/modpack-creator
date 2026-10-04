# Roadmap

Ideas aceptadas, todavía no construidas. **No es una promesa ni una orden de trabajo**: es dónde
va a chocar cada una, escrito ahora que está claro. Ninguna entrada se borra; cambia de estado
(Planeada · A medias · Hecha · Cerrada por medición · Bloqueada afuera). Terminar un ítem edita
este archivo en el mismo cambio que lo termina. Los ids se acuñan con
`python .agents/tools/bundle.py id i "<idea>"` y no cambian.

## Dónde estamos

Al cierre de la sesión del panel web del 2026-10-02 (detalle en
`.claude/logs/agent-changelog.md`, s-7985ee-aeb6be, y las entradas de abajo). **El panel está en la
rama `feat/panel-web`; lo demás está en `main`; nada está desplegado.**

- **Panel web, hecho en código** (i-7985ee-50d411): páginas con Jinja2 y htmx, roles de editor y
  operador por grupos del IdP, auditoría, defensa CSRF por origen con CSP estricta, y el latido
  del worker en `/health/ready`. Se despliega con `docs/runbooks/desplegar-panel.md`.
- **Panel web, lote siguiente, hecho en código** (i-7985ee-e69c9d, sobre i-7985ee-50d411): el
  detalle de un modpack muestra y edita archivos locales, canales y pins; la vista previa de lo
  que cambia, con changelogs, antes de actualizar o comparar builds; y la revisión diaria de
  versiones nuevas con aviso, página e insignia. Sin desplegar.

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
  respaldo, `docker/.env` con `MONGO_VERSION` y `FORWARDED_ALLOW_IPS`, `OIDC_ALLOWED_GROUPS` y
  `OIDC_OPERATOR_GROUPS` en `.env.yaml`, y restaurar.
  Sin eso el volumen nuevo arranca vacío y la API no arranca.
- **Lo siguiente, todo del dueño, en orden:**
  0. agregar `OIDC_OPERATOR_GROUPS: ""` a `.env.default` (el gate de `audit` falla en
     `env_template` hasta entonces) y crear el grupo de operadores en authentik;
  1. desplegar con los runbooks (`migrar-mongo-a-volumen.md` y `desplegar-panel.md`);
  2. S7 operación: un usuario de servicio no admin, una clave `ptlc_` con `allowed_ips` y revocar
     la vieja (i-7985ee-63226e);
  3. un login real con una cuenta fuera del grupo, otro de editor y otro de operador (con una
     simulación), y las comprobaciones de `desplegar-panel.md`;
  4. los puntos de control de las fases 1 y 2, con `PTERODACTYL_WRITES` vacío: armar el pack de
     un servidor real, escanearlo y comparar. Con eso se decide si se suma packwiz y se abre la
     fase 3, que escribe en servidores.
- **Fase 3, en código, sin ejecutar en real:**
  - importar un servidor (i-7985ee-17745b);
  - actualizarlo con backup, verificación y rollback a pedido (i-7985ee-495708), con modo
    simulación y probado de punta a punta con el cliente HTTP real contra un panel simulado.
  Antes, después del primer cierre: `.mrpack`, logs JSON, trabajos, avisos por Discord,
  servidor-bundle y `httpx2`.
- **Lo siguiente, del dueño, en orden:**
  1. desplegar (runbook de Mongo);
  2. el usuario de servicio y la clave nueva;
  3. el login real;
  4. los puntos de control de las fases 1 y 2;
  5. **la primera actualización real en el servidor de prueba**, con
     `docs/runbooks/primera-actualizacion-real.md`;
  6. decir qué versión de MongoDB corre en producción y, si es menor que 8.0, migrar primero a esa versión y subirla después, con
     "Subir a MongoDB 8.0" de `migrar-mongo-a-volumen.md` (CI ya usa `mongo:8.0`).
- **Pendiente de código:**
  - subir producción a MongoDB 8.0, si no lo está (d-7985ee-83eb5b);
  - packwiz, tras el punto de control de la fase 2.

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

### Cada servidor sabe qué bundle corre — Hecha (2026-10-01) · i-7985ee-b0264e
- **En qué quedó:**
  - `PUT /v1/server/{id}/bundle?bundle_id=…` asocia un servidor a su bundle (vacío lo desasocia).
  - Comparar un servidor ya no exige nombrar el bundle, y los avisos de desvío lo usan.
  - Refrescar el servidor desde el panel conserva la asociación: antes la habría borrado, porque
    `server_update` reemplaza el documento.
- **Chequeo:** tres tests en `tests/test_server_scan.py`.

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

### Importar un servidor: sus jars ajenos al bundle — Hecha (2026-10-01) · i-7985ee-17745b
- **En qué quedó** (d-7985ee-c8a15d): `POST /v1/server/{id}/import` recorre el último escaneo.
  - Lo identificado por hash y ajeno al bundle entra como del admin.
  - Lo desconocido se baja y su metadata se lee **como dato** (`src/library/jarmeta.py`: solo
    los cuatro archivos de metadata, con tope de bytes aunque el zip declare otro tamaño).
    Después se buscan candidatos en Modrinth, por id como slug y por nombre.
  - Lo que no tiene candidatos queda como archivo local del bundle.
  - `.../import/confirm` agrega el proyecto y reclama ese jar (la comparación lo trata como una
    versión a reemplazar); `.../import/local` lo deja como local.
  - Los archivos locales se congelan en el build y no entran al `.mrpack`.
- **Chequeo:**
  - `tests/test_import.py` (5) y `tests/test_jarmeta.py` (11), con jars plantados: bomba de
    compresión, tamaño declarado falso, no-zip y rutas fuera de la lista;
  - aceptar un candidato sin confirmar se vio fallar.
- **Con datos reales:** se leyó bien la metadata de 6 jars reales (Fabric, Quilt, NeoForge y
  Forge). La búsqueda real de candidatos encontró `ferrite-core` para el id `ferritecore`, y nada
  para un mod privado. Para Sodium aparecen también Sodium Extra y Reese's: por eso se confirma.

Antes de actualizar un servidor, todo jar de `mods/` tiene que estar explicado por el build
(d-7985ee-c8a15d). La importación agrega al bundle lo identificado por hash, propone candidatos
por metadata para que el admin confirme, y registra lo demás como archivos locales.

### Actualizar los mods de un servidor con backup, verificación y rollback — Hecha en código (2026-10-01), sin ejecutar en real · i-7985ee-495708
- **En qué quedó** (`src/services/update.py`), con las decisiones d-7985ee-c2adc8, d-7985ee-a13b33,
  d-7985ee-5445a0 y d-7985ee-5cc6ee:
  - `POST /v1/server/{id}/update` encola un trabajo sobre el build recomendado del bundle
    asociado, o el indicado. **Es una simulación salvo `dry_run=false`**, y aun así pasa por la
    puerta `PTERODACTYL_WRITES`.
  - Los pasos, con checkpoint:
    1. preflight sin escribir: escaneo, jars explicados, servidor libre y cupo de backups;
    2. staging verificado antes de parar;
    3. `save-all`, `stop` sondeando hasta offline, y `kill` si no para;
    4. backup bloqueado, buscado antes por nombre;
    5. jars viejos a `mods.old/<trabajo>/`;
    6. arranque y 5 minutos estable;
    7. backup desbloqueado y aviso.
  - **Ante un fallo después del cambio**, el servidor queda parado y se avisa.
    `POST /v1/server/{id}/rollback?update=…&mode=A|B` lo resuelve, solo a pedido. `JobFailed`
    termina un trabajo sin reintentar.
- **Cliente de Pterodactyl:** estado, recursos, backups (listar, crear, leer, bloquear,
  restaurar), `files/rename` y `files/create-folder`, contrastados con el código del panel.
  `ungated_writes` ahora mira las llamadas a `_request`, no la mención de un método.
- **Chequeo:**
  - 14 tests en `tests/test_update.py` contra un panel en memoria con estado (simulación sin
    escrituras, camino feliz, cuatro abortos sin escribir, `kill`, caída y rollback A o B, y un
    reintento que no duplica el backup);
  - `tests/test_update_http.py` corre la actualización completa con el **cliente HTTP real**
    contra ese panel servido en localhost, con las rutas de Wings;
  - se vieron fallar dos errores plantados.
- **Encontrado en el camino:** aiohttp codificaba en porcentaje el nombre de archivo del
  multipart, y Wings lo guarda tal cual: todo jar con `+` (casi todos los de Modrinth) se habría
  subido con otro nombre. Corregido (`quote_fields=False`) y fijado en el contrato.
- **Lo que falta:** **la primera ejecución real**, en el servidor de prueba del dueño, siguiendo
  `docs/runbooks/primera-actualizacion-real.md`. Borrar `mods.old/` y `mods.staging/` es a mano.
El procedimiento de 8 pasos de `research/2026-10-operacion-pterodactyl.md`: preflight, parar,
backup, staging, swap a `mods.old/`, arrancar, verificar, rollback A (devolver `mods.old`) o B
(restaurar el backup).
- **Lo que choca:** son escrituras sobre servidores reales. Antes, d-7985ee-05d032 tiene que pasar
  de aviso a fallo (i-7985ee-05c4a0). Un 204 de `power` no prueba nada: hay que volver a leer el
  estado. `restore` trunca sin parar el servidor.
- **A favor:** el cliente ya sabe listar, subir, renombrar y borrar.
- **Depende de:** i-7985ee-0ec295, i-7985ee-63226e, i-7985ee-098611.
- **Qué decidir primero:** el horario y si se avisa a los jugadores antes de parar.

### Trabajos durables en MongoDB con leases — Hecha (2026-10-01) · i-7985ee-0ec295
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
- **Programador:** con `SCAN_EVERY_HOURS`, encola el escaneo de cada servidor cuyo último
  escaneo es más viejo que ese intervalo. Lo pide completo si el último completo tiene más de
  `SCAN_FULL_EVERY_DAYS`, por defecto 7. El índice por servidor evita duplicados entre procesos.
- **Trabajos que escriben:** hechos en la fase 3: `server_update` y `server_rollback`
  (i-7985ee-495708), con la misma clave por servidor, así que un servidor no tiene dos a la vez.
- **Lo que falta:** nada de lo planeado.
Una colección `jobs` reclamada con `find_one_and_update`, con latidos, un checkpoint por paso y un
índice único parcial de un trabajo activo por servidor. El worker y el scheduler corren en el
`lifespan` (`research/2026-10-arquitectura.md` §1).
- **Lo que choca:** la entrega es al menos una vez, así que cada paso tiene que ser idempotente
  (tarjeta `retry-over-irreversible-effect`).
- **Depende de:** i-7985ee-ade111.

### Avisos por webhook de Discord — Hecha (2026-10-01) · i-7985ee-34933b
- **En qué quedó:**
  - `Notifier` encola cada aviso como un trabajo `notify`. Sin `DISCORD_WEBHOOK_URL` no hace
    nada, y si encolar falla deja la línea `notification not queued` sin propagar el error.
  - El envío es un paso con checkpoint. Ante un 429, el trabajo espera el `Retry-After` de
    Discord (`RetryLater`).
  - El texto no menciona a nadie (`allowed_mentions.parse` vacío) y se corta a 2000 caracteres.
  - El token del webhook vive en la configuración: nunca en un trabajo ni en un log.
  - **Eventos:** un build nuevo, y un servidor que se desvía de su build o vuelve a coincidir.
    Solo cuando cambia el estado: `ServerScan.drift` guarda el último anunciado.
- **Chequeo:** siete tests en `tests/test_notify.py`, más uno en `tests/test_builds.py`.
- **Prueba de humo en la imagen**, con un webhook falso en la red de podman: el build nuevo llegó
  como `📦 Smoke pack: build 1 creado, 2 archivos.` y el token no aparece en los logs.
- **Avisos de las actualizaciones:** hechos en la fase 3. `UpdateService` avisa un fallo, un éxito
  y un rollback, y una simulación no avisa (`_notify` recibe `dry_run`).
- **Lo que falta:** nada de lo planeado.
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

### Salud y logs estructurados — Hecha (2026-10-02) · i-7985ee-90f944
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
- **Latido del worker (2026-10-02):** `WorkerBeat` late en cada vuelta y mientras corre un
  trabajo; `/health/ready` da 503 si el último latido tiene más de 120 s o la tarea terminó. Si el
  proceso no corre worker (`JOBS_WORKER: "off"`) no lo exige. **Chequeo:** `tests/test_health.py` y
  `tests/test_jobs.py`.
- **Logs con `request_id` y `job_id`:** hechos. Cada línea de un request lleva `request_id`, y las
  de un trabajo, `job_id` y `kind`.
- **Lo que falta:** nada de lo planeado.
`/health/live` y `/health/ready` (ping a Mongo y latido del worker), healthchecks en Compose y logs
JSON con `request_id` y `job_id`.

### Panel del dueño con Jinja2 y HTMX — Hecha en código (2026-10-02), sin desplegar · i-7985ee-50d411
- **En qué quedó:**
  - Páginas en español con Jinja2 y htmx, en el mismo origen y con la misma sesión, sin CORS:
    tablero, modpacks (crear, agregar y quitar proyectos, resolver, decidir qué va al servidor,
    armar y recomendar builds, descargar el `.mrpack`, y los reemplazos globales), servidores
    (escanear, importar, vincular, simular, actualizar y hacer rollback), trabajos con
    seguimiento y auditoría paginada (`src/routers/web/`, `src/web/`). No hay página para editar
    los datos de un modpack (nombre, versión, loader): eso sigue en `/v1`.
  - El CSS se compila con Tailwind y htmx sale de npm, en una etapa Node de la imagen
    (d-7985ee-d948fc). Esto reemplaza el "sin build de JS" de la idea original; starlette-admin
    quedó sin hacer.
  - Dos roles por grupos del IdP, que se hacen cumplir en los servicios (d-7985ee-edef1e), y una
    auditoría de cada cambio, escrita antes de encolar lo que toca un servidor real
    (d-7985ee-b4719f).
  - Defensa CSRF por origen y CSP estricta (d-7985ee-491c83).
  - Una actualización real se ofrece por dos caminos (d-7985ee-aca3e8): directo, con un build
    elegido, o desde una simulación terminada, con su build; los dos fijan el build y el modpack
    para el que se planeó (si el servidor cambió de modpack, 409), y avisan cuando la actualización
    descarta un rollback pendiente (R17). Un rollback se rechaza si es de una simulación o de una actualización ya superada por cualquier
    otra real, haya escrito o no. Un rollback fallido no cierra la actualización; tras un A
    exitoso solo queda el B, escribiendo el nombre del servidor, y tras un B exitoso nada
    (d-7985ee-5445a0).
  - Un `/v1/login` o `/v1/logout` con un `redirect` de `//host` da 422 (era un redirect abierto).
  - **Addendum del 2026-10-03** (repaso de decisiones con el dueño, plan
    `docs/superpowers/plans/2026-10-03-panel-web-addendum.md`): se puede actualizar directo a un
    build elegido o desde una simulación (d-7985ee-aca3e8); la tabla de roles de
    `docs/decisions.md` la lee un barrido que clasifica toda ruta que escribe; la búsqueda de
    Modrinth muestra autor, descargas y lado; la auditoría filtra por fechas; "Guardar decisiones"
    en lote y atómico; `/jobs` como tabla con filtros y una página por trabajo; la revisión visual
    como `scripts/visual-sweep.sh`; una sola regla de `redirect`; y MongoDB 8.0 en todos lados con
    su camino de subida (d-7985ee-83eb5b).
- **Chequeo:** `tests/test_web_security.py`, `tests/test_roles.py`, `tests/test_routes_roles.py`,
  `tests/test_operator_actions.py`, `tests/test_audit_log.py`, `tests/test_web_*.py`, contra Mongo
  real. La revisión visual se hizo con un Chromium sin interfaz: 56 capturas (operador en claro y
  oscuro a 360, 768 y 1280 px, y editor en claro a 1280, por 8 páginas); encontró 3 problemas,
  corregidos en 23bfbae, y la segunda pasada dio 0. La imagen arm64 se construyó con podman local
  el 2026-10-03 (`main` importa, los assets están, el compose rechaza sin `MONGO_VERSION` o
  `FORWARDED_ALLOW_IPS`). Lo que **no** se comprobó: la imagen x64 (CI), un login real de editor y de operador, y Pterodactyl y Modrinth reales; el
  despliegue lo comprueba `docs/runbooks/desplegar-panel.md`.
- **Lo que falta:**
  - desplegar;
  - ~~el detalle de un modpack no muestra los archivos locales, los canales ni los pins (diseño
    del panel §3.2)~~: cerrado el 2026-10-03 (i-7985ee-e69c9d).
- **Lo que choca:** una acción de operador que se revoca vale hasta que vence la sesión (12 h
  como máximo; rotar `SESSION_SECRET` la corta).

### Vista previa de una actualización y revisión diaria de versiones nuevas — Hecha en código (2026-10-03), sin desplegar · i-7985ee-e69c9d
- **En qué quedó** (diseño `docs/superpowers/specs/2026-10-03-panel-web-next-design.md`, plan
  `docs/superpowers/plans/2026-10-03-panel-web-next.md`). Cierra dos de las "Ideas para más
  adelante" de `docs/plan.md` (la vista previa y la revisión nocturna, esta última diaria y con
  botón por decisión del dueño):
  - **Detalle del modpack:** muestra los archivos locales, y edita los canales y los pins; un pin
    a una versión que no es del proyecto, o un id con `.` o `$`, da 422 sin escribir.
  - **Vista previa de los cambios:** qué se agrega, quita o cambia, con el número de versión y el
    changelog de la nueva, en la simulación (`/servers/{id}/update/preview`,
    `/jobs/{id}/preview`), en el diálogo "Actualizar…" y en la comparación de dos builds
    (`/modpacks/{id}/builds/compare`). Se carga aparte, al pedirla, con 10 s en total para los
    changelogs; si Modrinth falla, "changelog no disponible" y la página sigue (d-7985ee-d88e90).
  - **Revisión de actualizaciones** del build recomendado (`/modpacks/{id}/actualizaciones`), con
    `POST /version_files/update`, que ignora los pins (d-7985ee-cadd66). Se guarda como
    `UpdateCheck`; avisa por Discord con los nombres de los mods, y el tablero muestra una
    insignia. Ofrece crear un build nuevo; nada se aplica solo.
  - **Programación diaria** con `UPDATE_CHECK_AT` y `UPDATE_CHECK_TZ`, ambas opcionales
    (d-7985ee-2e2f58); sin la primera no corre, y el botón sigue.
  - Un error de Modrinth o Pterodactyl (429, 502, 503, 504) sale como aviso del panel, no como 500.
  - **Un trabajo sin `payload.dry_run` cuenta como simulación en todos lados** (falla cerrado). El
    dueño debe confirmar que ningún trabajo real de producción carece de esa clave.
- **Chequeo:** `tests/test_changes.py`, `tests/test_update_check.py`, `tests/test_update_schedule.py`
  (con el cambio de horario de Chile), `tests/test_web_updates.py`, `tests/test_api_contracts.py`,
  contra Mongo real; la revisión visual del panel; y la imagen arm64 carga `America/Santiago`.
- **Lo que falta:**
  - desplegar (`docs/runbooks/desplegar-panel.md` explica el índice nuevo y las variables);
  - una falla entre calcular la revisión y guardarla (el aviso o el insert lanzan) no deja
    ningún `UpdateCheck`: el programador la reencola cada 5 minutos y vuelve a pedir a Modrinth;
  - una revisión manual hecha antes de `UPDATE_CHECK_AT` cuenta como la de hoy;
  - las revisiones guardadas antes de este lote no tienen ids de versión: muestran la insignia,
    pero no changelogs;
  - un pin queda en el modpack aunque se quite el proyecto, y otros menores del registro.
- **Lo que choca:** la revisión depende de que Modrinth responda; un día sin respuesta deja un
  `UpdateCheck` con `error`, sin aviso. Un job viejo sin `dry_run` se ve como simulación.

## Seguridad y despliegue

### Fijar las versiones de las dependencias — Hecha (2026-10-01) · i-7985ee-bb2fde
`requirements.txt` no fijaba ninguna versión y repetía `fastapi`: CI y la imagen instalaban lo más
nuevo cada vez. Es S1 de `plan-de-trabajo.md`.
- **En qué quedó:** las 12 dependencias con `==`, en las versiones con las que el gate ya pasaba;
  `fastapi` una sola vez. La regla `pinned_requirements` del audit lo exige (d-7985ee-cd8f73) y se
  vio fallar sobre el archivo anterior (14 fallos).
- **Lo que falta:** las dependencias transitivas siguen sin fijar (no hay lockfile; decisión
  consciente, como en `bluetooth-sync`). Authlib avisa que su integración con `httpx` está
  deprecada en favor de `httpx2`. **Cambiado a `httpx2==2.13.1`** (2026-10-01): Authlib 1.8 lo
  usa si está y cae a `httpx` deprecado si no. Un test lo exige sin la advertencia. No verificado:
  el login real con authentik sobre `httpx2`.

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
