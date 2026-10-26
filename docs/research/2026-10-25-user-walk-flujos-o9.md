# User walk: los flujos del panel (O9)

**Fecha:** 2026-10-25 · **Pedido:** O9 de `docs/research/2026-10-21-observaciones-ronda-3.md` («agrupar
las acciones parecidas, ordenar los procesos para que se sigan solos y mejorar la usabilidad en
general», con un user-walk antes de diseñar) · **Qué es:** hipótesis de un agente que camina el panel
leyendo su código (plantillas, rutas del panel, `src/web/help.py`, servicios). No son datos de
usuarios reales.

## Propósito y método

Propósito: dar al dueño la lista de lo que cuesta en cada flujo, con evidencia, para que decida la
forma antes de la spec de O9.

Método (skill `user-walk`): para cada flujo, el camino ideal en pasos; después cada demora,
interrupción, tropiezo, falla y mal uso; al final un premortem. Cada hallazgo lleva su evidencia
(`archivo:línea`), su severidad y una propuesta concreta.

- **Severidad.** *Alta*: se pierde trabajo o información, o el panel hace otra cosa que la que dice.
  *Media*: un paso que se pierde, se repite o se adivina. *Baja*: fricción o texto confuso.
- **Ancho.** «Teléfono» es 360 px; las rejillas del panel pasan a una columna bajo `xl` (1280 px) en
  el pack y bajo `lg` (1024 px) en el servidor.
- **Lo que no se hizo:** no se corrió el panel ni el barrido visual; lo visual se dedujo de las clases.
  La comprobación real más barata: el dueño en `scripts/demo.sh` con un teléfono, y
  `scripts/visual-sweep.sh` para las capturas.

**Actores.** Operador (escribe en servidores), editor (arma packs y builds) y quien abusa (un editor
que prueba acciones de operador; ya lo paran los servicios). Supuesto: un solo operador, el dueño, y
pocos editores.

## 1. Crear un modpack y agregar mods

**Camino ideal.** 1) Modpacks → «Nuevo modpack» (nombre, versión, loader) → la página del pack.
2) Busca en Modrinth y pulsa «Agregar» en cada resultado. 3) «Resolver». 4) Decide lo pendiente
(flujo 3). 5) «Crear build».

| # | Hallazgo | Evidencia | Sev. | Propuesta |
|---|---|---|---|---|
| W1 | Bajo 1280 px la columna lateral (Builds, Servidores, Contenido, Canales) queda **debajo de toda la lista de mods**. Con 200 mods, «Crear build» está a cientos de filas, en teléfono y en tablet. | `modpacks/detail.html:25-52` (`xl:grid-cols-3`) | Media | D1 |
| W2 | Agregar, quitar o cambiar un mod desde «Todos» deja la resolución vieja: las pestañas dicen «Resuelve para ver lo pendiente» hasta otro clic. La misma acción desde «Con problemas» o el diálogo del build sí resuelve sola. Dos modelos para la misma acción. | `routers/web/modpacks.py:255-257`; `_mods.html:39`; `_resolution.html:31-33` | Media | D2 |
| W3 | La guía dice que «Build creado» espera a «Pack resuelto», pero «Crear build» funciona siempre: resuelve solo y abre el diálogo si falta algo. El paso «Resolver» sobra o la guía miente. | `library/onboarding.py:79-80`; `_builds.html:12-15`; `routers/web/modpacks.py:975-1003` | Media | D2 |
| W4 | El paso «Buscar mods» lleva a `#mods`, que empieza **debajo** del buscador: el campo de búsqueda queda fuera de la vista. | `library/onboarding.py:141`; `modpacks/detail.html:29-45` | Baja | A5 |
| W5 | La búsqueda en vivo no muestra que está buscando: con Modrinth lento no aparece nada durante segundos. | `modpacks/detail.html:31-36`; `_search.html:16-19` | Baja | A12 |
| W6 | Tres acciones parecidas en el pack con alcances distintos: «Buscar actualizaciones de mods» (cabecera), «Resolver de nuevo» y «Actualizar desde Modrinth» (sobre los mods). Nada dice cuál usar cuándo. | `modpacks/detail.html:9`; `_resolution.html:17-21` | Baja | D6 |
| W7 | Un mod que vino con el pack importado no se puede quitar desde su tarjeta (solo los agregados a mano); la tarjeta no dice por qué ni qué hacer. | `_mods.html:189-196` | Baja | D12 |

**Demoras y fallas.** Resolver solo lee el catálogo; si falta algo encola un trabajo con su barra (ya
cubierto). Agregar con un puente activo ya encola (O6). Doble clic: los botones se desactivan mientras
viaja la petición (`static/app.js:370-386`).

## 2. Importar un modpack y decidir sus pendientes

**Camino ideal.** 1) Modpacks → «Importar modpack…». 2) Elige archivo (.mrpack, zip de Prism o de
CurseForge), URL de Modrinth o código de CurseForge. 3) Ve el avance del trabajo. 4) Lee el informe
(qué entró, qué no está en Modrinth, overrides). 5) «Abrir el modpack». 6) Decide lo pendiente y
revisa las versiones del pack ofrecidas.

| # | Hallazgo | Evidencia | Sev. | Propuesta |
|---|---|---|---|---|
| W8 | **El informe de la importación se pierde** si la persona recarga o sale de la página: solo vive en el fragmento que consulta cada 2 s. La página «Importar» no busca la importación en curso ni la última, y `/jobs/{id}` solo muestra el informe de una importación de **servidor**. Lo que no está en Modrinth y los overrides sin importar no se ven en ningún otro lado. | `_import_job.html:3-13`; `routers/web/modpacks.py:414-424`; `routers/web/activity.py:76` | Alta | A2 |
| W9 | «Mis trabajos» → «Ver detalle» de una importación de modpack lleva a `/actividad`, **una ruta que no existe** (404). El trabajo guarda `into`, no `bundle_id`. | `_my_jobs.html:9`; rutas en `routers/web/*.py` (no hay `/actividad`); `services/modpack_import.py:120,165,180` | Alta | A1 |
| W10 | Importar **dentro de un pack** («Contenido del pack»): la respuesta trae `HX-Trigger: job-started`, que recarga `#pack-main`, y el formulario con su `#import-result` está dentro. El avance desaparece y el `<details>` se cierra. El aviso del pack no sigue ese trabajo, así que la página tampoco se recarga al terminar. *Hipótesis*: comprobar con un test de navegador antes de arreglar. | `modpacks/detail.html:21,70-75`; `routers/web/modpacks.py:452`; `services/modpack.py:1467-1469` | Alta | A3 |
| W11 | Subir un zip de Prism de hasta 1 GiB no muestra avance de subida: el botón se desactiva y nada más, durante minutos. O6 pidió que nada largo deje sin saber si avanza. | `_import_form.html:8-17` | Media | A11 |
| W12 | El informe dice «N con otra versión elegida… ofrecida en su tarjeta» y «N fuera de las etiquetas… marcados en la lista», pero no dice cuáles ni hay filtro para encontrarlos entre 200 mods. | `_import_result.html:9-15`; `_mod_filters.html:6-11` | Media | D9 |
| W13 | Cuatro entradas para «importar», con nombres y resultados distintos: la página «Importar modpack», «Contenido del pack» en el pack, la casilla «Importar lo que tiene el servidor» al crear un pack desde un servidor, y «Importar el servidor». | `modpacks/import.html:9`; `modpacks/detail.html:70-76`; `servers/_new_modpack.html:44-47`; `servers/detail.html:95-126` | Media | D5 |
| W14 | Textos que dicen algo falso: «Reemplazar con un .mrpack o un zip de Prism…» (el formulario también acepta Modrinth y CurseForge); el subtítulo de «Importar modpack» no nombra CurseForge; la marca «del .mrpack» sale para todo mod importado (Prism, CurseForge, URL). | `modpacks/detail.html:72`; `modpacks/import.html:6`; `_mods.html:92`; `_mod_filters.html:10`; `mods/_card.html:83`; `_problem.html:166` | Baja | A8 |

## 3. Decidir lo pendiente (por decidir y problemas)

**Camino ideal.** 1) La guía o el tablero dicen «Decidir». 2) Llega a la pestaña con lo pendiente.
3) Elige en cada mod o usa «Todos al servidor». 4) «Guardar las N decisiones». 5) Arregla cada
problema desde su tarjeta. 6) «Crear build».

| # | Hallazgo | Evidencia | Sev. | Propuesta |
|---|---|---|---|---|
| W15 | El paso «Pack resuelto» de la guía ofrece «Decidir» → `?vista=decidir` aunque lo pendiente sean **solo problemas**: la persona llega a «No quedan mods por decidir». El tablero ya lo hace bien (problemas primero). | `library/onboarding.py:109-114`; `_pending.html:10-11`; contraste `library/attention.py:149-150` | Media | A4 |
| W16 | Se decide el lado en tres lugares: la pestaña «Por decidir», el select de cada fila y el diálogo del build. Los tres guardan lo mismo; el diálogo es un atajo útil. | `_pending.html:14-52`; `_row_selects.html:16-24`; `_decide_dialog.html:35-55` | Baja | Sin acción: los tres son el mismo dato y el diálogo evita un viaje. |

## 4. Fijar una versión y elegir su origen

**Camino ideal.** 1) Abre la tarjeta del mod. 2) Ve de dónde sale la versión (pack, a mano,
resolución, servidor) y cuál está «en uso». 3) «Usar esta» en otra, o fija una versión de la lista.
4) Si baja de versión, confirma. 5) Puede deshacer.

| # | Hallazgo | Evidencia | Sev. | Propuesta |
|---|---|---|---|---|
| W17 | **Cinco maneras de fijar**, con respuestas distintas: el select de la fila (con «Deshacer»), el select «Fijar una versión» + «Fijar» dentro de la tarjeta (sin «Deshacer»), «Usar esta» en los orígenes, «Usar la versión X» y «Fijar otra» en una tarjeta de problema; y dos de quitar («Quitar fijación» y la opción «sin fijar»). | `_row_selects.html:33-51`; `_versions.html:14-23`; `_mods.html:140-180,232-249`; `_problem.html:111-124` | Media | D4 |
| W18 | Las versiones de la tarjeta se piden a Modrinth cada vez que se abre («Cargando versiones…»), y el select de la fila, sin ellas, dice «ver versiones en la ficha»: hay que abrir la tarjeta para poder usar el select de afuera. | `_mods.html:80-81,182-185`; `_row_selects.html:41-43` | Baja | D4 |
| W19 | «Cómo funciona» y el «?» de orígenes listan tres orígenes; falta **«servidor»**, que la tarjeta muestra («servidor: Survival 1.2»). | `web/templates/help.html:66-73`; `web/help.py:30-32`; `library/pins.py:28`; `_mods.html:160-162` | Media | A6 |

**Interrupción.** El aviso con «Deshacer» queda hasta cerrarse; si la respuesta es el diálogo de baja,
no se pierde (`static/app.js` maneja `HX-Retarget #pin-dialog`). Bien.

## 5. Vincular un servidor, escanearlo y compararlo

**Camino ideal.** 1) Servidores → «Agregar» un servidor del panel. 2) En su página, «Escanear».
3) Vincular a un pack (o «Crear y vincular»). 4) Ver la comparación con el build. 5) Si sobran jars,
«Importar el servidor» y decidir sus candidatos.

| # | Hallazgo | Evidencia | Sev. | Propuesta |
|---|---|---|---|---|
| W20 | La página del servidor no sigue el orden del proceso. Con pack: Comparación, Actualizar, Modpack, Escaneo, Importar; bajo 1024 px la tabla de comparación (que lista también cada jar que coincide) va **antes** de todas las acciones. | `servers/detail.html:46-127`; `servers/_compare.html:44-51` | Media | D7 |
| W21 | Lo que devuelven «Escanear ahora», «Simular actualización» e «Importar» aparece en `#server-job`, **arriba de la página**, lejos del botón; en teléfono, fuera de la vista. Parece que el clic no hizo nada. | `servers/detail.html:18-20,86,118`; `servers/_update.html:16` | Media | A13 (y D7) |
| W22 | Vincular solo se hace desde el servidor. El pack dice «se vincula desde la página de cada servidor» y la guía del pack manda a la **lista** de servidores, donde hay que adivinar cuál. | `modpacks/detail.html:67`; `library/onboarding.py:146-149` | Media | D8 |
| W23 | Cambiar el select a «Sin modpack» o a otro pack y pulsar «Guardar» desvincula **sin confirmar**, y borra los orígenes «servidor» de ese servidor en su pack anterior. | `servers/_link_bundle.html:21-30`; `services/pterodactyl.py:159-168` | Media | D10 |
| W24 | «Una sesión típica» dice vincular y después escanear; la guía del servidor pone primero escanear (de ahí deduce versión y loader). | `web/templates/help.html:32`; `library/onboarding.py:122-125` | Baja | A7 |
| W25 | Tres «refrescar» en servidores con verbos distintos: «Refrescar desde el panel», «Escanear ahora», «Actualizar lista». | `servers/detail.html:15,86`; `servers/list.html:29,35` | Baja | D6 |

## 6. Crear un build, simular y actualizar un servidor

**Camino ideal.** 1) «Crear build» en el pack. 2) Al terminar, «Simular en X». 3) En el servidor, la
simulación muestra qué cambia. 4) «Ejecutar de verdad…», escribe el nombre, confirma. 5) La guía
queda en «Alta completa».

| # | Hallazgo | Evidencia | Sev. | Propuesta |
|---|---|---|---|---|
| W26 | **Se simula otro build que el recién creado.** «Simular» usa el build *recomendado* (o el último si no hay); un build nuevo no se recomienda solo. Con #1 recomendado, crear #2 y pulsar «Simular en X» simula #1; la guía cuenta «Simulado» solo con el *último* (#2), así que el paso no se cumple nunca. Nada en el camino apunta a «Recomendar». | `_builds.html:17-21,36-40`; `services/update.py:104-111`; `library/onboarding.py:120`; `routers/web/modpacks.py:496-502`; `modpacks/updates.html:42` | Alta | D3 |
| W27 | Dos caminos para actualizar que no se parecen: «Simular actualización» no deja elegir el build; «Actualizar…» sí, y muestra los cambios sin simular. El texto los explica, pero la persona elige entre dos botones con reglas distintas. | `servers/_update.html:13-53`; `routers/web/servers.py:355-366` | Media | D3 |
| W28 | «Crear build» en la página de actualizaciones, si el pack está bloqueado, da un aviso de error 409 genérico; el mismo botón en el pack abre el diálogo con lo que falta. | `routers/web/modpacks.py:1072-1083` contra `975-1003` | Media | A9 |
| W29 | «Simular» se ofrece a un **editor** en «Servidores vinculados» del pack y en el siguiente paso tras crear un build; llega a un botón deshabilitado. Es la clase K2 del user-walk del 2026-10-13, que quedó sin barrer aquí. | `modpacks/detail.html:61`; `_builds.html:17-21` | Media | A10 |
| W30 | «Buscar actualizaciones de mods» abre una página que muestra la revisión vieja; para revisar hay que pulsar otro botón casi igual, «Buscar actualizaciones». | `modpacks/detail.html:9`; `modpacks/updates.html:31` | Baja | D6 |

**Demoras.** La vista previa del diálogo directo espera hasta ~15 s con su indicador
(`servers/_update.html:42-46`); la simulación es un trabajo con barra. **Falla:** si la actualización
falla, el rollback A/B aparece en la misma sección (`servers/_update.html:55-101`). Bien.

## 7. Puentes (loaders secundarios)

**Camino ideal.** 1) En el pack, activa el puente en «Loaders secundarios» y guarda. 2) Busca; la
pestaña «Vía puente» muestra lo que corre por él. 3) Agregar un mod que solo corre por el puente abre
el diálogo «Agregar todo». 4) Cuando llega la nativa, «Pasar a nativa» con confirmación.

| # | Hallazgo | Evidencia | Sev. | Propuesta |
|---|---|---|---|---|
| W31 | «Loaders secundarios» vive dentro de la sección «Canales», cuyo «?» habla solo de canales. Quien busca el puente no lo encuentra por el título. | `modpacks/detail.html:78-81`; `web/help.py:24-25` | Baja | D12 |
| W32 | Modos de guardar distintos en la misma página: los selects de la fila guardan al cambiar; «Canales» y «Loaders secundarios» piden «Guardar»; los filtros de mods y de auditoría aplican al cambiar, los de trabajos piden «Filtrar». | `_row_selects.html:16`; `_channels.html:16`; `_bridges.html:28`; `_mod_filters.html:14`; `activity/audit.html:32`; `activity/jobs.html:8,38` | Baja | D12 |

Los diálogos del puente y del cambio de vía están bien: dicen qué entra y el riesgo, y piden
confirmar (`_bridge_dialog.html`, `_via_confirm.html`).

## 8. Trabajos y auditoría

**Camino ideal.** 1) «Mis trabajos» en la barra muestra lo propio en curso. 2) «Ver detalle» lleva a
donde se ve. 3) En Trabajos filtra y abre la tarjeta. 4) En Auditoría filtra quién hizo qué.

| # | Hallazgo | Evidencia | Sev. | Propuesta |
|---|---|---|---|---|
| W33 | La lista de trabajos solo tiene columna y filtro de **servidor**: un build, una revisión o una importación de un pack sale con «—», sin decir de qué pack es. | `activity/_job_parts.html:7,20-23`; `activity/jobs.html:10-18` | Media | D13 |
| (W9) | «Ver detalle» de una importación de modpack da 404 (ver flujo 2). | `_my_jobs.html:9` | Alta | A1 |

## 9. El tablero

**Camino ideal.** 1) Abre el panel: «Necesita atención» primero. 2) Cada tarjeta lleva a su acción.
3) Debajo, servidores, builds y trabajos recientes.

| # | Hallazgo | Evidencia | Sev. | Propuesta |
|---|---|---|---|---|
| W34 | La primera vez (sin packs ni servidores) dice «Todo en orden» y «No hay servidores registrados» / «No hay modpacks», sin un enlace para empezar. | `home.html:54-62,68,111` | Baja | D11 |
| W35 | La tarjeta de un pack con problemas dice «Decidir», igual que la de mods por decidir; los problemas no se deciden, se arreglan. | `library/attention.py:148-151` | Baja | D12 |

## Premortem

*Salió, y alguien lo dejó a la mitad. ¿Por qué?* Subió el zip de Prism y esperó sin ver avance (W11).
Fue a otra página; al volver, el informe ya no estaba (W8), y «Ver detalle» dio 404 (W9). En el pack
vio «N versiones ofrecidas» sin saber cuáles (W12). Creó el build #2 y pulsó «Simular en Survival»;
la simulación dijo «Pasaría al build 1» y la guía siguió sin marcar «Simulado» (W26). En el teléfono,
el resultado salió arriba, fuera de la vista (W21). Se rindió.

## Decisiones para el dueño

Cada una cambia lo que la persona ve. La opción recomendada va primero. Los precios son en sesiones
de agente (una sesión ≈ un lote con sus tests).

**D1. Dónde queda «Crear build» bajo 1280 px (W1).**
- **A (recomendada): Builds arriba de los mods en una columna.** En teléfono: guía, Builds (botón y
  último build), Mods, y el resto debajo. ~½ sesión.
- **B: pestañas de página** «Mods · Builds · Servidores · Ajustes» (Canales, Puentes, Contenido,
  Archivos). En teléfono cada pestaña es corta. ~1–2 sesiones; cambia los anclajes de la guía.
- **C: como está**; la guía ya salta a `#builds`. Gratis.

**D2. Resolver solo (W2, W3).**
- **A (recomendada): toda acción sobre los mods resuelve sola desde el catálogo**, como ya hacen las
  tarjetas de problema; «Resolver de nuevo» queda para pedirle a Modrinth. La guía pierde el paso
  «Resolver» y deja «Decidir». Ejemplo: agregas Sodium y la pestaña «Por decidir» ya dice «1».
  ~1 sesión; cada acción tarda lo que tarda resolver local.
- **B: aviso fijo** «El pack cambió: Resolver» junto a lo que cambió, con el botón ahí. ~½ sesión.
- **C: como está.** Gratis.

**D3. Qué build se simula y se instala (W26, W27).** *Contiene un defecto:* la guía y la acción no
cuentan el mismo build.
- **A (recomendada): simular elige el build**, como el diálogo directo, con el último preseleccionado
  si es más nuevo que el recomendado; «Simular build 2 en Survival» lleva ese número; la guía cuenta
  el build simulado. Ejemplo: tras crear #2, «Simular build 2 en Survival» → «Pasaría al build 2».
  ~1 sesión.
- **B: el siguiente paso es «Recomendar y simular»**: recomendar #2 y simularlo en un clic; la guía
  agrega el paso «Recomendado». ~½–1 sesión; recomendar pasa a ser parte del camino.
- **C: solo los textos**: «Simular el recomendado (#1)» y la guía cuenta el recomendado. ~¼ sesión;
  el build nuevo sigue sin simularse hasta recomendarlo a mano.

**D4. Un solo lugar para fijar (W17, W18).**
- **A (recomendada): la tarjeta es el lugar**: un bloque «Versión» con los orígenes, la lista de
  versiones y «Usar esta» en cada una, todos con «Deshacer»; la fila muestra solo una marca («fijado
  1.2 · pack»). «Fijar otra» de un problema abre ese bloque. ~1 sesión.
- **B: la fila es el lugar**: el select de la fila lista orígenes y versiones; la tarjeta solo informa.
  ~1 sesión; un select largo en el teléfono.
- **C: como está**, pero con «Deshacer» en todas. ~¼ sesión.

**D5. Un solo lugar para importar un pack (W13).**
- **A (recomendada): la página «Importar modpack» es la única**, con «Crear un pack nuevo / Reemplazar
  el contenido de: [pack]»; «Contenido del pack» queda como un enlace a ella con el pack elegido.
  «Importar el servidor» se renombra «Traer los mods del servidor» (es otra cosa). ~1 sesión; de paso
  arregla W10.
- **B: se mantienen los lugares** con los mismos nombres y el mismo informe. ~½ sesión.
- **C: como está.** Gratis.

**D6. Los verbos «actualizar», «refrescar», «buscar» (W6, W25, W30).**
- **A (recomendada): un verbo por cosa.** Servidor: «Simular instalación» / «Instalar build…».
  Catálogo: «Traer de Modrinth». Listas: «Recargar». «Buscar actualizaciones de mods» empieza la
  revisión y abre su página con el avance. ~½ sesión; cambia textos de la ayuda.
- **B: solo el servidor**: «Actualizar» queda únicamente para instalar un build; lo demás se
  renombra. ~¼ sesión.
- **C: como está.** Gratis.

**D7. La página del servidor en el orden del proceso (W20, W21).**
- **A (recomendada): Modpack → Escaneo → Comparación → Actualizar → Importar**, y el resultado de
  cada trabajo bajo su propio botón. La comparación muestra solo las diferencias, con «ver los N que
  coinciden» plegado. ~1 sesión.
- **B: el orden actual**, con la comparación plegada bajo 1024 px y el resultado arriba (A13). ~½
  sesión.
- **C: como está** (A13 igual se hace). Gratis.

**D8. Vincular desde el pack (W22).**
- **A (recomendada): «Vincular un servidor» en «Servidores vinculados»**, un select con los servidores
  sin pack (operador); la guía del pack apunta ahí. ~½ sesión.
- **B: la guía lleva a la lista de servidores filtrada a los que no tienen pack.** ~¼ sesión.
- **C: como está.** Gratis.

**D9. Encontrar lo que dejó la importación (W12).**
- **A (recomendada): un filtro «Versión»** (ofrecida del pack, fuera de etiquetas) en la lista, y el
  informe enlaza a él. Ejemplo: «12 con la versión del pack ofrecida → ver». ~½ sesión.
- **B: el informe nombra cada mod**, cada uno como enlace a su tarjeta. ~¼ sesión; largo con 50.
- **C: como está.** Gratis.

**D10. Desvincular (W23).**
- **A (recomendada): confirmar cuando el pack cambia o se quita**: «Survival deja Pack X: se borran
  sus 4 orígenes "servidor". ¿Seguir?». ~¼ sesión.
- **B: un botón aparte «Desvincular…»** con su diálogo; el select solo vincula. ~½ sesión.
- **C: como está.** Gratis.

**D11. El tablero la primera vez (W34).**
- **A (recomendada): estados vacíos con su acción**: «No hay modpacks. Crear o importar uno» y «No
  hay servidores. Agregar del panel». ~¼ sesión.
- **B: una tarjeta «Para empezar»** en «Necesita atención» con los dos pasos. ~½ sesión.
- **C: como está.** Gratis.

**D12. Coherencia pequeña (W7, W31, W32, W35).**
- **A (recomendada): todo junto**: «Loaders secundarios» como sección propia con su «?»; Canales y
  Loaders guardan al cambiar (con aviso y «Deshacer», como la fila); los filtros de Trabajos aplican al
  cambiar; la tarjeta del tablero dice «Arreglar» para problemas; la tarjeta de un mod del pack
  importado dice «vino con el pack: desactívalo o crea un reemplazo vacío». ~1 sesión.
- **B: elegir cuáles** de la lista. Según lo elegido.
- **C: ninguno.** Gratis.

**D13. Trabajos con su pack (W33).**
- **A (recomendada): la columna «Servidor» pasa a «Sobre»** (servidor o pack, con enlace) y el filtro
  suma los packs. ~½ sesión.
- **B: solo la columna**, sin filtro. ~¼ sesión.
- **C: como está.** Gratis.

## Arreglos sin decisión

Defectos: el panel hace otra cosa que la que dice, o pierde información. Cada uno empieza por un test
que falla.

- **A1 (W9).** «Ver detalle» de un trabajo sin pack ni servidor lleva a una ruta inexistente. Que
  lleve a `/jobs/{id}`; y la importación guarde el pack creado o `into` como `bundle_id` cuando lo
  sepa. `_my_jobs.html:9`.
- **A2 (W8).** El informe de una importación de modpack sobrevive: `/jobs/{id}` lo muestra como ya
  muestra el de un servidor, y la página «Importar» muestra la importación en curso o la última de la
  persona. `routers/web/activity.py:60-87`; `routers/web/modpacks.py:414-424`.
- **A3 (W10, hipótesis).** Importar dentro de un pack no debe borrar su propio avance: el trabajo
  entra en el aviso del pack (sus claves) o la respuesta no recarga `#pack-main`. Primero un test de
  navegador que lo muestre. `modpacks/detail.html:21,70-75`; `services/modpack.py:1467-1469`.
- **A4 (W15).** La guía ofrece «Decidir» hacia la pestaña que tiene lo pendiente: problemas primero,
  como el tablero. `library/onboarding.py:113`.
- **A5 (W4).** «Buscar mods» lleva al buscador (`#mods-section` o el campo), no debajo de él.
  `library/onboarding.py:141`.
- **A6 (W19).** «Cómo funciona» y el «?» de orígenes nombran el origen «servidor». Es texto que revisa
  el dueño: mostrarle el cambio. `web/templates/help.html:66-73`; `web/help.py:30-32`.
- **A7 (W24).** «Una sesión típica» en el mismo orden que la guía del servidor (escanear, luego
  vincular). Texto del dueño: mostrarle el cambio. `web/templates/help.html:32`.
- **A8 (W14).** Textos falsos de importar: el resumen de «Contenido del pack» nombra todo lo que acepta;
  el subtítulo de «Importar modpack» nombra CurseForge cuando está activo; «del .mrpack» pasa a «del
  pack» en la marca, el filtro, la ficha y la tarjeta de problema.
- **A9 (W28).** «Crear build» desde actualizaciones, si está bloqueado, lleva al pack con el diálogo
  abierto, como el botón del pack. `routers/web/modpacks.py:1072-1083`.
- **A10 (W29).** Ningún «Simular» se ofrece a un editor: «Ver» en su lugar, como el tablero.
  `modpacks/detail.html:61`; `_builds.html:17-21`.
- **A11 (W11).** La subida muestra su avance (porcentaje). Lo decidió O6 («nada largo sin saber si
  avanza»). `_import_form.html:8-17`.
- **A12 (W5).** La búsqueda muestra «Buscando…» mientras espera a Modrinth. Indicador de carga, como
  decidió O6. `modpacks/detail.html:31-36`.
- **A13 (W21).** El resultado de un trabajo del servidor se trae a la vista y se anuncia al llegar
  (desplazamiento y foco), donde sea que quede según D7. `servers/detail.html:18-20`.

**Estado (2026-10-25, la misma sesión):** hechos, cada uno con su test visto fallar, A1 (`fd1bec5`;
el `bundle_id` de una importación nueva sigue sin guardarse), A2 en su parte de `/jobs/{id}` (`f4c9b1e`;
la página «Importar» todavía no muestra la importación en curso ni la última), A4, A5 y A10 (`366f04f`;
un editor lee «Ver» donde un operador ve «Simular», como en el tablero), A9 (`f4c9b1e`: el pack abre el
diálogo con `?construir=1`) y A13 (`8e297a6`: `data-reveal` en `app.js`). Sin hacer: A3 (pide su test
de navegador primero), A6, A7 y A8 (textos que revisa el dueño), A11 y A12 (indicadores de O6).

**Estado (2026-10-26):** hechos el resto de A1 (una importación dentro de un pack guarda su `bundle_id`; la de un pack nuevo sigue llevando a `/jobs/{id}`, que muestra su informe), A2 (la página «Importar» muestra la importación en curso o la última de la persona), A3 (confirmada en el navegador: el barrido sin el arreglo da «la importación se cerró» y «perdió su avance»; el `<details>` de importar lleva `hx-preserve` y el aviso del pack sigue la importación por su `into`), A11 («Subiendo… N %» en el botón) y A12 («Buscando en Modrinth…»). A13 quedó comprobado en el navegador (sin `data-reveal`, el barrido da «llegó fuera de la vista»). Quedan A6, A7 y A8 (textos del dueño) y las 13 decisiones.

## Cuentas

35 hallazgos (W1–W35): **4 altos** (W8, W9, W10, W26), **17 medios**, **14 bajos**. De ellos, W16
queda sin acción. 13 decisiones (D1–D13) y 13 arreglos sin
decisión (A1–A13).

## Lo que este walk no cubre

- No se vio el panel: nada de esto es una captura. Falta el barrido visual a 360, 768 y 1280 px.
- No se caminó la API `/v1`, el login OIDC ni el rollback con un servidor real.
- No se lanzó un segundo caminante en contexto limpio (paso 6 de la skill); conviene antes de la spec.
