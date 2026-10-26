# User walk: O8 tanda 1, los builds

**Fecha:** 2026-10-26 · **Pedido:** el user-walk de la tanda 1 de O8 antes de su plan (d-7985ee-a99db7,
spec `docs/superpowers/specs/2026-10-25-o8-versiones-con-historia-design.md` §8: B1 «Construir como
prueba» y «Aprobar», B2 nombre, B3 estrella, B4 comparación explicada) · **Qué es:** hipótesis de dos
recorridos que leen el código (el del agente y uno en contexto limpio que no vio esta lista); no son datos
de usuarios reales. Nada se corrió.

**Severidad.** *Alta*: una instalación o un piso equivocados, o trabajo perdido. *Media*: un paso que se
pierde o confunde. *Baja*: fricción.

## Actores y metas

- **Editor** (arma packs y builds): probar una combinación sin comprometer el pack, ponerle nombre,
  destacar los builds que importan, entender qué cambió entre dos builds.
- **Operador** (el dueño; escribe en servidores): instalar la prueba en un servidor de prueba, decidir si
  pasa, no llevar a producción nada que no aprobó.
- **Mal uso**: un editor que recomienda o aprueba lo que no probó (permitido por D10: es de editor); un
  script por `/v1`. Supuesto: un operador y pocos editores.

## Camino ideal

1. El editor cambia el pack (fija Iris 1.7) → «Construir como prueba» → ve el trabajo y luego
   «Build 15 · prueba» en la lista, sin que «último» ni el piso cambien.
2. Le pone nombre «con Iris 1.7» (B2) y, si importa, una estrella (B3).
3. «Comparar con el anterior»: resumen, «En el pedido: fijado Iris 1.7 (a mano)», el porqué de cada mod (B4).
4. El operador, en el servidor de prueba, simula e instala el build 15, que el selector y el diálogo
   nombran como prueba.
5. a) Funcionó: «Aprobar» → build normal; si es el más nuevo, pasa a «último». b) Falló: el editor deshace
   el cambio del pack; la prueba queda marcada y nada quedó atrapado.

## Lo que sale mal

Lista completa antes de decidir; «C» = recorrido en contexto limpio, «A» = el del agente; los dos cuando ambos.

| # | Paso | Qué pasa | Evidencia | Prob. | Sev. | Fin |
|---|---|---|---|---|---|---|
| W1 | 4 | **Una prueba instalada en cualquier servidor vinculado sube el piso de todo el pack**, no solo el de ese servidor: el piso lee el último escaneo de cada servidor vinculado. Descartar la prueba no lo deshace; el próximo build normal lleva las versiones de la prueba a producción. (Corrige lo dicho al decidir B1: «el escaneo de ese servidor pone su piso».) | `services/modpack.py:1683-1695` (`_floor_sources`), d-7985ee-6dc34c | alta | Alta | **dueño, H1** |
| W2 | 5 | «Recomendar» funciona sobre una prueba (botón en cada fila y `/v1`): el recomendado es el piso, la instalación por defecto y el chequeo diario. | `_builds.html:36`, `services/modpack.py:2090`, `services/update.py:112` | alta | Alta | **dueño, H2** |
| W3 | 5b | Qué pasa con una prueba que falló: B1 solo define «Aprobar». ¿Queda marcada, se descarta (archiva) o se borra? Borrar rompe comparaciones, instalaciones registradas y la numeración. | §8 B1 | media | Media | **dueño, H3** |
| W4 | 1 | Discord anuncia «build N creado» también para una prueba, y su `.mrpack` se descarga como cualquiera. | `services/modpack.py:2036-2039` | alta | Media | **dueño, H4** |
| W5 | 1 | **Un reintento del trabajo mueve «último» a la prueba**: el camino del reintento llama a `bundle_latest_build` sin mirar nada, y la marca no viaja en el payload. | `services/modpack.py:2014-2019`, `:1956` | media | Alta | la marca va en el payload y en el build; nunca se mueve el puntero por una prueba, tampoco al reintentar |
| W6 | 1 | Una sola clave `build:{pack}` para los dos: «Construir como prueba» con un build normal en cola devuelve el normal, y al revés. | `services/modpack.py:1954-1962` | media | Alta | si el activo es del otro tipo, 409 «ya hay un build normal en curso» con su enlace |
| W7 | 1 | La marca se pierde en el diálogo de build bloqueado («Decidir y crear build») y en la ruta de crear desde actualizaciones. | `_builds.html:14`, `routers/web/modpacks.py:1013-1047`, `:1124-1139` | alta | Alta | campo oculto `prueba` por el diálogo y cada ruta de crear |
| W8 | 4 | El selector de simular y el de instalar dicen «(recomendado)/(último)», nunca «(prueba)»; el diálogo de instalar no avisa que es una prueba. La simulación preselecciona la más nueva (decidido) y ofrece instalarla. | `servers/_update.html:17-21,41-48` | media | Alta | «Build 15 · prueba · con Iris 1.7» en cada selector; el diálogo dice «Es un build de prueba» |
| W9 | 1 | Con solo pruebas: el servidor dice «todavía no tiene builds», el chequeo de actualizaciones lo rechaza (NO_BUILD), la guía no da por hecho «Build creado», «Instalar build…» queda sin preseleccionado. | `routers/web/servers.py:151`, `services/update_check.py:212,229`, `library/onboarding.py:138`, `_update.html:41` | media | Media | textos que nombran las pruebas; instalar preselecciona la más nueva si no hay otra |
| W10 | 4 | Un servidor que corre una prueba se compara contra el build vigente: cada mod de la prueba sale como «otra versión», y «traer los mods» podría meter al pack los de la prueba. | `services/pterodactyl.py:456,476`, `routers/web/servers.py:168` | media | Media | comparar contra el build instalado cuando se sabe cuál es |
| W11 | 5a | Aprobar una prueba vieja con un build normal más nuevo: «último» se queda en el nuevo (`$max`); quien aprueba puede esperar que la prueba quede vigente. | `database/modpack.py:321-324` | media | Baja | el aviso dice cuál queda vigente y ofrece recomendar |
| W12 | 5a | Aprobar días después: el build es el de entonces (congelado), y puede haber otros después. | — | baja | Baja | el aviso dice su edad y si hay builds posteriores |
| W13 | 2 | Nombre o estrella guardados con `save()` del documento entero: una copia vieja reescribiría `lock` y `request`. Dos pestañas renombrando: gana la última. | — | media | Alta | `$set` atómico de solo esos campos; la respuesta redibuja `#builds` |
| W14 | 2 | Estrella como alternar: un doble clic o dos pestañas la deshacen. | — | media | Baja | el formulario manda el estado («destacar» / «quitar») |
| W15 | 2 | Nombre vacío, de espacios, de más de 60 caracteres, con HTML. | — | baja | Baja | se recorta, vacío es sin nombre, tope 60, Jinja escapa |
| W16 | 2 | Plegar «N builds anteriores» esconde el recomendado, el último, uno instalado en un servidor o una prueba sin aprobar. | — | media | Media | esos nunca se pliegan; el filtro «Solo destacados» no se recuerda entre visitas |
| W17 | 3 | La comparación marca «fijado» y «baja, por un pin» con los pins **actuales** del pack, no con los del build. (Ya existe hoy.) | `services/modpack.py:2166-2174` | alta | Media | se usan los pins del pedido de cada build |
| W18 | 3 | «En el pedido» mostraría cambios que nadie hizo: los pisos cambian con cada escaneo, y un build guardado antes de un campo lo compara contra su valor por defecto. | `library/resolution.py:212-236` | alta | Media | sin pisos ni `free`/`unchecked`; un campo ausente en un lado no se compara |
| W19 | 3 | La comparación ya pide changelogs a Modrinth al abrir (existe hoy, con plazo). B4 no agrega requests; «con el anterior» a un clic la hará más frecuente. | `services/modpack.py:2140-2160` | media | Baja | se deja como está (no es nuevo); si molesta, D12 se aplica también aquí |
| W20 | 3 | «Comparar con el anterior» en el build 1 o después de un hueco de numeración. | — | baja | Baja | el anterior es el build existente previo; en el primero no hay botón |
| W21 | 1 | El trabajo de la prueba tarda (resuelve con Modrinth); la persona cierra la pestaña. | — | media | Baja | el trabajo sigue (ya existe) y la fila aparece al volver; «Mis trabajos» dice «Build de prueba» |
| W22 | 5 | Desde `/v1`: crear como prueba, aprobar, nombrar y destacar con los mismos roles (D13). | — | baja | Baja | mismos métodos del servicio; tests de contrato |

## Premortem

Salió y el dueño perdió algo. Por qué:

1. **Producción recibió la prueba.** Se probó en el servidor de prueba, falló, se deshizo el cambio; el
   próximo build normal no pudo bajar porque el escaneo de la prueba era piso (W1). O alguien pulsó
   «Recomendar» en la prueba (W2), o siguió la simulación hasta instalarla sin ver que era prueba (W8).
2. **La prueba quedó como «último» sin que nadie lo pidiera**: un reintento (W5), un build normal en cola
   (W6) o el diálogo de bloqueado (W7). El piso subió sin aviso.
3. **Un renombre borró un build**: guardar el documento entero desde una copia vieja (W13).

## Decidido por el dueño (2026-10-26, d-7985ee-3bb53b)

- **H1** (W1): el escaneo de un servidor que corre una prueba sin aprobar **no** es piso hasta que se
  apruebe. Un build normal instalado después en ese servidor puede bajarle versiones, marcadas antes de
  confirmar.
- **H2** (W2): en una prueba, «Aprobar y recomendar»; nunca una prueba recomendada.
- **H3** (W3): una prueba que falló queda marcada y nunca se borra. Ajusta W16: una prueba sin aprobar no se
  pliega mientras sea el build más nuevo o esté instalada; después, sí.
- **H4** (W4): Discord avisa «🧪 build de prueba N» y «📦 build N aprobado».

Lo demás (W5–W22) lo decide el agente como dice «Fin», y cada caso se vuelve un test que falla primero en
el plan de la tanda 1.

## Lo que este walk no cubre

El aspecto (barrido visual a 360, 768 y 1280 px cuando exista), un Pterodactyl real instalando una prueba,
y si el dueño de verdad usa un servidor aparte para probar (SUPUESTO: sí; no se comprobó). La comprobación real más barata: el dueño en `scripts/demo.sh` con un pack de dos builds.
