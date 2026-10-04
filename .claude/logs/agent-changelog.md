# Registro de cambios de los agentes

Cada sesión que cambia algo deja una entrada aquí, **la más nueva arriba**, justo debajo de esta
introducción. Los commits de este repositorio fueron `wip` sin cuerpo: el *por qué* de un cambio
no quedaba en ninguna parte, y una sesión no ve lo que otra aprendió. La referencia del formato
está al final del archivo, para que nada entre este párrafo y la primera entrada capture texto.

## 2026-10-02 · s-7985ee-aeb6be — Panel web, roles y auditoría, y despliegue seguro (i-7985ee-50d411)

**Qué.**
- **Panel web** (`src/routers/web/`, `src/web/`): Jinja2, htmx y Tailwind, en español, con modo
  oscuro. Tablero, modpacks (crear, agregar y quitar proyectos, resolver, decidir qué va al
  servidor, armar y recomendar builds, descargar el `.mrpack`, reemplazos globales; editar los
  datos de un modpack sigue en `/v1`), servidores (escanear, importar, vincular, simular,
  actualizar, rollback), trabajos con seguimiento y auditoría paginada.
- **Roles** (d-7985ee-edef1e): editor y operador, derivados de los grupos del IdP
  (`OIDC_OPERATOR_GROUPS`) y hechos cumplir en los servicios con `operator_only`. Una sesión sin
  `groups` es editor.
- **Auditoría** (d-7985ee-b4719f): `AuditEvent` en la base `audit`. Una actualización o un
  rollback escribe su evento antes de encolarse. `Job` suma `requested_by`.
- **Defensa web** (d-7985ee-491c83): rechazo de peticiones inseguras que no son del mismo origen
  (Fetch Metadata y `Origin`) y CSP estricta, ambos como middleware.
- **Compilación** (d-7985ee-d948fc): el CSS se compila con Tailwind en una etapa Node de la imagen.
- **Latido del worker** en `/health/ready` (120 s).
- **Despliegue:** `FORWARDED_ALLOW_IPS` pasa a obligatoria en el compose, y en CI. Runbook
  `docs/runbooks/desplegar-panel.md`; `OIDC_OPERATOR_GROUPS` y `FORWARDED_ALLOW_IPS` documentadas
  en el README y en `migrar-mongo-a-volumen.md`.
- **Seguridad, de paso:** `/v1/login`, `/v1/login/callback` y `/v1/logout` aceptaban
  `redirect=//otro.host` (redirect abierto, protocolo relativo). Ahora dan 422.

- **Addendum del 2026-10-03 (A1 a A11)**, tras repasar las decisiones con el dueño (2026-10-02 y
  2026-10-03):
  - A1 y A2: tras un rollback A exitoso solo queda el B, con aviso y el nombre del servidor
    escrito; tras un B exitoso, nada; "Simular actualización" y "Actualizar…" vuelven como botones
    independientes, con selector de build (R14, D1, R17);
  - A3 a A5: la tabla de roles en `docs/decisions.md` y un barrido que falla ante una ruta que
    escribe sin clasificar (R1, R8); un test prueba que mypy rechaza un método de operador sin
    `actor` (R6); una sola regla `LOCAL_PATH` para el `redirect` (R11);
  - A6 y A7: la búsqueda muestra autor, descargas y lado (R2); la auditoría filtra por fechas
    en UTC (R5);
  - A8 y A9: "Guardar decisiones" en lote y atómico (R12); `/jobs` como tabla con filtros y una
    página por trabajo (R13, R16);
  - A10: `scripts/visual-sweep.sh` (R10);
  - A11 (docs): MongoDB 8.0 en todos lados y su camino de subida por saltos en
    `migrar-mongo-a-volumen.md` (d-7985ee-83eb5b), la invariante de permisos denegados
    (d-7985ee-794d11) y este registro;
  - la ronda de la revisión final: los dos diálogos de actualización real avisan cuando hay un
    rollback pendiente que se va a perder (R17); los dos caminos mandan el modpack para el que se
    planearon y la ruta da 409 si el servidor cambió de modpack (la simulación guarda `bundle_id`
    en su plan); tras un A exitoso, pedir A da el 409 del servicio y una página vieja que pide B
    sin el campo del nombre recibe "vuelve a cargar la página"; el bloque de rollback dice cuándo
    y por qué falló el último intento (A o B, `RollbackState.last_failed`); el runbook de MongoDB
    tiene un camino para 4.4 o menos; y la tabla de roles, el roadmap y `AGENTS.md` dicen lo que
    hace el código.

- **Lote siguiente del 2026-10-03 (N1 a N7)**, i-7985ee-e69c9d:
  - N1: el barrido visual más firme, y un trabajo sin `payload.dry_run` cuenta como simulación en
    todos lados (falla cerrado; **el dueño debe confirmar que ningún trabajo real de producción
    carece de esa clave**);
  - N2: el detalle de un modpack muestra los archivos locales y edita canales y pins (un pin a
    una versión ajena o un id con `.` o `$` da 422 sin escribir);
  - N3: la vista previa de lo que cambia, con changelogs en texto plano (d-7985ee-d88e90), en la
    comparación de builds, y los errores de Modrinth o Pterodactyl como avisos y no como 500;
  - N4: la misma vista previa en la simulación y en el diálogo "Actualizar…";
  - N5: la revisión del build recomendado con `version_files/update`, que ignora los pins
    (d-7985ee-cadd66), guardada como `UpdateCheck` y avisada por Discord;
  - N6: la programación diaria con `UPDATE_CHECK_AT` y `UPDATE_CHECK_TZ` (d-7985ee-2e2f58), la
    página `/modpacks/{id}/actualizaciones`, la insignia del tablero y "crear build";
  - N7: decisiones, roadmap, plan, runbook y README;
  - la ronda de la revisión final (2026-10-04): un `UPDATE_CHECK_AT` sin comillas impide arrancar
    en vez de apagar la programación (N-R5: `load_env` sigue ignorando lo que no es texto, ahora
    con un aviso que nombra la clave y nunca el valor); la vista previa del diálogo "Actualizar…"
    se carga al abrirlo (`intersect once`) y no con cada visita, y sin escaneo o sin build es una
    nota en el diálogo (200), no un aviso; cualquier falla de una revisión guarda un
    `UpdateCheck` con su error; y `references.md`, los runbooks (pasos a–g como párrafos, el
    chequeo de `payload.dry_run` antes de desplegar, la revisión manual que cuenta como la del
    día) y el README.
- **Documentos del lote:** [diseño](../../docs/superpowers/specs/2026-10-03-panel-web-next-design.md)
  y [plan](../../docs/superpowers/plans/2026-10-03-panel-web-next.md).
- **Documentos de la sesión:** [diseño](../../docs/superpowers/specs/2026-10-02-panel-web-design.md),
  [plan del panel](../../docs/superpowers/plans/2026-10-02-panel-web.md) y
  [plan del addendum](../../docs/superpowers/plans/2026-10-03-panel-web-addendum.md).

**Estado final (2026-10-04).** Todo está en `main` y empujado a `origin` (8b5e2ca..ae17269, 73
commits). CI de ese push: el job `image` pasa (primera imagen x64 construida en CI: la app
importa, los assets del panel están y el compose valida); el job `gate` falla solo en el audit
`env_template`, porque falta `OIDC_OPERATOR_GROUPS` en `.env.default` (los permisos del dueño
bloquean a los agentes en ese archivo: la línea la agrega él).

**Entorno.** El Mac del dueño corre contenedores con podman 6.1.3 (Homebrew, VM
`podman-machine-default`) y `/opt/homebrew/bin/docker` es un enlace a podman, por elección suya.
`./scripts/test-mongo.sh`, las construcciones de imagen y `docker compose config` funcionan en
local (arm64 aquí, x64 en CI). Antes, los tests corrían contra un tarball descartable de MongoDB 8.0.

**Proceso.** El trabajo fue tres planes (el panel; el addendum con las decisiones del recorrido
del dueño, 18 dictámenes ya en `docs/decisions.md` y en los planes; y el lote siguiente a–d),
cada uno por tareas con un subagente implementador nuevo, revisión de la tarea, rondas de
correcciones y una revisión de toda la rama. Las revisiones encontraron, entre otras cosas: el
redirect abierto de protocolo relativo, el rollback B inalcanzable tras un A fallido, un rollback
ya hecho que se volvía a ofrecer, `UPDATE_CHECK_AT` sin comillas que apagaba la programación (YAML
base 60), la vista previa ansiosa de 10 s, ids de versión opacos y `Sec-Fetch-Site: none` aceptado.

Cuenta por plan (aproximada: los libros de cada plan se borraron, son cifras del controlador):

| Plan | Tareas | Rondas de corrección | Revisión de toda la rama |
|---|---|---|---|
| 1 (el panel) | 12 + un arreglo visual + ola final | en unas 4 a 6 tareas | 2 a 3 defectos Important entre tareas |
| Addendum | 11 + ola final | en unas 4 a 6 tareas | 2 a 3 defectos Important entre tareas |
| Lote siguiente (a–d) | 7 + ola final | en unas 4 a 6 tareas | 2 a 3 defectos Important entre tareas |

Los 18 dictámenes del controlador se revisaron con el dueño después del plan 1 y la mayoría
cambió: de ahí las 11 tareas del addendum.

**Por qué.** El dueño pidió un panel web para operar sin llamar a la API a mano, con dos niveles
de permiso y un registro de quién hizo qué sobre servidores reales. Cada decisión se registró en
`docs/decisions.md`; el roadmap cierra i-7985ee-50d411 y corrige tres entradas que habían quedado
desactualizadas (logs con `request_id`, trabajos que escriben, avisos de la fase 3).

**Arquitectura.** ✅ Cumple: los routers del panel solo inyectan `*Service`; la baseline de capas
sigue vacía. `AGENTS.md` suma dos invariantes (roles en los servicios, mismo origen y CSP).

**Qué salió mal en el camino.** Cada punto lo encontró una revisión, no el primer intento.
- `Sec-Fetch-Site: none` (una navegación del usuario) pasaba el chequeo de origen, y el 403 no
  llevaba la CSP. Ahora solo `same-origin` pasa en una petición insegura.
- La paginación de la auditoría por fecha se saltaba los eventos del mismo milisegundo. Ahora
  el cursor es el id del último evento y el orden desempata por `_id`.
- Un 422 de validación llegaba al panel como JSON con la entrada del usuario dentro de un aviso;
  ahora todo error del panel se muestra como aviso.
- Las decisiones sobre qué va al servidor se guardaban leyendo y reescribiendo el documento, y
  una decisión concurrente se perdía; ahora es un `$set` atómico por proyecto.
- Una actualización real no estaba atada a la simulación: podía instalar un build no previsualizado.
  Ahora lleva el build fijado al confirmar (desde el addendum, d-7985ee-aca3e8, por dos caminos:
  directo o desde una simulación terminada), y el modpack para el que se planeó.
- Un rollback se aceptaba sobre una simulación o sobre una actualización ya superada, y la
  búsqueda del último trabajo miraba solo los 50 más recientes. El servicio lo rechaza.
- El redirect abierto, ya existente, apareció al revisar el login. Un primer intento con
  *lookahead* en el patrón rompió el arranque: el motor de regex de pydantic no lo admite.
- La revisión visual (un Chromium sin interfaz, 56 capturas: operador en claro y oscuro a 360,
  768 y 1280 px, y editor en claro a 1280, por 8 páginas) encontró 3 problemas, corregidos en
  23bfbae y con 0 en la segunda pasada: a 360 px los hijos de las grillas desbordaban la página;
  el ítem activo de la navegación quedaba fuera de la pantalla; y un servidor cuyo modpack no
  tiene builds mostraba el error del servicio en inglés.
- La revisión final de la rama encontró, y se corrigió:
  - el rechazo de rollbacks superados era demasiado ancho. Un rollback A fallido pasaba a
    ser "lo último" y el B de la misma actualización se rechazaba: el servidor quedaba parado sin
    salida. Y una actualización real rechazada en el preflight, que no escribió nada, dejaba sin
    rollback a una anterior que sí escribió. Ahora solo supera una actualización real cuyo
    checkpoint llegó a `backup` o `swap`; un rollback no supera a la que deshace, y el panel lo
    sigue hasta ella. Un índice `key` + `created_at` sirve esa búsqueda;
  - un `name` vacío del IdP daba 401 y un bucle de login en el panel. La identidad es `iss` +
    `sub`; el nombre visible cae al email y luego al `sub`;
  - un 4xx sin aviso (un 429 o 400 de Modrinth o Pterodactyl) no mostraba nada en el panel;
    ahora sale el aviso genérico;
  - el chequeo de mismo origen deja a los orígenes de `CORS_ALLOW_ORIGINS` sin poder escribir.
    Se documentó (d-7985ee-491c83, `AGENTS.md`, el runbook): solo leen;
  - ningún test fallaba si se borraba un `add_middleware` de `main.py`; ahora uno lo prueba
    sobre `main.app`.
- **En el addendum:**
  - A6 rotuló `client_or_server` como "cliente y servidor", y Modrinth dice que funciona en uno
    de los dos: lo encontró la revisión; ahora "cliente o servidor", y `client_only` con
    `client_only_server_optional` como "cliente";
  - A8 listaba cada mod sin decidir dos veces (un formulario por mod y el del lote, porque los
    formularios no se anidan); ahora solo el del lote;
  - la shell de un subagente se colgó esperando stdin; los comandos se corren sin bloquear en la entrada;
  - la primera versión del camino de subida de MongoDB estaba en el paso 1, antes de migrar, y habría
    arrancado un `mongodb-data` vacío en un servidor viejo; no paraba `mongod` limpiamente (5.0 lo
    exige); y dijo que las páginas de 5.0 y 6.0 no se podían leer, cuando sí lo son en su versión
    `.md`. Ahora se migra primero a la versión actual y la subida es una sección aparte, con las
    cuatro páginas (8.0, 7.0, 6.0, 5.0) citadas;
  - ese camino seguía roto con una base en 4.4 o menos, y nadie lo vio hasta la revisión final: el
    PyMongo del repositorio (4.18.2) no conecta por debajo de 4.4, `mongo:4.4` no trae `mongosh`
    y el healthcheck del compose lo usa, así que la API nunca habría arrancado. Ahora, con 4.4 o
    menos, se sube a 5.0 en el contenedor viejo antes de migrar, con los comandos del shell `mongo`
    y comprobando AVX primero;
  - R17 (una actualización real posterior deja sin rollback a la fallida) se aplicaba en silencio:
    el operador podía perder el rollback A/B sin que ningún diálogo se lo dijera. Ahora los dos
    diálogos lo avisan antes de confirmar.

- **En el lote siguiente:**
  - N4: la vista previa se armaba al cargar la página (hasta 10 s) y mostraba ids de versión sin
    sentido; ahora se carga al pedirla y muestra los números de versión;
  - N5: el aviso de Discord nombraba los mods por id de proyecto y `latest_many` leía todo el
    historial; ahora usa los títulos del catálogo y una agregación;
  - N3 y N2: un error de Modrinth o Pterodactyl llegaba como un 500 genérico; ahora es un aviso;
  - dos shells o procesos de subagentes quedaron corriendo en lotes anteriores; se revisó y se
    limpió cada vez.
- **En la ronda final del lote siguiente:**
  - PyYAML (YAML 1.1) lee `UPDATE_CHECK_AT: 10:30` como 630 (base 60) y `load_env` lo ignoraba
    sin decir nada: la programación quedaba apagada, contra d-7985ee-2e2f58. `04:30` o `09:30`
    sin comillas sí quedan como texto, por el cero inicial, y por eso los tests no lo vieron;
  - la vista previa del diálogo usaba `hx-trigger="load"` dentro del `<dialog>` cerrado: cada
    visita de un operador pedía hasta 10 s de changelogs a Modrinth, y un servidor sin escanear
    ponía un 409 en los avisos sin que nadie hubiera pulsado nada. Al cambiarlo apareció otro
    error, ya existente desde N4: `app.js` cerraba el diálogo al terminar **cualquier** petición
    de un elemento dentro de él, así que cambiar el build lo cerraba (y abrirlo, con la carga
    perezosa, lo habría cerrado al instante). Ahora solo lo cierra la respuesta de su propio
    formulario; el barrido visual abre el diálogo y falla si se cierra o si la vista previa carga
    con la página (comprobado contra el `app.js` anterior: 6 problemas);
  - el registro decía que un aviso que falla dejaba al programador reencolando cada 5 minutos:
    no era así, `Notifier.notify` se traga sus errores. Lo que sí lo provocaba era MongoDB
    fallando al leer el último aviso, al guardar el resultado o al releer el modpack en la rama
    de error; ahora cualquier falla del paso guarda la revisión con su error.

**Qué quedó sin hacer.**
- ~~El detalle de un modpack no muestra los archivos locales, los canales ni los pins~~: hecho en
  el lote siguiente (N2).
- De ese lote: ~~una falla tras calcular una revisión no deja ningún `UpdateCheck`~~ (hecho en la
  ronda final; no era el aviso sino MongoDB); una revisión manual antes de `UPDATE_CHECK_AT`
  cuenta como la de hoy (ahora documentado); las revisiones previas no tienen ids de versión (sin
  changelogs); un pin sobrevive a quitar el proyecto.
- `load_env` (`src/library/utils/__init__.py`) escribe en el log, en nivel `info`, el **valor** de
  una clave que ya estaba en el entorno (y todo el archivo con `verbose`): puede ser un secreto
  (Low, ya existía). Debe registrar solo claves.
- Un `UPDATE_CHECK_AT:` vacío o nulo debería significar "apagado" y hoy falla con el mensaje de
  "ponlo entre comillas".
- Una consulta de títulos de Modrinth por fragmento y la espera secuencial del tablero (menores).
- Los índices por `target_id` y por acción en la auditoría (se escanea la colección; chica).
- **Del dueño, en orden:** agregar `OIDC_OPERATOR_GROUPS: ""` a `.env.default` (pone CI en
  verde); antes de desplegar, correr el conteo `dry_run` de `docs/runbooks/desplegar-panel.md`
  (debe dar 0); desplegar con los runbooks; crear el grupo de operadores en authentik; un login real
  de editor y de operador; la primera actualización real en el servidor de prueba.
- **Del cierre de evaluación y cosecha (2026-10-04):** el PATCH de `/v1` de un bundle
  (`ModpackDatabase.bundle_update` → `Document.set`) reemplaza enteros los mapas anidados `pins`,
  `replacements` y `server_decisions`: mandar un pin borra los demás (medido; ningún test lo
  cubre). Arreglo: actualizaciones por clave, como `pin_set` y `decision_set`. Además, el
  docstring de `src/library/scan.py` exagera el punto ciego de la caché (un restore con otra hora
  se nota; solo se pierde una edición con el mismo tamaño y la misma hora al milisegundo): solo
  una nota, el código no se tocó.
- **Limpieza:** las imágenes de podman bajadas (mongo 4.4, 5.0, 6.0, 8.0 y `modpack-creator-api:ci`)
  se pueden borrar, y `.superpowers/sdd/2026-10-02-panel-web/` (fuera de git).

**No verificado.**
- **La imagen, en parte.** Verificado: arm64 en local (2026-10-03) y x64 en CI (2026-10-04): la
  app importa, los assets están, `node` no está en la imagen final y el compose valida y se niega
  a arrancar sin `MONGO_VERSION` o `FORWARDED_ALLOW_IPS`. Sin verificar: el despliegue real.
- **Un login real** de editor y de operador con authentik, y el claim `groups`.
- **Pterodactyl y Modrinth reales:** nada de esto toca un servidor de juego; el flujo de
  actualización se probó con un panel simulado.
- **La versión de MongoDB de CI** (`mongo:8.0`): ahora es la versión de todos lados (d-7985ee-83eb5b); falta saber si producción ya la tiene.
- **`.env.default`:** `OIDC_OPERATOR_GROUPS: ""` lo agrega el dueño (el agente no lo lee ni lo
  edita); hasta entonces el audit `env_template` falla.
- **Del addendum:** un login real con authentik de editor y de operador; Pterodactyl y Modrinth
  reales; los datos de producción (y por tanto qué versión de MongoDB corre: los saltos del
  runbook no se probaron); y el camino del Chromium incluido de la revisión visual
  (`PLAYWRIGHT_CHROMIUM` apunta a uno ya instalado).
- **De la ronda final:** el aviso de rollback pendiente no aparece en la revisión visual (63
  capturas, 0 problemas), que no abre diálogos; solo lo prueban los tests de HTML. El camino de
  4.4 se ensayó con podman en arm64 (4.4.30 → 5.0.33 sobre el mismo volumen, FCV `"5.0"`, datos
  intactos): el chequeo de AVX en x86, el compose viejo real y los saltos por debajo de 4.4 no.
- **Del lote siguiente:** nunca se corrió contra Modrinth ni Pterodactyl reales (la vista previa, la
  revisión y el programador se probaron con clientes simulados y MongoDB real); la imagen x64 y el
  despliegue (índice nuevo de `UpdateCheck`, `UPDATE_CHECK_AT` y `UPDATE_CHECK_TZ` en el servidor);
  y qué trabajos de producción carecen de `payload.dry_run` (ahora se ven como simulación). La
  imagen arm64 sí carga `America/Santiago`.
- **De la ronda final del lote:** que `intersect once` cargue la vista previa al abrir el diálogo
  se comprobó solo en el Chromium sin interfaz del barrido (90 capturas, 0 problemas), no en
  Firefox ni Safari; el `mongosh` del chequeo de `payload.dry_run` se probó contra un MongoDB 8.0
  descartable, no contra producción; y qué `.env.yaml` de producción tiene valores sin comillas
  (desde ahora cada uno deja un aviso en el log al arrancar).
- **Del cierre:** Pterodactyl y Modrinth reales, un login OIDC real, los datos de producción y
  `intersect once` en Firefox y Safari.

## 2026-10-01 · s-7985ee-2ab23b — Fase 3: importar servidores y actualizar con backup, verificación y rollback a pedido

**Qué.**
- **Las decisiones del dueño, preguntadas antes de escribir:**
  - a pedido y sin aviso: `save-all` y parar (d-7985ee-c2adc8);
  - la API baja, verifica y sube los jars (d-7985ee-a13b33);
  - ningún rollback automático (d-7985ee-5445a0);
  - 5 minutos estable, y abortar sin cupo de backups (d-7985ee-5cc6ee);
  - los jars ajenos se cargan con una importación aparte (d-7985ee-c8a15d). La respuesta inicial
    del dueño ("cargarlos en la plataforma") se precisó con tres preguntas más.
- **i-7985ee-17745b, importar.**
  - Archivos locales del bundle, congelados en el build y fuera del `.mrpack`.
  - Lectura de metadata como dato (`src/library/jarmeta.py`).
  - Candidatos por slug y búsqueda (`ModrinthAPI.search`).
  - Confirmar reclama el jar; marcarlo lo deja como local.
- **Cliente de Pterodactyl para operar:** estado, recursos, backups, rename y create-folder,
  contrastados con el código del panel. `_write` admite PUT.
- **i-7985ee-495708, actualizar.**
  - `UpdateService` con simulación por defecto, pasos con checkpoint, `JobFailed` para no
    reintentar, y rollback A o B solo a pedido.
  - Runbook `docs/runbooks/primera-actualizacion-real.md`.
- `AGENTS.md` ya no dice que hay 14 inyecciones en la baseline de capas.

**Por qué.** El dueño pidió seguir con la fase siguiente y preguntarle lo necesario.

**Qué salió mal en el camino.**
- La regla `ungated_writes` daba un falso positivo con `_write(method=METHOD.PUT)`: era textual.
  Ahora mira las llamadas a `_request`.
- Una descarga mala creaba las carpetas de staging antes de verificar; ahora se crean después del
  primer jar verificado.
- Una lambda con argumentos por defecto no pasó mypy; se cambió por una función.
- **Hallazgo de la prueba por HTTP:** aiohttp codificaba el nombre del archivo en el multipart
  (`+` → `%2B`), y Wings lo guarda tal cual. Los contratos no lo veían porque solo comprobaban el
  campo. Corregido y fijado.

**Arquitectura.** ✅ Cumple: routers → `UpdateService` y `PterodactylService`. Toda escritura pasa
por `_write`, y la simulación pasa por `Ops`.

**Qué quedó sin hacer.**
- La primera ejecución real.
- La limpieza automática de `mods.old/` y `mods.staging/` (a mano, a propósito).
- Avisar a los jugadores (el dueño eligió no hacerlo).

**No verificado.**
- **Pterodactyl y Wings reales**: tiempos de parada y backup, permisos del usuario de servicio, y
  el comportamiento de `create-folder` con una carpeta que ya existe.
- Discord real y el login OIDC.

**Aprendizaje, propuesto al bundle:** un contrato que solo mira los campos no ve un valor
codificado en el camino. Hay que comprobar el valor exacto que recibe el otro lado, con caracteres
que un codificador toca.

**Medido.**
- Gate con Mongo: 233 pasan.
- Errores plantados que se vieron fallar:
  - en la actualización, el backup sin buscarlo por nombre y no verificar los 5 minutos
    (4 tests);
  - en la importación, aceptar candidatos sin confirmar.
- **Con datos reales:** la metadata de 6 jars reales, y la búsqueda de candidatos contra Modrinth.

## 2026-10-01 · s-7985ee-a7d531 — Avisos por Discord, programador de escaneos, servidor-bundle y httpx2

**Qué.**
- **i-7985ee-b0264e.** Un servidor se asocia a su bundle; comparar no exige nombrarlo, y
  refrescar desde el panel conserva la asociación.
- **i-7985ee-0ec295, el programador.** `SCAN_EVERY_HOURS` y `SCAN_FULL_EVERY_DAYS`: escaneos
  periódicos y uno completo cada cierto tiempo.
- **i-7985ee-34933b.**
  - `Notifier` entrega por la cola de trabajos (d-7985ee-de1061), con `RetryLater` para el
    `Retry-After` de Discord.
  - Avisa los builds nuevos, y el desvío o la vuelta de un servidor a su build, solo cuando
    cambia.
- **`httpx2==2.13.1`** en lugar de `httpx`, para quitar la integración deprecada de Authlib.

**Por qué.** El dueño pidió seguir con el roadmap, cerrando después con documentación y commits.

**Qué salió mal en el camino.**
- `drift_message` en el módulo de avisos formaba un ciclo de imports con el servicio de
  Pterodactyl; pasó a `src/library/scan.py`.
- El tipo del notificador en `JobService` se resolvió con un `Protocol`: el test de cableado de
  DI no podía resolver una referencia adelantada.
- El test de DI exigía tantos argumentos como parámetros. Ahora exige los obligatorios y acepta
  opcionales sin inyectar.
- El script de humo usaba `httpx`, que ya no está instalado.
- El primer webhook falso no mostraba su salida en `podman logs`; escribió a un archivo.

**Arquitectura.** ✅ Cumple: router → servicio; el aviso nunca falla lo que avisa.

**Qué quedó sin hacer.** Ver `docs/roadmap.md`, §Dónde estamos: lo que queda espera al dueño.

**No verificado.**
- Discord real.
- El login con authentik sobre `httpx2`: Authlib lo usa, pero el flujo OIDC no se ejecutó.
- Pterodactyl real.

**Medido.**
- Gate con Mongo: 191 pasan, sin advertencias.
- **Prueba de humo en la imagen:** un build por HTTP congeló `fabric-loader` 0.19.5 desde la meta
  real, sirvió su `.mrpack` y avisó a un webhook falso con el texto esperado y las menciones
  apagadas. El token no aparece en los logs.

## 2026-10-01 · s-7985ee-bcd5de — Exportar mrpack, logs JSON, trabajos durables y robustez del escaneo

**Qué.** Desarrollo sin decisiones nuevas del dueño, después del cierre (s-7985ee-9b0d95).
- **i-7985ee-c1de10.** Se borró `Settings.collection`, con un test que fija los nombres reales de
  las colecciones.
- **Escaneo.**
  - Las lecturas de Pterodactyl esperan `Retry-After` ante un 429, con tope y 4 intentos; las
    escrituras no.
  - Un shader para Iris en un pack sin Iris sugiere Iris.
- **i-7985ee-7352b3.** Exportar un build como `.mrpack`, validado contra la spec. El build congela
  la versión del loader, tomada de las metas de Fabric y Quilt o fijada por el admin
  (d-7985ee-53a818).
- **i-7985ee-90f944.** Logs JSON con `request_id` por request, advertencias de Python como JSON y
  sin el access log de uvicorn.
- **i-7985ee-0ec295.** Cola de trabajos propia con leases (d-7985ee-536c67), un worker en el
  lifespan y un primer tipo de solo lectura (`server_scan`).

**Por qué.** El dueño pidió seguir con el desarrollo que no necesita sus pruebas.

**Qué salió mal en el camino.**
- Un regex para borrar `Settings` dejó clases vacías; se arreglaron a mano antes de correr nada.
- La prueba de humo de la imagen mostró cuatro cosas que los tests no veían:
  - el access log de uvicorn reaparecía (`configure_logging` lo reactivaba);
  - un campo `color_message`;
  - la advertencia de Authlib como texto suelto;
  - tres reintentos seguidos contra un panel caído.

  Las cuatro se corrigieron con test.
- El test de advertencias pasaba solo y fallaba en la suite: pytest captura las advertencias, y
  `captureWarnings(True)` no hace nada la segunda vez.

**Arquitectura.** ✅ Cumple: el router de trabajos inyecta `JobService`, y las funciones del
`.mrpack` son puras.

**Qué quedó sin hacer.**
- El scheduler de escaneos periódicos.
- packwiz y las `overrides/` del `.mrpack`.
- `httpx2`.

**No verificado.** Pterodactyl real, que los jugadores importen el `.mrpack` en un launcher y el
login OIDC.

**Medido.**
- Gate con Mongo: 179 pasan.
- **Con datos reales:** un `.mrpack` de 5 archivos, cada uno bajado de su URL, coincidió en
  hashes y tamaño.
- **Prueba de humo de la imagen:** 202 y luego 409 al encolar, `request_id` del proxy en el log,
  0 líneas fuera de JSON, 0 del access log, y unos 30 s de espera entre intentos.
- Los errores plantados en trabajos (3), colecciones (1) y resolución se vieron fallar.

## 2026-10-01 · s-7985ee-9b0d95 — Cierre de la sesión de desarrollo del 2026-10-01: fases 0 a 2 en código

Resumen de la jornada, de S4 a la fase 2. El detalle de cada parte está en las entradas de abajo;
aquí, lo que hace falta para retomar.

**Qué quedó hecho** (en `main`, sin desplegar):
- **Fase 0, código:**
  - S4: tests contra MongoDB real en CI y bloqueo de red;
  - S5: los updates escriben y caben muchas versiones por proyecto;
  - la caché de usuarios;
  - S6: contratos y escrituras sin reintentos;
  - S7: `/health`;
  - deuda: capas en 0 y una `ClientSession` por cliente.
- **Fase 1:**
  - el modelo nuevo de Modrinth;
  - la resolución pura con su servicio;
  - el contenido con origen;
  - los builds inmutables con punteros.
- **Fase 2, en solo lectura:**
  - la puerta de escritura a Pterodactyl;
  - el escaneo por hash;
  - la comparación con un build.
- 133 tests contra Mongo real; el audit con 0 fallos, 0 avisos y las baselines vacías.

**Decisiones del dueño en la jornada:**
- d-7985ee-86fffa: reemplazos globales con reglas por pack;
- d-7985ee-20b06b: los conflictos se reportan, sin backtracking;
- d-7985ee-f0c7a6: en el servidor, lo ambiguo lo decide el admin;
- d-7985ee-30fe05: los bundles viejos no se migran;
- d-7985ee-b15dd4: lo del admin pasa por la resolución;
- d-7985ee-9170b8: shaders y resourcepacks solo en el cliente.

Además, no desplegar y seguir con el desarrollo. Las decisiones técnicas tomadas aquí son
d-7985ee-1a2f53, d-7985ee-1a20c1, d-7985ee-5739fc y d-7985ee-0277b6.

**Hallazgos** (defectos que ya existían, todos corregidos con test):
- **Usuarios:** la migración de usuarios de S3 daba 500 a todo usuario guardado antes.
- **Pterodactyl:** `power`, `command` y `delete` fallaban en cada 204 real y, con `backoff`,
  repetían la acción. `upload` y `download` no seguían el flujo de URL firmada de Wings.
- **Modrinth:**
  - los lotes por hash usaban rutas y cuerpos equivocados;
  - `project_versions` mandaba siempre `featured`, así que nunca traía todas las versiones;
  - con el enum de loaders, ningún shader, resourcepack ni datapack entraba al catálogo;
  - `project_version_update_*` y `project_dependencies` no habían funcionado nunca.
- **Datos:**
  - los updates guardaban el documento viejo;
  - el índice `unique` en `ModrinthVersion.project_id` impedía guardar más de una versión por
    proyecto, y quitarlo sin migrar habría impedido arrancar;
  - `GET /modpack/{id}/project` devolvía siempre `[]`;
  - un proyecto de modpack guardaba una copia y no una referencia;
  - `Settings.collection` no es de Beanie 2 (queda planeado, i-7985ee-c1de10);
  - MongoDB devuelve las fechas sin zona y en milisegundos.
- **Cliente HTTP:** los handlers compartidos mezclaban respuestas concurrentes.
- **API:** los ids inexistentes o inválidos daban 500.

**Aprendizajes** (tres, como propuestas al bundle: p-881066296a, p-9e406b711e, p-12c61d894f):
- Una clave de caché con fecha no coincide después de pasar por la base: hay que normalizar en el
  modelo y probar contra la base real.
- Un `xfail` estricto sin `raises` cuenta cualquier error como el esperado.
- Leer JSON de un 204 y reintentar repite una escritura.
- Además, los tests de este repositorio fallaron casi siempre al importar. Para verlos fallar por
  su razón se plantó un error en cada arreglo importante: es la única forma de saber que muerden.

**Pendientes, en orden** (detalle en `docs/roadmap.md`, §Dónde estamos):
1. Del dueño:
   - desplegar con el runbook de Mongo;
   - S7 operación (usuario de servicio y revocar la clave vieja);
   - un login real fuera del grupo;
   - los puntos de control de las fases 1 y 2 en solo lectura;
   - decidir packwiz, y después la fase 3.
2. Código sin decisiones:
   - el primer escaneo por `files/compress` y la pausa ante un 429;
   - el chequeo de Iris u OptiFine para shaders;
   - logs JSON;
   - `Settings.collection`;
   - `httpx2`;
   - igualar la versión de Mongo de CI con la de producción.
3. El hito de la fase 0 (`state-review` y harvest) se cierra cuando el dueño despliegue.

**No verificado.**
- Pterodactyl y Wings reales.
- El login OIDC real: las pruebas de humo firmaron una cookie de sesión con un secreto de prueba.
- Los datos de producción.

**Medido.**
- Gate con Mongo: 133 pasan. Sin Mongo: 87 pasan y 46 se saltan.
- Contra Modrinth real: una resolución de 6 proyectos y la identificación por hash de 3 jars
  reales.
- Pruebas de humo de la imagen con podman: salud, todo el flujo de bundle a build por HTTP, el
  índice viejo migrado al arrancar y los índices nuevos creados.

## 2026-10-01 · s-7985ee-6d9eea — Fase 2: solo lectura de verdad, escaneo por hash y comparación con un build

**Qué.**
- **La puerta de escritura (d-7985ee-5739fc).** Toda escritura a Pterodactyl pasa por `_write`,
  y `PTERODACTYL_WRITES` (apagado por defecto, en `.env.default`) la rechaza con 403 antes de
  enviar. El audit `ungated_writes` falla con una escritura fuera de la puerta.
- **Escaneo (i-7985ee-43df2f, opción 1).**
  - `src/library/scan.py`, puro: el plan de escaneo con caché por ruta, tamaño y fecha, y la
    comparación.
  - `PterodactylService.scan`: lista, baja solo lo que cambió, hashea sin cargar y con límite, e
    identifica por sha1. Guarda un `ServerScan`.
- **Comparación (i-7985ee-4364a3):** `GET /v1/server/{id}/compare`.
  `PterodactylVersion`/`PterodactylModpack` se reemplazaron sin migrar (d-7985ee-0277b6).

**Por qué.** Era la fase 2. Se invirtió el orden del plan: el manifiesto en el servidor
(i-7985ee-5c7506) es una escritura, y el punto de control de la fase es en solo lectura.

**Qué salió mal en el camino.**
- El segundo escaneo bajaba todo: MongoDB devuelve las fechas sin zona y en milisegundos. Lo
  encontró el test del servicio contra Mongo real; un doble en memoria no lo habría visto. Se
  normaliza en el modelo, con su test puro.
- Con jars reales se vio que un desconocido se volvía a bajar en cada escaneo. Ahora conserva su
  hash y solo se vuelve a preguntar.

**Arquitectura.** ✅ Cumple: router → servicio → base y cliente, y las funciones de escaneo son
puras.

**Qué quedó sin hacer.**
- Escribir el manifiesto en el servidor (fase 3, con la puerta abierta).
- El primer escaneo por `files/compress` y la pausa ante un 429.
- **El panel real:** no se puede probar desde aquí.

**No verificado.** Pterodactyl y Wings reales: el listado, las URLs firmadas y los límites de
tasa siguen la especificación y los contratos, pero no se ejecutaron contra un panel.

**Medido.**
- Gate con Mongo: 133 pasan, audit 0 fallos.
- La puerta se vio fallar con una escritura plantada. La comprobación de sha1 se vio fallar
  quitándola.
- **Con Modrinth real:** tres jars reales del CDN se identificaron con su versión exacta; uno
  hecho a mano quedó como desconocido.

## 2026-10-01 · s-7985ee-1ed73f — Fase 1: tipos de proyecto, contenido con origen y builds inmutables

**Qué.**
- El dueño decidió tres cosas:
  - no migrar bundles viejos (d-7985ee-30fe05);
  - lo del admin pasa por la resolución (d-7985ee-b15dd4);
  - shaders y resourcepacks solo en el cliente, datapacks también en el servidor
    (d-7985ee-9170b8).
- **Tipos (i-7985ee-60822c).** Loaders por tipo en `Target`, lado servidor por tipo y
  `needs_types`, para no traer todas las versiones de un proyecto cuando solo falta su tipo.
- **Contenido (i-7985ee-063a3a, i-7985ee-a17c2c).**
  - `ModpackBundle.projects` (`project_id` y origen), con actualizaciones atómicas;
  - `pack_replace` conserva lo del admin con un update de pipeline;
  - `ModpackProject` y sus `Link` desaparecieron.
- **Builds (i-7985ee-098611).** `ModpackBuild` inmutable, número único por bundle, `latest` con
  `$max` y `recommended` a mano. 409 si el lock no se puede congelar.
- 404 en vez de 500 para bundles y servidores inexistentes o con id inválido.
  `ModpackBundleCreate` para no fijar punteros ni contenido al crear.
- El script de fixtures graba también el shader y Fabric API para 26.3: sin filtro, Fabric API
  solo traía snapshots.

**Por qué.** Los ítems restantes de la fase 1, con las decisiones del dueño.

**Qué salió mal en el camino.**
- Los tests nuevos fallaron al importar, y otra vez se plantaron errores para verlos fallar por
  su razón: la carrera al agregar y el reemplazo que pierde lo del admin.
- mypy no aceptó `-ModpackBuild.number` como orden; se usa `SortDirection`.

**Arquitectura.** ✅ Cumple. El modelo importa `Lock` y `ResolveRequest` de
`src/library/resolution.py`, que es puro.

**Qué quedó sin hacer.**
- No se comprueba que el pack tenga Iris u OptiFine para un shader.
- Exportar un build (`.mrpack` o packwiz) es de la fase 2 (opción 2 de i-7985ee-43df2f).
- El punto de control de la fase 1 es del dueño.

**No verificado.** Datos de producción; el login OIDC (la prueba de humo firmó una cookie de
sesión con un secreto de prueba).

**Medido.**
- Gate con Mongo: 105 pasan.
- Humo HTTP sobre la imagen, contra Modrinth real: todo el flujo de bundle → build → recomendar,
  con sus 404 y 409, sin errores en los logs.

## 2026-10-01 · s-7985ee-f4f2fb — 7f735a: la resolución de un modpack, pura, con el servicio y contra Modrinth real

**Qué.**
- El dueño decidió tres cosas (d-7985ee-86fffa, d-7985ee-20b06b, d-7985ee-f0c7a6):
  - reemplazos globales con reglas por pack;
  - reportar conflictos, sin backtracking;
  - en el servidor, los mods ambiguos los decide el admin.
- `src/library/resolution.py`, una función pura con el algoritmo de la investigación §5:
  - canales, pins, exactas y libres, reemplazos;
  - opcionales sugeridas, embebidas, incompatibles;
  - versiones que Modrinth ya no tiene;
  - Quilt acepta versiones de Fabric;
  - lado servidor.
- `ModpackService.resolve` trae de Modrinth lo que el lock pide, ronda por ronda, y lo guarda en
  el catálogo. `POST /v1/modpack/{id}/resolve` y las reglas globales en `/v1/replacement`
  (`ModpackReplacement`, registrado en `init_beanie`).

**Por qué.** Era el siguiente ítem de la fase 1 y el dueño tomó sus decisiones.

**Qué salió mal en el camino.**
- El primer test del servicio dio un lock vacío. El filtro por `isinstance` descartaba en
  silencio los proyectos, y detrás había dos defectos que ya existían:
  - `project_find(bundle_id)` nunca encontraba nada (`modpack.$id` después del `$lookup`);
  - `ModpackProject` guarda una copia del proyecto, no una referencia (i-7985ee-a17c2c).

  Ahora un proyecto ilegible es un problema en el lock, no una omisión.
- Un test de reemplazos habría pasado aunque la regla global ganara. Se reforzó exigiendo que no
  haya problemas.

**Hallazgos.**
- `project_versions` siempre mandaba `featured`, que filtra a solo destacadas o solo no
  destacadas. Leído en la spec antes de usarlo para resolver.
- Contra Modrinth real, el shader del pack sale `unresolvable` (i-7985ee-60822c).

**Arquitectura.** ✅ Cumple: la resolución no hace I/O; el servicio trae y guarda.

**Qué quedó sin hacer.**
- El lock no se guarda (i-7985ee-098611).
- Shaders (i-7985ee-60822c).
- El backtracking queda descartado por decisión.

**Medido.**
- Gate con Mongo: 88 pasan. Los tests de resolución se vieron fallar al importar, y además con
  dos errores plantados (4 fallos).
- **Contra Modrinth real**, Mongo descartable: 6 proyectos para 26.3 con fabric dieron 8 entradas
  y 1 problema (el shader). Iris → la Sodium `bAZQdGpg` exacta; Mod Menu → Fabric API.

## 2026-10-01 · s-7985ee-3ca4fe — Capas en cero, una ClientSession por cliente, y httpx explicado

**Qué.**
- **Capas (i-7985ee-2bbbfd).** Las 14 inyecciones de base o cliente en routers pasaron a
  métodos de servicio. `LAYERING_BASELINE` queda vacía. Antes se escribió
  `tests/test_routes_auth.py`: toda ruta de `/v1` sin sesión da 401.
- **Sesión HTTP (i-7985ee-57034c).** Una `ClientSession` por cliente, creada en el primer
  request y recreada si cambia el event loop. `close()`, y el lifespan cierra los tres clientes.
- **`httpx`.** No sobra: el cliente OIDC de Authlib para Starlette lo importa sin declararlo.
  Queda dicho en `requirements.txt`.

**Por qué.** Deuda de la fase 0 que no necesitaba decisiones del dueño, mientras la fase 1
espera las suyas.

**Qué salió mal en el camino.**
- El test de rutas no encontró ninguna al principio: en FastAPI 0.142 los routers incluidos
  son perezosos, como ya anotaba `test_startup`. Se enumeran con `openapi()`.
- Al desindentar el bloque de `_request` se usó un `if True:` provisional, que se quitó.

**Arquitectura.** ✅ Cumple, y la baseline de capas llegó a 0.

**Medido.** Gate con Mongo: 61 pasan. El test de rutas se vio fallar abriendo `/server/external`:
dio 500, porque el bloqueo de red cortó la llamada al panel. El de la sesión se vio fallar
forzando una sesión por request: el servidor vio tres conexiones.

## 2026-10-01 · s-7985ee-3dba25 — f4ba63: el catálogo sigue el modelo nuevo de Modrinth, con respuestas reales grabadas

**Qué.**
- **Respuestas reales grabadas.** `scripts/record_modrinth_fixtures.py` graba respuestas de
  Modrinth en `tests/fixtures/modrinth/`, más las versiones exactas que piden las grabadas:
  - cinco proyectos: cliente, ambos lados, servidor, uno con dependencias y un shader;
  - versiones de tres proyectos;
  - los primeros 200 tags de versión de juego.
- **Modelo:** `environment` (lista en el proyecto, valor en la versión) y los lados deprecados
  opcionales. Los loaders son `list[str]`, y `CustomEnum` lee como `unknown` un valor nuevo.
- **Versiones de juego:** `newest_first` y `ModrinthAPI.game_versions()`.
- **Dependencias por versión:** `version_dependencies` reemplaza a `project_dependencies` y a
  `/project/{id}/dependencies`.

**Por qué.** Es el primer ítem de la fase 1 y no necesitaba decisiones del dueño.

**Qué salió mal en el camino.**
- El primer grabado no traía la versión de Sodium que fija Iris, así que el script ahora graba
  también las dependencias.
- Los tests fallaron todos juntos al importar. Para verlos fallar por su razón, se quitó cada
  arreglo por separado: el `unknown` y los loaders abiertos.

**Hallazgo.** Con el enum de loaders, el catálogo no podía guardar ningún shader, resourcepack
ni datapack: el parseo fallaba.

**Arquitectura.** ✅ Cumple.

**Qué quedó sin hacer.** Qué `environment` va en un pack de servidor; es de i-7985ee-7f735a.

**No verificado.** Las respuestas son del 2026-10-01; Modrinth puede cambiar.

**Medido.** Gate con Mongo: 58 pasan.

## 2026-10-01 · s-7985ee-5853ff — S7 (código): health live y ready, y el healthcheck de la API

**Qué.** `/health/live` y `/health/ready` sin sesión (i-7985ee-90f944). El router pasa por
`HealthService` y este por `HealthDatabase.ping`, con 2 s de límite. El router se monta al
importar, no en el lifespan. El compose tiene un healthcheck de la API con `urllib`.

**Por qué.** Era la parte de código de S7. La parte de operación (usuario de servicio, clave
nueva, revocar la vieja) es del dueño, en el panel.

**Qué salió mal en el camino.** Un comentario decía que montarlo al importar servía si el arranque
era lento. Es falso: uvicorn no atiende requests hasta que termina el lifespan. Se corrigió.

**Arquitectura.** ✅ Cumple: router → servicio → base, y `test_di_wiring_matches_constructor_types`
cubre el proveedor nuevo.

**Qué quedó sin hacer.** Los logs JSON y el latido del worker. S7 de operación.

**Medido.** Gate con Mongo: 48 pasan. **Humo con podman sobre la imagen construida, con un Mongo:**
- `live` y `ready` dan 200, y `ready` da 503 con Mongo detenido;
- el comando del healthcheck sigue al estado real, y `/v1/users/me` sin sesión da 401;
- con el índice `unique` viejo en `ModrinthVersion.project_id`, la API arranca y lo reemplaza por
  uno no `unique`.

## 2026-10-01 · s-7985ee-639167 — S6: los clientes siguen el contrato y las escrituras no se reintentan

**Qué.**
- **Contratos (i-7985ee-fb00ca).** Se contrastaron con fuentes primarias: el `openapi.yaml` v2.7.0
  de Modrinth, leído con un parser y no resumido; `routes/api-client.php`, `FileController` y
  `FileUploadController` del panel; y el handler de subida de Wings. Nueve tests en
  `tests/test_api_contracts.py`, contra un servidor local que contesta 405 a toda ruta que la
  especificación no define.
  - Modrinth: los lotes por hash en plural, con arrays, leen un mapa hash → versión.
  - Pterodactyl: `command` y `files/delete` por POST con JSON. `upload` y `download` en dos pasos,
    con URL firmada de Wings, y sin la clave del panel en esa URL.
- **Un 204 ya no es un error:** `JsonResponse` no decodifica un cuerpo vacío.
- **Reintentos (i-7985ee-05c4a0).** Sin `backoff` en las escrituras de Pterodactyl.
  `retried_writes` es FALLO, con `IDEMPOTENT_POSTS` para los tres POST de Modrinth que solo leen.
  Una entrada vieja falla. Se borró el `backoff` muerto de `handle_errors`.
- **Handlers por request (i-7985ee-236b45).** `_request` copia los handlers que recibe.
  `MultiPartRequest` deja que aiohttp escriba el `Content-Type` con el boundary.

**Por qué.** Era S6. **El hallazgo grave:** `power`, `command` y `delete` fallaban en cada éxito
real, porque intentaban leer JSON de un 204. Y con `backoff` **repetían la acción**: un `restart`
real se habría mandado dos veces y reportado como error. Ningún endpoint los llamaba todavía.

**Qué salió mal en el camino.**
- El resumen automático de la especificación de Modrinth decía que el GET por hash devuelve un
  mapa. Leído el YAML con un parser, devuelve una sola versión. Se usó el YAML.
- Un `ModrinthVersion` es un `Document` y no se puede construir sin `init_beanie`, que exige un
  servidor. Por eso los contratos de Modrinth corren dentro del fixture `mongo`.

**Arquitectura.** ✅ Cumple.

**Qué quedó sin hacer.** `ClientSession` por request (i-7985ee-57034c).

**No verificado.** Las APIs reales de Modrinth y Pterodactyl; los contratos son contra la
especificación.

**Medido.** Gate con Mongo: 44 pasan, audit 0 fallos y 0 avisos (antes, 6 avisos). Los nueve
contratos se vieron fallar antes. La regla se vio fallar plantando un `backoff` en `server_power`.

## 2026-10-01 · s-7985ee-92a5f4 — S5: los updates escriben, muchas versiones por proyecto y lotes que no pierden filas

**Qué.**
- **Updates (i-7985ee-055362).** `project_update` y `server_update` guardan lo que devuelve la
  API (los argumentos de `*_save` iban al revés). `project_update_many` empareja por
  `project_id`, no por el orden en que contesta Modrinth.
- **Versiones (i-7985ee-be55dd).** `ModrinthVersion.project_id` deja de ser `unique`, y
  `drop_unique_index` borra el índice viejo al arrancar. Los lotes de Modrinth pasan a
  `project_upsert_many` y `version_upsert_many`: un upsert por clave natural, deduplicado.
- **Caché (i-7985ee-c7b593).** `UsersInternal` sin `use_cache`.

**Por qué.** Era S5. Más los dos puntos traídos del otro repositorio.

**Qué salió mal en el camino.** Hallazgos, no tropiezos:
- `project_version_update_*` y `project_dependencies` lanzaban `TypeError` siempre (`upsert` sin
  actualización): no funcionaron nunca.
- `Settings.collection` no es de Beanie 2: las colecciones se llaman como la clase
  (i-7985ee-c1de10, planeado, sin tocar para no dejar huérfanos los datos).
- Medido: quitar el `unique` sin migrar habría impedido arrancar la API en producción
  (`OperationFailure`). Se descartó `allow_index_dropping`, que borra cualquier índice ajeno.

**Arquitectura.** ✅ Cumple.

**Qué quedó sin hacer.** i-7985ee-c1de10. Los `insert_many` de modpack y Pterodactyl siguen
ordenados, pero sin índices `unique` no pierden filas.

**No verificado.** Datos reales de producción.

**Medido.** Siete tests nuevos contra Mongo, todos vistos fallar antes. Gate: 34 pasan.

## 2026-10-01 · s-7985ee-f6eda9 — Lo que se trae de otro repositorio sobre MongoDB: bloqueo de red, insert_many, caché y contrato

**Qué.** El dueño pidió revisar cómo maneja MongoDB otro repositorio suyo, priorizando la
literatura. Se tomó lo útil:
- **Bloqueo de red en los tests (d-7985ee-1a20c1).** El fixture automático `no_network` rechaza
  `connect` salvo en tests con `@pytest.mark.network`, y `getaddrinfo` fuera de localhost siempre.
  `strict_markers` está activo y el fixture `mongo` exige el marcador. Están marcados
  `tests/test_database.py` y `tests/test_http_client.py`.
- **El fixture `mongo` dice por qué** reinicia Beanie en cada test (la caché es por clase y la
  clave no incluye la base; lo confirma `beanie/odm/utils/init.py`) y por qué no usa
  `skip_indexes`. El otro repositorio sí lo usa, y deja sus índices sin probar.
- **En el roadmap:**
  - `insert_many` con `ordered=True` pierde filas ante un duplicado (en i-7985ee-be55dd y S5);
  - `use_cache` en `UsersInternal`, i-7985ee-c7b593, ítem nuevo;
  - el contrato doble-contra-real para S6.

**Áreas.** `tests/conftest.py`, `tests/test_no_network.py`, `pyproject.toml`, dos marcadores,
documentos.

**Por qué.** Hoy un test podía resolver y conectarse a `api.modrinth.com` sin que nada lo notara.
Los tres tests de `tests/test_no_network.py` lo mostraron fallando antes del fixture.

**Qué salió mal en el camino.** Nada que cambiara el plan. El repositorio no estaba donde se
buscó primero, y el dueño dio la ruta.

**Arquitectura.** ✅ Cumple.

**Qué quedó sin hacer.**
- Lo del otro repositorio que no aplica aquí: su CI nunca corre los tests contra Mongo, y su
  producción es FerretDB. Se le dijo al dueño, pero no se escribió allí.
- El arreglo de `insert_many` y el de `use_cache` quedan planeados, no hechos.

**No verificado.** Lo mismo que s-7985ee-8315e1. CI en el remoto: falta el push.

**Medido.** `./scripts/test-mongo.sh`: 27 pasan, 1 `xfail`. Sin el marcador, el test de aiohttp
falla con el `RuntimeError` del bloqueo y los de base fallan en el fixture.

## 2026-10-01 · s-7985ee-8315e1 — S4: MongoDB real en CI, y la migración de usuarios antiguos que reventaba

**Qué.**
- i-7985ee-ade111: servicio `mongo:8.0` en el job `gate`, y el fixture `mongo` en
  `tests/conftest.py`. El fixture usa bases con prefijo propio por test y las borra al terminar.
  Sin `TEST_MONGODB_URI`, se salta en local y falla en CI. `init_databases(client, prefix)` sale
  de `configure_beanie`, que se comporta igual que antes. En local, `scripts/test-mongo.sh`.
- `tests/test_database.py`:
  - tres tests de `user_from_provider`: identidad nueva, `iss` + `sub` y no el email, y la
    migración de un usuario anterior a S3;
  - el bug de i-7985ee-055362 reproducido, en `xfail(strict=True, raises=AssertionError)` hasta S5.
- **Bug corregido (S3, sin desplegar):** la migración de usuarios antiguos nunca funcionó. Su
  `provider` no tiene `sub`, el `find_one` de Beanie lo validaba contra el modelo nuevo y lanzaba
  `ValidationError`. Resultado: un 500 en el login de todo usuario guardado antes de S3. Ahora se
  busca y se migra en crudo, con un `find_one_and_update` atómico.
- d-7985ee-1a2f53; roadmap, `AGENTS.md` (comandos y verificación) y d-7985ee-361ed0 al día.

**Áreas.** `src/database/users.py`, `src/model/__init__.py`, `tests/`, `.github/workflows/check.yml`,
`scripts/test-mongo.sh`, documentos.

**Por qué.** Era S4 de `plan-de-trabajo.md` §3. El primer test contra la base real encontró el bug
de S3, que los diez tests de `tests/test_auth.py` no veían porque no tocan Mongo.

**Qué salió mal en el camino.** El `xfail` estricto, sin `raises`, se tragó un `ImportError` y
lo contó como el bug esperado. Ahora exige `AssertionError`.

**Arquitectura.** ✅ Cumple. El acceso crudo a la colección queda dentro de `src/database/`.

**Qué quedó sin hacer.**
- Desconocida la versión de Mongo de producción; CI usa 8.0. Igualarla al conocerla.
- S5 sigue: quitar el `xfail` al arreglar los updates.
- `UsersInternal` tiene `use_cache = True`; no se miró si el caché de Beanie puede devolver un
  usuario viejo después de la migración.

**No verificado.** Datos reales de producción, en particular que los usuarios antiguos tengan
exactamente la forma del test (`provider` sin `sub`, sacada del modelo anterior a S3). CI en el
remoto: falta el push.

**Medido.** `./scripts/test-mongo.sh` en local (podman, `mongo:8.0`): 24 pasan, 1 `xfail`.
El test de migración se vio fallar antes del arreglo, y los tests de base, saltarse sin la URI y
fallar con `CI` definido.

## 2026-10-01 · s-7985ee-704f46 — fase-0 fusionada en main: el próximo update exige el runbook de MongoDB

**Qué.**
- `fase-0` (S2, S3 y el cierre) fusionada en `main` por fast-forward, por pedido explícito del dueño.
  El cierre anterior (s-7985ee-0cc0fa) recomendaba esperar al runbook; el dueño eligió fusionar
  ahora.
- `README.md` y el roadmap ahora avisan que el próximo `./scripts/update.sh` en el servidor exige
  seguir antes `docs/runbooks/migrar-mongo-a-volumen.md`.

**Áreas.** `README.md`, `docs/roadmap.md`, este registro.

**Por qué.** El dueño pidió cerrar la sesión de código con todos los cambios en `main`.

**Arquitectura.** ✅ Cumple: solo documentos; el código es el de `6be8379`, ya verificado por CI.

**Qué quedó sin hacer.** Lo mismo que s-7985ee-0cc0fa, salvo la fusión. **El riesgo cambió de lugar:**
ya no lo frena la rama, lo frena seguir el runbook antes del próximo update. La rama `fase-0` sigue
en el remoto; se puede borrar.

**Medido.** CI en `main` después de la fusión: ver el push de este commit.

## 2026-10-01 · s-7985ee-0cc0fa — Cierre de la sesión del 2026-10-01: hallazgos, aprendizajes y pendientes

Resumen de toda la jornada, de la adopción del método a S3. El detalle de cada parte está en las
entradas de abajo y en los documentos que se citan; aquí solo lo que hace falta para retomar.

**Qué quedó hecho.**
- **Método adoptado:** bundle 0.0.25, carrier `r-7985ee`, `AGENTS.md` con invariantes, gate
  `hatch run gate` en CI.
- **Arreglos con test:**
  - el arranque en 3.12;
  - el cableado de `ModrinthService`;
  - la fuga del token de Pterodactyl por un dict compartido.
- **Investigación:** cuatro informes (`docs/research/`), el plan (`docs/plan.md`) y el plan de trabajo
  (`docs/plan-de-trabajo.md`).
- **S1 en `main`:** dependencias fijas.
- **S2 y S3 en la rama `fase-0`, con CI en verde:**
  - volumen de Mongo con nombre;
  - imagen que arranca;
  - login con autorización por grupo e identidad `iss` + `sub`.

**Hallazgos** (el estado del proyecto que alguien que llega necesita saber):
- **El despliegue anterior nunca pudo arrancar la API en Docker**: la imagen no copiaba `main.py` ni
  recibía `.env.yaml`. ASUNCIÓN: la API corría fuera de Docker.
- **Los datos de Mongo vivían en un volumen anónimo.** Cada `stop.sh` los dejaba huérfanos.
- **Cualquier cuenta del IdP podía operar los servidores** (cerrado en S3, falta desplegar).
- **Pterodactyl no da hashes y no los va a dar** (pterodactyl/panel#4894). Identificar mods exige
  leer los bytes o que la app sea quien instala (i-7985ee-43df2f).
- **Modrinth y Minecraft cambiaron:**
  - Minecraft numera por año (`26.x`);
  - `client_side`/`server_side` están deprecados por `environment`;
  - `/project/{id}/dependencies` no sirve para resolver (i-7985ee-f4ba63).
- **Nadie hace lo que este proyecto se propone.** El análogo es Technic Solder, sin dependencias
  (`docs/research/2026-10-plataformas-similares.md`).

**Aprendizajes** (lo que cambió cómo se trabaja aquí):
- **Investigar en la fuente primaria antes de tocar algo importante paga**: la regla de login que
  exigía `email_verified` habría dejado afuera a todos, porque authentik lo manda en `false` por
  defecto desde 2025.10. Se detectó leyendo la documentación de authentik antes de desplegar.
- **Un chequeo en verde puede estar ciego:** `bundle.py ids` pasaba sin ver la mitad de las
  definiciones, por una diferencia entre la plantilla del método y la herramienta. Comparar lo que
  un chequeo examinó con lo que existe.
- **Probar en otra versión esconde errores:** el `NameError` de arranque solo aparece en 3.12, no en
  3.14.
- **CI solo en `main` deja sin chequear el trabajo en ramas**, y aquí no hay `gh` para abrir PRs. Por
  eso CI corre ahora en cada push.

**Pendientes, en orden** (el detalle en `docs/roadmap.md` por id):
1. **Desplegar `fase-0` siguiendo `docs/runbooks/migrar-mongo-a-volumen.md`** (respaldo, versión,
   restaurar), y después fusionar `fase-0` en `main`. **No fusionar antes:** `scripts/update.sh` hace
   `git pull` y desplegaría el volumen nuevo, vacío. Es lo único que bloquea.
2. Del dueño:
   - crear el grupo en authentik;
   - las variables nuevas en `.env.yaml` (`OIDC_ALLOWED_GROUPS` es obligatoria);
   - contestar la decisión 3 (¿hay datos en producción?);
   - **revocar la clave de Pterodactyl** si la API corrió alguna vez sin `MODRINTH_TOKEN` (fuga
     corregida en `81a7779`).
3. Las sesiones siguientes de la fase 0, en orden (`docs/plan-de-trabajo.md` §3):
   - S4: MongoDB real en CI; también cubre `user_from_provider`, hoy sin test;
   - S5: los updates escriben y caben muchas versiones;
   - S6: contratos y reintentos;
   - S7: usuario de servicio y `/health`.
4. Sueltos:
   - `httpx` en `requirements.txt` sin uso (i-7985ee-bb2fde);
   - las 14 inyecciones en la baseline de capas (i-7985ee-2bbbfd);
   - los 6 avisos de reintentos (i-7985ee-05c4a0).
5. Propuestas al bundle esperando al home: 5 de la primera jornada y las de este cierre.

**No verificado.**
- El runbook no se ejecutó.
- El login real contra authentik no se probó.
- MongoDB real, Modrinth y Pterodactyl no se tocaron.

**Medido.** `main` (S1) y `fase-0` (S2 y S3) en verde en CI. `fase-0` corrió los jobs `gate` y `image`,
con 21 tests.

## 2026-10-01 · s-7985ee-0a09db — S2 y S3: los datos de Mongo en un volumen con nombre, la imagen que arranca y el login con autorización

**Qué.** En la rama `fase-0`:
- S2 (i-7985ee-e21d5d, i-7985ee-a77cdc):
  - el compose con volúmenes con nombre, `mongo:${MONGO_VERSION:?}`, puertos en `127.0.0.1`,
    healthcheck y `service_healthy`;
  - `.env.yaml` montado de solo lectura;
  - el Dockerfile copia `main.py` y `src/`;
  - `run.sh` falla sin `.env.yaml` y `stop.sh` advierte contra `-v`;
  - un job `image` en CI;
  - `docs/runbooks/migrar-mongo-a-volumen.md`.
- S3 (i-7985ee-c64e06):
  - `authorize` como función pura, con la identidad `iss` + `sub` y migración en el primer login;
  - `UsersService.login` reemplaza a `provider_validate`;
  - sesión `https_only` y 12 h, CORS solo con orígenes nombrados, 401 si la sesión no tiene
    usuario, y `regex=` → `pattern=`.
- CI corre en cada push, no solo en `main`.

**Áreas.** `docker/`, `scripts/`, `main.py`, `src/library/auth/`, `src/services/users.py`,
`src/database/users.py`, `src/model/users/`, `src/routers/v1/auth.py`, `src/dependences.py`,
`.env.default`, `.github/workflows/check.yml`, `tests/`, `docs/`.

**Por qué.** El dueño pidió aplicar las decisiones. Aceptó las recomendaciones 1, 2 y 4. La 3
(¿hay datos en producción?) no se contestó: se supuso que sí.

**Arquitectura.** ✅ Cumple: la decisión de autorizar vive en el servicio y el router solo lo llama
(d-7985ee-16ed28).

**Qué salió mal en el camino.**
- La primera versión de `authorize` exigía `email_verified` siempre. La investigación previa a S2
  mostró que authentik lo manda en `false` por defecto desde 2025.10: nadie habría podido
  entrar. El grupo ya no lo exige; la lista de emails sí. Se vio fallar un test nuevo antes del
  arreglo.
- El script que esperaba a CI tenía un error de comillas y daba vueltas sin medir nada. Se cambió
  por un script en archivo.

**Qué quedó sin hacer.**
- Desplegar S2 y S3 siguiendo el runbook. El grupo de authentik y las variables nuevas son del
  dueño.
- `user_from_provider` (consulta a Mongo) sin test hasta S4.
- Un healthcheck de la API espera `/health` (S7).

**No verificado.**
- La imagen, el compose y su rechazo sin `MONGO_VERSION` no se probaron en esta máquina (no hay
  Docker). Los probó el job `image` de CI en la rama: todos los pasos en verde
  ([run 36886674154](https://github.com/fabaindaiz/modpack-creator/actions/runs/36886674154)).
- El runbook no se ejecutó.
- El login real contra authentik no se probó.

**Medido.** El gate local pasa con 21 tests (11 nuevos de autorización e identidad, contando el
del aviso de authentik).

## 2026-10-01 · s-7985ee-46eda5 — S1: dependencias con versión exacta y una regla del audit que lo exige

**Qué.**
- `docs/plan-de-trabajo.md`: las decisiones previas, la rutina de cada sesión, la fase 0 en siete
  sesiones, las fases siguientes con sus puntos de control, las cadencias y los riesgos. Enlazado
  desde `plan.md` y `AGENTS.md`.
- S1 (i-7985ee-bb2fde): `requirements.txt` con `==` en las 12 dependencias y sin el `fastapi`
  repetido. Una regla nueva en `scripts/audit.py`, `pinned_requirements`, con su test.

**Áreas.** `requirements.txt`, `scripts/audit.py`, `tests/test_audit.py`, `docs/`, `AGENTS.md`.

**Por qué.** El dueño pidió un plan de trabajo y empezar a implementarlo. S1 es la única sesión de
la fase 0 que no espera una de sus decisiones. La decisión 4 (versiones exactas) se tomó con la
recomendación del plan (d-7985ee-cd8f73), y es fácil de revertir.

**Arquitectura.** ✅ Cumple: d-7985ee-d0f29c sigue (una sola lista; la imagen y hatch quedan fijos
a la vez).

**Qué salió mal en el camino.** El docstring de la regla nueva citó primero un id de decisión
inventado como marcador. Se reemplazó por el acuñado antes de correr el gate.

**Qué quedó sin hacer.**
- Las transitivas sin fijar.
- `httpx` en `requirements.txt` sin uso en el código, con un aviso de deprecación de Authlib.
- S2 a S7 esperan las decisiones 1 a 3.

**No verificado.** CI con las versiones fijas: el cambio no está empujado. La imagen Docker no se
construyó.

**Medido.** La regla nueva marcó 14 fallos sobre el archivo anterior (13 sin versión y 1 repetida)
y 0 después. El gate pasa con 11 tests.

## 2026-10-01 · s-7985ee-7c3cda — Investigación de plataformas similares y plan en fases

**Qué.**
- Cuatro informes en `docs/research/`:
  - 23 plataformas y herramientas similares comparadas;
  - resolución de dependencias, metadatos de loaders y formatos de exportación;
  - operar servidores de Pterodactyl con backup y rollback;
  - arquitectura (trabajos durables, tests con Mongo, autorización, avisos, UI, observabilidad).
- `docs/plan.md`: la posición del proyecto ("un Solder con dependencias"), el modelo objetivo
  (pack → reglas → build inmutable → despliegue ← manifiesto observado) y cinco fases.
- 14 ítems nuevos en `docs/roadmap.md`.
- Los informes enlazados desde `docs/references.md` y el mapa de `AGENTS.md`.
- Antes, en esta misma fecha: la investigación de hashes en Pterodactyl (i-7985ee-43df2f).

**Áreas.** `docs/plan.md`, `docs/research/`, `docs/roadmap.md`, `docs/references.md`, `AGENTS.md`.

**Por qué.** El dueño pidió una investigación detallada de plataformas similares y de cómo seguir el
proyecto, con planes, ideas e informes, y cerrar la sesión.

**Arquitectura.** ✅ Cumple: solo documentos; ningún cambio de código.

**Qué salió mal en el camino.**
- Un informe se escribió con un id de roadmap inventado como marcador. Se reemplazó por los ids
  acuñados antes de enlazarlo.
- `bundle.py ids` pasaba con 0 errores pero reconocía 21 de 42 definiciones: el formato de
  encabezado del roadmap (id antes del `·`, como dice la plantilla del método) no es el que lee la
  herramienta. Al mover los ids después del `·`, el chequeo marcó 5 definiciones dobles en la tabla
  de costos; esa tabla ahora cita los ids en la segunda columna. Queda en `adapted`.

**Qué quedó sin hacer.**
- Ninguna fase del plan empezó.
- Hallazgos nuevos que el código actual no cumple:
  - el volumen de Mongo está comentado en `docker/docker-compose.yaml`, así que recrear el
    contenedor borra la base (i-7985ee-e21d5d);
  - `ModrinthProject` exige `client_side`/`server_side`, que están deprecados;
  - Minecraft numera por año (`26.x`);
  - `ModrinthService.project_dependencies` usa un endpoint que mezcla todas las versiones
    (i-7985ee-f4ba63).

**No verificado.**
- Las páginas de add-ons de BuiltByBit (403), Shockbyte y Apex.
- El significado de `embedded` en las dependencias de Modrinth (la spec no lo define).
- Que un egg pueda correr packwiz antes de Java.
- El límite por webhook de Discord.
- Todo está marcado así en cada informe.

**Medido.** Llamadas reales a las APIs públicas, citadas en los informes:
- `/tag/game_version` tiene 917 entradas; la última release es `26.3`;
- `POST /version_files/update` resolvió una versión vieja a la más nueva compatible;
- Quilt meta devolvió 307 loaders sin filtrar por versión.

## 2026-10-01 · s-7985ee-134d63 — Bootstrap del método: gate en CI con hatch, audit, tests de arranque y la fuga de cabeceras corregida

**Qué.**
- Adopción del bundle 0.0.25 y la evaluación (`.agents/evaluation-2026-10-01-38f8aa.md`).
- El entorno hatch (`pyproject.toml`, Python 3.12, dependencias desde `requirements.txt`).
- El gate `hatch run gate`, en CI (`.github/workflows/check.yml`) y en `scripts/check.sh`.
- `scripts/audit.py` con sus tests.
- `tests/test_startup.py` y `tests/test_http_client.py`.
- `AGENTS.md` con invariantes, `docs/decisions.md`, `docs/references.md`, `docs/roadmap.md`,
  `.claude/settings.json` y los skills `verify` y `state-review`.

Arreglos de código, cada uno visto fallar antes en un test o en mypy:
- el import faltante de `PterodactylVersion`, que impedía arrancar en 3.12;
- `ModrinthService` recibía la base de modpacks;
- los argumentos por defecto mutables de `src/library/api/`: un dict compartido reenviaba la
  cabecera `Authorization` de Pterodactyl a otros clientes, reproducido contra un servidor local;
- el campo `server` de `PterodactylVersion`, que ya usaban sus dos llamadores;
- el re-export muerto de `src/library/auth`.

**Áreas.** `src/library/api/`, `src/dependences.py`, `src/services/pterodactyl.py`,
`src/model/pterodactyl/modpack.py`, `src/database/pterodactyl.py`, `src/library/auth/`,
`tests/`, `scripts/`, `docs/`, `.claude/`, `.github/`, `pyproject.toml`, `.mypy.ini`.

**Por qué.**
- El dueño pidió inicializar el repositorio con `.agents` y aprobó la topología de la Fase 3.
- Decisiones del dueño: hatch para el entorno, los routers pasan por un servicio, CI es el gate.
- La app corre en su servidor, conectada a su panel de Pterodactyl.

**Arquitectura.** ✅ Cumple. Las 14 inyecciones de routers que saltan la capa de servicios quedan
en una baseline que solo se achica (d-7985ee-16ed28), no arregladas.

**Qué salió mal en el camino.**
- El primer test de rutas suponía que `main.py` monta los routers al importar; los monta en el
  `lifespan`. Además, desde FastAPI 0.142 `app.routes` muestra los routers incluidos como
  `_IncludedRouter` sin `path`. El test ahora lee `app.openapi()["paths"]`.
- La primera lista de defaults mutables (10) contaba mal: las firmas de varias líneas escondían
  otras siete.

**Qué quedó sin hacer.**
- El Dockerfile sigue sin copiar `main.py` (i-7985ee-a77cdc).
- Los updates siguen sin escribir el dato nuevo (i-7985ee-055362).
- El índice único de `ModrinthVersion.project_id` sigue (i-7985ee-be55dd).
- Los contratos de Modrinth y Pterodactyl siguen mal (i-7985ee-fb00ca).
- Los 6 avisos de reintentos sobre POST (i-7985ee-05c4a0).
- Cualquier cuenta del IdP entra (i-7985ee-c64e06).
- `version_create` está anotado como `PterodactylServer` y devuelve una lista (i-7985ee-4364a3).

**No verificado.** La imagen Docker no se construyó (no hay Docker en esta máquina). MongoDB, OIDC
y las APIs reales no se tocaron. CI todavía no corrió: el workflow existe, pero no se ha empujado.
`version_update` ahora lanza `NotImplementedError` en vez de devolver `None`; nadie lo llama.

**Medido.**
- Antes: `import main` en 3.12 → `NameError`; mypy → 4 errores; 0 tests.
- Después: el gate pasa con 10 tests, mypy con 0 errores en 51 archivos, y el audit con 0 fallos,
  6 avisos y una baseline de capas de 14.

---

## Formato de una entrada

```markdown
## YYYY-MM-DD · s-7985ee-<content6> — <título de una línea>
**Qué.** Qué cambió, concreto.
**Áreas.** Archivos o carpetas.
**Por qué.** La razón, incluido el pedido que la originó.
**Arquitectura.** ✅ Cumple · ⚠️ Desvío · REVISAR — y por qué.
**Qué salió mal en el camino.** Qué hizo mal el primer intento y qué lo detectó.
**Qué quedó sin hacer.** La deuda que este cambio creó o dejó pasar, con nombre.
**Desvío del plan.** Dónde lo construido se aparta de lo aprobado, y la medición que lo decidió.
**No verificado.** Lo que no se pudo comprobar en este entorno, y dónde espera la pregunta.
**Medido.** El número, si se afirmó algo.
```

El id se acuña una vez con `python .agents/tools/bundle.py id s "<título>"` y no se recalcula.
