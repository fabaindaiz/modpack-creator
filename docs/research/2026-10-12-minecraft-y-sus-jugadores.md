# Minecraft y sus jugadores: el caso de uso de un modpack para jugar con amigos

**Fecha:** 2026-10-12.
**Para quién:** el equipo que construye modpack-creator (arma packs desde Modrinth, congela builds
`.mrpack` y los despliega a servidores Pterodactyl de un admin que juega con amigos).
**Método:** fuentes primarias donde existen (Minecraft Wiki, minecraft.net, NeoForged, documentación
de Modrinth y la **API pública de Modrinth consultada hoy**), guías de hosting y foros como
complemento. Lo que no se verificó en una fuente primaria está marcado **SUPUESTO** y se repite al
final. Las fuentes sin licencia solo aportaron ideas; no se copió texto.

> Nota de lectura: los números de Modrinth salen de `GET /v2/search` y `GET /v2/projects`
> consultados el 2026-10-12. Cambian todos los días; sirven como orden de magnitud y ranking, no
> como cifra exacta.

---

## 1. El juego

### 1.1 Modos de juego

Según la [Minecraft Wiki, *Game mode*](https://minecraft.wiki/w/Game_mode):

| Modo | Qué es | Relevancia para un pack |
|---|---|---|
| **Supervivencia** | recolectar, fabricar, vida y hambre; al morir se pierden ítems y experiencia | el modo de casi todos los modpacks y SMP |
| **Creativo** | bloques infinitos, vuelo, invulnerable | construcción; mods de decoración y construcción |
| **Hardcore** | supervivencia con dificultad Difícil fija; al morir se pasa a espectador para siempre | packs de desafío (RLCraft y similares se juegan "casi hardcore") |
| **Aventura** | solo se rompen o colocan bloques con herramientas o etiquetas permitidas | mapas de aventura; poco usado en packs |
| **Espectador** | vuela a través de bloques sin interactuar | el admin mirando el mundo; el muerto en hardcore |

En servidor, el modo por defecto, la dificultad y `hardcore` se fijan en `server.properties`
([wiki](https://minecraft.wiki/w/Server.properties)).

### 1.2 Progresión típica (vanilla)

- **Early game:** madera → piedra → hierro; refugio, comida, cama; minería en cuevas.
- **Mid game:** diamantes, encantamientos, aldeanos (comercio), granjas; portal al **Nether**
  (fortalezas, varas de blaze, bastiones).
- **Late game:** ojos de ender → fortaleza → **End**; el **Ender Dragon** es el jefe final "de
  referencia": al derrotarlo se abre el portal de salida y un *End gateway* a las islas exteriores
  (élitros, ciudades del End). Se puede reinvocar ([wiki, Ender Dragon](https://minecraft.wiki/w/Ender_Dragon)).
  El Wither es el jefe opcional alternativo.
- **Post-game:** netherite, granjas técnicas, megaconstrucciones. Aquí es donde un mundo vanilla
  suele "acabarse" para un grupo, y donde los modpacks agregan una progresión nueva (máquinas,
  magia, quests, jefes de mods). **SUPUESTO** (consenso de comunidad, no fuente primaria).

**Dimensiones:** Overworld, Nether, End. Muchos mods agregan dimensiones propias (Twilight Forest,
minería, el Aether…), lo que importa para backups y tamaño del mundo.

### 1.3 Java vs Bedrock

| | Java Edition | Bedrock Edition |
|---|---|---|
| Plataformas | PC (Windows, macOS, Linux) | Windows, Xbox, Switch, PlayStation, iOS, Android… |
| Personalización | **mods** (Fabric, NeoForge, Forge, Quilt), datapacks, resource packs | *add-ons* y Marketplace oficial |
| Crossplay | entre Java | entre todas las plataformas Bedrock |
| Servidores | jar oficial + loaders + Paper, etc. | Bedrock Dedicated Server |

Fuente: [wiki, Bedrock Edition](https://minecraft.wiki/w/Bedrock_Edition) (código C++,
crossplay, Marketplace). **Esta app es exclusivamente Java**: los mods de Modrinth y `.mrpack` son
Java. Un amigo que juega en consola o móvil no puede unirse a un servidor modded salvo con puentes
como [Geyser](https://geysermc.org/wiki/geyser/), que no traduce contenido de mods (**SUPUESTO**:
la página de Geyser consultada no lo detalla; es el conocimiento común).

### 1.4 Cadencia de actualizaciones: "game drops" y la numeración nueva

- Desde 2024 Mojang dejó la gran actualización anual por **game drops**: actualizaciones temáticas
  más pequeñas, **3–4 por año, más o menos trimestrales**. Armored Paws (abril 2024) fue la primera
  anunciada así; Bats and Pots (dic. 2023) se reclasificó después
  ([wiki, Game drop](https://minecraft.wiki/w/Game_drop); [anuncio, cobertura de prensa](https://www.techradar.com/gaming/consoles-pc/minecraft-to-get-more-frequent-content-updates-in-the-future-work-on-native-ps5-version-continues)).
- Consecuencia: la serie 1.21 llegó a **1.21.11** (12 versiones). 1.21.4 (*The Garden Awakens*,
  2024-12-03), 1.21.5 (*Spring to Life*), 1.21.6 (*Chase the Skies*), 1.21.9 (*The Copper Age*),
  1.21.11 (*Mounts of Mayhem*, 2025-12-09) ([wiki 1.21.4](https://minecraft.wiki/w/Java_Edition_1.21.4),
  [wiki 1.21.11](https://minecraft.wiki/w/Java_Edition_1.21.11), [Game drop](https://minecraft.wiki/w/Game_drop)).
- **Desde 2026 la numeración es por año:** `AA.D.H` (año, drop, hotfix). 26.1 (2026-03-24),
  26.1.1, 26.1.2, 26.2 (2026-06-16, *Chaos Cubed*), 26.3 (2026-09-15, *Wilderness Bound*); hay
  snapshots de 26.4 ([minecraft.net](https://www.minecraft.net/en-us/article/minecraft-new-version-numbering-system),
  [wiki 26.1](https://minecraft.wiki/w/Java_Edition_26.1), [26.2](https://minecraft.wiki/w/Java_Edition_26.2),
  [26.3](https://minecraft.wiki/w/Java_Edition_26.3); fechas confirmadas en `GET /v2/tag/game_version` de Modrinth).
- **26.1 exige Java 25** y es la primera versión publicada **sin ofuscar**; 1.21.11 fue la última
  ofuscada, la última que pide Java 21 y la última con formato `1.x.y`
  ([wiki 26.1](https://minecraft.wiki/w/Java_Edition_26.1), [wiki 1.21.11](https://minecraft.wiki/w/Java_Edition_1.21.11)).

**Java requerido por versión** (relevante para la imagen Docker del egg en Pterodactyl), según la
[wiki, *Tutorial: Update Java*](https://minecraft.wiki/w/Tutorial:Update_Java):

| Minecraft | Java |
|---|---|
| 1.12 – 1.16.5 | 8 |
| 1.17 – 1.17.1 | 16 |
| 1.18 – 1.20.4 | 17 |
| 1.20.5 – 1.21.11 | 21 |
| 26.1+ | 25 |

Las versiones viejas suelen correr con Java más nuevo, pero **Forge 1.12.2/1.16.5 en la práctica
pide Java 8** (**SUPUESTO**: experiencia de comunidad; no verificado en la documentación de Forge).

### 1.5 Por qué la gente se queda en versiones viejas

Lo que dice la API de Modrinth hoy (proyectos que declaran soportar cada versión):

| Versión | Mods | Modpacks | Por qué sigue viva |
|---|---:|---:|---|
| 1.7.10 | 984 | 66 | la era clásica (GregTech: New Horizons y packs "expert" de culto) |
| 1.12.2 | 2.834 | 194 | última antes de *The Flattening* (1.13 reescribió IDs y comandos); RLCraft, SkyFactory 4, SevTech |
| 1.16.5 | 5.834 | 297 | Nether Update; última Forge estable antes de los cambios de mundo de 1.17/1.18; ATM6 |
| 1.18.2 | 8.802 | 438 | Caves & Cliffs completo; Vault Hunters 3, DawnCraft |
| 1.19.2 | 12.252 | 1.069 | ATM8, BMC1 Fabric |
| **1.20.1** | **34.685** | **7.355** | **la versión modded más grande**: última que concentró Forge antes del cisma con NeoForge; Create, BMC4, ATM9, Prominence II |
| 1.20.4 | 13.592 | 1.262 | |
| **1.21.1** | **32.034** | **5.078** | **la versión estable de NeoForge**: ATM10, Create 6, Cobblemon, Pixelmon |
| 1.21.4 | 17.623 | 1.877 | |
| 1.21.8 | 17.369 | 1.543 | |
| 1.21.10 | 15.281 | 1.599 | |
| 1.21.11 | 19.580 | 2.902 | última 1.x, mucha actividad Fabric |
| 26.1 / 26.1.2 | 11.669 / 14.886 | 460 / 1.363 | Java 25, sin ofuscar |
| 26.2 | 13.616 | 1.490 | |
| 26.3 | 7.664 | 489 | salió hace menos de un mes |

Las razones, con fuente:
- **NeoForge** nació en julio de 2023 como fork de Forge, con casi todo el equipo
  ([NeoForged, 2023](https://neoforged.net/news/2023-retrospection/)). Eso partió el ecosistema:
  1.20.1 quedó como el "último Forge común", y NeoForge fijó **1.21.1** como versión de largo plazo
  ("we will continue to support 1.21.1") ([NeoForged, 2024](https://neoforged.net/news/2024-retrospection/)).
  A fines de 2025, 1.21.1 seguía siendo la versión NeoForge más popular, con más de 16.000 mods
  ([NeoForged, 2025](https://neoforged.net/news/2025-retrospection/)).
- **Los mods de contenido grandes no siguen cada drop.** En Modrinth hoy, Create, Farmer's Delight,
  Mekanism, Ars Nouveau y Cobblemon llegan hasta **1.21.1**; Botania, Tinkers' Construct, Alex's Mobs
  y Thermal hasta **1.20.1** o antes. En cambio los mods de rendimiento y utilidades (Sodium,
  Lithium, FerriteCore, JEI, Xaero) ya están en **26.3**. Con drops trimestrales, perseguir la última
  versión deja fuera justo el contenido que hace interesante un pack.
- Un mundo no se puede bajar de versión (§5.5): una vez que el grupo empieza, se queda.
- La comunidad juega "la versión donde está su mod favorito". **SUPUESTO** como motivación, pero
  coherente con los números de arriba.

---

## 2. Estilos de juego y lo que busca la gente

Ninguna fuente primaria clasifica "estilos"; esta taxonomía sale de las categorías de Modrinth y
CurseForge y de los packs más descargados. Los ejemplos son reales; la motivación es **SUPUESTO**.

| Estilo | Qué busca | Ejemplos (loader, versión) |
|---|---|---|
| **Construcción** | bloques decorativos, herramientas de construcción, a veces creativo | Chipped, Macaw's, WorldEdit/Axiom (**SUPUESTO**: ejemplos de memoria) |
| **Redstone / técnico** | granjas, máquinas vanilla; vanilla "puro" con mods de rendimiento | packs de optimización como RSO (Fabric, 26.3) |
| **Exploración** | worldgen nuevo, estructuras, biomas | Terralith, Biomes O' Plenty, mapas (Xaero, JourneyMap) |
| **Aventura / RPG** | jefes, dungeons, clases, loot | Prominence II (Fabric 1.20.1), DawnCraft (Forge 1.18.2), RLCraft (Forge 1.12.2) |
| **Tecnología / automatización** | máquinas, energía, almacenamiento lógico | Create, Mekanism, Applied Energistics 2 |
| **Magia** | progresión mística | Ars Nouveau, Botania |
| **Kitchen sink** | "un poco de todo", cada amigo se especializa | All the Mods 9 (Forge 1.20.1), ATM10 (NeoForge 1.21.1) |
| **Skyblock / quests** | progresión guiada desde casi nada | SkyFactory 4 (1.12.2), SkyBlock Enhanced (Fabric) |
| **Expert** | recetas encadenadas, cientos o miles de quests, meses de juego | GregTech: New Horizons (>300 mods, >3.900 quests, [sitio oficial](https://www.gtnewhorizons.com/)) |
| **Vanilla+** | "Minecraft pero mejor", sin romper la sensación | Better MC (Forge/Fabric 1.20.1), Vanilla Perfected |
| **Temáticos** | Pokémon, zombis, terror | Cobblemon (Fabric 1.21.1), Pixelmon (NeoForge 1.21.1), Zombie Invade, Cave Horror Project |
| **Rendimiento / estética (cliente)** | más FPS, shaders, sin cambiar el juego | Fabulously Optimized (Fabric, 26.3), Iris + Complementary/Bliss |
| **SMP con amigos** | mundo compartido de larga duración, chat de voz, mapas | vanilla+ con Simple Voice Chat, Xaero, Waystones |

**Servidores públicos vs privados.** Los públicos (minijuegos, Hypixel) corren Paper/plugins y el
cliente es vanilla o con mods de rendimiento; los privados con amigos son el terreno de los modpacks
completos, donde todos deben tener el mismo pack. Este proyecto vive en el segundo. Que haya packs
de cliente para Hypixel (Skyblocker Modpack, 1,1 M descargas en Modrinth) muestra el primer caso.

---

## 3. Vanilla vs modded

| | Vanilla | Modded |
|---|---|---|
| Cliente | el launcher oficial | Modrinth App, Prism, CurseForge App, ATLauncher… |
| Servidor | jar oficial o Paper/Purpur + plugins | Fabric/NeoForge/Forge/Quilt + mods |
| Qué deben instalar los amigos | nada (o mods de cliente opcionales) | **el mismo pack, misma versión** |
| Personalización sin mods | datapacks y resource packs | también, más mods |

- **Datapacks:** carpeta o zip con `pack.mcmeta` que cambia lógica (recetas, loot, worldgen,
  dimensiones…). Son **por mundo, del lado servidor**, y su `pack_format` cambia con cada versión
  ([wiki, Data pack](https://minecraft.wiki/w/Data_pack)). Terralith existe como datapack **y** como
  mod (Modrinth lista `datapack` entre sus loaders).
- **Resource packs:** texturas, modelos, sonidos; **del lado cliente**. Un servidor puede ofrecer uno
  con `resource-pack` y obligarlo con `require-resource-pack`
  ([wiki, Resource pack](https://minecraft.wiki/w/Resource_pack)).
- **Shaders:** las *core shaders* oficiales van en resource packs; los shaders "de verdad" de la
  comunidad requieren OptiFine o **Iris** (188 M descargas en Modrinth; 3er mod más descargado).
  Shaderpacks como Complementary Reimagined (70 M) y Bliss (15 M) son solo cliente.
- El cliente modded "solo rendimiento" es el uso más masivo: el top de Modrinth está dominado por
  mods de cliente (Sodium, Iris, Entity Culling, ImmediatelyFast, Mod Menu), y el modpack más
  descargado, **Fabulously Optimized** (18,4 M), es solo cliente y funciona en cualquier servidor
  vanilla ([Modrinth](https://modrinth.com/modpack/fabulously-optimized)).

---

## 4. Mods y modpacks más frecuentes

### 4.1 Mods (Modrinth, 2026-10-12)

`cs`/`ss` = `client_side`/`server_side` de la API (campos que Modrinth marca como **obsoletos, a
reemplazar por `environment`**, [docs](https://docs.modrinth.com/api/operations/getproject/)).

**Rendimiento**

| Mod | Descargas | Loaders | Lado | Versiones |
|---|---:|---|---|---|
| Sodium | 242,8 M | Fabric, NeoForge, Quilt | solo cliente | hasta 26.3 |
| Entity Culling | 180,3 M | todos | solo cliente | 26.3 |
| FerriteCore | 160,0 M | todos | ambos opcional | 1.16.5–26.3 |
| Lithium | 135,9 M | Fabric, NeoForge, Quilt | ambos opcional (útil en servidor) | 26.3 |
| ImmediatelyFast | 131,5 M | todos | cliente | 26.3 |
| ModernFix | 81,3 M | Fabric, Forge, NeoForge | ambos | 1.16.4–26.1.2 |
| C2ME | 39,9 M | Fabric | **servidor requerido**, cliente opcional | hasta 26.4 snapshot |
| Embeddium | 36,9 M | Forge/NeoForge | cliente | **se quedó en 1.21.4** (port de Sodium para Forge, ya sin uso desde que Sodium soporta NeoForge) |
| Noisium | 24,8 M | todos | **solo servidor** | hasta 1.21.6 |
| Starlight | 16,2 M | Fabric | ambos | **hasta 1.20.4** (abandonado) |
| Krypton | 43,6 M | Fabric | ambos (red) | 26.3 |
| spark (profiler) | 23,5 M | todos | ambos | 26.3 |
| Chunky (pre-generar) | 19,2 M | también Paper/Bukkit | ambos | 26.3 |
| Distant Horizons | 37,0 M | todos | ambos opcional | 1.12.2–26.3 |

Lección: los mods de rendimiento **cambian y mueren** (Starlight, Embeddium, Noisium); un pack
congelado hace un año puede tener mods sin sucesor en la versión siguiente.

**Utilidades**

| Mod | Descargas | Lado |
|---|---:|---|
| JEI | 82,4 M | ambos opcional |
| EMI | 30,0 M | cliente requerido, servidor opcional |
| REI | 26,9 M | ambos opcional |
| Xaero's Minimap / World Map | 116,7 M / 102,1 M | cliente requerido, servidor opcional |
| JourneyMap | 15,2 M | ambos opcional |
| Jade (info al mirar bloques) | 71,3 M | ambos opcional |
| AppleSkin | 94,8 M | ambos opcional |
| Mod Menu | 154,1 M | solo cliente (Fabric) |
| Simple Voice Chat | 73,5 M | **ambos**; abre el **puerto UDP 24454** en el servidor ([Modrinth](https://modrinth.com/plugin/simple-voice-chat)) |
| Waystones | 26,8 M | ambos requerido |
| VeinMiner | 93,7 M | servidor requerido |

**Contenido**

| Mod | Descargas (Modrinth) | Loaders | Última versión en Modrinth |
|---|---:|---|---|
| Biomes O' Plenty | 38,2 M | Fabric, Forge, NeoForge | 26.3 |
| Cobblemon | 36,3 M | Fabric, Forge, NeoForge | 1.21.1 |
| Create | 27,5 M | Forge, NeoForge | 1.21.1 |
| Farmer's Delight | 25,1 M | Forge, NeoForge | 1.21.1 |
| Terralith | 24,1 M | + datapack | 26.3 (servidor requerido) |
| Sophisticated Backpacks | 20,0 M | Forge, NeoForge | 26.3 |
| Alex's Mobs | 15,5 M | Forge, NeoForge | 1.20.1 |
| Applied Energistics 2 | 6,2 M | Fabric, Forge, NeoForge | 26.3 |
| Botania | 4,2 M | Fabric, Forge, Quilt | 1.20.1 |
| Ars Nouveau | 4,0 M | Forge, NeoForge | 1.21.1 |
| Tinkers' Construct | 4,0 M | Forge, NeoForge | 1.20.1 |
| Mekanism | 3,8 M | Forge, NeoForge | 1.21.1 |

Los mods de contenido "clásicos" tienen **muchas menos descargas en Modrinth** que en CurseForge,
donde nacieron. El catálogo de Modrinth está sesgado a Fabric y a cliente. **SUPUESTO** como
explicación; el sesgo en sí se ve en los datos (§4.3).

**Librerías** (dependencias que el usuario nunca pide pero siempre aparecen): Fabric API (275,6 M,
el proyecto más descargado), Cloth Config, YACL, Architectury, Fabric Language Kotlin, Kotlin for
Forge, Forge Config API Port, GeckoLib, Balm, Puzzles Lib, Collective, Bookshelf, Moonlight,
owo-lib, TerraBlender, Placeholder API, Konkrete/Melody (FancyMenu). Varias son **requeridas en
ambos lados** (Architectury, owo-lib, Bookshelf, Moonlight, TerraBlender, Fzzy Config): si faltan
en el servidor o en un cliente, no conecta.

### 4.2 Modpacks

**Modrinth, por descargas** (18.798 modpacks en total):

| Pack | Descargas | Loader | Versión | Lado |
|---|---:|---|---|---|
| Fabulously Optimized | 18,4 M | Fabric | 26.3 | solo cliente |
| Zombie Invade 100 Days | 15,0 M | Forge | 1.20.1 | ambos |
| Cobblemon Official Modpack | 11,2 M | Fabric | 1.21.1 | ambos |
| COBBLEVERSE | 7,1 M | Fabric | 1.21.1 | ambos |
| BattleArmory TACZ | 3,6 M | Forge/NeoForge | 1.20.1 | ambos |
| Better MC [FABRIC] BMC2 | 3,6 M | Fabric | 1.20.1 | ambos |
| Better MC [FORGE] BMC4 | 2,3 M | Forge | 1.20.1 | ambos |
| The Pixelmon Modpack | 2,3 M | Forge/NeoForge | 1.21.1 | ambos |
| Prominence II | 2,1 M | Fabric | 1.20.1 | ambos |
| Create+ | 1,4 M | Forge/NeoForge | 1.21.1 | ambos |

**CurseForge, por descargas totales** (listado público consultado hoy,
[CurseForge](https://www.curseforge.com/minecraft/search?page=1&pageSize=20&sortBy=total+downloads&class=modpacks);
cifras leídas por un extractor automático, tómense como aproximadas):
RLCraft (~30 M, Forge 1.12.2), All the Mods 10 (~22 M, NeoForge 1.21.1), Pixelmon (~20 M),
Better MC BMC4 (~19 M, Forge 1.20.1), SkyFactory 4 (~14 M, 1.12.2), ATM9 (~14 M, Forge 1.20.1),
Prominence II (~13 M, Fabric 1.20.1), DawnCraft (~10 M, Forge 1.18.2), ATM8 (1.19.2), Fabulously
Optimized (~8 M), Roguelike Adventures and Dungeons (1.12.2), SevTech: Ages (1.12.2), ATM6 (1.16.5),
Vault Hunters 3 (1.18.2).

**Hallazgo importante:** **All the Mods, RLCraft, SkyFactory y Vault Hunters no están en
Modrinth** (búsqueda en la API: sin resultados del autor original; solo homenajes o clones). Los
packs más jugados del mundo modded viven en CurseForge.

### 4.3 Loaders

Modpacks en Modrinth por loader: **Fabric 13.423**, Forge 3.840, NeoForge 2.545, Quilt 686 (un pack
puede tener más de uno). Fabric domina los packs de cliente y vanilla+; Forge 1.20.1 y NeoForge
1.21.1 dominan los packs grandes de contenido (Create, ATM, Mekanism son Forge/NeoForge).

---

## 5. Servidores con amigos

### 5.1 Cómo se hostea

| Opción | Mods | Notas |
|---|---|---|
| **Realms (Java)** | **no** ("Mods and plugins cannot be installed") | hasta 11 jugadores (dueño + 10), backups automáticos cada pocas horas, 7,99 USD/mes ([wiki, Realms](https://minecraft.wiki/w/Realms)) |
| LAN / "abrir a LAN" / Essential, e4mc | sí, en el PC del anfitrión | el mundo solo existe mientras el anfitrión juega (**SUPUESTO**) |
| Host comercial (Shockbyte, Bisect, Apex, Modrinth Hosting…) | sí, con instalador de packs de 1 clic | la mayoría corre sobre Pterodactyl o un panel propio (**SUPUESTO**) |
| **Pterodactyl propio** (este caso) | sí | eggs = plantillas JSON de instalación y arranque; los oficiales cubren Paper/Forge/Fabric; los instaladores de packs son add-ons, varios de pago |

El repositorio ya investigó Pterodactyl a fondo (`docs/research/2026-10-operacion-pterodactyl.md`);
aquí solo lo que es del juego.

**Requisitos.** La wiki pide CPU de buen rendimiento por núcleo (el servidor usa "three cores at
most") y RAM modesta para vanilla: 1 GB para 1–4 jugadores, 2 GB para 5–10
([wiki, Server/Requirements](https://minecraft.wiki/w/Server/Requirements)). Un pack grande es otra
escala: las guías de hosting dan **10–12 GB para ATM10 con 1–10 jugadores** y 8–10 GB por cliente
([WinterNode](https://winternode.com/games/minecraft/modpacks/all-the-mods-10-atm10), guía
comercial). La regla práctica "packs ligeros 4–6 GB, kitchen sink 8–12 GB" es **SUPUESTO**.

**Cuántos jugadores.** Realms limita a 10 amigos, lo que sugiere el tamaño de diseño de Mojang
para "jugar con amigos". Un grupo típico de 2–10, con 2–4 conectados a la vez, es **SUPUESTO**.

**Acceso.** `white-list=true` impide entrar a quien no está en la lista; `enforce-whitelist` además
expulsa a los conectados que no estén; `online-mode` exige cuentas verificadas; `max-players` por
defecto 20 ([wiki, server.properties](https://minecraft.wiki/w/Server.properties)). Se gestiona con
`/whitelist add` y `/op` ([tutorial](https://minecraft.wiki/w/Tutorial:Setting_up_a_Java_Edition_server)).

### 5.2 Mods de cliente y de servidor

- Cada mod declara su lado. En `.mrpack`, cada archivo lleva `env.client` y `env.server` con
  `required`/`optional`/`unsupported`; hay `overrides/`, `server-overrides/` y `client-overrides/`
  aplicados en capas ([Modrinth, formato mrpack](https://support.modrinth.com/en/articles/8802351-modrinth-modpack-format-mrpack)).
- **Un mod solo de cliente en el servidor lo tumba al arrancar**: el error típico es
  "Attempted to load class … for invalid dist DEDICATED_SERVER" o un `NoClassDefFoundError` de
  `net/minecraft/client/...` ([guía Supercraft](https://supercraft.host/wiki/minecraft/modded_server_wont_start_client_class/);
  [issue de Distant Horizons](https://gitlab.com/distant-horizons-team/distant-horizons/-/issues/1147)).
- **Un mod de contenido que falta en el cliente** impide conectar (registros desalineados).
  **SUPUESTO** en el mensaje exacto; el fenómeno es general.
- Mods solo de servidor (Noisium, C2ME, VeinMiner sin hotkey) no se reparten a los amigos.
- Mods opcionales de cliente (minimapa, shaders, Zoomify, sonido) son preferencia de cada amigo.

### 5.3 Actualizar un pack en un mundo en curso

Lo que recomiendan los hosts ([Shockbyte](https://shockbyte.com/en-gb/help/knowledgebase/articles/how-to-update-modpacks-installed-on-your-server),
[xGamingServer](https://xgamingserver.com/docs/minecraft/update-modpacks)):
1. Detener el servidor y **respaldar el mundo**.
2. Reemplazar `mods/` (y `libraries/`); `config/`, `scripts/`, `kubejs/` solo si el pack lo pide,
   porque pueden tener ajustes propios del admin.
3. Nunca tocar `world/`, `server.properties`, `eula.txt`, whitelist/ops.
4. Leer el changelog del pack: algunos cambios exigen mundo nuevo.
5. Arrancar, leer la consola, entrar y revisar bloques o ítems faltantes.

Además, del lado del juego:
- **Worldgen nuevo solo aparece en chunks nuevos.** Agregar Terralith o BoP a mitad de mundo genera
  bordes abruptos entre lo viejo y lo nuevo, y lo explorado no cambia. Por eso se pre-genera con
  Chunky *después* de fijar el worldgen ([Chunky](https://modrinth.com/mod/chunky)). **SUPUESTO**
  la parte de los bordes (comportamiento conocido, no citado de fuente primaria).
- Todos los amigos deben actualizar su cliente a la vez: el desfase entre versiones de mods es el
  problema número uno de un SMP modded (**SUPUESTO**, consenso de foros).

### 5.4 Backups

- No copiar los archivos del mundo con el servidor escribiendo: se corrompe el backup. Usar
  `/save-off` + `/save-all flush`, respaldar y `/save-on`, o detener el servidor
  ([wiki, Commands/save](https://minecraft.wiki/w/Commands/save); [guía de backup](https://loafhosts.com/guides/minecraft/minecraft-world-backup-guide)).
- Varias versiones de backup, alguna fuera del servidor, y verificadas.
- Un crash a mitad de un guardado puede dejar un `region/r.X.Z.mca` inválido (32×32 chunks); se puede
  restaurar solo ese archivo ([Shockbyte, corrupción](https://shockbyte.com/billing/knowledgebase/46/How-to-Fix-Corrupted-Minecraft-Worlds.html)).
  `sync-chunk-writes` reduce ese riesgo ([wiki](https://minecraft.wiki/w/Server.properties)).

### 5.5 Qué se rompe

| Acción | Qué pasa |
|---|---|
| **Quitar un mod que agrega bloques/ítems/entidades** | Forge/NeoForge guardan el mapa de registros en el mundo y avisan ("missing registry entries"); si se confirma (`/fml confirm` o `-Dfml.queryResult=confirm`), **esos bloques e ítems se borran** y se crea un backup automático. **Fabric no avisa**: simplemente faltan ([GameServerKings](https://www.gameserverkings.com/knowledge-base/minecraft/error-missing-mods-in-world/), [foro Forge](https://forums.minecraftforge.net/topic/91346-fml-confirm/)) |
| Quitar una dimensión de un mod | lo construido allí se pierde; jugadores dentro aparecen en otro lado (**SUPUESTO**) |
| **Bajar de versión de Minecraft** | no soportado; el juego avisa; puede corromper el mundo (comunidad; **SUPUESTO** la cita primaria exacta) |
| Subir de versión de Minecraft | el mundo se convierte (sin vuelta atrás); los mods del pack tienen que existir para la versión nueva; el Java requerido puede cambiar (17 → 21 → 25) |
| Cambiar de loader (Forge → NeoForge, Fabric → NeoForge) | en la práctica, mundo nuevo: cambian los mods y sus IDs (**SUPUESTO**) |
| Actualizar un mod con cambios de datos | puede pedir migración propia o perder datos de ese mod (**SUPUESTO**, depende de cada mod) |
| Cambiar configs del servidor (`serverconfig/`, `defaultconfigs/`) | se pisan ajustes que el admin hizo a mano (**SUPUESTO**, patrón de Forge/NeoForge) |

---

## 6. Problemas frecuentes

1. **Crash al arrancar el servidor** por un mod solo de cliente (§5.2).
2. **Mods o versiones desalineadas entre cliente y servidor**: "mismatched mod list", no conecta.
   Lo resuelve que todos instalen el mismo `.mrpack`.
3. **Incompatibilidades entre mods**: dos mods que tocan lo mismo (p. ej. renderizado: Sodium y
   OptiFine). Modrinth tiene dependencias `incompatible` que casi nadie usa (ver
   `docs/research/2026-10-resolucion-y-formatos.md`).
4. **Memoria**: poca RAM da `OutOfMemoryError` o pausas de GC; demasiada también empeora el GC.
   Packs grandes piden 8–12 GB (§5.1). **SUPUESTO** la parte de "demasiada".
5. **Rendimiento (TPS bajo)**: generación de chunks, granjas, máquinas, muchas entidades. Se mide con
   **spark**, se alivia con pre-generación (Chunky), `view-distance`/`simulation-distance` y mods
   de servidor (Lithium, FerriteCore, ModernFix, C2ME).
6. **Mod que falta en el cliente** o versión equivocada del pack en un amigo.
7. **Java equivocado** para la versión (Java 21 con 26.x no arranca; Forge viejo con Java nuevo).
8. **Corrupción por quitar mods** (§5.5).
9. **Actualizar Minecraft a mitad de mundo** porque salió un drop, y descubrir que la mitad de los
   mods no existe en la versión nueva (§1.5).
10. **Puertos**: voice chat necesita UDP 24454 además del 25565 TCP.

---

## 7. Ciclo de vida de un mundo y un pack en un grupo de amigos

Ninguna fuente primaria lo describe; es la síntesis de los puntos anteriores y del comportamiento
común en foros. Todo este apartado es **SUPUESTO** salvo donde enlaza.

1. **Elegir.** Alguien propone un estilo (Create, kitchen sink, vanilla+, Cobblemon). El estilo
   decide el loader y la versión: Create/ATM → NeoForge 1.21.1 o Forge 1.20.1; vanilla+ y
   rendimiento → Fabric reciente.
2. **Armar y probar.** Se parte de un pack existente o se arma uno; se prueba en un mundo local.
3. **Arranque.** Se fija el worldgen, se pre-generan chunks, se abre el servidor, whitelist, se
   reparte el pack a los amigos.
4. **Las primeras semanas**: la etapa de más jugadores conectados y más cambios: falta un mod,
   alguno molesta, hay que ajustar configs. Actualizaciones **menores** frecuentes.
5. **Madurez.** El grupo raleja; quedan 1–3 jugadores constantes. Se actualizan mods solo por bugs.
6. **Congelamiento.** Cuando el mundo tiene mucho construido, el admin **deja de actualizar**: el
   riesgo de romper algo pesa más que la novedad. Un drop nuevo de Minecraft **no** se aplica al
   mundo en curso.
7. **Fin de temporada.** El grupo pierde interés o sale un pack/versión nueva atractiva. Se archiva
   el mundo (backup descargable) y empieza una **temporada nueva**: mundo nuevo, a menudo versión y
   pack nuevos. Las temporadas duran semanas a pocos meses.

---

## Qué implica para esta app

Recomendaciones concretas, ordenadas por impacto. Cada una cita el hallazgo que la sostiene.

1. **El cambio de versión de Minecraft es una "temporada nueva", no un update.** Un despliegue que
   cambia `minecraft` o el loader respecto del build actual del servidor debería exigir confirmación
   explícita y backup, y **un build con versión de Minecraft menor que la del mundo debería
   bloquearse** (§5.5). Aplica igual al rollback: volver a un build anterior que tenía otra versión
   de MC es bajar de versión.
2. **Diff con semántica de riesgo.** Al comparar build nuevo contra el manifiesto del servidor,
   clasificar: *agrega mod* (bajo), *actualiza mod* (medio), **quita mod con contenido o dimensión**
   (alto: borra bloques e ítems; en Fabric sin aviso), *agrega worldgen* (avisar que solo afecta
   chunks nuevos), *cambia librería requerida en ambos lados* (todos los clientes deben actualizar).
3. **Separar de verdad cliente y servidor.** Usar `environment` de Modrinth (no los campos
   obsoletos `client_side`/`server_side`): nunca subir al servidor algo `unsupported` en servidor
   (lo tumba al arrancar), y exportar el `.mrpack` con `env` correcto para que los amigos reciban
   mods de cliente opcionales como opcionales (§4.1, §5.2).
4. **Que los amigos tengan exactamente el build del servidor.** El problema más común es el
   desfase. La app ya produce el `.mrpack`; conviene un enlace estable "build recomendado del
   servidor X" y mostrar el número de build en algún lugar visible del servidor (por ejemplo el
   MOTD; **SUPUESTO** que sea útil, a validar con el dueño).
5. **Java según versión.** Validar antes de desplegar que la imagen Docker del egg corresponde
   (8/16/17/21/25, §1.4). Desplegar 26.x a un servidor con Java 21 falla al arrancar.
6. **Backup consistente.** Si el backup se toma con el servidor encendido, envolverlo en
   `save-off` / `save-all flush` / `save-on`; si no, detener antes (§5.4). Guardar más de un backup
   y permitir descargarlo (fin de temporada, §7).
7. **No pisar lo del admin.** Al desplegar, `mods/` es del pack; `config/`, `defaultconfigs/`,
   `kubejs/`, `world/serverconfig/` y `server.properties` pueden tener cambios manuales. Tratar
   overrides del pack y archivos del admin por separado (coincide con "contenido del admin" del
   plan, i-7985ee-063a3a).
8. **Datapacks y resource packs son contenido de primera clase.** Los datapacks van en
   `world/datapacks/` (por mundo, servidor); resource packs y shaderpacks son solo cliente. Terralith
   viene en ambas formas (§3).
9. **Elegir versión con datos.** Al crear un pack, mostrar cuántos de los mods pedidos existen para
   cada versión/loader. Hoy 1.20.1 (Forge) y 1.21.1 (NeoForge) concentran el contenido; 26.x tiene
   rendimiento y utilidades pero no Create, Mekanism, Farmer's Delight ni Cobblemon (§1.5, §4.1).
10. **Congelar es la norma; la app debe hacerlo fácil.** Un puntero "recomendado" que no se mueve
    solo, y un aviso de "hay actualizaciones" que no se aplica sin decisión (§7). Distinguir
    "actualizaciones de mods en la misma versión de MC" (rutina) de "subir MC" (temporada).
11. **Memoria y puertos.** Avisar si el pack es grande y el servidor tiene poca RAM (orden: 8–12 GB
    para kitchen sink, §5.1), y si el pack trae Simple Voice Chat, recordar la asignación UDP 24454
    en Pterodactyl.
12. **Alcance honesto: Modrinth no tiene los packs más jugados.** ATM, RLCraft, SkyFactory y Vault
    Hunters están solo en CurseForge (§4.2). La app arma packs desde Modrinth; "importar ATM10" no es
    posible con esta fuente. Decirlo en la interfaz evita la expectativa.
13. **Mods abandonados.** Un pack congelado puede tener mods sin sucesor (Starlight, Embeddium,
    Noisium). Al subir de versión, la resolución debería informar "sin versión para X" con
    reemplazos conocidos, que es el requisito de reemplazos de `docs/requirements.md`.
14. **Riesgos que la app no puede evitar** y debe explicitar: worldgen ya generado, datos internos
    de mods que cambian entre versiones, y mundos corruptos por crash. El rollback restaura archivos,
    pero si los jugadores siguieron jugando tras el update, se pierde ese progreso.

---

## No verificado (SUPUESTOS)

- Que el post-game vanilla sea donde un grupo "termina" un mundo y los packs extienden la progresión.
- Que Geyser no traduzca contenido de mods (la página consultada no lo detalla).
- Que Forge 1.12.2/1.16.5 necesite Java 8 en la práctica aunque la wiki diga "8 o más".
- La motivación de quedarse en versiones viejas por "mod favorito" (los números la apoyan, pero no
  hay encuesta).
- La taxonomía de estilos y su motivación (§2); los ejemplos de mods de construcción.
- Que el sesgo de Modrinth hacia Fabric/cliente explique las bajas descargas de mods de contenido.
- Tamaño típico del grupo (2–10, 2–4 a la vez) y duración de temporadas (§5.1, §7).
- Todo el ciclo de vida del §7.
- Rangos de RAM generales ("4–6 GB ligeros, 8–12 GB kitchen sink"); el de ATM10 viene de una guía
  comercial, no del autor del pack; que "demasiada RAM" empeore el GC.
- Que la mayoría de hosts comerciales use Pterodactyl o similar.
- Comportamiento de LAN/Essential/e4mc (el mundo solo vive mientras el anfitrión juega).
- El mensaje exacto del desfase de mods entre cliente y servidor.
- Bordes abruptos de worldgen al agregar mods a mitad de mundo.
- La cita primaria de que bajar de versión no está soportado (solo fuentes de comunidad).
- Efectos de quitar una dimensión, cambiar de loader, migraciones de datos de mods, y que se pisen
  `serverconfig/`/`defaultconfigs/`.
- Que el desfase de versiones sea el problema número uno de un SMP modded.
- Las cifras de CurseForge (leídas por extractor automático; aproximadas).
- Cifras de ventas/jugadores activos de Minecraft: no se incluyeron porque solo se hallaron en
  fuentes secundarias (blogs de hosting citan >350 M copias y ~200 M de jugadores activos mensuales).

## Fuentes

- Minecraft Wiki: [Game mode](https://minecraft.wiki/w/Game_mode), [Ender Dragon](https://minecraft.wiki/w/Ender_Dragon),
  [Bedrock Edition](https://minecraft.wiki/w/Bedrock_Edition), [Game drop](https://minecraft.wiki/w/Game_drop),
  [Java Edition 1.21.4](https://minecraft.wiki/w/Java_Edition_1.21.4), [1.21.11](https://minecraft.wiki/w/Java_Edition_1.21.11),
  [26.1](https://minecraft.wiki/w/Java_Edition_26.1), [26.2](https://minecraft.wiki/w/Java_Edition_26.2),
  [26.3](https://minecraft.wiki/w/Java_Edition_26.3), [Tutorial: Update Java](https://minecraft.wiki/w/Tutorial:Update_Java),
  [Server/Requirements](https://minecraft.wiki/w/Server/Requirements), [Server.properties](https://minecraft.wiki/w/Server.properties),
  [Tutorial: Setting up a Java Edition server](https://minecraft.wiki/w/Tutorial:Setting_up_a_Java_Edition_server),
  [Realms](https://minecraft.wiki/w/Realms), [Data pack](https://minecraft.wiki/w/Data_pack),
  [Resource pack](https://minecraft.wiki/w/Resource_pack), [Commands/save](https://minecraft.wiki/w/Commands/save).
- Mojang: [Minecraft new version numbering system](https://www.minecraft.net/en-us/article/minecraft-new-version-numbering-system).
- NeoForged: [2023](https://neoforged.net/news/2023-retrospection/), [2024](https://neoforged.net/news/2024-retrospection/),
  [2025](https://neoforged.net/news/2025-retrospection/).
- Modrinth: API `api.modrinth.com/v2` (`/search`, `/projects`, `/tag/game_version`) consultada el
  2026-10-12; [formato mrpack](https://support.modrinth.com/en/articles/8802351-modrinth-modpack-format-mrpack);
  [docs de proyecto](https://docs.modrinth.com/api/operations/getproject/);
  páginas de [Fabulously Optimized](https://modrinth.com/modpack/fabulously-optimized),
  [Simple Voice Chat](https://modrinth.com/plugin/simple-voice-chat), [Chunky](https://modrinth.com/mod/chunky).
- CurseForge: [modpacks por descargas totales](https://www.curseforge.com/minecraft/search?page=1&pageSize=20&sortBy=total+downloads&class=modpacks);
  [blog, top modpacks](https://blog.curseforge.com/top-minecraft-modpacks-on-curseforge/).
- GregTech: New Horizons: [sitio oficial](https://www.gtnewhorizons.com/).
- GeyserMC: [wiki](https://geysermc.org/wiki/geyser/).
- Guías de hosting y comunidad (secundarias): [Shockbyte, actualizar packs](https://shockbyte.com/en-gb/help/knowledgebase/articles/how-to-update-modpacks-installed-on-your-server),
  [xGamingServer](https://xgamingserver.com/docs/minecraft/update-modpacks),
  [GameServerKings, mods faltantes](https://www.gameserverkings.com/knowledge-base/minecraft/error-missing-mods-in-world/),
  [Supercraft, clase de cliente](https://supercraft.host/wiki/minecraft/modded_server_wont_start_client_class/),
  [Distant Horizons issue 1147](https://gitlab.com/distant-horizons-team/distant-horizons/-/issues/1147),
  [foro Forge, /fml confirm](https://forums.minecraftforge.net/topic/91346-fml-confirm/),
  [WinterNode, ATM10](https://winternode.com/games/minecraft/modpacks/all-the-mods-10-atm10),
  [LoafHosts, backups](https://loafhosts.com/guides/minecraft/minecraft-world-backup-guide),
  [Shockbyte, corrupción](https://shockbyte.com/billing/knowledgebase/46/How-to-Fix-Corrupted-Minecraft-Worlds.html),
  [TechRadar, game drops](https://www.techradar.com/gaming/consoles-pc/minecraft-to-get-more-frequent-content-updates-in-the-future-work-on-native-ps5-version-continues).
