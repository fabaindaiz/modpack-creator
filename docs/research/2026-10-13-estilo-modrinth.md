# El estilo de Modrinth como guía de diseño: qué permite la licencia

**Pregunta** (dueño, 2026-10-13, durante la revisión de la tanda A de i-7985ee-44060c): ¿se puede
mostrar el panel con estilos parecidos a los de Modrinth, como guía de diseño, dentro de lo que
permite su licencia? **Cómo se hizo:** un agente investigador (solo lectura), 2026-10-13.
**Advertencia de fuente:** todo se leyó con WebFetch, que resume; ningún archivo se leyó entero.
Todo queda SIN VERIFICAR hasta abrir los archivos crudos enlazados. Lo que sostenga una decisión se
verifica antes de construir.

## Licencias, por parte

| Parte | Licencia | Fuente |
|---|---|---|
| `modrinth/code`, `apps/frontend` (el sitio, Nuxt; antes knossos) | AGPL-3.0 | https://raw.githubusercontent.com/modrinth/code/main/apps/frontend/LICENSE |
| Marca dentro de `apps/frontend` (logo, favicons, componentes de marca, imágenes de noticias) | todos los derechos reservados | https://raw.githubusercontent.com/modrinth/code/main/apps/frontend/COPYING.md |
| `packages/ui` (componentes Vue, utilidades Tailwind) | GPL-3.0, sin la marca | https://raw.githubusercontent.com/modrinth/code/main/packages/ui/LICENSE |
| `packages/assets` (íconos, `styles/variables.scss`, `inter.scss`, `omorphia.scss`) | GPL-3.0; `branding/*` y `external/*` reservados | https://raw.githubusercontent.com/modrinth/code/main/packages/assets/COPYING.md |
| Raíz del monorepo | cada paquete con su licencia | https://raw.githubusercontent.com/modrinth/code/main/COPYING.md |
| Íconos | Lucide (`lucide-static`): ISC, MIT en los derivados de Feather | https://lucide.dev/license |
| Fuente | Inter 3.19: SIL OFL 1.1 | https://raw.githubusercontent.com/rsms/inter/master/LICENSE.txt |
| Este panel | GPL-3.0 (`LICENSE`; `pyproject.toml` no declara licencia) | local |

No se revisaron los repositorios viejos `omorphia` y `knossos`, ni el LICENSE de la raíz (dio 404).

## Qué permite

- **Copiar CSS, componentes o tokens** de `packages/ui` o `packages/assets` (GPL-3.0): cabe en un
  panel GPL-3.0, conservando los avisos de copyright y licencia en lo copiado. Copiar de
  `apps/frontend` (AGPL-3.0) suma la cláusula de red: quien use el panel por la red debe poder obtener
  el código fuente. Es una lectura de la licencia, no una cita.
- **Reproducir el aspecto desde cero** (colores, espaciados, forma de las tarjetas y los filtros): no
  hay fuente primaria que lo trate; son ideas, no código (inferencia). Los hex de `variables.scss`:
  verde `#1bd96a` en oscuro y `#00af5c` en claro; superficies `#16181c`, `#27292e` y `#34363c`.
- **Íconos y fuente:** Lucide e Inter se pueden usar directamente desde sus propias fuentes, con su
  aviso de licencia.

## Marca

- El COPYING.md del frontend prohíbe usar el nombre, el logo, la imagen de portada «y cualquier
  variación» sin permiso escrito de Rinth, Inc.; un fork debe sacarlos.
- Los términos (https://modrinth.com/legal/terms): «Modrinth», el logo, los nombres, diseños y
  eslóganes relacionados son marcas de la empresa y no se usan sin permiso previo y por escrito.
  Prohíben suplantar a la empresa; esa cláusula se refiere a la conducta en el servicio, no al estilo.
- Ninguna fuente leída protege el verde de la marca como tal. No se encontró una guía de marca aparte.

## Recomendación (inferencia, no de las fuentes)

Escribir el aspecto desde cero: tema oscuro, acento verde propio (no `#1bd96a` exacto), tarjetas
redondeadas, mismo ritmo de espaciado. Lucide e Inter desde sus fuentes, con sus avisos. No copiar
componentes salvo que se acepte GPL-3.0 en esos archivos (el panel ya lo es: costo bajo). Nunca el
nombre como marca del panel, el logo ni la mascota; una nota en el pie de que el panel no está
afiliado a Modrinth. Incorporar Lucide o Inter es un tercero que entra al repositorio: decisión del
dueño.
