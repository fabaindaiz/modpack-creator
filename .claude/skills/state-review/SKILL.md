---
name: state-review
description: Revisión periódica del sistema de instrucciones de modpack-creator. Úsala cuando
  pidan "revisión de estado", "¿está al día la documentación?", "state review", antes de empezar
  un bloque grande de trabajo, o cuando el bundle de .agents/ tenga una versión nueva.
allowed-tools: Bash, Read, Grep, Glob
---

# State review

Sin un ritual, la documentación se pudre: los agentes borran código más rápido de lo que releen
prosa. Esta revisión es de solo lectura; sus hallazgos van al reporte y a `docs/roadmap.md`.

## Qué responder

1. ¿Existe cada documento del mapa de `AGENTS.md`, y sigue siendo verdadera cada afirmación?
   Abre los archivos que nombra; no lo asumas.
2. ¿Cada regla sigue teniendo su enforcer (`docs/decisions.md`, última columna)? ¿Alguno saltó en
   el período (git log de `scripts/audit.py`, `tests/`)?
3. ¿Qué cambió que debió ser una fila de decisiones y no lo es?
4. ¿Qué del roadmap ya está cerrado, por estar hecho o por una medición? ¿`LAYERING_BASELINE` y
   `UNREGISTERED_BASELINE` de `scripts/audit.py` se achicaron como dice el roadmap?
5. ¿`AGENTS.md` pasó de 200 líneas? ¿Qué sección creció?
6. ¿Qué reglas siguen sin enforcer (`—`) y se podrían subir de peldaño barato?
7. ¿Qué fricción quedó anotada más de una vez en `.claude/logs/agent-changelog.md` sin arreglarse?
   Ponle precio y compárala con el trabajo de producto.
8. ¿Qué aprendimos que el método no sabe? Si pasa la prueba de generalidad de
   `.agents/method/prompt-context.md` §*Improving the method*, es una propuesta
   (`.agents/method/prompt-harvest.md`).
9. ¿El bundle está atrasado? Compara `version` de `.agents/README.md` con el último release del
   repositorio home; si hay uno nuevo, `.agents/method/prompt-update.md` antes del próximo trabajo
   grande.

## Comandos

```bash
./scripts/check.sh
hatch run python .agents/tools/bundle.py changelog --since "$(sed -n 's/^version: "\(.*\)"/\1/p' .agents/README.md)"
```

## Reportar

Una lista corta por pregunta, con evidencia (archivo, línea, salida). Lo que no pudiste
comprobar, dilo.
