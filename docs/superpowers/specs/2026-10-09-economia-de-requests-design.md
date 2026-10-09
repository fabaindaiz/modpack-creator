# Economía de requests: caché, límites y trabajos con avance

**Fecha:** 2026-10-09 · **Origen:** pedido del dueño (2026-10-09): "el número de requests es muy
importante: limitarlas por usuario, juntar las que se puedan, cachear en la base toda la información
posible y no recargar la información de un modpack más de una vez cada 5 minutos, salvo a mano";
y después: "todo lo que sea varias requests, en trabajos que esperan su turno y muestran el progreso;
el botón no genera un trabajo duplicado si ya corre; siempre se ve qué se procesa y que la app no se
quedó pegada". **Investigación:** `docs/research/2026-10-09-auditoria-requests.md`.

## Decisiones del dueño (2026-10-09)

| | Decisión |
|---|---|
| E1 | La información de Modrinth de un modpack se pide como mucho una vez cada 5 minutos, salvo el botón manual. |
| E2 | Crear un build no usa la ventana: antes revisa cambios (2–3 requests agrupadas), porque un build queda congelado. |
| E3 | Botón manual «Actualizar desde Modrinth»: datos y versiones de los mods del pack, agrupados; una vez por minuto por pack. La búsqueda de actualizaciones sigue aparte. |
| E4 | Cupo de 60 requests a Modrinth por minuto por usuario y tope global de 240 por minuto (Modrinth corta a 300); se respeta `Retry-After`. Después: «si hay margen, no tan estrictos» — el cupo por usuario solo rige cuando se gastó la mitad del minuto de la app. |
| E5 | Sin cupo, el panel avisa «Demasiadas consultas a Modrinth: intenta en N s»; los trabajos de fondo esperan su turno. |
| E6 | La primera carga de un pack (más requests que el cupo) es un trabajo de fondo que espera el tope global y no gasta el cupo del usuario; lo pedido queda guardado aunque falle a la mitad; al terminar, el pack se resuelve solo. |
| E7 | Todo lo que haga varias requests es un trabajo: espera su turno, muestra su avance y no se duplica (pulsar de nuevo muestra el que corre). |
| E8 | El avance se ve en una franja arriba de la página del pack (qué se procesa, paso, N de M, hace cuánto se actualizó, si espera turno) y, en general, en la barra superior: los trabajos abiertos del usuario con su avance y atajos al detalle (también subir a un servidor, respaldos). |
| E9 | Un build es un trabajo (revisa cambios, resuelve, congela); si queda bloqueado, el diálogo de decidir y arreglar se abre al terminar, y si la página ya no está abierta la franja lo dice con un enlace. |

Decididas por mí, reversibles y sin cambio visible: los datos de un proyecto fuera de un pack valen
24 h; un id que Modrinth no conoce se recuerda 24 h; las listas de ids van de a 100; la búsqueda se
guarda 10 minutos; leer y decidir con datos de menos de 5 minutos no hace ninguna request.

## Piezas

1. **Límite** (en proceso: la app corre en un solo proceso uvicorn): un balde global de 240/min
   para el cliente de Modrinth y uno de 60/min por usuario, atribuido por un `contextvar` que la
   petición del panel fija con el actor. Un 429 de Modrinth pausa el balde global hasta su
   `Retry-After`, y los reintentos a ciegas de las lecturas dejan de reintentar un 429. En el panel,
   sin cupo, 429 con los segundos; en un trabajo, se espera y el trabajo lo muestra.
2. **Frescura del catálogo**: por proyecto y objetivo (versión de Minecraft y loaders) se guarda la
   lista de ids de versión que Modrinth listaba y cuándo se pidió completa; por pack,
   `modrinth_checked_at`. Detectar cambios es una request agrupada de proyectos (de a 100) que
   compara su lista de versiones con la guardada, y otra de las versiones nuevas por id. Una versión
   que el proyecto ya no lista no se usa.
3. **Resolver desde la base**: con todas las listas presentes y el pack revisado hace menos de 5
   minutos, 0 requests y en el acto (decidir, quitar, fijar, aceptar beta siguen siendo inmediatos).
   Si falta algo o pasó la ventana, se encola (o se muestra) el trabajo de catálogo del pack.
4. **Trabajos con avance**: `Job` gana `progress` (hecho, total, qué) y `waiting_until`. Un trabajo
   por pack a la vez (la clave `bundle:<id>`, que ya comparte la búsqueda de actualizaciones):
   pulsar de nuevo devuelve el activo. Tipos nuevos: `catalog_refresh`, `build_create`,
   `modpack_import` (desde Modrinth, o la identificación de un archivo ya leído), y la importación de
   un servidor.
5. **Franja del pack y barra superior**: la franja sondea la fila del trabajo activo del pack; la
   barra, los trabajos activos del usuario. Las dos dicen hace cuánto cambió el trabajo.
6. **Lecturas de una request**, servidas de la base: el selector de versiones desde el catálogo; la
   búsqueda, guardada; el relleno de fichas con TTL y caché negativa; changelogs agrupados.

## Orden

F1 límite y `Retry-After` · F2 frescura y resolver desde la base · F3 trabajos con avance, trabajo de
catálogo, botón manual, franja y barra · F4 build como trabajo · F5 importaciones como trabajos ·
F6 lecturas desde la base. Cada fase deja el gate en verde y su commit.
