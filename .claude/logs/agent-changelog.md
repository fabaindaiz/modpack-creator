# Registro de cambios de los agentes

Cada sesión que cambia algo deja una entrada aquí, **la más nueva arriba**, justo debajo de esta
introducción. Los commits de este repositorio fueron `wip` sin cuerpo: el *por qué* de un cambio
no quedaba en ninguna parte, y una sesión no ve lo que otra aprendió. La referencia del formato
está al final del archivo, para que nada entre este párrafo y la primera entrada capture texto.

## 2026-10-01 · s-7985ee-7c3cda — Investigación de plataformas similares y plan en fases

**Qué.**
- Cuatro informes en `docs/research/`:
  - 23 plataformas y herramientas similares comparadas;
  - resolución de dependencias, metadatos de loaders y formatos de exportación;
  - operar servidores de Pterodactyl con backup y rollback;
  - arquitectura (trabajos durables, tests con Mongo, autorización, avisos, UI, observabilidad).
- `docs/plan.md`: la posición del proyecto ("un Solder con dependencias"), el modelo objetivo
  (pack → reglas → build inmutable → despliegue ← manifiesto observado) y cinco fases.
- 14 ítems nuevos en `docs/roadmap.md`.
- Los informes enlazados desde `docs/references.md` y el mapa de `AGENTS.md`.
- Antes, en esta misma fecha: la investigación de hashes en Pterodactyl (i-7985ee-43df2f).

**Áreas.** `docs/plan.md`, `docs/research/`, `docs/roadmap.md`, `docs/references.md`, `AGENTS.md`.

**Por qué.** El dueño pidió una investigación detallada de plataformas similares y de cómo seguir el
proyecto, con planes, ideas e informes, y cerrar la sesión.

**Arquitectura.** ✅ Cumple: solo documentos; ningún cambio de código.

**Qué salió mal en el camino.**
- Un informe se escribió con un id de roadmap inventado como marcador. Se reemplazó por los ids
  acuñados antes de enlazarlo.
- `bundle.py ids` pasaba con 0 errores pero reconocía 21 de 42 definiciones: el formato de
  encabezado del roadmap (id antes del `·`, como dice la plantilla del método) no es el que lee la
  herramienta. Al mover los ids después del `·`, el chequeo marcó 5 definiciones dobles en la tabla
  de costos; esa tabla ahora cita los ids en la segunda columna. Queda en `adapted`.

**Qué quedó sin hacer.**
- Ninguna fase del plan empezó.
- Hallazgos nuevos que el código actual no cumple:
  - el volumen de Mongo está comentado en `docker/docker-compose.yaml`, así que recrear el
    contenedor borra la base (i-7985ee-e21d5d);
  - `ModrinthProject` exige `client_side`/`server_side`, que están deprecados;
  - Minecraft numera por año (`26.x`);
  - `ModrinthService.project_dependencies` usa un endpoint que mezcla todas las versiones
    (i-7985ee-f4ba63).

**No verificado.**
- Las páginas de add-ons de BuiltByBit (403), Shockbyte y Apex.
- El significado de `embedded` en las dependencias de Modrinth (la spec no lo define).
- Que un egg pueda correr packwiz antes de Java.
- El límite por webhook de Discord.
- Todo está marcado así en cada informe.

**Medido.** Llamadas reales a las APIs públicas, citadas en los informes:
- `/tag/game_version` tiene 917 entradas; la última release es `26.3`;
- `POST /version_files/update` resolvió una versión vieja a la más nueva compatible;
- Quilt meta devolvió 307 loaders sin filtrar por versión.

## 2026-10-01 · s-7985ee-134d63 — Bootstrap del método: gate en CI con hatch, audit, tests de arranque y la fuga de cabeceras corregida

**Qué.**
- Adopción del bundle 0.0.25 y la evaluación (`.agents/evaluation-2026-10-01-38f8aa.md`).
- El entorno hatch (`pyproject.toml`, Python 3.12, dependencias desde `requirements.txt`).
- El gate `hatch run gate`, en CI (`.github/workflows/check.yml`) y en `scripts/check.sh`.
- `scripts/audit.py` con sus tests.
- `tests/test_startup.py` y `tests/test_http_client.py`.
- `AGENTS.md` con invariantes, `docs/decisions.md`, `docs/references.md`, `docs/roadmap.md`,
  `.claude/settings.json` y los skills `verify` y `state-review`.

Arreglos de código, cada uno visto fallar antes en un test o en mypy:
- el import faltante de `PterodactylVersion`, que impedía arrancar en 3.12;
- `ModrinthService` recibía la base de modpacks;
- los argumentos por defecto mutables de `src/library/api/`: un dict compartido reenviaba la
  cabecera `Authorization` de Pterodactyl a otros clientes, reproducido contra un servidor local;
- el campo `server` de `PterodactylVersion`, que ya usaban sus dos llamadores;
- el re-export muerto de `src/library/auth`.

**Áreas.** `src/library/api/`, `src/dependences.py`, `src/services/pterodactyl.py`,
`src/model/pterodactyl/modpack.py`, `src/database/pterodactyl.py`, `src/library/auth/`,
`tests/`, `scripts/`, `docs/`, `.claude/`, `.github/`, `pyproject.toml`, `.mypy.ini`.

**Por qué.**
- El dueño pidió inicializar el repositorio con `.agents` y aprobó la topología de la Fase 3.
- Decisiones del dueño: hatch para el entorno, los routers pasan por un servicio, CI es el gate.
- La app corre en su servidor, conectada a su panel de Pterodactyl.

**Arquitectura.** ✅ Cumple. Las 14 inyecciones de routers que saltan la capa de servicios quedan
en una baseline que solo se achica (d-7985ee-16ed28), no arregladas.

**Qué salió mal en el camino.**
- El primer test de rutas suponía que `main.py` monta los routers al importar; los monta en el
  `lifespan`. Además, desde FastAPI 0.142 `app.routes` muestra los routers incluidos como
  `_IncludedRouter` sin `path`. El test ahora lee `app.openapi()["paths"]`.
- La primera lista de defaults mutables (10) contaba mal: las firmas de varias líneas escondían
  otras siete.

**Qué quedó sin hacer.**
- El Dockerfile sigue sin copiar `main.py` (i-7985ee-a77cdc).
- Los updates siguen sin escribir el dato nuevo (i-7985ee-055362).
- El índice único de `ModrinthVersion.project_id` sigue (i-7985ee-be55dd).
- Los contratos de Modrinth y Pterodactyl siguen mal (i-7985ee-fb00ca).
- Los 6 avisos de reintentos sobre POST (i-7985ee-05c4a0).
- Cualquier cuenta del IdP entra (i-7985ee-c64e06).
- `version_create` está anotado como `PterodactylServer` y devuelve una lista (i-7985ee-4364a3).

**No verificado.** La imagen Docker no se construyó (no hay Docker en esta máquina). MongoDB, OIDC
y las APIs reales no se tocaron. CI todavía no corrió: el workflow existe, pero no se ha empujado.
`version_update` ahora lanza `NotImplementedError` en vez de devolver `None`; nadie lo llama.

**Medido.**
- Antes: `import main` en 3.12 → `NameError`; mypy → 4 errores; 0 tests.
- Después: el gate pasa con 10 tests, mypy con 0 errores en 51 archivos, y el audit con 0 fallos,
  6 avisos y una baseline de capas de 14.

---

## Formato de una entrada

```markdown
## YYYY-MM-DD · s-7985ee-<content6> — <título de una línea>
**Qué.** Qué cambió, concreto.
**Áreas.** Archivos o carpetas.
**Por qué.** La razón, incluido el pedido que la originó.
**Arquitectura.** ✅ Cumple · ⚠️ Desvío · REVISAR — y por qué.
**Qué salió mal en el camino.** Qué hizo mal el primer intento y qué lo detectó.
**Qué quedó sin hacer.** La deuda que este cambio creó o dejó pasar, con nombre.
**Desvío del plan.** Dónde lo construido se aparta de lo aprobado, y la medición que lo decidió.
**No verificado.** Lo que no se pudo comprobar en este entorno, y dónde espera la pregunta.
**Medido.** El número, si se afirmó algo.
```

El id se acuña una vez con `python .agents/tools/bundle.py id s "<título>"` y no se recalcula.
