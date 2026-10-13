# Panel, tanda B: plan

**Spec:** `docs/superpowers/specs/2026-10-13-panel-tanda-b-design.md` (decisiones d-7985ee-61a9b0 …
d-7985ee-a44a91). **Método:** cada tarea, test primero y visto fallar; `./scripts/check.sh &&
git commit`; `./scripts/test-mongo.sh` al cerrar cada pieza. **Ejecución:** la tarea 1 primero (todo
depende del estilo); después las tareas 2–3 (yo) y 4–5 (un agente en una copia aparte del repo,
fuera de él) en paralelo, porque no comparten archivos salvo las celdas de `modpacks/list.html`; la
6 al final. **Estado (2026-10-13):** las 6 tareas hechas (B3 `0e33a4e`, B1 `96e79a2` y `b0d90e3`, B2 y B4 por un agente, `d7b16a2` y `58435b9`, merge `c703f53`); gate con MongoDB 1446, barrido 194 capturas sin problemas.

## Restricciones

Tailwind para estructura y layout, sin CSS propio salvo `@theme`, `@font-face` y la capa de
componentes que ya existe; sin `<script>` ni `style=` inline; los routers solo inyectan servicios;
textos en español; la CSP no cambia.

## Tareas

| # | Qué | Dónde | Tests (fallan primero) |
|---|---|---|---|
| 1 | B3: `@theme` con la paleta, semánticos, Inter, sprite de Lucide (`scripts/build-icons.mjs`, `icons.txt`, `npm run build`), `ui.icon`, radios en macros, íconos en la navegación y botones principales, pie con «no afiliado» y `LICENSES.html` | `src/web/assets/app.css`, `package.json`, `src/web/static/`, `_macros.html`, `base.html` | `tests/test_web_style.py`: escalas y semánticos en `app.css`; `@font-face`; cada `ui.icon("x")` en `icons.txt`; el sprite generado tiene cada símbolo (si existe `dist`); pie y licencias; `ui.icon` sin estilos inline |
| 2 | B1: `onboarding.py` puro; hechos desde los servicios; `_onboarding.html` en servidor y pack | `src/library/onboarding.py`, servicios, `routers/web/{servers,modpacks}.py`, plantillas | `tests/test_onboarding.py` (cada combinación); web: la lista en las dos páginas, «Alta completa» plegada, un hecho que falla deja el paso sin hacer |
| 3 | F09: releases de Modrinth en memoria un día; selects en los dos formularios; texto sin Modrinth | `ModrinthService`, `modpacks/list.html`, `servers/_new_modpack.html` | servicio: una request por día, solo releases, error → `None`; web: select con la sugerida marcada; sin Modrinth, input |
| 4 | B2: `attention.py` puro; franja; tarjetas del tablero | `src/library/attention.py`, `routers/web/home.py`, `home.html` | `tests/test_attention.py` (gravedad, tope 5, vacío); web: franja, «Ver todas», «Todo en orden», tarjetas |
| 5 | B4: `data-label` en cada `ui.table`; apilado bajo `sm` | `_macros.html` (`ui.table`), las 7 plantillas con tabla | web: cada celda de cada tabla con `data-label` igual a su encabezado |
| 6 | Barrido (desborde 0 a 360 px en todas), filas «Se cumple en», roadmap, registro, revisión final | `scripts/visual_sweep.py`, `docs/` | barrido |

## Lo que no cubre

El gate no ve cómo se ve: el parecido con Modrinth lo juzga el dueño mirando el panel; el barrido
solo garantiza que no desborde y que no haya errores de CSP.

## Registro de ejecución

Decisiones tomadas en la ejecución, con su costo si fueron un error:

- B3: Inter del release oficial 4.1 de rsms/inter en el repo; Lucide como `lucide-static` (paquete oficial) solo en el build. `@fontsource` e `inter-ui` se descartaron por ser redistribuciones de terceros. Costo: ninguno.
- B3: botones `rounded-lg`, etiquetas `rounded-full`, toda tarjeta `rounded-xl border` (reemplazo mecánico en 23 plantillas). Costo: una clase por plantilla.
- B1: un paso espera solo a los pasos que necesita (vincular un servidor nunca espera; simular espera al build y al servidor). Costo: ninguno.
- B1: `simulated_build` cuenta simulaciones y actualizaciones reales del mismo pack (revisión final: el número solo coincidía con el build de otro pack). Costo: los trabajos de antes del deploy no tienen pack, y el último paso queda sin hacer hasta la próxima simulación.
- F09: Modrinth listado en memoria un día; si falla, cinco minutos sin preguntar; espera 3 s como máximo; un solo pedido a la vez (el barrido mostró páginas lentas sin esto). Costo: ninguno.
- B2 (agente): columnas cortas de las tarjetas con ancho fijo (`6.5rem`, `7rem`), porque cada tarjeta es su propia grilla; un servidor puede tener dos tarjetas (Escanear y Vincular); una resolución por pack. Costo: ruido en el tablero, no acciones equivocadas.
- B2: un fallo que un trabajo posterior del mismo tipo y servidor terminó bien no se muestra (revisión final). Costo: ninguno.
- B4 (agente): las clases de apilado van una vez en el `tbody` de `ui.table`, no en cada celda. Costo: una celda con clases propias podría chocar; hoy ninguna.
- El barrido cuenta como problema una tabla que desborda su caja a 360 px (antes solo veía el desborde de la página).
- Revisión final (Opus, contexto limpio): crítico, la imagen (`.dockerignore` excluía `scripts/`) → `!scripts/build-icons.mjs`, test `test_docker_context.py`, y CI revisa el sprite, la fuente y las licencias; importantes: contraste de zinc-500 (test de contraste WCAG), simulación por pack, fallos ya resueltos, la lista de Modrinth que colgaba páginas, y los hechos del alta que fallan. Los 8 menores, en el roadmap.
