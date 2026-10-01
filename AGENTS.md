# AGENTS.md

Guía para asistentes de código en este repositorio. `CLAUDE.md` solo importa este archivo: es la
única fuente.

## Qué es

**modpack-creator**: una API (FastAPI) para crear y gestionar modpacks de Minecraft. Consulta mods en
Modrinth, guarda el estado en MongoDB (Beanie) y gestiona servidores a través de Pterodactyl.
Autenticación por OIDC.

- Entrada: `main.py`. Configuración: `.env.yaml` (plantilla en `.env.default`; nunca se versiona).
- Capas: `src/routers/v1/` → `src/services/` → `src/database/` y `src/library/api/client/`
  (Modrinth, Pterodactyl). Los modelos están en `src/model/`. La inyección de dependencias está en
  `src/dependences.py`.

## Comandos reales

```bash
./scripts/build.sh     # docker build de la imagen
./scripts/run.sh       # docker compose up -d
./scripts/stop.sh      # docker compose down
./scripts/update.sh    # git pull + build + run
./scripts/check.sh     # gate: bundle.py verify sobre .agents/
```

No hay tests. `.mypy.ini` existe, pero ningún script ejecuta mypy.

## Método y conocimiento (`.agents/`)

- **Un cambio que toque estado, contratos, datos, seguridad o verificación** empieza por
  `.agents/knowledge/INDEX.md` y las tarjetas que enlaza. Una revisión en contexto limpio, solo si
  se pide, va al subagente `knowledge-reviewer` (`.claude/agents/`, copiado de `.agents/agents/`).
- **Privacidad:** nada escrito en `.agents/` identifica este repositorio, a su dueño, sus usuarios ni
  su infraestructura. `./scripts/check.sh` lo comprueba.
- **Los procedimientos propios de este repositorio mandan.** Un procedimiento de `.agents/method/`
  nunca reemplaza uno de aquí. Si nombra un archivo, un formato, un paso, una tarea o una regla de
  commit que este repositorio define de otra forma, gana la de este repositorio, y el reemplazo se
  anota en `adapted` de `.agents/carrier.toml`.
- `.agents/` se genera fuera de este repositorio: no se edita a mano. Una mejora es una propuesta
  (`bundle.py propose`, en `.agents/proposals/`). Las actualizaciones llegan desde el repositorio
  home (`upstream` en `carrier.toml`).

## Mapa

| Dónde | Qué |
|---|---|
| `docs/requirements.md` | requisitos funcionales |
| `docs/roadmap.md` | trabajo pendiente |
| `src/library/api/README.md` | el cliente HTTP común |
| `.agents/README.md` | qué contiene el bundle y qué archivos son de este repositorio |
