# AGENTS.md

Guía para asistentes de código en este repositorio: **invariantes, y dónde está todo lo demás**.
Cada regla dice qué se rompe si se ignora y qué la hace cumplir. `CLAUDE.md` solo importa este
archivo: es la única fuente (d-7985ee-b4786f).

## Qué es

**modpack-creator**: una API (FastAPI) para crear y gestionar modpacks de Minecraft. Consulta mods
en Modrinth, guarda el estado en MongoDB (Beanie) y opera servidores a través de Pterodactyl.
Autenticación por OIDC con sesión en cookie.

**Dónde corre:** en un servidor del dueño, conectado a su propio panel de Pterodactyl. Las
acciones contra el panel (subir, borrar, apagar) ocurren sobre **servidores de juego reales**, y
nada de eso se puede observar desde el entorno de desarrollo.

- Entrada: `main.py`. Configuración: `.env.yaml` (plantilla en `.env.default`; nunca se versiona).
- Capas: `src/routers/v1/` → `src/services/` → `src/database/` y `src/library/api/client/`
  (Modrinth, Pterodactyl). Modelos en `src/model/`. Inyección de dependencias en `src/dependences.py`.
- Archivos ejemplares: `src/database/modpack.py` (una clase de base de datos),
  `src/services/modrinth.py` (un servicio), `src/library/api/client/modrinth.py` (un cliente).

## Invariantes

Decididas, no preferencias. `docs/decisions.md` indexa cada una con lo que la hace cumplir.

- **Python 3.12, uno solo**: la imagen, `.mypy.ini` y hatch dicen lo mismo. En 3.12 las
  anotaciones se evalúan al definir: un nombre sin importar en una firma rompe el arranque
  completo, y en 3.14 no se nota. *Audit* `python_versions` (d-7985ee-7bc399).
- **Los routers solo inyectan servicios.** Un router nunca pide un `*Database` ni un `*API`.
  Las 14 inyecciones que hoy lo violan están en `LAYERING_BASELINE` de `scripts/audit.py`, que
  solo se achica: al mover una al servicio, sácala de la lista en el mismo cambio. *Audit*
  `layering` (d-7985ee-16ed28).
- **Ningún argumento por defecto mutable.** Un `headers: dict = {}` compartido acumuló la
  cabecera `Authorization` de Pterodactyl y la mandó a otros hosts. Las funciones de
  `src/library/api/` devuelven un dict nuevo; nunca modifican el que reciben. *Audit*
  `mutable_defaults`, `tests/test_http_client.py` (d-7985ee-147399).
- **La app importa y el cableado de dependencias coincide con los constructores.** Un servicio
  con la base equivocada no falla hasta que alguien llama al endpoint. `tests/test_startup.py`
  (d-7985ee-313583).
- **Todo `Document` está registrado en `init_beanie`** (`src/model/__init__.py`); si no, lanza al
  primer uso. *Audit* `registration` (d-7985ee-8eb944).
- **La configuración obligatoria falla cerrada**: `getenv` sin default lanza; nunca le pongas un
  default a `SESSION_SECRET`, `MONGODB_URI` ni a un token. Toda variable obligatoria está en
  `.env.default`. *Audit* `env_template` (d-7985ee-2e4d10).
- **No reintentes escrituras externas.** Un POST/PATCH/PUT/DELETE a Pterodactyl o Modrinth no
  lleva `backoff` sobre `Exception`: una respuesta perdida no dice si el servidor ya se apagó o el
  archivo ya se borró. *Audit*, solo aviso por ahora (d-7985ee-05d032).
- **Contrasta cada llamada externa con su especificación** antes de escribirla: rutas, métodos y
  forma del cuerpo están en `docs/references.md`. El gate no habla con las APIs reales.
- **CORS con comodín depende de `SameSite=lax`** en la cookie de sesión. No cambies `same_site`
  sin cambiar antes `allow_origins` (d-7985ee-4e4aee; sin enforcer).

## Archivos que no se editan a mano

- `.env.yaml`: secretos; ni se lee ni se edita (`.claude/settings.json`, d-7985ee-57db37).
- `.agents/` salvo `carrier.toml`, `proposals/` y `evaluation-*.md`: lo genera el repositorio home.
  Una mejora es una propuesta (`bundle.py propose`).

## Comandos

```bash
./scripts/check.sh     # el gate = hatch run gate: bundle, audit, typecheck, tests (lo mismo que CI)
hatch run tests -k x   # un test
./scripts/build.sh     # docker build de la imagen
./scripts/run.sh       # docker compose up -d
./scripts/stop.sh      # docker compose down
./scripts/update.sh    # git pull + build + run
```

El entorno es hatch con Python 3.12 (`pyproject.toml`); las dependencias salen de
`requirements.txt` (d-7985ee-d0f29c).

## Verificación

**El gate es CI** (`.github/workflows/check.yml`, d-7985ee-fd2a8d): un cambio no está listo si
`./scripts/check.sh` no pasa. Los tests se escriben antes que el código que describen, y se ven
fallar. Lo que el gate **no** cubre, y hay que decirlo en el reporte: la imagen Docker, MongoDB
real, el login OIDC y cualquier llamada real a Modrinth o Pterodactyl. Skill `verify`.

## Commits

El usuario es el único autor: sin `Co-Authored-By` ni otra línea de atribución. Conventional
Commits (`feat:`, `fix:`, `docs:`, `test:`, `build:`, `ci:`, `chore:`). Solo cuando se pide
(d-7985ee-8b8db2).

## Registro

Cada sesión que cambia algo deja una entrada en `.claude/logs/agent-changelog.md`, arriba, con un
id de `bundle.py id s "<título>"`: qué, por qué, qué salió mal en el camino, qué quedó sin hacer y
qué no se pudo verificar.

## Método y conocimiento (`.agents/`)

- **Un cambio que toque estado, contratos, datos, seguridad o verificación** consulta
  `.agents/knowledge/INDEX.md` antes de decidir y abre solo las tarjetas que enlaza; decide con su
  *Applies if* y *Not when*, corre su chequeo antes de dar algo por hecho. Si una invariante de
  aquí contradice una nota, gana la de aquí y se dice. Una revisión en contexto limpio, solo si se
  pide, va al subagente `knowledge-reviewer`; si un cambio borra o reescribe datos guardados o toca
  autenticación y nadie la pidió, ofrécela en una línea.
- **Privacidad:** nada escrito en `.agents/` ni en lo que sale de este repositorio identifica a
  un repositorio privado, a su gente ni a sus usuarios; `bundle.py privacy .agents` lo comprueba.
- **Los procedimientos propios de este repositorio mandan.** Un procedimiento de `.agents/method/`
  nunca reemplaza uno de aquí; donde nombra un archivo, formato, paso o regla de commit que este
  repositorio define distinto, gana el de aquí y `.agents/carrier.toml` `adapted` lo registra.

## Mapa

| Dónde | Qué responde |
|---|---|
| `docs/decisions.md` | qué está decidido y qué lo hace cumplir |
| `docs/roadmap.md` | qué está en curso, qué sigue y dónde choca |
| `docs/references.md` | qué dicen las especificaciones externas, y qué cambió aquí por eso |
| `docs/requirements.md` | requisitos funcionales |
| `scripts/audit.py` | las reglas estructurales, ejecutables |
| `.claude/logs/agent-changelog.md` | qué cambió cada sesión, y por qué |
| `src/library/api/README.md` | el cliente HTTP común |
| `.agents/README.md` | qué contiene el bundle y qué archivos son de este repositorio |
