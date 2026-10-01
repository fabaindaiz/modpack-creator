# Plan: hacia dónde seguir

**Fecha:** 2026-10-01. Se basa en la idea original del repositorio (`docs/requirements.md`), en el
estado del código (`.agents/evaluation-2026-10-01-38f8aa.md`) y en cuatro investigaciones de
fuentes primarias:

| Informe | Responde |
|---|---|
| [`research/2026-10-plataformas-similares.md`](research/2026-10-plataformas-similares.md) | qué hacen 23 plataformas y herramientas parecidas, y qué tomar de cada una |
| [`research/2026-10-resolucion-y-formatos.md`](research/2026-10-resolucion-y-formatos.md) | cómo resolver dependencias con la API de Modrinth, elegir loader y exportar |
| [`research/2026-10-operacion-pterodactyl.md`](research/2026-10-operacion-pterodactyl.md) | cómo actualizar un servidor con backup y rollback usando solo la Client API |
| [`research/2026-10-arquitectura.md`](research/2026-10-arquitectura.md) | trabajos durables, tests, autorización, avisos, UI y observabilidad |

El cómo, sesión por sesión, está en [`plan-de-trabajo.md`](plan-de-trabajo.md). Este documento es la visión y el orden. El detalle de cada paso, con lo que choca y lo que hay que
decidir, está en [`roadmap.md`](roadmap.md) por su id. **Nada de esto es un compromiso**: es el
orden que se recomienda hoy, y lo que se aprenda en cada fase puede cambiarlo.

## La idea original, y lo que el mercado dice de ella

`docs/requirements.md` pide cinco cosas: obtener la última versión de un mod, listar sus
dependencias, declarar reemplazos de dependencias, declarar mods añadidos por un admin y gestionar
las versiones de los mods. A eso el código agregó operar servidores de un panel Pterodactyl propio.

**Nadie hace exactamente esto.**
- Los paneles abiertos (PufferPanel, Crafty, MCSManager) casi no gestionan mods.
- Los eggs oficiales de Pterodactyl solo instalan un pack al reinstalar, sin diff ni backup.
- Los add-ons que lo hacen son de pago y piden modificar Wings.
- Los hostings lo resuelven dentro de su plataforma cerrada. Ni Modrinth Hosting hace backup
  automático antes de actualizar.
- Ningún resolvedor abierto (Modrinth App, packwiz, Prism, ferium) mira las dependencias
  `incompatible`, y ninguno tiene reemplazos declarados salvo un caso fijo (Fabric API → QFAPI en
  Quilt).

El análogo más cercano es **Technic Solder**: una API propia con builds numerados y punteros
`recommended`/`latest`. Le faltan justo las dependencias.

**El lugar de este proyecto:** un *Solder con dependencias*, para un dueño que opera sus propios
servidores en Pterodactyl. Arma packs reproducibles (resolviendo y congelando), sabe qué tiene cada
servidor y lo actualiza con red de seguridad.

## El modelo objetivo

```text
            ┌──────────── autoría (la API) ────────────┐        ┌──── servidores (Pterodactyl) ────┐
  Pack ──▶  Reglas ──▶  Resolución ──▶  Build N (inmutable) ──▶  Despliegue ──▶  Servidor
  (MC, loader,   (reemplazos,   (lock: proyecto,   ▲ punteros:       (trabajo con      │
   proyectos)     pins, canales,  versión, archivo,  │ recomendado,     backup, swap,     │
                  excluir)        sha512, lado,      │ último           arranque,         │
                                  razón)             │                  rollback)         ▼
                                                     └──── diff ◀──── Manifiesto observado
                                                                      (lo que hay en mods/)
```

- **Pack:** versión de juego, loader, proyectos pedidos y **contenido del admin** separado del
  contenido del pack (i-7985ee-063a3a).
- **Reglas:** reemplazos de dependencias, pins de versión, canales permitidos y listas de
  excluir/forzar. Son el requisito "declarar reemplazos" generalizado.
- **Build:** el resultado congelado de resolver, con número y la razón de cada entrada. Nunca se
  edita: se crea otro. Volver atrás es mover un puntero (i-7985ee-098611).
- **Manifiesto observado:** lo que de verdad hay en cada servidor, identificado por hash
  (i-7985ee-5c7506, i-7985ee-43df2f).
- **Despliegue:** un trabajo durable que lleva un servidor de su manifiesto a un build, con backup y
  rollback (i-7985ee-495708).

## Fases

Cada fase deja algo usable y es requisito de la siguiente. Los costos son estimaciones.

### Fase 0: que lo que existe sea seguro y arranque (≈ 2–4 días)

Sin esto, todo lo demás se construye sobre una imagen que no arranca, datos que se pierden y un
login abierto.

| Orden | Ítem | Por qué ahora |
|---|---|---|
| 0 | i-7985ee-bb2fde · fijar las versiones de las dependencias | hoy CI y la imagen instalan lo más nuevo cada vez |
| 1 | i-7985ee-e21d5d · los datos de MongoDB sobreviven y sus puertos no se publican | hoy recrear el contenedor **borra la base** y Mongo queda expuesto al host |
| 2 | i-7985ee-a77cdc · la imagen arranca | hoy no copia `main.py` ni recibe configuración |
| 3 | i-7985ee-c64e06 · quién puede iniciar sesión | cualquier cuenta del IdP opera los servidores |
| 4 | i-7985ee-63226e · usuario de servicio dedicado en Pterodactyl | la clave de un admin abre todo el panel |
| 5 | i-7985ee-ade111 · tests contra MongoDB real | requisito de 6–7 y de toda la fase 3 |
| 6 | i-7985ee-055362 · los updates escriben | hoy no actualizan nada |
| 7 | i-7985ee-be55dd · muchas versiones por proyecto | hoy solo cabe una |
| 8 | i-7985ee-fb00ca, i-7985ee-05c4a0 · contratos y reintentos | antes de llamar a esos endpoints de verdad |
| 9 | i-7985ee-90f944 · salud y logs | para ver las fallas de las fases siguientes |

### Fase 1: el catálogo y la resolución (requisitos 1–3, ≈ 1–2 semanas)

| Ítem | Qué entrega |
|---|---|
| i-7985ee-f4ba63 · el modelo nuevo de Modrinth | `environment`, versiones por año (`26.x`), dependencias por versión, sin `/project/{id}/dependencies` |
| i-7985ee-7f735a · resolver un modpack | el algoritmo de `research/2026-10-resolucion-y-formatos.md` §5: lock con dependencias, reemplazos, pins, incompatibles y razones |
| i-7985ee-063a3a · contenido del pack vs del admin | requisito 4 |
| i-7985ee-098611 · builds inmutables con punteros | requisito 5: "gestionar versiones" como builds numerados |

Al final de esta fase la API ya sirve sin tocar ningún servidor: arma packs correctos y explica
cada decisión.

### Fase 2: saber qué tiene cada servidor (≈ 1 semana)

| Ítem | Qué entrega |
|---|---|
| i-7985ee-5c7506 · manifiesto por servidor | un archivo de estado en el servidor más su copia en Mongo |
| i-7985ee-43df2f · identificar lo instalado (opción 1) | listar `mods/`, re-hashear solo lo que cambió, `POST /version_files` |
| i-7985ee-4364a3 · versiones de servidores | cerrar el `wip pterodactyl version` sobre lo anterior |

Al final, la API importa un servidor existente y dice: "este servidor está en el build N, salvo
estos 3 jars cambiados a mano".

### Fase 3: operar (≈ 2 semanas)

| Ítem | Qué entrega |
|---|---|
| i-7985ee-0ec295 · trabajos durables | colección `jobs` con leases, worker y scheduler en el proceso, sin Redis |
| i-7985ee-495708 · actualizar con backup y rollback | el procedimiento de `research/2026-10-operacion-pterodactyl.md` |
| i-7985ee-34933b · avisos por Discord | "hay actualizaciones", "servidor X actualizado", "rollback en Y" |

### Fase 4: distribuir y mirar (a demanda)

| Ítem | Qué entrega |
|---|---|
| i-7985ee-7352b3 · exportar el pack | `.mrpack` para jugadores desde el mismo build; packwiz si se elige la sincronización (opción 2 de i-7985ee-43df2f). Posiblemente es i-7985ee-1ba498 ("generators") |
| i-7985ee-50d411 · panel del dueño | Jinja2 + HTMX sobre los trabajos y los builds |

## Ideas para más adelante

Sin id todavía. Entran al roadmap cuando alguien las quiera construir.

- **Vista previa de una actualización**: el diff de builds con los changelogs de cada versión que
  cambia, antes de aplicar (Modrinth Hosting).
- **Revisión nocturna**: buscar actualizaciones de todos los builds con `POST /version_files/update`
  y mandar un resumen por Discord, sin aplicar nada.
- **Detección de cambios manuales** con el log de actividad de Pterodactyl (`server:file.*`,
  `server:sftp.*`) entre dos escaneos.
- **Plugins de Paper/Velocity** desde Hangar o Modrinth, con el mismo modelo de build.
- **Página para jugadores** por servidor: qué versión del pack usar, con el `.mrpack` para
  descargar (exaroton).
- **Plugin de Pelican**, si el panel migra: la misma API, pero vista dentro del panel.
- **CurseForge**: necesita una API key aprobada y respetar `allowModDistribution`; solo si aparece
  un mod imprescindible que no está en Modrinth.
- **Sincronización con packwiz** en el arranque del servidor (opción 2): menos tráfico y estado
  verificado por el propio servidor; pide editar el egg.

## Decisiones que son del dueño

Ordenadas por cuándo bloquean.

1. **Fase 0:** ¿la configuración llega al contenedor montando `.env.yaml` o con `env_file`?
   (i-7985ee-a77cdc)
2. **Fase 0:** ¿quién puede iniciar sesión: una lista de emails, un grupo del IdP o las dos?
   (i-7985ee-c64e06)
3. **Fase 0:** ¿producción ya tiene datos en Mongo? Decide si hay migración en i-7985ee-be55dd y si
   la fase 0.1 es urgente.
4. **Fase 2:** ¿el nodo de Pterodactyl alcanza la API? Decide si la opción 2 (packwiz) es posible.
5. **Fase 3:** ¿se acepta parar el servidor para actualizar, y en qué horario? Decide la
   programación de los trabajos.
6. **Fase 4:** ¿los jugadores bajan el pack desde aquí? Decide si `.mrpack` es un producto o un
   extra.
