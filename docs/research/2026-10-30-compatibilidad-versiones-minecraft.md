# Compatibilidad de mods entre versiones de Minecraft (2026-10-30)

**Pregunta.** Cuando alguien se suma a una entrada de la base de conocimiento desde otra versión de Minecraft
(K10, dueño 2026-10-30), el panel ofrece ampliar la entrada y dice si las dos versiones son «probablemente
compatibles» o «muy probablemente no». ¿Entre qué versiones siguen funcionando los mods sin recompilar?

**Método.** Un subagente investigador leyó los anuncios de Fabric (fabricmc.net), NeoForged (neoforged.net), la
especificación de `fabric.mod.json`, la de `neoforge.mods.toml` y la API de Modrinth. Lo leyó a través de un
resumidor (WebFetch), no el texto completo: cada dato es «leído por resumen». Lo marcado SUPUESTO no viene de una
fuente primaria. Nada se copió al repositorio salvo frases cortas.

## Lo que dicen las fuentes

- **Las roturas caen en los «drops»** (1.X → 1.X+1 y las actualizaciones grandes dentro de 1.X, y desde 2026
  26.1 → 26.2 → 26.3). Casi todos los anuncios de Fabric de un drop listan cambios que rompen mods: 1.16.2
  («we expect most mods will require an update»), 1.17, 1.18, 1.19, 1.19.3 («affect almost all mods»), 1.19.4
  («neither mod authors nor players should assume compatibility»), 1.20, 1.20.2, 1.20.3, 1.20.5, 1.21, 1.21.2,
  1.21.4, 1.21.5, 1.21.9, 1.21.11, 26.1 («no mods from 1.21.11 or before will work without, at a minimum,
  recompilation»), 26.2 y 26.3. Fuentes: `https://fabricmc.net/<año>/<mes>/<día>/<versión>.html`, por ejemplo
  https://fabricmc.net/2023/03/01/1194.html y https://fabricmc.net/2026/03/14/261.html.
- **Dentro de un drop (hotfix) los mods suelen funcionar, pero ningún loader lo promete.** Mojang llamó a 1.21.1
  un hotfix (https://www.minecraft.net/en-us/article/minecraft-java-edition-1-21-1, leído solo como fragmento de
  búsqueda). Fabric publica un solo anuncio para cada grupo: {1.19, 1.19.1, 1.19.2}, {1.20, 1.20.1},
  {1.20.5, 1.20.6}, {1.21, 1.21.1}, {1.21.2, 1.21.3}, {1.21.6–1.21.8}, {1.21.9, 1.21.10}. Que compartan anuncio
  es la única evidencia para la mayoría (SUPUESTO: compatibles).
- **Excepción:** 1.20.2 → 1.20.3 rompe (el anuncio de Fabric de 1.20.3 lista roturas), aunque comparten «1.20».
- **Desde 26.1 la versión es año.drop.hotfix** (https://www.minecraft.net/en-us/article/minecraft-new-version-numbering-system,
  fragmento de búsqueda). NeoForge: «The first three components identify the Minecraft version»
  (https://neoforged.net/news/26.1release/). Lithium en Modrinth publica un solo archivo para
  ["26.1", "26.1.1", "26.1.2"].
- **Rangos legibles por máquina:** `fabric.mod.json` acepta rangos (`~1.21`, `1.20.x`, `>=1.21 <1.22`;
  https://wiki.fabricmc.net/documentation:fabric_mod_json_spec) y `neoforge.mods.toml` rangos de Maven
  (`[1.20.6,1.21)`; https://docs.neoforged.net/docs/gettingstarted/modfiles). Son evidencia del autor, débil
  (SUPUESTO: muchos los escriben amplios sin probar). Modrinth solo dice que `game_versions` es «A list of versions
  of Minecraft that this version supports» (https://docs.modrinth.com/api/operations/getversion/); su
  `/tag/game_version` tiene un campo `major` que no distingue los drops de 26.x.

## La regla que aplica el panel

1. Las dos versiones en el mismo grupo de hotfix (tabla abajo): **«probablemente compatible»**.
2. Si no: **«es muy probable que no sea compatible»**.
3. Una sola mejora por datos propios, sin pedir nada a Modrinth: si en el catálogo hay archivos de alguno de los dos
   mods que listan las dos versiones a la vez, es evidencia del autor: «probablemente compatible». No encontrar
   ninguno no es evidencia de lo contrario.

Grupos (de los anuncios de Fabric; cada versión no listada es su propio grupo): {1.16.2–1.16.5}, {1.18–1.18.2}
(SUPUESTO), {1.19, 1.19.1, 1.19.2}, {1.20, 1.20.1}, {1.20.3, 1.20.4} (SUPUESTO), {1.20.5, 1.20.6}, {1.21, 1.21.1},
{1.21.2, 1.21.3}, {1.21.6, 1.21.7, 1.21.8}, {1.21.9, 1.21.10}, y en 26.x, año.drop igual (26.1, 26.1.1, 26.1.2).

**Lo incierto.** Ningún loader garantiza la compatibilidad de un hotfix; los grupos de un solo anuncio son una
inferencia; las páginas de Mojang se leyeron como fragmentos. Por eso el texto dice «probablemente» y nunca
cambia nada solo: es una sugerencia (D8, d-7985ee-56a529).
