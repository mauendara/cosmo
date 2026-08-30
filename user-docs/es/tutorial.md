# Tutorial: tu primera ejecución de Cosmo

> Nota: esta traducción puede no estar actualizada. El inglés es la fuente canónica de esta documentación — consulta la [versión en inglés](../en/tutorial.md).

Al final de esto habrás inicializado (bootstrap) un repositorio real, convertido una idea
en bruto en una tarea encolada, la habrás ejecutado en Cosmo de principio a fin, e
inspeccionado lo que ocurrió. Toma una sola sesión más el tiempo que tarde la tarea en sí.

Este es el único recorrido lineal en la documentación. Todo lo opcional vive en
las [guías how-to](how-to/); todo lo exhaustivo vive en
[reference](reference/).

---

## 0. Requisitos previos

Cosmo invoca herramientas reales (shells out). Todas estas deben estar en `PATH`:

| Herramienta | Por qué |
| --- | --- |
| **Python 3.12+** y [`uv`](https://docs.astral.sh/uv/) | Cosmo en sí |
| **git** | worktrees, ramas, la escalera de merge |
| **Docker** | el gate de validación ejecuta cada build y test en contenedores |
| **CLI de [OpenSpec](https://github.com/Fission-AI/OpenSpec)** (`openspec`) | el flujo propose/apply/archive que Cosmo dirige |
| **[gitleaks](https://github.com/gitleaks/gitleaks)** | el escaneo de secretos en el pre-commit, y el escaneo de respaldo del propio gate |
| **Un CLI de harness** — hoy [Claude Code](https://claude.com/claude-code) (`claude`) | el agente que realmente escribe el código |

En Windows, ejecuta todo dentro de WSL2 y mantén el repositorio en el sistema de
archivos de WSL2, no en `/mnt/c` — consulta [setup-wsl2](how-to/setup-wsl2.md).

## 1. Instalar Cosmo

```bash
git clone <this repo> cosmo
cd cosmo
uv sync
uv run cosmo --version
```

Para tener un `cosmo` desnudo en tu `PATH`:

```bash
uv tool install --editable .
```

Usa `--editable` desde un checkout completo. Las plantillas de proyecto y de harness
de Cosmo se leen desde el directorio `templates/` del repositorio, no desde el
wheel instalado, y una instalación no editable falla con:

```
Cosmo's templates/ directory was not found at .../lib/python3.14/templates.
This requires an editable install (`uv tool install --editable .`) from a
full checkout of Cosmo's own repository.
```

El resto de este tutorial escribe `cosmo`; si te saltaste la instalación de la
herramienta, escribe `uv run cosmo` en su lugar.

## 2. Verificar el host

```console
$ cosmo doctor
harness: claude (from config default)

core checks
┏━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃        ┃ check                  ┃ detail                                            ┃
┡━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ ok     │ python                 │ 3.12.7                                            │
│ ok     │ git                    │ /usr/bin/git                                      │
│ ok     │ docker                 │ /usr/bin/docker                                   │
│ ok     │ openspec               │ /home/you/.local/bin/openspec                     │
│ ok     │ gitleaks               │ /home/you/.local/bin/gitleaks                     │
│ ok     │ disk space             │ 84.1 GB free at /home/you/.local/share/cosmo      │
│ ok     │ state dirs writable    │ /home/you/.local/share/cosmo and siblings         │
│ ok     │ work dir filesystem    │ /home/you/.local/share/cosmo/work                 │
│ ok     │ event/state store      │ not yet created -- initializes on first write     │
│ ok     │ leaked gate containers │ none found                                        │
└────────┴────────────────────────┴───────────────────────────────────────────────────┘
harness checks (claude)
┏━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
│ ok     │ claude cli           │ /home/you/.local/bin/claude                      │
│ ok     │ subscription billing │ ANTHROPIC_API_KEY is unset (subscription billing) │
│ ok     │ permission mode      │ dontAsk                                          │
└────────┴──────────────────────┴──────────────────────────────────────────────────┘
```

Dos verificaciones que vale la pena entender antes de continuar:

- **`subscription billing`** falla, de forma definitiva, si `ANTHROPIC_API_KEY`
  está configurada. Con la clave presente, el harness factura silenciosamente
  por token en lugar de contra tu suscripción — algo caro de descubrir a la
  mañana siguiente de una ejecución desatendida. Desconfigúrala.
- **`disk space`** falla por debajo de `disk.min_free_gb` (10 GB por defecto).
  Una ejecución se aborta en esta verificación en lugar de iniciar y fallar
  cada tarea a mitad de camino con el disco lleno.

Corrige cualquier cosa reportada como `FAIL` antes de continuar. `cosmo doctor`
sale con código distinto de cero cuando encuentra un problema bloqueante, así
que también funciona en un script de pre-ejecución.

## 3. Inicializar (bootstrap) un repositorio destino

Un **repositorio destino** (target repo) es el proyecto sobre el que quieres que
se haga el trabajo. Es algo distinto del propio checkout de Cosmo.

```console
$ cosmo init ~/code/my-app --project-template vite-react-local
harness: claude (from config default)
project template: vite-react-local
git branch: git init, then created and checked out 'develop'
openspec/ created
docs/: created 7, skipped (already exists) 0
.agent/claude/: synced (template_version=da0446ae5a99)
  created CLAUDE.md -> .agent/claude/CLAUDE.md
  created .claude -> .agent/claude
  created agents -> .agent/claude/agents
  created skills -> .agent/claude/skills
registered project my-app-997f83de
git identity: set Your Name <you@example.com>
committed init bootstrap output
```

Lo que acaba de suceder, en orden:

1. `git init` si el directorio aún no era un repositorio, y luego se creó y
   se hizo checkout de la rama de integración configurada (`git.base_branch`,
   por defecto `develop`). Si el repositorio ya estaba en una rama distinta
   con el árbol sucio, Cosmo se niega a tocarlo y te indica que lo resuelvas
   tú mismo.
2. `openspec init` si `openspec/` no existía.
3. Se sembró `docs/` a partir de la plantilla de proyecto. **Los archivos
   existentes nunca se sobrescriben** — `docs/` pertenece a tu repositorio
   una vez sembrado. `--force` sobrescribe, con una confirmación previa.
4. Se creó `docs/specs/`, donde van tus specs escritas a mano.
5. Se escribió la política operativa del harness, las definiciones de
   agente/skill y los hooks de guardrail bajo `.agent/claude/`, y luego se
   crearon enlaces simbólicos con lo que el harness espera en la raíz del
   repositorio (`CLAUDE.md`, `.claude`, `agents`, `skills`). Los enlaces
   simbólicos son relativos, así que el repositorio puede moverse o
   clonarse en otro lugar sin romperse.
6. Se registró el proyecto, para que `--harness` pueda resolverse a partir
   de una ruta más adelante.

Elige una plantilla que se ajuste a tu stack — `cosmo templates list`
muestra lo disponible (`_blank`, `java-spring-react`, `vite-react-local` hoy
en día). Si ninguna encaja, parte de `_blank` y consulta
[add-project-template](how-to/add-project-template.md).

Ahora confirma que el propio repositorio está listo:

```bash
cosmo doctor --project-path ~/code/my-app
```

## 4. Una nota sobre `--repo`

Todo comando que opera sobre un repositorio destino (`spec add`, `spec queue`,
`run`, `queue retry`) acepta `--repo <path>`, que por defecto es el directorio
actual. La ruta resuelta se verifica contra el registro de `cosmo init`: un
error de tipeo o un directorio sin `init` falla ruidosamente en lugar de
operar silenciosamente en un lugar equivocado.

Este tutorial escribe `--repo` explícitamente. Una vez que te acostumbres,
haz `cd` al repositorio destino y omite la bandera.

## 5. Escribir una spec en bruto

Escribe lo que quieres, en la forma que sea que salga. Puede describir varias
piezas de trabajo — el trabajo de Cosmo es dividirlo.

```bash
cat > ~/login-idea.md <<'EOF'
Add email + password login.

Users should be able to sign up, log in, and log out. Sessions persist
across a page reload. Show a clear error for a wrong password. Logged-out
users hitting a protected route get redirected to the login page.
EOF
```

## 6. Enriquecerla y descomponerla

```bash
cosmo spec add add-login --repo ~/code/my-app --from ./login-idea.md
```

Esto copia tu archivo como `docs/specs/add-login-spec.md` y conduce al
harness a través de dos pasos: **enriquecimiento** (leyendo los `docs/backend/`,
`docs/frontend/`, `docs/data-model.md`, `docs/base-standards.md` propios de tu
repositorio en busca de sus convenciones) y **descomposición** (dividiendo el
trabajo en unidades con dependencias explícitas).

Escribe un archivo por unidad de trabajo bajo
`docs/specs/add-login-spec/tasks/`, y luego imprime una tabla de vista previa:

```console
add-login-spec tasks
┏━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┓
┃ task_id          ┃ title                     ┃ depends_on       ┃ priority ┃ allow_test_edits ┃ file        ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━┩
│ add-login-schema │ User table and migration  │ -                │ 0        │ -                │ schema-task…│
│ add-login-api    │ Login and logout endpoints│ add-login-schema │ 0        │ -                │ api-task.md │
│ add-login-page   │ Login page and redirects  │ add-login-api    │ 0        │ -                │ page-task.md│
└──────────────────┴───────────────────────────┴──────────────────┴──────────┴──────────────────┴─────────────┘
```

Cada archivo es frontmatter YAML más un cuerpo en markdown:

```markdown
---
task_id: api
title: Login and logout endpoints
depends_on: [schema]
priority: 0
allow_test_edits: false
---

Implement POST /api/auth/login ...
```

La vista previa muestra ids *con namespace* (`add-login-api`) mientras que
los archivos en disco llevan los desnudos (`api`) — consulta el paso 7 para
saber por qué.

**Todavía no se ha encolado nada.** Estos son archivos reales, rastreados por
git, en tu repositorio, y la ventana entre ahora y el siguiente comando *es*
el paso de revisión — no hay una UI de aprobación separada. Ábrelos. Corrige
el alcance, la redacción, las dependencias. Si el entregable completo de una
tarea vive bajo una ruta de test protegida (una suite `e2e/`, por ejemplo),
configura `allow_test_edits: true` en ella ahora — de lo contrario los hooks
de guardrail se negarán correctamente a dejar que el agente escriba nada y
la tarea fallará por una razón que parecerá no tener sentido.

## 7. Encolarla

```console
$ cosmo spec queue add-login --repo ~/code/my-app
queued add-login-schema
queued add-login-api
queued add-login-page
```

Cada id de tarea recibe un prefijo con el nombre de la spec al momento de
insertarse (`api` → `add-login-api`), y los bordes de `depends_on` dentro del
lote se reescriben para coincidir. `task_queue.task_id` es una única clave
global compartida entre todos los proyectos que usan una misma base de datos
de Cosmo, así que dos proyectos que descompongan cada uno en una tarea
llamada `scaffold-app` de otro modo colisionarían — y un borde `depends_on`
se resolvería contra la tarea ya finalizada del proyecto equivocado. Una
entrada de `depends_on` que no forma parte de este lote se deja intacta, así
que aún puedes apuntar a algo encolado anteriormente.

Volver a ejecutar `spec queue` sobre un lote ya encolado es una operación
sin efecto (no-op), no un error.

```console
$ cosmo queue ls
task queue
┏━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━┓
┃ task_id                 ┃ status ┃ attempts ┃ depends_on            ┃ priority ┃ blocked_reason ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━┩
│ add-login-schema        │ queued │ 0/2      │ -                     │ 0        │ -              │
│ add-login-api           │ queued │ 0/2      │ add-login-schema      │ 0        │ -              │
│ add-login-page          │ queued │ 0/2      │ add-login-api         │ 0        │ -              │
└─────────────────────────┴────────┴──────────┴───────────────────────┴──────────┴────────────────┘
```

## 8. Previsualizar el orden

```console
$ cosmo run --repo ~/code/my-app --dry-run
harness: claude (from project registration)
1. add-login-schema
2. add-login-api
3. add-login-page
```

Esto resuelve el DAG y no imprime nada más — sin llamadas al harness, sin
worktrees, sin costo. Los ciclos de dependencias se rechazan aquí (y también
al momento de encolar), nunca se descubren a mitad de una ejecución.

## 9. Ejecutarlo

```bash
cosmo run --repo ~/code/my-app
```

La cola se vacía estrictamente una tarea a la vez hasta quedar vacía, hasta
que un circuit breaker se dispara, hasta que un límite de costo o de cuota
interviene, o hasta que el tiempo de reloj de la ejecución
(`timeouts.run_wall`, 10 horas por defecto) expira.

Para cada tarea, en orden:

```
QUEUED → PROPOSING → PROPOSED → IMPLEMENTING → VALIDATING
       → REVIEWING → COMMITTING → MERGING → FINISHING → DONE
```

- **PROPOSING** — el harness ejecuta el flujo de propuesta de OpenSpec,
  creando `openspec/changes/<task-id>/` dentro de un worktree nuevo en
  `<work_dir>/<run_id>/<task_id>` en la rama `task/<task-id>`.
- **IMPLEMENTING** — el harness escribe el código y hace commit de él,
  vigilado por un temporizador de estancamiento (stall timer) y un tiempo de
  reloj. El progreso se obtiene observando el `tasks.md` del change, no de
  nada que el agente afirme.
- **VALIDATING** — el gate. Diff gate → escaneo de gitleaks → build de
  Docker → tests unitarios → e2e. Esto es lo único que decide si la tarea
  funcionó. Consulta
  [validation-gate-and-guardrails](concepts/validation-gate-and-guardrails.md).
- **REVIEWING** — una sesión de harness *nueva*, sin memoria de la
  implementación, lee `git diff <base>...HEAD` y la spec del change, y
  escribe un veredicto de aprobar/rechazar en un archivo. Se desactiva con
  `review.enabled = false`.
- **COMMITTING** — aplica el límite de líneas de los archivos de
  conocimiento sobre cualquier `docs/**/*.md` que la tarea haya tocado, y
  añade una línea a `docs/decisions-log.md`.
- **MERGING** — hace merge de `task/<task-id>` en tu rama base mediante la
  escalera de conflictos (merge, luego rebase + reejecución del gate, luego
  bloqueo).
- **FINISHING** — `openspec archive`, en modo best-effort; un fallo aquí se
  registra y nunca deshace un merge que ya ocurrió.

Verás una línea por cada transición de estado, más el ruido de cada llamada
a herramienta:

```
01:54:20Z >> run.started
01:54:31Z >> task.state_changed [add-login-schema] queued -> proposing
02:11:07Z >> task.state_changed [add-login-schema] implementing -> validating
02:19:44Z >> task.validation_result [add-login-schema] passed=True, unit=pass (14p/0f/0s), e2e=pass (3p/0f/0s)
02:20:12Z >> task.completed [add-login-schema]
```

La ejecución termina con un resumen y un código de salida — `0` solo para
una parada limpia por `completed` o `queue_empty`, `1` para todo lo demás,
incluyendo `blocked_remaining`:

```
stopped (queue_empty)
completed=3 blocked=0 requeued=0 retried=1
```

Para conducir una sola tarea ya encolada en lugar de toda la cola:

```bash
cosmo run --repo ~/code/my-app --task add-login-schema
```

## 10. Inspeccionar lo que ocurrió

Mucho después de la ejecución, desde una terminal distinta, nada de esto
necesita que el proceso de la ejecución siga existiendo.

```bash
cosmo report                    # la ejecución más reciente
cosmo report --run <run_id>     # una específica
cosmo report --follow           # en vivo, hasta que la ejecución llegue a un estado terminal
```

Estado, razón de parada o pausa, conteos de completadas y bloqueadas
desglosados por razón, costo, duración.

```bash
cosmo events tail                        # eventos recientes de todo
cosmo events tail --run <run_id>
cosmo events tail --task add-login-api
cosmo events tail --type task.blocked
cosmo events tail --payload              # cuerpo JSON completo bajo cada fila
cosmo events tail --follow               # tail -f
```

La tabla te dice *que* algo ocurrió; `--payload` te dice *qué*.

Para una tarea que no funcionó:

```bash
cosmo queue show add-login-api        # estado, intentos, último error, ruta del worktree
cosmo queue failures add-login-api    # el registro completo de fallos de cada intento
```

`queue failures` es el comando que importa después de una noche desatendida.
Imprime el tipo y la etapa de fallo de cada intento, un resumen, y el
**detalle real del error** — mensajes de aserción, extractos de stack,
nombres de tests que fallaron. Ese texto no tiene ninguna otra superficie de
CLI; los payloads de eventos deliberadamente no lo llevan.

## 11. Lidiar con una tarea bloqueada

Una tarea `BLOCKED` conserva su worktree y su rama en disco para que las
revises. Obtén la ruta desde `cosmo queue show`.

Una vez que hayas corregido lo que la causó — una dependencia faltante, una
spec mala, un entorno roto — vuelve a ponerla en la cola:

```bash
cosmo queue retry add-login-api --repo ~/code/my-app
```

`retry` reinicia el contador de intentos y, si el worktree todavía tiene el
commit que hizo `PROPOSING`, descarta solo la implementación fallida y
conserva el change de OpenSpec ya válido — de modo que la siguiente
ejecución retoma en `IMPLEMENTING` sin pagar de nuevo por el propose.

Si la tarea ya se ha bloqueado por la *misma razón* varias veces, `retry` se
niega y lo indica en lugar de darle otra ronda silenciosa de intentos.
`--force` lo anula — úsalo después de que un humano haya abordado realmente
la causa recurrente, no para hacer que el mensaje desaparezca.

## 12. Próximos pasos

- Ejecútalo desatendido durante la noche: [setup-vps](how-to/setup-vps.md) o
  [setup-wsl2](how-to/setup-wsl2.md).
- Entérate cuando algo se rompa: `cosmo notify config` te guía por la
  configuración de Telegram de principio a fin, incluyendo el envío de un
  mensaje de prueba real.
- Entiende lo que el gate realmente verifica:
  [validation-gate-and-guardrails](concepts/validation-gate-and-guardrails.md).
- Ajusta los límites antes de una ejecución larga:
  [configure-quotas](how-to/configure-quotas.md).
