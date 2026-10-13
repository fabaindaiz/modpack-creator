# Panel 0.3.1, tanda A: el estado de un mod en su fila, y auditoría y trabajos como tarjetas

**Fecha:** 2026-10-13 · **Origen:** observaciones 1 a 6 del dueño sobre la 0.3.1 (i-7985ee-44060c en
`docs/roadmap.md`) · **Decisiones:** revisión interactiva con el dueño, 2026-10-13 (A1–A13 abajo,
filas en `docs/decisions.md`) · **Investigación relacionada:** `docs/research/2026-10-13-estilo-modrinth.md`
(el estilo va en la tanda B, no aquí).

## Qué se busca

Que el panel se recorra y se maneje sin estorbos: el detalle aparece cuando se pide, el estado de un
mod se cambia donde se muestra, y la auditoría y los trabajos se leen y se filtran sin conocer ningún
id.

**Se sabe que funciona cuando:** se cambia la excepción, el lado o la fijación de un mod sin abrir su
ficha; se encuentra «qué hizo X en el pack Y» eligiendo de listas, sin escribir ids; ningún detalle
largo rompe el ancho de la página, tampoco a 360 px.

## Decisiones (dueño, 2026-10-13)

| | Decisión |
|---|---|
| A1 | Las etiquetas cambiables de la fila de un mod son `<select>` nativos; elegir aplica el cambio sin recargar (htmx). |
| A2 | Tres selects: lado, canal (excepción) y fijación, esta última con las versiones. El origen («agregado», «del .mrpack») queda informativo. |
| A3 | Las versiones del select de fijación salen del catálogo al pintar, sin llamar a Modrinth: las 15 más nuevas más la fijada. Sin lista en el catálogo, el select ofrece el estado actual y «ver versiones en la ficha». |
| A4 | Los tres selects están siempre en la fila; sin estado, en gris («canal del pack», «sin fijar»). |
| A5 | Se quita la sección «Excepciones por mod» de Canales; el estado de una excepción («en uso…», «sin efecto…») va como texto chico bajo su select. |
| A6 | La pestaña Todos tiene una barra de filtros: nombre, Lado, Canal, Fijación y Origen, cada opción con su cantidad. |
| A7 | Las sugerencias son una cuarta pestaña, «Sugerencias», con su conteo y el contenido de hoy; el recuadro celeste se va y el resumen suma «· N sugerencias». |
| A8 | Auditoría y trabajos son listas de tarjetas expandibles (`<details>`) en todos los anchos. |
| A9 | La tarjeta de auditoría cerrada: cuándo, quién, la acción en español y el objetivo por nombre. Abierta: los detalles traducidos, con el nombre junto a cada id de mod o versión, y al final los ids crudos. |
| A10 | El evento guarda el nombre del objetivo al escribirse (`target.name`). |
| A11 | Filtros de auditoría: Persona y Acción, selects con conteo; Objetivo, un campo con sugerencias (`<datalist>`) de nombre, tipo y conteo; Desde y Hasta como hoy. |
| A12 | Un conteo respeta los demás filtros activos y no el suyo; una opción en 0 se ve en gris y se puede elegir. Vale para mods y auditoría. |
| A13 | La tarjeta de un trabajo, al abrirse, carga una vez el fragmento de su página (progreso, error, plan de la simulación o reporte de importación), los ids y «Abrir página»; `/jobs/{id}` se queda. |
| A14 | Una fijación cambiada desde la fila muestra un aviso con «Deshacer», que vuelve a la fijación anterior (o la quita). |

**Del agente** (reversibles, invisibles o ya convenidos): los nombres en español de acciones y claves
de detalle son la tabla de §6, que el dueño revisa con esta spec; la estructura y el layout son clases
de Tailwind, como el resto del panel (dueño, 2026-10-13), sin CSS propio; nada de JS inline (CSP,
d-7985ee-491c83), y el JS que haga falta va en un archivo estático; los conteos de auditoría salen de
una consulta `$facet`; los filtros viven en la URL y la paginación los conserva; índices de `audit`
según las consultas nuevas.

## 1. La fila de un mod (A1–A5, A14)

```
▸ [ico] Iris   agregado                                         1.8.0
        [En el servidor ⌄] [acepta beta ⌄] [fijado 1.7.2 ⌄]
        en uso: no hay una estable más nueva que la instalada
```

- Los selects salen del `<summary>`: van en una línea propia bajo el título, fuera del elemento que
  abre la ficha, para que tocarlos no la abra. A 360 px envuelven (`flex flex-wrap`).
- **Lado:** «Por decidir» (solo mientras no hay decisión), «En el servidor», «Desactivado». Elegir
  llama a `POST /modpacks/{id}/decisions` (existe) y resuelve de nuevo, como hoy. Se quitan los
  botones «¿Va en el servidor?» de la ficha.
- **Canal:** «canal del pack», «acepta beta», «acepta alpha». Elegir beta o alpha llama a `POST
  …/channel-exceptions` (existe); «canal del pack» llama a `DELETE …/channel-exceptions/{project}`,
  que deja de redirigir a Canales y responde `#mods`. El texto de estado sale de la resolución vigente,
  como hoy en `_channel_exceptions.html`, que se borra.
- **Fijación:** «sin fijar», luego las versiones (número y canal). Elegir llama a `POST …/pins`
  (existe): una baja abre el diálogo de confirmación de hoy (`#pin-dialog`); «sin fijar» llama a
  `DELETE …/pins/{project}` sin `hx-confirm` (el aviso con Deshacer lo reemplaza). La insignia «baja
  (piso X)» se queda junto al select.
- **Aviso con Deshacer (A14):** tras fijar o quitar desde la fila, `#notices` recibe «JEI fijado en
  19.60 · Deshacer»; Deshacer repite la llamada con la fijación anterior (o la quita), sin ruta
  nueva. Si deshacer es una baja, pide confirmación como cualquier otra.
- **Un mod que no está en el pack** (una dependencia): lado y fijación sí, como hoy desde su tarjeta;
  canal también (el servicio ya acepta lo que nombra la resolución).
- **Las versiones (A3):** `mod_list` reúne en una consulta las listas del catálogo
  (`version_lists_find` para todos los mods del pack) y sus versiones, filtradas como
  `project_versions` (versión de MC, loaders del tipo, canales del pack más la excepción del mod,
  `listed`), de la más nueva a la más vieja, recortadas a 15 más la fijada. La ficha sigue cargando su
  lista completa al abrirse.
- **Las fijaciones de mods que ya no están** siguen listadas bajo la lista, con su «Quitar fijación».

## 2. Los filtros de la lista de mods (A6, A12)

```
Nombre [sod______]  Lado [Todos ⌄]  Canal [con excepción (2) ⌄]
Fijación [Todas ⌄]  Origen [Todos ⌄]                    [Limpiar]
```

- Lado: Todos, Por decidir, En el servidor, Desactivado. Canal: Todos, canal del pack, con excepción,
  acepta beta, acepta alpha. Fijación: Todas, fijados, sin fijar, con baja. Origen: Todos, agregados,
  del .mrpack, dependencias. Nombre: contiene, sin distinguir mayúsculas ni acentos.
- Los parámetros van en la URL (`?vista=todos&lado=…&canal=…`); cambiar un select pide `#mods` con
  htmx (`hx-get`, `hx-push-url`). Se cuenta en memoria sobre las filas ya calculadas: cada conteo con
  los demás filtros aplicados y el suyo no.
- Filtrar no cambia los conteos de las pestañas.

## 3. La pestaña Sugerencias (A7)

Cuarta pestaña tras «Con problemas», con el contenido de hoy de `_resolution.html` (mod, «opcional
para» o «necesario para», «Agregar»), conteo en celeste; sin resolución vigente, «Resuelve para ver lo
pendiente», como las otras. El resumen sobre las pestañas suma «· N sugerencias» con su enlace.

## 4. La tarjeta expandible (A8)

Una macro de `_macros.html`, `ui.card_list`/`ui.card`, que usan auditoría y trabajos: `<ul>` de
`<li><details>`; el `<summary>` es una grilla de Tailwind que en `sm:` alinea las partes en columnas
(se lee como una tabla) y bajo `sm` las apila en dos líneas. El cuerpo abierto va con sangría y
`break-words`; nada en él puede ensanchar la página. Reemplaza `ui.table` en estas dos páginas y cierra
F40 para ellas.

## 5. Auditoría (A9–A12)

**El evento.** `AuditTarget` suma `name: Optional[str]`. Cada `audit.record` pasa el nombre que
conoce (pack, servidor, mod); si no lo conoce, `None` y se muestra el id. Base reseteada en el deploy
de la 0.3.1: no hay migración. `AuditEvent` sigue sin otros cambios.

**La lista.** Cerrada: `hace 5 min · Olga · Fijó una versión · Pack «Survival»`. Abierta: los
detalles como `<dl>` con las etiquetas de §6; un `project_id` muestra el título del catálogo y un
`version_id` su número (una consulta por página para todos); un dict anidado (`downgrade`) se escribe
como frase («19.57 → 19.51, confirmada»); una clave sin etiqueta se muestra cruda. Al final, en gris:
ids del objetivo, persona (`sub`) y petición.

**Los filtros.** `GET /audit?actor=&action=&target=&desde=&hasta=`. Persona (`actor.sub`, mostrado
por nombre) y Acción (agrupada por área con `<optgroup>`: Packs, Builds, Servidores, Reemplazos) son
selects; Objetivo es un `<input list>` cuyo `<datalist>` ofrece «Survival · pack (31)», y su valor
elegido se traduce a `kind:id`; un texto que no coincide con ninguna opción filtra por nombre exacto.
Una sola agregación con `$facet` devuelve la página (50 eventos, `before` como hoy) y los tres
conteos, cada uno con el `$match` de los demás filtros. Índices: `at`, `(actor.sub, at)`,
`(action, at)`, `(target.kind, target.id, at)`, a confirmar con `explain` en el plan.

## 6. Nombres en español

Acciones (el dueño las revisa aquí):

| Acción | Texto |
|---|---|
| `bundle.create` | Creó un pack |
| `bundle.update` | Editó un pack |
| `bundle.delete` | Borró un pack |
| `bundle.project.add` | Agregó un mod |
| `bundle.project.remove` | Quitó un mod |
| `bundle.project.replace` | Reemplazó un mod |
| `bundle.decision` | Decidió el lado |
| `bundle.pin` | Fijó una versión |
| `bundle.channel_exception` | Aceptó otro canal |
| `bundle.channel_exception.delete` | Quitó la excepción de canal |
| `bundle.replacement.delete` | Quitó un reemplazo del pack |
| `bundle.catalog_refresh` | Actualizó desde Modrinth |
| `bundle.update_check` | Buscó actualizaciones |
| `bundle.import.request` | Pidió importar un pack |
| `build.create` | Creó un build |
| `build.request` | Pidió un build |
| `build.recommend` | Recomendó un build |
| `replacement.set` | Creó un reemplazo |
| `replacement.delete` | Borró un reemplazo |
| `server.create` | Creó un servidor |
| `server.bundle` | Asoció un pack al servidor |
| `server.refresh` | Actualizó los servidores |
| `server.scan` | Escaneó un servidor |
| `server.scan.queue` | Pidió escanear |
| `server.import` | Importó un servidor |
| `server.import.request` | Pidió importar un servidor |
| `server.import.local` | Importó archivos locales |
| `server.import.confirm` | Confirmó una importación |
| `server.update` | Actualizó un servidor |
| `server.rollback` | Hizo rollback |

Claves de detalle: `project_id` Mod, `version_id` Versión, `server` En el servidor (sí/no),
`decisions` Decisiones, `channel` Canal, `downgrade` Baja, `cleared` Limpiado, `number` Build,
`dry_run` Simulación. El plan completa la tabla con las claves que encuentre (`grep` de cada
`audit.record`); una nueva sin etiqueta no rompe nada, se ve cruda.

Un test recorre las acciones que el código registra y exige su texto, así una acción nueva no queda
en inglés.

## 7. Trabajos (A13)

La misma tarjeta: cerrada, lo de la fila de hoy (tipo, servidor, estado, paso, quién, cuándo), y
mientras el trabajo está activo sigue sondeando (`/jobs/{id}/tr` pasa a devolver la tarjeta).
Abierta, `hx-get="/jobs/{id}/row"` con `hx-trigger="toggle once"` carga `_job_row.html` (ya existe),
más los ids y «Abrir página». Un trabajo activo y abierto no se vuelve a cerrar al sondear: el sondeo
reemplaza solo el `<summary>`.

## Errores

- Un select que falla (422, 409 que no es una baja, red): htmx no reemplaza nada con un 4xx/5xx, así
  que el select se quedaría mostrando lo elegido sin que se haya guardado. Cada select lleva su valor
  guardado (`data-saved`) y el JS estático lo restaura en `htmx:responseError` y `htmx:sendError`; el
  error se avisa como hoy. Lo mismo al cancelar el diálogo de una baja.
- Un filtro con un valor desconocido en la URL se ignora (como `vista` hoy).
- `name` vacío o ausente en un evento: se muestra `kind` e id.

## Tests (antes que el código)

- Fila: cada select con sus opciones y la marcada; elegir llama a la ruta correcta y responde `#mods`;
  una baja abre `#pin-dialog`; Deshacer vuelve a la fijación anterior; versiones del catálogo sin
  llamar a Modrinth y recortadas a 15 más la fijada; sin lista, «ver versiones en la ficha».
- Filtros de mods: cada filtro y su combinación; conteos que respetan los demás; valor desconocido.
- Sugerencias: la pestaña, su conteo, «Agregar», y que el recuadro ya no está.
- Auditoría: `target.name` en cada `record`; textos de cada acción; filtros y conteos con `$facet`
  (MongoDB real); datalist; paginación con filtros; detalle traducido y crudo para lo desconocido.
- Trabajos: tarjeta cerrada igual a la fila; abrir carga el fragmento; el sondeo no la cierra.
- `tests/test_routes_roles.py` sin filas nuevas (no hay métodos de servicio que escriban nuevos).
- Barrido visual: fila con los tres selects, filtros, pestaña Sugerencias, auditoría y trabajos
  abiertos y cerrados, a 360, 768 y 1280 px, claro y oscuro.

## Fuera de esta tanda

El estilo inspirado en Modrinth, el tablero, el móvil compacto en las demás páginas y F09 (tanda B);
los loaders secundarios (tanda C).
