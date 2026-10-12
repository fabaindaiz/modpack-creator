# Detectar restauraciones de backup desde la Client API de Pterodactyl

Fecha: 2026-10-12. Fuentes: código de `pterodactyl/panel` en el tag `v1.15.1` (último release),
`pterodactyl/wings` en `v1.13.3` (último release), `pelican-dev/panel` y `pelican-dev/wings` en
`main`, y `spatie/laravel-query-builder` `6.4.4` (versión fijada en el `composer.lock` del panel).
Todo leído con `gh api repos/<repo>/contents/<ruta>?ref=<tag>`. Los números de línea son de esos
tags. Lo no verificado en código va marcado **SUPUESTO**.

Documentación oficial: el repositorio `pterodactyl/documentation` (rama `master`, 193 entradas)
no tiene ninguna página de la Client API ni de actividad; la referencia de la API no es oficial.
Por eso todo lo de abajo sale del código.

Contexto del problema: la app cachea hashes de `/mods/*.jar` por (ruta, tamaño, mtime). Wings,
al restaurar un backup, **reescribe el mtime del archivo con el del backup** (ver 3.1), así que un
jar restaurado puede coincidir en (ruta, tamaño, mtime) con una entrada vieja de la caché aunque su
contenido sea otro.

---

## 1. `GET /api/client/servers/{server}/activity`

**Existe.**

- Ruta: `routes/api-client.php:70`
  `Route::get('/activity', Client\Servers\ActivityLogController::class)->name('api:client:server.activity');`
  (dentro del grupo `/servers/{server}`, con prefijo `/api/client` y middleware
  `throttle:api.client`, `app/Providers/RouteServiceProvider.php:56`).
- **Desde qué versión:** el endpoint por servidor aparece en **v1.9.0**. Comprobado leyendo
  `routes/api-client.php` en cada tag: en `v1.8.1` solo está `/api/client/account/activity`
  (línea 33); en `v1.9.0` ya está la de servidor (línea 65). El `CHANGELOG.md` de v1.9.0 (línea
  300) dice "Adds a new activity log view for both user accounts and individual servers". Los
  eventos de Wings (SFTP, uploads, power, consola) llegan a partir de **v1.10.0** (`CHANGELOG.md:258`,
  sección v1.10.0 en la línea 240). Los eventos de restauración ya existen en v1.9.0
  (`BackupController.php:219` y `BackupStatusController.php:95` en ese tag).
- Controlador: `app/Http/Controllers/Api/Client/Servers/ActivityLogController.php:22-53`.
  - Permiso: `$this->authorize(Permission::ACTION_ACTIVITY_READ, $server)` (línea 24);
    `ACTION_ACTIVITY_READ = 'activity.read'` (`app/Models/Permission.php:66`). El dueño del
    servidor y un root admin pasan siempre (`app/Policies/ServerPolicy.php:26-28`, `before()`);
    un subusuario necesita `activity.read`. Una clave `ptlc_` actúa como su usuario.
  - **Ordenamiento:** `->allowedSorts(['timestamp'])` (línea 28). Parámetro `sort=timestamp`
    (ascendente) o `sort=-timestamp` (descendente). **No hay orden por defecto**: no se llama a
    `defaultSort`, así que sin `sort` la consulta va sin `ORDER BY` y el orden lo decide la base
    (**SUPUESTO**: en la práctica, por `id` ascendente, o sea el más viejo primero). El panel web
    siempre manda `sorts: { timestamp: -1 }`
    (`resources/scripts/components/server/ServerActivityLogContainer.tsx:19`). **Siempre manda
    `sort=-timestamp`.**
  - **Filtros:** solo `filter[event]`, parcial: `->allowedFilters([AllowedFilter::partial('event')])`
    (línea 29). En spatie 6.4.4 un filtro parcial es `LOWER(event) LIKE '%valor%'`
    (`src/Filters/FiltersPartial.php`, `getWhereRawParameters`), y un valor con comas se separa
    en un array (`src/QueryBuilderRequest.php:19`, delimitador `','`) que se combina con **OR**
    (`FiltersPartial.php`, rama `is_array($value)` con `orWhereRaw`). Así
    `filter[event]=backup.restore,file.decompress` trae las dos familias en una sola consulta.
    Un filtro no permitido (por ejemplo `filter[ip]`, que el frontend tipa pero el endpoint de
    servidor no acepta) lanza `InvalidFilterQuery` → **400**, porque
    `disable_invalid_filter_query_exception` es `false` por defecto (`config/query-builder.php:39`
    de spatie; el panel no publica su propio `config/query-builder.php`: 404 en `v1.15.1`).
    No hay filtro por fecha: el "desde" se resuelve ordenando y comparando `timestamp` en el cliente.
  - **Paginación:** `->paginate(min($request->query('per_page', 25), 100))` (línea 47): 25 por
    defecto, máximo 100, con `page=N`.
  - Excluye `ActivityLog::DISABLED_EVENTS = ['server:file.upload']` (línea 30;
    `app/Models/ActivityLog.php:65`). Ojo: esto excluye `file.upload` (pedir la URL de subida),
    **no** `file.uploaded` (la subida terminada, que manda Wings).
  - **`APP_ACTIVITY_HIDE_ADMIN`** (`config/activity.php:11`, por defecto `false`): si está en
    `true`, se ocultan las entradas cuyo actor es un root admin que no es dueño ni subusuario del
    servidor (controlador, líneas 31-46). Una restauración hecha por un admin ajeno al servidor
    **desaparecería** del endpoint.
  - Retención: `APP_ACTIVITY_PRUNE_DAYS`, 90 días por defecto (`config/activity.php:5`).
- **Forma de cada entrada** (`app/Transformers/Api/Client/ActivityLogTransformer.php:19-35`),
  envuelta por fractal como `{"object":"activity_log","attributes":{...}}`
  (`ActivityLog::RESOURCE_NAME = 'activity_log'`, `ActivityLog.php:59`):
  `id` (sha1 del id interno: estable pero opaco, no ordenable), `batch`, `event`, `is_api`,
  `ip`, `description`, `properties` (objeto; para backups trae `name` y, en `restore`,
  `truncate`), `has_additional_metadata`, `timestamp` (ISO 8601 con zona, `toAtomString()`).
  Con `include=actor` se agrega el usuario. La lista lleva `meta.pagination` (**SUPUESTO**: la
  forma estándar del serializer de fractal del panel, `total`, `count`, `per_page`,
  `current_page`, `total_pages`; no lo abrí).
- **Estado del servidor:** el middleware `AuthenticateServerAccess`
  (`app/Http/Middleware/Api/Client/Server/AuthenticateServerAccess.php:49-60`) llama a
  `validateCurrentState()` (`app/Models/Server.php:403-414`), que lanza
  `ServerStateConflictException` (**409**) si el servidor está suspendido, en mantenimiento, sin
  instalar o instalando, **restaurando un backup** (`status = restoring_backup`) o en
  transferencia. Solo `GET /api/client/servers/{server}` (y `resources` en algunos casos) se
  salva. O sea: **durante una restauración `/activity` (y los endpoints de archivos) dan 409** a
  un usuario que no es root admin. El mensaje es "This server is currently restoring from a
  backup, please try again later." (`app/Exceptions/Http/Server/ServerStateConflictException.php:24`).

## 2. Eventos de restauración

Todos los registra **el panel**, no Wings como evento de actividad: Wings solo avisa al panel por
la API remota y el panel escribe la fila.

| Evento | Cuándo | Dónde |
|---|---|---|
| `server:backup.restore` | al aceptar la petición, **antes** de que cambien los archivos; `properties`: `name`, `truncate` | `app/Http/Controllers/Api/Client/Servers/BackupController.php:210-212` |
| `server:backup.restore-complete` | Wings termina con éxito y llama a la API remota | `app/Http/Controllers/Api/Remote/Backups/BackupStatusController.php:105` |
| `server.backup.restore-failed` (**con punto, no dos puntos**: errata del panel) | Wings termina con error | `BackupStatusController.php:105` |
| `server:backup.restore-failed` | al reiniciar Wings, para servidores que quedaron en `restoring_backup` | `app/Http/Controllers/Api/Remote/Servers/ServerDetailsController.php:118` |

Detalles que importan:

- El fallo normal se registra como `server.backup.restore-failed`. Un filtro exacto por
  `server:backup.restore-failed` no lo encuentra; el filtro parcial `backup.restore` sí encuentra
  los cuatro.
- La rama de `resetState` (`ServerDetailsController.php:101-102`) busca eventos
  `server:backup.restore-started`, un nombre que **nadie registra** (el inicio se registra como
  `server:backup.restore`). En la práctica ese `restore-failed` con dos puntos casi nunca se
  escribe; el estado del servidor igual se limpia (líneas 128-131). Inferido del código; no
  probado.
- Una restauración fallida puede haber escrito archivos a medias (Wings escribe archivo por
  archivo, ver 3.1). **Hay que tratar `restore-failed` igual que `restore-complete`.**
- Los cuatro aparecen en la actividad del servidor: el de inicio porque el middleware
  `ServerSubject` pone al servidor como sujeto de todo log en rutas `/servers/{server}`
  (`app/Http/Middleware/Activity/ServerSubject.php:21-24`); los de Wings porque se registran con
  `->subject($model, $model->server)` (`BackupStatusController.php:106`).
- **Admin / Application API:** en Pterodactyl la **única** forma de restaurar es
  `POST /api/client/servers/{server}/backups/{backup}/restore` (`routes/api-client.php:138-139`).
  La Application API no tiene rutas de backups (`routes/api-application.php` en v1.15.1: no
  aparece `backup`), y el área de administración no tiene acción de restaurar (ningún
  `Activity::event` ni restore en `app/Http/Controllers/Admin`). Un admin que restaura lo hace
  por la Client API (o el panel de cliente) y **queda registrado** con él como actor; solo se
  oculta si `APP_ACTIVITY_HIDE_ADMIN=true` y el admin no es miembro del servidor (sección 1).
- Lo que dice el lado de Wings: `RestoreBackup` (`wings server/backup.go:121-167`) suspende el
  servidor, espera que se detenga, escribe los archivos y, con `defer`, llama
  `SendRestorationStatus(..., err == nil)` (línea 134): eso es lo que dispara
  `restore-complete` / `restore-failed`. Si Wings muere a mitad de camino, no se registra ni uno
  ni otro hasta que reinicia (`resetState`).

## 3. Otros cambios de archivos que la caché por (ruta, tamaño, mtime) no ve

### 3.1 Qué conserva el mtime viejo (verificado en Wings v1.13.3)

| Operación | Evento | ¿mtime viejo? | Fuente |
|---|---|---|---|
| Restaurar backup (local o S3) | `server:backup.restore*` | **Sí**: `Chtimes(file, info.ModTime(), info.ModTime())` por cada archivo | `server/backup.go:158-164` |
| Descomprimir un archivo | `server:file.decompress` (panel, `FileController.php:204`) | **Sí**: `fs.Chtimes(p, f.ModTime(), f.ModTime())`, "Update the file modification time to the one set in the archive" | `server/filesystem/compress.go:298-301` (`DecompressFile` → `extractStream`, líneas 159-175) |
| Transferir a otro nodo | ninguno | **Sí**: el destino usa `ExtractStreamUnsafe` → el mismo `extractStream` con `Chtimes` | `router/router_transfer.go:180`, `compress.go:184-192` |
| Reinstalar | `server:reinstall` solo desde el panel de cliente (`SettingsController.php:68`); **ninguno** desde admin (`Admin/ServersController.php:111-113`) ni Application API (`ServerManagementController.php:55-57`, `routes/api-application.php:90`) | Depende del script de instalación del egg. **SUPUESTO**: los scripts típicos descargan con `curl`/`wget` (mtime nuevo); un script que descomprime un zip con `unzip` conserva los mtimes del zip | `app/Services/Servers/ReinstallServerService.php` |
| SFTP: escribir/crear | `server:sftp.write`, `server:sftp.create` (Wings, `server/activity.go:17-18`) | **No**: el `Setstat` de Wings solo hace `Chmod`, ignora los tiempos que pide el cliente | `sftp/handler.go:202-217` (y `setstatMode`, 165-186) |
| SFTP: renombrar/mover | `server:sftp.rename` | Conserva mtime y tamaño, pero cambia la ruta: cache miss normal. Riesgo solo si un archivo distinto se mueve a una ruta ya cacheada con el mismo tamaño y mtime | `server/activity.go:20` |
| Subir por el panel | `server:file.uploaded` (Wings, `server/activity.go:22`) | No: escritura nueva, mtime actual (**SUPUESTO**: no revisé el handler de subida en detalle) | — |
| Renombrar / copiar por el panel | `server:file.rename`, `server:file.copy` | Rename: igual que SFTP. Copy: archivo nuevo (**SUPUESTO**: mtime nuevo) | `FileController.php:148,167` |
| Bajar una URL | `server:file.pull` | No: descarga nueva | `FileController.php:260` |

### 3.2 Retraso de los eventos de Wings

Los eventos que registra Wings (`server:sftp.*`, `server:file.uploaded`, `server:console.command`,
`server:power.*`) se guardan en un SQLite local y se mandan al panel por lotes: cada
`activity_send_interval` = **60 s** por defecto, como mucho `activity_send_count` = **100** por
envío (`config/config.go:229,232`; `internal/cron/cron.go:47-50`). El panel conserva el
`timestamp` original de Wings (`app/Http/Controllers/Api/Remote/ActivityProcessingController.php:37-59`),
así que **un evento puede aparecer con un `timestamp` anterior a tu último escaneo**. Los
eventos de restauración, descompresión y reinstalación los escribe el panel en el momento: no
tienen este retraso.

## 4. Rate limit

- `/activity` está dentro del grupo de la Client API con `throttle:api.client`
  (`RouteServiceProvider.php:56`), así que **cuenta para el mismo cupo** que todas las llamadas
  del cliente.
- El limitador (`RouteServiceProvider.php:93-100`) es por usuario (`$request->user()->uuid`, o
  la IP si no hay usuario), con `config('http.rate_limit.client')` peticiones por
  `client_period` minutos: **256 por minuto** por defecto (`config/http.php`,
  `APP_API_CLIENT_RATELIMIT`, `client_period = 1`). El cupo lo comparten todas las claves y
  sesiones del mismo usuario.
- No tiene un limitador propio (no hay `ResourceLimit` en la ruta, `routes/api-client.php:70`).
- Al pasarse: 429 estándar de Laravel con `Retry-After` (**SUPUESTO**: comportamiento estándar
  de `ThrottleRequests`, no revisado en este repo).

## 5. Pelican (fork)

Revisado en `pelican-dev/panel` `main` (último release `v1.0.0-rc.1`) y `pelican-dev/wings`:

- Mismo endpoint (`routes/api-client.php:60`), mismo permiso (`SubuserPermission::ActivityRead`),
  mismo `allowedSorts('timestamp')`, `AllowedFilter::partial('event')`, `per_page` 25 con máximo
  100, mismo `DISABLED_EVENTS` (`app/Http/Controllers/Api/Client/Servers/ActivityLogController.php:29-40`;
  `app/Models/ActivityLog.php:68`). Cambia cómo arma la consulta (`whereHas('subjects', ...)`
  y un scope `hideAdminActivity`), no el contrato. El transformer pasa a ser `ActivityLogData`
  (**SUPUESTO**: los mismos campos; no lo abrí).
- Mismos nombres de evento, **errata incluida**: `server:backup.restore` (en la API,
  `BackupController.php:249`, y en la UI Filament, `app/Filament/Server/Resources/Backups/BackupResource.php:205`),
  `server:backup.restore-complete` / `server.backup.restore-failed`
  (`app/Http/Controllers/Api/Remote/Backups/BackupStatusController.php:102`), y el mismo
  `restore-started` que nadie escribe (`ServerDetailsController.php:97,118`). Agrega
  `server:backup.rename`.
- Rate limit: igual (256/min), pero el período también es configurable
  (`APP_API_CLIENT_RATELIMIT_PERIOD`, `config/http.php:14-15`).
- Pelican Wings también conserva el mtime al restaurar y al descomprimir (`server/backup.go:162-168`,
  `server/filesystem/compress.go:411`).
- Ojo con un detalle de Pelican: en `BackupController::restore` la condición es
  `!$backup->is_successful && is_null($backup->completed_at)` (línea 247; en Pterodactyl es `||`).
  No cambia la detección.

---

## Recomendación

1. **Una petición antes de cada escaneo:**

   ```
   GET /api/client/servers/{id}/activity?filter[event]=backup.restore,file.decompress,reinstall&sort=-timestamp&per_page=25
   ```

   El filtro parcial `backup.restore` atrapa los cuatro nombres (`server:backup.restore`,
   `server:backup.restore-complete`, `server:backup.restore-failed` y la errata
   `server.backup.restore-failed`). Sumar `file.decompress` (conserva mtimes) y `reinstall`
   cuesta lo mismo: es la misma consulta. **No filtres por nombre exacto.**

2. **Regla:** si alguna entrada tiene `timestamp` ≥ el **inicio** del último escaneo completado
   menos un margen (por ejemplo 2 min, por la deriva de reloj entre panel y Wings), invalida la
   caché de ese servidor entera: rehash de todo `/mods`. Usa el inicio y no el fin del escaneo:
   así una restauración que arrancó durante el escaneo también cuenta. Si hay 25 resultados y el
   último sigue siendo posterior al corte, no hace falta paginar: ya hay que invalidar.
   Opcional: guardar los `id` vistos para no invalidar dos veces por la misma entrada dentro del
   margen.

3. **Cuando el endpoint falla:**
   - **409**: el servidor está restaurando, instalando o en transferencia. No escanees: el
     escaneo de archivos también daría 409. Reintenta más tarde y, cuando vuelva, **invalida**
     (pasó algo de eso).
   - **403** (subusuario sin `activity.read`), **404** (panel anterior a v1.9.0, o servidor sin
     acceso), **400** (el panel no acepta el filtro), **5xx**, timeout o **429**: no hay forma de
     saberlo, así que **falla cerrado**: escanea sin confiar en el mtime (rehash completo, o al
     menos comparar contra una huella más fuerte, ver 5) y registra el motivo. Un 429 no se
     reintenta en bucle: comparte el cupo de 256/min del usuario.
   - Es una lectura (GET): se puede reintentar sin problema (no la toca la invariante de
     escrituras).

4. **Lo que este método no ve:**
   - Reinstalaciones desde el admin o la Application API, y transferencias entre nodos: no dejan
     evento. Las transferencias conservan los mtimes. Complemento barato: guardar el
     `node` del servidor (atributo de `GET /api/client/servers/{id}`) e invalidar si cambia;
     esa llamada ya suele hacerse.
   - Restauraciones de un admin no miembro con `APP_ACTIVITY_HIDE_ADMIN=true`.
   - Entradas más viejas que `APP_ACTIVITY_PRUNE_DAYS` (90 días): si el último escaneo es más
     antiguo que eso, invalida sin preguntar.
   - Cambios hechos directamente en el disco del nodo, fuera de Wings.

5. **Defensa en profundidad (opcional):** sumar `created` a la clave. El listado de archivos de
   Wings devuelve `created` = `s.CTime()` y `modified` = `s.ModTime()`, en RFC 3339 con
   resolución de segundos (`wings server/filesystem/stat.go:22-34`). En Linux, `CTime()` es
   `st.Ctim` (`stat_linux.go:14-21`): no es la fecha de creación sino el **ctime del inodo**, que
   el kernel actualiza en toda escritura y en todo `Chtimes`, y que nadie puede fijar a mano. Una
   restauración, una descompresión o una transferencia dejan `created` ≈ ahora aunque `modified`
   sea viejo. Así que una clave (ruta, tamaño, mtime, `created`) detecta esos casos **sin
   depender de la actividad**, y con la misma llamada de listado que ya hace el escaneo.
   Advertencias: el código lleva un `// TODO: remove. Ctim is not actually ever been correct`
   (`stat_linux.go:12`), así que el campo podría desaparecer o cambiar en una versión futura de
   Wings. Además, un `chmod` o un cambio de dueño también mueve el ctime (falso positivo barato:
   un rehash de más). Si se adopta, la consulta a `/activity` queda como segunda señal, no como
   la única.
