# Panel, tanda B: plan

**Spec:** `docs/superpowers/specs/2026-10-13-panel-tanda-b-design.md` (decisiones d-7985ee-61a9b0 …
d-7985ee-a44a91). **Método:** cada tarea, test primero y visto fallar; `./scripts/check.sh &&
git commit`; `./scripts/test-mongo.sh` al cerrar cada pieza. **Ejecución:** la tarea 1 primero (todo
depende del estilo); después las tareas 2–3 (yo) y 4–5 (un agente en una copia aparte del repo,
fuera de él) en paralelo, porque no comparten archivos salvo las celdas de `modpacks/list.html`; la
6 al final. **Estado (2026-10-13):** escrito.

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
