# Panel web con Tailwind, dos niveles de permisos y lo que falta para producción

Diseño aprobado por el dueño el 2026-10-02, sección por sección. Cubre el panel del dueño
(i-7985ee-50d411) y los pendientes de código para desplegar. Lo que hace el dueño en el servidor
sigue en los runbooks.

## Qué se quiere

- **Un panel completo**: armar modpacks y operar servidores desde el navegador, sin Swagger.
- **Lo usan varios admins**: hace falta saber quién hizo qué, y no todos pueden escribir en los
  servidores.
- **Dos niveles**: editor y operador, por grupos del IdP.
- **Tailwind compilado con npm** (`@tailwindcss/cli`). Es la opción C, elegida por el dueño. Esto
  reemplaza el "sin build de JS" de `research/2026-10-arquitectura.md` §5, y va como decisión
  nueva en `docs/decisions.md`.
- **Listo para producción**: lo que falta de código para desplegar, y el runbook al día.

**Éxito:**
- un editor arma un pack hasta un build recomendado sin tocar `/docs`;
- un operador actualiza un servidor, primero simulado y después real, y sigue el trabajo en
  vivo;
- un editor no puede escribir en un servidor por ninguna vía: ni el panel ni `/v1`;
- cada acción queda en la auditoría con su autor;
- la imagen se construye con el CSS dentro, y el gate pasa.

## Fuentes consultadas (2026-10-02)

- **Tailwind v4.3.3** (2026-07-16). Se instala con `npm install tailwindcss @tailwindcss/cli`.
  La entrada es `@import "tailwindcss";` y se compila con
  `npx @tailwindcss/cli -i in.css -o out.css`. El Play CDN "is designed for development purposes
  only, and is not intended for production" (tailwindcss.com/docs).
- **htmx**: la 4.0.0 salió el 2026-08-28, después de varias betas. La 2.0.x sigue con
  mantenimiento (2.0.10, 2026-09-06). Se elige **2.0.11** (la etiqueta `latest` de npm; la 4 está como `next`); migrar a la 4 queda para después.
  Sobre seguridad, la documentación dice: token CSRF por `hx-headers`, `selfRequestsOnly`
  (activado por defecto), `allowEval: false` y una CSP como capa adicional.
- **OWASP, hoja de CSRF**: Fetch Metadata es defensa en profundidad. Si falta `Sec-Fetch-*`,
  verificar el origen "is a mandatory requirement". `same-site` solo se acepta si se confía en
  los subdominios hermanos. Una cookie `SameSite` no protege de un subdominio hermano.

## 1. Arquitectura y build

```
package.json, package-lock.json   tailwindcss y @tailwindcss/cli 4.3.3, htmx.org 2.0.11: versiones exactas
src/web/assets/app.css            @import "tailwindcss"; @source "../templates";
src/web/templates/                Jinja2: base.html, macros (tabla, botón, badge, aviso, confirmación) y una carpeta por sección
src/web/static/dist/              salida del build, en .gitignore: app.css minificado y htmx.min.js
src/routers/web/                  routers HTML: inyectan solo servicios, como /v1 (d-7985ee-16ed28)
```

**Build**
- `npm run build` compila el CSS minificado y copia `htmx.min.js` desde `node_modules`, así que
  el lockfile fija htmx.
- `npm run dev` hace lo mismo en modo `--watch`.
- El Dockerfile tiene dos etapas:
  1. `FROM node:24-slim AS assets` corre `npm ci` y `npm run build`;
  2. la imagen Python copia solo `src/web/static/dist/`. Node no llega a la imagen final.

**CSS**
- El CSS compilado nunca se versiona: se regenera en cada build de la imagen, así que no puede
  quedar viejo frente a las plantillas (tarjeta `derived-copy-goes-stale-silently`).
- Se sirve con `?v=<sha256 corto del archivo>`, calculado al arrancar, para que el navegador no
  use una copia vieja.

**CI**
- El gate de Python no necesita Node: los tests renderizan HTML, no CSS.
- El job `image` comprueba, dentro de la imagen, que `dist/app.css` y `dist/htmx.min.js` existen
  y no están vacíos.
- Para que un chequeo así cuente, hay que verlo fallar una vez quitando la etapa
  (`a-check-must-be-seen-to-fail`).

**Dependencias Python:** `jinja2` y `python-multipart`, con `==` (`pinned_requirements`).

**Cómo se sirve**
- El panel va en `/` y `/static` es un `StaticFiles`. `/docs` y `/v1` no cambian.
- Si `dist/` no existe, como en un checkout sin `npm run build`, la app arranca igual y el panel
  se ve sin estilos. La carpeta `src/web/static/` se versiona con un `.gitkeep`, para que el
  montaje no falle.
- Todas las respuestas llevan esta CSP:
  `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https://cdn.modrinth.com data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'`.
  Los íconos de Modrinth se sirven desde `cdn.modrinth.com`. `/docs` necesita otra política,
  porque Swagger carga recursos de un CDN, así que la CSP se aplica a todas las rutas salvo
  `DOCS_PATH` y `/openapi.json`.
- htmx se configura por `<meta name="htmx-config">` con `allowEval: false`,
  `selfRequestsOnly: true` y `includeIndicatorStyles: false`. Este último evita que htmx inyecte
  un `<style>` inline, que la CSP bloquearía. No se usan `hx-on` ni scripts inline.
- El JS propio vive en `src/web/static/app.js`, versionado, para lo poco que htmx no cubre:
  manejar errores y las confirmaciones.

## 2. Permisos en dos niveles y auditoría

**Roles.** Se derivan en cada request y nunca se guardan (`persist-inputs-derive-verdicts`).
- **Editor**: quien pasa `authorize`, como hoy (`OIDC_ALLOWED_GROUPS` o un email verificado de
  `OIDC_ALLOWED_EMAILS`).
- **Operador**: además, uno de sus grupos está en `OIDC_OPERATOR_GROUPS`. Un email de la lista
  permitida nunca hace operador a nadie.

**Qué exige operador:**
- `UpdateService.enqueue_update`, también con `dry_run=true`, porque la simulación lee el panel en
  nombre de una escritura;
- `UpdateService.enqueue_rollback`;
- `PterodactylService.import_server`, `import_confirm` e `import_local`;
- `PterodactylService.server_link_bundle`.

Todo lo demás es de editor.

**Dónde se hace cumplir.** En los servicios, por construcción.
- Los métodos de arriba reciben un `Actor` y lanzan `HTTP_403_FORBIDDEN` si
  `not actor.is_operator`.
- `Actor` es un valor inmutable con la identidad (`iss`, `sub`), el nombre y `groups`.
  `is_operator` es una propiedad que se calcula con `OIDC_OPERATOR_GROUPS` leída al arrancar.
- Como el chequeo vive en el servicio, alcanza a `/v1`, al panel y a cualquier ruta futura
  (`kill-switch-reaches-every-path`).

**La sesión**
- En el login se guarda en la sesión, junto al usuario, la lista `groups` de los claims: el dato
  de entrada, no el veredicto. `get_current_actor` arma el `Actor` desde la sesión.
- Una sesión anterior a este cambio no trae `groups`, así que cuenta como editor (falla cerrada).
  Para operar, el usuario vuelve a entrar. Se prueban las dos mezclas: sesión vieja con código
  nuevo, y sesión nueva leída por `get_current_user` (`no-simultaneous-deploy`).
- **Límite aceptado:** sacar a alguien del grupo de operadores vale cuando vence su sesión, a lo
  más `SESSION_MAX_AGE` (12 h). El runbook lo dice, junto con cómo cortarlo antes: rotar
  `SESSION_SECRET` cierra todas las sesiones.

**Configuración**
- `OIDC_OPERATOR_GROUPS` es obligatoria (`getenv` sin default) y está en `.env.default`, como
  `OIDC_ALLOWED_GROUPS`.
- Vacía significa que nadie opera, y eso se valida con un test.
- El job `image` de CI la pasa al importar `main`.

**Auditoría: `AuditEvent`**
- Es un `Document` registrado en `init_beanie` (audit `registration`). Campos:
  - `at`;
  - `actor`: `iss`, `sub` y nombre;
  - `action`: un texto estable, como `build.create` o `server.update`;
  - `target`: tipo e id;
  - `details`: un dict pequeño, sin secretos;
  - `request_id`.
- Lleva un índice por `at` descendente y otro por `target`.
- **Cuándo se escribe:**
  - Una acción que solo toca Mongo escribe el evento después de tener éxito.
  - **Una acción sobre un servidor real**, como encolar una actualización, un rollback o una
    importación, escribe el evento **antes** de encolar. Si esa escritura falla, la acción no
    ocurre (`order-writes-by-failure-residue`: lo que queda tras una caída es un evento sin
    trabajo, no un trabajo sin autor).
- `Job` suma `requested_by: Optional[ActorRef]`, opcional para que los trabajos viejos carguen.
  Lo copia el servicio que encola. Los trabajos del programador y de `notify` lo dejan vacío.
- Las acciones que se auditan:
  - de modpacks: crear y editar bundle, agregar, quitar y reemplazar proyectos, build, recomendar
    y reemplazos;
  - de servidores: alta, refresco, asociar bundle, escanear, importar, confirmar, local,
    actualizar y rollback.
  - Las lecturas no se auditan.

## 3. Páginas, en etapas

Los textos van en español. El modo oscuro sigue `prefers-color-scheme` (variante `dark:` de
Tailwind). Una acción que el editor no puede hacer se ve deshabilitada con un "requiere
operador", y el servicio igual la rechaza.

1. **Base y tablero.**
   - `base.html` con navegación, el usuario y su rol, y el logout.
   - Sin sesión, `GET /` redirige a `/v1/login?redirect=/`.
   - El tablero muestra los servidores con su bundle y su estado de desvío (`ServerScan.drift`),
     los últimos builds de cada bundle y los diez trabajos más recientes.
2. **Modpacks.**
   - La lista y la creación (`ModpackBundleCreate`).
   - El detalle: los proyectos por origen (pack o admin), los archivos locales, los canales y
     los pins.
   - La búsqueda en Modrinth es en vivo (`hx-trigger="input changed delay:300ms"`), con un
     botón para agregar.
   - Resolver y mostrar el lock: entradas, razones, problemas y mods sin decidir con su
     selector de lado servidor.
   - Builds: crear (si da 409, se muestra la razón), la lista, recomendar y descargar el
     `.mrpack`.
   - Los reemplazos globales.
3. **Servidores.**
   - La lista, con altas desde el panel de Pterodactyl (`server_external`).
   - El detalle: bundle asociado, último escaneo, "escanear ahora" (un trabajo) y la comparación
     con su build (coincide, otra versión, falta, sobra, desconocido).
   - Importar, con los candidatos para confirmar o dejar como local.
   - **Actualizar:**
     1. el botón pide una simulación;
     2. la página muestra su resultado;
     3. "ejecutar de verdad" abre una confirmación que exige escribir el nombre del servidor, y
        la validación se repite en el servidor.
   - Rollback A o B, desde una actualización fallida.
4. **Trabajos y auditoría.**
   - La lista de trabajos con su estado y paso. Un trabajo activo se refresca solo
     (`hx-trigger="every 2s"`) y deja de pedir al terminar: la respuesta sin el trigger corta
     el sondeo.
   - La auditoría, paginada y filtrable por actor, acción y objetivo.

Se necesitan algunos métodos nuevos de lectura en los servicios: listar trabajos recientes,
listar la auditoría y los últimos builds por bundle. Pasan por `*Database`, nunca desde el
router.

## 4. Seguridad web, errores, tests y producción

**CSRF**
- Un middleware para toda la app (`/v1` y panel) rechaza con 403 los métodos POST, PUT, PATCH y
  DELETE cuando:
  - `Sec-Fetch-Site` existe y no es `same-origin`. No se acepta `same-site`: no se confía en los
    subdominios hermanos;
  - o, si falta, `Origin` existe y no coincide con el origen de la app.
- Si faltan los dos encabezados, el request pasa. Es el caso de un cliente sin navegador (curl,
  un script) que ya tiene la cookie. Un navegador que cruza el origen siempre manda `Origin` en
  un POST, así que no puede llegar a este caso.
- `/v1/login/callback` es GET y queda fuera.
- El origen de la app se toma del request (esquema y `Host`). Detrás del proxy inverso, uvicorn
  tiene que confiar en los encabezados reenviados para ver el esquema y el host reales.
  `--proxy-headers` ya está activo por defecto, y las IPs del proxy se dan en la variable
  `FORWARDED_ALLOW_IPS`, que uvicorn lee directamente. Esa variable va en el `environment` del
  compose, no en `.env.yaml`, porque la lee uvicorn antes de cargar la app. El runbook la
  configura, y un test cubre que un request con `Origin: https://<host público>` pasa cuando el
  esquema llega reenviado.

**Errores con htmx**
- Una respuesta 401 a un request de htmx lleva `HX-Redirect: /v1/login?redirect=<página>`.
- Los 403, 404 y 409 devuelven un fragmento de aviso que `app.js` pone en una zona fija.
  Esto se hace con `htmx:beforeSwap` habilitando el swap para esos códigos, sin `eval`.
- Un 500 muestra un aviso genérico con el `request_id`.

**Tests** (se escriben antes que el código y se ven fallar):
- Barrido de rutas del panel: sin sesión, toda ruta redirige al login (o da 401 si viene de
  htmx), igual que `tests/test_routes_auth.py` para `/v1`.
- **Barrido de roles:** con una sesión de editor, toda ruta de operador da 403, en `/v1` y en el
  panel. Las rutas de operador se enumeran desde los métodos de servicio que exigen `Actor`, no
  a mano.
- Servicios: un editor recibe 403 y no se encola nada. Un operador encola y deja el evento antes
  que el trabajo. Si la auditoría falla, no hay trabajo.
- Sesión vieja sin `groups`: cuenta como editor. Sesión nueva: `get_current_user` la sigue
  leyendo.
- CSRF: cross-site, same-site, origen distinto y sin encabezados dan 403. Same-origin pasa. GET
  pasa siempre.
- CSP: el encabezado está en el panel y en `/v1`, y no está en `/docs`.
- Plantillas: cada página renderiza con datos de prueba, y los nombres se escapan (un mod llamado
  `<script>` aparece como texto).
- Auditoría contra Mongo real (fixture `mongo`): un evento por acción, con el autor.
- **Fuera del gate, y se dice en el reporte:**
  - la vista en distintos anchos (360 px, 768 px, 1280 px), con nombres largos y packs de 300
    mods, revisada a mano con capturas (`sweep-the-rendered-extremes`);
  - el login real con un operador y un editor;
  - la imagen en el servidor.

**Producción (subproyecto A)**
- **Latido del worker:** `JobWorker` actualiza en memoria un `last_beat` en dos momentos: en
  cada vuelta del bucle (cada `idle`, 5 s) y en cada latido del trabajo en curso (cada
  `heartbeat_every`, 20 s). Así, un paso largo, como los 5 minutos de estabilidad de una
  actualización, no lo deja viejo. `/health/ready` da 503 si este proceso tiene worker
  (`JOBS_WORKER` no es `off`) y el último latido tiene más de 120 s, o si la tarea del worker
  terminó. El test congela el reloj, y también cubre un proceso con `JOBS_WORKER: "off"`, que
  nunca falla por esto.
- **Imagen y CI:** la etapa de Node y el chequeo de `dist/`. También se pasa
  `OIDC_OPERATOR_GROUPS` en el job `image`.
- **`.env.default`:** `OIDC_OPERATOR_GROUPS`. **`docker/docker-compose.yaml`:**
  `FORWARDED_ALLOW_IPS: ${FORWARDED_ALLOW_IPS:?}`, sin default, en el `environment` de la API, y
  el valor en `docker/.env` (la IP o la red del proxy inverso).
- **Runbooks:**
  - `migrar-mongo-a-volumen.md` suma crear el grupo de operadores en authentik y la variable;
  - un `docs/runbooks/desplegar-panel.md` para el proxy (encabezados reenviados) y la
    comprobación después del despliegue.
- **Roadmap:**
  - i-7985ee-50d411 pasa a "Hecha" al cerrar la etapa 4;
  - se corrigen las tres entradas desactualizadas: logs con `request_id`, trabajos que escriben y
    avisos de la fase 3. Se verifican antes en el código.
- **Necesita un dato del dueño:** la versión de Mongo de producción, para igualar CI.

## Fuera de alcance

- La migración a htmx 4.
- starlette-admin.
- Páginas para jugadores.
- Una vista previa de changelogs en las actualizaciones.
- Roles más finos que dos niveles.
- Revocar el rol de operador antes de que venza la sesión, más allá de rotar `SESSION_SECRET`.

## Orden de trabajo

Cada etapa es un cambio que pasa el gate por sí solo:

1. producción y CSRF, la base del build y la CSP;
2. `Actor`, roles y auditoría, en los servicios y en `/v1`;
3. la etapa 1 del panel;
4. la etapa 2 (modpacks);
5. la etapa 3 (servidores);
6. la etapa 4 (trabajos y auditoría), con el cierre del roadmap y de las decisiones.
