# Operar servidores de Pterodactyl con seguridad: backups, energía, estado y actualización de mods

**Fecha:** 2026-10-01 · **Método:** código fuente en los últimos tags (no hay referencia oficial de
la API; el código es la fuente primaria). Lo inferido dice **INFERIDO** o **SIN VERIFICAR**.
Solo lectura: nada de esto está implementado.

Prefijos de URL: **P** = `https://github.com/pterodactyl/panel/blob/v1.15.1/`,
**W** = `https://github.com/pterodactyl/wings/blob/v1.13.3/`,
**PL** = `https://github.com/pelican-dev/panel/blob/v1.0.0-beta38/`.

## Resumen

- Con la **Client API** y una clave `ptlc_` de un **usuario dedicado no-admin**, agregado como
  subusuario con los permisos justos, alcanza para todo el ciclo de actualización. La Application
  API (`ptla_`) solo hace falta para cambiar el comando de arranque o variables no editables.
- El panel no da una operación "actualizar mods" atómica: el servicio la arma con
  **parar → backup → staging → swap → arrancar → verificar**, y dos rollbacks (barato y completo).
- Hay trampas: `power` responde 204 aunque no haga nada; `restore` trunca el directorio **sin parar
  el servidor**; los backups tienen throttle y cupo; Wings reinicia solo un servidor que crashea.

## 1. Client API vs Application API

| | Client API (`/api/client`) | Application API (`/api/application`) |
|---|---|---|
| Qué hace | archivos, energía, comandos, backups, schedules, variables, websocket, actividad, subusuarios | usuarios, nodos, servidores (build, startup, suspender, reinstalar), nests/eggs de solo lectura |
| Clave | `ptlc_`, con todos los derechos del usuario; solo se limita con `allowed_ips` (≤ 50) | `ptla_`, ACL por recurso; exige usuario root admin |
| Fuente | P `routes/api-client.php#L57-L153`, `app/Models/ApiKey.php#L219` | P `routes/api-application.php#L78-L122` |

- La Client API rechaza claves de aplicación (P `app/Http/Middleware/Api/Client/RequireClientApiKey.php#L19`).
- **La clave `ptlc_` de un root admin pasa también la Application API y todos los chequeos de
  dueño** (P `app/Http/Requests/Api/Application/ApplicationApiRequest.php#L45-L47`): usar la cuenta
  admin para el servicio le da todo.
- Límite: 256 requests/min por usuario en cada API (P `config/http.php#L14-L19`).

**Recomendación.** Un usuario dedicado, no admin, agregado como subusuario de cada servidor con:
`websocket.connect`, `control.console/start/stop/restart`, `file.read/read-content/create/update/delete`,
`backup.read/create/restore` (y `delete` si el servicio rota sus backups), `startup.read`,
`activity.read` (P `app/Models/Permission.php#L18-L66`), y `allowed_ips` = la IP del servicio.
Tarjetas: `a-default-scope-is-the-widest-one`, `one-write-gate-makes-read-only-real`.

## 2. Backups

- **Crear:** `POST /backups` con `name` (≤ 191), `is_locked`, `ignored` (líneas)
  (P `.../Backups/StoreBackupRequest.php#L15-L22`).
- **Throttle:** 2 backups cada 600 s por servidor; los fallidos o borrados cuentan (429)
  (P `config/backups.php#L24-L32`, `app/Services/Backups/InitiateBackupService.php#L78-L87`).
- **Cupo:** la Client API **no rota**: al llegar a `feature_limits.backups` responde 400; los
  schedules sí borran el más viejo no bloqueado (P `.../InitiateBackupService.php#L89-L107`,
  `app/Jobs/Schedule/RunTaskJob.php#L69`).
- **Asíncrono:** Wings responde 202 y archiva en segundo plano; el panel completa `is_successful`,
  `checksum`, `bytes`, `completed_at` (un fallo también trae `completed_at`). Se sondea
  `GET /backups/{uuid}` hasta `completed_at != null` (W `router/router_server_backup.go#L39-L80`,
  P `.../Remote/Backups/BackupStatusController.php#L51-L70`).
- **Con el servidor encendido** se archiva sin pausar escrituras (W `server/backup.go#L60-L70`);
  hacer `save-all` o parar antes es INFERIDO, no guía documentada.
- **Restaurar:** `POST /backups/{uuid}/restore {"truncate": bool}`; solo si el estado del servidor es
  nulo y el backup fue exitoso; durante el restore todo devuelve 409. **Wings trunca primero y no
  para el servidor** ("TODO: stop the server if it is running"); si el proceso no se detiene en 2
  minutos, el restore falla con los archivos ya truncados. Al terminar, el estado vuelve a nulo
  aunque haya fallado; el evento de fallo es `server.backup.restore-failed`
  (W `router/router_server_backup.go#L91-L160`, `server/backup.go#L122-L149`;
  P `.../BackupStatusController.php#L84-L109`). Throttle: 3 restores cada 15 min.

## 3. Energía y estado

- `POST /power {"signal": start|stop|restart|kill}` responde **204 de inmediato**; Wings lo ejecuta
  con un lock de energía y, si otro lo tiene, **falla en silencio** (solo log)
  (W `router/router_server.go#L53-L98`, `server/power.go#L56-L120`). Stop espera hasta 10 min y
  luego SIGKILL.
- `GET /resources` → `current_state` ∈ offline|starting|running|stopping, con caché de 20 s
  (P `.../ResourceUtilizationController.php#L24-L33`).
- **`running` ya significa "terminó de arrancar"**: Wings pasa de starting a running cuando una
  línea de consola coincide con `startup.done` del egg (W `server/listeners.go#L160-L183`). Un
  modpack colgado queda en `starting` para siempre: hace falta timeout.
- **Reinicio por crash** activado por defecto, salvo si el último crash fue hace < 60 s
  (W `config/config.go#L258-L271`): un mod roto puede arrancar en bucle una vez.
- **Websocket:** `GET /websocket` da `{token, socket}` (JWT de 10 min); exige `Origin` igual a la URL
  del panel. Eventos útiles: `status`, `console output`, `backup completed:<uuid>`,
  `backup restore completed`, `token expiring` (W `router/websocket/*`, `server/events.go#L10-L21`).

## 4. Schedules del panel

Tareas `command | power | backup`, hasta 10 por schedule, con `time_offset` ≤ 900 s y
`only_when_online` (P `.../Schedules/StoreTaskRequest.php#L36-L48`,
`app/Services/Schedules/ProcessScheduleService.php#L45-L53`). Cada tarea corre tras el offset de la
anterior **sin esperar** a que el backup termine ni a que el servidor arranque.

**Veredicto:** sirven como red de seguridad nocturna ("save-all → backup → restart", que sí rota
backups); no pueden reemplazar los archivos, verificar el arranque ni volver atrás.

## 5. Variables de arranque y actividad

- `PUT /startup/variable {key, value}` solo cambia variables `user_viewable` y `user_editable` del
  egg, también para root admin; el comando de arranque no se cambia por la Client API
  (P `.../StartupController.php#L55-L97`). Una variable tipo "URL del pack" toma efecto en el
  próximo arranque.
- `GET /activity?filter[event]=…&sort=-timestamp` (P `.../ActivityLogController.php#L22-L52`):
  eventos `server:file.write/delete/rename/pull/decompress`, `server:sftp.write/create/delete/rename`,
  `server:startup.edit`, `server:backup.*`. Sirven para **detectar cambios manuales en `mods/`**,
  con límites: Wings los envía en lotes cada 60 s, `server:file.upload` está oculto, las acciones de
  admin pueden ocultarse y se podan a los 90 días. Lo hecho directo en el host no queda registrado:
  la detección real de deriva sigue siendo el manifiesto de hashes.
- `files/pull` **rechaza IPs privadas y de loopback** (W `router/downloader/downloader.go#L86-L95`):
  el servicio no puede servir jars al panel por la LAN con `pull`; sí por `files/upload` (URL
  firmada de 15 min, P `.../FileUploadController.php#L44`).

## 6. Pelican

Mismos endpoints de cliente; claves `pacc_`/`papp_`; `.pelicanignore`; tarea extra `delete_files`;
un sistema de plugins (`plugins/`); OpenAPI servida en `/docs/api/client(.json)`
(PL `routes/api-client.php`, `app/Models/Plugin.php#L123`, `routes/docs.php`). Migrar a Pelican no
cambia el diseño; su sistema de plugins abriría integrarse **dentro** del panel (ver
`2026-10-plataformas-similares.md`).

## Procedimiento propuesto: actualizar los mods de un servidor

Cada paso dice qué hacer si falla. Los pasos marcados ⚠ son irreversibles o caros de revertir.

| Paso | Qué | Si falla |
|---|---|---|
| 0. Preflight | `GET /servers/{id}`: estado nulo, no suspendido ni instalando, cupo de backups ≥ 1 (`backup_count`). Leer `/activity` desde la última corrida: si alguien que no es el servicio tocó `mods/`, abortar o preguntar. Guardar el listado de `mods/`. | abortar; nada cambió |
| 1. Parar | `command "save-all"`, esperar, `power stop`; sondear hasta `offline` (≤ 10 min, con la caché de 20 s); si no, `kill` y volver a sondear. | arrancar si hace falta y abortar |
| 2. Backup | `POST /backups {name: "pre-update <job>", is_locked: true}`; esperar `completed_at` y exigir `is_successful`. | 429/400/fallo: arrancar y abortar |
| 3. Staging | subir los jars nuevos a un directorio de staging (`files/upload`); verificar tamaños (y hashes) con `files/list`. | borrar staging, arrancar; `mods/` intacto |
| 4. Swap ⚠ | `PUT /files/rename`: los jars viejos a `mods.old/<job>/`, los nuevos a `mods/`. | renombrar de vuelta |
| 5. Arrancar | `power start`; esperar `running` (≤ 5–10 min); mirar la consola por crash y un `status` que vuelve a offline. | rollback A |
| 6. Verificar | `running` estable N minutos, `uptime` creciendo, opcional un `list` por consola y re-chequear hashes. Éxito: desbloquear el backup, borrar `mods.old` más tarde. | rollback A |
| 7. Rollback A | parar/kill, devolver `mods.old`, borrar los nuevos, arrancar, verificar. No gasta el backup. | rollback B |
| 8. Rollback B ⚠ | confirmar offline, `restore {truncate: true}`; sondear hasta estado nulo; confirmar en `/activity` que no hubo `restore-failed`; arrancar y verificar. Si falla: avisar a un humano, **no reintentar en bucle**. | humano |

**Irreversible o con pérdida:** sobrescribir o borrar en el paso 4 sin `mods.old`; el mundo avanza
mientras corren los mods nuevos (el rollback B pierde ese progreso; el A deja datos de mods nuevos
en el mundo); `restore` con truncate no es atómico; rotar backups. **Nunca confiar en un 204: volver
a leer el estado.** Tarjetas: `retry-over-irreversible-effect`, `order-writes-by-failure-residue`,
`cleanup-belongs-to-the-supervisor` (borrar staging y `mods.old` lo hace el trabajo, no el paso).
