# User walk: el panel 0.3.2 (tandas A y B)

**Fecha:** 2026-10-13 · **Pedido:** el dueño, tras probar la demo local («haz un user-walk más y
cierra todos los cambios») · **Qué es:** hipótesis de un agente que camina el panel leyendo su
código; no son datos de usuarios reales. La comprobación real más barata: el dueño usando la demo
(`scripts/demo.sh`) y la 0.3.2 desplegada.

## 1. Actores

- **Operador** (del grupo `OIDC_OPERATOR_GROUPS`): da de alta servidores, vincula, simula, actualiza.
- **Editor** (cualquier otro autorizado): arma packs y builds, decide mods; no vincula, ni simula, ni
  actualiza (lo hacen cumplir los servicios, d-7985ee-edef1e).
- **Quien abusa:** un editor que intenta acciones de operador; un enlace viejo o manipulado. Las dos
  cosas ya las paran los servicios y el chequeo de origen.

## 2. Caminos ideales

**Alta (operador):** 1) abre el servidor nuevo: la lista «Para terminar el alta (1 de 5)», escaneado;
2) «Crear o vincular» lo lleva a #modpack: crea el pack desde lo escaneado (versión y loader
sugeridos, F09); 3) «Resolver» lo lleva al pack; resuelve; 4) «Decidir» abre la pestaña Por decidir;
decide en las filas; 5) «Crear build»; 6) «Simular» lleva a #actualizar; la simulación termina y la
lista se pliega en «Alta completa».

**Día a día:** el tablero abre con «Necesita atención»; cada tarjeta lleva a su acción; en la lista
de mods filtra, cambia el lado, el canal o la fijación desde la fila (Deshacer si se equivocó); en
auditoría y trabajos abre las tarjetas para ver el detalle.

## 3. Lo que sale mal, listado antes de tratarlo

- **Demoras:** resolver o actualizar el catálogo tarda (es un trabajo con su barra); Modrinth lento al
  pedir las versiones (tope de 3 s, ya); una simulación larga (la página se recarga al terminar).
- **Interrupciones:** sesión vencida a mitad (htmx sigue el 401 al login); pestaña abierta durante el
  deploy (ya: 286 para la ruta vieja); dos pestañas del mismo pack.
- **Tropiezos:** **K1** en la página del pack, el paso «Resolver» apunta a la misma página, arriba, sin
  llegar al botón; doble cambio rápido de un select (dos pedidos, dos avisos de Deshacer); volver días
  después (la lista refleja el estado de ese momento); un filtro que deja la lista vacía (ya dice
  «Ningún mod coincide»).
- **Fallas:** la lectura de un hecho del alta falla (ya: el paso queda sin hacer, nunca un 500);
  Modrinth caído al crear un pack (ya: campo de texto); un pack renombrado reparte sus eventos de
  auditoría en dos nombres.
- **Abuso / rol:** **K2** a un editor, los pasos «Crear o vincular» y «Simular» (y las tarjetas
  «Simular» y «Vincular» del tablero) le ofrecen un botón que termina en un control deshabilitado.
- **Premortem** («lo dejó a la mitad»): siguió un botón del alta y no encontró la acción en la página
  a la que llegó: **K3** la tarjeta «Escanear» del tablero lleva al servidor sin ancla, con el botón
  de escanear abajo, lejos; K1; K2.

## 4. Fusión, precio y decisión

| Caso | Probabilidad | Costo sin tratar | Dónde termina | Decisión |
|---|---|---|---|---|
| K1 «Resolver» del pack sin ancla | alta (todo pack nuevo) | confusión, un clic perdido | vuelve al flujo: `#mods`, donde está «Resolver» | se trata |
| K2 acciones de operador ofrecidas a un editor | media | confusión, cree que el panel falla | falla visible: «lo hace un operador», sin botón; la tarjeta del tablero dice «Ver» | se trata |
| K3 «Escanear» sin ancla | media | un desplazamiento buscando el botón | vuelve al flujo: `#escaneo` | se trata |
| Doble cambio rápido de un select | baja | dos avisos de Deshacer; el último gana | — | no se trata: el resultado guardado es el último elegido, como espera quien elige dos veces |
| Pack renombrado en auditoría | baja | dos opciones en el filtro | — | no se trata: d-7985ee-7199ab eligió guardar el nombre de entonces |
| Dos pestañas del mismo pack | baja | una pestaña muestra un estado viejo hasta recargar | — | no se trata: cada acción responde con el estado vigente |

## 5. Tests (antes del código)

- K1: `tests/test_onboarding.py::test_resolving_points_to_the_mods_where_the_resolve_button_is`.
- K2: `tests/test_onboarding.py::test_an_editor_is_told_an_operator_does_the_operator_steps`,
  `tests/test_attention.py::test_an_editor_sees_operator_cards_without_an_action_of_theirs`.
- K3: `tests/test_attention.py::test_scanning_lands_on_the_scan_section`.

## 6. Ampliación

No se lanzó un segundo caminante: el dueño pidió cerrar. Lo nuevo que encuentre la demo vuelve aquí.
