# O8: versiones resueltas con historia, fuente y conocimiento

> **Decidida el 2026-10-26 (d-7985ee-a99db7): ver §8, que manda sobre §3–§6 donde difieren.** Antes del
> plan de cada tanda falta su user-walk (`docs/research/2026-10-21-observaciones-ronda-3.md`, «Reparto»).

**Fecha:** 2026-10-25 · **Origen:** O8 y O2 de `docs/research/2026-10-21-observaciones-ronda-3.md` ·
**Base:** d-7985ee-fce4ff (los orígenes de una fijación) y d-7985ee-da27e6 (el origen «servidor») ·
**Reglas que no se tocan:** un mod nunca retrocede salvo por un pin (d-7985ee-6dc34c); una acción
peligrosa se sugiere, nunca es automática (d-7985ee-56a529); un build nunca se edita.

Marca **SUPUESTO**: lo que no se comprobó en el código o en una fuente primaria.

## 1. El problema, en palabras del dueño

> «Que fijar una versión no sea una acción única que luego se sobrescriba, sino que queden versiones
> resueltas del modpack con su información, fuente, y quizás que se puedan añadir comentarios y
> revisiones de si funcionó, y espacio para escribir errores, y herramientas para entender
> incompatibilidades, marcarlas y añadirlas a alguna base de conocimiento. Esto es algo grande y de
> mucha ayuda.» (2026-10-21)

Dicho en piezas:

1. **Historia:** lo que se fijó o resolvió antes no se pierde al cambiarlo.
2. **Información y fuente:** cada versión dice de dónde vino y qué se sabe de ella.
3. **Revisiones:** se puede decir si una versión (o un build) funcionó, con un comentario.
4. **Errores:** hay lugar para pegar el error que se vio.
5. **Incompatibilidades:** herramientas para encontrarlas, marcarlas y guardarlas en una base de
   conocimiento que sirva la próxima vez.

## 2. Lo que ya existe

### Lo que dio O2 (la base mínima)

- `PinRecord` en `ModpackBundle.pin_origins` (`src/model/modpack/project.py`): por cada mod fijado,
  una lista de `PinOrigin` (versión del pack, elegida a mano, resolución, servidor), cada uno con su
  versión, si pasa la validación del pack y por qué no, una etiqueta, una fecha y, si es de un
  servidor, su id. Uno está en uso; su versión es la de `pins`.
- Importar fija la versión del pack y la valida; escanear un servidor vinculado ofrece su versión
  como origen «servidor» (uno por servidor); la tarjeta deja poner otro origen en uso, con la
  confirmación de una baja (d-7985ee-c62c61).

### Lo que ya guarda el resto del sistema

- **Builds** (`ModpackBuild`): cada build es una resolución congelada y numerada, con su pedido
  (`ResolveRequest`, que incluye los pins) y su lock (cada mod con versión, razón, quién lo pidió).
  No se edita. En el código no hay borrado de builds (búsqueda de `ModpackBuild.*delete` sin
  resultados). Ya son «versiones resueltas del modpack».
- **Comparar dos builds**: existe (`/modpacks/{id}/builds/compare`, `compare_builds`,
  `src/library/changes.py`).
- **Escaneos** (`ServerScan`): uno por escaneo, con fecha y archivos. En el código no hay borrado de
  escaneos (búsqueda sin resultados). SUPUESTO: no hay una purga fuera del código de la app.
- **Auditoría** (`AuditEvent`): fijar (`bundle.pin`), importar pins (`bundle.pin.import`) y actualizar
  un servidor (con el número de build) quedan con autor y fecha. SUPUESTO: los `details` de
  `bundle.pin` bastan para reconstruir la versión anterior; no se revisó cada llamada.
- **Incompatibilidades de Modrinth**: el resolvedor ya reporta un problema `incompatible` cuando una
  dependencia declarada como incompatible entra al pack (`_check_incompatible`,
  `src/library/resolution.py`). Solo lo que declara el autor del mod en Modrinth.

### Lo que falta

| Pedido | Hoy |
|---|---|
| Historia de un mod | Un origen nuevo de la misma fuente **reemplaza** al anterior (`_put` en `src/library/pins.py`; `server_origins` cambia la versión del origen del servidor). La versión vieja solo queda, a pedazos, en builds, escaneos y auditoría. Nada la junta. |
| Fuente e información | El origen dice su fuente y validación. Falta verlo junto a la historia. |
| Revisiones y comentarios | No existen. No hay ningún campo de nota en los modelos. |
| Errores | No existen. |
| Entender incompatibilidades | Solo la comparación de builds y lo que declara Modrinth. |
| Marcar incompatibilidades y base de conocimiento | No existen. |

## 3. Modelo propuesto

Idea central: **no reescribir nada que ya existe; agregar al lado**. Los builds siguen siendo las
versiones resueltas; la historia de un mod se arma leyendo lo que ya se guarda; lo nuevo son las
notas y las incompatibilidades, en colecciones propias.

### 3.1 Historia de un mod (se deriva, casi no se guarda)

`ModHistory` (pura, en `src/library/`, sin base de datos): para un pack y un mod, una línea de tiempo
con:

- cada build que lo trae (número, versión, fecha, si fue recomendado);
- cada escaneo de un servidor vinculado que lo tiene (servidor, versión, fecha);
- cada cambio de fijación de la auditoría (quién, de qué versión a cuál);
- los orígenes actuales del `PinRecord`.

Lo único nuevo que se guarda: **los orígenes reemplazados**. Hoy se pierden. Propuesta:
`PinRecord.past: list[PinOrigin]` (por defecto vacía, con un tope, por ejemplo 20 por mod). Cuando
`_put` o `server_origins` reemplazan un origen, el viejo pasa a `past`. Ver D2.

### 3.2 Notas y revisiones (colección nueva)

`VersionNote(Document)`:

| Campo | Qué es |
|---|---|
| `bundle_id` | el pack donde se probó |
| `project_id`, `version_id` | el mod y su versión; ambos vacíos si la nota es de un build entero |
| `build_number` | el build, si la nota es de un build o se probó en uno |
| `server_id` | el servidor donde se vio, si aplica |
| `verdict` | `works` / `fails` / `issues` / `untested` (ver D4) |
| `text` | el comentario |
| `error` | el error pegado, con tope de tamaño (ver D5) |
| `author` | `ActorRef`, como la auditoría |
| `at`, `edited_at` | fechas |
| `archived` | borrar es archivar (ver D11) |

Índices: `(bundle_id, project_id)` y `(project_id, version_id)`.

Un build sigue sin editarse: su nota vive aparte y apunta a él por número.

### 3.3 Incompatibilidades y base de conocimiento (colección nueva)

`KnownIssue(Document)`, una entrada de la base:

| Campo | Qué es |
|---|---|
| `kind` | `incompatible` (dos mods) o `broken` (una versión sola) |
| `a`, `b` | `{project_id, from_version_id?, to_version_id?}`: el mod y el rango donde se vio; `b` vacío si es `broken` |
| `game_version`, `loader` | dónde se vio (vacío = cualquiera) |
| `status` | `suspected` / `confirmed` / `resolved` |
| `summary` | una línea («Sodium 0.6 con Iris 1.7: pantalla negra») |
| `notes` | ids de las `VersionNote` que la respaldan |
| `seen_in` | packs donde se vio |
| `author`, `at`, `updated_at` | |

Se registra en `init_beanie` con `VersionNote` (invariante de registro). Un `KnownIssue` nunca cambia
una resolución sola: a lo más produce un aviso (ver D8).

### 3.4 Cómo se relaciona

```
ModpackBundle ──pins / pin_origins (en uso + candidatos + past)
     │
     ├── ModpackBuild n (congelado) ◄── VersionNote (build_number)
     │        └ lock: mod → versión  ◄── VersionNote (project_id, version_id)
     ├── ServerScan (por servidor vinculado)  → historia, origen «servidor»
     └── AuditEvent (bundle.pin, update)      → historia
VersionNote ──notes──► KnownIssue (global o por pack, D9) ──aviso──► tarjeta y build
```

### 3.5 Migración de datos

- **Nada se reescribe.** Las colecciones nuevas empiezan vacías. `PinRecord.past` tiene default vacío:
  un registro guardado antes carga igual. No hace falta runbook (no se borra ni transforma nada);
  SUPUESTO: lo confirma `.agents/knowledge/INDEX.md` al planificar.
- La historia de lo anterior al cambio sale de builds, escaneos y auditoría, que ya están. Lo único
  que no se recupera son los orígenes reemplazados antes de este cambio.
- **Nunca bajar:** una nota «falló» no mueve nada. La salida sigue siendo un pin. La tarjeta puede
  **sugerir** «Fijar la última que funcionó (19.51)», que abre el diálogo de baja existente
  (d-7985ee-c62c61). Los pisos no cambian por una nota.
- **Sugerir, nunca automático:** un `KnownIssue` confirmado avisa; no quita mods, no fija versiones,
  no bloquea por su cuenta salvo que el dueño elija D8-B.
- Roles: cada método nuevo que escribe suma su fila en `## Acciones y roles` de `docs/decisions.md`
  (barrido de `tests/test_routes_roles.py`).

## 4. Lo que vería el panel

1. **Tarjeta de un mod, pestaña «Historia»**: la línea de tiempo (builds, servidores, cambios de pin,
   orígenes pasados) y, en cada versión, sus notas con el veredicto («✔ funcionó en Survival, build
   14 · Fabián»). Botón «Agregar nota». Desde una versión anterior que funcionó: «Usar esta», con la
   confirmación de baja.
2. **Página de un build**: bloque «¿Funcionó?» con el veredicto del build y sus notas, y «Agregar nota»
   con el campo de error.
3. **Comparar builds** (existe): junto a cada mod que cambió, sus notas y si tiene una entrada en la
   base («⚠ incompatibilidad conocida con Iris»). Botón «Marcar como sospechoso».
4. **Página «Conocimiento»** (nueva, en el menú): las entradas de la base, filtrables por mod, estado y
   versión de Minecraft; cada una con sus notas y los packs donde se vio.
5. **Tarjetas de problema y tablero**: un aviso cuando el pack trae una combinación de la base.
6. **Barrido visual** de las pantallas nuevas (360, 768, 1280 px, claro y oscuro), sin script ni
   `style=` inline (CSP).

## 5. Decisiones para el dueño

Cada una con la recomendada primero. «Costo» es esfuerzo, datos y riesgo.

### D1. Qué es «una versión resuelta del modpack»

- **A (recomendada): el build.** Ya está congelado y numerado; las notas apuntan a él.
  *Verías:* «Build 14 · 3 notas · ✔ funcionó en Survival». *Costo:* bajo; nada nuevo que guardar. Una
  resolución que no se construyó no queda.
- **B: también cada resolución.** Cada vez que se resuelve se guarda una instantánea, aunque no se
  construya. *Verías:* «Resolución del 24-10, 18:02 (sin build)». *Costo:* medio; crece la base en
  cada resolución (un lock por vez); se duplica con los builds.
- **C (intermedia): guardar una resolución solo cuando la marcas.** Botón «Guardar esta resolución»
  sin construir. *Verías:* «Resolución guardada: prueba con Iris». *Costo:* medio-bajo; un concepto
  más que explicar al lado del build.

### D2. Cómo se guarda la historia de un mod

- **A (recomendada, intermedia): derivarla y guardar solo los orígenes reemplazados** (`past`, tope de
  20 por mod). *Verías:* en «Historia», «servidor Survival: 0.5.8 (hasta el 20-10) → 0.6.1».
  *Costo:* bajo; un campo con default; nada que migrar.
- **B: solo derivarla** de builds, escaneos y auditoría. *Verías:* lo mismo, salvo los orígenes que
  nunca llegaron a un build ni a la auditoría. *Costo:* el menor; se pierde lo que se ofreció y se
  reemplazó sin usarse.
- **C: un registro de eventos aparte** (`PinEvent`, cada cambio de cada origen). *Verías:* todo, al
  minuto. *Costo:* medio-alto; otra colección que crece sin tope y que repite la auditoría.

### D3. A qué se le escribe una nota

- **A (recomendada): a la versión de un mod en un pack, o a un build entero**, con servidor opcional.
  *Verías:* «JEI 19.57 en Pack Survival: falló al abrir el inventario» y «Build 14: arranca bien».
  *Costo:* medio; dos formas de nota en un modelo.
- **B: solo al build.** *Verías:* «Build 14: falló; sospecho de JEI». *Costo:* bajo; la nota no sigue
  al mod a otros builds ni a otros packs.
- **C: solo a la versión del mod, global** (sin pack). *Verías:* la nota en todos los packs que usan
  esa versión. *Costo:* bajo-medio; pierde el contexto (otro loader, otra combinación).

### D4. Forma de una revisión

- **A (recomendada): veredicto + texto + error.** Veredicto: funcionó / falló / con problemas / sin
  probar. *Verías:* un chip de color por nota y un filtro «solo las que fallaron». *Costo:* bajo.
- **B: solo texto libre.** *Verías:* comentarios sin estado. *Costo:* el menor; no se puede filtrar ni
  sugerir «la última que funcionó».
- **C: formulario estructurado** (dónde: cliente o servidor; cuándo: al arrancar o jugando; tipo: crash,
  visual, rendimiento). *Verías:* filtros finos y estadísticas. *Costo:* medio; más campos que llenar
  cada vez, y más pruebas.

### D5. Cómo entra un error

- **A (recomendada): pegar el texto**, con tope (por ejemplo 64 KiB), mostrado plegado. *Verías:* un
  bloque «Error» que se despliega. *Costo:* bajo. Un log puede traer nombres de jugadores o IPs: se
  queda en la base y no sale (ver D9).
- **B: subir el archivo** (`crash-report-*.txt`, `latest.log`). *Verías:* «crash-2026-10-24.txt
  (210 KiB) · ver». *Costo:* medio; disco y tope como las importaciones (invariante de subidas).
- **C: leerlo del servidor por Pterodactyl** (solo lectura, no pasa por la compuerta de escritura).
  *Verías:* «Traer el último crash de Survival». *Costo:* medio-alto; SUPUESTO que la API de archivos
  lo permite como los escaneos; hay que contrastar con `docs/references.md`.
- **D (intermedia): A ahora y C en una tanda posterior.**

### D6. Herramientas para entender una incompatibilidad

- **A (recomendada): comparar el build que funcionó con el que falló**, con las notas y la base al
  lado de cada cambio, y «Marcar como sospechoso». *Verías:* «Entre build 12 (✔) y 14 (✘) cambiaron 6
  mods; Iris tiene una incompatibilidad conocida». *Costo:* bajo-medio; reusa `compare_builds`.
- **B: A + leer el error** y señalar los mods que nombra (ids de mod, paquetes Java, mixins).
  *Verías:* «El error nombra a sodium y iris». *Costo:* medio; heurística sin especificación,
  necesita investigación y fallará a veces (SUPUESTO: los crash reports de Fabric y NeoForge listan
  mods con un formato estable).
- **C: B + bisección asistida**: propone un build de prueba con la mitad de los cambios revertidos.
  *Verías:* «Probar build 15 (revierte 3 de 6 cambios)». *Costo:* alto; revertir es bajar mods, así que
  cada paso es una sugerencia con la confirmación de baja, nunca solo; conviene en un pack de prueba.

### D7. Qué se marca como incompatibilidad

- **A (recomendada): dos mods con un rango de versiones**, más Minecraft y loader opcionales.
  *Verías:* «Sodium 0.6.0–0.6.3 ✕ Iris 1.7.x · 1.21.1 · Fabric». *Costo:* medio; hay que pedir el rango
  en un formulario simple («desde esta versión», «hasta: aún no se sabe»).
- **B: dos mods, cualquier versión.** *Verías:* «Sodium ✕ Iris». *Costo:* bajo; avisa de más cuando
  una versión nueva ya lo arregló.
- **C: dos versiones exactas.** *Verías:* «Sodium 0.6.1 ✕ Iris 1.7.2». *Costo:* bajo; deja de avisar con
  la siguiente versión, aunque siga roto.

### D8. Qué hace una incompatibilidad marcada al resolver

- **A (recomendada): solo avisa** en la tarjeta, el tablero y el build. *Verías:* «⚠ Conocido: Iris
  1.7 da pantalla negra con Sodium 0.6 (nota del 24-10)». *Costo:* bajo; cumple d-7985ee-56a529.
- **B: problema que pide decisión antes de construir** («Ignorar en este pack» o resolverlo).
  *Verías:* el build en espera hasta que eliges. *Costo:* medio; un paso más en cada build; más
  tests del resolvedor.
- **C: nada al resolver**; solo vive en la página «Conocimiento». *Verías:* la base, sin avisos.
  *Costo:* el menor; la base no ayuda en el momento.

### D9. Alcance de la base de conocimiento

- **A (recomendada): global a la instalación**, cada entrada con los packs donde se vio. *Verías:* al
  agregar Iris a otro pack, el aviso de la base. *Costo:* bajo-medio.
- **B: por pack.** *Verías:* la base solo dentro de su pack. *Costo:* bajo; se repite el trabajo en cada
  pack.
- **C: global y exportable** (un archivo para compartir). *Verías:* «Exportar conocimiento». *Costo:*
  medio; antes de exportar hay que quitar errores y nombres (privacidad), y decidir el formato.

### D10. Quién escribe

- **A (recomendada): notas y marcas, editor; confirmar o resolver una entrada de la base y archivar la
  nota de otro, operador** (con `operator_only` en el servicio). *Costo:* bajo; filas nuevas en
  «Acciones y roles».
- **B: todo editor.** *Costo:* el menor; cualquiera confirma una incompatibilidad global.
- **C: todo operador.** *Costo:* bajo; el tester (editor) no podría anotar lo que ve.

### D11. Editar y borrar notas

- **A (recomendada): el autor edita (se guarda el texto anterior); borrar es archivar.** *Verías:*
  «editada» con la versión anterior a un clic; «Mostrar archivadas». *Costo:* bajo-medio.
- **B: inmutables; solo se agrega.** *Costo:* el menor; un error de tipeo queda para siempre.
- **C: edición y borrado libres.** *Costo:* bajo; una entrada de la base puede quedar sin respaldo.

### D12. Información de una versión

- **A (recomendada): lo que el catálogo ya tiene** (número, canal, fecha, etiquetas, validación) y un
  enlace a Modrinth. *Costo:* ninguno en requests.
- **B: además el changelog**, guardado. *Verías:* el changelog plegado en la historia. *Costo:* medio;
  más requests y espacio (spec de economía de requests); SUPUESTO que el catálogo no lo guarda hoy.

### D13. `/v1`

- **A (recomendada): notas y base también por `/v1`**, con los mismos roles (viven en el servicio).
  *Costo:* bajo; un router más y sus tests de contrato.
- **B: solo el panel.** *Costo:* el menor; un script no puede anotar.

### D14. Cómo se entrega

- **A (recomendada): dos tandas.** Tanda 1: historia (D2), notas y revisiones (D3–D5, D11) en la
  tarjeta y el build. Tanda 2: comparar con notas, marcar, la base y sus avisos (D6–D9). *Costo:* el
  dueño usa la tanda 1 antes de cerrar la 2.
- **B: todo junto.** *Costo:* una revisión más grande; más tiempo sin nada desplegado.
- **C: solo la tanda 1 por ahora**; la base espera a ver cuánto se anota. *Costo:* el menor; la parte
  que el dueño llamó «de mucha ayuda» queda para después.

## 6. Estimación (en sesiones, gruesa)

| Parte | Sesiones |
|---|---|
| User-walk del flujo y revisión de estas decisiones | 1 |
| Tanda 1: `past`, `ModHistory`, `VersionNote`, pestaña «Historia», nota del build, roles, tests | 3–4 |
| Tanda 2: comparar con notas, `KnownIssue`, página «Conocimiento», avisos | 3–4 |
| Opcionales: D5-C (log desde Pterodactyl) +1–2; D6-B (leer el error) +1–2 con investigación; D6-C (bisección) +2–3; D8-B +1; D9-C +1 | |
| **Total con las recomendadas** | **7–9** |

## 7. Lo que el gate no cubriría

El aspecto de las pantallas nuevas (barrido visual manual), los datos de producción (cuántos builds y
escaneos hay, y si alguno se purgó fuera de la app: SUPUESTO que no), y cualquier lectura real del
servidor si se elige D5-C.

## 8. Lo decidido (dueño, 2026-10-26, d-7985ee-a99db7)

Con `decision-review`, una por turno. Las del dueño, todas la recomendada salvo donde se dice:

| # | Decisión |
|---|---|
| D1 | La versión resuelta es **el build**. El dueño pidió explorar las pruebas como builds, nombres, destacados y una mejor revisión: de ahí B1–B4. |
| B1 | **«Construir como prueba»**: misma numeración, marca «prueba», nunca el build vigente (no sube el piso, el puntero «último» la salta); «Aprobar» quita la marca. Tiene que ser un build: solo así todas las versiones están resueltas y el pack es instalable. Instalada en un servidor, su escaneo **no** es piso del pack hasta aprobarla (H1, d-7985ee-3bb53b, que corrige lo dicho aquí: el piso lee cada servidor vinculado y vale para todo el pack); «Recomendar» en una prueba es «Aprobar y recomendar» (H2); nunca se borra (H3); Discord la avisa como prueba (H4). |
| B2 | **Nombre** corto (~60 caracteres) y editable; los comentarios de un build son sus notas (D3, D4). |
| B3 | **Estrella** «Destacar», varios por pack: primero en la lista, filtro «Solo destacados», los viejos sin estrella plegados en «N builds anteriores». «Recomendado» sigue siendo uno y es el que pone el piso. |
| B4 | **Comparación explicada**: «Comparar con el anterior» en cada build; un resumen («6 cambios: 4 suben, 1 baja, 1 nuevo»); «En el pedido» (canales, pins, MC, loader, puentes, de `ResolveRequest`); el porqué de cada mod (`LockEntry.reason`, `requested_by`); las notas de cada lado. Sin requests nuevos. |
| D2 | A: derivar y guardar los orígenes reemplazados (`PinRecord.past`). |
| D3 | A: a un build o a la versión de un mod en un pack, servidor opcional. |
| D4 | A: veredicto + texto + error. |
| D5 | **D**: pegar ahora; «Traer el último crash» por Pterodactyl (`files/contents`, solo lectura, hasta 4 MiB) en una tanda posterior. |
| D11 | A: el autor edita, se guarda el texto anterior; borrar es archivar. |
| D10 | A, con lo de B1–B3 de editor (como `build_create` y `build_recommend`). |
| D12 | **C, nueva**: lo del catálogo y el changelog pedido a Modrinth solo al desplegarlo, sin guardarlo (reusa la lectura de la simulación). Condición del dueño: «solo si no aumenta las request demasiado»; un test comprueba que dibujar la historia no llama a Modrinth. |
| D6 | A sobre B4: notas y avisos junto a cada cambio y «Marcar como sospechoso». B (leer el error) y C (bisección), descartadas. |
| D7 | A con **rango opcional**: vacío es cualquier versión; desde la comparación, la versión que lo provocó se sugiere a un clic y no se llena sola. |
| D9 | A: global, con los packs donde se vio. |
| D8 | A: solo avisa. |
| D14 | **Tres tandas**: 1 builds (B1–B4, ~3 sesiones), 2 historia y notas (D2–D5, D11, D12, ~3), 3 conocimiento (D6–D9, ~3). Las notas al lado de la comparación llegan con la 2. |

Del agente (s-7985ee-2b8b13): **D13** A (`/v1` también, mismos roles). Nombre, estrella y marca de prueba
se guardan junto al build sin tocar su `lock` ni su `request` («un build nunca se edita» es su contenido) y
quedan en la auditoría. Topes: 20 orígenes pasados por mod, 64 KiB por error. La simulación (O9 D3)
preselecciona el build más nuevo, prueba incluida.

