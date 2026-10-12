# Guía de instalación

Cómo instalar modpack-creator en un servidor nuevo, de cero a un panel que funciona. Es para el
dueño, en el servidor. Si el servidor **ya corría** una versión anterior, no empezar aquí: seguir
primero [`runbooks/migrar-mongo-a-volumen.md`](runbooks/migrar-mongo-a-volumen.md) y después
[`runbooks/desplegar-panel.md`](runbooks/desplegar-panel.md).

Versión de esta guía: **0.2.0** (2026-10-09). Como el primero, este release es **de solo lectura**: arma
modpacks, escanea servidores y compara, pero no escribe en ningún servidor de juego hasta que se
habilite a propósito (paso 10).

## Qué queda instalado

```text
navegador ──https──▶ proxy inverso ──http (LAN)──▶ API :8000 ──▶ MongoDB (volumen mongodb-data)
                     (otra máquina, TLS)            (este servidor, solo en API_BIND_IP)
                                                     │
                                                     ├──▶ authentik (login OIDC)
                                                     ├──▶ api.modrinth.com, meta.fabricmc.net, meta.quiltmc.org
                                                     └──▶ Pterodactyl (Client API, solo lectura)
```

- **Este servidor:** la API (`modpack-api`) y MongoDB (`modpack-mongodb`), con Docker Compose.
  mongo-express (`modpack-mongoweb`) solo se levanta a pedido.
- **Otra máquina de la red:** el proxy inverso, que termina TLS y reenvía a `API_BIND_IP:8000`.

## Requisitos

| Dónde | Qué |
|---|---|
| Este servidor | Linux con **Docker Engine y Docker Compose v2** (`docker compose version`), `git`, salida a internet hacia Modrinth, las metas de Fabric y Quilt, authentik y el panel de Pterodactyl |
| La máquina del proxy | nginx o Caddy, con un certificado TLS para el nombre del panel (por ejemplo `panel.example`) |
| authentik (u otro IdP OIDC) | una aplicación OAuth2/OIDC que mande el claim `groups` |
| Pterodactyl | un usuario de servicio **no admin**, con una clave de la Client API (`ptlc_…`) |

## 1. Clonar

```bash
git clone <url del repositorio> modpack-creator
cd modpack-creator
git checkout v0.2.0
```

## 2. authentik

1. Crear una aplicación con un proveedor **OAuth2/OpenID**:
   - **Redirect URI, en modo Regex:**
     `^https://panel\.example/v1/login/callback(\?redirect=[^&#]*)?$`. La app agrega a la URI
     la página a la que vuelve después del login (`?redirect=…`, `src/routers/v1/auth.py`), así
     que una URI en modo Strict sin la query no coincide. Los puntos van escapados;
   - scopes: `openid`, `email`, `profile`, y que el token lleve el claim `groups`.
2. Crear dos grupos:
   - el de **usuarios** del panel (por ejemplo `modpack-users`): van a `OIDC_ALLOWED_GROUPS`;
   - el de **operadores** (por ejemplo `modpack-operators`): van a `OIDC_OPERATOR_GROUPS`. Un
     operador también tiene que estar en un grupo de usuarios.
3. Anotar el **Client ID**, el **Client Secret** y la URL de metadatos
   (`https://<authentik>/application/o/<slug>/.well-known/openid-configuration`).

Quien entra sin ser operador es **editor**: arma modpacks y builds, pero no opera servidores. La
identidad es `iss` + `sub`, nunca el email (d-7985ee-361ed0).

## 3. Pterodactyl

Con la clave de un administrador cualquiera abre todo el panel. Por eso:

1. Crear un usuario **no admin** (por ejemplo `modpack-bot`).
2. Agregarlo como **subusuario** de cada servidor que la app tenga que ver. Para este release, de
   solo lectura, bastan `file.read` (listar `mods/`), `file.read-content` (bajar un jar para
   hashearlo) y `backup.read` (la simulación lista los backups). Los permisos de escritura vienen
   después, en el paso 10.
3. Con ese usuario, crear una clave de la **Client API** (`ptlc_…`). En "Allowed IPs" poner la IP
   pública con que sale este servidor.

## 4. La configuración de la API: `.env.yaml`

```bash
cp .env.default .env.yaml
chmod 600 .env.yaml
```

Completar `.env.yaml`. **Todo valor va entre comillas**: YAML lee `10:30` o `8080` sin comillas
como números, y la API ignora lo que no es texto (o no arranca, en `UPDATE_CHECK_AT`).

**Obligatorias.** Sin cualquiera de estas variables, la API no arranca:

| Variable | Qué poner |
|---|---|
| `MONGODB_URI` | `"mongodb://mongoadmin:<MONGODB_PASSWORD>@mongodb:27017/"`, con la misma contraseña que `docker/.env` (paso 5). Generarla con `openssl rand -hex 24`: solo `0-9a-f`, así no hay que escaparla en la URI (una `/` sería `%2F`) y mongo-express, que la pone tal cual en su URL, no se rompe |
| `SESSION_SECRET` | un secreto largo y aleatorio: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`. Rotarlo cierra todas las sesiones |
| `OIDC_METADATA_URL` | la URL de metadatos del paso 2 |
| `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` | los del paso 2 |
| `OIDC_ALLOWED_GROUPS` | los grupos de usuarios, separados por coma: `"modpack-users"` |
| `OIDC_OPERATOR_GROUPS` | los grupos de operadores: `"modpack-operators"`. Si queda vacía (`""`), nadie opera |
| `PTERODACTYL_API_URL` | la URL del panel de Pterodactyl, por ejemplo `"https://panel.pterodactyl.example/"` |

**Opcionales:**

| Variable | Para qué |
|---|---|
| `PTERODACTYL_TOKEN` | la clave `ptlc_…` del paso 3. Sin ella no se leen servidores |
| `PTERODACTYL_WRITES` | **no definirla** en este release. Solo `"enabled"` habilita las escrituras (paso 10) |
| `OIDC_ALLOWED_EMAILS` | emails verificados que entran sin estar en un grupo, separados por coma |
| `DISCORD_WEBHOOK_URL` | avisos de actualizaciones, escaneos y trabajos |
| `UPDATE_CHECK_AT`, `UPDATE_CHECK_TZ` | revisión diaria de versiones nuevas: `"04:30"`, `"America/Santiago"` |
| `SCAN_EVERY_HOURS`, `SCAN_FULL_EVERY_DAYS` | escaneo periódico de los servidores (sin la primera, no hay); uno completo cada N días (por defecto 30: la fecha de creación de cada jar ya detecta una restauración) |
| `MODRINTH_TOKEN`, `MODRINTH_AGENT` | token de Modrinth y `User-Agent` propio (Modrinth pide uno que identifique al proyecto) |
| `CORS_ALLOW_ORIGINS` | otros orígenes que **solo leen** la API (d-7985ee-491c83) |
| `SESSION_MAX_AGE` | duración de la sesión en segundos (por defecto 43200, 12 h) |
| `IMPORT_TMP_MAX_MIB` | disco temporal que pueden ocupar juntas las subidas de importación en curso, en MiB (por defecto 4096). Cada archivo subido se escribe en `/tmp` del contenedor (o en `TMPDIR`) pasado 1 MiB y se borra al terminar la petición; un zip de Prism llega a 1 GiB. Una subida que no cabe se rechaza antes de leerse: «Hay otra importación subiendo; intenta en un momento». Dejarlo por debajo del espacio libre de ese disco |
| `LOG_LEVEL`, `REQUEST_TIMEOUT`, `CONNECT_TIMEOUT`, `JOBS_WORKER`, `DOCS_PATH`, `REDOCS_PATH` | ajustes finos; los valores por defecto sirven |

`.env.yaml` nunca se versiona. Se monta de solo lectura en el contenedor.

## 5. Las variables del compose: `docker/.env`

```bash
cp docker/.env.example docker/.env
chmod 600 docker/.env
```

| Variable | Qué poner |
|---|---|
| `MONGO_VERSION` | `8.0` en una instalación nueva. Con datos existentes, la que los escribió ([`runbooks/migrar-mongo-a-volumen.md`](runbooks/migrar-mongo-a-volumen.md)) |
| `MONGODB_PASSWORD` | la contraseña del usuario `mongoadmin`, la misma de `MONGODB_URI` |
| `MONGOWEB_PASSWORD` | la de mongo-express |
| `API_BIND_IP` | la IP de LAN de **este** servidor por la que llega el proxy. La API se publica solo ahí |
| `FORWARDED_ALLOW_IPS` | la IP de LAN **del proxy**: uvicorn solo confía en su `X-Forwarded-Proto` |

Sin cualquiera de las obligatorias, el compose se niega a arrancar: es a propósito.

## 6. El proxy inverso (en la otra máquina)

El proxy tiene que:
- reenviar `Host` tal como lo mandó el navegador;
- mandar `X-Forwarded-Proto` con el esquema real;
- **no** reescribir `Origin` ni quitar `Sec-Fetch-Site`;
- no agregar su propia CSP;
- dejar pasar subidas de hasta **1 GiB**: el zip de una instancia de Prism (importar un modpack) llega
  hasta ahí. nginx corta en 1 MiB por defecto (`client_max_body_size`). Caddy y Traefik no ponen
  límite salvo que se configure uno (SUPUESTO, de memoria: verificarlo en la versión instalada).

Si reescribe `Origin`, todo POST del panel da 403.

**nginx:**

```nginx
server {
    listen 443 ssl;
    server_name panel.example;
    # ssl_certificate y ssl_certificate_key: los del nombre del panel

    location / {
        client_max_body_size 1025m;  # el zip de una instancia de Prism, hasta 1 GiB
        proxy_pass http://<API_BIND_IP>:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

**Traefik** (proveedor de archivos; el entrypoint y el TLS, los de tu instalación). Un solo router
sirve el panel y la API: son la misma app en el mismo origen, sin separar rutas ni quitar prefijos:

```yaml
http:
  routers:
    modpack:
      entryPoints:
      - web
      service: modpack@file
      rule: Host(`panel.example`)
  services:
    modpack:
      loadBalancer:
        passHostHeader: true          # el default, explícito: el chequeo de origen compara contra este Host
        healthCheck:
          path: /health/live          # live: si Mongo cae, el panel muestra el error en vez de un 503 de Traefik
          interval: 30s
          timeout: 5s
        servers:
        - url: http://<API_BIND_IP>:8000
```

La app tiene que recibir `X-Forwarded-Proto: https`. Si el TLS termina antes de Traefik, su
entrypoint tiene que confiar en esa capa (`forwardedHeaders.trustedIPs`). Sin un forward-auth
delante: la app hace su propio login OIDC.

**Caddy** manda `Host`, `X-Forwarded-Proto` y `X-Forwarded-For` por defecto:

```caddyfile
panel.example {
    reverse_proxy <API_BIND_IP>:8000
}
```

Entre el proxy y la API el tráfico va en **HTTP plano**, y la cookie de sesión cruza la LAN sin
cifrar. En una LAN propia es aceptable; si no lo es, el proxy llega a la API por un túnel
(WireGuard, Tailscale) y `API_BIND_IP` es la IP del túnel.

## 7. El firewall de este servidor

Solo el proxy debería llegar al puerto 8000. Docker publica los puertos con sus propias reglas de
iptables, y esas reglas **saltan ufw**. La restricción va en la cadena `DOCKER-USER`, que ve el
paquete **después** del DNAT: por eso el puerto se compara con conntrack (`--ctorigdstport`), y
la interfaz de entrada (`-i`) evita bloquear las conexiones que salen de los contenedores hacia
otro puerto 8000 (https://docs.docker.com/engine/network/firewall-iptables/):

```bash
ip -o -4 addr show | grep <API_BIND_IP>      # el nombre de la interfaz, p. ej. eth0
sudo iptables -I DOCKER-USER -i <interfaz> -p tcp -m conntrack --ctorigdstport 8000 --ctdir ORIGINAL ! -s <IP del proxy> -j DROP
```

Para que sobreviva a un reinicio, guardarla con la herramienta de la distribución (por ejemplo
`netfilter-persistent save`). MongoDB (27017) y mongo-express (8081) solo escuchan en `127.0.0.1`.

## 8. Construir y levantar

```bash
./scripts/build.sh    # la imagen: modpack-creator-api:latest y :<commit>
./scripts/run.sh      # la API y MongoDB, en segundo plano
docker ps --format '{{.Names}} {{.Status}}'   # modpack-api y modpack-mongodb en (healthy)
```

El primer arranque crea las colecciones y los índices. `/health/ready` puede tardar unos segundos
en dar 200.

## 9. Comprobar

1. Salud, desde este servidor y a través del proxy:

   ```bash
   docker exec modpack-api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health/ready').read())"
   curl -s -o /dev/null -w '%{http_code}\n' https://panel.example/health/ready     # 200
   ```

2. La versión que corre aparece en `https://panel.example/docs` (`0.2.0+<commit>`).
3. **Un login con una cuenta fuera de los grupos** termina en un 403 ("is not allowed to use this
   API"), y no queda sesión.
4. **Un login de editor** ve deshabilitados los botones de operador.
5. **Un login de operador** puede simular una actualización en un servidor de prueba. La
   simulación lee y no escribe nada.
6. **Un POST de otro origen da 403:**

   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' -X POST -H 'Origin: https://otro.example' https://panel.example/v1/modpack
   ```

   Si una acción del propio panel da 403, revisar `FORWARDED_ALLOW_IPS` y que el proxy no toque
   `Host` ni `Origin`.
7. **Desde otro equipo de la LAN** que no sea el proxy, `curl -m 5 http://<API_BIND_IP>:8000/health/live`
   no responde: el firewall del paso 7 funciona.

El detalle de cada comprobación está en
[`runbooks/desplegar-panel.md`](runbooks/desplegar-panel.md) §4.

## 10. Más adelante: habilitar las escrituras

Las actualizaciones reales (con backup y rollback) escriben en servidores de juego. Se habilitan
recién con [`runbooks/primera-actualizacion-real.md`](runbooks/primera-actualizacion-real.md):
- darle al usuario de servicio, en el servidor de prueba, el resto de los permisos que lista
  [`research/2026-10-operacion-pterodactyl.md`](research/2026-10-operacion-pterodactyl.md) §1
  (`control.*`, `file.create/update/delete`, `backup.create/restore`…);
- poner `PTERODACTYL_WRITES: "enabled"` en `.env.yaml`;
- hacer la primera actualización con el runbook.

## Operar

| Para | Hacer |
|---|---|
| **Actualizar** a un commit o tag nuevo | `git fetch && git checkout <tag>`, luego `./scripts/build.sh && ./scripts/run.sh`. Antes, anotar el commit que corría (`docker images modpack-creator-api`). `./scripts/update.sh` hace `git pull` + build + run sobre la rama actual |
| **Volver atrás** | `docker tag modpack-creator-api:<commit anterior> modpack-creator-api:latest && ./scripts/run.sh`, sin recompilar |
| **Respaldar** MongoDB | `docker exec modpack-mongodb mongodump -u mongoadmin -p "$MONGODB_PASSWORD" --authenticationDatabase admin --archive --gzip > modpack-$(date +%F).archive.gz` |
| **Mirar la base** | `docker compose -f docker/docker-compose.yaml up -d mongoweb` y un túnel SSH a `127.0.0.1:8081`; `./scripts/stop.sh` lo apaga |
| **Ver los logs** | `docker logs -f modpack-api`: una línea JSON por petición, con su id. Rotan a 5 × 10 MB |
| **Detener** | `./scripts/stop.sh`. **Nunca** `docker compose … down -v`: borra el volumen con los datos |
| **Cortar todas las sesiones** | rotar `SESSION_SECRET` en `.env.yaml` y `./scripts/stop.sh && ./scripts/run.sh` |
