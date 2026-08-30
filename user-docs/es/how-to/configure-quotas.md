# Cómo configurar cuotas, techos de costo y presupuestos de reintento

> Nota: esta traducción puede no estar actualizada. El inglés es la fuente canónica de esta documentación — consulta la [versión en inglés](../../en/how-to/configure-quotas.md).

Todo lo de aquí va en tu archivo de config de usuario — `$COSMO_CONFIG`, o
`~/.config/cosmo/config.toml`. Cada clave es una anulación combinada en
profundidad (deep-merged), así que solo escribes las que cambias.

```bash
cosmo config show          # lo que realmente está vigente
cosmo config show --paths  # qué archivo está leyendo
```

La config se valida al cargarse. Un valor incorrecto hace fallar el comando
con código de salida `2` en lugar de a mitad de ejecución.

---

## Si tienes una suscripción (la postura por defecto)

Deja los techos de costo en `0.0`. Significan "sin parada forzosa", lo cual
es correcto cuando los dólares no son la restricción vinculante — las
ventanas de límite de tasa sí lo son.

Lo que realmente quieres es que la ejecución se pause cuando una ventana se
agota y se reanude sola cuando esta se restablece, que es el comportamiento
por defecto. Verifica que el retraso de reanudación de respaldo coincida con
la ventana de tu plan:

```toml
[quota]
default_5h_resume_delay_seconds = 18000    # 5 hours; used when the signal carries no reset time
```

La señal primaria normalmente lleva un tiempo de restablecimiento y este
retraso no se usa. La forma observada en la señal a veces no lo lleva, que es
lo que esto cubre.

## Si tienes facturación medida (metered billing)

Configura ambos techos. Son independientes.

```toml
[cost]
max_cost_per_run_usd  = 40.0   # alcanzar esto DETIENE la ejecución
max_cost_per_task_usd = 5.0    # alcanzar esto BLOQUEA la tarea, la ejecución continúa
warn_at_fraction      = 0.75   # evento run.cost_warning al 75% del límite de la ejecución
```

- El techo de **ejecución** (run) detiene todo con `cost_limit_reached`.
  Dimensiónalo como lo que estás dispuesto a perder durante la noche, no lo
  que esperas gastar.
- El techo de **tarea** bloquea la tarea responsable y deja que la cola
  continúe. Este es el más útil de los dos en el día a día: una tarea
  patológica atascada en un problema difícil no consume todo el presupuesto
  de la noche.

Una tarea bloqueada por costo solo puede desbloquearse si una persona sube el
techo (el costo registrado nunca baja), así que al inicio de la siguiente
ejecución Cosmo reevalúa cada una de ellas contra la config *actual* y
desbloquea las que ya no superan el límite — conservando su contador de
intentos y su worktree, ya que nada de la tarea en sí falló. Verás eventos
`task.cost_requeued`.

Así que la recuperación de "lo configuré demasiado bajo" es simplemente:
súbelo y vuelve a ejecutar.

## Gastar créditos de uso más allá de la ventana de suscripción

Algunas cuentas tienen créditos que mantienen las llamadas funcionando más
allá de la asignación incluida. Para usarlos en vez de pausar:

```toml
[quota]
bypass_5h_with_credits = true

[cost]
max_cost_per_run_usd = 50.0    # OBLIGATORIO
```

Cosmo **se niega a iniciar** con el bypass activado y sin techo de gasto. El
bypass elimina lo que de otro modo detendría el gasto, así que no se envía
sin el respaldo que recrea esa protección.

Cada señal saltada emite un evento `quota.bypassed` de severidad `warning`
con el tiempo de restablecimiento de la ventana y el gasto acumulado hasta el
momento. Presta atención a esos eventos.

El agotamiento semanal nunca se salta.

## Ajustar la detección de cuotas

Tres detectores, en orden descendente de confianza. Rara vez necesitas tocar
los dos primeros.

```toml
[quota]
result_error_subtypes           = ["error_rate_limit"]   # secundario
heuristic_consecutive_threshold = 3                       # terciario
heuristic_max_duration_seconds  = 5.0                     # terciario
```

La heurística terciaria se dispara cuando esa cantidad de *tareas distintas*
falla en menos de esa cantidad de segundos sin haberse ejecutado ninguna
llamada a herramientas — la forma característica de un rechazo duro por
límite de tasa. Nunca se reporta como confirmada y nunca se le permite
concluir una ventana semanal, ya que el tiempo por sí solo no puede
distinguir una.

Si ves pausas de cuota espurias, sube `heuristic_consecutive_threshold`. Si
el agotamiento real no se está detectando y está quemando la noche en fallos
instantáneos, bájalo.

`result_error_subtypes` no tiene una captura verificada detrás de su valor
por defecto. Es configurable específicamente para que pueda corregirse el
día que observes una real — revisa `cosmo events tail --payload` después de
un agotamiento genuino.

## Presupuestos de reintento

```toml
[retries]
max_attempts           = 2
delay_min              = 30
delay_max              = 60
repeat_block_threshold = 2
```

`max_attempts = 2` significa que el **tercer** fallo a nivel de código
bloquea la tarea. Solo cuentan los juicios genuinos a nivel de código: un
veredicto del gate de `code_error` o `test_integrity`, o un timeout de
`IMPLEMENTING`. Un `environment_error` nunca consume un intento, y tampoco lo
hace un fallo confirmado como `flaky` por reejecución.

Anulaciones por tarea al momento de encolar:

```bash
cosmo queue add openspec/changes/hard-thing --task-id hard-thing --max-attempts 4
```

`repeat_block_threshold` gobierna `cosmo queue retry`. Debido a que un
reintento restablece `attempt_count` a cero, nada más recuerda que una tarea
ya se bloqueó por el mismo motivo en ejecuciones anteriores — puedes darle
otro presupuesto indefinidamente sin notarlo. Una vez que su bloqueo más
reciente coincide con esta cantidad de bloqueos previos por el mismo motivo,
`retry` se niega y te lo indica en su lugar. `--force` lo anula; úsalo
después de que una persona haya atendido la causa recurrente, no para hacer
desaparecer el mensaje.

## El circuit breaker

```toml
[circuit_breaker]
consecutive_blocked_threshold = 3
environment_error_threshold   = 3
reap_failure_weight           = 2
```

Sube `consecutive_blocked_threshold` si deliberadamente estás ejecutando un
lote donde se espera que varias tareas necesiten atención humana y prefieres
que la ejecución siga adelante con el resto. Bájalo si prefieres enterarte
temprano de que algo está sistémicamente mal.

Los bloqueos por `merge_conflict` y `flaky_unresolved` nunca cuentan para el
conteo consecutivo. No intentes compensarlos aquí.

Un circuit breaker disparado deja la ejecución en `PAUSED` y **requiere una
persona** — ese es el propósito. Reanuda con `cosmo run resume` una vez que
hayas atendido la causa.

## Timeouts

```toml
[timeouts]
proposing_wall     = 900
implementing_wall  = 5400
implementing_stall = 1200
validating_wall    = 2700
validating_stall   = 600
reviewing_wall     = 900
committing_wall    = 300
merging_wall       = 300
run_wall           = 36000
kill_grace         = 20
```

Dos reglas:

1. **Cada temporizador de stall debe ser menor que su reloj de pared
   (wall).** La carga de config se niega en caso contrario — un temporizador
   de stall que nunca puede dispararse desactiva silenciosamente la única
   protección contra un harness colgado.
2. **Si subes `implementing_wall` o `validating_wall`, sube también
   `WatchdogSec` en la unidad de systemd.** El watchdog está dimensionado
   contra el peor caso de una tarea sana; dejarlo atrás hará que systemd mate
   tareas largas perfectamente sanas.

`run_wall` es el reloj de toda la ejecución. Al expirar se detiene con
`max_time`. Configúralo según la ventana que realmente tienes — si estás
ejecutando de 22:00 a 07:00, eso es 32400, no el valor por defecto de 36000.

`kill_grace` es el intervalo de SIGTERM a SIGKILL sobre el grupo de
procesos. Subirlo le da a un harness más tiempo para cerrarse limpiamente;
bajarlo recupera un host atascado más rápido.

## Presupuestos de etapa del gate

```toml
[gate]
stage_timeout_seconds = 1800
```

Por etapa Docker (build, unit, e2e), no el gate completo. Súbelo para un
monorepo lento. Un timeout de etapa se clasifica como `environment_error`,
así que no consume el presupuesto de reintentos de la tarea.

## Disco

```toml
[disk]
min_free_gb = 20.0
```

Se verifica una sola vez al iniciar la ejecución; por debajo de ese valor la
ejecución aborta con `disk_low` antes de hacer nada. Dimensiónalo por
encima de lo que realmente consumen el worktree de una sola tarea más las
capas de Docker, con margen — el modo de fallo que esto previene (un disco
llenándose a mitad de la ejecución, haciendo fallar cada tarea con errores
que se leen como errores de código) es mucho peor que un inicio abortado.

```toml
[log_retention]
done_days    = 7
blocked_days = 30
```

Logs crudos del harness, indexados según el estado terminal actual de la
tarea.

## Desactivar la revisión adversarial

```toml
[review]
enabled = false
```

Esto elimina una llamada completa al harness por tarea — significativo tanto
en tiempo como en gasto. También elimina la única verificación que lee el
diff sin memoria de cómo fue escrito. Desactívala a sabiendas.

El presupuesto propio de la revisión es `retries.max_attempts`, compartido
con los fallos del gate, no un techo separado.

## Un ejemplo trabajado: durante la noche, con suscripción, con cautela

```toml
[timeouts]
run_wall = 30600            # 8.5 horas: 22:15 a 06:45

[retries]
max_attempts = 3            # un intento más antes de bloquear

[circuit_breaker]
consecutive_blocked_threshold = 2   # detectarlo temprano si la noche va mal

[disk]
min_free_gb = 25.0

[notify]
enabled = true
min_severity = "info"       # el default 'warning' puede quedarse callado toda la noche
telegram_bot_token = "..."
telegram_chat_id = "..."
```

```bash
chmod 600 ~/.config/cosmo/config.toml
cosmo config show           # confirma que se combinó como esperabas
```
