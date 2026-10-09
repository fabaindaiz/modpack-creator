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
