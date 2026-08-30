# Cómo agregar una plantilla de proyecto

> Nota: esta traducción puede no estar actualizada. El inglés es la fuente canónica de esta documentación — consulta la [versión en inglés](../../en/how-to/add-project-template.md).

Una **plantilla de proyecto** es el árbol `docs/` que `cosmo init` siembra en
un repositorio objetivo. Esos documentos son lo que el paso de enriquecimiento
lee para aprender tus convenciones, y lo que el agente implementador consulta
mientras escribe código. Una plantilla que describe tu stack con precisión es
la forma más económica de evitar que cada tarea redescubra las mismas
restricciones por prueba y error.

Agrega una cuando trabajes en un stack que las plantillas incluidas no cubren.

```console
$ cosmo templates list
project templates
┏━━━━━━━━━━━━━━━━━━━┓
┃ name              ┃
┡━━━━━━━━━━━━━━━━━━━┩
│ _blank            │
│ java-spring-react │
│ vite-react-local  │
└───────────────────┘
```

## Dónde viven las plantillas

```
templates/
  harness/
    claude/            # política operativa del harness, agentes, skills, hooks
  projects/
    _blank/            # esqueleto solo de esquema
    java-spring-react/
    vite-react-local/
      docs/            # ← todo lo que hay aquí se copia al repositorio objetivo
```

Las plantillas se leen desde el propio checkout de Cosmo, no desde un wheel
instalado — por eso la instalación documentada es `uv tool install --editable .`.
Una instalación no editable falla con un mensaje que te lo indica exactamente.

Agregar una plantilla significa agregar un directorio. No hay un registro que
actualizar, ni código que cambiar: `cosmo templates list` enumera los
directorios bajo `templates/projects/`.

## 1. Copia el esqueleto

```bash
cd <your cosmo checkout>
cp -r templates/projects/_blank templates/projects/my-stack
```

`_blank` es solo esquema — el conjunto correcto de encabezados sin nada
completado. Si tu stack se parece más a una plantilla existente, empieza
desde esa en su lugar:

```bash
cp -r templates/projects/vite-react-local templates/projects/my-stack
```

Confirma que sea visible:

```bash
cosmo templates list
```

## 2. Entiende qué se copia

Todo lo que está bajo `templates/projects/<name>/docs/` se copia a `docs/`
en el repositorio objetivo, preservando la estructura. Nada más en el
directorio de la plantilla se usa.

El esqueleto `_blank`:

```
docs/
  base-standards.md
  data-model.md
  api-spec.yml
  backend/
    architecture.md
    persistence.md
    security.md
    error-handling.md
  frontend/
    architecture.md
    state-management.md
    styling.md
```

No estás obligado a mantener esta forma. `vite-react-local`, para un stack
solo de frontend, elimina `backend/` y `api-spec.yml` por completo y agrega
`persistence.md` y `testing.md` en el nivel superior. Incluye los documentos
sobre los que tu stack realmente tiene decisiones tomadas.

Dos cosas **deliberadamente no** forman parte de ninguna plantilla:

- **`docs/specs/`** — eso es contenido de lote de specs, no boilerplate de
  stack. `cosmo init` crea el directorio vacío por sí mismo.
- **`docs/decisions-log.md`** — Cosmo le agrega contenido durante
  `COMMITTING`, con un encabezado que escribe en el primer uso.

Los archivos **nunca se sobrescriben** por defecto. `docs/` pertenece al
repositorio objetivo una vez sembrado, así que volver a ejecutar `cosmo init`
no sobrescribirá las ediciones hechas ahí. `--force` sobrescribe, con un
mensaje de confirmación.

## 3. Escribe los documentos

La regla que importa: **escribe las restricciones que de otro modo
descubriría una tarea fallida.** Estos archivos no son tutoriales — a nadie
le hace falta que le expliquen React. Son los hechos específicos, no obvios y
exigidos por el gate sobre tu stack que un agente hará mal en su primer
intento, y en cada primer intento posterior.

Ejemplos concretos de la plantilla `vite-react-local` incluida, cada uno de
los cuales existe porque una tarea real gastó un intento en él:

> Fija `@playwright/test` exactamente en `1.49.0`, nunca en `@latest` — el
> gate de validación ejecuta la etapa de e2e en
> `mcr.microsoft.com/playwright:v1.49.0-noble`, un contenedor que solo tiene
> los binarios de navegador de esa versión. Un `@playwright/test` más nuevo
> resuelve a una versión de navegador que el contenedor no tiene.

> `playwright.config.ts` debe configurar el reporter `json` para que escriba
> en `playwright-report/results.json` — la ruta exacta que el gate parsea. Un
> reporter por defecto o solo HTML deja ese archivo faltante, lo cual el gate
> reporta como "playwright produjo ningún reporte", indistinguible de que la
> suite nunca se ejecutó.

> `playwright.config.ts` debe leer su URL base desde `process.env.BASE_URL`,
> no desde un puerto localhost fijo en el código — el gate levanta la app
> compilada como un contenedor en una red Docker privada y pasa `BASE_URL`
> apuntando al hostname de ese contenedor.

Cada uno tiene dos oraciones y cada uno ahorra un intento fallido por
proyecto, para siempre. Ese es el estándar al que apuntar.

Cubre, como mínimo:

- **Todo lo que el gate exige y para lo cual tu stack debe estar
  configurado.** El fijado de Playwright y la ruta del reporter de arriba
  son el arquetipo. Si esto se hace mal, cada proyecto sobre esta plantilla
  lo redescubre.
- **La disposición de directorios**, específicamente dónde viven el backend
  y el frontend. `gate.backend_dir` y `gate.frontend_dir` toman por defecto
  `backend/` y `frontend/`; si los tuyos difieren, dilo aquí *y* configúralos
  en config.
- **Convenciones de pruebas** — cómo se llama un archivo de prueba, dónde va,
  qué queries usar. Ten en cuenta que los guardrail hooks protegen
  `src/test/**`, `e2e/**` y `**/*.{test,spec}.{ts,tsx,jsx}` por defecto.
- **Persistencia y modelo de datos** — el esquema, el enfoque de migraciones
  y cómo se relacionan las entidades.
- **Manejo de errores y postura de seguridad** — la taxonomía, y qué nunca
  debe aparecer en el cuerpo de una respuesta.
- **Estilo y estándares** — formateo, convenciones de nombres, reglas de
  lint que se exigen en vez de sugerirse.

Mantenlos factuales y actualizados en lugar de narrar historia. El historial
de git y el log de eventos ya cubren lo que sucedió. Y mantén cada archivo
por debajo de `knowledge.max_file_lines` (400 por defecto) — Cosmo impone ese
límite durante `COMMITTING`, así que un archivo demasiado largo hará fallar
una tarea en lugar de recortarse silenciosamente.

## 4. Ajusta el gate al stack

Si tu stack no es un backend Maven más un frontend Node, la plantilla sola no
alcanza — las imágenes y directorios del gate son configuración:

```toml
[gate]
backend_image  = "golang:1.23"
backend_dir    = "server"
frontend_image = "node:24.19-bookworm"
frontend_dir   = "web"
```

Si falta `backend_dir` se omiten por completo las etapas de backend. Si falta
`frontend_dir` se omite la etapa de e2e.

`playwright_image` debe estar fijado a un tag explícito — la carga de config
rechaza `:latest` o un nombre de imagen sin tag. Si lo cambias, cambia
`playwright_npm_version` en la misma edición y actualiza la instrucción de
fijado en el documento de testing de tu plantilla. Esos tres son un solo
hecho registrado en tres lugares, y dejar que diverjan es exactamente el
fallo que el fijado existe para prevenir.

Ten en cuenta que los *comandos* de build y test por etapa actualmente no son
configurables. El gate ejecuta Maven contra un directorio de backend y
`npm ci && npm run build` contra uno de frontend — así que una plantilla de
frontend debe producir un repositorio con un lockfile confirmado y un script
`build`, y una plantilla de backend debe producir un proyecto Maven. Un stack
genuinamente distinto (Go, Rails, Python) puede usar el sistema de plantillas
para su documentación hoy, pero sus etapas de build necesitarán trabajo en el
gate. Dilo en la plantilla en vez de dejar que alguien lo descubra a las 3am.

## 5. Pruébala de punta a punta

```bash
mkdir /tmp/template-test
cosmo init /tmp/template-test --project-template my-stack
```

Verifica que `docs/` se vea bien, que `docs/specs/` exista, y que los
symlinks se hayan creado. Luego ejecuta el gate contra un worktree real de un
proyecto construido sobre ella:

```bash
cosmo validate /path/to/a/worktree --task-id template-smoke-test
```

Esto ejecuta el gate completo de forma independiente sin tocar la cola — la
forma más rápida de descubrir que tu `backend_dir` está mal o que tu reporter
de Playwright no está escribiendo donde el parser busca.

## 6. Contribúyela de vuelta

Si el stack es uno que otras personas usan, abre un pull request. Consulta
[CONTRIBUTING.md](../../../CONTRIBUTING.md). Incluye:

- El directorio de la plantilla.
- Una nota en el pull request describiendo el stack al que apunta y cualquier
  configuración `[gate]` que necesite.
- Idealmente, evidencia de que ejecutaste una tarea real a través de ella —
  las restricciones que vale la pena documentar son las que un fallo real te
  enseñó, y esas son para las que existe la plantilla.
