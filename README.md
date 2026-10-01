# modpack-creator
A simple tool to create and manage modpacks for Minecraft.

API (FastAPI) que consulta mods en Modrinth, guarda los modpacks en MongoDB y opera los
servidores de un panel de Pterodactyl propio.

## Correr

1. Copia `.env.default` a `.env.yaml` y completa los valores (nunca se versiona).
2. `./scripts/build.sh` y `./scripts/run.sh` levantan la API, MongoDB y mongo-express con Docker.

## Desarrollar

Requiere [hatch](https://hatch.pypa.io). El entorno usa Python 3.12 y las dependencias de
`requirements.txt`.

```bash
./scripts/check.sh   # el gate: lo mismo que corre CI
```

Las reglas del repositorio, para personas y agentes, están en [`AGENTS.md`](AGENTS.md); las
decisiones, en [`docs/decisions.md`](docs/decisions.md).
