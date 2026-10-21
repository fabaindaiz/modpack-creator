# Observaciones de la ronda 3 (2026-10-21)

Lo que el dueño y un tester vieron usando la versión desplegada, más los pedidos que salieron de ahí.
Se revisan una por una; **el plan se arma cuando todas estén cerradas** (dueño, 2026-10-21). Cada una
dice su estado: abierta (falta una decisión o un dato), decidida, o sin acción.

## Observaciones

### O1. Importar un zip de Prism se cuelga y termina en «bad gateway» — abierta

El tester subió el zip de una instancia de Prism: la página quedó esperando un rato y respondió
502. Hoy `import_prism` lee el zip entero dentro de la petición (`src/services/modpack_import.py`,
`import_prism`); el trabajo de importación empieza recién después. Hipótesis sin verificar: la lectura
pasa los 60 s que nginx espera por defecto (`proxy_read_timeout`) o el proceso se cae. Falta el log del
servidor para saber cuál.

Pedido del dueño que la generaliza (O6).

### O2. Un pack importado por URL de Modrinth deja mods «sin versión soportada» — abierta (decisión)

MixinTrace Reborn y KotlinLangForge quedaron como problema tras resolver: sus versiones en el pack no
tienen la versión de Minecraft o el loader del pack en sus etiquetas de Modrinth, y la tarjeta solo
ofrece quitarlos o reemplazarlos. Hoy importar no fija nada (docstring de `modpack_import.py`,
d-7985ee-58801d): la resolución vuelve a elegir cada mod con los filtros. El tester propone aceptar
las versiones exactas del pack y validar solo al agregar o actualizar.

Opciones puestas al dueño (2026-10-21), sin respuesta todavía:

- **A** (recomendada): las versiones exactas del pack se aceptan, marcadas «versión del pack (fuera de
  las etiquetas de Modrinth)»; agregar o actualizar valida como hoy. ~1–2 sesiones; cambia la regla
  «importar no fija nada».
- **B**: se valida como hoy, con un botón por tarjeta «Usar la versión del pack igual». ~media sesión.
- **C**: como A, solo para packs publicados en Modrinth o CurseForge.

**Respuesta del dueño (2026-10-21):** «Importar un modpack si ya trae versiones fijadas debería
importarlas igualmente; total, siempre podemos volver a resolver un modpack manualmente.» Sobre la
propuesta del tester: que quede con esa marca **y además se valide**; el pin queda con sus orígenes
marcados y guardados, y se puede elegir cuál usar. Lo amplía en O8.

### O8. Versiones resueltas con historia, fuente y conocimiento — abierta (pedido del dueño, grande)

«Que fijar una versión no sea una acción única que luego se sobrescriba, sino que queden versiones
resueltas del modpack con su información, fuente, y quizás que se puedan añadir comentarios y revisiones
de si funcionó, y espacio para escribir errores, y herramientas para entender incompatibilidades,
marcarlas y añadirlas a alguna base de conocimiento. Esto es algo grande y de mucha ayuda.»

Lectura (supuesto, a confirmar al diseñarlo): cada mod del pack guarda varias versiones candidatas, cada
una con su origen (el pack importado, el admin, la resolución, un servidor escaneado) y su validación;
el pack elige cuál usa; y alrededor, notas por versión (funcionó, falló, el error), incompatibilidades
marcadas, y una base de conocimiento que las junta. Necesita su propia spec.

### O3. El texto guía cuesta entenderlo — decidida en su forma

El tester pide un globo al pasar el cursor o un botón de ayuda con un tutorial. Opciones puestas
(2026-10-21): un «?» por paso que despliega la explicación (sirve en el teléfono) más una página «Cómo
funciona» (recomendada); solo el globo (no hay hover en el móvil); solo un botón con un tutorial.

**Respuesta del dueño (2026-10-21):** la explicación con el «?»; y más allá de la ayuda: «mejorar harto
las ayudas y mejorar los flujos para hacerlos más entendibles: acciones similares juntas, procesos en
orden fáciles de seguir y más usable en general» (O9).

### O9. Flujos más entendibles — abierta (pedido del dueño)

Agrupar las acciones parecidas, ordenar los procesos para que se sigan solos y mejorar la usabilidad en
general. Pide una revisión de flujos (user-walk) antes de diseñar.

## Orden (dueño, 2026-10-21)

Primero el bundle de `.agents/` a 0.0.32, con la cosecha y la actualización completa; después, el orden
del plan, que se arma cuando todas las observaciones estén cerradas.

### O4. Escanear el servidor «Ra Monke» da 500 — abierta (falta el log)

Sin el log del servidor no se puede reproducir. Pedido al dueño: la salida de
`docker compose -f docker/docker-compose.yaml logs api --since 72h | grep -iE "error|traceback|killed|500"`
y la versión desplegada.

### O5. La auditoría le gustó — sin acción

### O6. Toda tarea larga corre como trabajo — abierta (pedido del dueño)

«Que importar un zip (y todas las tareas largas) funcione dentro de un job: cualquier tarea que en la
práctica dure más de 10 o 20 segundos, porque podría dejar al usuario sin saber si se está
procesando.» Inventario de lo que hoy corre dentro de la petición: en curso (abajo).

### O7. Juntar información de uso y de errores en un archivo — abierta (pedido del dueño)

«Que se pueda juntar info de uso y errores de una forma que luego pueda pasarte ese archivo para
entender qué ocurrió y cómo mejorarlo, patrones de uso que optimizar.»

Lo que hay hoy: logs JSON por línea a stderr con `request_id`, una línea por petición (método, ruta,
estado, duración; sin query string) en `src/library/logging.py`; solo viven en `docker logs`. La
auditoría (`AuditEvent`, MongoDB) guarda cada acción de escritura con su actor. Los trabajos guardan
su error. Nada junta todo eso en un archivo descargable.

## Investigación

### Lo que hoy tarda dentro de la petición (para O6; inventario del código, 2026-10-21)

Casi todo lo pesado ya es un trabajo: escanear desde el panel (`server_scan`), actualizar desde
Modrinth (`catalog_refresh`), crear un build (`build_create`), buscar actualizaciones (`update_check`),
importar por URL o código (`modpack_import`), importar un servidor (`server_import`) y actualizar o
hacer rollback de un servidor (`server_update`, `server_rollback`). «Resolver de nuevo» solo lee el
catálogo, y si le falta algo encola `catalog_refresh` (`CatalogPending`). El panel muestra el avance
consultando cada 2–3 s (`_job_progress.html`, el aviso del pack, «Mis trabajos»).

Lo que todavía trabaja dentro de la petición:

| Ruta | Por qué puede tardar | Trabajo relacionado |
|---|---|---|
| `POST /modpacks/importar` con un zip de Prism, y `POST /v1/modpack/import/mrpack` | descomprime y calcula sha1 y sha512 de hasta 1 GiB de jars antes de encolar (`read_prism`, en un hilo) | `modpack_import` solo hace la parte de Modrinth |
| las mismas rutas con un `.mrpack` o un zip de CurseForge | hasta 200 MiB descomprimidos; corre en el bucle de eventos, así que además bloquea las demás peticiones 1–3 s | `modpack_import` |
| `POST /v1/server/{id}/scan` | el escaneo completo dentro de la petición (descarga y hash de cada jar): minutos con 200 mods | `server_scan` ya existe; la ruta es el duplicado síncrono viejo de `POST /v1/server/{id}/scan/job` |
| `POST /modpacks/{id}/projects` con un puente activo | `bridge_companions_missing` resuelve el mod solo **sin** `local_only`: hasta 32 rondas de llamadas a Modrinth, una por proyecto que falte, más dos llenados del catálogo de hasta 5 s | ninguno; `catalog_refresh` hace la misma búsqueda |
| `GET /modpacks/{id}/actualizaciones`, `GET /modpacks/{id}/builds/compare`, `GET /servers/{id}/update/preview` | changelogs con tope de 10 s más un llenado de 5 s: hasta ~15 s | `update_check` hace la búsqueda; la vista previa no |

Lo demás (agregar por slug sin puente, fijar, refrescar servidores, las fichas de mods) es una o pocas
llamadas en lote, con topes de 3–5 s.
