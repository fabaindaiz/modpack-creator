# Tablero "Necesita atención" (F38): investigación

Fecha: 2026-10-05. Solo lectura del repositorio; investigación web con fecha de acceso 2026-10-05.
Todo lo marcado SIN VERIFICAR no se comprobó contra una fuente primaria o contra el código.
Licencias: solo se toman ideas; ningún texto, arte ni código de estas fuentes entra al repositorio.

## Resumen

El tablero actual (`src/routers/web/home.py`, `home.html`) ya calcula casi todo lo que hace falta:
estado de deriva por servidor (`ServerRow.state`), builds recientes, trabajos recientes y
conteo de actualizaciones por pack. Falta una capa que **filtre y ordene** eso en "qué hacer ahora".
Recomendación: una franja "Necesita atención" arriba de las tres tablas, con tarjetas de una sola
acción, ordenadas por severidad, tope de 5 y enlace "ver todos", y un estado vacío "Todo en orden"
que además muestra cuándo fue la última revisión.

## 1. Qué muestran herramientas comparables

Hallazgos (resultado de búsqueda; no se abrió cada documento completo, salvo donde se indica):

- **Pelican Panel** (licencia: SIN VERIFICAR; el repositorio es de código abierto): el tablero muestra
  tarjetas por servidor con punto de estado, nodo, IP:puerto, uptime y barras de CPU/RAM/disco; los
  estados de contenedor son cinco: Running, Starting, Stopping, Offline, Missing. Sus
  comprobaciones de salud devuelven `ok`, `warning`, `failed`, `skipped`, `crashed`. Una discusión
  de usuarios pide avisos de caídas, errores de Wings y cambios administrativos por webhook,
  Telegram o correo. Lección: el estado es por servidor y a simple vista; los avisos
  son una petición abierta, o sea que ni siquiera la referencia los resuelve bien.
  - https://deepwiki.com/pelican-dev/panel/7.5-health-checks-and-monitoring (solo el extracto de la
    búsqueda; el fetch dio 429)
  - https://github.com/pelican-dev/panel/discussions/42 (leído)
- **Crafty Controller**: el tablero muestra CPU, memoria y disco del anfitrión y el estado de cada
  servidor, con gráficas de jugadores. Es estado, no tareas: no hay una lista "qué falta atender"
  que se haya encontrado. https://docs.craftycontrol.com/ (solo resultado de búsqueda)
- **PufferPanel**: no se encontró información útil en esta ronda. SIN VERIFICAR.
- **AMP (CubeCoders)**: tiene monitoreo en vivo y actualización de una instancia desde la página
  de Métricas y estado. No se encontró un indicador "hay actualización" en el tablero.
  https://discourse.cubecoders.com/t/how-to-update-amp-to-the-latest-version/2297 (resultado de
  búsqueda)
- **Modrinth Hosting / Modrinth App**: detecta caídas por código de salida o estado de energía y
  analiza los registros para sugerir una causa; la pestaña Contenido tiene un modal "Actualizar
  todo" donde se elige la versión de cada proyecto y se ven los changelogs. Es el patrón más
  cercano a "hay actualizaciones": una acción masiva con vista previa.
  https://modrinth-code.mintlify.app/hosting/managing-servers (sitio de terceros que documenta
  el repositorio de Modrinth; confirmar contra Modrinth antes de citarlo)
- **CurseForge**: no se investigó a fondo. SIN VERIFICAR.
- **Renovate** (Dependency Dashboard, un issue de GitHub): agrupa por *por qué está detenido*:
  Pending Approval, Awaiting Schedule, Errored, Edited/Blocked. Es una lista de tareas con casillas
  que actúan. Lección: agrupar por motivo y poner la acción en la misma fila.
  https://github.com/renovatebot/renovate/issues/12414 (resultado de búsqueda)
- **Dependabot**: las alertas se ordenan por "Most important" (impacto, accionabilidad, relevancia),
  se filtran y se descartan con motivo, y un descarte se puede reabrir. Lección: ordenar por
  importancia, no por fecha; permitir descartar con motivo (para el futuro, no para la v1).
  https://docs.github.com/en/code-security/dependabot/dependabot-alerts/viewing-and-updating-dependabot-alerts (leído)
- **Uptime Kuma**: resumen con Total, Up, Down, certificados por vencer; un monitor nuevo empieza
  en "Unknown" hasta el primer latido. Lección: separar "Sin comparar/Sin escaneo" (desconocido) de
  "Desviado" (problema), como ya hace el panel. (resultados de búsqueda; varias guías de terceros)
- **NN/g, estados vacíos**: tres tareas: comunicar el estado del sistema, enseñar algo y dar un
  camino directo a la tarea. Aplica a "Todo en orden" y a "Empieza aquí".
  https://www.nngroup.com/articles/empty-state-interface-design/ (leído)

Patrón común: (a) el estado va en el objeto (servidor, pack), con un color y una palabra, no solo
color; (b) las herramientas que listan tareas (Renovate, Dependabot) agrupan o ordenan por
importancia y ponen la acción junto al elemento; (c) ninguna de las de servidores de juego lo
resuelve, así que aquí no hay un estándar que copiar. El estudio previo
`docs/research/2026-10-plataformas-similares.md` no trata el tablero (no hay coincidencias de
"tablero" o "atención" en él).

## 2. Candidatos de atención en este panel, por urgencia

Severidades: **Crítica** (algo falló o puede estar roto en un servidor real), **Alta** (decisión
tuya bloquea un build o un despliegue), **Media** (hay trabajo disponible), **Baja** (limpieza).

| # | Ítem | Severidad | Una acción | Enlace existente |
|---|---|---|---|---|
| 1 | Actualización de servidor fallida con rollback posible (`JobService.rollback_state` devuelve un estado) | Crítica | "Revisar y revertir" | `/servers/{id}` (la página ya ofrece A y B) |
| 2 | Trabajo fallido reciente (`Job.status == "failed"`) de actualización o rollback, aún no superado | Crítica | "Ver el error" | `/jobs/{id}` o `/jobs?status=failed` (SIN VERIFICAR que la ruta de detalle exista con ese nombre) |
| 3 | Servidor desviado de su build (`state == "drift"`) | Alta | "Ver diferencias" | `/servers/{id}` |
| 4 | Pack con mods por decidir (`lock.undecided`) o problemas de resolución (`lock.problems`, `not lock.complete`) | Alta | "Decidir" / "Ver problemas" | `/modpacks/{id}` (sección de resolución) |
| 5 | Servidor con modpack, build recomendado más nuevo que el instalado (`installed_build`) | Media | "Actualizar servidor" | `/servers/{id}` |
| 6 | Pack con actualizaciones de Modrinth sin fijar (`update_counts`) | Media | "Ver actualizaciones" | `/modpacks/{id}/actualizaciones` |
| 7 | Pack con cambios sin build (la resolución guardada tiene otro `request_digest`, o sin builds) | Media | "Crear build" | `/modpacks/{id}` |
| 8 | Revisión de actualizaciones con error (`UpdateCheck.error`) | Media | "Reintentar revisión" | `/modpacks/{id}/actualizaciones` |
| 9 | Escaneo viejo o ausente ("Sin escaneo", o `scanned_at` más antiguo que el doble del intervalo) | Media | "Escanear ahora" | `/servers/{id}` |
| 10 | Servidor sin modpack (`bundle_id is None`) | Baja | "Crear modpack desde este servidor" o "Vincular" | `/servers/{id}` (ya tiene el flujo de la ronda 1) |
| 11 | Servidor con "Modpack desconocido" (`bundle_id` apunta a un pack borrado) | Media | "Vincular otro pack" | `/servers/{id}` |
| 12 | Candidatos de importación sin confirmar | Baja | "Revisar candidatos" | `/servers/{id}` (SIN VERIFICAR dónde viven hoy) |

Notas de criterio:
- **Un servidor, una tarjeta.** Los ítems 1, 3, 5, 9, 10 y 11 caen sobre el mismo servidor: mostrar el
  más grave y listar el resto como etiquetas pequeñas, para no repetir el nombre cinco veces.
  Un pack con los ítems 4, 6 y 7 igual.
- **Un trabajo activo** (`active_for`) del servidor suprime sus tarjetas y muestra "en curso":
  el estado ya cambiará (la página del servidor se refresca al terminar, ronda 1).
- El ítem 2 se solapa con el 1: si el fallo ya tiene rollback ofrecible, solo sale el 1. Si fue un
  escaneo fallido repetido, es Media; un `server_update` o `server_rollback` fallido es Crítico.
- Un fallo "superado" (hubo después un trabajo correcto del mismo `key`) se oculta: sin esto el
  tablero acumula ruido que nunca se va. Esto requiere definir "superado" (decisión del dueño).
- Respetar roles: Actualizar y revertir son de operador (`operator_only` en los servicios). Un editor
  ve la tarjeta pero el botón lleva a la página, que ya negará; mejor ocultar la acción o mostrar
  "pide a un operador".
- Dependabot/Renovate sugieren poder **descartar** con motivo; para este panel quedaría fuera de la
  primera versión (necesita un documento nuevo).

## 3. Diseño y texto (interfaz en español)

- **Conteo arriba, lista debajo.** Una línea: "Necesita atención: 4" con chips por severidad
  ("1 crítico · 2 altos · 1 medio") que son enlaces ancla. El conteo solo no basta (no dice qué
  hacer); la lista sola abruma. Uptime Kuma y Dependabot hacen lo mismo: resumen + lista.
- **Orden:** severidad, luego antigüedad del problema (el más viejo primero: lleva más tiempo roto),
  luego nombre. No ordenar por fecha sola.
- **Cuántos antes de "ver todos":** 5 (SIN VERIFICAR contra investigación; es una heurística
  razonable y barata de cambiar). Si hay críticas, mostrarlas siempre todas aunque pasen de 5.
  "Ver todos (12)" despliega el resto con `<details>` (sin JS inline, por la CSP del panel) o lleva a
  una vista con filtros.
- **Cada tarjeta:** nombre del objeto (enlace) · qué pasa en una frase · desde cuándo · un botón.
  Verbo en infinitivo y concreto: "Revertir", "Decidir mods", "Ver diferencias", "Escanear ahora".
  Evitar "Resolver" suelto: F07 ya mostró que se confunde con "Crear build". Evitar
  "Actualizaciones" vs "Actualizar" (F42): usar "Ver actualizaciones disponibles" para el pack y
  "Actualizar servidor" solo en el servidor.
- **Señal no solo con color:** icono o palabra ("Crítico", "Alto"), contraste en modo claro y oscuro;
  la insignia ya existente (`ui.badge` con tonos `warning`/`accent`/`neutral`) sirve.
- **Estado vacío "Todo en orden":** tres cosas, según NN/g: estado ("Todo en orden: 3 servidores
  coinciden con su build; última revisión hace 2 h"), qué verás aquí, y una vía directa. Si no hay
  nada registrado, otro mensaje: "Empieza aquí" con "Registrar servidores" y "Crear un modpack"
  (F08). No mezclar los dos casos: "sin datos" no es "todo bien".
- **Honestidad sobre lo desconocido:** si un escaneo nunca corrió, no decir "todo en orden". Contarlo
  como atención Media ("Sin escaneo") o separar "no comprobado: 2".
- **Móvil (360 px):** una columna, tarjetas apiladas con el botón a ancho completo (objetivo táctil
  de 44 px como mínimo, SIN VERIFICAR contra WCAG: es la cifra habitual), la franja arriba de las
  tablas, y las tres tablas existentes plegables (F40 pide tablas que pasan a tarjetas). El
  texto del problema se corta a 2 líneas con el detalle en la página del objeto.
- **Actualización viva:** el tablero ya se refresca al terminar un trabajo en la página del servidor;
  para el tablero, un `hx-trigger="every 60s"` sobre la franja es suficiente y evita recalcular en
  cada vista (ver costo, abajo).

## 4. Costo de datos

Lo que muestra el repositorio hoy:

- `home` ya carga: `latest_builds()` (una consulta de bundles + una `$or` de builds),
  `server_find()` y, **por cada servidor**, `scan_latest` (una consulta cada uno, con sort por
  `scanned_at` y límite 1; hay índice solo en `server_id`, no compuesto con `directory` ni
  `scanned_at`: SIN VERIFICAR el plan de la consulta con volúmenes reales). `jobService.recent(10)` usa
  el índice de `created_at`. `latest_many(...)` trae el último `UpdateCheck` de cada pack en una consulta
  (índice `bundle_id, checked_at, _id`).
- `ServerScan.files` es una lista incrustada con hashes por jar: cada `scan_latest` trae el documento
  completo, y `scan_find` (que usa `schedule_scans` y `scan_set_drift`) trae **todos** los escaneos
  completos. Para el tablero, una proyección sin `files` evitaría leer cientos de KB por servidor
  (SIN VERIFICAR el tamaño real; es solo la forma del modelo).

Por ítem:

| Ítem | Costo | Por qué |
|---|---|---|
| 3 deriva, 9 escaneo viejo/ausente, 10 sin pack, 11 pack desconocido | **Barato** | ya está en `ServerRow` (`state`, `scanned_at`, `bundle_id`); sin consultas nuevas |
| 6 actualizaciones | **Barato** | ya se calcula (`update_counts`) |
| 8 revisión con error | **Barato** | `UpdateCheck.error` ya viene en `latest_many` |
| 2 trabajo fallido | **Barato a medio** | `Job.find(status="failed")` con `created_at` descendente ya usa el índice `status, created_at`; falta el filtro "superado" (comparar con el último trabajo del mismo `key`: una consulta por clave, con el índice `key, created_at`) |
| 1 rollback ofrecible | **Medio** | `rollback_state` hace 2 a 3 consultas por servidor (`last_real_update`, `get`, `rollbacks_of`), con índices ya hechos. Para N servidores son 2N a 3N consultas; aceptable con pocos servidores, vigilar si crecen. Una consulta única "último `server_update` real fallido por `key`" evitaría el barrido (nuevo, SIN VERIFICAR) |
| 5 build instalado vs recomendado | **Medio** | `installed_build` usa `last_real_update` por servidor; mismo costo que el 1, y se puede compartir la consulta con él |
| 4 por decidir / problemas | **Medio a caro** | `ModpackResolution` guarda el lock completo (con `undecided` y `problems`), indexado por `bundle_id` (único). Leerlo es barato pero trae el lock entero; no hay campos resumidos. Un resumen guardado (conteos) o una proyección lo abarataría. **No** volver a resolver en el tablero: la resolución llama a Modrinth |
| 7 cambios sin build | **Medio** | hay que comparar el `request_digest` guardado con el de la solicitud actual (como ya hace la página del pack); calcular el digest es local, sin red. La resolución puede ser vieja o inexistente, y entonces el ítem se calla en vez de adivinar |
| 12 candidatos de importación | **SIN VERIFICAR** | no se vio dónde se guardan hasta confirmar |

Consideraciones:
- **Sin llamadas externas en el tablero.** Pterodactyl solo se lee ya en escaneos del trabajador; el
  tablero debe seguir leyendo solo MongoDB (el panel real no es observable desde el entorno de
  desarrollo, AGENTS.md).
- Un servicio nuevo (por ejemplo `AttentionService`) debe ser el único que el router inyecte (invariante
  de capas), con una función pura que reciba lo ya cargado y devuelva la lista ordenada: fácil de
  probar sin base. El cálculo duplicado de `home.py` (`_server_row`) puede reutilizarse.
- Índices nuevos: **ninguno es imprescindible** para una v1 con pocos servidores y packs. Candidatos
  si crece: `ServerScan (server_id, scanned_at desc)`, y una proyección sin `files`. Cualquier
  índice nuevo debe pasar por `init_beanie` y el registro de `src/model/__init__.py`.
- Caché: el dato cambia con trabajos y escaneos; un recálculo por vista es aceptable. Si pesa,
  guardar un documento "resumen" que actualice el trabajador, pero eso es estado derivado que
  puede quedar viejo en silencio (la nota de `schedule_scans` ya advierte de eso): preferir calcular
  al vuelo.

## Preguntas para el dueño (para decidir, no decididas aquí)

1. ¿Un fallo "superado" desaparece solo, o se descarta a mano con motivo (como Dependabot)?
2. ¿"Sin escaneo" y "servidor sin modpack" cuentan como atención, o solo se muestran como estado?
3. ¿Tope de 5 con "ver todos", o mostrar todo con las críticas primero?
4. ¿Los editores ven las tarjetas que solo un operador puede actuar?

## Qué no cubre esta investigación

- No se abrieron las páginas de Crafty, PufferPanel ni AMP más allá de los resultados de búsqueda;
  no hay evidencia de que alguna tenga una lista de tareas.
- No se midió ningún tiempo de consulta ni tamaño de documento: las notas de costo salen de leer el
  modelo y los índices declarados.
- La búsqueda de Modrinth Hosting proviene de documentación de terceros.
