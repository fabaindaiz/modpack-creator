# User walk: O8 tanda 2, historia y notas

**Fecha:** 2026-10-26 · **Pedido:** el user-walk de la tanda 2 de O8 antes de su plan (d-7985ee-a99db7, spec
`docs/superpowers/specs/2026-10-25-o8-versiones-con-historia-design.md` §8: D2 historia, D3–D5 notas y error,
D10 roles, D11 editar y archivar, D12 changelog, D13 `/v1`) · **Qué es:** hipótesis de dos recorridos que leen
el código (el del agente y uno en contexto limpio que no vio esta lista); nada se corrió.

**Severidad.** *Alta*: se pierde trabajo o el panel muestra una historia falsa. *Media*: un paso que confunde
o tarda. *Baja*: fricción.

## Actores y metas

- **Editor (tester):** después de probar un build en un servidor, anotar si funcionó, pegar el error, marcar la
  versión de un mod que falló; volver días después y entender qué cambió.
- **Operador (el dueño):** leer la historia de un mod antes de decidir una versión, «Fijar la última que
  funcionó», archivar notas ajenas.
- **Mal uso:** un editor que archiva notas ajenas; un log con IPs, UUIDs o tokens; una historia enorme.

## Camino ideal

1. Abre la tarjeta de Iris en el pack → «Historia» se carga al abrirla: builds que lo traen, servidores que lo
   corren, cambios de fijación, orígenes pasados.
2. En la versión 1.7.2: «Agregar nota» → veredicto «falló», servidor Survival, texto, error pegado → la nota
   aparece en esa versión y en el build donde se vio.
3. En el build 15: «Agregar nota» → «Build 15: pantalla negra al entrar».
4. Días después, la tarjeta sugiere «Fijar la última que funcionó (1.6.4)» → el diálogo de baja dice qué nota lo
   respalda → confirma.
5. Despliega «Changelog» de una versión → se pide a Modrinth en ese momento.
6. Edita su nota (queda «editada», con la anterior a un clic) o la archiva.

## Lo que sale mal

«C» = recorrido en contexto limpio; «A» = el del agente.

| # | Qué pasa | Evidencia | Prob. | Sev. | Fin |
|---|---|---|---|---|---|
| N1 | **El `past` se borraría solo** (C): todo camino que escribe un registro de fijación lo reconstruye desde cero, así que «Usar esta», «Quitar fijación» o un escaneo vaciarían lo guardado. | `services/modpack.py:165-166` (`_with_origin`), `:653`, `:661`, `:833`; `library/pins.py:189` | alta | Alta | `past` viaja en cada constructor; un test por camino de escritura |
| N2 | **La spec nombra mal dónde se reemplaza un origen** (C): `_put` solo arma la vista. Los reemplazos reales son `_with_origin` y `server_origins`; también se quitan orígenes (un servidor que vuelve a la fijada, desvincular). | `library/pins.py:97-104`, `:194-215`, `:216` | alta | Alta | el plan nombra esos sitios; quitar también pasa a `past` |
| N3 | Un servidor que va y vuelve entre versiones llena el tope de 20 y empuja fuera la versión original del pack (C). | `server_origins` | media | Media | sin duplicados por (fuente, servidor, versión); al menos una entrada por fuente |
| N4 | **La auditoría no dice «de qué versión a cuál»** (C): `bundle.pin` guarda solo `version_id`; importar guarda conteos; lo que escribe un escaneo no se audita. | `services/modpack.py:620-623`, `:722`; `services/pterodactyl.py:213,363` | alta | Alta | desde ahora se guarda `from` (id y número); lo anterior dice «antes: desconocida» |
| N5 | **Escaneos de otro pack** (C): un `ServerScan` no guarda su pack; un servidor que cambió de pack trae a la historia versiones que este pack nunca corrió, y desvincular hace desaparecer lo pasado. | `model/pterodactyl/scan.py:10` | media | Alta | los escaneos nuevos guardan su pack; los viejos solo si el servidor sigue vinculado, marcados «servidor vinculado ahora» |
| N6 | **Abrir la historia sería lento** (A, C): leer todos los builds con su lock entero, todos los escaneos con todos sus archivos y la auditoría sin índice, en cada apertura. | `database/modpack.py:332`; `model/audit.py:25-30` | alta | Media | proyecciones por proyecto (como `scan_latest_identifying`), consulta de auditoría indexada por `details.project_id`, carga perezosa al abrir |
| N7 | **Una nota a medio escribir se pierde cuando `#mods` se redibuja** (A, C): toda acción de la tarjeta, y canales y puentes, reemplazan `#mods`. | `_mods.html:143,173`; O9 D12 | media | Alta | **dueño, P1** (dónde vive el formulario) + borrador local |
| N8 | **Una nota enviada con la sesión vencida se pierde** (A, C): la sesión dura 12 h; el POST lleva al login y la página se va. También al cerrar la pestaña. | `main.py:122`; `static/app.js:69-72` | media | Alta | borrador en `localStorage` (por pack y destino), restaurado al volver; se borra al guardar |
| N9 | Un error de más de 64 KiB: cortarlo pierde la causa («Caused by» está al final) o rechazarlo pierde el pegado. | D5 | media | Alta | **dueño, P2** |
| N10 | **Un log trae datos privados** (A, C): IPs y UUIDs de jugadores, tokens de launchers o webhooks; sale por `/v1`. | — | media | Alta | **dueño, P3** (qué se enmascara); nunca va a Discord; se muestra en `<pre>` escapado |
| N11 | **El changelog ya se guarda** (C): la lectura de la comparación guarda en el catálogo lo que trae, y D12 dice «sin guardarlo». Además espera hasta 10 s, y un `toggle once` no reintenta. | `services/modpack.py:2236-2246`, `:83` | — | Media | **dueño, P4**; «Reintentar» como en `mods/_versions.html` |
| N12 | Quitar un mod borra su registro de fijación, y con él su `past`; las notas (colección propia) sobreviven y reaparecen al volver a agregarlo (C). | `database/modpack.py:156` | media | Media | **dueño, P5** |
| N13 | **«Fijar la última que funcionó» elige mal** (C): un «funcionó» de otra versión de Minecraft o loader; uno del build entero donde el mod no se probó; una versión con un «falló» posterior; otra vía (nativa o puente); sobre el piso no hay confirmación y fija en silencio. | `services/modpack.py:752` | media | Alta | solo el destino actual y la misma vía; manda el último veredicto de cada versión; una nota del build cuenta si ninguna del mod la contradice; el diálogo dice qué nota lo respalda, también sin baja |
| N14 | Doble envío: dos notas iguales (C). | — | media | Baja | botón desactivado en vuelo (existe) + clave de idempotencia en el formulario |
| N15 | Dos ediciones de una nota a la vez: gana la última y el «texto anterior» guardado es el equivocado (C). | — | baja | Media | `rev` como en `bundle_pin_record_set`; 409 «la nota cambió» |
| N16 | Archivar o editar la nota de otro: si lo chequea la ruta, una ruta nueva lo salta; si el autor se compara por email, falla (C). | AGENTS.md (roles) | media | Alta | `operator_only` en el servicio; autor por `iss`+`sub`; filas en «Acciones y roles»; barrido de rutas |
| N17 | Una historia larguísima: 200 builds y escaneos diarios (C). | — | media | Media | solo los puntos donde cambió la versión, paginado |
| N18 | Un pack borrado deja notas huérfanas (C). | — | baja | Baja | se archivan con el pack |
| N19 | Una nota archivada sigue contando para «la última que funcionó» (A). | — | media | Media | las archivadas no cuentan |
| N20 | Muchas notas en un build: la página crece sin fin (A). | — | media | Baja | las 5 últimas; «Ver todas» |
| N21 | `/v1` lista notas con su error entero: respuestas enormes (A). | — | baja | Baja | la lista sin `error`; el detalle lo trae |
| N22 | La nota de un servidor que luego se borra o renombra (A). | — | baja | Baja | la nota guarda el nombre del servidor de ese momento |
| N23 | Las notas junto a cada cambio en la comparación (B4) llegan con esta tanda (A). | plan tanda 1 | — | — | tarea propia |

## Premortem

Salió y el dueño perdió trabajo o confió en una historia falsa. Por qué:

1. «Usar esta» vació el `past` (N1): la historia no mostró la versión original del pack, y el dueño bajó a otra
   creyendo que siempre había estado ahí.
2. «Fijar la última que funcionó» tomó un «funcionó» de la versión anterior de Minecraft o de un build donde el
   mod no cargó, y el diálogo no dijo qué nota lo respaldaba (N13): un mundo roto.
3. Dos horas de investigación de un crash se perdieron por la sesión vencida o un redibujo (N7, N8).
4. El error cortado conservó la cabeza y perdió el «Caused by» (N9): la nota culpó al mod equivocado.
5. Escaneos del pack anterior del servidor mostraron versiones que este pack nunca tuvo (N5).
6. La historia tardaba segundos en packs viejos (N6) y nadie anotó nada.

## Decidido por el dueño (2026-10-26, d-7985ee-a68b5c)

- **P1** (N7): un diálogo «Agregar nota», fuera de `#mods`, con borrador local.
- **P2** (N9): los primeros 48 KiB y los últimos 16 KiB, con la marca de lo cortado y un aviso antes de guardar.
- **P3** (N10): se enmascaran tokens e IPv4, con un aviso en el campo; los jugadores se mantienen.
- **P4** (N11): el changelog queda en el catálogo (corrige la letra de D12; menos requests).
- **P5** (N12): la historia sobrevive a quitar y volver a agregar el mod; la fijación no vuelve.

Y la forma, antes del plan (d-7985ee-16be41): una página del build (T1); la historia en la tarjeta y en la ficha (T2), solo con lo que cambió, de a 20 (T3); «estable» marca un build (T3b, corregido por el dueño); «Recomendar el último estable» y «Fijar la versión del último build estable» (T4); «estable» y el veredicto en la lista (T5); las notas en la comparación (T6).

Lo demás lo decide el agente como dice «Fin», y cada caso se vuelve un test que falla primero en el plan de la
tanda 2.

## Lo que este walk no cubre

El aspecto (barrido visual cuando exista), logs reales de Minecraft (formato del crash report de Fabric y
NeoForge: SUPUESTO que el «Caused by» va al final), y cuántas notas escribe de verdad el dueño. La comprobación
real más barata: el dueño anotando un build en `scripts/demo.sh`.
