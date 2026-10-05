# Caché del catálogo de Modrinth: reglas de la API y diseño propuesto

Fecha: 2026-10-05. Solo lectura; no se tocó el repositorio. Fuentes primarias: documentación de Modrinth y sondas reales contra `api.modrinth.com` hechas hoy (marcadas SONDA).

## 1. Reglas de la API de Modrinth

- **Límite de tasa:** 300 peticiones por minuto por IP, con o sin token; más requiere contactar a soporte. Cabeceras `X-Ratelimit-Limit`, `X-Ratelimit-Remaining`, `X-Ratelimit-Reset` (segundos hasta el reinicio). Fuente: https://docs.modrinth.com/api/ (acceso 2026-10-05). SONDA: `x-ratelimit-limit: 300`, `remaining: 299`, `reset: 0` en una petición suelta.
- **User-Agent:** obligatorio y único; los genéricos de librería se arriesgan a bloqueo. Formatos recomendados: `project_name`, `github_username/project_name/1.56.0`, `github_username/project_name/1.56.0 (contacto@dominio)`. Misma fuente. El cliente del repositorio ya manda `MODRINTH_AGENT` (src/library/api/client/modrinth.py); SIN VERIFICAR su valor concreto (no se leyó la configuración).
- **Guía de caché/sondeo:** la documentación no da ninguna ("No specific caching guidance is documented" en lo leído). SIN VERIFICAR en otra página de la documentación.
- **Largo de `ids`:** `GET /projects?ids=[...]` documenta `ids` como cadena JSON de ids y/o slugs, **sin límite explícito** (https://docs.modrinth.com/api/operations/getprojects/, 2026-10-05). SONDA: 1000 ids (URL de ~20 KB) respondió 200 (con ids repetidos, así que el resultado se deduplica; no prueba 1000 proyectos distintos). SIN VERIFICAR el límite real de URL del servidor/Cloudflare; el estándar práctico de Cloudflare ronda 16 KB de línea de petición en algunos planes: SIN VERIFICAR. Recomendación prudente: trozos de 100-200 ids (~2-4 KB). `/versions?ids=` tiene la misma forma; no se sondeó: SIN VERIFICAR.
- **Ids desconocidos:** SONDA: `projects?ids=["sodium","nonexistentzzz","AANobbMI"]` devuelve 200 y **omite** los desconocidos (sodium por slug y por id colapsan en un solo resultado). No hay error ni marcador: "no lo conoce Modrinth" solo se deduce por ausencia.
- Las ids son base62 de 8 caracteres y los slugs pueden cambiar: guardar por id (misma fuente de identificadores).

## 2. Peticiones condicionales

- **ETag / If-None-Match:** SONDA: las respuestas **no traen `ETag`**. No hay revalidación por ETag.
- **Last-Modified / If-Modified-Since:** SONDA: hay `last-modified`, pero vale la hora de la propia respuesta (13:36:13 para una petición de 13:36:13), así que no refleja la última modificación del proyecto; enviar `If-Modified-Since` con esa fecha dio 304. Es un artefacto del borde, no una señal de cambio: no confiar. Tampoco aparece en la documentación: SIN VERIFICAR su contrato.
- **Cache-Control:** SONDA: `cache-control: public, max-age=2678400` (31 días), `expires` a +31 días, `cf-cache-status: HIT/MISS` (Cloudflare). Es un valor llamativamente largo para datos que cambian; no está documentado, así que se trata como no fiable. Modrinth puede purgar el borde al cambiar un proyecto: SIN VERIFICAR. Consecuencia: usar ese `max-age` como TTL propio sería demasiado largo para título/versiones; y una petición repetida puede servirse desde el borde sin costar contra el origen (no se verificó si cuenta para el límite; en la sonda `remaining` no bajó de 299 en HIT ni en MISS, pero fue una muestra mínima).

## 3. Qué campos cambian y con qué frecuencia

Base documentada (https://docs.modrinth.com/api/operations/getprojects/): `updated` = fecha de creación de la última versión; `published` = creación; `icon_url` opcional y nulo posible; `downloads` entero acumulado; `status` obligatorio. La frecuencia de cambio es mi estimación (SIN VERIFICAR con datos históricos), salvo `updated` y `downloads`, que por definición cambian con cada versión / continuamente.

| Campo | Cambia | TTL sensato para este panel |
|---|---|---|
| `id`, `slug`, `project_type`, `published` | casi nunca (el slug puede cambiar por decisión del autor) | sin TTL; se rehidrata al refrescar lo demás |
| `icon_url` | raro (el autor sube un icono; el hash de la URL cambia) | 7 días; si no tiene icono, 24 h (marcador negativo) |
| `title`, `description`, `categories`, `loaders`, `game_versions` | de vez en cuando, sobre todo con cada versión nueva | 1-3 días |
| `versions` (lista de ids), `updated` | con cada lanzamiento (activos: semanas; en algunos, días) | 1 día; el panel que importa versiones ya tiene su propia vía de versiones |
| `downloads`, `followers` | continuamente | 1-7 días; es solo informativo, no justifica peticiones |
| `status` (retirado, archivado, privado) | raro pero relevante (un proyecto retirado desaparece o cambia) | junto al grupo de título |

Conclusión: para un panel de un solo operador, un TTL único de 24 h para todo y 7 d para icono presente cubre bien; no hace falta TTL por campo con granularidad.

## 4. Recomendación: diseño de caché del catálogo

### Hechos del código actual (leído)
`fill_catalog` (src/services/modrinth.py) pide los ids que no están guardados **o** que no tienen `icon_url`. Dos efectos: (a) un proyecto sin icono real en Modrinth se vuelve a pedir en cada render; (b) un id desconocido (omitido por Modrinth, ver sonda) nunca se guarda, así que también se vuelve a pedir siempre. Además nada refresca los proyectos con icono: sus datos envejecen sin límite (salvo `project_update*`). Sin comprobar la parte de `src/services/modpack.py` salvo que llama a `fill_catalog` en 3 sitios.

### Diseño propuesto
1. **Marcador `fetched_at` por proyecto** (datetime UTC en `ModrinthProject`; actualizado en el upsert). Necesita registrar el modelo/migración trivial: documentos viejos sin el campo se tratan como "viejos" (se refrescan una vez). Es cambio de modelo, afecta `src/model/` (d-7985ee: todo `Document` registrado ya lo está).
2. **Regla de pendiente (`needs_fetch`)**: falta, o `fetched_at` más viejo que su TTL. TTL: 7 d si tiene icono, 24 h si no. Se elimina la condición "sin icono => pedir siempre".
3. **Caché negativa para ids desconocidos:** como Modrinth omite los ids que no conoce, tras una respuesta *exitosa* que no los incluye, guardar un registro mínimo (colección propia `modrinth_unknown`, o un documento con `missing=True`) con `checked_at`, y no volver a pedirlos 24 h. Cuidado clave: **solo marcar como desconocido tras una respuesta 200 completa**; un fallo, timeout o el `wait_for` de 5 s **no** debe producir marca negativa (de lo contrario un hipo de red oculta proyectos reales). Con trozos, solo los ids del trozo respondido con éxito.
4. **Servir viejo mientras se refresca (stale-while-revalidate):** un proyecto presente con TTL vencido se muestra tal cual y se refresca con el mismo lote; el plazo de 5 s solo protege la página, y lo viejo ya está en base. Solo lo *ausente* es lo que realmente espera.
5. **Trozos y deduplicado:** `ids` en trozos de ~100 (por URL y por prudencia), deduplicados y ordenados; concurrencia baja (2). Con 300/min de límite, un render no se acerca; el riesgo real es el bucle de tecleo.
6. **Relleno desde la búsqueda:** la búsqueda ya devuelve por hit `project_id`, `slug`, `title`, `description`, `icon_url`, `downloads`, `categories`, `project_type`, etc. (SIN VERIFICAR campo a campo contra la documentación de `/search`; verificar antes de usar). Cambios sugeridos:
   - **No llamar a `projects?ids` por cada pulsación.** Pintar los hits con lo que trae la propia búsqueda (el icono viene en el hit) y rellenar el catálogo con **un** lote al abandonar/seleccionar (o al hacer clic en un hit), o solo cuando ya tenga que guardarse.
   - Si se quiere llenar al buscar: aplicar el mismo `needs_fetch` (los que ya están frescos no se piden), cancelar la petición anterior (AbortController en el cliente) y llamar al relleno solo si la consulta es estable ≥ 300 ms *y* quedan ids pendientes. Con ~10 hits y caché, la mayoría de pulsaciones no tocarán Modrinth.
   - Limitar con un tope por ventana (p. ej. máx. 1 relleno/s por sesión) en el servicio, y no dejar que esas peticiones compitan con el render (plazo propio más corto, p. ej. 2 s).
7. **Refresco en segundo plano opcional:** una tarea periódica que renueva por `fetched_at` más viejo en lotes de 100, lejos del camino de la página; evita refrescar en render. Mayor complejidad (un proceso más que parar, ver invariantes del repositorio); posponer salvo que el stale-while-revalidate no baste.
8. **Cabecera de tasa:** leer `X-Ratelimit-Remaining` y, si es bajo (<20), saltar los rellenos oportunistas (búsqueda) y dejar solo los necesarios; ante 429, no reintentar en el render (coherente con "no reintentar" del repositorio, que aplica a escrituras; las lecturas sí pueden, pero no dentro del plazo de 5 s).

### Compromisos
- `fetched_at` y TTL: más escrituras a Mongo en los refrescos, y un cambio de modelo; a cambio, el bucle de peticiones desaparece. Alternativa más barata: solo un booleano `no_icon_checked` (sin TTL): arregla el bucle sin refrescar nada, pero deja datos viejos para siempre.
- Caché negativa: ahorra peticiones, pero un proyecto recién aprobado en Modrinth puede tardar hasta 24 h en aparecer; mitigable con un botón/acción de operador "refrescar" que ignore la marca.
- TTL largo de icono vs. cambios del autor: un icono nuevo tarda hasta 7 d; aceptable para un panel privado.
- Stale-while-revalidate: nunca bloquea, pero la primera visita tras vencer muestra datos viejos.
- No usar `If-Modified-Since`/ETag: no hay ETag y `last-modified` es la hora de la respuesta; no aporta.

### Orden sugerido de cambios (tests antes del código, según el repositorio)
1. Test: un proyecto guardado sin icono y con `fetched_at` reciente no se vuelve a pedir; uno con TTL vencido sí; un id omitido por Modrinth con respuesta 200 se marca; con fallo/timeout no.
2. Modelo + `needs_fetch` en `fill_catalog`; trozos de 100.
3. Búsqueda: pintar con los datos del hit, relleno en la selección; revisar `tests/test_api_contracts.py` y `docs/references.md` con la forma real del hit.
4. Lo que el gate no cubre y habrá que decir: respuesta real de Modrinth, límites reales de URL, comportamiento en el servidor del dueño.

### Pendiente de verificar (SIN VERIFICAR)
Límite máximo real de `ids` y de largo de URL; si las respuestas del borde (HIT) cuentan contra los 300/min; contrato de `Last-Modified`/`Cache-Control`; semántica de purga del borde; campos del hit de `/search` y `/versions?ids`; frecuencias reales de cambio de cada campo.
