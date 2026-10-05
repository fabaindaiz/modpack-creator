# modpack-creator
A simple tool to create and manage modpacks for Minecraft.

API (FastAPI) que consulta mods en Modrinth, guarda los modpacks en MongoDB y opera los
servidores de un panel de Pterodactyl propio.

## Correr

**Instalación nueva:** [`docs/guia-instalacion.md`](docs/guia-instalacion.md), de cero a un panel que funciona.

> **Antes del próximo `./scripts/update.sh` en un servidor que ya corría:** seguir
> [`docs/runbooks/migrar-mongo-a-volumen.md`](docs/runbooks/migrar-mongo-a-volumen.md). Desde
> 2026-10-01 MongoDB guarda sus datos en un volumen nuevo que arranca vacío, y la API exige
> `OIDC_ALLOWED_GROUPS` y `OIDC_OPERATOR_GROUPS` (los grupos que operan servidores; vacía = nadie
> opera). Para el panel web, además: [`docs/runbooks/desplegar-panel.md`](docs/runbooks/desplegar-panel.md).

1. Copia `.env.default` a `.env.yaml` y completa los valores (nunca se versiona).
2. Crea `docker/.env` con `MONGO_VERSION`, `MONGODB_PASSWORD`, `MONGOWEB_PASSWORD`, `FORWARDED_ALLOW_IPS` y `API_BIND_IP` (la lista comentada está en `docs/runbooks/migrar-mongo-a-volumen.md` §3; en local, `API_BIND_IP=127.0.0.1`).
3. `./scripts/build.sh` y `./scripts/run.sh` levantan la API y MongoDB con Docker. mongo-express solo a pedido: `docker compose -f docker/docker-compose.yaml up -d mongoweb`; `./scripts/stop.sh` lo apaga también.

### Con podman

En un Mac (u otra máquina sin Docker) sirve podman: `docker` como enlace a `podman`
(`ln -s "$(command -v podman)" /usr/local/bin/docker`, o la ruta de tu `PATH`) y su máquina
iniciada (`podman machine init && podman machine start`, una vez). Con eso los scripts funcionan sin
cambios: `./scripts/build.sh` construye la imagen (en un Mac con Apple Silicon sale arm64; la x64
la construye CI), `./scripts/run.sh` levanta el compose (rechaza arrancar sin `MONGO_VERSION`,
`FORWARDED_ALLOW_IPS` ni `API_BIND_IP` en `docker/.env`), y `./scripts/test-mongo.sh` corre el gate con un MongoDB
descartable, para que los tests de base no se salten.

### Revisión diaria de actualizaciones (opcional)

Con `UPDATE_CHECK_AT` (`HH:MM`, hora local) cada modpack con un build se revisa una vez al día
contra Modrinth, en la zona `UPDATE_CHECK_TZ` (por defecto `America/Santiago`). Solo mira: avisa
por Discord, en `/modpacks/{id}/actualizaciones` y con una insignia en el tablero, y nunca aplica
nada. Sin `UPDATE_CHECK_AT` no hay programación (el botón de la página sigue); un valor mal
escrito impide arrancar. Los dos valores van entre comillas en `.env.yaml`
(`UPDATE_CHECK_AT: "04:30"`): sin ellas YAML lee `10:30` como un número y la API no arranca. Una
revisión manual hecha antes de `UPDATE_CHECK_AT` cuenta como la de ese día. Ver [`docs/runbooks/desplegar-panel.md`](docs/runbooks/desplegar-panel.md).

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
