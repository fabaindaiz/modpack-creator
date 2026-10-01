# modpack-creator
A simple tool to create and manage modpacks for Minecraft.

API (FastAPI) que consulta mods en Modrinth, guarda los modpacks en MongoDB y opera los
servidores de un panel de Pterodactyl propio.

## Correr

> **Antes del próximo `./scripts/update.sh` en un servidor que ya corría:** seguir
> [`docs/runbooks/migrar-mongo-a-volumen.md`](docs/runbooks/migrar-mongo-a-volumen.md). Desde
> 2026-10-01 MongoDB guarda sus datos en un volumen nuevo que arranca vacío, y la API exige
> `OIDC_ALLOWED_GROUPS`.

1. Copia `.env.default` a `.env.yaml` y completa los valores (nunca se versiona).
2. Crea `docker/.env` con `MONGO_VERSION`, `MONGODB_PASSWORD` y `MONGOWEB_PASSWORD` (ver el runbook).
3. `./scripts/build.sh` y `./scripts/run.sh` levantan la API, MongoDB y mongo-express con Docker.

## Desarrollar

Requiere [hatch](https://hatch.pypa.io). El entorno usa Python 3.12 y las dependencias de
`requirements.txt`.

```bash
./scripts/check.sh   # el gate: lo mismo que corre CI
```

Las reglas del repositorio, para personas y agentes, están en [`AGENTS.md`](AGENTS.md); las
decisiones, en [`docs/decisions.md`](docs/decisions.md).
