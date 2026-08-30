# Arquitectura: visión general

> Nota: esta traducción puede no estar actualizada. El inglés es la fuente canónica de esta documentación — consulta la [versión en inglés](../../en/concepts/architecture-overview.md).

De qué está hecho Cosmo, y por qué las piezas están organizadas así.

## La forma que tiene

```
                       cosmo run
                           │
              ┌────────────▼────────────┐
              │  run loop (serial)      │   one task at a time
              │  DAG scheduler          │   recomputes eligibility each pass
              │  circuit breaker        │
              │  quota + cost accounting│
              └────────────┬────────────┘
                           │  per task
              ┌────────────▼────────────┐
              │  task state machine     │
              └──┬───────┬───────┬──────┘
                 │       │       │
        ┌────────▼──┐ ┌──▼─────┐ ┌▼──────────┐
        │ harness   │ │ gate   │ │ git       │
        │ adapter   │ │ Docker │ │ worktree  │
        │ (LLM)     │ │ only   │ │ + merge   │
        └───────────┘ └────────┘ └───────────┘
                 │       │       │
              ┌──▼───────▼───────▼──┐
              │ SQLite: state       │
              │ + append-only events│
              └─────────────────────┘
```

Tres límites en ese diagrama están impuestos por tests que leen el código
fuente, no por convención:

- **El gate nunca importa el harness.** La validación evita el LLM por
  completo — es una invocación directa de Docker, y no existe ninguna ruta de
  código por la cual un agente pueda influir en su propio veredicto.
- **La escalera de merge nunca importa el harness.** Un conflicto de merge
  por lo tanto nunca se le devuelve al agente para que lo resuelva a ciegas —
  no hay ningún adaptador dentro del alcance de esa ruta de código al cual
  entregárselo.
- **Solo el módulo adaptador de Claude puede nombrar binarios, flags o
  variables de entorno específicos de Claude.** El código central de
  orquestación nunca se ramifica según qué harness esté configurado.

## Serial por diseño

Cosmo ejecuta exactamente una tarea a la vez. Los worktrees aíslan el
*código*, no el runtime: los puertos, las bases de datos y `/dev/shm` siguen
siendo compartidos, así que dos tareas concurrentes competirían por los tres.
La paralelización implicaría resolver eso primero, y el diseño actual lo dice
así en lugar de simular un aislamiento que no tiene.

Un bloqueo de proceso lo hace cumplir. Un segundo `cosmo run` contra el mismo
almacén se niega a iniciar.

## La cola es un DAG, no un FIFO

Las tareas llevan aristas explícitas `depends_on`. Nada se infiere jamás a
partir de nombres de archivo, el contenido del spec, o el orden — una
dependencia inferida que está equivocada es peor que ninguna dependencia.

La planificación recalcula el conjunto elegible completo en cada pasada, no
solo una tarea por delante. Cuando una tarea se bloquea, las ramas
independientes del grafo siguen ejecutándose. `priority` desempata entre
tareas simultáneamente elegibles; nunca anula una arista.

Los ciclos se rechazan al momento de encolar y de nuevo al iniciar la
ejecución, nunca se descubren a mitad de ejecución.

Dos puertas de entrada llevan a la misma cola, y `cosmo run` no puede
distinguir por cuál entró una tarea:

- **`cosmo spec add` → `cosmo spec queue`** — un spec preliminar, enriquecido
  contra la propia documentación de tu repositorio y descompuesto en archivos
  de tarea que puedes editar a mano antes de encolarlos.
- **`cosmo queue add`** — un cambio de OpenSpec redactado a mano, encolado
  directamente.

## La máquina de estados de la tarea

```
QUEUED → PROPOSING → PROPOSED → IMPLEMENTING → VALIDATING
       → REVIEWING → COMMITTING → MERGING → FINISHING → DONE
                              ↘ FAILED_RETRY ↗
                              ↘ BLOCKED
```

- **PROPOSING** — el harness ejecuta el flujo de trabajo `propose` de
  OpenSpec. Tiene su propia política acotada de "reintentar una vez, luego
  bloquear" que nunca toca el contador de intentos de la tarea; todavía no
  hay ningún código al que atribuirle un error de código. El nombre del
  cambio queda fijado en el prompt, porque todo lo que viene después
  (`openspec archive`, la reutilización del worktree) lo asume.
- **PROPOSED → IMPLEMENTING** — el harness escribe y hace commit del código,
  vigilado por un reloj de pared y un temporizador de estancamiento. El
  progreso se lee del `tasks.md` del cambio, no de nada que el agente
  afirme.
- **VALIDATING** — el gate. Ver
  [validation-gate-and-guardrails](validation-gate-and-guardrails.md).
- **REVIEWING** — una revisión adversarial fresca y sin memoria. Se omite
  cuando `review.enabled = false`.
- **COMMITTING** — nunca llama al harness. Impone el límite de líneas de los
  archivos de conocimiento en cualquier `docs/**/*.md` que la tarea haya
  tocado, y agrega una línea autoría de Cosmo a `docs/decisions-log.md`. Una
  violación del límite regresa a `IMPLEMENTING` como un reintento informado.
- **MERGING** — la escalera de conflictos, abajo.
- **FINISHING** — `openspec archive` de mejor esfuerzo. Un fallo aquí se
  registra como advertencia y nunca deshace un merge que ya se completó con
  éxito.

`attempt_count` solo se incrementa en intentos que representan un juicio
genuino a nivel de código. Ver la tabla de fallos en el documento del gate.

## Aislamiento por worktree

Cada tarea obtiene `git worktree add <work_dir>/<run_id>/<task_id> -b
task/<spec_id>` — un directorio de trabajo dedicado sobre un único almacén de
objetos compartido. Sin cambios de rama, sin trabajo a medio aplicar de la
tarea anterior, sin bailes con `git stash`.

Inmediatamente después de la creación, Cosmo sincroniza los recursos del
harness en el worktree e instala el hook de pre-commit de gitleaks. (Los
hooks de Git viven en el directorio de hooks *común* del repositorio,
compartido por cada worktree vinculado, así que instalarlos por worktree es
idempotente y autorreparable.)

El worktree y la rama de una tarea `BLOCKED` se dejan en disco para que los
inspecciones. Un barrido al inicio elimina los worktrees pertenecientes a
ejecuciones que ya terminaron.

## La escalera de merge

`cosmo run --repo` apunta al checkout propio y dedicado de Cosmo del
repositorio objetivo, el cual permanece en la rama base en todo momento.
Nunca es el directorio de trabajo interactivo de un desarrollador.

Ante un conflicto de merge, la escalera es:

1. Intentar el merge.
2. Ante un conflicto, hacer rebase de la rama de la tarea sobre la rama base
   y **volver a ejecutar el gate de validación completo**. Un rebase cambia
   el código bajo los tests; un rebase que no se vuelve a validar es un
   merge que nunca se probó.
3. Si sigue en conflicto, o si la revalidación falla: bloquear la tarea con
   `merge_conflict` y continuar.

El conflicto nunca se le devuelve al agente. Los bloqueos por
`merge_conflict` quedan excluidos del conteo del circuit breaker — señalan
contención de la cola sobre archivos compartidos, no un entorno roto.

## La interfaz del adaptador de harness

Una sola interfaz, `HarnessAdapter`, con siete métodos: `preflight`,
`probe`, `propose`, `implement`, `review`, `get_progress`, `cancel`. Cada
método devuelve el mismo `HarnessResult` uniforme.

Cada adaptador también declara sus capacidades como datos a nivel de clase, y
cada flag nombra el respaldo que toma Cosmo cuando es falso:

| Capacidad | Falso significa |
| --- | --- |
| `reports_native_progress` | vigilar el `tasks.md` del cambio en su lugar |
| `supports_retry_context` | componer un prompt de reintento sintético |
| `has_internal_timeout` | Cosmo impone un timeout externo |
| `reports_native_cost` | estimar a partir de tokens, o desactivar el freno duro de costo |
| `supports_gating` | solo inspección del diff a posteriori — estrictamente más débil |
| `supports_structured_stream` | recurrir a la vivacidad por mtime de archivo |

`cosmo harness list` imprime la tabla. Cómo escribir un adaptador:
[write-a-new-adapter](../how-to/write-a-new-adapter.md).

Nótese que `validate` deliberadamente *no* está en esta interfaz, pese a que
el diseño original la incluía. La validación evita el harness por completo,
así que un método que nunca toca el harness no pertenece al adaptador de
harness.

## Ciclo de vida del proceso

Matar una llamada al harness significa matar todo su grupo de procesos —
SIGTERM, `timeouts.kill_grace` segundos, luego SIGKILL — seguido de un
barrido en busca de contenedores Docker huérfanos (encontrados por sus
etiquetas `orchestrator.run_id` / `orchestrator.task_id`) y de cualquier
proceso que aún mantenga el worktree abierto.

Un reap fallido emite un evento `task.failed` de severidad `critical`
ponderado en `circuit_breaker.reap_failure_weight` (por defecto 2, es decir
el doble), porque un pool de procesos filtrado envenena cada tarea posterior
y la ejecución debe detenerse rápido.

`cosmo doctor` verifica si hay contenedores del gate filtrados como
comprobación central, así que el desorden de una ejecución anterior es
visible antes de que comience la siguiente.

## Estado y continuidad: sin memoria de recuperación

No hay **ningún almacén vectorial, ningún índice de embeddings, ninguna
memoria basada en recuperación (retrieval)**. Eso es una decisión, no un
vacío. La continuidad entre tareas proviene de tres fuentes deterministas:

- **Registros de eventos estructurados** — una tabla de solo anexado con un
  número de secuencia transaccional, de modo que el orden sobrevive a un
  crash.
- **Tablas de estado actual en SQLite** — la cola, el estado de la
  ejecución, los costos, el progreso, los heartbeats, y un historial de
  fallos por intento.
- **Markdown bajo control de versiones** en el repositorio objetivo — los
  archivos de conocimiento en `docs/` que el agente mantiene, acotados por
  `knowledge.max_file_lines` e impuestos por Cosmo en lugar de confiarse al
  agente, más `docs/decisions-log.md`, al que el propio Cosmo le anexa
  líneas para que su formato nunca se desvíe.

La compensación es deliberada: cada una de esas fuentes es consultable,
diferenciable (diffable), e idéntica en una relectura. Una capa de
recuperación haría que el recuerdo entre tareas fuera más difuso justo donde
el sistema más necesita ser reproducible — y un recuerdo equivocado en un
ciclo desatendido es un bug que nadie está despierto para detectar.

## Configuración y estado en disco

```
$XDG_CONFIG_HOME/cosmo/config.toml     user config  (or $COSMO_CONFIG)
$XDG_DATA_HOME/cosmo/
  cosmo.db                             state + events
  work/<run_id>/<task_id>/             task worktrees
  logs/                                raw harness logs
```

Los valores por defecto siguen XDG, así que una máquina de desarrollo no
necesita root; un servidor sobreescribe los tres a algo como `/var/cosmo`.
Mismo código, distinta configuración según el host. La configuración se
valida al momento de cargarse — un valor malformado falla de inmediato, no a
mitad de la ejecución.

## Observabilidad

| Pregunta | Comando |
| --- | --- |
| ¿Cómo terminó la ejecución? | `cosmo report` |
| ¿Qué pasó, en orden? | `cosmo events tail --run <id> --payload` |
| ¿Por qué está atascada esta tarea? | `cosmo queue show <task_id>` |
| ¿Qué falló realmente, con el texto de error real? | `cosmo queue failures <task_id>` |
| ¿Está listo el host? | `cosmo doctor` |

Bajo systemd, el run loop envía señales de disponibilidad y watchdog de
`sd_notify`, de modo que un proceso genuinamente atascado se mata y se
reinicia, mientras que una detención deliberada (una pausa del circuit
breaker) no lo es. Las notificaciones salen desde un proceso *separado* y
siempre activo (`cosmo notify watch`) — la entrega desde dentro del run loop
nunca podría reportar el propio crash del run loop.

---

## Todavía no implementado

Aclarado aquí para que no se confunda con una funcionalidad ya entregada:

- **Un plano de control MCP.** Un servidor MCP delgado sobre el mismo
  contrato de la CLI, que permitiría a un agente o herramienta operar el
  plano de control de Cosmo (encolar, consultar estado, cancelar, logs)
  desde afuera. Es una capacidad distinta de que Cosmo *use* un agente como
  harness, y las dos cosas no deben confundirse. **Hoy no existe tal
  servidor.**
- **Adaptadores distintos de Claude Code.** La interfaz es real y el límite
  está impuesto por tests; el segundo adaptador aún no está escrito.
- **Ejecución paralela de tareas.** Ver "Serial por diseño" arriba.
