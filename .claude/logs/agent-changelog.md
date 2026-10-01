# Registro de cambios de los agentes

Cada sesión que cambia algo deja una entrada aquí, **la más nueva arriba**, justo debajo de esta
introducción. Los commits de este repositorio fueron `wip` sin cuerpo: el *por qué* de un cambio
no quedaba en ninguna parte, y una sesión no ve lo que otra aprendió. La referencia del formato
está al final del archivo, para que nada entre este párrafo y la primera entrada capture texto.

## 2026-10-01 · s-7985ee-0cc0fa — Cierre de la sesión del 2026-10-01: hallazgos, aprendizajes y pendientes

Resumen de toda la jornada, de la adopción del método a S3. El detalle de cada parte está en las
entradas de abajo y en los documentos que se citan; aquí solo lo que hace falta para retomar.

**Qué quedó hecho.**
- **Método adoptado:** bundle 0.0.25, carrier `r-7985ee`, `AGENTS.md` con invariantes, gate
  `hatch run gate` en CI.
- **Arreglos con test:**
  - el arranque en 3.12;
  - el cableado de `ModrinthService`;
  - la fuga del token de Pterodactyl por un dict compartido.
- **Investigación:** cuatro informes (`docs/research/`), el plan (`docs/plan.md`) y el plan de trabajo
  (`docs/plan-de-trabajo.md`).
- **S1 en `main`:** dependencias fijas.
- **S2 y S3 en la rama `fase-0`, con CI en verde:**
  - volumen de Mongo con nombre;
  - imagen que arranca;
  - login con autorización por grupo e identidad `iss` + `sub`.

**Hallazgos** (el estado del proyecto que alguien que llega necesita saber):
- **El despliegue anterior nunca pudo arrancar la API en Docker**: la imagen no copiaba `main.py` ni
  recibía `.env.yaml`. ASUNCIÓN: la API corría fuera de Docker.
- **Los datos de Mongo vivían en un volumen anónimo.** Cada `stop.sh` los dejaba huérfanos.
- **Cualquier cuenta del IdP podía operar los servidores** (cerrado en S3, falta desplegar).
- **Pterodactyl no da hashes y no los va a dar** (pterodactyl/panel#4894). Identificar mods exige
  leer los bytes o que la app sea quien instala (i-7985ee-43df2f).
- **Modrinth y Minecraft cambiaron:**
  - Minecraft numera por año (`26.x`);
  - `client_side`/`server_side` están deprecados por `environment`;
  - `/project/{id}/dependencies` no sirve para resolver (i-7985ee-f4ba63).
- **Nadie hace lo que este proyecto se propone.** El análogo es Technic Solder, sin dependencias
  (`docs/research/2026-10-plataformas-similares.md`).

**Aprendizajes** (lo que cambió cómo se trabaja aquí):
- **Investigar en la fuente primaria antes de tocar algo importante paga**: la regla de login que
  exigía `email_verified` habría dejado afuera a todos, porque authentik lo manda en `false` por
  defecto desde 2025.10. Se detectó leyendo la documentación de authentik antes de desplegar.
- **Un chequeo en verde puede estar ciego:** `bundle.py ids` pasaba sin ver la mitad de las
  definiciones, por una diferencia entre la plantilla del método y la herramienta. Comparar lo que
  un chequeo examinó con lo que existe.
- **Probar en otra versión esconde errores:** el `NameError` de arranque solo aparece en 3.12, no en
  3.14.
- **CI solo en `main` deja sin chequear el trabajo en ramas**, y aquí no hay `gh` para abrir PRs. Por
  eso CI corre ahora en cada push.

**Pendientes, en orden** (el detalle en `docs/roadmap.md` por id):
1. **Desplegar `fase-0` siguiendo `docs/runbooks/migrar-mongo-a-volumen.md`** (respaldo, versión,
   restaurar), y después fusionar `fase-0` en `main`. **No fusionar antes:** `scripts/update.sh` hace
   `git pull` y desplegaría el volumen nuevo, vacío. Es lo único que bloquea.
2. Del dueño:
   - crear el grupo en authentik;
   - las variables nuevas en `.env.yaml` (`OIDC_ALLOWED_GROUPS` es obligatoria);
   - contestar la decisión 3 (¿hay datos en producción?);
   - **revocar la clave de Pterodactyl** si la API corrió alguna vez sin `MODRINTH_TOKEN` (fuga
     corregida en `81a7779`).
3. Las sesiones siguientes de la fase 0, en orden (`docs/plan-de-trabajo.md` §3):
   - S4: MongoDB real en CI; también cubre `user_from_provider`, hoy sin test;
   - S5: los updates escriben y caben muchas versiones;
   - S6: contratos y reintentos;
   - S7: usuario de servicio y `/health`.
4. Sueltos:
   - `httpx` en `requirements.txt` sin uso (i-7985ee-bb2fde);
   - las 14 inyecciones en la baseline de capas (i-7985ee-2bbbfd);
   - los 6 avisos de reintentos (i-7985ee-05c4a0).
5. Propuestas al bundle esperando al home: 5 de la primera jornada y las de este cierre.

**No verificado.**
- El runbook no se ejecutó.
- El login real contra authentik no se probó.
- MongoDB real, Modrinth y Pterodactyl no se tocaron.

**Medido.** `main` (S1) y `fase-0` (S2 y S3) en verde en CI. `fase-0` corrió los jobs `gate` y `image`,
con 21 tests.

## 2026-10-01 · s-7985ee-0a09db — S2 y S3: los datos de Mongo en un volumen con nombre, la imagen que arranca y el login con autorización

**Qué.** En la rama `fase-0`:
- S2 (i-7985ee-e21d5d, i-7985ee-a77cdc):
  - el compose con volúmenes con nombre, `mongo:${MONGO_VERSION:?}`, puertos en `127.0.0.1`,
    healthcheck y `service_healthy`;
  - `.env.yaml` montado de solo lectura;
  - el Dockerfile copia `main.py` y `src/`;
  - `run.sh` falla sin `.env.yaml` y `stop.sh` advierte contra `-v`;
  - un job `image` en CI;
  - `docs/runbooks/migrar-mongo-a-volumen.md`.
- S3 (i-7985ee-c64e06):
  - `authorize` como función pura, con la identidad `iss` + `sub` y migración en el primer login;
  - `UsersService.login` reemplaza a `provider_validate`;
  - sesión `https_only` y 12 h, CORS solo con orígenes nombrados, 401 si la sesión no tiene
    usuario, y `regex=` → `pattern=`.
- CI corre en cada push, no solo en `main`.

**Áreas.** `docker/`, `scripts/`, `main.py`, `src/library/auth/`, `src/services/users.py`,
`src/database/users.py`, `src/model/users/`, `src/routers/v1/auth.py`, `src/dependences.py`,
`.env.default`, `.github/workflows/check.yml`, `tests/`, `docs/`.

**Por qué.** El dueño pidió aplicar las decisiones. Aceptó las recomendaciones 1, 2 y 4. La 3
(¿hay datos en producción?) no se contestó: se supuso que sí.

**Arquitectura.** ✅ Cumple: la decisión de autorizar vive en el servicio y el router solo lo llama
(d-7985ee-16ed28).

**Qué salió mal en el camino.**
- La primera versión de `authorize` exigía `email_verified` siempre. La investigación previa a S2
  mostró que authentik lo manda en `false` por defecto desde 2025.10: nadie habría podido
  entrar. El grupo ya no lo exige; la lista de emails sí. Se vio fallar un test nuevo antes del
  arreglo.
- El script que esperaba a CI tenía un error de comillas y daba vueltas sin medir nada. Se cambió
  por un script en archivo.

**Qué quedó sin hacer.**
- Desplegar S2 y S3 siguiendo el runbook. El grupo de authentik y las variables nuevas son del
  dueño.
- `user_from_provider` (consulta a Mongo) sin test hasta S4.
- Un healthcheck de la API espera `/health` (S7).

**No verificado.**
- La imagen, el compose y su rechazo sin `MONGO_VERSION` no se probaron en esta máquina (no hay
  Docker). Los probó el job `image` de CI en la rama: todos los pasos en verde
  ([run 36886674154](https://github.com/fabaindaiz/modpack-creator/actions/runs/36886674154)).
- El runbook no se ejecutó.
- El login real contra authentik no se probó.

**Medido.** El gate local pasa con 21 tests (11 nuevos de autorización e identidad, contando el
del aviso de authentik).

## 2026-10-01 · s-7985ee-46eda5 — S1: dependencias con versión exacta y una regla del audit que lo exige

**Qué.**
- `docs/plan-de-trabajo.md`: las decisiones previas, la rutina de cada sesión, la fase 0 en siete
  sesiones, las fases siguientes con sus puntos de control, las cadencias y los riesgos. Enlazado
  desde `plan.md` y `AGENTS.md`.
- S1 (i-7985ee-bb2fde): `requirements.txt` con `==` en las 12 dependencias y sin el `fastapi`
  repetido. Una regla nueva en `scripts/audit.py`, `pinned_requirements`, con su test.

**Áreas.** `requirements.txt`, `scripts/audit.py`, `tests/test_audit.py`, `docs/`, `AGENTS.md`.

**Por qué.** El dueño pidió un plan de trabajo y empezar a implementarlo. S1 es la única sesión de
la fase 0 que no espera una de sus decisiones. La decisión 4 (versiones exactas) se tomó con la
recomendación del plan (d-7985ee-cd8f73), y es fácil de revertir.

**Arquitectura.** ✅ Cumple: d-7985ee-d0f29c sigue (una sola lista; la imagen y hatch quedan fijos
a la vez).

**Qué salió mal en el camino.** El docstring de la regla nueva citó primero un id de decisión
inventado como marcador. Se reemplazó por el acuñado antes de correr el gate.

**Qué quedó sin hacer.**
- Las transitivas sin fijar.
- `httpx` en `requirements.txt` sin uso en el código, con un aviso de deprecación de Authlib.
- S2 a S7 esperan las decisiones 1 a 3.

**No verificado.** CI con las versiones fijas: el cambio no está empujado. La imagen Docker no se
construyó.

**Medido.** La regla nueva marcó 14 fallos sobre el archivo anterior (13 sin versión y 1 repetida)
y 0 después. El gate pasa con 11 tests.

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
