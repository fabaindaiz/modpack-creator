# Importar un modpack: `.mrpack` y URL/ID de Modrinth

**Fecha:** 2026-10-09 · **Origen:** observaciones de un tester del panel (2026-10-08) ·
**Investigación:** `docs/research/2026-10-09-importar-modpacks.md` · **Decisiones:** del dueño, el
2026-10-09 (D0–D6 abajo).

## Qué hace

Se sube un `.mrpack` o se da la URL, el slug o el id de versión de un modpack publicado en Modrinth,
y sus mods entran a un modpack de la app:

- **Nuevo modpack** (lista de modpacks, "Importar modpack…"): se crea con el nombre, la versión de
  Minecraft, el loader y la versión del loader que dice el `.mrpack`.
- **Reemplazar el contenido** (detalle de un pack): los mods del pack (origen `pack`) se reemplazan
  con los del archivo; lo que agregó el admin se queda (`pack_replace`, d-7985ee-b15dd4). Si el
  archivo es para otra versión de Minecraft o de loader, se rechaza sin tocar nada.

## Decisiones (dueño, 2026-10-09)

| | Decisión |
|---|---|
| D0 | Las dos entradas: nuevo modpack y reemplazar contenido. |
| D1 | Primera versión: `.mrpack` y URL/slug/id de un modpack publicado en Modrinth. El zip de Prism, después. |
| D2 | CurseForge, al roadmap: necesita una clave por instalación y sus términos prohíben guardar sus datos. |
| D3 | Un archivo que no está en Modrinth se informa ("súbelo al servidor a mano") y no se guarda. |
| D4 | Los overrides se listan y no se guardan; guardarlos para crear y exportar se investiga aparte. |
| D5 | `env.server: unsupported` deja el mod "Desactivado", y `required` "En el servidor", solo donde nadie decidió. |
| D6 | Hasta 50 MiB, leído en memoria sin extraer: topes de entradas y de tamaño descomprimido, rutas seguras. |

## Cómo

- **Leer** (`src/library/mrpack_import.py`, puro): el zip se abre en memoria; se rechaza si pesa más
  de 50 MiB, tiene más de 5.000 entradas, un miembro descomprime más de lo que dice o el total pasa
  de 200 MiB (se cuenta leyendo, no se cree a la cabecera del zip). `modrinth.index.json` se valida
  como al exportar (formato, juego, rutas dentro de la instancia, sha1 y sha512). El loader sale de
  `dependencies` (`fabric-loader`, `quilt-loader`, `forge`, `neoforge`). Los jars de
  `overrides*/mods/` se hashean; el resto de los overrides se cuenta por carpeta.
- **Identificar**: una llamada `POST /version_files` por sha512 (índice) y otra por sha1 (jars de
  overrides). Lo identificado entra por proyecto; lo que no, al informe.
- **Lo que corre**, igual que al importar un servidor (d-7985ee-58801d): sin pins; una versión beta o
  alpha fuera de los canales del pack deja la excepción de canal de ese mod (d-7985ee-b1ce87), y
  `env.server` siembra la decisión de servidor; las dos, solo donde no hay nada.
- **Desde Modrinth**: el proyecto debe ser un modpack; sin versión, la más nueva listada. Su archivo
  primario se baja del CDN de Modrinth (solo `cdn.modrinth.com`), con tope de 50 MiB, y debe
  coincidir con su sha512.
- **Subida**: se rechaza sin `Content-Length` o con uno mayor que el tope antes de leer el formulario
  (Starlette 1.7 no limita el tamaño de un archivo en un formulario).
- **Permisos**: editor, como crear un bundle y reemplazar su contenido. Audita `bundle.import`.

## Informe

Mods importados, archivos que no están en Modrinth (por ruta), overrides sin importar (cuántos y en
qué carpetas), y las excepciones y decisiones que dejó.

## Fuera

CurseForge (D2), el zip de Prism (D1), guardar overrides (D4), una ruta `/v1` (el panel primero).
