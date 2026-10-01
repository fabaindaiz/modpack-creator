# Roadmap

Ideas aceptadas, todavía no construidas. **No es una promesa ni una orden de trabajo**: es dónde
va a chocar cada una, escrito ahora que está claro. Ninguna entrada se borra; cambia de estado
(Planeada · A medias · Hecha · Cerrada por medición · Bloqueada afuera). Terminar un ítem edita
este archivo en el mismo cambio que lo termina. Los ids se acuñan con
`python .agents/tools/bundle.py id i "<idea>"` y no cambian.

## Dónde estamos

- 2026-10-01: el repositorio adoptó el método. Hay un gate en CI (`hatch run gate`) y la app vuelve
  a importar en Python 3.12.
- El último trabajo de producto fue `wip pterodactyl version` (2025-07-24): registrar qué archivos
  tiene un servidor y en qué versión. Quedó a medias: `version_create` existe, `version_update` lanza
  `NotImplementedError`, y no hay forma de saber qué mod es cada jar porque **Pterodactyl no da
  hashes** (i-7985ee-43df2f).
- Antes de seguir con eso, lo más barato y urgente es que la imagen arranque (i-7985ee-a77cdc) y que
  los updates escriban (i-7985ee-055362).
- 2026-10-01: después de una investigación de plataformas similares y de las APIs, el orden
  recomendado está en [`plan.md`](plan.md), en cinco fases. La **fase 0** (seguridad y arranque)
  va primero. Lo más urgente: hoy recrear el contenedor de Mongo borra la base (i-7985ee-e21d5d).

## Modpacks y versiones

### Allow to keep mods versions — Planeada, falta una decisión · i-7985ee-43df2f
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

### Resolver un modpack: lock con dependencias, reemplazos e incompatibilidades — Planeada · i-7985ee-7f735a
Requisitos 2 y 3 de `docs/requirements.md`. Dada una versión de juego, un loader, proyectos, reglas
de reemplazo, pins y canales, produce un lock con la razón de cada entrada y los conflictos
reportados. El algoritmo está en `research/2026-10-resolucion-y-formatos.md` §5.
- **Lo que choca:** depende de i-7985ee-f4ba63. Las respuestas de Modrinth cambian con el tiempo:
  el resultado se congela en un build (i-7985ee-098611), nunca se recalcula en el despliegue.
- **A favor:** `ModrinthVersion.dependencies` ya modela `dependency_type`.
- **Qué decidir primero:** ¿los reemplazos son por pack o globales? ¿Hace falta backtracking, o
  basta con reportar el conflicto?

### Seguir el modelo nuevo de Modrinth: environment, versiones por año y dependencias por versión — Planeada · i-7985ee-f4ba63
- `client_side` y `server_side` están deprecados por `environment`, y `ModrinthProject` los exige.
- Minecraft numera por año (`26.3`): ninguna comparación de versiones puede suponer `1.x.y`.
- `ModrinthService.project_dependencies` usa `/project/{id}/dependencies`, que mezcla todas las
  versiones.
- **Chequeo:** tests con respuestas reales grabadas de la API (tarjeta `test-double-fidelity`); el
  campo nuevo y el viejo conviven (tarjeta `no-simultaneous-deploy`).

### Contenido del pack separado del contenido agregado por el admin — Planeada · i-7985ee-063a3a
Requisito 4. Modrinth Hosting y exaroton separan "lo que trae el pack" de "lo que agregó el
dueño", y eso sobrevive a actualizar el pack.
- **Qué decidir primero:** ¿lo agregado por el admin pasa por la misma resolución, o se toma tal
  cual?

### Builds inmutables de un modpack con punteros recomendado y último — Planeada · i-7985ee-098611
Requisito 5, al estilo de Technic Solder: cada resolución congela un build numerado, que nunca se
edita. Los punteros `recomendado` y `último` deciden qué se despliega, así que volver atrás es mover
un puntero.
- **Lo que choca:** reemplaza el modelo actual de `ModpackProject` (un proyecto por fila, sin
  versión).
- **Qué decidir primero:** ¿qué pasa con los bundles existentes en Mongo?

### Manifiesto de lo instalado en cada servidor — Planeada · i-7985ee-5c7506
Un archivo de estado en `mods/` del servidor (escrito con `files/write`) y su copia en Mongo:
proyecto, versión, ruta, sha512 y el build de origen. Al escanear, solo los jars que el manifiesto
no explica se reconcilian por hash (i-7985ee-43df2f). Los desconocidos van a cuarentena; nunca se
borran.
- **Lo que choca:** el archivo en el servidor puede quedar viejo si alguien edita a mano; el log de
  actividad lo detecta solo en parte (`research/2026-10-operacion-pterodactyl.md` §5).

### Exportar el pack para jugadores — Planeada · i-7985ee-7352b3
`.mrpack` (y packwiz si se elige la opción 2) desde el mismo build que corre en el servidor, para
que los jugadores coincidan. Solo URLs de los cuatro dominios permitidos; nunca embeber jars de
terceros (CurseForge `allowModDistribution`).
- **Chequeo:** validar cada export contra su spec antes de servirlo (tarjeta
  `validate-each-transformation-run`).
- Posiblemente es i-7985ee-1ba498.

### Versiones de servidores de Pterodactyl — A medias · i-7985ee-4364a3
El trabajo de `wip pterodactyl version`.
- **Mecanismo:** `version_create` guarda un `PterodactylVersion` por archivo de un directorio.
- **Contenido que falta:** no hay hash (depende de i-7985ee-43df2f).
- `version_create` está anotado como `PterodactylServer`, pero devuelve una lista.
- `version_update` no existe.
- `PterodactylModpack` no está registrado en Beanie (`UNREGISTERED_BASELINE` en `scripts/audit.py`).

### Una versión de Modrinth por fila, muchas por proyecto — Planeada · i-7985ee-be55dd
`ModrinthVersion.project_id` tiene `Indexed(unique=True)`, así que la segunda versión de un mismo
proyecto falla al insertar.
- **Qué decidir primero:** si producción ya tiene datos, el índice existente se borra a mano; Beanie
  no lo quita.
- En el mismo cambio: los `find_one({"id": version.id})` de `src/services/modrinth.py` buscan un
  campo que no existe (`id` en vez de `version_id`).

### Los updates escriben el dato nuevo — Planeada · i-7985ee-055362
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

### Los clientes siguen el contrato de Modrinth y Pterodactyl — Planeada · i-7985ee-fb00ca
Modrinth:
- los lotes por hash usan `version_file` en vez de `version_files`;
- mandan strings donde van arrays.

Pterodactyl:
- `server_command` usa GET con query; tiene que ser POST con cuerpo JSON;
- `server_files_delete` manda query en vez de cuerpo.

Hoy ningún router los llama. i-7985ee-43df2f depende de `POST /version_files`.
- **Chequeo:** un test por llamada, contra un servidor aiohttp local que valide método, ruta y
  cuerpo según `docs/references.md`.

### Las escrituras externas no se reintentan — Planeada · i-7985ee-05c4a0
Los 6 AVISOS del audit (`server_power`, `server_files_upload`, `server_files_delete` y los POST de
hashes de Modrinth).
- Los POST de Modrinth son lecturas idempotentes por diseño: se pueden marcar como tales.
- Los de Pterodactyl no.
- **Al terminar:** el aviso pasa a FALLO en `scripts/audit.py`.

### Una ClientSession por cliente, no por request — Planeada · i-7985ee-57034c
aiohttp lo desaconseja explícitamente (`docs/references.md`). Requiere cerrar las sesiones en el
`lifespan`.

### Los handlers de respuesta no se comparten entre requests — Planeada · i-7985ee-236b45
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

### Trabajos durables en MongoDB con leases — Planeada · i-7985ee-0ec295
Una colección `jobs` reclamada con `find_one_and_update`, con latidos, un checkpoint por paso y un
índice único parcial de un trabajo activo por servidor. El worker y el scheduler corren en el
`lifespan` (`research/2026-10-arquitectura.md` §1).
- **Lo que choca:** la entrega es al menos una vez, así que cada paso tiene que ser idempotente
  (tarjeta `retry-over-irreversible-effect`).
- **Depende de:** i-7985ee-ade111.

### Avisos por webhook de Discord — Planeada · i-7985ee-34933b
Un protocolo `Notifier` y una outbox entregada con reintentos que respetan `retry_after`. Un aviso
que falla nunca tumba una actualización (tarjeta `best-effort-side-channels`).

### Salud y logs estructurados — Planeada · i-7985ee-90f944
`/health/live` y `/health/ready` (ping a Mongo y latido del worker), healthchecks en Compose y logs
JSON con `request_id` y `job_id`.

### Panel del dueño con Jinja2 y HTMX — Planeada · i-7985ee-50d411
Mismo origen y misma sesión, sin CORS ni build de JS. starlette-admin es opcional, para navegar
documentos (podría reemplazar mongo-express).

## Seguridad y despliegue

### Los datos de MongoDB sobreviven y sus puertos no se publican — Planeada, urgente · i-7985ee-e21d5d
`docker/docker-compose.yaml` tiene el volumen de Mongo **comentado**: recrear el contenedor borra la
base. Además publica 27017 y 8081 al host, usa `mongo:latest` y no tiene healthchecks.
- **Qué decidir primero:** si producción ya corre así, respaldar antes de tocar el compose.

### Usuario de servicio dedicado en Pterodactyl — Planeada · i-7985ee-63226e
Una clave `ptlc_` de un usuario **no admin**, subusuario de cada servidor con los permisos justos y
`allowed_ips`. La clave de un root admin pasa también la Application API
(`research/2026-10-operacion-pterodactyl.md` §1).

### Tests contra un MongoDB real en CI — Planeada · i-7985ee-ade111
`services: mongo` con versión fija en `check.yml` y una base descartable por sesión. mongomock-motor
no sirve con el cliente async de PyMongo que usa Beanie 2 (`research/2026-10-arquitectura.md` §2).

### La imagen Docker arranca: copia main.py y recibe su configuración — Planeada · i-7985ee-a77cdc
`COPY src .` deja `main.py` fuera y aplana `src/`. `docker-compose.yaml` no entrega `.env.yaml` ni
un `env_file`.
- **Qué decidir primero:** ¿la configuración llega montando `.env.yaml` o con `env_file`?
- **Chequeo:** construir la imagen en CI e importar `main` dentro de ella.

### Quién puede iniciar sesión — Planeada, falta una decisión · i-7985ee-c64e06
`user_from_provider` crea a cualquier usuario que llegue del IdP, y todos los endpoints solo piden
sesión. Cualquier cuenta del proveedor opera los servidores de Pterodactyl.
- **Qué decidir primero:** una lista permitida, un grupo o rol del IdP, o que el IdP ya restrinja el
  login.

### Los routers pasan por un servicio — A medias · i-7985ee-2bbbfd
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
