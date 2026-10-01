# Plataformas similares: qué hacen, qué tomar y qué evitar

**Fecha:** 2026-10-01 · **Método:** documentación oficial y, en las herramientas abiertas, lectura
del código fuente (marcado **(src)**). Lo no comprobado dice **SIN VERIFICAR**. Fuentes [Sn] al
final. Solo lectura.

## Resumen

Nadie cubre lo que este proyecto se propone: **un servicio propio que arma un modpack resolviendo
dependencias, lo instala en los servidores de un panel Pterodactyl y lo mantiene actualizado con
seguridad.** Los paneles abiertos (PufferPanel, Crafty, MCSManager) casi no gestionan mods; los
eggs oficiales de Pterodactyl solo instalan un pack al reinstalar; los add-ons que sí lo hacen son
de pago y piden modificar Wings; los hostings (Modrinth Hosting, exaroton, Aternos, Bisect) lo
resuelven dentro de su plataforma cerrada. Las herramientas de línea de comandos (packwiz,
mrpack-install, itzg, FTB installer) resuelven partes, y de ellas salen las lecciones.

**El análogo más cercano es Technic Solder**: una API auto-alojada "entre el repositorio de
modpacks y el launcher", con *builds* numerados y punteros `recommended`/`latest`. Le falta lo que
`docs/requirements.md` ya pide: dependencias.

## Comparación

| Plataforma | Tipo | Manifiesto / estado | Dependencias | Lados | Actualizar / volver atrás | Qué instaló | Abierto |
|---|---|---|---|---|---|---|---|
| Eggs oficiales de Pterodactyl (Modrinth/CurseForge) [S1,S2] | script de instalación | lee `modrinth.index.json` / `manifest.json` | ninguna (pack ya resuelto) | Modrinth: solo `env.server=="required"` (src) | **solo reinstalando**, sin diff ni backup (src) | nada | sí |
| Modpack Installer / Mod Manager (ric-rac, Blueprint) [S3,S4] | add-on de pago para Pterodactyl | SIN VERIFICAR | SIN VERIFICAR | SIN VERIFICAR | "install, update and delete mods" | "identificación automática de versiones"; **requiere modificar Wings** | no |
| Pelican "Minecraft Modrinth" (plugin oficial) [S5,S6] | plugin del panel (Pelican en beta) | `mods/.modrinth-metadata.json` (project_id, version_id, filename, installed_at) (src) | ninguna (src) | tags del egg | instalar/actualizar/borrar, sin backup | su archivo de metadata | GPL-3.0 |
| Pelican Mod Manager (fork de gOOvER) [S7] | plugin | `.pelican-mod-manager.json` | sin documentar | — | actualizaciones masivas en segundo plano | **por hash** (sha512 Modrinth, murmur2 CurseForge, sha256 Hangar); re-hashea solo si cambió tamaño o mtime | GPL-3.0 |
| Pelican Modpack Manager [S8] | plugin | `.modpack-manager.json` (pack + versión) | del pack | — | actualizar packs; recupera la versión tras reinstalar | nivel pack | MIT |
| PufferPanel [S9] | panel | plantillas CurseForge y FTB | ninguna | server pack | re-instalar | nada | sí |
| Crafty Controller [S10] | panel | sin modpacks nativos; import de CurseForge en borrador (2026-05) | — | — | gestor de backups | — | sí |
| MCSManager [S11] | panel | sin soporte de mods (issue abierto) | — | — | — | — | Apache-2.0 |
| AMP 2.7 "Deimos" [S12] | panel comercial | "Store": Modrinth, Hangar, SpiGet; modpacks de Modrinth desde 2.7.2 | **resuelve**, respetando loader y versión; corrigió "quitar un mod que comparte una dependencia" | sí | desactivar/quitar/actualizar | la Store | no |
| Modrinth Hosting [S13] | hosting, 5–20 USD/mes | servidor "vinculado" a un modpack; **contenido del pack separado del agregado por el usuario** | "varias capas de profundidad" | lista mantenida de mods problemáticos | vista previa del changelog; reinstalar/reparar; **sin backups automáticos** (piden hacerlo a mano antes) | su base | app GPL, backend cerrado |
| exaroton [S14] | hosting | pack + carpeta `overrides/` que sobrevive a actualizaciones | manual | muestra a los jugadores qué versión y launcher usar | packs se actualizan solos | plataforma | no |
| Aternos [S15] | hosting gratuito | solo biblioteca curada | manual | packs con archivos de servidor | auto-actualiza | plataforma | no |
| BisectHosting [S16] | hosting | menú con 2300+ packs | del pack | server packs | parar, luego actualizar; **pregunta qué hacer con los archivos existentes** | pack instalado | no |
| packwiz [S17,S18] | CLI de autoría, amigable con git | `pack.toml` + `index.toml` + un `.pw.toml` por mod (url, hash, `side`, `update.modrinth`) | agrega las requeridas al hacer `add`; no mira `incompatible` (src) | `side` | `packwiz update --all`; packwiz-installer sincroniza por hash y guarda `packwiz.json` | el lock son los `.pw.toml` | MIT |
| ferium [S19] | CLI | perfil JSON (mods + filtros, sin versiones) | resuelve en cada `upgrade` | — | siempre la última compatible; lo que no bajó va a `.old/` | la carpeta | MPL-2.0 |
| mrpack-install [S20] | CLI de servidor | `packstate.json` (sha512 por archivo) (src) | del pack | `env.server` | `update` compara hashes; respalda los archivos modificados localmente; **rechaza cambiar MC/loader en el lugar** (src) | packstate | MIT |
| itzg/docker-minecraft-server [S21] | imagen Docker | `.modrinth-manifest.json` etc.: lista de lo que escribió (src) | `none\|required\|optional`; ignora `incompatible` (src) | listas de exclusión/inclusión forzada | estado deseado en cada arranque; **manifiesto anterior − nuevo = borrar**; sin rollback | manifiesto | MIT |
| Modrinth App [S22] | launcher | perfiles; importa/exporta `.mrpack` | al instalar | `env` | lock de pack | **por hash** (`/version_files`) (src) | GPL-3.0 |
| Prism Launcher [S23] | launcher | `mods/.index/<slug>.pw.toml` (formato packwiz) (src) | resuelve al instalar y al actualizar (8.0+) | `side` | verificador de actualizaciones | `.index` | GPL-3.0 |
| Server packs de CurseForge [S24] | convención | el autor sube el server pack aparte | del autor | artefacto separado | manual | — | no |
| FTB Server Installer [S25] | CLI de servidor | `.manifest.json` (src) | del pack | omite `clientonly` (src) | **diff de manifiestos**: iguales se quedan, cambiados se bajan, quitados se borran; avisa si el pack instalado es otro | manifiesto | fuente pública |
| ATLauncher [S26] | launcher | formato propio | — | — | servidor SIN VERIFICAR | — | GPL-3.0 |
| **Technic Solder** [S27] | **API auto-alojada** | modpack → *builds* (cada uno con versión de MC) → versiones de mod (md5, tamaño); punteros `recommended` y `latest` | **ninguna** (src) | ninguno | **volver atrás = mover `recommended`**; el launcher baja solo lo cambiado | caché del launcher | MIT, activo |

## Qué tomar y qué evitar

- **Eggs de Pterodactyl:** tomar la preferencia por el server pack. Evitar tratar "reinstalar" como
  "actualizar" y descartar en silencio los mods `env.server == optional`.
- **Add-ons de pago:** prueban que el problema vale dinero. Evitar depender de modificar Wings:
  quedarse en la Client API.
- **Plugins de Pelican:** tomar el archivo de metadata por servidor dentro de `mods/` y el
  re-hasheo solo de lo que cambió de tamaño o fecha. Evitar no resolver dependencias.
- **AMP:** tomar el **conteo de referencias** de las dependencias compartidas antes de quitar una.
- **Modrinth Hosting:** tomar la separación **contenido del pack / contenido del admin** (es el
  requisito "mods añadidos por un admin"), la vista previa del changelog y las acciones de
  reparar. Evitar actualizar sin backup automático: es su hueco y se puede superar.
- **exaroton:** tomar la capa `overrides/` que sobrevive y decirles a los jugadores la versión
  exacta del pack.
- **BisectHosting:** tomar "parar y después actualizar" y preguntar explícitamente qué hacer con
  los archivos existentes.
- **packwiz:** tomar la forma del lock (lado, hash y fuente de actualización por mod). Exportar a
  packwiz o `.mrpack` en vez de inventar un formato.
- **ferium:** tomar la cuarentena de archivos desconocidos (`.old/`) en vez de borrarlos. Evitar
  resolver "la última" en cada corrida: no es reproducible.
- **mrpack-install:** tomar los hashes en el estado, el respaldo de archivos modificados a mano y el
  rechazo de cambiar MC/loader en el lugar.
- **itzg:** tomar "manifiesto anterior − nuevo = borrar" y las listas de exclusión para mods de
  cliente mal etiquetados. Evitar los borrados por glob (`REMOVE_OLD_MODS`) e ignorar `incompatible`.
- **FTB installer:** tomar el diff de tres vías (igual / cambiado / quitado) y la guarda "hay otro
  pack instalado".
- **Technic Solder:** tomar **builds inmutables numerados con punteros**: volver atrás es mover un
  puntero.

## Las cinco lecciones

1. **Registrar explícitamente lo instalado**, en el servidor y en Mongo. Todas las herramientas que
   actualizan con seguridad escriben un manifiesto con proyecto, versión, ruta y hash. Como
   Pterodactyl no da hashes, el servicio escribe su manifiesto por servidor y solo reconcilia por
   hash (descargando) los jars que no reconoce. → i-7985ee-5c7506, i-7985ee-43df2f.
2. **Modelar el estado deseado como builds inmutables y aplicar por diff.** Un build es un conjunto
   congelado de (proyecto, versión, archivo, sha512, lado). Se calcula agregar/actualizar/quitar
   contra el manifiesto del servidor; solo se borra lo propio y lo desconocido va a cuarentena.
   → i-7985ee-098611.
3. **Actualizar de forma segura y reversible**: parar, backup, aplicar, arrancar, verificar; volver
   atrás moviendo el puntero al build anterior o restaurando el backup. Rechazar cambiar MC/loader
   en el lugar. → i-7985ee-495708.
4. **Resolver al armar el pack y congelar el resultado.** Requeridas transitivas, respetar
   `incompatible` (que packwiz, itzg, Prism y la Modrinth App ignoran), contar referencias de las
   dependencias compartidas, aplicar los reemplazos como mapa de sustitución. "La última" pertenece
   al armado, nunca al despliegue. → i-7985ee-7f735a.
5. **No confiar solo en la metadata de lado.** Usar `environment` por versión más listas del admin
   (excluir / forzar), y publicar el pack de cliente desde el mismo build para que los jugadores
   coincidan con el servidor. → i-7985ee-7352b3.

## Fuentes

- S1 https://github.com/pterodactyl/game-eggs (`minecraft/java/modrinth/egg-modrinth-generic.json`, `curseforge/egg-curse-forge-generic.json`)
- S2 https://github.com/pelican-eggs/minecraft/tree/main/java
- S3 https://www.sourcexchange.net/products/minecraft-modpack-installer-for-blueprint
- S4 https://www.sourcexchange.net/products/minecraft-mod-manager-for-pterodactyl
- S5 https://pelican.dev/docs/panel/advanced/plugins
- S6 https://hub.pelican.dev/plugins/minecraft-modrinth · https://github.com/pelican-dev/plugins
- S7 https://github.com/gOOvER/pelican-mod-manager
- S8 https://hub.pelican.dev/plugins/modpack-manager
- S9 https://github.com/pufferpanel/templates
- S10 https://docs.craftycontrol.com/pages/user-guide/faq/ · https://gitlab.com/crafty-controller/crafty-4/-/merge_requests/1012
- S11 https://github.com/MCSManager/MCSManager · https://github.com/MCSManager/MCSManager/issues/1603
- S12 https://discourse.cubecoders.com/t/amp-deimos-2-7-0-release-notes/38578 · https://discourse.cubecoders.com/t/amp-deimos-2-7-2-release-notes/39817
- S13 https://modrinth.com/hosting · https://modrinth.com/news/article/content-management-overhaul/ · https://support.modrinth.com/en/articles/14030930-managing-backups
- S14 exaroton, artículos de soporte: Install modpacks, Install mods, Modpack overrides, Mismatched Mods (https://support.exaroton.com/hc/en-us/)
- S15 Aternos, artículos de soporte: Install mods, Install modpacks, Suggesting new mods (https://support.aternos.org/hc/en-us/)
- S16 https://help.bisecthosting.com/hc/en-us/articles/40490920836251-How-to-Install-and-Update-a-Modpack-on-a-Minecraft-Server
- S17 https://packwiz.infra.link/ · https://github.com/packwiz/packwiz
- S18 https://packwiz.infra.link/tutorials/installing/packwiz-installer/ · https://github.com/packwiz/packwiz-installer
- S19 https://github.com/gorilla-devs/ferium
- S20 https://github.com/nothub/mrpack-install
- S21 https://docker-minecraft-server.readthedocs.io/en/latest/mods-and-plugins/ · https://github.com/itzg/mc-image-helper
- S22 https://github.com/modrinth/code · https://docs.modrinth.com/api/operations/getlatestversionsfromhashes/
- S23 https://prismlauncher.org/news/release-8.0/ · https://github.com/PrismLauncher/PrismLauncher
- S24 https://support.curseforge.com/support/solutions/articles/9000197242-file-project-types-and-additional-fields
- S25 https://github.com/FTBTeam/FTB-Server-Installer
- S26 https://atlauncher.com/ · https://github.com/ATLauncher/ATLauncher
- S27 https://github.com/TechnicPack/TechnicSolder
- S28 https://github.com/pterodactyl/panel/blob/1.0-develop/routes/api-client.php

SIN VERIFICAR: las páginas de add-ons de BuiltByBit respondieron 403; Shockbyte y Apex no se
revisaron.
