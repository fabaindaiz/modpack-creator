# Runbook: desplegar el panel web, los roles y la auditoría

**Para quién:** el dueño, en el servidor, al desplegar la rama `feat/panel-web` (o `main` después
del merge). **Por qué hace falta:** el panel (i-7985ee-50d411) suma dos roles que salen de los
grupos del IdP, un registro de auditoría, un chequeo de origen contra CSRF y una CSP estricta. Los
tres dependen de que el proxy inverso y el compose estén bien configurados, y nada de eso se puede
probar en CI: la imagen y el proxy solo existen en el servidor. Ninguna de estas comprobaciones
escribe en un servidor de juego.

Antes de empezar, si el servidor aún corre la versión anterior a la fase 0, seguir primero
[`migrar-mongo-a-volumen.md`](migrar-mongo-a-volumen.md). Este runbook supone `docker/.env` con
`MONGO_VERSION` y las contraseñas, y el volumen `mongodb-data` ya en uso. Nunca
`docker compose … down -v` (d-7985ee-398653).

## La ronda 3 (2026-10-21, rama `feat/ronda-3`, sale en 0.3.2 salvo que el dueño autorice otra)

Sigue de solo lectura. **Ninguna variable nueva** (la clave del diagnóstico sale de `SESSION_SECRET`),
**nada se migra**: los documentos viejos cargan con sus campos nuevos vacíos. Lo que se nota:
- **Una base nueva, `diagnostics`**, con un índice TTL de 30 días sobre `at` (d-7985ee-ea4185); Beanie
  lo crea al arrancar. Cambiar después los días pide `collMod` a mano, no basta con el código.
- **Las subidas se copian a `/tmp/modpack-imports` del contenedor** para su trabajo (d-7985ee-4c3f11).
  Recrear el contenedor (`update.sh`) con una importación en cola la hace fallar: esperar a que no
  haya importaciones en curso antes del deploy, o pedir que se suba de nuevo.
- **`POST /v1/server/{id}/scan` responde 202 con el trabajo**, no el escaneo: un script que lo use
  cambia.
- **Cada escaneo lee una vez más el panel** (el nodo del servidor). Los escaneos viejos no tienen
  nodo y no cuentan como cambio, así que el deploy no dispara escaneos completos.
- **Cada mod de un pack importado queda fijado** a la versión del pack (d-7985ee-fce4ff): solo los
  packs importados después del deploy; los anteriores no cambian.
- Recargar las pestañas abiertas después del deploy (el estilo y la navegación cambiaron).

## De 0.3.1 a 0.3.2 (2026-10-13)

Los selects en la fila de cada mod, los filtros, la pestaña Sugerencias y auditoría y trabajos como
tarjetas (i-7985ee-44060c). Sigue de solo lectura. **Ninguna variable nueva, nada se migra**; «Volver
atrás» a 0.3.1 sigue valiendo. Lo que se nota:
- **`audit` tiene un índice nuevo.** Beanie crea al arrancar `(at, _id)` y no borra los viejos
  (`at`, `target.kind+id`, `actor.sub`), que quedan sin uso: la página filtra dentro de `$facet`, que
  no usa índices. Con pocos eventos no importa; borrarlos a mano es opcional.
- **Los eventos guardan el nombre de su objetivo** desde esta versión; los anteriores se ven con su
  tipo e id.
- **Una página del panel abierta durante el deploy** sondea `/jobs/{id}/tr`, que ya no existe: avisa
  un error cada 2 s hasta recargarla. Recargar las pestañas abiertas después del deploy.
- **Los filtros de la lista de mods y de la auditoría viven en la URL**: un enlace guardado los
  conserva.

**También la tanda B** (estilo, alta guiada, tablero y móvil; sale en 0.3.2, d-7985ee-a95f45):
- La imagen arma un sprite de íconos (`scripts/build-icons.mjs`, Lucide) y sirve Inter desde
  `src/web/static/fonts/`; los avisos de licencia están en `/static/LICENSES.html`. Nada cambia en
  `docker/.env` ni en `.env.yaml`.
- Las actualizaciones nuevas guardan su pack (`bundle_id`): el último paso del alta («Simulado») de un
  servidor queda sin hacer hasta su primera simulación después del deploy.
- Crear un pack pide a Modrinth la lista de versiones de Minecraft (una vez al día; si no responde en
  3 s, el campo de texto de siempre).

## De 0.3.0 a 0.3.1 (2026-10-12)

La ronda 2 del panel, tanda 1: avisos de bajas de versión y la confirmación «Fijar igual», el aviso de
jars sin explicar en el diálogo de actualización, enlaces en los changelogs, la carrera al crear un
pack desde un servidor y la limpieza al quitar un mod. Sigue de solo lectura. **Ninguna variable
nueva, ninguna colección nueva, nada se migra**; «Volver atrás» a 0.3.0 sigue valiendo. Dos cambios
que se notan:
- **Quitar un mod** borra también su pin, su decisión de servidor y su excepción de canal, salvo que
  la última resolución guardada (y vigente) lo traiga como dependencia de otro mod
  (d-7985ee-881689). La auditoría (`bundle.project.remove`) guarda lo borrado, por si hay que
  reponerlo a mano.
- **`PATCH /v1/modpack/{id}`** con un pin anterior al piso responde 409; para guardarlo igual,
  `?confirm_downgrade=true` (d-7985ee-0b9ebb). Un cliente de la API que fije pines tiene que saberlo.

Si se despliega desde 0.2.0, valen también los pasos de abajo.

## De 0.2.0 a 0.3.0 (2026-10-12)

Sigue siendo de solo lectura (`PTERODACTYL_WRITES` vacía). Ninguna variable obligatoria nueva; lo
que hay que mirar antes de `./scripts/update.sh`:
- **Proxy:** dejar pasar subidas de hasta 1 GiB (el zip de Prism; paso 4). Sin eso, importar un zip
  grande da 413 en el proxy, no en la app.
- **Disco del contenedor:** las subidas de importación se escriben en `/tmp` del contenedor mientras
  dura la petición, hasta `IMPORT_TMP_MAX_MIB` juntas (opcional, por defecto 4096 MiB). Que el disco
  del host tenga ese espacio libre.
- **CurseForge (opcional):** `CURSEFORGE_API_KEY` en `.env.yaml`, la clave propia pedida con el
  formulario de CurseForge. Sin ella el panel no ofrece CurseForge (d-7985ee-9473a9).
- **La clave de Pterodactyl** tiene que poder leer la actividad de cada servidor (`activity.read`; el
  dueño y un root admin la tienen siempre): el escaneo la mira para detectar una restauración. Sin
  ese permiso, cada escaneo es completo (d-7985ee-894b2c).
- **MongoDB:** dos colecciones nuevas, `LoaderVersion` y `PanelServerList` (§2). Nada se migra; la
  versión anterior ignora los campos nuevos, así que «Volver atrás» sigue valiendo.

## 1. Antes de desplegar

1. **Crear el grupo de operadores en authentik** (por ejemplo `modpack-operators`) y poner en él a
   quienes pueden actualizar, simular, hacer rollback, importar y asociar servidores. Quien opera
   también tiene que estar en un grupo de `OIDC_ALLOWED_GROUPS`; un editor es quien entra sin ser
   operador. Comprobar que el proveedor OIDC de la aplicación manda el claim `groups`.
2. **`.env.yaml`:** agregar `OIDC_OPERATOR_GROUPS` (obligatoria, como `OIDC_ALLOWED_GROUPS`). Vacía
   (`""`) significa que nadie opera: es la forma de desplegar el panel sin habilitar operadores
   todavía. Sin la variable, la API no arranca.
3. **`docker/.env`:** el proxy inverso corre **en otra máquina** de la red (decisión del dueño,
   2026-10-05). Dos variables obligatorias; sin cualquiera de ellas el compose se niega a arrancar:
   - `API_BIND_IP`: la IP de LAN de este servidor por la que llega el proxy. La API se publica
     solo en ella (`API_BIND_IP:8000`), nunca en todas las interfaces;
   - `FORWARDED_ALLOW_IPS`: la IP del proxy **tal como la ve el contenedor**. Con Docker y el
     puerto publicado, el contenedor ve la IP de LAN del proxy. Con un valor equivocado uvicorn
     ignora `X-Forwarded-Proto`, la API cree que habla en `http` y el chequeo de origen rechaza
     con 403 cada acción del panel. El log de la API no registra la IP de origen; si hay dudas,
     en el host `sudo conntrack -L -p tcp --dport 8000` (paquete `conntrack`) muestra con qué IP
     llegan las conexiones al contenedor.

   Además, ya que la API queda en la LAN:
   - **firewall:** solo el proxy debería llegar a `API_BIND_IP:8000`. Docker publica los puertos
     con sus propias reglas de iptables, que **saltan ufw**. La restricción va en la cadena
     `DOCKER-USER`, con la interfaz de entrada y conntrack (el paquete ya pasó el DNAT):
     `iptables -I DOCKER-USER -i <interfaz> -p tcp -m conntrack --ctorigdstport 8000 --ctdir ORIGINAL ! -s <ip del proxy> -j DROP`
     (`docs/guia-instalacion.md` §7). Sin eso, cualquier equipo de la LAN entra por HTTP sin pasar
     por el proxy;
   - **entre el proxy y la API el tráfico va en HTTP plano:** la cookie de sesión cruza la LAN sin
     cifrar. Es aceptable en una LAN propia; si no lo es, el proxy tiene que llegar por un túnel
     (WireGuard, Tailscale).
   - **`PTERODACTYL_WRITES`** (en `.env.yaml`) **vacía o ausente** para el primer release, que es
     de solo lectura: se habilita recién con
     [`primera-actualizacion-real.md`](primera-actualizacion-real.md) (d-7985ee-5739fc).

4. **El proxy inverso** tiene que:
   - reenviar `Host` tal como lo mandó el navegador (en nginx, `proxy_set_header Host $host;`);
   - mandar `X-Forwarded-Proto` con el esquema real (`$scheme`);
   - **no reescribir `Origin`**: el chequeo compara el `Origin` del navegador con
     `esquema://host` de la API, y si el proxy lo cambia, todo POST da 403;
   - no quitar `Sec-Fetch-Site` ni agregar su propia CSP: la API ya manda la suya;
   - dejar pasar subidas de hasta 1 GiB, el zip de una instancia de Prism (en nginx,
     `client_max_body_size 1025m;`).
5. **Los orígenes de `CORS_ALLOW_ORIGINS`, si hay alguno, solo leen** (d-7985ee-491c83). Pueden
   seguir haciendo un GET con la cookie, pero todo POST, PUT, PATCH o DELETE desde otro origen da
   403 en el chequeo de origen, también desde uno nombrado ahí. Un cliente en otro origen que
   escribía en la API deja de poder hacerlo: tiene que servirse desde el mismo origen que la API.
6. **Respaldo** de MongoDB, como en el paso 2 de `migrar-mongo-a-volumen.md`. El despliegue no toca
   ni borra documentos, pero es un cambio que sube a producción y el respaldo es barato.
7. **Ningún trabajo `server_update` sin `payload.dry_run`.** Desde N1, un trabajo de
   actualización sin esa clave cuenta como simulación en todos lados (falla cerrado): si una
   actualización real vieja no la tuviera, el panel la mostraría como simulación y no ofrecería su
   rollback. Con el `MONGODB_PASSWORD` de "1. Leer la versión que corre" de
   `migrar-mongo-a-volumen.md` (base `jobs`, colección `Job`, como las registra
   `src/model/__init__.py`), y con su `unset` antes de levantar nada (ese mismo paso dice por qué):

   ```bash
   docker exec modpack-mongodb mongosh --quiet -u mongoadmin -p "$MONGODB_PASSWORD" \
     --authenticationDatabase admin jobs \
     --eval 'db.Job.countDocuments({kind:"server_update","payload.dry_run":{$exists:false}})'
   ```

   Tiene que dar `0`. Si da otro número, **no desplegar todavía**: listarlos (mismo comando con
   `db.Job.find({kind:"server_update","payload.dry_run":{$exists:false}},{status:1,created_at:1,payload:1}).toArray()`)
   y decidir con el dueño qué fue cada uno antes de seguir.

## 2. Qué cambia en MongoDB

Nada se migra ni se reescribe. Al arrancar, Beanie crea:
- dos índices nuevos de `Job`: `created_at` (descendente), con el que el panel lista los trabajos
  recientes, y `key` + `created_at` (descendente), con el que un rollback busca la última
  actualización del servidor. El de `status` + `created_at` ya existía;
- la colección `AuditEvent` en la base `audit`, con sus índices;
- la colección `UpdateCheck` (las revisiones de actualizaciones), con un índice por `bundle_id`,
  `checked_at` (descendente) y `_id` (descendente), con el que el panel lee la última revisión de
  cada modpack.

- desde 2026-10-12, las colecciones `LoaderVersion` (base `modpack`) y `PanelServerList` (base
  `pterodactyl`), cada una con un índice único por `key`.

Crear un índice no toca ningún documento. Los escaneos viejos cargan sin `created_at` en sus
archivos y siguen sirviendo como caché. Tampoco se reescribe el catálogo: `ModrinthVersion`
suma el campo opcional `changelog`, que los documentos viejos no tienen y cargan igual; se llena
solo, la primera vez que alguien abre una vista previa de los cambios.

**Revisión diaria de actualizaciones (opcional).** Dos variables nuevas, ambas opcionales y
fuera de `.env.default`:
- `UPDATE_CHECK_AT`: la hora local `HH:MM` (24 horas, dos dígitos) a la que cada modpack con un
  build se revisa una vez al día. Sin ella no hay programación; el botón de la página
  `/modpacks/{id}/actualizaciones` sigue funcionando. Un valor mal escrito **impide arrancar**
  (`UPDATE_CHECK_AT must be HH:MM…` en los logs), a propósito;
- `UPDATE_CHECK_TZ`: la zona horaria, por defecto `America/Santiago`; una zona desconocida
  también impide arrancar.

**Las dos van entre comillas** en `.env.yaml`:

```yaml
UPDATE_CHECK_AT: "04:30"
UPDATE_CHECK_TZ: "America/Santiago"
```

Sin comillas, YAML (1.1, el que lee `.env.yaml`) toma `10:30` como el número 630 en base 60, y
la API ignora todo valor de `.env.yaml` que no sea texto. Antes eso apagaba la programación sin decir nada;
ahora la API **no arranca** y los logs dicen `UPDATE_CHECK_AT debe ir entre comillas en .env.yaml`
(N-R5). `04:30` sin comillas sí queda como texto, por el cero inicial, pero no hay que contar con
eso. Cualquier otra variable que YAML no lea como texto se sigue ignorando como antes, ahora con
un aviso en los logs que nombra la variable (nunca su valor).

Una revisión cuenta como la del día, sea programada o manual: una revisión hecha con el botón
antes de `UPDATE_CHECK_AT` es la de ese día, y el programador no vuelve a revisar ese modpack
hasta el día siguiente. Una revisión que falla también cuenta (queda guardada con su error).

Una revisión solo lee Modrinth y guarda el resultado; no toca ningún servidor. Avisa por Discord
(si hay `DISCORD_WEBHOOK_URL`) solo cuando las actualizaciones sin pin difieren de las del último
aviso. Para comprobarlo después de desplegar: poner `UPDATE_CHECK_AT` unos minutos adelante,
reiniciar, y mirar en `/jobs` un trabajo "Revisión de actualizaciones" (`update_check`) por modpack con build.

Los trabajos viejos cargan sin `requested_by` (el campo es opcional). Crear un índice sobre una
colección chica tarda un instante; en el primer arranque, `/health/ready` puede tardar unos
segundos más en dar 200.

## 3. Desplegar

```bash
git pull
./scripts/build.sh       # la imagen compila el CSS del panel en una etapa Node (d-7985ee-d948fc)
./scripts/run.sh
docker ps --format '{{.Names}} {{.Status}}'   # modpack-api y modpack-mongodb en (healthy)
```

`build.sh` etiqueta la imagen dos veces: `modpack-creator-api:latest`, la que levanta el compose, y
`modpack-creator-api:<commit>`. **Anotar el commit que corría antes de desplegar**
(`docker images modpack-creator-api`): es el de "Volver atrás". La versión que corre se ve en
`/docs` (por ejemplo `0.1.0+57ed2e0`).

mongo-express ya no arranca con `run.sh`: se levanta solo a pedido, con
`docker compose -f docker/docker-compose.yaml up -d mongoweb`, y `./scripts/stop.sh` lo apaga.
Sigue en `127.0.0.1:8081` (un túnel SSH para mirarlo desde otro equipo).

La imagen se construye por primera vez en CI al empujar: si `build.sh` falla en la etapa `assets`,
se ve aquí. Los usuarios que ya tenían sesión **tienen que volver a entrar**: su sesión no trae
`groups` y cuenta como editor hasta entonces.

## 4. Comprobar después del despliegue

Con `https://panel.example` como la dirección pública (la del proxy).

1. **`/health/ready` da 200.**

   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' https://panel.example/health/ready
   docker exec modpack-api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health/ready').read())"
   ```

   Da 503 si Mongo no contesta o si el worker de trabajos no late desde hace más de 120 s
   (salvo con `JOBS_WORKER: "off"`, que no lo exige). Si da 503, mirar `docker logs modpack-api`.
2. **Un login de editor no ve habilitados los botones de operador.** Entrar con una cuenta en
   `OIDC_ALLOWED_GROUPS` pero fuera de `OIDC_OPERATOR_GROUPS`. En un servidor, "Simular
   actualización", "Importar" y el selector de modpack con "Guardar" se ven deshabilitados, con "Requiere
   operador" de explicación.
   Si se fuerza la petición (por ejemplo con `curl` y la cookie), el servicio responde 403:
   el botón no es la defensa.
3. **Un login de operador hace una simulación.** Cerrar sesión, entrar con una cuenta de
   `OIDC_OPERATOR_GROUPS`, y en un servidor de **prueba** pulsar "Simular actualización". Es una
   simulación (`dry_run`): lee el panel y no escribe nada. El trabajo aparece en "Trabajos" y la
   acción en "Auditoría", con el nombre del operador y antes que el trabajo. Una actualización real
   sigue el runbook [`primera-actualizacion-real.md`](primera-actualizacion-real.md).
4. **Un POST de otro origen da 403.**

   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' -X POST -H 'Origin: https://otro.example' \
     https://panel.example/v1/modpack
   ```

   Tiene que dar `403`, aunque no haya sesión (el chequeo de origen corre antes). Si da `401`, el
   proxy está reescribiendo o quitando `Origin`. Y la prueba inversa: una acción desde el panel
   en el navegador **no** puede dar 403; si lo da, `FORWARDED_ALLOW_IPS` o `Host` están mal
   (paso 1.3 y 1.4).
5. **La CSP se ve en las herramientas del navegador.** En Red, la respuesta de cualquier página
   trae `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; …`, y
   la consola no muestra violaciones. `/docs` no la lleva a propósito: carga sus scripts de un CDN.

## 5. El límite de revocación

Un operador conserva el rol hasta que vence su sesión: el rol se lee de los grupos que el IdP
mandó **al iniciar sesión**, y la cookie dura `SESSION_MAX_AGE` (43200 s, 12 h, por defecto). Sacar
a alguien de `OIDC_OPERATOR_GROUPS` en authentik no lo corta hasta entonces.

Para cortarlo ya: **rotar `SESSION_SECRET`** en `.env.yaml` y reiniciar la API
(`./scripts/stop.sh && ./scripts/run.sh`, que conserva los volúmenes). Eso invalida **todas** las
sesiones: todos vuelven a entrar, y los que ya no están en el grupo entran como editores.

## Volver atrás

Los datos no cambian, así que volver es la imagen anterior, sin recompilar:

```bash
docker tag modpack-creator-api:<commit anterior> modpack-creator-api:latest
./scripts/run.sh          # el compose ve otra imagen detrás de :latest y recrea la API
```

Si esa imagen ya no existe, desde git: `git checkout <commit anterior>`,
`./scripts/build.sh && ./scripts/run.sh`. La colección `audit` y los índices nuevos quedan en
Mongo sin que nadie los lea; las sesiones nuevas siguen siendo válidas para el código viejo
(`get_current_user` las lee igual). `FORWARDED_ALLOW_IPS` sobra pero no estorba.
