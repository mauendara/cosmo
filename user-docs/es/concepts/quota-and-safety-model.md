# Cuota, costo y el modelo de seguridad

> Nota: esta traducción puede no estar actualizada. El inglés es la fuente canónica de esta documentación — consulta la [versión en inglés](../../en/concepts/quota-and-safety-model.md).

Todo lo que puede detener una ejecución, y todo lo que decide cuándo debería
hacerlo.

Una ejecución desatendida no tiene a ningún humano que note que ya gastó
$400, agotó una ventana de rate-limit hace tres horas, llenó el disco, o pasó
las últimas dos horas fallando cada tarea por la misma razón rota. Cada una
de esas situaciones necesita su propio detector y su propia respuesta.

## Las cinco cosas que detienen o pausan una ejecución

| Causa | Resultado | ¿Se reanuda automáticamente? |
| --- | --- | --- |
| Cola vacía / todas las tareas terminadas | `STOPPED` (`queue_empty` o `completed`) | n/a — exit 0 |
| Solo quedan tareas bloqueadas | `STOPPED` (`blocked_remaining`) | no — exit 1 |
| Se agotó el reloj de pared de la ejecución | `STOPPED` (`max_time`) | no |
| Se alcanzó el techo de costo | `STOPPED` (`cost_limit_reached`) | no |
| Disco por debajo del piso | `STOPPED` (`disk_low`) | no |
| Se disparó el circuit breaker | `PAUSED` (`circuit_breaker`) | **no — necesita un humano** |
| Se agotó la ventana de cuota de cinco horas | `PAUSED` (`quota_exhausted_5h`) | sí, después de que la ventana se reinicia |
| Se agotó la cuota semanal | `PAUSED` o `STOPPED` (`quota_exhausted_weekly`) | no |

Solo `completed` y `queue_empty` salen con `0`. Todo lo demás sale con `1`,
que es lo que hace que `Restart=on-failure` junto con
`RestartPreventExitStatus=1` en la unidad de systemd hagan lo correcto: un
reinicio a ciegas no arregla ninguno de estos casos, así que systemd no lo
intenta. Un proceso genuinamente *colgado* nunca llega a `sys.exit` — la
muerte por watchdog de systemd es una señal, no un código de salida, así que
ese caso sí se reinicia.

---

## `blocked_remaining` — por qué "cola vacía" no siempre es éxito

Hay dos situaciones muy distintas en las que al planificador no le queda
nada por ejecutar: la cola realmente terminó, o cada tarea restante está
`BLOCKED` con un fallo sin resolver. Reportar ambas como `queue_empty`
significaba que una ejecución que no logró nada se veía verde y salía con
`0`, y nadie se enteraba hasta que se ponía a investigar.

`blocked_remaining` se elige cuando al menos una tarea realmente se bloqueó
durante la ejecución. Produce salida amarilla y un código de salida distinto
de cero, así que aparece en una notificación y en el estado de systemd en
lugar de tener éxito silenciosamente.

## El circuit breaker

Algunos fallos son sobre una sola tarea. Otros significan que el entorno
está roto y que cada tarea subsiguiente fallará de la misma manera —
quemando cuota y dinero para no aprender nada.

El breaker pausa la ejecución completa cuando se cumple cualquiera de los
dos umbrales:

- **`consecutive_blocked_threshold`** (por defecto 3) — esa cantidad de
  *tareas distintas* cayendo en `BLOCKED` seguidas. Una tarea que llega a
  `DONE` reinicia la racha; "consecutivo" solo tiene sentido en relación con
  los éxitos intermedios.
- **`environment_error_threshold`** (por defecto 3) — peso acumulado de
  errores de entorno entre tareas distintas. Un fallo de reap de proceso
  contribuye con `reap_failure_weight` (por defecto 2) en lugar de 1, porque
  un pool de procesos filtrado envenena todo lo que viene después.

**Los bloqueos por `merge_conflict` y `flaky_unresolved` quedan excluidos por
completo** — ni suman ni reinician la racha. Señalan contención de la cola
sobre archivos compartidos, no un entorno roto, y dejar que disparen el
breaker pausaría una ejecución perfectamente sana.

Un breaker disparado queda `PAUSED`, no `STOPPED`, y **reanudarlo requiere un
humano**. Ese es el punto: la ejecución se detuvo porque algo necesita que
una persona lo revise, y reanudar automáticamente frustraría el propósito.
Cuando ya lo hayas resuelto:

```bash
cosmo run resume            # la ejecución pausada más reciente
cosmo run resume <run_id>
```

Nótese que una pausa del breaker detiene la ejecución *completa*, ramas
independientes del DAG incluidas. Cuando el entorno es sospechoso, "seguir
ejecutando las ramas que todavía no fallaron" es una apuesta a que el fallo
es local — que es exactamente lo que el breaker acaba de concluir que no es.

## Detección de cuota

Las ventanas de rate-limit son la restricción que realmente afecta a una
ejecución nocturna facturada por suscripción. Cosmo detecta el agotamiento
de tres maneras, en orden descendente de confianza.

**1. Primaria — la señal estructurada propia del harness.** El adaptador de
Claude extrae una señal de rate-limit del stream de salida de la CLI, que
entrega una ventana (`five_hour` o `weekly`) y, cuando el canal la incluye,
una hora de reinicio. Confirmada. Solo es procesable en una llamada
*fallida*: una señal de rate-limit vista a mitad de stream no significa que
la llamada haya fallado — el reintento interno propio de la CLI a menudo la
absorbe y la llamada de todos modos tiene éxito.

**2. Secundaria — el subtipo de error del resultado final**, comparado
contra `quota.result_error_subtypes` (por defecto `["error_rate_limit"]`).
También se trata como confirmada. Este valor por defecto todavía no tiene
ninguna captura verificada detrás — es configurable precisamente para que se
pueda corregir el día en que se observe una real, en lugar de estar fijado
sobre una suposición.

**3. Terciaria — una heurística de reloj de pared.**
`heuristic_consecutive_threshold` tareas distintas (por defecto 3) que
fallan en menos de `heuristic_max_duration_seconds` (por defecto 5) con cero
llamadas a herramientas ejecutadas. Nunca se reporta como confirmada, y nunca
se le permite concluir `weekly` — no hay forma de inferir una ventana
semanal solo a partir del tiempo transcurrido, así que una señal no
confirmada siempre se trata como el caso de cinco horas, más corto y más
seguro.

### Qué sucede ante una señal

Una ventana de **cinco horas** pausa la ejecución y programa una reanudación
automática: en la hora de reinicio reportada, o en
`quota.default_5h_resume_delay_seconds` (por defecto 18000, es decir 5
horas) cuando la señal no lleva hora de reinicio — la forma observada del
canal a menudo no la incluye.

Un agotamiento **semanal** pausa o detiene según si el presupuesto restante
de la ejecución podría sobrevivirlo. Una semana no es algo para sentarse a
esperar.

### Cómo saltarse la pausa de cinco horas

Algunas cuentas tienen créditos de uso que mantienen las llamadas
funcionando más allá de la asignación de suscripción incluida.
`quota.bypass_5h_with_credits = true` opta por gastarlos: la ejecución
continúa más allá de una señal confirmada de cinco horas y emite un evento
`quota.bypassed` de severidad `warning` que lleva la hora de reinicio y el
gasto acumulado hasta el momento.

**Esto requiere un `cost.max_cost_per_run_usd` distinto de cero.** Cosmo se
niega a cargar una configuración con el bypass activado y sin techo de
gasto — el bypass existe para eliminar lo que de otro modo detendría el
gasto, así que no puede publicarse sin el respaldo que lo recrea.

## Costo

Dos techos independientes, ambos con valor por defecto `0.0`, lo cual
significa *sin freno duro* — la postura correcta para un harness facturado
por suscripción, donde las ventanas de cuota gobiernan en lugar de los
dólares.

- **`cost.max_cost_per_run_usd`** — la ejecución completa. Un evento
  `run.cost_warning` se dispara en `cost.warn_at_fraction` (por defecto
  80%); alcanzar el techo detiene la ejecución con `cost_limit_reached`.
- **`cost.max_cost_per_task_usd`** — una sola tarea. Excederlo bloquea la
  tarea con `blocked_reason=cost` y continúa, en lugar de detener la
  ejecución.

Configura ambos si estás en facturación medida.

Una tarea bloqueada por costo tiene una propiedad útil: solo puede
desbloquearse legítimamente si un humano sube el techo, ya que el costo
registrado nunca baja. Así que al inicio de cada ejecución, Cosmo reevalúa
cada tarea bloqueada por `cost` contra la configuración *actual* y
desbloquea las que ya no exceden el límite — preservando su contador de
intentos y su worktree, porque nada en la tarea misma falló. Cada una emite
`task.cost_requeued`.

## Disco

`disk.min_free_gb` (por defecto 10) se verifica una vez, al iniciar la
ejecución. Por debajo de ese valor, la ejecución aborta de inmediato con
`disk_low` y severidad `critical`.

La alternativa es peor de lo que suena: un disco que se llena a mitad de
ejecución hace fallar cada tarea subsiguiente con errores de I/O que se leen
exactamente como errores de código — así que el agente intenta "arreglarlos",
los reintentos queman presupuesto, y el circuit breaker eventualmente se
dispara por una razón completamente equivocada.

Los worktrees, las imágenes Docker y los logs del harness son lo que llena
el disco. La retención de logs es automática (`log_retention.done_days` /
`blocked_days`); las imágenes Docker son tuyas para podar.

## Timeouts y muertes de proceso

Cada estado tiene un reloj de pared. `IMPLEMENTING` y `VALIDATING` también
tienen temporizadores de estancamiento, y la configuración se niega a
cargar si un temporizador de estancamiento está fijado a más tiempo que su
propio reloj de pared — un temporizador de estancamiento que nunca puede
dispararse desactiva silenciosamente la única protección contra un harness
colgado.

Un timeout mata al **grupo de procesos entero**: SIGTERM,
`timeouts.kill_grace` segundos (por defecto 20), SIGKILL. Luego un barrido
elimina los contenedores del gate por sus etiquetas `orchestrator.run_id` /
`orchestrator.task_id` y verifica si quedan procesos manteniendo el worktree
abierto.

Equivocarse en esto sale caro de una forma que se nota horas después: un
padre matado cuyos hijos de Maven, Node, Chromium o Docker sobreviven deja
un host llenándose lentamente de huérfanos hambrientos de memoria hasta que
cada tarea posterior falla por razones que no tienen nada que ver con su
propio código.

`cosmo doctor` reporta contenedores del gate filtrados como comprobación
central, así que el desorden de una ejecución anterior es visible antes de
que comience la siguiente.

## Recuperación ante crash

Cosmo es estrictamente serial y de un solo proceso, así que una tarea
encontrada en cualquier estado no terminal al iniciar solo puede significar
que el proceso que la conducía murió.

Al inicio de cada ejecución:

- Cada tarea en pleno vuelo se emite como `task.interrupted` (`warning`) y
  se vuelve a encolar.
- Una fila de `run_state` todavía marcada como `running` se cierra como
  `crashed`.
- Las tareas bloqueadas por costo se reevalúan contra el techo actual.
- Los worktrees obsoletos de ejecuciones terminadas se barren.

## El modelo de permisos

Específico del adaptador de Claude Code, aunque la postura se generaliza.

- **`dontAsk` falla cerrado.** Solo se ejecutan las llamadas a herramientas
  que coinciden con la lista de permitidos. Nada que no esté explícitamente
  permitido se ejecuta — el valor por defecto es la denegación, no el
  permiso.
- **`bypassPermissions` nunca se usa.** No simplemente se omite:
  `--dangerously-skip-permissions` y `bypassPermissions` se afirman ausentes
  del argv construido, y un test separado lo verifica desde afuera. El host
  guarda credenciales reales; el radio de impacto no es cero.
- **Las reglas de denegación son absolutas** y aplican en todos los modos.
  Las rutas con forma de secreto (`.env*`, `secrets/**`, `*.pem`, `id_rsa*`)
  y las herramientas de programación y reanudación se deniegan por completo.
- **Solo se cargan los ajustes del proyecto** (`--setting-sources project`).
  El `~/.claude` global del operador — hooks personales arbitrarios,
  plugins y servidores MCP de costo y efectos secundarios desconocidos — no
  se incorpora a una ejecución desatendida.
- **La lista de permitidos se pasa tanto en la línea de comandos como en
  `settings.json`.** Claude Code tiene una compuerta de confianza de
  espacio de trabajo (workspace trust): en un directorio que nunca pasó por
  el diálogo interactivo de confianza — algo que un worktree recién creado
  por tarea nunca puede hacer — ignora silenciosamente cada entrada de
  `permissions.allow` de `settings.json` y deniega `Write`/`Edit`/`Bash`,
  sin mostrar nada que el adaptador pueda ver. Pasar la misma lista como un
  flag de CLI no se ve afectado por la confianza de espacio de trabajo.
- **`ANTHROPIC_API_KEY` se elimina** del entorno del proceso hijo, y `cosmo
  doctor` falla directamente si está definida en el host. Su presencia
  cambia silenciosamente la facturación de la suscripción a tarifas de API
  por token — algo caro de descubrir después de una noche desatendida.
- **La telemetría está activada, el registro de contenido está
  explícitamente desactivado.** `OTEL_LOG_USER_PROMPTS=0` se fija en lugar
  de confiarse al valor por defecto: los prompts y el contenido de archivos
  en un backend de telemetría son una vía de exfiltración de datos para un
  código base privado.

Modelo de amenazas completo: [SECURITY.md](../../../SECURITY.md).

## Notificaciones

Cosmo puede avisarte cuando ocurre cualquiera de las situaciones anteriores,
por hoy a través de Telegram.

```bash
cosmo notify config    # interactivo: token, descubrimiento de chat id, mensaje de prueba real
```

`cosmo notify watch` sondea la tabla de eventos y reenvía cualquier cosa en
o por encima de `notify.min_severity` (por defecto `warning`), más
`task.completed` incondicionalmente.

Se ejecuta como su **propio proceso**, nunca en línea dentro de `cosmo run`.
Eso no es prolijidad arquitectónica — un receptor viviendo dentro del run
loop no puede reportar el propio crash del run loop, porque lo que enviaría
ese mensaje muere junto con él. El watcher también dispara su propia alerta
cuando la tabla de eventos queda en silencio durante
`notify.stale_after_seconds` mientras la ejecución no está en un estado
terminal, lo cual es la única señal que detecta un proceso de ejecución que
murió sin decir nada.
