# Runbook: pasar a la fase 0 sin perder datos (S2 y S3)

**Para quién:** el dueño, en el servidor, al desplegar la rama `fase-0` (o `main` después del merge).
**Por qué hace falta:** hasta ahora MongoDB guardaba sus datos en un **volumen anónimo** (la imagen
declara `VOLUME /data/db`). Desde S2 los guarda en un **volumen con nombre** (`mongodb-data`), que
arranca vacío: sin estos pasos, la API aparece sin datos. Los datos viejos no se borran solos, pero
quedan en un volumen huérfano.

Fuentes: [`docker compose up`](https://docs.docker.com/reference/cli/docker/compose/up/) y
[`down`](https://docs.docker.com/reference/cli/docker/compose/down/) (los volúmenes anónimos
sobreviven a `up`, pero no se reusan después de `down`),
[volúmenes](https://docs.docker.com/engine/storage/volumes/),
[mongodump](https://www.mongodb.com/docs/database-tools/mongodump/) y
[mongorestore](https://www.mongodb.com/docs/database-tools/mongorestore/) (`--archive`),
[subir a 8.0](https://www.mongodb.com/docs/manual/release-notes/8.0-upgrade-standalone/) y
[a 7.0](https://www.mongodb.com/docs/manual/release-notes/7.0-upgrade-standalone/) (de a una versión
mayor; consultadas el 2026-10-03). Las líneas de comando combinan esas opciones; marcar cualquier diferencia en
`.claude/logs/agent-changelog.md`.

## 0. Antes: dónde están hoy los datos

```bash
docker inspect modpack-mongodb --format '{{range .Mounts}}{{.Name}} -> {{.Destination}}{{"\n"}}{{end}}'
docker volume ls -f dangling=true
```

- El volumen montado en `/data/db` es el que tiene los datos actuales.
- Si alguna vez se corrió `./scripts/stop.sh` (`down`), puede haber datos **más viejos** en volúmenes
  huérfanos de la segunda lista. No se tocan en este runbook. Revisarlos aparte si falta algo.

## 1. Leer la versión que corre

```bash
export MONGODB_PASSWORD='…'   # la misma que usa el compose hoy
docker exec modpack-mongodb mongosh --quiet -u mongoadmin -p "$MONGODB_PASSWORD" \
  --authenticationDatabase admin --eval 'db.version()'
```

`MONGO_VERSION` será esa versión mayor y menor (por ejemplo `8.0`). **Nunca una menor**: MongoDB
no admite bajar de versión.

Todo el repositorio corre en MongoDB 8.0 (d-7985ee-83eb5b; CI usa `mongo:8.0`). Si `db.version()`
es menor que 8.0 pero al menos 5.0, **no la subas todavía**: migra primero a `mongodb-data` **a esa
misma versión** (pasos 2 a 8) y sube después, en [Subir a MongoDB 8.0](#subir-a-mongodb-80). Ahora
el contenedor viejo sigue siendo el que tiene los datos, y el compose nuevo todavía no está
desplegado. Si el comando de arriba falla porque no encuentra `mongosh` (`executable file not
found`), la versión es 4.4 o menor: sigue el bloque siguiente.

### Si la versión es 4.4 o menor

Con 4.4 o menos, el compose nuevo no sirve tal cual:

- La API no conecta por debajo de 4.4: el PyMongo del repositorio (4.18.2) tiene
  `pymongo.common.MIN_SUPPORTED_SERVER_VERSION = "4.4"` (comprobado en el entorno de hatch con
  `hatch run python -c "import pymongo.common as c; print(c.MIN_SUPPORTED_SERVER_VERSION)"`).
- La imagen `mongo:4.4` **no trae `mongosh`**, solo el shell viejo `mongo` (`/usr/bin/mongo`);
  `mongo:5.0` trae los dos y desde `mongo:6.0` solo `mongosh`. El healthcheck del compose nuevo usa
  `mongosh`: un `mongo:4.4` nunca queda `healthy` y la API (`depends_on: service_healthy`) nunca
  arranca.

Por eso, **con 4.4 o menos se sube hasta 5.0 en el contenedor viejo, antes de migrar**, y se migra
después a 5.0 (pasos 2 a 8 con `MONGO_VERSION=5.0`); el resto de los saltos va en
[Subir a MongoDB 8.0](#subir-a-mongodb-80), desde 5.0.

Mientras la versión sea 4.4 o menor, cada `docker exec … mongosh` de este runbook se escribe con
`mongo`, el resto igual (comprobado con `mongo:4.4`, 4.4.30). Para leer la versión:

```bash
docker exec modpack-mongodb mongo --quiet -u mongoadmin -p "$MONGODB_PASSWORD" \
  --authenticationDatabase admin --eval 'db.version()'
```

y para la FCV:

```bash
docker exec modpack-mongodb mongo --quiet -u mongoadmin -p "$MONGODB_PASSWORD" \
  --authenticationDatabase admin --eval 'db.adminCommand({ getParameter: 1, featureCompatibilityVersion: 1 })'
```

**Antes de pasar a 5.0, comprobar AVX** (en x86_64, lo que dice `uname -m`): MongoDB 5.0 y
posteriores no corren en una CPU x86 sin AVX.

```bash
grep -m1 -o avx /proc/cpuinfo
```

Sin salida, 5.0 no va a arrancar en este servidor: **detente aquí**. Los datos quedan en 4.4, en el
contenedor viejo, y hace falta planificar otro hardware antes de desplegar esta versión.

Los saltos, uno por versión mayor (… → 4.2 → 4.4 → 5.0), con el compose **viejo** (todavía sin
`git pull`) y sobre su volumen anónimo:

**a.** Parar la API vieja: `docker compose -f docker/docker-compose.yaml stop api`.

**b.** Respaldar con el comando de "2. Respaldo" (la imagen 4.4 trae `mongodump`), otro nombre de
archivo por salto.

**c.** Comprobar la FCV con el comando de "y para la FCV", más arriba: tiene que ser la de la
versión que corre (`"4.4"` corriendo 4.4). Si es menor, fijarla con el paso **f.** para la versión
que corre.

**d.** Parar mongod limpiamente: `docker compose -f docker/docker-compose.yaml stop -t 120 mongodb`.

**e.** En el `docker/docker-compose.yaml` viejo, cambiar el `image:` de `mongodb` (al principio,
`mongo:latest`) por la mayor siguiente (`image: mongo:5.0` desde 4.4) y levantar solo Mongo:
`docker compose -f docker/docker-compose.yaml up -d mongodb`. Compose recrea el contenedor
conservando sus volúmenes, también el anónimo (fuentes de arriba); confirmar con el comando de
"0. Antes" que `/data/db` sigue montado desde el mismo volumen. El compose viejo no tiene
healthcheck: esperar a que `db.version()` responda la versión nueva.

**f.** Fijar la FCV de la versión nueva, sin `confirm` (con `mongo` hasta 4.4; en 5.0 sirve
también `mongosh`), al llegar a 5.0 (`"4.4"` al llegar a 4.4):

```bash
docker exec modpack-mongodb mongo --quiet -u mongoadmin -p "$MONGODB_PASSWORD" \
  --authenticationDatabase admin --eval 'db.adminCommand({ setFeatureCompatibilityVersion: "5.0" })'
```

**g.** Arrancar la API vieja: `docker compose -f docker/docker-compose.yaml up -d api`.

El cambio de `image:` es local, y `git pull` se niega a pisar un archivo con cambios locales: antes
del `git pull` de "5. Desplegar", `git checkout -- docker/docker-compose.yaml` (el compose nuevo toma
la versión de `MONGO_VERSION`, que entonces es `5.0`). Por debajo de 4.4, la
página de upgrade de cada versión dice lo mismo (de a una mayor, con la FCV de la que corre); esos
saltos no están comprobados aquí.

## 2. Respaldo

```bash
docker exec modpack-mongodb mongodump -u mongoadmin -p "$MONGODB_PASSWORD" \
  --authenticationDatabase admin --archive > respaldo-$(date +%F).archive
ls -l respaldo-*.archive      # tiene que pesar más que cero
```

`docker exec` **sin `-t`**: un TTY corrompe la salida binaria. Guardar el archivo fuera del servidor
también.

## 3. Variables del compose

Crear `docker/.env`, que no se versiona (`.gitignore` ya cubre `.env`):

```bash
MONGO_VERSION=8.0          # la que leíste en el paso 1 (si era 6.0, aquí 6.0)
MONGODB_PASSWORD=…
MONGOWEB_PASSWORD=…
FORWARDED_ALLOW_IPS=…      # la IP o red del proxy inverso (para uvicorn: confiar en X-Forwarded-Proto)
```

Sin `MONGO_VERSION` o sin `FORWARDED_ALLOW_IPS`, el compose se niega a arrancar. Es a propósito
(d-7985ee-398653, d-7985ee-491c83). El panel y su proxy se detallan en
[`desplegar-panel.md`](desplegar-panel.md).

## 4. `.env.yaml` (nuevas variables de S3)

Agregar, como en `.env.default`:

- `OIDC_ALLOWED_GROUPS`: **obligatoria**. El grupo del IdP cuyos miembros usan la API (por
  ejemplo `modpack-admins`; créalo en authentik si no existe). Sin ella, la API no arranca.
- `OIDC_OPERATOR_GROUPS`: **obligatoria**. Los grupos del IdP cuyos miembros **operan**: actualizar,
  simular, hacer rollback, importar y asociar servidores (d-7985ee-edef1e). Crea el grupo en
  authentik (por ejemplo `modpack-operators`); sus miembros también tienen que estar en
  `OIDC_ALLOWED_GROUPS`. Vacía (`""`) significa que **nadie** opera: todos son editores. Quien ya
  tenía sesión abierta tiene que **volver a entrar** después del despliegue: su sesión vieja no
  trae grupos y cuenta como editor. Sin la variable, la API no arranca.
- `OIDC_ALLOWED_EMAILS`: opcional. Solo vale con email verificado; authentik lo manda sin verificar
  por defecto.
- `SESSION_HTTPS_ONLY`: `"false"` **solo** si se entra por HTTP sin TLS. Con `"true"` y HTTP, el
  login no funciona.
- `MONGODB_URI`: si la API corre en el compose, `mongodb://mongoadmin:<contraseña>@mongodb:27017`. Si
  corre fuera de Docker en el mismo host, `…@127.0.0.1:27017`.

## 5. Desplegar

```bash
git pull                 # la rama o main con S2 y S3
./scripts/build.sh
./scripts/run.sh         # crea mongodb-data vacío; Mongo crea el usuario root con MONGODB_PASSWORD
docker ps --format '{{.Names}} {{.Status}}'   # modpack-mongodb tiene que decir (healthy)
```

## 6. Restaurar

```bash
docker exec -i modpack-mongodb mongorestore -u mongoadmin -p "$MONGODB_PASSWORD" \
  --authenticationDatabase admin --archive --nsExclude 'admin.*' < respaldo-AAAA-MM-DD.archive
```

`--nsExclude 'admin.*'` deja los usuarios de Mongo como los creó el paso 5. Restaurar `admin`
reemplazaría las credenciales.

## 7. Verificar

```bash
docker exec modpack-mongodb mongosh --quiet -u mongoadmin -p "$MONGODB_PASSWORD" \
  --authenticationDatabase admin --eval \
  'for (const d of ["modpack","modrinth","pterodactyl","userdata"]) { const x = db.getSiblingDB(d); print(d, x.getCollectionNames().map(c => c + "=" + x[c].countDocuments()).join(" ")) }'
```

- Comparar con lo que había antes (o con `mongorestore`, que informa cuántos documentos restauró).
- Iniciar sesión con una cuenta del grupo. Tiene que entrar. El primer login de un usuario viejo le
  agrega su `sub` (identidad `iss` + `sub`, d-7985ee-361ed0).
- Iniciar sesión con una cuenta fuera del grupo. Tiene que dar 403.

## 8. Después

- El volumen anónimo viejo **no se borra**. Cuando todo esté verificado y el respaldo guardado afuera,
  se puede quitar a mano (`docker volume rm <nombre del paso 0>`).
- **Nunca** `docker compose … down -v`: borra `mongodb-data`.

## Subir a MongoDB 8.0

**Solo si el paso 1 dio una versión menor que 8.0**, y solo cuando Mongo ya corre desde
`mongodb-data` con el compose nuevo (pasos 2 a 8 terminados y verificados). Se sube de una versión
mayor a la siguiente: 5.0 → 6.0 → 7.0 → 8.0. Salta los saltos que ya diste. Con 4.4 o menos, los
saltos hasta 5.0 ya se dieron en el contenedor viejo ([el bloque de "1. Leer la versión que corre"](#si-la-versión-es-44-o-menor)):
desde 5.0 todas las imágenes traen `mongosh`, el que usan los comandos de aquí.

En cada salto:

1. **Parar la API**: `docker compose -f docker/docker-compose.yaml stop api`. Antes, comprobar
   que no hay trabajos activos (`/jobs`, filtrando por estado). `PTERODACTYL_WRITES` vacío es una
   segunda salvaguarda.
2. **Respaldar**, con el comando de "2. Respaldo" y otro nombre de archivo por salto.
3. **Comprobar la FCV.** Tiene que ser la de la versión que corre:
   ```bash
   docker exec modpack-mongodb mongosh --quiet -u mongoadmin -p "$MONGODB_PASSWORD" \
     --authenticationDatabase admin --eval \
     'db.adminCommand({ getParameter: 1, featureCompatibilityVersion: 1 })'
   ```
   Si es menor (por ejemplo `"4.4"` corriendo 5.0), fíjala con el comando del paso 6 de este mismo bloque, de la
   versión que corre y vuelve a comprobar.
4. **Parar mongod limpiamente**: `docker compose -f docker/docker-compose.yaml stop -t 120 mongodb`.
   La página de 5.0 exige confirmar que `mongod` se apagó limpio; `-t 120` le da tiempo antes del
   kill.
5. **Cambiar la versión y levantar solo Mongo.** En `docker/.env`, `MONGO_VERSION` a la mayor
   siguiente; desde la raíz del repositorio:
   ```bash
   docker compose -f docker/docker-compose.yaml up -d mongodb
   ```
   Es el mismo comando que `./scripts/run.sh` (misma raíz y mismo `-f`, así que Compose lee
   `docker/.env`, el directorio del archivo compose), pero **sin** `./scripts/run.sh`: ese levanta
   todo el compose y arrancaría la API parada antes de fijar la FCV. Recrea el contenedor de Mongo
   sobre el mismo volumen. Esperar a `modpack-mongodb … (healthy)` en `docker ps` y confirmar con
   `db.version()`.
6. **Fijar la FCV de la versión nueva**, con el mismo `docker exec … mongosh --eval`:
   - 5.0 y 6.0, sin `confirm`: `db.adminCommand({ setFeatureCompatibilityVersion: "5.0" })`;
   - 7.0 y 8.0, **con** `confirm: true` (obligatorio desde 7.0):
     `db.adminCommand({ setFeatureCompatibilityVersion: "8.0", confirm: true })`.

   Las páginas de 5.0, 6.0 y 7.0 dicen que el comando es idempotente: si no termina, se repite.
7. **Arrancar la API otra vez** (`docker compose -f docker/docker-compose.yaml up -d api`, o
   `./scripts/run.sh`) y comprobar el panel. Las páginas de upgrade
   recomiendan un período de prueba antes de fijar la FCV nueva (*burn-in*, por si hubiera que
   volver atrás); aquí el respaldo de cada salto es el camino de vuelta. Seguir con el siguiente
   salto.

Termina en `MONGO_VERSION=8.0` con FCV `"8.0"`. **Nunca** `docker compose … down -v`. Un salto que
falla no se arregla bajando `MONGO_VERSION`: se restaura el respaldo de ese salto (el de su paso 2)
en un volumen nuevo.

Fuentes por salto, consultadas el 2026-10-03:
- a 8.0: [*Upgrade a Standalone to 8.0*](https://www.mongodb.com/docs/manual/release-notes/8.0-upgrade-standalone/):
  exige 7.0 con FCV `"7.0"`; `confirm: true`;
- a 7.0: [*Upgrade a Standalone to 7.0*](https://www.mongodb.com/docs/manual/release-notes/7.0-upgrade-standalone/):
  exige 6.0 con FCV `"6.0"`; `confirm: true`;
- a 6.0: [*Upgrade a Standalone to 6.0*](https://www.mongodb.com/docs/v6.0/release-notes/6.0-upgrade-standalone.md)
  (archivada): exige 5.0 con FCV `"5.0"`; FCV `"6.0"` sin `confirm`;
- a 5.0 (en el contenedor viejo, [antes de migrar](#si-la-versión-es-44-o-menor)):
  [*Upgrade a Standalone to 5.0*](https://www.mongodb.com/docs/v5.0/release-notes/5.0-upgrade-standalone.md)
  (archivada): exige 4.4 con FCV `"4.4"` y que `mongod` se haya apagado limpio; FCV `"5.0"` sin
  `confirm`.

## Volver atrás

Los datos están en el respaldo del paso 2.
1. `git checkout` del commit anterior y `./scripts/build.sh && ./scripts/run.sh`. El compose viejo
   crea un volumen anónimo nuevo y vacío.
2. Repetir el paso 6 contra ese contenedor.
