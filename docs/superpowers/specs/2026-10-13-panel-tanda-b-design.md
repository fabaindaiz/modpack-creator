# Panel, tanda B: estilo propio, alta guiada, tablero y móvil

**Fecha:** 2026-10-13 · **Origen:** observaciones 8 a 10 del dueño sobre la 0.3.1 (i-7985ee-44060c),
más el móvil compacto (F40 en el resto), F09 y el estilo inspirado en Modrinth · **Decisiones:** del
dueño, 2026-10-13 (B-D1 a B-D5 abajo); el resto, del agente, con su fila en `docs/decisions.md` ·
**Investigación:** `docs/research/2026-10-13-estilo-modrinth.md`,
`docs/research/2026-10-05-tablero-necesita-atencion.md`,
`docs/research/2026-10-12-construccion-de-modpacks.md`. **Ejecución:** el dueño pidió «continúa el
plan hasta terminar todo» tras responder sus cuatro preguntas: esta spec y su plan no esperan otro
visto bueno.

## Qué se busca

Que el panel se vea como una herramienta de la familia Modrinth sin serlo, que alguien con un
servidor nuevo sepa en cada página qué le falta para tener un build probado, que el tablero diga qué
atender primero, y que ninguna página desborde a 360 px.

**Se sabe que funciona cuando:** al lado de Modrinth el panel parece de la misma familia; desde un
servidor recién importado se llega a la simulación siguiendo solo la lista de pasos; el tablero
abre con lo urgente arriba; el barrido no marca desbordes a 360 px en ninguna página.

## Decisiones del dueño (2026-10-13)

| | Decisión |
|---|---|
| B-D1 | Orden: B3 (estilo) → B1 (alta) → B2 (tablero) → B4 (móvil). |
| B-D2 | Paleta «oscuro profundo + verde vivo»: oscuro fondo `#111316`, tarjeta `#1a1d21`, borde `#2a2e33`, texto `#e6e8eb`, tenue `#9aa1a9`, acento `#22c55e`; claro fondo `#f6f7f8`, tarjeta `#ffffff`, borde `#e3e6ea`, texto `#16181c`, tenue `#5b636b`, acento `#15803d`. Radio 12px tarjetas, 8px botones, píldora en etiquetas. Escrita desde cero; sin nombre, logo ni mascota de Modrinth; nota de «no afiliado». |
| B-D3 | Entran Lucide (ISC) e Inter (OFL), de sus propias fuentes, con sus avisos. |
| B-D4 | Alta guiada: una lista «Para terminar el alta» en la página del servidor y en la del pack, cada paso hecho o con su acción ahí mismo y una línea de por qué; F09 en los dos formularios de crear pack. |
| B-D5 | Tablero: franja «Necesita atención» (hasta 5 tarjetas de una acción, por gravedad, «ver todas», «Todo en orden» con la última revisión) y debajo servidores, builds y trabajos como tarjetas compactas con anchos según el texto. |

## B3. Estilo

- **Tokens (enfoque mixto, del agente):** en `src/web/assets/app.css`, un `@theme` que redefine las
  escalas `zinc` y `emerald` de Tailwind con la paleta de B-D2 (el oscuro usa los tonos 900/950 y
  800; el claro 50/200), así toda plantilla cambia sin tocarla, y agrega tokens semánticos
  (`--color-surface`, `--color-raised`, `--color-line`, `--color-muted`, `--color-accent`) para lo
  nuevo. Lo que se toque después migra a los semánticos.
- **Inter:** `src/web/static/fonts/InterVariable.woff2` y `LICENSE-Inter.txt`, del release 4.1 de
  rsms/inter; `@font-face` en `app.css` (`font-display: swap`), `--font-sans` con Inter primero.
- **Lucide:** `lucide-static` (paquete oficial, ISC) como dependencia de build; un script
  `scripts/build-icons.mjs` arma `src/web/static/dist/icons.svg` (un `<symbol>` por ícono) con los
  nombres de `src/web/assets/icons.txt`, y copia su licencia a `dist/LICENSE-lucide.txt`. `npm run
  build` lo corre; la imagen ya corre `npm run build`. Macro `ui.icon(name, classes)`:
  `<svg aria-hidden="true"><use href="…/icons.svg#name"></use></svg>`. Un test exige que cada
  `ui.icon("x")` de las plantillas esté en `icons.txt`.
- **Componentes:** radios de B-D2 en `ui.button`, `ui.badge` (píldora), `ui.section`, `ui.card_list`
  y `ui.notice`; íconos en la navegación y en los botones principales.
- **Pie:** «Modpack Creator no está afiliado a Modrinth.» y un enlace «Licencias» a
  `/static/LICENSES.html` (los avisos de Inter, Lucide y htmx).

## B1. Alta guiada

- **Pasos, una función pura** (`src/library/onboarding.py`): recibe hechos y devuelve pasos
  `Step(key, label, done, detail, action)`. Del servidor: escaneado (n identificados) · pack
  vinculado · pack resuelto sin pendientes · build creado · simulado con el último build. Del pack:
  tiene mods · resuelto sin pendientes · build creado · servidor vinculado · simulado en un servidor
  vinculado. Un paso no hecho cuyo anterior tampoco lo está dice «después de …» sin botón.
- **Por qué, una línea por paso** (de la investigación del 2026-10-12): el build congela lo que se
  instala; simular muestra bajas y jars sin explicar antes de tocar el servidor; nunca bajar un mod
  con el mundo cargado.
- **Hechos** de servicios que ya existen (escaneo, vínculo, resolución vigente, `latest_build`,
  trabajos); un método nuevo de lectura por servicio si hace falta, sin rutas nuevas que escriban.
- **Dónde:** `_onboarding.html`, arriba de la página del servidor y del pack; con todo hecho, una
  línea «Alta completa» plegada.
- **F09:** en «Crear modpack» (lista) y en «Crear modpack» del servidor, la versión de Minecraft es
  un `<select>` de las releases que lista Modrinth (`/tag/game_version`, guardadas en memoria un día)
  y el loader un `<select>`; sin Modrinth, el campo de texto de hoy. El servidor sugiere la versión
  y el loader de su escaneo, como hoy.

## B2. Tablero

- **Franja** (`src/library/attention.py`, pura): ítems `Attention(severity, text, action_label,
  href)` desde los hechos del tablero: servidor desviado → Simular; trabajo fallido en 24 h → Ver;
  pack con pendientes o problemas en su resolución vigente → Decidir; pack con actualizaciones →
  Ver; servidor sin escaneo → Escanear; servidor sin pack → Vincular. Orden por gravedad y luego por
  fecha; 5 visibles y el resto en un `<details>` «Ver todas (n)»; vacía, «Todo en orden» y la hora
  de la última revisión de Modrinth más reciente.
- **Debajo:** servidores, últimos builds y trabajos con `ui.card_list`; columnas con
  `grid-cols-[minmax(0,1fr)_auto_…]`: el texto largo toma el resto y lo corto (estado, fechas,
  números) su ancho.

## B4. Móvil compacto

- `ui.table` (modpacks, servidores, builds, archivos locales, reemplazos, versiones, comparación)
  apila cada fila como tarjeta bajo `sm`: cada `<td>` lleva `data-label` y una clase de Tailwind
  (`max-sm:before:content-[attr(data-label)]`) lo muestra como etiqueta; desde `sm`, tabla. Sin JS.
- El barrido exige 0 px de desborde en cada página a 360 px.

## Errores

- Modrinth caído al crear un pack: el campo de texto de siempre (F09 no bloquea).
- Un hecho del alta que falla al leerse (escaneo inexistente, 404): el paso queda «no hecho», nunca un
  500.
- Un ícono que falta en `icons.txt`: lo atrapa el test, no el navegador.

## Tests

- Estilo: el `@theme` define las escalas y los semánticos; `@font-face` de Inter; el sprite contiene
  cada ícono usado; el pie dice «no está afiliado»; la CSP no cambia.
- Alta: la función pura con cada combinación de hechos; la página del servidor y la del pack con la
  lista; F09 con y sin Modrinth.
- Tablero: la función pura (orden, tope, vacío); la página con la franja y las tarjetas.
- Móvil: cada `ui.table` con `data-label` en sus celdas; barrido sin desbordes.

## Fuera de esta tanda

Los loaders secundarios (tanda C). Un asistente de alta aparte (B-D4 lo descartó).
