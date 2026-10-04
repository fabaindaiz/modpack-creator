# Panel web, usabilidad (ronda 1): ficha de mod, crear en el momento, estado vivo y avisos

El dueño aprobó este diseño el 2026-10-04 en el chat. Sale del recorrido documentado en
`docs/research/2026-10-04-usabilidad-panel.md`; los F01… de aquí son los hallazgos de ese documento.
Se apoya en las specs del panel anteriores (`2026-10-02-panel-web-design.md` y
`2026-10-03-panel-web-next-design.md`); donde las contradice, manda esta.

## Qué se quiere

Los pedidos del dueño:
1. Los mods tienen una ficha visual con imagen, descripción y versión actual, más las versiones y los
   loaders disponibles: hoy no se reconocen solo por nombre y slug.
2. Lo que depende de otra cosa se hace en el momento: por ejemplo, crear un modpack desde la pestaña
   de servidores para vincularlo.
3. El panel se ve mejor, es agradable de usar y las tareas frecuentes cuestan menos pasos.
4. Se itera con pruebas reales, y lo aprendido queda documentado.

**Decisiones del dueño (2026-10-04):**
- la ficha es una **lista única con ficha desplegable**;
- crear en el momento por **las dos vías y con siguiente paso**;
- **el núcleo ahora** y el resto en una ronda 2;
- estilo **sobrio**, el actual, **con mejor jerarquía**.

**Decisiones del dueño en la revisión por temas (2026-10-04):**
- **Ficha:**
  - la fila como en §1;
  - los íconos se completan al ver el pack, con 1 llamada;
  - se fija una versión solo con el selector.
- **Crear en el momento:**
  - **el editor crea y el operador vincula e importa**;
  - sin escaneo, botón "Escanear para deducir";
  - el siguiente paso es un botón.
- **Estado:**
  - las secciones se recargan solas;
  - una resolución vieja se muestra con aviso;
  - **"Crear build" decide los pendientes en el momento**, en un diálogo.
- **Avisos:**
  - **junto al formulario y en la esquina fija**;
  - **detalle técnico plegable y saneado**;
  - los errores quedan y los éxitos se van solos.
- **Visual:**
  - el pack con Mods ancho y una columna lateral;
  - el servidor con estado arriba y acciones al lado.

**Éxito:**
- ningún id opaco de Modrinth a la vista donde exista un nombre o un número de versión;
- desde un servidor sin modpack se llega a un modpack vinculado e importado sin salir de la página;
- un trabajo que termina refresca su página;
- un aviso de error se ve desde donde se hizo clic;
- la tarea "servidor → modpack → simular" de F36 (unas 25 acciones y 6 cambios de página) se repite
  en la instancia local y cuesta claramente menos (se mide y se reporta);
- el gate pasa y el barrido visual termina sin problemas.

## 1. Ficha de mod: una sola lista del pack

**Una lista reemplaza dos.** En la página del pack, la sección "Proyectos" (con sus dos tablas, "Del
pack" y "Agregados por el admin") y la tabla del lock se juntan en **"Mods"**:
- una fila por cada proyecto del pack;
- más una por cada entrada de la última resolución que no esté en el pack (sus dependencias).

**La fila**, visible siempre:
- ícono, título y descargas;
- el origen como insignia: "agregado" (`admin`), "del .mrpack" (`pack`) o "dependencia de *Título*";
- el lado: "En el servidor", "Solo cliente" o "Sin decidir" (de la última resolución);
- la versión, con el `version_number` del último build y, si la última revisión de actualizaciones
  trae una más nueva y no fijada, "→ *número*";
- "fijado" si tiene pin.

Un número de versión largo se corta con elipsis y el texto completo va en `title` (F35).

**La ficha**: un `<details>` por fila, sin JS propio (la CSP lo exige), con:
- la descripción corta y las categorías;
- los loaders y las versiones de Minecraft del proyecto, marcando si soporta las del pack: ✓ o "no
  soporta 1.21.1";
- enlaces a Modrinth, al código y a los issues, si existen;
- las acciones, según el rol y el origen:
  - **quitar**, con la regla de hoy: solo lo agregado;
  - **decidir servidor o cliente** ahí mismo, si está "Sin decidir", con el `decision_set`
    existente;
  - **fijar versión** con un selector (F05). Se carga al abrir la ficha (`hx-trigger="toggle once"`
    sobre el `<details>`) desde `GET /modpacks/{id}/projects/{project_id}/versions`, que lista las
    versiones del proyecto para el loader y la versión de MC del pack (las del catálogo, o
    `project_versions` de Modrinth si faltan) con número, canal y fecha. Envía el id de versión al
    `pin_set` existente. Quitar el pin sigue igual. El formulario de hoy, que pide el id de versión
    a mano, desaparece (decisión del dueño: solo el selector).

**Encima de la lista**, solo cuando hacen falta:
- los problemas de la resolución;
- las sugerencias;
- el formulario "Decidir varios" de hoy (`decisions/bulk`), con los mods por decidir por título.

La búsqueda y "por slug o id" siguen debajo, en la misma columna.

**Datos:**
- `ModrinthProject` suma `icon_url: Optional[str] = None`. Se llena con lo que Modrinth devuelve
  cuando el proyecto se guarda o se refresca. Un documento viejo sin el campo se lee como `None`.
- Al armar la lista, los proyectos del pack que falten en el catálogo o no tengan `icon_url` se
  refrescan con **una** llamada por lotes (`project_update_many` o `project_create_many` de
  `ModrinthService`, que ya existen). Es best effort, con un tope de 5 s como los changelogs: si
  Modrinth falla o no alcanza, la lista se muestra sin esos íconos, nunca con un 500.
- Un ícono solo se muestra si su URL es de `cdn.modrinth.com` (la regla de `_icon` de hoy). Si no,
  va un cuadro neutro.

**Nombres en vez de ids en todo el panel:**
- sugerencias del lock con título, motivo y botón "Agregar" (F12);
- "dependencia de" y "requerida por" con título (F13);
- comparación del servidor: proyecto con título (completando el catálogo como arriba) y versión
  con `version_number`. Si la versión no está en el catálogo, se usa el nombre del jar (F20, F30);
- el bloque `<pre>` del drift deja de mostrarse en el servidor, porque la tabla lo dice todo (F22).
  El texto del drift sigue para Discord.

**Búsqueda:** al agregar un resultado, su botón pasa a "Ya está" con un swap fuera de banda
(`hx-swap-oob`) en la misma respuesta que actualiza la lista (F02).

## 2. Crear en el momento

**Un formulario en el servidor sin modpack**: "Nuevo modpack para este servidor", en la sección
Modpack, junto al selector de vincular (que sigue). Pide:
- nombre (por defecto, el del servidor);
- versión de Minecraft y loader, **deducidos del último escaneo** si lo hay, editables, con la
  explicación de la deducción;
- la casilla "Importar lo que tiene el servidor": marcada si hay escaneo, deshabilitada si no.

Sin escaneo, el formulario ofrece **"Escanear para deducir"** (el `enqueue_scan` existente). Al
terminar el escaneo la sección se refresca sola (§3) y aparece rellenado.

**Deducción.** Es una función pura en `src/library/deduce.py`:

`deduce_target(identified: list[tuple[str, list[str], list[str]]]) -> Deduction`

Recibe, por cada jar identificado, (version_id, loaders, game_versions) del catálogo. Devuelve:
- `loader: Optional[str]`, el valor de `MinecraftLoaders` que aparece en más jars;
- `game_version: Optional[str]`, la versión de MC que aparece en más jars;
- `identified: int` y los votos de cada uno, para explicar: "8 de 9 jars identificados son fabric;
  9 de 9 soportan 1.21.1".

Un **empate deja `None`** y el usuario elige; nada se adivina. Solo cuentan los loaders que son
valores de `MinecraftLoaders`: `datapack`, `iris` y similares no votan.

**El método**:

`PterodactylService.bundle_for_server(server_id, name, version, loader, import_mods: bool, actor) -> BundleForServer`

- Es de operador (`operator_only`), porque vincular e importar lo son. Lleva su fila en `## Acciones
  y roles`.
- Rechaza con 409 si el servidor ya tiene modpack.
- En orden: crea el bundle, lo vincula y, si `import_mods`, corre `import_server`.
- Cada paso deja su evento de auditoría, como hoy: `bundle.create`, el del vínculo y el de la
  importación.
- Si la importación falla después de crear y vincular, el bundle queda creado y vinculado. La
  respuesta lo dice y ofrece "Importar" de nuevo: lo que queda es un pack vacío vinculado, que se
  puede reintentar, nunca un servidor tocado. Solo lee el panel.

**Editor crea, operador vincula (decisión del dueño).** El formulario lo ven los dos roles:
- **un operador** crea, vincula e importa en un paso con `bundle_for_server`;
- **un editor** ve las mismas casillas, pero "Vincular" e "Importar" aparecen deshabilitadas con
  "requiere operador". Al enviar se crea solo el pack, con el `ModpackService.bundle_create`
  existente (de editor) y el nombre, el loader y la versión deducidos. La respuesta dice "Modpack
  creado. Pide a un operador que lo vincule a este servidor e importe sus mods". Al recargar, el
  pack ya aparece en el selector de vincular.

La ruta elige el método según `actor.is_operator`, como cualquier diferencia de interfaz. La regla
vive en los servicios: `bundle_for_server` es `operator_only` y `bundle_create` es de editor, como
hoy.

La ruta es `POST /servers/{id}/modpack`. Responde con el fragmento del resultado: el pack creado, lo
importado (como el resultado de importar de hoy, con el archivo local rotulado, F25) y el siguiente
paso. El siguiente paso es **un botón, nunca algo automático** (decisión del dueño): nada resuelve,
escanea ni crea un build sin que se pida.

**Siguiente paso.** Una macro `next_step` muestra una acción destacada al pie de cada resultado:
- después de importar o de crear desde el servidor: "Resolver y crear build", que enlaza al pack;
- después de crear un build: "Simular en *servidor*" por cada servidor vinculado al pack, que
  enlaza a su sección Actualizar;
- después de guardar decisiones, si ya no quedan pendientes: "Crear build" destacado.

**Mensajes que apuntan al arreglo correcto (F26).** El rechazo de un jar sin explicar pasa al
español y distingue dos casos:
- el pack ya lo incluye (como proyecto o archivo local) pero el build no: "crea un build nuevo: el
  modpack ya lo incluye";
- tampoco el pack lo incluye: "importa el servidor".

## 3. Estado vivo

**Un trabajo que termina refresca su página (F19).**
- `GET /jobs/{id}/row`, cuando devuelve un trabajo terminado, agrega `HX-Trigger: job-finished`.
  Solo pasa una vez, porque una fila terminada ya no se consulta.
- La página del servidor envuelve sus secciones (no la fila del trabajo) en `#server-main`, con
  `hx-get` a su propia URL, `hx-trigger="job-finished from:body"`, `hx-select="#server-main"` y
  `hx-swap="outerHTML"`. No hay rutas nuevas.
- La página de actualizaciones del pack hace lo mismo para la revisión.

**La última resolución se guarda (F39).** Es un `Document` nuevo, `ModpackResolution`, en la base
`{prefix}modpack` y registrado en `init_beanie`, con:
- `bundle_id`, único;
- `resolved_at`;
- `request_digest`;
- `lock`.

Funciona así:
- `ModpackService.resolve` y `build_create` la escriben con un upsert por `bundle_id`. La fila de
  `resolve` en la tabla de roles cambia de texto: ahora guarda.
- `request_digest(request: ResolveRequest) -> str` es una función pura: sha256 del JSON canónico de
  la petición, que ya incluye pins, decisiones, canales y reemplazos (los globales mezclados).
- La página muestra la guardada, con "resuelto *cuándo*". Si el digest de la petición actual es
  distinto, dice "el pack cambió desde la última resolución: resuelve de nuevo".
- **La resolución guardada nunca es entrada de nada**: `build_create` vuelve a resolver siempre.
  Es una copia derivada que se muestra; el digest decide si está vigente, nunca la fecha (tarjetas
  `derived-copy-goes-stale-silently` y `persist-inputs-derive-verdicts`).
- **"Crear build" con mods por decidir abre un diálogo para decidirlos en el momento** (F15,
  decisión del dueño). Lo decide el servidor, no una resolución guardada que podría estar vieja:
  - `build_create` rechaza con un error propio, `BuildBlocked`, que hereda del 409 de hoy (así `/v1`
    y cualquier otro llamador siguen viendo un 409 con el mismo texto). Lleva el lock recién
    resuelto.
  - La ruta del panel lo atrapa. Si el único motivo son mods por decidir, responde con un diálogo
    que lista esos mods por título, cada uno con "En el servidor" o "Solo cliente", y el botón
    "Decidir y crear build".
  - Ese formulario hace `POST /modpacks/{id}/builds` con las decisiones. La ruta llama al
    `decisions_set` existente y después a `build_create`, las dos de editor. Si todavía falla (por
    problemas, o por otro mod sin decidir que apareció), el motivo se muestra **dentro del diálogo**.
  - Si hay problemas (no solo indecisión), no hay diálogo: el motivo va en un aviso.
  - Si la resolución vigente ya sabe que hay pendientes, el botón dice "Decidir y crear build…",
    pero hace lo mismo.

**Botones que muestran que trabajan (F31).** El macro `button` agrega `hx-disabled-elt="this"` a todo
botón con `hx-post`, `hx-put` o `hx-delete`. El CSS da a `.htmx-request` un estado ocupado visible:
opacidad y `cursor: progress`, sin JS.

**Tablero (F37).** El estado de cada servidor sale del último escaneo que tenga drift calculado. Un
escaneo de un servidor vinculado siempre lo calcula, también el de la simulación. "Sin comparar"
queda solo para un servidor sin modpack.

## 4. Avisos y textos

**Dónde aparece un aviso (F11): en los dos lugares (decisión del dueño).**
- **Junto al formulario**, si lo provocó un formulario: `static/app.js` (en `htmx:beforeSwap`, que
  ya desvía los avisos) lo pone justo después del `<form>` que hizo la petición, en un hueco
  `[data-form-notice]` que crea si falta. Es una sola regla genérica, sin trabajo por formulario. El
  aviso anterior de ese formulario se reemplaza.
- **En la esquina fija** en el resto de los casos (un botón suelto, un trabajo): `#notices` pasa a
  ser un contenedor fijo, arriba a la derecha en pantallas anchas y arriba a todo el ancho en móvil,
  con `z-50`. Se ve desde cualquier scroll.

**Cierre (decisión del dueño).** Un aviso de error o de advertencia queda hasta cerrarlo. Uno de
éxito desaparece solo a los 5 s; lo hace `app.js`, sin código inline. Se suman avisos de éxito
donde hoy una acción no confirma nada: "Build 3 creado", "Decisiones guardadas", "Modpack
vinculado".

**Textos (F17).** El mensaje crudo de un cliente HTTP nunca es el texto principal de un aviso. El
"Request has failed with status error 404: Not Found" se reemplaza por un texto en español según el
servicio y el estado: "Pterodactyl no encontró ese servidor", "Modrinth no respondió; intenta de
nuevo", etc.

**Detalle plegable (decisión del dueño).** Bajo el texto va un `<details>` "Ver detalle" con:
- el servicio y el estado HTTP;
- el mensaje del servicio externo, **saneado**: sin cadenas de consulta en las URLs (las URLs
  firmadas de Wings llevan un token), sin cabeceras ni tokens, recortado a 500 caracteres;
- el `request_id` del log.

El detalle crudo completo sigue solo en el log.
- **Los pasos de los trabajos se traducen** con un mapa `JOB_STEP` junto a `JOB_KIND` (F34).
- **"Sobra" de un mod de solo cliente** dice "solo cliente: no va en el servidor" (F21).

## 5. Visual

**Página del modpack (decisión del dueño): Mods ancho y una columna lateral.**
- **Izquierda (2/3 en `xl`):** la búsqueda y "por slug o id" arriba; debajo, el estado de la
  resolución ("Resuelto hace 5 min · 10 archivos", con el aviso de vieja si corresponde, y
  Resolver); después, los problemas, sugerencias y "Decidir varios" si hacen falta; al final, la
  lista de Mods.
- **Derecha (1/3):**
  1. Builds, con "Crear build", la lista y comparar.
  2. **Servidores vinculados**, una sección nueva de solo lectura: cada servidor con su estado y
     un enlace "Simular" a su sección Actualizar.
  3. Canales.
  4. Archivos locales, plegados si no hay.
- El encabezado conserva "Actualizaciones", renombrado **"Buscar actualizaciones de mods"** para
  no confundirlo con actualizar un servidor (F42), y "Todos los modpacks".
- Debajo de `xl` todo va en una columna, en ese mismo orden.

**Página del servidor (decisión del dueño): estado arriba y acciones al lado.**
- **Arriba, una línea de resumen:** modpack (enlace), build instalado (el de la última
  actualización real terminada, o "sin actualizar desde el panel"), el recomendado, el último
  escaneo y "coincide" o "N diferencias".
- **Izquierda (2/3):** la comparación con el build.
- **Derecha (1/3):**
  1. Actualizar.
  2. Modpack: vincular, o el formulario nuevo del §2.
  3. Escaneo.
  4. Importar, siempre visible (decisión del dueño). Si la comparación no tiene jars que el pack no
     explique, dice "No hay nada que importar: el modpack ya explica todos los jars del último
     escaneo" y el botón queda deshabilitado con ese motivo. Sin escaneo, dice "Escanea el
     servidor para saber qué importar".

**Para todas las páginas:**
- Una sección vacía (pins, archivos locales) se pliega a una línea con su acción (F06).
- Cada sección tiene una sola acción primaria (verde); el resto es secundario.
- La simulación terminada muestra el bloque de cambios y deja la lista de jars en un `<details>`
  (F33).
- No hay paleta, tipografía ni librerías nuevas.

## Seguridad y datos

- Las rutas nuevas que escriben (`POST /servers/{id}/modpack`) pasan la tabla de roles. Las de
  lectura (`…/versions`) son de editor.
- `ModpackResolution` es nuevo y opcional para el código. Si no existe, la página se ve como hoy.
  `icon_url` es opcional. No hay migración: los dos se llenan con el uso. Un rollback de la imagen
  ignora la colección nueva y el campo extra.
- El texto de Modrinth (descripciones, títulos, números de versión) se escapa siempre, y los
  enlaces se arman con ids validados o URLs de esquema `https` del catálogo.
- Las escrituras a Pterodactyl no cambian: todo lo nuevo solo lee el panel.

## Tests

- **Unidades puras:**
  - `deduce_target`: mayoría, empate → `None`, loaders que no votan, sin jars;
  - `request_digest`: estable ante el orden, cambia con pins, decisiones, canales y reemplazos.
- **Mongo:**
  - `ModpackResolution`: upsert y lectura;
  - `icon_url` presente y ausente.
- **Servicio:**
  - `bundle_for_server`: de operador; 409 si ya tiene pack; audita cada paso; una importación
    fallida deja el pack vinculado y lo dice;
  - el rechazo de F26 en sus dos casos.
- **Panel:**
  - la lista única: fila, ficha, origen, dependencia con título, lado, versión → nueva;
  - el selector de versiones;
  - "Ya está" fuera de banda;
  - el formulario del servidor con y sin escaneo;
  - `HX-Trigger` en la fila terminada y ausente en la activa;
  - `#server-main` con `hx-select`;
  - resolución guardada vigente y vieja;
  - "Crear build" deshabilitado con mods por decidir;
  - los avisos sin el texto crudo del cliente;
  - `JOB_STEP`;
  - las cadenas de Modrinth escapadas.
- **Decisiones antes del build:**
  - `BuildBlocked` sigue siendo un 409 para `/v1`;
  - el diálogo lista los pendientes por título;
  - "Decidir y crear build" guarda y construye;
  - un fallo posterior se muestra dentro del diálogo.
- **Formulario del editor:** crea el pack, no vincula ni importa, y lo dice.
- **Avisos:**
  - el detalle saneado (una URL con `?token=` sale sin la consulta, con tope de 500 caracteres y
    con el `request_id`);
  - los avisos de éxito marcados como tales, para que `app.js` los cierre.
- **Las dos páginas:**
  - "Servidores vinculados" en el pack;
  - la línea de resumen del servidor (build instalado, recomendado, diferencias);
  - Importar con jars sin explicar, y deshabilitado con "No hay nada que importar" sin ellos.
- **Lo que es JS** (el aviso junto al formulario, el cierre a los 5 s): el barrido visual con
  Playwright, que provoca un error de formulario y uno de botón suelto.
- Los barridos de roles y de autenticación cubren solos las rutas nuevas.
- El barrido visual suma la ficha abierta y el servidor sin modpack, y tiene que terminar sin
  problemas.

## Verificación con uso real

Al terminar, se repite el recorrido de `docs/research/2026-10-04-usabilidad-panel.md` en la
instancia local descartable: Modrinth real y Pterodactyl simulado con jars reales. Los mismos
escenarios y anchos. Se mide de nuevo la tarea de F36. Lo encontrado, lo aprendido y lo que no hay
que repetir se agrega a ese documento, como ronda 2.

## Fuera de alcance (ronda 2)

- El tablero "necesita atención".
- Móvil: navegación compacta y tablas como tarjetas.
- Selectores de la versión de Minecraft y del loader al crear un pack (F09).
- Advertir bajas de versión (F27).
- El diálogo de actualización directa que advierte lo que la simulación encontraría (F28).
- Enlazar URLs en changelogs (F29).
