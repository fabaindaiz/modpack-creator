# Diálogos accesibles con htmx en el panel (investigación, 2026-10-05)

Alcance: solo lectura del repo; fuentes web consultadas el 2026-10-05. Todo lo que no salió de una fuente primaria está marcado SIN VERIFICAR.
Nota de método: las páginas se leyeron con WebFetch (resumen por modelo pequeño), así que las citas son paráfrasis breves, no texto literal garantizado.

## Estado actual del repo (leído, no editado)
- `confirm_dialog` (`_macros.html:149`): `<dialog aria-labelledby=...>`, con `open` si `open_now=True` (diálogo de decisión del build, no modal).
- `app.js`: `[data-dialog-open]` llama `showModal()`; `[data-dialog-close]` llama `close()`; el cajón `#mod-drawer` llama `showModal()` en `htmx:afterSwap` cuando el target es `#mod-drawer-body`, guarda el opener, y en `close` vacía el cuerpo y devuelve el foco; clic en el backdrop cierra. `htmx:afterRequest` cierra el diálogo de un form.
- CSP: `default-src 'self'; script-src 'self'; style-src 'self'` (`src/library/web_security.py:6`).

## 1. `<dialog>`: `showModal()` frente a `open`

Fuentes: WHATWG HTML, interactive-elements (https://html.spec.whatwg.org/multipage/interactive-elements.html); MDN `<dialog>` (https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/dialog); MDN `showModal()` (https://developer.mozilla.org/en-US/docs/Web/API/HTMLDialogElement/showModal); caniuse (https://caniuse.com/dialog, https://caniuse.com/mdn-html_elements_dialog_closedby).

| Aspecto | `showModal()` | atributo `open` (o `show()`) |
|---|---|---|
| Resto del documento | inerte (todo salvo el diálogo y sus descendientes) | interactivo |
| Capa | top layer + `::backdrop` | sin top layer ni backdrop |
| Foco inicial | pasos de enfoque del diálogo: el elemento con `autofocus`; si no, el "focus delegate"; si no, el propio diálogo | con `open` puesto a mano no se ejecutan los pasos de `showModal()`; el spec avisa que el atributo a mano tiene "consecuencias extrañas" (SIN VERIFICAR el detalle exacto del foco inicial con `open` estático; se infiere del spec y de MDN) |
| Foco de retorno | guarda el "previously focused element" y lo restaura al cerrar | no guarda nada |
| Escape / `cancel` | close watcher: Escape dispara `cancel` (cancelable) y luego `close`; MDN: Escape cierra solo el último diálogo abierto | sin Escape (`closedby` por defecto `none` en no modales) |
| Aviso oficial | | MDN recomienda `show()`/`showModal()` y no el atributo `open`; el spec dice usar `close()`/`requestClose()` en vez de quitar `open` |

Más datos:
- `showModal()` lanza `InvalidStateError` si el diálogo ya está abierto como no modal (MDN). Consecuencia directa para el repo: llamar `showModal()` sobre un `<dialog open>` renderizado por el servidor lanza. Hay que quitar `open` del HTML y abrirlo por JS (o llamar `close()` antes).
- `autofocus`: MDN recomienda ponerlo en el elemento con el que el usuario interactúa primero, y si no hay otro, en el botón de cerrar. No usar `tabindex` en el `<dialog>`.
- `closedby` (`any` | `closerequest` | `none`): por defecto `closerequest` en modales y `none` en no modales (spec y MDN). caniuse: Chrome/Edge 134+, Firefox 141+, Opera 119+; Safari solo en Technical Preview; Safari iOS no lo tiene hasta la 27.2 (cifras de caniuse; global 73,39 %). Conclusión: `closedby="any"` (cierre al clic fuera) no es portable hoy en iOS; mantener el cierre por clic en el backdrop en `app.js`.
- Soporte de `<dialog>`: Chrome 37, Firefox 98, Safari 15.4 (caniuse; 96,77 % global); MDN la marca Baseline "widely available" desde marzo 2022. iOS Safari: versión SIN VERIFICAR en la página (equivale a Safari 15.4 por convención, no leído).
- Bloqueo de scroll: el spec y MDN no mencionan que `showModal()` bloquee el scroll de la página. No lo bloquea por sí solo (SIN VERIFICAR en fuente primaria; es comportamiento conocido). Patrón usual: `html:has(dialog:modal) { overflow: hidden }` en CSS (compatible con la CSP; SIN VERIFICAR soporte de `:has` y `:modal` aquí, ambos son Baseline por conocimiento, no leído).
- Bugs conocidos por navegador (iOS VoiceOver, foco de retorno en Safari, etc.): no se encontró una fuente primaria que los enumere (el artículo de Scott O'Hara de 2023 no detalla bugs). SIN VERIFICAR; que la barrida real en WebKit lo compruebe.

## 2. WAI-ARIA APG (Dialog Modal) y cajones laterales

Fuente: https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/ (consultada 2026-10-05).
- Etiqueta: `aria-labelledby` apuntando a un título visible, o `aria-label`. `aria-describedby` opcional y mejor omitido si el contenido es estructurado (listas, tablas).
- Foco inicial: dentro del diálogo. Contenido estructurado o grande: un elemento estático con `tabindex="-1"` al inicio (título o primer párrafo) para no saltar el scroll. Acción de alto riesgo: la opción menos destructiva.
- Tab/Shift+Tab cicla dentro del diálogo; Escape lo cierra.
- Foco de retorno: al elemento que lo invocó, salvo que ya no exista o el flujo sugiera un destino más lógico.
- `aria-modal="true"`: solo si el código impide toda interacción fuera y el estilo oculta lo de fuera. Con `showModal()` el navegador hace lo primero (inert); no hace falta añadirlo (SIN VERIFICAR que los lectores de pantalla actuales dependan de él).
- "Drawer"/side sheet: el APG no tiene un patrón propio; un cajón modal es un diálogo modal y se rige por este patrón. Un cajón no modal sería otro patrón (SIN VERIFICAR: no hay guía APG específica; se afirma por ausencia en la página leída).
- Contenido reemplazado por un swap: el APG no lo trata (SIN VERIFICAR / no cubierto). Criterio derivado de las reglas anteriores: si el elemento enfocado desaparece con el swap, el foco cae a `body` y se pierde el contexto del diálogo; hay que reenfocar a mano.
- Diálogos anidados: el APG de este patrón no los trata en lo leído. MDN: Escape cierra solo el último. Con `showModal()` un segundo modal se apila en el top layer y el primero queda inerte. SIN VERIFICAR el comportamiento del foco al cerrar el interior más allá de "vuelve al opener guardado".

## 3. htmx 2 y diálogos con CSP estricta

Fuentes: https://htmx.org/events/, https://htmx.org/docs/ (consultadas 2026-10-05); búsqueda en GitHub (resultados citados abajo).
- `htmx:afterSwap`: tras insertar el contenido; `detail.elt` y `detail.target`. `htmx:afterSettle`: tras el asentamiento (settle delay por defecto 20 ms, clase `htmx-settling`). `htmx:load`: por cada nodo nuevo, con `detail.elt`. `htmx:oobAfterSwap` para swaps fuera de banda.
- CSP: los atributos `hx-on:*` dependen de `eval()` (docs: `htmx.config.allowEval=false` los deshabilita) y chocan con una CSP sin `unsafe-eval`; los docs dicen que lo que se pierde puede reimplementarse con JS propio y el modelo de eventos de htmx. Es lo que ya hace el repo: delegación en `app.js` con `data-*`.
- Patrón que funciona con CSP estricta: listener global en `document` para `htmx:afterSwap` (o `htmx:load`) que busca `dialog[data-autoopen]` dentro de `evt.detail.elt` o del target y llama `showModal()`. Es la misma idea del cajón actual.
- El foco tras un swap: los docs de htmx no tratan `autofocus` tras un swap (lo que se leyó no lo menciona; SIN VERIFICAR que htmx lo reaplique). htmx conserva el foco del elemento con `id` estable cuando el swap lo reemplaza (comportamiento conocido; SIN VERIFICAR en esta lectura). `hx-swap="... focus-scroll:true"` controla solo el scroll del elemento enfocado.
- Un `<dialog autofocus>`/`open` insertado por swap: el HTML nuevo con `open` aparece no modal; `showModal()` después lanzará `InvalidStateError` (ver sección 1).
- Incidencias públicas encontradas (ejemplos de terceros, no del núcleo de htmx; relevancia indirecta):
  - un proyecto que abría diálogos al cargar tuvo que escuchar el evento de swap porque con htmx no se vuelve a disparar `DOMContentLoaded`: https://github.com/emergent-company/emergent.memory/pull/558. Ojo: en htmx 4 los eventos se llaman `htmx:after:swap`; este repo usa htmx 2.0.11 y `htmx:afterSwap` es correcto, pero se rompe si se sube a 4.
  - un modal sobre una región de toasts la deja inerte (`showModal()` vuelve inertes los botones de los avisos): https://github.com/ousiassllc/pitha-trador/issues/353. Relevante: el `htmx:afterRequest` del repo cierra el diálogo justo para que el aviso sea visible.
  - No se encontró en el repositorio de htmx un issue que trate específicamente `showModal` tras swap; SIN VERIFICAR que no exista.

## 4. Recomendación para el panel

Un solo patrón: el servidor renderiza siempre el `<dialog>` cerrado (nunca `open`), con título, `aria-labelledby`, y marca `data-autoopen`; `app.js` lo abre con `showModal()` desde un único manejador de `htmx:afterSwap`. Las confirmaciones disparadas por botón siguen con `[data-dialog-open]`, que ya llaman `showModal()`.

Reglas:
1. `confirm_dialog(open_now=True)` deja de emitir `open` y emite `data-autoopen`. Así el diálogo de decisión obtiene inert, backdrop, Escape, foco inicial y retorno nativos (cierra el hallazgo diferido). Antes de `showModal()`, `if (!dialog.open)`; si estuviera abierto no modal, `close()` primero.
2. Un solo manejador `htmx:afterSwap`: busca `[data-autoopen]` en `evt.detail.target` (y descendientes); guarda `opener = evt.detail.elt` (solo si no hay un modal abierto) en un `WeakMap` dialogo -> opener, abre, borra la marca para no reabrir en swaps posteriores. El cajón pasa a este mismo manejador (su `showModal()` ya existe; hoy depende de `target.id`).
3. Foco inicial: título del diálogo con `tabindex="-1"` si el contenido es largo o estructurado (cajón de ficha, lista de mods a decidir); en confirmaciones destructivas, `autofocus` en "Cancelar" (la opción menos destructiva, APG). En el cajón, no dejar el foco por defecto en "Cerrar" si hay más contenido: enfocar el título de la ficha.
4. Foco de retorno: al opener guardado si sigue en el DOM; si no (el swap lo reemplazó, como el botón "Crear build" tras `outerHTML`), a un destino lógico fijo (el contenedor `#builds` con `tabindex="-1"`, o el encabezado de su sección). El `htmx:afterRequest` que cierra el diálogo del form debe sumar este reenfoque, porque el nativo falla si el opener ya no existe.
5. Contenido reemplazado dentro del diálogo abierto (clic en otra ficha desde el cajón, reintentar el build): tras `afterSwap`, reenfocar el título nuevo (`tabindex="-1"`) y poner el scroll arriba (ya se hace). No cerrar y reabrir el diálogo: perdería el opener.
6. Cierre: Escape nativo (modal). Clic en el backdrop: mantener el manejador de `app.js` (no usar `closedby="any"` mientras iOS no lo soporte; se puede añadir como mejora progresiva sin dañar). Evitar `cancel.preventDefault()`.
7. Scroll: añadir en CSS (sin inline) `html:has(dialog:modal){overflow:hidden}` (SIN VERIFICAR su efecto en iOS; verificar en la barrida).
8. Anidados: evitar. Si la ficha del mod se abre desde el diálogo de decisión (hoy `mod_link` está dentro de `_decide_dialog.html`), el cajón se apila sobre el diálogo y el retorno de foco es al enlace del diálogo, que sigue existiendo: es válido, pero hay que probarlo. Alternativa más simple: cerrar el de decisión y abrir la ficha con un "volver" (decisión de producto, no técnica).
9. `aria-modal`: no añadir; `showModal()` ya bloquea el resto.
10. `aria-label` fijo del cajón ("Ficha del mod"): preferible `aria-labelledby` del título de la ficha para que el nombre del lector sea el del mod (el nombre cambia con el swap).

## Lista de comprobaciones para la barrida con Playwright
Para cada uno de los tres diálogos (decisión, confirmación, cajón), en Chromium, Firefox y WebKit, y a 360, 768 y 1280 px, claro y oscuro:
- Se abre tras la acción/swap y `dialog.matches(':modal')` es verdadero (no solo `open`): el de decisión incluido.
- `[open]` sin `:modal` no aparece en ningún diálogo (detecta la regresión de `open` estático).
- El resto del documento es inerte: Tab repetido nunca sale del diálogo; `document.activeElement` siempre dentro; un clic fuera del diálogo no activa nada salvo cerrar por backdrop.
- Foco inicial: `document.activeElement` es el elemento esperado (título, o "Cancelar" en destructivos), no `body`.
- Escape cierra solo el diálogo superior; tras cerrar, el foco está en el opener, o en el destino lógico cuando el opener ya no existe tras el swap (`#builds`). Nunca `body`.
- Nombre accesible: `getByRole('dialog', {name: ...})` encuentra cada uno; el cajón tiene el nombre del mod; árbol de accesibilidad sin `aria-modal` contradictorio.
- Swap dentro del cajón abierto (clic en otra ficha, "volver"): el diálogo sigue abierto, el foco va al título nuevo, scroll arriba, y el opener original sigue siendo el de retorno.
- Reintentar el build desde el diálogo de decisión (se reemplaza su contenido): sigue siendo modal, foco en el aviso o título, sin duplicar diálogos (`count == 1`).
- Una segunda carga del mismo swap no reabre un diálogo ya cerrado (la marca `data-autoopen` se consume).
- Clic en el backdrop cierra; clic dentro del contenido no.
- Scroll: con el diálogo abierto, la página de fondo no desplaza (`scrollY` constante tras rueda/teclado); tras cerrar, se restaura.
- Cierre tras el envío del form: el aviso resultante es visible y no queda inerte detrás de un modal.
- Teclado: Shift+Tab desde el primer elemento va al último; Enter/Espacio en "Cerrar" cierra.
- CSP: cero violaciones en consola (sin inline); ningún `hx-on`.
- Excepciones: ningún `InvalidStateError` en consola al abrir (cubre `showModal()` sobre un diálogo ya abierto).
- Lo que Playwright no cubre: lectores de pantalla reales (VoiceOver iOS, NVDA); SIN VERIFICAR el comportamiento ahí.

## Qué quedó sin verificar (resumen)
Foco inicial exacto con `open` estático; bloqueo de scroll nativo; soporte de `:has`/`:modal` y versión de iOS Safari para `<dialog>`; bugs de WebKit/VoiceOver; anidados en el APG; reaplicación de `autofocus` y del foco por htmx tras un swap; ausencia de issues de htmx específicos sobre `showModal`.
