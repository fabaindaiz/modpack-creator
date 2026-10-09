# Pendientes de la sesión del 2026-10-09: plan y decisiones

**Origen:** pedido del dueño (2026-10-09): «resolvamos los pendientes investigando los casos más
detalladamente, luego revisemos el plan en detalle de forma interactiva». **Investigación:**
`docs/research/2026-10-09-importar-servidor-como-trabajo.md` (S),
`docs/research/2026-10-09-flujo-actualizacion-y-caches.md` (U),
`docs/research/2026-10-09-api-modrinth-detalle.md` (M),
`docs/research/2026-10-09-curseforge-prism-y-deuda-panel.md` (C, P, B). **Estado:** en revisión con
el dueño, tema por tema; cada decisión se anota aquí al tomarla.

## Arreglos sin decisión (test primero, en este orden)

| Id | Qué | Dónde | Por qué no espera |
|---|---|---|---|
| A1 | La preparación de una actualización lista `/mods.staging/<job>` antes de crearla: Wings da 404 y el paso falla en sus 3 intentos (U, H1). El panel falso de los tests devuelve `[]` y lo esconde | `src/services/update.py:261-271`, `tests/_panel.py` | bloquearía la primera actualización real |
| A2 | `POST /v1/modpack/{id}/build` no toma la clave `build:<id>`: puede correr a la par del trabajo del panel y uno falla por el índice único tras gastar sus requests (U, H6) | `src/routers/v1/modpack.py` | carrera real |
| A3 | Las rutas `/v1` que usan `get_current_user` no fijan `CURRENT_USER`: esperan dentro de la petición en vez de dar 429 (U, H9) | `src/dependences.py` | el límite por usuario no rige ahí |
| A4 | Las lecturas de Modrinth reintentan un 404 (`backoff` sobre `Exception`): un slug que no existe cuesta 2 requests (S) | `src/library/api/client/modrinth.py` | requests gastadas |
| A5 | `project_get_many` sin trocear de a 100 en `/v1` y en la importación de servidor (S, U) | `src/services/{modpack,pterodactyl}.py` | consistencia con F6 |
| A6 | Código muerto: `projects_by_id`, `project_version_update_filter` (B2) | `src/services/modrinth.py` | limpieza |
| A7 | La pausa ante un 429 de Modrinth sin `Retry-After` es de 60 s: Modrinth no lo manda y su balde se rellena a 5/s (M) | `src/library/api/utils.py:81` | ver T3·D4: la pausa corta se decide ahí |

## Temas con decisiones (la recomendada primero; detalle y costos en cada documento)

### T1 · Importar un servidor como trabajo (S §5)
| Id | Decisión | Recomendada |
|---|---|---|
| S1 | De qué escaneo parte la importación | escanear primero, incremental |
| S2 | Cuánto buscar por jar desconocido | slug en lote; búsqueda por texto solo si el slug no dio un mod |
| S3 | Dónde viven los candidatos pendientes | checkpoint del último trabajo, filtrado por lo ya decidido |
| S4 | Escaneo completo semanal | `created_at` en la clave y completo cada 30 días |

### T2 · Escaneos viejos y actualizar un servidor (S §5, U §6)
| Id | Decisión | Recomendada |
|---|---|---|
| S5 | Escaneos guardados sin metadata | la importación baja una vez esos jars |
| U1 | Las descargas de la simulación | no bajar: verificar con los datos del build |
| U2 | Subida a staging | en lote (una URL y un POST por tanda) |
| U3 | El reescaneo tras actualizar (= S6) | sembrado con los hashes verificados |

### T3 · Cachés, `/v1` y el limitador (U §6, M)
| Id | Decisión | Recomendada |
|---|---|---|
| U4/M4 | Versión del loader en caché | U: 6 h (1 h si no hay versión), forzable, la vencida si la meta cae; M: 24 h |
| U6 | Lista de servidores del panel | guardada 5 min, refresco al cargar si está vieja, botón 1/min |
| U5/S7 | `/v1` y los trabajos | 202 + `Job` con las claves del panel |
| M5 | El limitador y las cabeceras de Modrinth | leer `X-Ratelimit-Remaining` (piso 30) y pausar 2 s ante un 429 |

### T4 · Revisión de actualizaciones y catálogo (M)
| Id | Decisión | Recomendada |
|---|---|---|
| M1 | Endpoint para mods con excepción de canal | `version_files/update_individual` (no está en la OpenAPI) |
| M2 | Qué versión se anuncia | solo posterior al lock y que el resolvedor tomaría; la release que anula una excepción, siempre |
| M3 | Versiones editadas en su sitio | al crear un build, releer en lote las versiones del lock (~2 requests) |
| M6 | Changelogs | en lote + guardar lo que trae la revisión diaria |

### T5 · CurseForge (C)
| Id | Decisión | Recomendada |
|---|---|---|
| C1 | CurseForge sí o no | opcional con clave propia del dueño; mientras, «expórtalo como .mrpack» |
| C2 | Lo que no está en Modrinth | informarlo, no bajarlo |
| C3 | Nombres de CurseForge en el informe | solo ids del manifiesto y enlace |
| C4 | Archivos `required: false` | «Por decidir» |
| C5 | Códigos de perfil | no |

### T6 · Prism (P)
| Id | Decisión | Recomendada |
|---|---|---|
| P1 | Qué sube el usuario | zip sin `saves/` con tope bajo; el completo leído del temporal si hace falta |
| P2 | `.jar.disabled` | «Desactivado» |
| P3 | Jars desconocidos | informarlos; candidatos después |
| P4 | `.pw.toml` | solo pistas |

### T7 · Panel (B) y textos
| Id | Decisión | Recomendada |
|---|---|---|
| B1 | Pestaña tras una acción del diálogo (y al agregar un mod) | la de la página (`HX-Current-URL`) |
| B3–B5 | Panel lateral con 404 dentro del panel; «con problema» en la ficha de una dependencia; los conteos de decisiones y excepciones en los informes de importación | hacerlos, con sus textos |
| TX | Los textos nuevos de la sesión sin aprobar (importador, trabajos, avisos, pack) | aprobarlos o cambiarlos |

### T8 · Overrides (`docs/research/2026-10-09-guardar-overrides.md`)
| Id | Decisión | Estado |
|---|---|---|
| O1 | Qué se guarda | espera la consulta del dueño con el tester |
| O5 | Lados (`both`/`server`/`client`) | por decidir |
| O6 | Qué trae la importación | por decidir |

## Después de la revisión

Cada tema decidido se vuelve tareas con test primero; el orden propuesto: A1–A7, T2, T1, T4, T3,
T7, T6, T5, T8. Lo que no se pueda probar sin un panel real (A1 contra Wings) se dice en el
reporte.

## Decisiones tomadas (dueño, 2026-10-09)

- **T1:** S1 escanear primero, incremental · S2 slug en lote, búsqueda por texto solo si falla ·
  S3 candidatos en el checkpoint del último trabajo de importación · S4 `created_at` en la clave y
  escaneo completo cada 30 días.
- **T2:** S5 la importación baja una vez los jars sin metadata · U1 la simulación no baja: verifica
  con los datos del build · U2 subida a staging en lote · U3 el reescaneo tras actualizar, sembrado
  con los hashes verificados.
- **T3:** U4/M4 versión del loader 24 h por (loader, versión de MC), forzable con «Actualizar desde
  Modrinth», la vencida si la meta cae · U6 lista de servidores guardada 5 min, refresco al cargar
  si está vieja, botón 1/min, la guardada si el panel falla, y todas las páginas · U5/S7 `/v1` con
  202 + `Job` y las claves del panel · M5 el limitador lee `X-Ratelimit-Remaining` (piso 30) y
  pausa 2 s ante un 429 sin `Retry-After`; 240 sigue de techo.
- **T4:** M1 `version_files/update_individual`: **una sola request con todos los hashes del pack**,
  cada uno con sus filtros (el dueño pidió que nunca sea de a un mod; el nombre del endpoint
  confunde: es en lote), 2 si hay excepciones; test de contrato propio porque no está en la
  OpenAPI · M2 se anuncia solo lo posterior al lock que el resolvedor tomaría; la release que anula
  una excepción, siempre, con ese texto · M3 al crear un build se releen en lote las versiones del
  lock · M6 changelogs en lote, y se guardan los que trae la revisión diaria.
- **T5:** C1 CurseForge opcional con la clave propia del dueño (`CURSEFORGE_API_KEY` en `.env.yaml`);
  sin clave, el panel sugiere exportarlo como `.mrpack` · C2 lo que no está en Modrinth se informa,
  no se baja · C3 en el informe, ids del manifiesto y enlace · C4 los `required: false` entran
  «Por decidir».
- **T6:** C5 los códigos de perfil de CurseForge **sí, con clave** (decisión del dueño, contra la
  recomendada): el endpoint no documentado queda aislado, con test de contrato propio; si falla, el
  importador lo dice («CurseForge no respondió al código; expórtalo como .zip») sin afectar lo demás;
  un código dura 7 días · P1 zip de Prism sin mundos, hasta ~200 MB; uno completo se lee desde el
  temporal sin descomprimir los mundos (tope ~1 GiB) · P2 `.jar.disabled` entra «Desactivado» ·
  P3 los desconocidos se informan; candidatos después, con la importación de servidores · P4 (mío,
  sin cambio visible) los `.pw.toml` solo como pistas: la identificación es por hash.
- **T7:** B1 tras una acción (diálogo o agregar un mod) queda la pestaña que se tenía · B3 el 404
  del panel lateral, dentro del panel: «Modrinth no conoce este mod.» · B4 la ficha de una
  dependencia que solo es problema: «<pack> · dependencia de <mod>, con problema» · B5 los informes
  de importación dicen «Marcados En el servidor: N · Aceptan beta: N» · TX los textos nuevos de la
  sesión, aprobados como están.
- **T8:** O1 sigue esperando la consulta del dueño con el tester · O5 tres lados por archivo
  (ambos, servidor, cliente), mapeados a las capas del `.mrpack`, por defecto según la ruta y
  editables · O6 al importar se traen los overrides de texto con su procedencia; binarios y jars se
  informan con el motivo. O5 y O6 se aplican cuando O1 se decida (si O1 fuera «ninguno», no aplican).

## Orden de trabajo acordado

1. A1–A7 (sin decisión; A1 primero: bloquearía la primera actualización real).
2. T2 actualizar un servidor (U1–U3) y T1 importar un servidor como trabajo (S1–S5).
3. T4 revisión de actualizaciones (M1–M3, M6).
4. T3 cachés, `/v1` y limitador (U4–U6, M5).
5. T7 panel (B1, B3–B5).
6. T6 Prism, luego T5 CurseForge (C1–C5).
7. T8 overrides, cuando O1 se decida.

## Traspaso (2026-10-09): lo que sigue va en una sesión cloud

El dueño cerró el build 0.2.0 con «lo hecho + U3» y sigue el resto en una sesión cloud.

- **Rama:** todo está en `main` (fast-forward, empujado el 2026-10-09, con `docs/deploy-fixes`);
  la sesión cloud parte de `main` en una rama nueva. Versión `0.2.0`; el tag `v0.2.0` y el
  despliegue los hace el dueño.
- **Hecho en este build:** A1–A6 (A3 entró con los arreglos de la revisión de cierre), U1–U3. A7 va
  con T3 (M5).
- **Sigue, en este orden:** T1 con S5 (importar un servidor como trabajo); T4 (revisión de
  actualizaciones con excepciones); T3 (cachés del loader y de la lista de servidores, el resto de
  `/v1` con trabajos, el limitador con cabeceras y la pausa de 2 s, que es A7); T7 (panel); T6
  (Prism); T5 (CurseForge: necesita la clave del dueño para probarse en real); T8 cuando O1 se
  decida. Cada decisión está arriba, en «Decisiones tomadas».
- **Entorno de la sesión cloud:**
  - Los tests de base necesitan un MongoDB: `TEST_MONGODB_URI`. Sin él se **saltan**, y el gate
    queda en verde y ciego en esa parte; nada que toque `src/database` o los servicios se da por
    verificado así. CI corre `mongo:8.0` siempre.
  - Usar `./scripts/test-mongo.sh` (un mongod nuevo por corrida): reusar uno para varias corridas
    lo hace crecer (i-7985ee-9b250c).
  - La revisión visual (`./scripts/visual-sweep.sh`) necesita Docker y Chromium; si la sesión cloud
    no los tiene, queda pendiente y se dice.
  - `.env.default` y `.env.yaml` no se leen (permisos del dueño): una variable nueva se le pide.
- **Pendiente del dueño, fuera del código:** volver a importar el servidor del tester tras desplegar
  (registra «En el servidor» y las excepciones de canal); la clave de CurseForge (T5); O1 (T8).
