# Panel, ronda 2, tanda 1: plan

**Origen:** revisión de decisiones del 2026-10-12 (i-7985ee-1bb2ea), tanda 1: F27, F28, F29 y las
cuatro preguntas que dejó la tanda A. Las decisiones están en `docs/decisions.md`: d-7985ee-c62c61,
f43772, 83e764, 7bccf8, 881689, b8e927, d4bc8e, 0b9ebb, 322302, 58228f. d-7985ee-7bccf8 y
d-7985ee-d4bc8e no piden código (ya es así). **Método:** cada tarea, test primero y visto fallar;
el gate con MongoDB (`scripts/test-mongo.sh`) al cerrar cada una.

## Hallazgo al leer el código (cambia cómo se cumple d-7985ee-b8e927)

`PterodactylDatabase.server_set_bundle` no es condicional: en la carrera la segunda vinculación no
falla, pisa la primera. Y la app no tiene cómo borrar un pack. "Deshacer" necesita: vincular solo si
el servidor sigue sin pack (`{"_id": oid, "bundle_id": None}`) y borrar un pack recién creado solo si
sigue vacío (sin builds, sin proyectos).

## Tareas, en este orden

| # | Qué | Decisión | Dónde |
|---|---|---|---|
| 1 | Crear el pack desde un servidor vincula de forma condicional; si pierde la carrera, borra el pack vacío (`bundle.delete`) y responde 409 con el texto decidido | b8e927 | `src/database/{pterodactyl,modpack}.py`, `PterodactylService.bundle_for_server`, `tests/test_bundle_for_server*` |
| 2 | Quitar un mod borra su pin, su decisión y su excepción en el mismo `update_one`, salvo que la última resolución lo traiga como dependencia; sin resolución, los conserva; el evento lo registra y el aviso lo dice | 881689 | `ModpackDatabase.bundle_project_remove`, `ModpackService.project_remove`, ruta web del borrado |
| 3 | Un filtro de Jinja enlaza las URL `http(s)` de un changelog ya escapado | 58228f | `src/web/__init__.py`, `modpacks/_changes.html` |
| 4 | Función pura `direction` (sube, baja, cambio de rama, sin datos) por fecha de publicación; `ChangePreview.downgrades`; la vista previa marca cada baja y su recuento | 322302, f43772 | `src/library/changes.py`, `ModpackService._preview`, `_changes.html` |
| 5 | `pin_set` compara con el piso: sin `confirm_downgrade`, una baja es un 409 que la describe; el panel muestra la confirmación "Fijar igual"; `/v1` lo pide en el PATCH (`?confirm_downgrade=true`) | c62c61, 0b9ebb | `ModpackService.pin_set`/`bundle_update`, `routers/web/modpacks.py`, `routers/v1/modpack.py`, `_versions.html` |
| 6 | La lista de mods marca un pin bajo el piso: "baja (piso X)" | f43772 | `ModpackService.mod_list`, `modpacks/_mods.html` |
| 7 | La vista previa del diálogo de actualización avisa los jars sin explicar del último escaneo para el build elegido, con su antigüedad y el arreglo | 83e764 | `ModpackService.server_change_preview`, `servers/_preview_body.html` |
| 8 | Barrido visual (360/768/1280, claro y oscuro), filas de `docs/decisions.md` con lo que las hace cumplir, roadmap y registro | — | — |

## Lo que no cubre

El gate no ve Modrinth real (las fechas de publicación vienen del catálogo; una versión que el
catálogo no tiene queda "sin datos" en la vista previa y no se marca), ni Pterodactyl real.
