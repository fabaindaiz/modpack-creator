# Bajas de versión (F27): cómo decidir "esto es una baja"

Fecha: 2026-10-05. Investigación de solo lectura. Fuentes accedidas el 2026-10-05.
Marca SIN VERIFICAR = no confirmado en fuente primaria (viene de memoria o resumen).
Nota de método: las páginas se leyeron con WebFetch, que resume con un modelo; lo citado entre comillas es lo que devolvió, no una copia literal verificada del archivo.

## 1. Datos de versiones en Modrinth

- `version_number`: la documentación dice "The version number. Ideally will follow semantic versioning" (https://docs.modrinth.com/api/operations/getprojectversions/). "Ideally" = texto libre sin garantía. No hay orientación oficial de cómo comparar.
- `date_published`: obligatorio, ISO-8601. Sin más garantías documentadas.
- `version_type`: `release`, `beta`, `alpha` ("release channel"). Sin orden declarado.
- Orden del listado `GET /project/{id}/version`: la documentación NO especifica el orden. No depender de él (SIN VERIFICAR empíricamente que venga por fecha descendente; en la práctica suele venir así, pero no está prometido).
- `status` (listed, archived, draft, unlisted, scheduled, unknown) y `featured` existen en el objeto Version (https://docs.modrinth.com/api/operations/getversion/). No se documenta un campo de "eliminada".
- Otro campo: el código de Labrinth tiene `ordering: Option<i32>` en el modelo Version (apps/labrinth/src/models/v3/projects.rs). No aparece en la documentación pública de la API; SIN VERIFICAR que se exponga ni que sirva.
- No hay orden por id: los ids son cadenas base62 opacas (SIN VERIFICAR en fuente primaria, pero la documentación no promete orden).
- Orden fiable disponible: solo `date_published` (es lo que usa Modrinth internamente, ver 2). Es un orden de subida, no de "versión de software".

## 2. Cómo deciden "más nuevo" otras herramientas (solo ideas; no copiar código)

- Labrinth (servidor de Modrinth), https://github.com/modrinth/code, `apps/labrinth/src/routes/v3/version_file.rs`:
  - `update_files_internal` (`POST /version_files/update`): SQL `ORDER BY v.date_published ASC`, se toma la última visible. Es decir, "más nuevo" = mayor `date_published`, filtrando por loaders, game_versions y version_types.
  - `get_update_from_hash` (`POST /version_file/{hash}/update`): filtra y usa `.sorted()` + `next_back()`; la clave de orden del tipo Version no se vio en el fragmento (SIN VERIFICAR; probablemente fecha).
  - Conclusión: Modrinth no compara números de versión; responde por fecha. Licencia del repositorio: SIN VERIFICAR (no la leí; tratar como solo ideas).
- Prism Launcher, `launcher/modplatform/modrinth/ModrinthCheckUpdate.cpp`: pide a Modrinth la última versión por hash y declara actualización si el hash difiere (y la versión es "preferred"). No compara números ni fechas localmente; el fragmento no mostraba prevención de bajas. Licencia: GPL-3.0 (SIN VERIFICAR; no leí el archivo LICENSE).
- packwiz, `modrinth/modrinth.go` (licencia MIT, verificada en LICENSE): `findLatestVersion` compara con FlexVer primero; si empata, versión de juego y loader preferidos; como último desempate, fecha. Emite el aviso "Modrinth versions for %s inconsistent between latest version number and newest release date" cuando número y fecha discrepan. Es el único que admite explícitamente que ambas señales pueden contradecirse.
- ferium / libium: `select_latest` toma el primero de una lista "sorted in the order of preference (e.g. chronological)", o sea orden de la API/cronológico, sin comparar números. Licencia: SIN VERIFICAR.
- mrpack-install: no pude leer su fuente (URL 404). SIN VERIFICAR.

Resumen: quien pregunta a Modrinth usa fecha; solo packwiz compara números (FlexVer) y deja la fecha de desempate. Ninguno detecta explícitamente bajas, salvo el aviso de inconsistencia de packwiz.

## 3. Casos límite

(Razonamiento a partir de los datos; los ejemplos concretos son SIN VERIFICAR contra casos reales.)
- Re-subida de una versión vieja: fecha nueva, número viejo. Fecha dice "subida", número dice "baja". Se contradicen.
- Canales: beta más nueva que la release (el caso JEI): número y fecha dicen que la beta es posterior; la baja ocurre porque la política del pack solo acepta release. Aquí el número SÍ baja (19.57 → 19.51) con fecha anterior.
- Ramas por versión de MC (2.x para 1.21, 1.x para 1.20): al cambiar de MC, el número baja legítimamente y es lo correcto. No es una baja de verdad; hay que comparar solo entre versiones del mismo MC/loader, o rebajar la confianza si el MC del pack cambió.
- Versiones eliminadas o archivadas/no listadas: el id viejo puede dejar de resolverse en el catálogo; entonces no hay datos para comparar y no se debe afirmar nada ("no se puede comparar").
- Números no semánticos (`1.20.1-forge-47.2`, `build.123`, `v2`): un parser semver falla o ordena mal. Hay que tolerarlo (FlexVer, o declarar "desconocido").

## 4. Recomendación para el panel

Datos disponibles: de la versión anterior (`from`) y la nueva (`to`) en el catálogo: `date_published`, `version_type`, `version_number`.

Regla propuesta, por señales (cada una independiente; la baja se marca según cuántas coinciden):
1. Señal fecha: `to.date_published < from.date_published`. Es la señal más cercana a lo que hace Modrinth. Fiable cuando ambos datos están en el catálogo.
2. Señal número: comparación tolerante (FlexVer o tuplas numéricas) `to.version_number < from.version_number`, solo si ambos se parsean con el mismo esquema; si no, "indeterminado".
3. Señal canal: orden `release > beta > alpha`; `to.version_type` de menor rango que `from`. Un cambio de beta a release no es baja aunque el número baje a veces (SIN VERIFICAR la frecuencia).

Veredicto:
- Baja segura: fecha y número bajan (las dos). Es el caso JEI. Confianza alta.
- Posible baja: solo una de las dos señales baja (p. ej. re-subida: fecha sube y número baja; o fecha baja y número igual/indeterminado). Confianza media; texto con "posible".
- No marcar: ambas suben o iguales, o no hay datos de uno de los lados (decir "sin datos para comparar" solo si el dueño lo quiere; si no, omitir).
- Cambio de versión de MC o de loader entre `from` y `to`: no afirmar baja por número; marcar como "cambio de rama" o rebajar la confianza. Hace falta que `ModrinthVersion` traiga `game_versions` y `loaders` (probablemente ya los tiene; no lo verifiqué).
- Los dos sin fecha verificable por la versión eliminada: no se puede comparar.

Confianza: no existe una prueba definitiva de baja, porque Modrinth no la ofrece. Con las dos señales coincidiendo el riesgo de falso positivo es bajo; con una sola, moderado. Por eso el aviso debe informar y no decidir.

Texto en español (propuesta):
- Baja segura: "Baja de versión: JEI 19.57.0.450 → 19.51.0.418 (la nueva es anterior y de un canal más estable: el servidor tenía una beta)". Variante sin canal: "Baja de versión: la versión nueva es anterior a la que tiene el servidor."
- Posible: "Posible baja de versión: el número baja pero la fecha de publicación es posterior (puede ser una re-subida). Revísalo."
- Cambio de rama: "Cambia de rama de Minecraft (1.20 → 1.21): el número puede bajar sin ser una baja."
- Resumen en el encabezado de la vista previa: "N bajas de versión" con enlace/ancla a esas filas.

Comportamiento del diálogo: marcar y advertir, no bloquear.
- Marcar la fila (icono/etiqueta) en la vista previa y en la comparación (cumple F27: "en vista previa, comparación y diálogo").
- En el diálogo de actualización directa, mostrar un aviso con el recuento y exigir, como mucho, una confirmación explícita ("Entiendo que baja N mods") solo para "baja segura"; las "posibles" solo se muestran.
- No bloquear: una baja puede ser intencionada (el pack solo acepta release, es la política del dueño) y la regla tiene falsos positivos. Un bloqueo sin salida contradice esa política.
- Implementación sugerida (idea, no ejecutada): ampliar `Change` con un campo calculado (`direction`: up/down/unknown) en una función pura nueva en `src/library/` junto a `diff_entries`, alimentada por `ChangePreview` en `_preview`, para mantener la capa pura sin E/S. Si la baja entra en tests, probar: JEI beta→release, re-subida, cambio de MC, número no semántico, fecha ausente.

## Fuentes
- https://docs.modrinth.com/api/operations/getprojectversions/ (2026-10-05)
- https://docs.modrinth.com/api/operations/getversion/ (2026-10-05)
- https://docs.modrinth.com/api/operations/getlatestversionfromhash/ (2026-10-05)
- https://github.com/modrinth/code, `apps/labrinth/src/routes/v2|v3/version_file.rs`, `models/v3/projects.rs` (2026-10-05)
- https://github.com/packwiz/packwiz, `modrinth/modrinth.go`, LICENSE (MIT) (2026-10-05)
- https://github.com/PrismLauncher/PrismLauncher, `launcher/modplatform/modrinth/ModrinthCheckUpdate.cpp` (2026-10-05)
- https://github.com/gorilla-devs/libium, `src/upgrade/check.rs` (2026-10-05)
- Repo local: src/library/changes.py (`diff_entries` solo compara ids de versión por igualdad), docs/research/2026-10-04-usabilidad-panel.md (F27, ítem 8).
