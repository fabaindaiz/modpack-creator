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

## Modpacks y versiones

### i-7985ee-43df2f · Allow to keep mods versions — Planeada, falta una decisión
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

**Qué decidir primero.**
- ¿El modelo es "la app manda" (2), "la app observa" (1), o las dos: la 1 para importar servidores
  existentes y detectar cambios manuales, y la 2 para instalar?
- ¿El nodo de Pterodactyl puede alcanzar la API?

### i-7985ee-4364a3 · Versiones de servidores de Pterodactyl — A medias
El trabajo de `wip pterodactyl version`.
- **Mecanismo:** `version_create` guarda un `PterodactylVersion` por archivo de un directorio.
- **Contenido que falta:** no hay hash (depende de i-7985ee-43df2f).
- `version_create` está anotado como `PterodactylServer`, pero devuelve una lista.
- `version_update` no existe.
- `PterodactylModpack` no está registrado en Beanie (`UNREGISTERED_BASELINE` en `scripts/audit.py`).

### i-7985ee-be55dd · Una versión de Modrinth por fila, muchas por proyecto — Planeada
`ModrinthVersion.project_id` tiene `Indexed(unique=True)`, así que la segunda versión de un mismo
proyecto falla al insertar.
- **Qué decidir primero:** si producción ya tiene datos, el índice existente se borra a mano; Beanie
  no lo quita.
- En el mismo cambio: los `find_one({"id": version.id})` de `src/services/modrinth.py` buscan un
  campo que no existe (`id` en vez de `version_id`).

### i-7985ee-055362 · Los updates escriben el dato nuevo — Planeada
`ModrinthService.project_update(_many)` y `PterodactylService.server_update` guardan el documento
viejo con el `id` de un objeto recién parseado (`None`), así que el dato nuevo nunca se escribe.
- **Chequeo:** un test con una base Mongo real o un doble fiel (tarjeta `test-double-fidelity`).
- **Qué decidir primero:** ¿se acepta `mongomock-motor`, o un MongoDB en CI (servicio de GitHub
  Actions)?

### i-7985ee-1ba498 · API handler for generators — Planeada, sin definir
Del roadmap original.
- **Qué decidir primero:** qué es un "generator" aquí. ¿Generar el modpack (`.mrpack` o packwiz) a
  partir de un bundle? Si es eso, es la mitad de la opción 2 de i-7985ee-43df2f.

## Integraciones externas

### i-7985ee-fb00ca · Los clientes siguen el contrato de Modrinth y Pterodactyl — Planeada
Modrinth:
- los lotes por hash usan `version_file` en vez de `version_files`;
- mandan strings donde van arrays.

Pterodactyl:
- `server_command` usa GET con query; tiene que ser POST con cuerpo JSON;
- `server_files_delete` manda query en vez de cuerpo.

Hoy ningún router los llama. i-7985ee-43df2f depende de `POST /version_files`.
- **Chequeo:** un test por llamada, contra un servidor aiohttp local que valide método, ruta y
  cuerpo según `docs/references.md`.

### i-7985ee-05c4a0 · Las escrituras externas no se reintentan — Planeada
Los 6 AVISOS del audit (`server_power`, `server_files_upload`, `server_files_delete` y los POST de
hashes de Modrinth).
- Los POST de Modrinth son lecturas idempotentes por diseño: se pueden marcar como tales.
- Los de Pterodactyl no.
- **Al terminar:** el aviso pasa a FALLO en `scripts/audit.py`.

### i-7985ee-57034c · Una ClientSession por cliente, no por request — Planeada
aiohttp lo desaconseja explícitamente (`docs/references.md`). Requiere cerrar las sesiones en el
`lifespan`.

### i-7985ee-236b45 · Los handlers de respuesta no se comparten entre requests — Planeada
`DEFAULT_RESPONSE` y los `streamResponse` de clase son instancias únicas que guardan el cuerpo de la
última respuesta. Entre `handle()` y la lectura del llamador hay `await`, así que un request
concurrente puede pisar el cuerpo de otro (por lectura, sin reproducir).
- **Chequeo:** un test con dos requests concurrentes contra un servidor local.

## Seguridad y despliegue

### i-7985ee-a77cdc · La imagen Docker arranca: copia main.py y recibe su configuración — Planeada
`COPY src .` deja `main.py` fuera y aplana `src/`. `docker-compose.yaml` no entrega `.env.yaml` ni
un `env_file`.
- **Qué decidir primero:** ¿la configuración llega montando `.env.yaml` o con `env_file`?
- **Chequeo:** construir la imagen en CI e importar `main` dentro de ella.

### i-7985ee-c64e06 · Quién puede iniciar sesión — Planeada, falta una decisión
`user_from_provider` crea a cualquier usuario que llegue del IdP, y todos los endpoints solo piden
sesión. Cualquier cuenta del proveedor opera los servidores de Pterodactyl.
- **Qué decidir primero:** una lista permitida, un grupo o rol del IdP, o que el IdP ya restrinja el
  login.

### i-7985ee-2bbbfd · Los routers pasan por un servicio — A medias
- **Mecanismo:** la regla y el audit existen (d-7985ee-16ed28).
- **Contenido que falta:** 14 inyecciones en `LAYERING_BASELINE`.
- Cada una se mueve a su servicio y sale de la lista en el mismo cambio.

## Proceso y herramientas

Fricción vista al menos dos veces, con su costo. Vacío por ahora.

## Cerradas por medición

Ninguna todavía.

## Lo que cada una le cuesta a una invariante

| Idea | ¿Rompe alguna invariante? |
|---|---|
| i-7985ee-43df2f opción 2/3 | toca escrituras sobre servidores reales: d-7985ee-05d032 debe pasar de aviso a fallo antes |
| i-7985ee-be55dd | cambia un índice con datos en producción: no se puede probar solo con el gate |
| i-7985ee-57034c | cambia el ciclo de vida de los clientes: el test de arranque (d-7985ee-313583) debe cubrir el cierre |
