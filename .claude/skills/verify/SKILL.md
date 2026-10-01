---
name: verify
description: Corre el gate de modpack-creator y reporta con honestidad qué pasó. Úsalo antes de
  desplegar al servidor, después de tocar src/library/api/, src/dependences.py, src/model/ o
  docker/, y cuando pidan "verifica", "corre el gate", "revisa que funcione" o "¿está listo?".
allowed-tools: Bash, Read
---

# Verify

Este repositorio no puede arrancar la app completa sin MongoDB, el proveedor OIDC y el panel de
Pterodactyl; el gate comprueba todo lo que sí se puede comprobar sin ellos. Lo que no cubre se
dice, no se da por verificado.

## El gate

```bash
./scripts/check.sh        # = hatch run gate: bundle, audit, typecheck, tests (lo mismo que CI)
```

## Si cambió algo de esto

- **`src/library/api/client/*`** (llamadas a Modrinth o Pterodactyl): el gate no habla con las APIs
  reales. Contrasta la ruta, el método y la forma del cuerpo con `docs/references.md` y dilo en el
  reporte.
- **`src/model/*`** (un `Document`, un índice): `scripts/audit.py` revisa el registro en
  `init_beanie`; un índice nuevo o cambiado afecta los datos que ya están en producción, y eso el
  gate no lo ve.
- **`docker/*` o `requirements.txt`**: el gate no construye la imagen. Dilo.
- **`.env.default` o un `getenv` nuevo**: `audit` exige que toda variable obligatoria esté en la
  plantilla.

## Reportar

Di qué pasó y qué no, con la salida. Nunca llames verificado algo que no corrió. Los AVISOS del
audit (reintentos sobre POST) son conocidos y están en `docs/roadmap.md`: nómbralos como
conocidos, no como nuevos.
