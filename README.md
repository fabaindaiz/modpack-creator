# modpack-creator
A simple tool to create and manage modpacks for Minecraft.

API (FastAPI) que consulta mods en Modrinth, guarda los modpacks en MongoDB y opera los
servidores de un panel de Pterodactyl propio.

## Correr

> **Antes del próximo `./scripts/update.sh` en un servidor que ya corría:** seguir
> [`docs/runbooks/migrar-mongo-a-volumen.md`](docs/runbooks/migrar-mongo-a-volumen.md). Desde
> 2026-10-01 MongoDB guarda sus datos en un volumen nuevo que arranca vacío, y la API exige
> `OIDC_ALLOWED_GROUPS` y `OIDC_OPERATOR_GROUPS` (los grupos que operan servidores; vacía = nadie
> opera). Para el panel web, además: [`docs/runbooks/desplegar-panel.md`](docs/runbooks/desplegar-panel.md).

1. Copia `.env.default` a `.env.yaml` y completa los valores (nunca se versiona).
2. Crea `docker/.env` con `MONGO_VERSION`, `MONGODB_PASSWORD`, `MONGOWEB_PASSWORD` y `FORWARDED_ALLOW_IPS` (ver el runbook).
3. `./scripts/build.sh` y `./scripts/run.sh` levantan la API, MongoDB y mongo-express con Docker.

## Desarrollar

Requiere [hatch](https://hatch.pypa.io). El entorno usa Python 3.12 y las dependencias de
`requirements.txt`.

```bash
./scripts/check.sh   # el gate: lo mismo que corre CI
```

## Panel: CSS

El CSS del panel se compila con Tailwind y htmx se copia desde npm (Node 24 o superior). Los
resultados van a `src/web/static/dist/`, que no se versiona.

```bash
npm ci           # instala las versiones exactas de package-lock.json
npm run build    # compila app.css y copia htmx.min.js
npm run dev      # lo mismo, y recompila app.css al cambiar las plantillas
```

La imagen Docker lo construye ella misma en una etapa de Node: no hace falta correr nada antes de
`./scripts/build.sh`.

## Revisión visual

`./scripts/visual-sweep.sh [salida]` captura el panel a 360, 768 y 1280 px, en claro y oscuro, como
operador (y a 1280 px en claro como editor), con datos de demo sobre un MongoDB descartable. Imprime
las páginas con status distinto de 200, desborde horizontal o errores de consola, y sale con un
código distinto de 0 si hay alguna. Las capturas quedan en `salida` (por defecto un directorio
temporal que imprime). Se corre a mano: no va en CI ni en `hatch run gate`.

```bash
hatch run visual:playwright install chromium   # solo la primera vez
./scripts/visual-sweep.sh
```

Requiere Docker, Node y hatch. La app corre desde un directorio temporal con configuración falsa:
`.env.yaml` no se carga. Con `PLAYWRIGHT_CHROMIUM=<ruta>` usa un Chromium que ya tengas.

Las reglas del repositorio, para personas y agentes, están en [`AGENTS.md`](AGENTS.md); las
decisiones, en [`docs/decisions.md`](docs/decisions.md).
