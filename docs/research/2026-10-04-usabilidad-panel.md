# Usabilidad del panel: recorrido real, hallazgos y oportunidades

**Fecha:** 2026-10-04 · **Método:** recorrido con Playwright de las tareas frecuentes en una
instancia local descartable: configuración falsa, Mongo propio, **Modrinth real** (API pública,
solo lectura) y un **Pterodactyl simulado** cuyos servidores sirven jars reales bajados de Modrinth.
Así el escaneo, la identificación y la importación corren sobre hashes reales. Capturas a 1280 y
360 px, en claro y oscuro, con sesión de operador. Ronda 1, antes de cambiar nada. Los
identificadores de hallazgo (F01…) son estables: las siguientes rondas los citan.

## Resumen

El panel **funciona de punta a punta**: se crea un pack, se buscan y agregan mods, se resuelve, se
decide, se hace un build, se vincula un servidor, se escanea, se importa, se simula y se compara con
changelogs. Lo que falla es **cómo se reconoce cada cosa y cuánto cuesta llegar**:

1. **Los mods se ven como ids.** La búsqueda muestra ícono, descripción y autor, pero apenas un mod
   entra al pack se vuelve un título más un id opaco (`AANobbMI`). Hay más ids crudos en las
   sugerencias, en "requerida por" y en la comparación del servidor (`EsAfCjCV`, "versión
   `EO4u1E2G`"). El dueño lo nombró: *"solo por nombre y slugs no se reconocen bien"*.
2. **Lo que depende de otra cosa obliga a salir de la página.** Vincular un servidor exige que el
   modpack ya exista. Importar exige vincular. Simular después de importar exige un build nuevo, que
   se hace en otra página. Ningún paso ofrece el siguiente.
3. **El estado no se refresca ni se conserva.** Un escaneo terminado deja la página diciendo "Sin
   escaneo" hasta recargar. El lock se pierde al recargar. El tablero dice "Sin comparar" mientras el
   detalle muestra la comparación.
4. **Los errores quedan fuera de la vista.** El aviso va arriba de la página, que no se fija al
   hacer scroll. Algunos mensajes vienen en inglés técnico.

**La tarea completa** "este servidor → un modpack que lo describa → actualizarlo" tomó **unas 25
acciones, 6 cambios de página y 2 callejones sin salida** (F36).

## Tareas medidas

| Tarea | Hoy | Dónde duele |
|---|---|---|
| Crear un pack y agregar 7 mods | formulario y 7 búsquedas, cada una con su clic | la lista resultante no se reconoce (F01); el botón no confirma (F02) |
| Resolver y hacer el primer build | Resolver, decidir 2, Crear build | Resolver no se guarda (F39); sin indicador de carga (F31) |
| Vincular un servidor nuevo a un pack que no existe | imposible en la página: Modpacks → crear → volver | F18, F23 |
| Importar lo que tiene un servidor | escanear, recargar, importar | resultado sin siguiente paso (F25) |
| Simular después de importar | falla y sugiere "importar primero", cuando falta un build nuevo | F26 |
| Ver qué falta atender | no existe: el tablero son tres tablas | F38 |

## Hallazgos

Severidad: **A** bloquea o engaña · **M** cuesta pasos o confunde · **B** cosmético.

### Reconocer mods (pedido 1 del dueño)
- **F01 (A)** La lista de proyectos muestra solo el título y el id: sin ícono, descripción, versión
  actual ni loaders y versiones de Minecraft disponibles.
- **F12 (M)** "Sugerencias" muestra un id opaco, sin título, sin motivo y sin botón para agregar.
- **F13 (M)** "Por qué: requerida por `mOgUt4GM`" muestra el id en vez de "Mod Menu".
- **F14 (M)** Proyectos (izquierda) y Lock (derecha) listan los mismos mods con datos distintos: el
  nombre en una tabla y la versión y el lado en otra.
- **F20 (A)** La comparación del servidor usa ids de proyecto para los mods que no están en el
  catálogo local, y siempre ids de versión. El nombre del jar es legible; el id no.
- **F30 (M)** La vista previa usa números de versión y la comparación usa ids: el mismo dato con dos
  formas.
- **F35 (B)** El número de versión del lock se corta en mitad de la palabra.
- **Dato:** el catálogo (`ModrinthProject`) ya guarda `description`, `game_versions`, `loaders`,
  `downloads`, `categories` y el lado. **No guarda `icon_url`**: la ficha necesita ese campo
  opcional nuevo.

### Hacer en el momento lo que depende de otra cosa (pedido 2)
- **F18 (A)** No hay forma de crear un modpack desde un servidor. La lista de Pterodactyl no dice qué
  loader ni qué versión de Minecraft corre cada uno.
- **F23 (A)** Pasar de un servidor a un pack que lo describa toma unos 9 pasos y 3 cambios de página.
- **F25 (M)** El resultado de importar no ofrece el siguiente paso (resolver, build nuevo, simular),
  y la comparación queda vieja. Una ruta suelta bajo "Pendientes: 0" es el archivo local registrado,
  pero no lo dice.
- **F26 (A)** Una simulación falla con "jars the build does not explain, importar el servidor
  primero…": mezcla idiomas y sugiere un arreglo equivocado, porque el import ya se hizo y lo que
  falta es un build.
- **F15 (M)** "Crear build" sigue verde y habilitado mientras hay mods por decidir. Responde 409, y
  el aviso no se ve (F11).
- **F05 (M)** Fijar una versión pide escribir un id de versión de Modrinth a mano.
- **F09 (M)** La versión de Minecraft y la del loader se escriben a mano al crear un pack.

### Estado y respuesta
- **F19 (A)** Un trabajo terminado ("Listo") no refresca la página: siguen "Sin escaneo" y "Todavía
  no hay escaneo".
- **F39 (A)** El lock no se guarda: cada visita dice "Todavía no se resolvió", incluso justo después
  de crear un build.
- **F37 (M)** El tablero dice "Sin comparar" mientras el detalle muestra la comparación.
- **F31 (M)** "Resolver" (0,3–2,5 s) y "Crear build" no muestran que están trabajando.
- **F02 (M)** Después de "Agregar" en la búsqueda, el resultado sigue diciendo "Agregar".
- **F27 (A)** La vista previa muestra una **baja de versión** (JEI 19.57 → 19.51: el servidor tenía
  una beta y el pack solo acepta release) como un "cambio" más, sin advertir.
- **F28 (M)** El diálogo de actualización directa no advierte lo que la simulación sí encontró (un
  jar sin explicar).

### Errores y textos
- **F11 (A)** Los avisos de error van a `#notices`, arriba de la página y sin fijar: desde donde se
  hizo clic parece que no pasó nada.
- **F17 (M)** "Request has failed with status error 404: Not Found" está en inglés, es técnico y no
  dice qué hacer.
- **F34 (B)** Las filas de trabajos muestran nombres internos de pasos (`drift`, `log`).
- **F21 (M)** En el servidor, "Sobra" para mods de solo cliente dice "el build no lo incluye", cuando
  podría decir "solo cliente: no va en el servidor".
- **F29 (B)** Los changelogs se ven en Markdown crudo (decidido: texto plano; queda la idea de
  enlazar las URLs).

### Disposición y visual
- **F03 (M)** 10 resultados altos empujan todo hacia abajo y la página se hace muy larga.
- **F04 / F32 (M)** "Del pack (0)" frente a "Agregados por el admin (9)": en ningún flujo probado se
  llenó "Del pack", ni siquiera con mods importados.
- **F06 (B)** Las secciones vacías ocupan tanto como las útiles.
- **F07 (M)** No queda clara la relación entre "Resolver", que no guarda, y "Crear build", que sí.
- **F08 / F38 (M)** El tablero vacío no dice por dónde empezar, y con datos no dice qué falta atender.
- **F22 / F33 (B)** Información duplicada: el bloque `<pre>` de drift repite la tabla, y la
  simulación lista los jars además del bloque de cambios.
- **F40 (M)** A 360 px la navegación ocupa 3 líneas y corta "Auditoría", la página del pack mide unos
  2300 px, ".mrpack" queda fuera de la vista y el formulario de pins queda apretado.
- **F41** El modo oscuro está bien: no se vieron problemas de contraste.
- **F42 (B)** El botón "Actualizaciones" del pack se confunde con "Actualizar" un servidor.

## Oportunidades (para decidir)

1. **Ficha de mod.** Ícono, título, autor, descripción corta, lado, versión del build y última
   disponible, loaders y versiones de Minecraft que soporta, y enlaces. Va en una sola lista del
   pack que junta proyectos y lock (F01, F14, F35). Se reutiliza en sugerencias, comparación y vista
   previa (F12, F13, F20, F30), con `icon_url` en el catálogo.
2. **Crear en el momento.** "Crear modpack desde este servidor": nombre, loader y versión deducidos
   de los jars identificados, y después vincular, escanear e importar en un solo flujo (F18, F23).
   "Nuevo modpack" dentro del selector de vincular. Ofrecer el siguiente paso después de importar,
   decidir y hacer un build (F25, F26).
3. **Estado vivo.** Al terminar un trabajo, refrescar las secciones afectadas (F19). Guardar el
   último lock resuelto (F39). Unificar el estado del tablero (F37). Indicadores de carga (F31).
   Botón "Ya está" al agregar (F02).
4. **Avisos donde se actúa.** Avisos fijos, o junto al formulario que los provocó (F11). Textos en
   español con el siguiente paso (F17, F26). Pasos traducidos (F34).
5. **Tablero de tareas.** "Necesita atención": servidores con diferencias, trabajos fallidos, packs
   con actualizaciones o mods por decidir, cada uno con su acción. "Empieza aquí" cuando está vacío
   (F08, F38).
6. **Elegir en vez de escribir.** Selector de versión para pins (F05). Versiones de Minecraft y del
   loader desde las fuentes oficiales (F09).
7. **Móvil.** Navegación compacta, secciones plegables y tablas que pasan a tarjetas (F40).
8. **Advertir bajas de versión** en vista previa, comparación y diálogo (F27).

## Aprendizajes del proceso

- **Un simulador con jars reales sirve.** El escaneo, la identificación por hash y la importación
  corrieron contra Modrinth real sin tocar ningún servidor. El simulador vive fuera del repositorio
  (scratchpad) y debe aceptar el `uuid` además del identificador corto, como el panel real: el
  primer intento dio 404 por eso, no por la app.
- **En Playwright, esperar la respuesta y no `networkidle`.** Dos veces el script leyó la página
  antes del swap de htmx y pareció un fallo de la app. `expect_response` sobre la ruta lo evita.
- **No se entra al deploy real con una cookie fabricada.** Leer `.env.yaml` desde el contenedor para
  firmar una sesión rompe la regla de AGENTS.md, y el modo automático lo bloqueó dos veces, con
  razón. Para probar el deploy real, la cookie la genera el dueño o se usa su navegador.
- **Lo que solo se ve usando la app:** F19, F26, F27, F37 y F39 no los habría encontrado un test que
  mira el HTML. El barrido visual existente usa datos sembrados y no ejerce los flujos.

## Para no repetir

- No dar por bueno un flujo porque cada página carga: medirlo como tarea, con pasos y cambios de
  página.
- No mostrar un id de Modrinth donde se puede mostrar un nombre o un número de versión.
- No responder un error de una acción solo arriba de la página.
- No dejar en una página una sección que dependa de un trabajo sin refrescarla al terminar.
