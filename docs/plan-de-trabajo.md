# Plan de trabajo: cómo seguir, sesión por sesión

**Fecha:** 2026-10-01. [`plan.md`](plan.md) dice **qué** construir y en qué fases. Este documento
dice **cómo** trabajar: las decisiones que van antes, la rutina de cada sesión, la fase 0 partida en
sesiones concretas y las cadencias que mantienen el repositorio en orden. El detalle de cada ítem
(con qué choca, qué decidir) está en [`roadmap.md`](roadmap.md) por su id.

Las estimaciones son suposiciones, para ordenar y no para comprometer.

## 1. Antes de la primera sesión: cuatro decisiones

Bloquean la fase 0. Conviene tomarlas juntas, en una sola conversación.

| # | Decisión | Opciones | Recomendación | Bloquea |
|---|---|---|---|---|
| 1 | ¿Cómo llega la configuración al contenedor? | montar `.env.yaml` · `env_file` | **montar `.env.yaml` como solo lectura**: la app ya lo lee con `load_env(".env.yaml")`, y `env_file` exige `CLAVE=valor`, que no es el formato YAML de la plantilla | S2 |
| 2 | ¿Quién puede iniciar sesión? | lista de emails · grupo del IdP · los dos | **grupo del IdP**, más `email_verified`; la lista de emails como respaldo (`research/2026-10-arquitectura.md` §3) | S3 |
| 3 | ¿Producción ya tiene datos en MongoDB? | sí · no | si sí, **respaldo (`mongodump`) antes de S2**: hoy el volumen está comentado y recrear el contenedor borra la base | S2, S5 |
| 4 | ¿Cómo se fijan las dependencias? | versión exacta (`==`) · rango · lockfile | **versión exacta** en `requirements.txt`, como en `bluetooth-sync`; se suben a propósito, de a una, con CI | S1 |

## 2. La rutina de cada sesión

Es el *session loop* del método (`.agents/method/prompt-bootstrap.md` §The session loop), en los
términos de este repositorio. Un agente que lee `AGENTS.md` ya la sigue; aquí está para el dueño.

| Paso | Qué se hace aquí |
|---|---|
| 0. Brief | Leer `docs/roadmap.md` §Dónde estamos, las últimas entradas de `.claude/logs/agent-changelog.md` y `git status`. Decirlo en seis líneas (en curso, en el árbol, qué restringe, qué está viejo, qué cambia el pedido, fricción). Una línea vacía dice `nada`. |
| 1. Elegir y ponerle precio | **Un ítem del roadmap por sesión.** Leer su entrada completa y las decisiones que cita (`docs/decisions.md`). |
| 2. Preguntar todo junto | Las decisiones que son del dueño, en un solo mensaje, antes de escribir. Si el cambio toca estado, contratos, datos o seguridad: buscar en `.agents/knowledge/INDEX.md` y abrir solo las tarjetas que enlaza. |
| 3. Construir | **El test primero, y verlo fallar.** Extender antes que crear. Contrastar cada llamada externa con `docs/references.md` y `docs/research/`. |
| 4. Verificar | `./scripts/check.sh`. Decir lo que el gate no cubre (imagen, Mongo real, OIDC, APIs reales). Correr el chequeo de cada tarjeta usada. Si el cambio toca autenticación o borra datos, ofrecer una revisión con `knowledge-reviewer`. |
| 5. Mirar el resultado | Si hay salida visible (un endpoint, un archivo exportado), mirarla de verdad, no solo los números. |
| 6. Documentos al día | **En el mismo cambio:** el estado del ítem en el roadmap, una fila en `decisions.md` si se decidió algo, la baseline de `scripts/audit.py` si se achicó. |
| 7. Reportar | Qué se hizo, qué no, qué quedó sin verificar. Commit en Conventional Commits, sin trailers, **solo cuando el dueño lo pide**. |
| 8. Cierre | Una entrada en `.claude/logs/agent-changelog.md`, con qué salió mal en el camino y qué quedó sin hacer. Push, y **CI en verde** antes de dar el ítem por hecho. |

**Ramas:** para un ítem que toca datos de producción, autenticación o el compose (S2, S3, S5), trabajar
en una rama y abrir un PR: CI corre antes de llegar a `main`. Lo demás puede ir directo a `main`.

**Cómo pedírselo a un agente**, por ejemplo:

```text
Toma i-7985ee-e21d5d del roadmap. Sigue la rutina de AGENTS.md: brief primero,
el test antes del código, el gate verde, el roadmap y el changelog al día. No hagas commit.
```

## 3. La fase 0, en sesiones

El objetivo: **que lo que existe sea seguro, reproducible y arranque.** Son unos 3–4 días de trabajo.
El orden importa: cada sesión deja el gate verde y prepara la siguiente.

### S1 · Dependencias fijas · i-7985ee-bb2fde · ≈ 1 h
- **Qué cambia:** `requirements.txt` con `==` (sin el `fastapi` repetido), con las versiones que hoy
  instala el entorno de hatch (se leen con `importlib.metadata`).
- **Test primero:** una regla nueva en `scripts/audit.py` (toda línea de `requirements.txt` lleva
  `==`), con su test en `tests/test_audit.py`. Verla fallar sobre el archivo de hoy.
- **Hecho cuando:** el gate pasa y CI instala exactamente lo mismo que el entorno local.
- **Por qué primero:** hoy una release de cualquier dependencia puede romper CI o la imagen sin que
  cambie una línea del repositorio.

### S2 · Los datos sobreviven y la imagen arranca · i-7985ee-e21d5d, i-7985ee-a77cdc · ≈ medio día
- **Antes:** respaldo de producción si tiene datos (decisión 3).
- **Qué cambia:**
  - `docker/docker-compose.yaml`: volumen de Mongo, imagen de Mongo con versión fija (la que corre
    hoy, `db.version()`), puertos de 27017 y 8081 cerrados o atados a `127.0.0.1`, healthchecks y
    `depends_on: service_healthy`;
  - `docker/Dockerfile`: copiar `main.py` y `src/` conservando el paquete;
  - la configuración, según la decisión 1.
- **Test primero:** un job de CI que construye la imagen e importa `main` dentro de ella con
  variables falsas. Hoy tiene que fallar.
- **Hecho cuando:** ese job pasa, y en el servidor `./scripts/update.sh` levanta la API con los
  datos intactos.
- **Cuidado:** recrear contenedores en producción es el único paso irreversible de la fase.
  Tarjeta `derived-copy-goes-stale-silently` (la imagen es una copia derivada del código).

### S3 · Quién entra · i-7985ee-c64e06 · ≈ medio día
- **Qué cambia:**
  - la decisión de autorizar sale de `login_callback` a una función pura
    (`userinfo → permitido o no`);
  - `email_verified` obligatorio, y grupo o lista según la decisión 2;
  - no crear usuarios desconocidos, e identidad por `iss` + `sub`;
  - la sesión con `https_only` y un `max_age` de horas;
  - sin CORS comodín (d-7985ee-4e4aee pasa a tener enforcer);
  - `FORWARDED_ALLOW_IPS` si hay proxy.
- **Test primero:** la función pura con casos: grupo correcto, grupo ausente, email sin verificar,
  usuario desconocido.
- **Hecho cuando:** los tests pasan y un login real con una cuenta fuera del grupo es rechazado.
- **Cuidado:** los usuarios guardados hoy están identificados por email: hace falta migrarlos.
  Ofrecer la revisión con `knowledge-reviewer` (toca autenticación). Tarjetas
  `fail-closed-defaults` y `a-default-scope-is-the-widest-one`.

### S4 · MongoDB real en CI · i-7985ee-ade111 · ≈ medio día
- **Qué cambia:** `services: mongo` (la misma versión fija de S2) en `.github/workflows/check.yml`,
  y un fixture que hace `init_beanie` sobre una base descartable por sesión de tests.
- **Test primero:** el primer test contra la base real reproduce el bug de i-7985ee-055362 (un
  update que no escribe). Queda rojo y marcado como esperado hasta S5.
- **Hecho cuando:** CI corre tests contra Mongo y el local también (con un Mongo en Docker).
- Tarjeta `test-double-fidelity`: por eso no mongomock (`research/2026-10-arquitectura.md` §2).

### S5 · Los updates escriben y caben muchas versiones · i-7985ee-055362, i-7985ee-be55dd · ≈ medio día
- **Qué cambia:**
  - `project_update(_many)` y `server_update` guardan el dato nuevo;
  - sin `unique` en `ModrinthVersion.project_id`;
  - los `find_one({"id": …})` pasan a `version_id`.
- **Test primero:** el de S4, más una inserción de dos versiones del mismo proyecto.
- **Hecho cuando:** los dos tests pasan.
- **Cuidado:** Beanie no borra el índice único ya creado en producción. El borrado es un paso
  manual, documentado en el changelog y hecho con respaldo.

### S6 · Contratos y reintentos · i-7985ee-fb00ca, i-7985ee-05c4a0 · ≈ medio día
- **Qué cambia:**
  - rutas y cuerpos de Modrinth (`version_files`, con arrays) y de Pterodactyl (`command` y
    `files/delete` por POST con cuerpo JSON);
  - sin `backoff` sobre `Exception` en las escrituras.
- **Test primero:** un test por llamada contra un servidor aiohttp local que valida método, ruta y
  cuerpo según `docs/references.md`.
- **Hecho cuando:** `retried_writes` pasa de AVISO a FALLO en `scripts/audit.py` y el audit sigue
  en 0 fallos.

### S7 · Usuario de servicio y salud · i-7985ee-63226e, i-7985ee-90f944 · ≈ medio día
- **Qué cambia (operación):**
  - en el panel, un usuario no admin, subusuario de cada servidor con los permisos de
    `research/2026-10-operacion-pterodactyl.md` §1;
  - una clave `ptlc_` con `allowed_ips`, cargada en `.env.yaml`;
  - **la clave vieja se revoca**: esto también cierra el riesgo de la fuga de cabeceras corregida
    en el bootstrap.
- **Qué cambia (código):** `/health/live` y `/health/ready` (que hace ping a Mongo), y el
  healthcheck de la API en el compose los usa.
- **Hecho cuando:** la API opera con la clave nueva, la vieja ya no existe y el healthcheck se ve
  `healthy`.

### Hito: fase 0 cerrada
- Todos sus ítems en estado Hecha en el roadmap, con lo que quedó faltando.
- Correr el skill `state-review` y el harvest (`.agents/method/prompt-harvest.md`).
- Reescribir §Dónde estamos del roadmap.

## 4. Las fases siguientes, por bloques

Cada fase termina en un **punto de control**: una prueba con datos reales del dueño y una decisión
antes de seguir.

| Fase | Sesiones (≈) | Ítems, en orden | Punto de control |
|---|---|---|---|
| 1. Catálogo y resolución | 5–6 | i-7985ee-f4ba63 (con respuestas reales grabadas de Modrinth como fixtures) → i-7985ee-7f735a (la resolución como función pura, probada con esos fixtures) → i-7985ee-098611 (con migración de los bundles actuales) → i-7985ee-063a3a | resolver el pack que hoy corre en un servidor real y comparar el lock con lo instalado; decidir backtracking y reemplazos por pack o globales |
| 2. Saber qué tiene cada servidor | 3 | i-7985ee-5c7506 → i-7985ee-43df2f (opción 1) → i-7985ee-4364a3 | importar un servidor real **en solo lectura** (tarjeta `one-write-gate-makes-read-only-real`); decidir si se suma la opción 2 (packwiz) |
| 3. Operar | 5–6 | i-7985ee-0ec295 → i-7985ee-495708 (primero en modo simulación, después sobre un servidor de prueba) → i-7985ee-34933b | la primera actualización real, sobre un servidor poco crítico, con backup y el rollback ensayado; decidir horarios |
| 4. Distribuir y mirar | a demanda | i-7985ee-7352b3, i-7985ee-50d411, i-7985ee-1ba498 | — |

**Parar después de la fase 1 es un final válido**: la API ya arma packs correctos y explica cada
decisión, sin tocar ningún servidor.

## 5. Cadencias

| Cuándo | Qué |
|---|---|
| Cada sesión | la rutina de §2; CI en verde antes de cerrar un ítem |
| Al cerrar cada fase | skill `state-review`, harvest, §Dónde estamos reescrito |
| Cuando `agents-knowledge` publique una release | `.agents/method/prompt-update.md` antes del siguiente trabajo grande |
| Una vez al mes, o ante un aviso de seguridad | subir dependencias de a una, cada una en su commit con CI (posible desde S1) |
| Antes de cualquier cambio en producción | respaldo de Mongo; backup del servidor en Pterodactyl |
| Cuando cambie una API externa | actualizar `docs/references.md` y el informe en `docs/research/`; re-grabar los fixtures |

## 6. Riesgos

| Riesgo | Qué lo dispara | Cómo se mitiga |
|---|---|---|
| CI o la imagen se rompen solos | dependencias sin fijar | S1 (i-7985ee-bb2fde) |
| Pérdida de la base | recrear el contenedor de Mongo sin volumen | S2 primero, con respaldo |
| Daño en un servidor de juego real | escrituras reintentadas, `restore` que trunca, un 204 creído | d-7985ee-05d032 como fallo (S6); modo simulación y servidor de prueba en la fase 3 |
| Acceso indebido | cualquier cuenta del IdP entra | S3 antes de cualquier endpoint que escriba |
| Las APIs externas cambian | Modrinth ya deprecó campos; Minecraft cambió de numeración | fixtures grabados, `docs/references.md`, `state-review` |
| El alcance crece sin fin | 26 ítems en el roadmap | una sesión, un ítem; los puntos de control deciden si seguir |

## 7. Para empezar

1. Contestar las cuatro decisiones de §1 (sirve una línea por decisión).
2. Abrir una sesión con: *"Toma i-7985ee-bb2fde (S1 de docs/plan-de-trabajo.md) y sigue la rutina
   de AGENTS.md."*
3. Al cerrarla, pedir el commit y el push, y mirar que CI quede verde.
