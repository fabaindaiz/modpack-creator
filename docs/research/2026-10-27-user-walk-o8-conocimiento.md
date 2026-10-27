# User walk: O8 tanda 3, la base de conocimiento

**Fecha:** 2026-10-27 · **Pedido:** el user-walk de la tanda 3 de O8 antes de su plan (d-7985ee-a99db7, spec
`docs/superpowers/specs/2026-10-25-o8-versiones-con-historia-design.md` §8: D6 «Marcar como sospechoso», D7 dos
mods con rango opcional, D8 solo avisa, D9 global con los packs donde se vio, D10 roles, D13 `/v1`) · **Qué es:**
hipótesis de dos recorridos que leen el código (el del agente y uno en contexto limpio que no vio esta lista);
nada se corrió.

**Severidad.** *Alta*: un aviso falso o que falta lleva a un mundo roto, o se pierde trabajo. *Media*: un paso
que confunde o tarda. *Baja*: fricción.

## Actores y metas

- **Editor (tester):** cuando un build falla, encontrar el par culpable y marcarlo como sospechoso, con las notas
  que lo respaldan.
- **Operador (el dueño):** confirmar, acotar o descartar una marca, y confiar en los avisos al actualizar
  servidores reales.
- **Mal uso:** un script de `/v1` que manda ids inventados o `status=confirmed` siendo editor; una marca por
  corazonada; dos operadores a la vez.

## Camino ideal

1. Compara el build 12 (✔) con el 14 (✘) → junto a Iris, que subió, «Marcar como sospechoso».
2. El formulario trae Iris con «desde 1.7.0» sugerido a un clic (no llenado), pide el otro mod (a menudo uno que
   no cambió, Sodium), Minecraft y loader opcionales, una línea de resumen y las notas del build → la entrada
   queda «sospechosa», con este pack en «visto en».
3. El operador abre «Conocimiento», filtra por mod y estado, lee la entrada con sus notas y packs → «Confirmar».
4. Días después, en otro pack, agrega Iris → la tarjeta, el build y el tablero muestran «⚠ Conocida: Iris 1.7 ✕
   Sodium 0.6 (pantalla negra)»; nada se bloquea.
5. Sale Iris 1.7.3 que lo arregla → el operador pone «arreglado en 1.7.3» → los packs que ya pasaron dejan de
   ver el aviso; los que siguen en 1.7.2 lo siguen viendo.

## Lo que sale mal

«C» = recorrido en contexto limpio; «A» = el del agente. Evidencia comprobada por el agente en las líneas
marcadas.

| # | Qué pasa | Evidencia | Prob. | Sev. | Fin |
|---|---|---|---|---|---|
| K1 | **Un aviso hecho `Problem` bloquea el build** (C): `_refuse_blocked` rechaza cualquier `lock.problems` y el tablero los cuenta como bloqueo. Rompe D8. | `services/modpack.py:64-68` (comprobado); `library/attention.py:112` | media | Alta | lista propia de avisos, nunca en `lock.problems`; un test construye con una entrada confirmada |
| K2 | `Problem.kind` ya tiene «incompatible» (de Modrinth) con «Quitar»: un aviso de la base dibujado igual parece veredicto del resolvedor (C). | `library/resolution.py:309`; `_problem.html` | media | Media | plantilla y texto propios («conocida en la base»), otro nombre de tipo |
| K3 | **Rango comparado por número de versión** (A, C): Modrinth no numera con semver («mc1.21.1-0.6.0-fabric»). | `library/changes.py:22-31` usa fechas (comprobado), como los pisos | alta | Alta | por `date_published`, la regla del proyecto |
| K4 | **Ramas mezcladas** (C): Sodium 0.5.11 para 1.20.1 sale después que 0.6.0 para 1.21; con Minecraft vacío, un pack 1.20.1 cae «dentro». Avisos falsos todas las semanas. | `direction` da «branch» sin Minecraft ni loader en común (comprobado) | alta | Alta | el rango solo vale para versiones que comparten Minecraft y loader con su extremo; si no, «otra rama», ni dentro ni fuera |
| K5 | **La fecha del extremo no está en el catálogo de otro pack** (C): el catálogo guarda solo lo pedido para algún pack; calcularla al dibujar pediría a Modrinth. | `services/modpack.py:411-418` | alta | Media | al marcar se guardan id, número, fecha, Minecraft y loaders de cada extremo; dibujar no llama a Modrinth |
| K6 | **Cuántas consultas suma un aviso** (A, C): el lock no tiene fechas; en el tablero son todos los mods de todos los packs. | `LockEntry` sin fecha (`resolution.py:270`) | alta | Media | una lectura de la base por página, filtrada en memoria por mods; fechas del catálogo solo de los mods que coinciden; índices por cada mod; un test cuenta las llamadas |
| K7 | Los avisos sacados de la resolución actual desaparecen cuando está vieja, aunque el build vigente y el servidor sigan con el par (C). | `services/modpack.py:1650-1656` | media | Alta | se evalúa el build vigente y el último escaneo de cada servidor vinculado, no solo la resolución |
| K8 | **Las actualizaciones no avisan** (C): el chequeo diario, «Actualizaciones» y la vista previa de actualizar un servidor (`_changes.html`) no miran la base; una actualización segura entra a un par conocido sin un ⚠. La spec §4 no lo nombra. | `services/modpack.py:2408-2446` | alta | Alta | **dueño, K-Q3** |
| K9 | Una versión rota sola (`broken` en el modelo de la spec) con el piso puesto: la salida es un pin a la anterior, siempre sugerido con el diálogo de baja (C). | spec §3.5; D7 dice «dos mods» | media | Media | **dueño, K-Q4** (si existe la marca de un mod solo) |
| K10 | **Duplicados** (A, C): doble envío, A×B y B×A, dos editores marcando el mismo par. | `note.py:49,56` (las notas sí tienen `key`) | alta | Media | par guardado en orden fijo, `key` de idempotencia; al marcar un par que ya existe, «Sumarse a la existente» (agrega la nota y el pack) |
| K11 | Dos escrituras a la vez: un editor corrige el rango mientras el operador confirma (C). | `notes.py:66-67` (`rev` de las notas) | media | Media | `rev` y 409, como las notas |
| K12 | **Las sospechas llenan los avisos** (A, C): si avisan en todos los packs, la corazonada de un tester es global; si no avisan, una rotura real sin confirmar calla. | D8, D10 | alta | Media | **dueño, K-Q1** |
| K13 | **«Resuelto» es ambiguo** (C): resolver porque 1.7.3 lo arregló apaga el aviso para quien sigue en 1.7.2; una falsa alarma también quedaría «resuelta», como si hubiera sido real. | spec §3.3 | alta | Alta | **dueño, K-Q2** |
| K14 | **Los extremos del rango son ambiguos** (C): ¿«hasta» es la última rota o la primera buena? | D7 «hasta: aún no se sabe» | alta | Alta | **dueño, K-Q2** (las palabras del formulario) |
| K15 | Rango al revés o ajeno: «desde» después de «hasta», una versión de otro mod, un id que el catálogo no conoce por `/v1` (C). | D13 | media | Media | se valida al escribir: el extremo es del mod y desde ≤ hasta; escribir puede leer la versión de Modrinth (una lectura); un id desconocido, 422 |
| K16 | Un mod o una versión retirados de Modrinth: la página muestra ids (C). | — | baja | Baja | títulos y números guardados con la entrada (K5); la entrada sigue |
| K17 | **El otro mod no cambió** (A, C): no está en la lista de cambios, así que el formulario debe ofrecer cualquier mod del build que falló, con su versión de ese build sugerida. | `_changes.html` lista solo agregados, quitados y cambiados | alta | Media | el segundo mod se elige del lock del build que falló; nada se llena solo (D7) |
| K18 | Puentes: un mod de Fabric en un pack NeoForge por Connector; una marca con loader Fabric no avisa al pack puenteado (C). | `LockEntry.via` (`resolution.py:285`) | media | Media | se compara con los loaders de la versión (guardados, K5), no con el del pack; «vía Connector» en el aviso |
| K19 | Reemplazos: Sodium cambiado por Embeddium en todos los packs; una entrada de uno no avisa del otro (C). | `services/modpack.py:2453` | baja | Baja | no se maneja: son mods distintos |
| K20 | Las notas que respaldan una entrada se archivan: queda «confirmada» sin evidencia (C). | `notes.py:72-82` | media | Baja | «sin notas vigentes» en la entrada; no bloquea nada |
| K21 | **Privacidad** (C): la base es global y las notas guardan el error con jugadores y nombres de servidor (solo se enmascaran tokens e IPv4). | `library/notes.py:11-17` | media | Media | «Conocimiento» muestra veredicto, texto y pack de cada nota, nunca el error; el error queda en la nota de su pack; el resumen se escribe, no se copia del error |
| K22 | **Saltarse el rol** (A, C): un editor manda `status=confirmed` por `/v1`, o edita una entrada confirmada. | AGENTS.md (roles) | media | Alta | crear siempre deja «sospechosa»; cambiar el estado y editar una confirmada, `operator_only` en el servicio; filas en «Acciones y roles» |
| K23 | El editor se arrepiente de su marca y no puede quitarla (D10 da resolver al operador) (C). | `notes.py:76` (archivar la propia) | alta | Baja | el autor retira su propia sospecha, como archiva su nota |
| K24 | Un pack que pasó el rango: el aviso se va de la tarjeta, pero la página de un build viejo lo sigue mostrando, como historia (C). | D9 | media | Baja | se evalúa por build mirado; la tarjeta, con el build vigente |
| K25 | Dónde vive «Conocimiento» y cómo se explica (A): una entrada nueva del menú y su texto en «Cómo funciona». | `base.html:3-7` (NAV) | — | — | **dueño, K-Q5** |
| K26 | Un pack borrado queda en «visto en» (A). | — | baja | Baja | se muestra «un pack borrado», sin enlace |
| K27 | La página «Conocimiento» crece sin fin (A). | — | media | Baja | de a 20, con los filtros de la spec (mod, estado, Minecraft) |

## Premortem

Salió y el dueño dejó de confiar en la base, o rompió un mundo. Por qué:

1. Una actualización de servidor entró a un Sodium × Iris conocido porque la vista previa y el chequeo diario no
   miraban la base (K8): la base tenía razón y no se veía donde se actuaba.
2. Una incompatibilidad confirmada bloqueó una release: se metió en `lock.problems` «para reusar» (K1).
3. El rango por fecha cruzó ramas: todos los packs 1.20.1 mostraban «⚠ Sodium 0.6.0+» por los backports 0.5.x
   (K4); a la semana nadie leía los ⚠.
4. «Resuelto» apagó el aviso para un pack que seguía fijado en la versión rota (K13, K14).
5. El tablero se puso lento: cada aviso pedía las fechas de todos los mods de todos los packs, o una llamada a
   Modrinth (K5, K6).
6. La base se llenó de duplicados y sospechas sin revisar mostradas como avisos globales (K10, K12).
7. El nombre de un jugador de un crash pegado apareció en la página global (K21).

## Decidido por el dueño (2026-10-27, d-7985ee-57d185)

- **K-Q1** (K12): una sospecha se ve en la comparación, en «Conocimiento» y como «N sospechas por revisar» en el
  tablero del operador; los packs avisan solo lo confirmado.
- **K-Q2** (K13, K14): sospechosa, confirmada, descartada; «Rota desde» y «Arreglado en» (la primera buena) por
  mod; arreglada deja de avisar solo a quien ya pasó esa versión.
- **K-Q3** (K8): también avisa en «Actualizaciones», la vista previa de actualizar un servidor y el chequeo diario.
- **K-Q4** (K9): el segundo mod es opcional; sin él, «Versión rota», con «Fijar la anterior que funcionó» sugerido.
- **K-Q5** (K25): «Conocimiento» después de Servidores en el menú; la sección «Qué es la base de conocimiento» en
  «Cómo funciona», con el texto que el dueño aceptó:

  > La base de conocimiento guarda las combinaciones que fallan: dos mods que no funcionan juntos, o una versión que
  > falla sola. Vale para todos los packs.
  >
  > 1. Al comparar un build que funcionó con uno que falló, pulsa «Marcar como sospechoso» junto al mod que crees
  >    culpable.
  > 2. Un operador la confirma o la descarta. Mientras sea sospecha, solo se ve aquí y al comparar.
  > 3. Confirmada, avisa en cualquier pack que tenga esa combinación, también al actualizar. Solo avisa: nunca cambia
  >    ni bloquea nada.
  >
  > Cuando una versión nueva lo arregla, anótala en «Arreglado en»: deja de avisar a quien ya la tiene.

Lo demás lo decide el agente como dice «Fin», y cada caso se vuelve un test que falla primero en el plan de la
tanda 3.

## Lo que este walk no cubre

El aspecto (barrido visual cuando exista), cuántas marcas escribe de verdad el dueño, y si los rangos por fecha
aciertan con mods reales (la comprobación real más barata: marcar en `scripts/demo.sh` un par con builds de dos
versiones de Minecraft y ver que el aviso no cruce de rama).
