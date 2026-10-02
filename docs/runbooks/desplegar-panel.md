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

## 1. Antes de desplegar

1. **Crear el grupo de operadores en authentik** (por ejemplo `modpack-operators`) y poner en él a
   quienes pueden actualizar, simular, hacer rollback, importar y asociar servidores. Quien opera
   también tiene que estar en un grupo de `OIDC_ALLOWED_GROUPS`; un editor es quien entra sin ser
   operador. Comprobar que el proveedor OIDC de la aplicación manda el claim `groups`.
2. **`.env.yaml`:** agregar `OIDC_OPERATOR_GROUPS` (obligatoria, como `OIDC_ALLOWED_GROUPS`). Vacía
   (`""`) significa que nadie opera: es la forma de desplegar el panel sin habilitar operadores
   todavía. Sin la variable, la API no arranca.
3. **`docker/.env`:** agregar `FORWARDED_ALLOW_IPS` con la IP o red **del proxy inverso tal como la
   ve el contenedor**. Es obligatoria: sin ella, el compose se niega a arrancar. Con un valor
   equivocado uvicorn ignora `X-Forwarded-Proto`, la API cree que habla en `http` y el chequeo de
   origen rechaza con 403 cada acción del panel.

   ```bash
   docker network inspect bridge --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}'   # si el proxy corre en el host
   ```

4. **El proxy inverso** tiene que:
   - reenviar `Host` tal como lo mandó el navegador (en nginx, `proxy_set_header Host $host;`);
   - mandar `X-Forwarded-Proto` con el esquema real (`$scheme`);
   - **no reescribir `Origin`**: el chequeo compara el `Origin` del navegador con
     `esquema://host` de la API, y si el proxy lo cambia, todo POST da 403;
   - no quitar `Sec-Fetch-Site` ni agregar su propia CSP: la API ya manda la suya.
5. **Respaldo** de MongoDB, como en el paso 2 de `migrar-mongo-a-volumen.md`. El despliegue no toca
   ni borra documentos, pero es un cambio que sube a producción y el respaldo es barato.

## 2. Qué cambia en MongoDB

Nada se migra ni se reescribe. Al arrancar, Beanie crea:
- los índices nuevos de `Job` (`created_at` y `status` + `created_at`), que el panel usa para
  listar los trabajos recientes;
- la colección `AuditEvent` en la base `audit`, con sus índices.

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

La imagen se construyó por primera vez en CI al empujar: si `build.sh` falla en la etapa `assets`,
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

Los datos no cambian, así que volver es el código: `git checkout` del commit anterior,
`./scripts/build.sh && ./scripts/run.sh`. La colección `audit` y los índices nuevos quedan en
Mongo sin que nadie los lea; las sesiones nuevas siguen siendo válidas para el código viejo
(`get_current_user` las lee igual). `FORWARDED_ALLOW_IPS` sobra pero no estorba.
