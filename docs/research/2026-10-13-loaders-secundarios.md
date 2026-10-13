# Loaders secundarios: mods de otro loader mediante capas de compatibilidad (observación 7)

**Pregunta** (i-7985ee-44060c, observación 7 del dueño, 2026-10-13): un modpack con un loader
principal y loaders secundarios, para usar mods de otro loader mediante mods de compatibilidad; las
sugerencias, del principal; el resto visible y permitido, marcado como no preferible frente a un mod
nativo. **Cómo se hizo:** un agente investigador (solo lectura), 2026-10-13. **Advertencia de
fuente:** todo se leyó a través de resúmenes de WebFetch/WebSearch, no de los archivos primarios
completos (las descargas directas fallaron). Lo que sostenga una decisión se vuelve a verificar
contra la fuente antes de construir. Lo marcado SIN VERIFICAR no tiene fuente primaria.

## Resumen

Hay dos puentes mantenidos: **Sinytra Connector** (mods de Fabric sobre NeoForge, y sobre Forge solo
en 1.20.1) y **Kilt** (mods de Forge sobre Fabric, experimental). Quilt corre mods de Fabric de forma
nativa, y eso el resolvedor ya lo modela (`LOADER_ACCEPTS = {"quilt": {"quilt", "fabric"}}` en
`src/library/resolution.py`). **Modrinth no tiene metadatos de «puente»:** una versión compatible con
Connector es simplemente una versión etiquetada `fabric`, así que «puenteado» tiene que salir de la
configuración del pack, no de Modrinth.

## 1. Capas de compatibilidad

**Sinytra Connector**
- Modrinth id `u58R1TMW`, slug `connector`; loaders `forge, neoforge`; versiones 1.20.1, 1.21,
  1.21.1 y 26.1.2; licencia MIT. https://api.modrinth.com/v2/project/connector
- En beta: muchos mods pueden no funcionar; Forge solo en 1.20.1. https://github.com/Sinytra/Connector,
  https://modrinth.com/mod/connector
- Compañero obligatorio: **Forgified Fabric API** (id `Aqlf1Shp`, slug `forgified-fabric-api`;
  loaders `forge, neoforge`; 1.20.1, 1.21, 1.21.1, 26.1–26.2; Apache-2.0). Es incompatible con el
  Fabric API normal. https://api.modrinth.com/v2/project/forgified-fabric-api
- Opcional: Connector Extras (integración con REI, EMI y TR Energy).
- `config/connector.json`: `hiddenMods`, `globalModAliases`, `enableMixinSafeguard` (activo por
  defecto: detiene la carga ante mixins que no pudo arreglar).
  https://moddedmc.wiki/en/project/connector/latest/docs/configuration
- La compatibilidad depende de cada mod; el consejo del proyecto es reproducir primero en Fabric.
  https://moddedmc.wiki/en/project/connector/latest
- Un mod etiquetado `fabric` y `forge` (sin `neoforge`) era rechazado por NeoForge; el arreglo estaba
  hecho y sin publicar al leerlo. https://github.com/Sinytra/Connector/issues/1934

**Kilt**
- Id `t87XZEqv`, slug `kilt`; loader `fabric`; 1.20.1 y 1.21.1; LGPL-2.1-only; actualizado el
  2026-10-06. Se describe a sí mismo como «muy inestable, puede romper mundos, puede causar crashes».
  https://api.modrinth.com/v2/project/kilt

**Quilt:** corre mods de Fabric directamente; Quilted Fabric API no cubre los detalles internos de
Fabric API, así que algunos mods fallan (fuente de terceros: SIN VERIFICAR). Su actividad actual,
SIN VERIFICAR.

**Otros:** no se encontraron. La lista de loaders de Modrinth (https://api.modrinth.com/v2/tag/loader)
no tiene ningún loader-puente. Forge 1.20.1 sobre NeoForge 1.20.1: no revisado.

## 2. Cómo lo representa Modrinth

- Etiquetas de loader: fabric, forge, neoforge, quilt (más las de shaders y datapacks); ninguna
  `connector`. El `loaders` de una versión lista solo loaders nativos.
- La app de Modrinth filtra estrictamente por el loader de la instancia (SIN VERIFICAR contra su
  documentación).
- Un `.mrpack` declara en `dependencies` `minecraft`, `forge`, `neoforge`, `fabric-loader` y
  `quilt-loader`; el ejemplo trae un solo loader, y si un launcher acepta dos está SIN VERIFICAR.
  https://support.modrinth.com/en/articles/8802351-modrinth-modpack-format-mrpack

## 3. Otras herramientas

Prism, packwiz, CurseForge y ATLauncher: SIN VERIFICAR desde fuentes primarias. Un resumen de
terceros dice que Prism usa un loader por instancia y Connector se instala como un mod más.

## 4. En el servidor

- Connector declara compatibilidad con cliente y servidor (página de Modrinth).
- Falla conocida en servidor dedicado: Connector quita los miembros `@Environment(CLIENT)` de las
  clases normales pero no de las de mixin, y un método de mixin solo de cliente tumba el servidor al
  cargar, sin crash report. El arreglo es el mod «Connector Server Mixin Fix», solo de servidor
  (https://www.curseforge.com/minecraft/mc-mods/connector-server-mixin-fix; su página en Modrinth,
  SIN VERIFICAR). Issues relacionados: https://github.com/Sinytra/Connector/issues/1428, #1501,
  #924, #1563, #1359 (solo los títulos).
- Un servidor necesitaría NeoForge, Connector, Forgified Fabric API, probablemente el arreglo de
  mixins y, si aplica, Connector Extras.

## 5. Recomendación (inferencia del investigador, no de las fuentes; para la revisión de decisiones)

- **Declarar:** `secondary_loaders` ordenado en el target, validado contra una tabla de puentes
  (`neoforge`←`fabric` vía Connector + Forgified Fabric API; `forge`←`fabric` solo 1.20.1;
  `fabric`←`forge` vía Kilt). Quilt←Fabric sigue en `LOADER_ACCEPTS`, como nativo.
- **Ordenar:** siempre lo nativo cuando hay una versión nativa en un canal permitido; lo puenteado
  solo si no hay nativa. Nunca cambiar sola una versión nativa instalada por una puenteada (encaja
  con d-7985ee-6dc34c: el piso). Lo puenteado solo para las versiones de Minecraft que el puente
  soporta. `Target.fits` necesita un nivel (nativo, puenteado), no un booleano.
- **Sugerencias:** solo nativas. Lo puenteado, visible y agregable, con una etiqueta «vía Connector
  (Fabric)» y la nota «no preferible: hay uno nativo» cuando lo hay.
- **Compañeros:** al agregar un mod puenteado, agregar el puente y su compañero, como
  `SHADER_LOADER_MODS` hace con Iris. Nunca Forgified Fabric API junto al Fabric API normal: una
  dependencia `fabric-api` de un mod de Fabric se resuelve a `forgified-fabric-api` sobre NeoForge.
  Es lo más probable que falle; va con tests.
- **Exportar:** el `.mrpack` nombra solo el loader principal; el puente va en metadatos propios y en
  una nota al usuario.
- **Servidor:** un mod puenteado es «servidor: riesgoso»; sugerir el arreglo de mixins si hay alguno.
- **Riesgos:** los dos puentes están en beta o son experimentales; los fallos aparecen al correr, no
  al resolver; Modrinth no muestra las incompatibilidades declaradas con el puente; la cobertura de
  versiones varía por mod; el esquema 26.x de versiones de Minecraft exige cuidado al comparar rangos.

## Verificación (2026-10-13, segunda pasada)

Un investigador releyó las afirmaciones contra la API de Modrinth (`/v2/project`, `/version`, `/tag/loader`)
y los repositorios. Los valores de la API llegaron como campos JSON y son fiables; las listas largas de
versiones llegaron resumidas. Lo que **cambia o se agrega**:

- **Connector por versión, no por proyecto:** la lista `loaders` del proyecto es la unión. Por versión:
  Forge solo en 1.20.1; NeoForge en 1.21 (una sola beta), 1.21.1 y 26.1.2. Todas sus versiones son
  beta. https://api.modrinth.com/v2/project/connector/version
- **Dependencias de Connector:** Forgified Fabric API (`Aqlf1Shp`) **requerida** en todas; **Launchpad**
  (`voWgQoWV`, solo NeoForge) **requerida** en las de 26.1.2; Connector Extras (`FYpiwiBR`) opcional.
- **El choque con el Fabric API normal** no está en Modrinth como dependencia `incompatible` (las 10
  versiones más nuevas de Forgified Fabric API no tienen dependencias); lo dice el README de Connector
  («not compatible with it»). El diseño no lo puede leer de Modrinth: lo tiene que saber.
  https://raw.githubusercontent.com/Sinytra/Connector/refs/heads/master/README.md
- **Kilt** (`t87XZEqv`, Fabric, 1.20.1 y 1.21.1, LGPL-2.1-only): sus versiones nuevas son `release` (las
  1.20.1 viejas, alpha), aunque su texto sigue diciendo «muy inestable». Requiere Fabric API
  (`P7dR8mSH`) y Fabric Language Kotlin (`Ha28R6CL`); se declara incompatible con Embeddium y Async.
- **Etiquetas de loader de Modrinth:** ninguna nombra un puente; un mod de Fabric que corre por
  Connector está etiquetado `fabric` (a veces el autor suma `forge`/`neoforge`).
- **`.mrpack`:** claves `minecraft`, `forge`, `neoforge`, `fabric-loader`, `quilt-loader`; la
  especificación no prohíbe dos loaders, el ejemplo trae uno y avisa que puede sumar claves. Si un
  launcher acepta dos: SIN VERIFICAR.
- **«Connector Server Mixin Fix»:** no aparece en Modrinth (sí otros parches de Connector:
  `sinytra_crash_fix`, `connector-recipe-bridge`). Sigue SIN VERIFICAR.
- **Quilt** carga mods de Fabric en la mayoría de los casos (una sola dirección); QFAPI no cubre los
  detalles internos de Fabric API (página de Modrinth de `qsl`). Que QSL esté discontinuado: SIN VERIFICAR.
- **Otros puentes modernos:** ninguno nuevo; Fabricated Legacy Forge es para versiones viejas.
