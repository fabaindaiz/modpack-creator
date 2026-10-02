# Runbook: la primera actualización real de un servidor (fase 3)

**Para quién:** el dueño, sobre el **servidor de prueba** del panel, la primera vez que la API
escriba en un servidor. **Por qué hace falta:** la actualización (i-7985ee-495708) se probó contra
un panel simulado, en memoria y por HTTP, pero nunca contra Pterodactyl y Wings reales: parar,
hacer un backup, mover archivos y arrancar ocurren sobre un servidor de juego de verdad.

Lo que la API garantiza y este runbook comprueba en real:
- nada se escribe sin `PTERODACTYL_WRITES: "enabled"` y `dry_run=false` (d-7985ee-5739fc);
- un jar que el build no explica, un servidor ocupado o sin cupo de backups abortan **antes** de
  escribir (d-7985ee-c8a15d, d-7985ee-5cc6ee);
- ante un fallo después del cambio, el servidor queda parado y nadie hace rollback por ti
  (d-7985ee-5445a0).

Las llamadas usan `curl` con la cookie de sesión de un login real (`-b session=...`). `$API` es la
URL de la API y `$S` el id del servidor de prueba en la API (`GET /v1/server`).

## 0. Antes

- La API desplegada con el runbook de Mongo, el usuario de servicio y su clave `ptlc_`
  (i-7985ee-63226e). El usuario de servicio necesita, en el servidor de prueba, permisos de
  archivos, backups (crear, restaurar, **borrar** para poder bloquear) y energía.
- El servidor de prueba con el mismo egg y loader que el pack, poco crítico, y **al menos un cupo
  de backup libre** en el panel.
- Un webhook de Discord (`DISCORD_WEBHOOK_URL`) para ver los avisos; opcional, pero conviene.
- `PTERODACTYL_WRITES` todavía **vacío**.

## 1. Asociar, escanear e importar (solo lectura)

```bash
curl -X PUT  "$API/v1/server/$S/bundle?bundle_id=$BUNDLE" -b session=...
curl -X POST "$API/v1/server/$S/scan?full=true" -b session=...
curl -X POST "$API/v1/server/$S/import" -b session=...
```

- `added`: proyectos que entraron al bundle como del admin.
- `candidates`: confirmar cada uno (`POST .../import/confirm?path=...&project_id=...`) o dejarlo como
  archivo local (`POST .../import/local?path=...`). **Mirar el slug**: la metadata se equivoca.
- `local`: quedaron como archivos locales: la actualización no los toca.

Después, un build nuevo (`POST /v1/modpack/$BUNDLE/build`), recomendarlo, y comparar:
`GET /v1/server/$S/compare`. Lo que diga `different_version` y `missing` es lo que la actualización
va a cambiar.

## 2. Simulación (sigue sin escribir)

```bash
curl -X POST "$API/v1/server/$S/update" -b session=...        # dry_run=true por defecto
curl "$API/v1/job/$JOB" -b session=...
```

- `status: done`, y en `checkpoint.preflight` la lista `install` (jars nuevos) y `remove` (los que
  van a `mods.old/`). En `checkpoint.log.lines`, cada escritura que habría hecho, en orden.
- Las descargas **sí** se hicieron y se verificaron contra el sha512 del build.
- Si falla, `error` dice por qué; nada se escribió.

## 3. La actualización real

1. En `.env.yaml`: `PTERODACTYL_WRITES: "enabled"`; reiniciar la API.
2. `curl -X POST "$API/v1/server/$S/update?dry_run=false" -b session=...`
3. Seguir `GET /v1/job/$JOB`, y mirar a la vez la consola y la lista de backups del servidor en el
   panel. Orden esperado: subidas a `/mods.staging/<trabajo>/` con el servidor corriendo,
   `save-all`, parada, backup **bloqueado**, `mods.old/<trabajo>/`, arranque y 5 minutos de
   verificación.
4. Al terminar: `done`, el backup desbloqueado y el aviso `✅`. Comparar otra vez: `in_sync`.

**Qué anotar en `.claude/logs/agent-changelog.md`:** cuánto tardó la parada (la caché del panel es de
20 s), cuánto el backup, si algo del log difiere del orden esperado.

## 4. Si falla

- **Antes de parar** (preflight, descargas, staging): el servidor sigue igual. Leer `error`.
- **El backup falla o no termina:** la API vuelve a arrancar el servidor sin cambios y avisa.
- **Después del cambio** (no arranca o se cae en los 5 minutos): el servidor queda **parado** y el
  aviso pide elegir:

```bash
# A: devolver los jars viejos (no pierde lo jugado; deja en el mundo datos de los mods nuevos)
curl -X POST "$API/v1/server/$S/rollback?update=$JOB&mode=A" -b session=...
# B: restaurar el backup (pierde lo jugado desde el backup; trunca antes de restaurar)
curl -X POST "$API/v1/server/$S/rollback?update=$JOB&mode=B" -b session=...
```

Si el rollback tampoco queda estable, la API para el servidor y avisa: **no reintentar en bucle**,
mirar la consola.

## 5. Limpieza (a mano, cuando todo esté bien)

`mods.old/<trabajo>/` y `mods.staging/<trabajo>/` quedan en el servidor a propósito: son la
evidencia y el material del rollback A. Borrarlos desde el panel cuando ya no hagan falta. El
backup previo queda desbloqueado, y el panel lo rota como cualquier otro.

## 6. Volver a solo lectura

Si no se va a actualizar nada más por ahora: `PTERODACTYL_WRITES` vacío y reiniciar la API. Los
escaneos y las importaciones no lo necesitan.
