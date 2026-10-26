"""The panel's help (O3, owner 2026-10-21): what the «?» next to a section's title opens, in place.

Each entry is the text and the anchor of the «Cómo funciona» page (`templates/help.html`) that says
more. A template asks for one by its key (`ui.section(..., help="mods")`): a key that is not here
fails the render, so a typo shows in the tests. The owner reviews this copy.
"""

HELP: dict[str, tuple[str, str]] = {
    # Modpacks.
    "modpacks": ("Cada modpack es una lista de mods para una versión de Minecraft y un loader. Ábrelo para "
                 "agregar mods, resolverlo y crear builds.", "modpack"),
    "nuevo-modpack": ("Elige la versión de Minecraft y el loader. Los mods se agregan después, uno a uno o "
                      "importando un pack.", "modpack"),
    "mods": ("Los mods del pack y las dependencias que trajo la última resolución. Agrega mods buscándolos o "
             "por su slug: el pack se resuelve solo y elige la versión de cada uno. En cada fila decides si el "
             "mod va en el servidor, si acepta beta o alpha y si queda fijado a una versión.", "resolver"),
    "builds": ("Un build es una resolución congelada y numerada: lo que se instala en un servidor. Créalo "
               "cuando la resolución no tenga problemas ni mods por decidir. El recomendado es el que se ofrece "
               "al actualizar.", "build"),
    "servidores-vinculados": ("Los servidores que usan este modpack: al actualizar uno se instala un build de "
                              "este pack. Se vinculan desde la página de cada servidor.", "vincular"),
    "contenido": ("Reemplaza los mods que vinieron de un pack con los de otro archivo; lo que agregaste a mano se "
                  "queda. Cada mod toma la versión que trae el pack.", "modpack"),
    "canales": ("Qué tan estables pueden ser las versiones que elige la resolución: release, beta o alpha. Un "
                "mod puede aceptar beta o alpha por su cuenta desde su fila.", "resolver"),
    "puentes": ("Un puente, como Sinytra Connector, deja usar mods de otro loader. Actívalo solo si lo "
                "necesitas: no todos los mods funcionan a través de él.", "puente"),
    "archivos-locales": ("Archivos del servidor que no están en Modrinth. Se quedan en el servidor al "
                         "actualizar y nunca se exportan.", "escanear"),
    "origenes": ("Una versión fijada guarda de dónde salió: la del pack importado, la elegida a mano, la que "
                 "tiene instalada cada servidor vinculado o la que elegiría la resolución. La marcada «en uso» es "
                 "la que se instala. «Resolución» deja el mod sin fijar: se actualiza solo y nunca baja de versión.",
                 "origenes"),
    # Updates of a modpack's mods.
    "revision": ("Busca en Modrinth versiones nuevas de los mods del build. Solo mira: no cambia el pack.",
                 "resolver"),
    "con-version-nueva": ("Mods con una versión más nueva para este pack. Para usarla, resuelve y crea un build "
                          "nuevo.", "resolver"),
    "fijadas": ("Mods fijados a una versión: hay una más nueva, pero el pack sigue con la fijada hasta que la "
                "cambies en su tarjeta.", "fijar"),
    # Servers.
    "servidores": ("Los servidores registrados aquí. Ábrelo para vincularlo a un modpack, escanearlo o "
                   "actualizarlo.", "vincular"),
    "panel-pterodactyl": ("Los servidores de tu panel de Pterodactyl que todavía no están registrados aquí. "
                          "Registrarlo no cambia nada en el servidor.", "vincular"),
    "servidor-modpack": ("El modpack que usa este servidor. Vincularlo no instala nada: dice con qué pack "
                         "compararlo y qué builds ofrecer al instalar.", "vincular"),
    "actualizar": ("Instala un build en el servidor. «Simular instalación» muestra qué cambiaría sin tocar "
                   "nada; instalar de verdad apaga el servidor, hace un respaldo y cambia los mods. Si algo "
                   "sale mal, el rollback vuelve atrás.", "actualizar"),
    "ultimo-escaneo": ("Escanear lee los mods instalados en el servidor y los identifica en Modrinth. Solo lee: "
                       "no cambia nada.", "escanear"),
    "comparacion": ("Lo que encontró el último escaneo, comparado con el build: qué sobra, qué falta y qué "
                    "versión cambia.", "escanear"),
    "importar-servidor": ("Arma el modpack con lo que el servidor tiene instalado: lo escanea y agrega cada mod "
                          "que identifica. Lo que no está en Modrinth queda como archivo local.", "escanear"),
}
