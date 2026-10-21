# Automatización que aplica solo lo seguro: qué dice la literatura (2026-10-21)

Para la propuesta al bundle sobre la regla del dueño d-7985ee-56a529 («una acción peligrosa se sugiere,
nunca es automática; solo una actualización segura ocurre sola»). Pregunta: ¿hay práctica o literatura
establecida para «un reconciliador aplica solo lo que puede mostrar como seguro (más nuevo, por la misma
vía, dentro de los canales, nunca bajo lo instalado), y propone el resto»?

**Método y límite.** Un agente investigador leyó las páginas con una herramienta que resume y cita con un
modelo pequeño: cada cita es **SUPUESTO** hasta contrastarla con la página viva. No se alcanzaron la
referencia de `matchUpdateTypes`/`automergeType` de Renovate, la opción `update-types` de Dependabot, el
comportamiento de npm y pip ante una baja, ni la automatización de imágenes de Flux.

## Lo encontrado

| Fuente | Qué dice | Frente a la regla |
|---|---|---|
| Renovate, *Automerge* (docs.renovatebot.com/key-concepts/automerge/) | automerge solo donde «elegirías merge igual»; los cambios no mayores en semver no deberían romper; excluye pre-1.0; por defecto espera a que pasen los checks | la apoya y la acota: lo seguro es semver más tests, no vía ni canal |
| Dependabot, *Automating Dependabot with GitHub Actions* (docs.github.com) | el ejemplo hace auto-merge solo si `update-type == 'version-update:semver-patch'`, y pide checks obligatorios | el mismo patrón: el bot propone, el merge automático es opcional y por tipo |
| apt, *apt_preferences(5)* (manpages.debian.org) | «nunca baja salvo que la prioridad supere 1000»; ninguna prioridad por defecto lo hace; bajar «puede ser riesgoso» | apoya «nunca bajo lo instalado» por defecto; bajar o cambiar de release es un pin explícito |
| Argo CD, *Automated Sync Policy* (argo-cd.readthedocs.io) | el sync automático no borra lo que ya no está en Git sin `prune: true`; self-heal apagado por defecto; no permite rollback con sync automático | la apoya con fuerza: quitar y volver atrás no son automáticos; pero es «apagado salvo que se habilite», no una cola de propuestas |
| Flux, *Kustomization* (fluxcd.io) | `.spec.prune` es obligatorio: hay que elegir | la acota: no hay un default seguro, se fuerza la elección |
| Google SRE, *Automation at Google* (sre.google) | automatizar dominios bien definidos; ante fallas repetidas, parar y avisar | apoyo general, no la regla |

## Conclusión

Nadie la formula como una sola regla. Sus partes tienen respaldo, sobre todo como defaults: aplicar solo
lo que se puede mostrar seguro (Renovate, Dependabot), nunca bajar sin un pin explícito (apt), y no quitar
ni volver atrás en automático (Argo CD). La clasificación «misma vía y mismo canal» no aparece en ninguna
fuente leída: es lo que agrega este repositorio.
