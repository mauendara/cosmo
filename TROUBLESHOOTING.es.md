# Solución de problemas

[🇬🇧 English](TROUBLESHOOTING.md) | 🇪🇸 Español

> Nota: esta traducción puede no estar actualizada. El inglés es la fuente canónica de esta documentación — consulta la [versión en inglés](TROUBLESHOOTING.md).

Empieza aquí ante cualquier fallo. Luego baja hasta el síntoma específico.

## Los primeros cuatro comandos

```bash
cosmo doctor                        # el host todavía es capaz
cosmo report                        # cómo terminó la ejecución
cosmo queue ls --status blocked     # qué está atascado
cosmo queue failures <task_id>      # el texto real del error
```

`cosmo queue failures` es el que la gente pasa por alto. Es el único lugar donde
viven los mensajes de aserción reales y los extractos de stack — los payloads de
eventos llevan los *nombres* de las pruebas que fallaron, no su texto,
deliberadamente.

Para ver todo en orden, incluyendo payloads:

```bash
cosmo events tail --run <run_id> --payload
```

---

## Problemas a nivel de ejecución

### La ejecución salió con 1 pero dice que la cola está vacía

Revisa la razón de parada en `cosmo report`. Lo más probable es `blocked_remaining`:
el planificador no tenía nada más que ejecutar porque todo lo restante está
`BLOCKED`, no porque el trabajo terminó. Esa es una razón de parada distinta
precisamente para que nunca se lea como un éxito.

```bash
cosmo queue ls --status blocked
cosmo queue failures <task_id>
```

### La ejecución está en PAUSED y no continúa por sí sola

Mira la razón de la pausa:

- **`circuit_breaker`** — suficientes tareas distintas se bloquearon (o se acumuló
  suficiente peso de errores de entorno) como para que Cosmo concluyera que algo
  sistémico está mal. **Esto requiere un humano por diseño.** Arregla la causa
  subyacente, luego `cosmo run resume`.
- **`quota_exhausted_5h`** — una ventana de límite de tasa. Se reanuda sola en el
  momento de reinicio reportado, o después de
  `quota.default_5h_resume_delay_seconds`. No hay nada que hacer salvo esperar.
- **`quota_exhausted_weekly`** — una semana no es algo que esperar dentro de una
  ejecución. Reanuda manualmente cuando la ventana realmente se haya reiniciado.

```bash
cosmo run resume              # la más recientemente pausada
cosmo run resume <run_id>
```

### El circuit breaker no deja de activarse

Se activa con `consecutive_blocked_threshold` tareas distintas bloqueándose
seguidas, o peso acumulado de errores de entorno. Mira *por qué*:

```bash
cosmo events tail --type task.blocked --payload
```

Si las razones de bloqueo son todas iguales, es una causa, no tres fallos. Las más
comunes: Docker no disponible o sin espacio en disco, el CLI del harness roto o con
límite de tasa alcanzado, o una plantilla de proyecto a la que le falta una
restricción que cada tarea redescubre.

Los bloqueos `merge_conflict` y `flaky_unresolved` nunca cuentan para el conteo
consecutivo, así que si se está activando a pesar de eso, algo más está mal.

### La ejecución se detuvo con `cost_limit_reached`

Se alcanzó `cost.max_cost_per_run_usd`. Súbelo e inicia una ejecución nueva. Ten en
cuenta que una ejecución nueva reinicia todos los contadores desde cero — por eso la
unidad de systemd deliberadamente *no* se reinicia automáticamente ante este código
de salida.

Si tareas individuales se están bloqueando por `cost`, ese es el tope por tarea. Sube
`cost.max_cost_per_task_usd` y simplemente ejecuta de nuevo: en cada arranque, Cosmo
reevalúa las tareas bloqueadas por costo contra la configuración *actual* y desbloquea
las que ya no exceden el límite, preservando su conteo de intentos y su worktree.
Verás eventos `task.cost_requeued`.

### La ejecución se detuvo con `disk_low` antes de hacer nada

Por debajo de `disk.min_free_gb` (10 GB por defecto) al arranque. Este abandono es
deliberado: un disco que se llena a mitad de una ejecución hace fallar cada tarea
posterior con errores de E/S que se leen exactamente como errores de código, así que
el agente intenta "arreglarlos", los reintentos agotan presupuesto, y el circuit
breaker se activa por la razón equivocada.

Recupera espacio:

```bash
docker system prune -a                    # las imágenes del gate son grandes
du -sh ~/.local/share/cosmo/work/*        # worktrees por ejecución
du -sh ~/.local/share/cosmo/logs
```

Los worktrees de ejecuciones terminadas se barren al arranque de la siguiente
ejecución. Los worktrees de tareas `BLOCKED` se conservan para inspección — elimínalos
a mano una vez que termines, o resuelve las tareas. Los logs del harness rotan según
`log_retention.done_days` (7) y `blocked_days` (30). Las imágenes de Docker son tuyas
para podar.

### La ejecución se detuvo con `max_time`

Expiró `timeouts.run_wall` (10 horas por defecto). No es un error — ajústalo a la
ventana que realmente tienes.

### La ejecución se detuvo con `crashed`, o las tareas volvieron como `task.interrupted`

El proceso de la ejecución anterior murió. Cosmo es estrictamente serial y de un solo
proceso, así que una tarea encontrada a medio camino al arranque solo puede
significar eso. La recuperación es automática: las tareas interrumpidas se vuelven a
encolar y la fila `run_state` obsoleta se cierra. El trabajo en curso se pierde.

Revisa `journalctl -u cosmo-run.service` para la causa real — OOM kill,
`wsl --shutdown`, un timeout de watchdog.

### Un segundo `cosmo run` se niega a iniciar

Por diseño — una ejecución por `data_dir`, aplicada mediante un archivo de bloqueo
(`<data_dir>/cosmo-run.lock`) que contiene el PID propietario:

```
another cosmo run (pid 4711) already holds /var/cosmo/cosmo-run.lock --
wait for it to finish, or remove the lock file if you've confirmed it's dead
```

Un bloqueo **obsoleto** — uno cuyo PID ya no está vivo — se reclama
automáticamente, así que solo ves esto cuando un proceso realmente está corriendo.
Verifica con `systemctl status cosmo-run.service` o `ps -p <pid>`. Elimina el archivo
a mano solo si el PID nombrado ha sido reutilizado por algo no relacionado.

---

## Problemas a nivel de tarea

### Una tarea está BLOCKED — ¿ahora qué?

```bash
cosmo queue show <task_id>       # estado, intentos, último error, ruta del worktree
cosmo queue failures <task_id>   # cada intento, con el detalle real del error
```

El worktree y la rama quedan en el disco. Ve a mirarlos — el estado de fallo es
exactamente como lo dejó el agente.

Una vez que hayas arreglado la causa:

```bash
cosmo queue retry <task_id> --repo /path/to/repo
```

`retry` reinicia el conteo de intentos. Si el worktree todavía tiene el commit que
hizo `PROPOSING`, solo se descarta la implementación fallida y el cambio de OpenSpec
válido sobrevive, así que la siguiente ejecución retoma en `IMPLEMENTING` sin pagar
de nuevo por el propose.

### `cosmo queue retry` se niega

El guardia de bloqueo repetido: esta tarea ya se bloqueó por la misma razón
`retries.repeat_block_threshold` veces antes. Como `retry` reinicia el contador de
intentos, nada más recuerda eso, y podrías entregarle otro presupuesto
indefinidamente sin notarlo.

Lee `cosmo queue failures <task_id>` y aborda la razón recurrente. Luego usa
`--force`. Úsalo porque un humano arregló algo, no para silenciar el mensaje.

### Bloqueada con `blocked_reason=environment`

Algo fuera del código falló: Docker no disponible, un timeout de etapa, el proceso
del harness muriendo, una llamada de revisión rota. Los errores de entorno no
consumen el presupuesto de reintentos de código, pero sí obtienen un reintento local
acotado.

```bash
cosmo doctor
docker ps -a
docker run --rm hello-world
```

### Bloqueada con `blocked_reason=merge_conflict`

La escalera de merge intentó hacer merge, encontró un conflicto, hizo rebase y volvió
a ejecutar el gate completo, y aun así no pudo aterrizarlo. El conflicto nunca se le
devuelve al agente para que lo resuelva a ciegas.

Resuélvelo tú mismo en el worktree de la tarea, o redefine el alcance de la tarea.
Estos bloqueos quedan excluidos del conteo del circuit breaker — significan
contención de cola sobre archivos compartidos, no un entorno roto. Si estás
recibiendo muchos de estos, tus tareas se superponen demasiado; agrega bordes
`depends_on` para serializar las que tocan los mismos archivos.

### Bloqueada con `blocked_reason=code_failure`

El gate falló genuinamente, `max_attempts` veces.

```bash
cosmo queue failures <task_id>
```

Mira primero `failure_stage`:

- `build` — no compila.
- `unit_tests` / `e2e_tests` — fallos de prueba reales, con el texto de aserción
  real en `error_detail`.
- `test_integrity` — el gate de diff rechazó el cambio. Ver más abajo.
- `secrets` — gitleaks encontró algo en el diff.
- `adversarial_review` — el revisor fresco lo rechazó; `error_detail` lleva la
  razón.

A menudo el arreglo está en el spec, no en el código: la tarea estaba
subespecificada, o era demasiado grande para aterrizar dentro del presupuesto de
intentos. Divídela y vuelve a encolarla.

### Bloqueada con `blocked_reason=flaky_unresolved`

Una prueba falló, se volvió a ejecutar de forma aislada `gate.flaky_rerun_limit`
veces, y falló cada vez — así que no es inestable, o es inestable de una forma que el
aislamiento no reproduce. Trátala primero como un fallo real. Si genuinamente es
inestable, agrégala a `quarantine.yml` con un responsable y un vencimiento.

### `test_integrity` — el gate de diff rechazó el cambio

Uno de: se eliminó un archivo de prueba existente, se **modificó de cualquier
forma** un archivo de prueba existente, se introdujo una anotación de skip, el
conteo neto de aserciones bajó, o un archivo de prueba perdió más de
`gate.diff_gate_loc_drop_threshold` líneas netas.

El segundo caso es el que sorprende a la gente. Agregar un archivo de prueba *nuevo*
está bien — eso es lo que un agente bien portado debería hacer. Tocar uno
*existente* es una violación sin importar si el cambio fue honesto, porque
distinguir los dos es exactamente el juicio que un agente sin supervisión no puede
hacer en nombre propio.

`cosmo queue failures` nombra cuál. Luego decide honestamente:

- **El agente manipuló las pruebas.** Funcionando como se pretende. Mejora el spec
  para que la tarea sea alcanzable sin debilitar pruebas, o divídela.
- **El cambio legítimamente elimina pruebas** (eliminando una funcionalidad,
  refactorizando una suite). Define `allow_test_edits` en esa tarea —
  `cosmo queue add --allow-test-edits`, o la clave de frontmatter.

### El agente produjo una implementación vacía y la revisión la rechazó

Casi siempre es el guardia de rutas de prueba haciendo su trabajo en una tarea cuyo
entregable completo vive bajo una ruta protegida — una suite `e2e/`, `src/test/**`,
un `*.test.tsx`. El agente correctamente se negó a escribir nada y no entregó nada.

Define `allow_test_edits: true` en el frontmatter del archivo de tarea y vuelve a
encolar.

### La etapa e2e reporta "playwright produced no report"

Tu `playwright.config.ts` no está escribiendo donde el gate lee. Esto es
indistinguible de que la suite nunca se ejecutó, por lo que falla.

```ts
reporter: [["json", { outputFile: "playwright-report/results.json" }]],
```

### E2E falla con "Executable doesn't exist at .../chrome-headless-shell"

`@playwright/test` no está fijado o es más nuevo que la imagen del gate. El gate
ejecuta `mcr.microsoft.com/playwright:v1.49.0-noble`, que solo tiene los binarios de
navegador de esa versión; un paquete más nuevo resuelve a una compilación de
navegador que el contenedor no tiene. Funciona bien en tu máquina, donde los
navegadores están instalados localmente.

```bash
npm install -D @playwright/test@1.49.0     # que coincida con gate.playwright_npm_version
```

### E2E se ejecuta pero nada carga

`playwright.config.ts` tiene codificado un puerto de localhost. El gate inicia la
aplicación compilada como un contenedor en una red privada de Docker y pasa
`BASE_URL` apuntando al hostname de ese contenedor.

```ts
use: { baseURL: process.env.BASE_URL ?? "http://localhost:4173" }
```

### El gate se saltó una etapa por completo

La selección de etapa está guiada por directorios. Sin `gate.backend_dir` → se
saltan las etapas de backend. Sin `gate.frontend_dir` → se salta e2e. Si tu
disposición de archivos difiere de `backend/` y `frontend/`, define esas claves.

Un repositorio sin backend **no** salta e2e — Playwright se ejecuta contra el
frontend solo. Eso es a propósito: pasar e2e silenciosamente sin ejecutar ninguna
prueba sería indistinguible de un repositorio sin suite.

### Una tarea está atascada en `implementing` durante horas

Verifica si realmente está trabajando:

```bash
cosmo events tail --task <task_id> --payload
```

Los eventos `task.progress` muestran subtareas completándose. `task.heartbeat`
muestra que sigue viva. Si ambos fluyen, está trabajando, solo que despacio.

Si ninguno fluye, el temporizador de estancamiento (`timeouts.implementing_stall`,
20 minutos por defecto) debería activarse y matarla. Si no lo hace, el reloj de pared
(`implementing_wall`, 90 minutos por defecto) lo hará.

Una causa clásica de "viva pero sin progreso": la sesión puso en segundo plano un
comando largo y lo está sondeando en lugar de trabajar. El hook
`background_task_guard` bloquea `run_in_background: true` en `Bash` justo para esto.
Si lo ves de todas formas, verifica que `cosmo init` realmente instaló los hooks —
`ls .agent/claude/hooks/` en el repositorio objetivo.

### Una tarea nunca inicia

```bash
cosmo run --dry-run     # is it in the resolved order at all?
cosmo queue show <task_id>
```

Si no está en el orden, un `depends_on` no cumplido la está reteniendo. El resumen de
la ejecución en `stalled_queued_tasks` lista exactamente estas. Verifica que el id de
dependencia esté escrito tal como realmente se encoló — recuerda que `spec queue`
antepone el nombre del spec a los ids.

---

## Problemas de entorno

### `cosmo doctor` falla en `subscription billing`

`ANTHROPIC_API_KEY` está definida. Elimínala. Su presencia cambia silenciosamente la
facturación de tu suscripción a tarifas de API por token. Revisa los perfiles de
shell y las propias líneas `Environment=` de la unidad de systemd.

### `cosmo doctor` falla en `docker`

`docker` no está en `PATH`, o el usuario de ejecución no está en el grupo `docker`.
Después de `usermod -aG docker <user>`, el cambio de grupo necesita una nueva sesión
de inicio (o `newgrp docker`) para surtir efecto.

### `cosmo doctor` advierte sobre `work dir filesystem`

Tu `work_dir` está en `/mnt/...` — un montaje de unidad de Windows bajo WSL2. Los
builds ahí pasan por el puente 9p y son lo bastante lentos como para distorsionar
cada timeout de tu configuración. Muévelo dentro del sistema de archivos de WSL2.
Ver [setup-wsl2](user-docs/es/how-to/setup-wsl2.md).

### `cosmo doctor` reporta contenedores del gate filtrados

Los contenedores de una ejecución anterior sobrevivieron. Limpia antes de empezar:

```bash
docker ps -a --filter label=orchestrator.run_id
docker rm -f $(docker ps -aq --filter label=orchestrator.run_id)
```

Si sigue pasando, la terminación por grupo de procesos no se está completando —
revisa si hay eventos `task.failed` con `circuit_breaker_weight` en el payload, que
es la señal de fallo de recolección.

### No se encuentran las plantillas

```
Cosmo's templates/ directory was not found at .../lib/python3.14/templates.
This requires an editable install (`uv tool install --editable .`) from a
full checkout of Cosmo's own repository.
```

Exactamente lo que dice. Las plantillas viven en el repositorio, no en el wheel
instalado:

```bash
cd /path/to/cosmo/checkout
uv tool install --editable .
```

### La configuración falla al cargar (código de salida 2)

El error nombra la clave y la restricción. Las recurrentes:

- **Un temporizador de estancamiento igual o mayor que su reloj de pared.**
  Rechazado, porque un temporizador de estancamiento que nunca puede activarse
  desactiva silenciosamente la única protección contra un harness colgado.
- **`playwright_image` sin fijar.** `:latest` o un nombre de imagen desnudo se
  rechaza; una actualización silenciosa upstream convierte una suite verde en roja
  de la noche a la mañana y se presenta como una regresión fantasma.
- **`bypass_5h_with_credits` sin `max_cost_per_run_usd`.** El bypass elimina lo que
  de otro modo detendría el gasto; no se envía sin el tope que lo recrea.
- **Una clave desconocida.** Los extras están prohibidos, así que un error tipográfico
  es un error en vez de un no-op silencioso. `cosmo config show` imprime lo que
  realmente está vigente.

### El archivo de cuarentena rompe el gate

```
entry 'com.example.FooTest#flakyUnderLoad' (owner 'x@y.com') expired on
2026-01-31 -- renew or remove it
```

Funcionando como está diseñado. Una entrada vencida lanza error en lugar de ser
ignorada — una cuarentena obsoleta que protege silenciosamente a una prueba muerta es
el modo de fallo que todo el mecanismo existe para prevenir. Renueva el vencimiento
con una nueva decisión de responsable, o elimina la entrada y arregla la prueba.

### Los commits de la rama de tarea fallan por identidad de git faltante

`cosmo init` configura una identidad local en el repositorio objetivo cuando ninguna
existe. Si registraste el proyecto sin un `init` completo, define una:

```bash
git -C /path/to/repo config user.name "Cosmo"
git -C /path/to/repo config user.email "cosmo@yourdomain"
```

### Los commits fallan con "gitleaks not found on PATH"

El hook de pre-commit falla en modo cerrado: sin `gitleaks` no hay commit, en lugar
de un escaneo de secretos silenciosamente omitido. Instala gitleaks. `cosmo doctor`
lo verifica, así que este es un problema visible en el preflight en lugar de una
sorpresa a mitad de ejecución.

---

## Problemas de notificaciones

### `cosmo notify watch` se niega a iniciar

`notify.enabled` es falso, o falta el token del bot o el id del chat. Ejecuta
`cosmo notify config` — escribe la tabla y envía un mensaje de prueba real antes de
declarar éxito.

### Las notificaciones se detuvieron, y no sé si la ejecución sigue viva

Para eso está `watch.stale`: la ausencia de eventos durante
`notify.stale_after_seconds` (30 minutos por defecto) mientras la ejecución no está
en un estado terminal se alerta por sí misma. Si tampoco estás recibiendo *eso*, el
proceso de vigilancia está caído:

```bash
systemctl status cosmo-notify.service
```

Es una unidad separada de `cosmo-run.service`, sin dependencia de orden entre
ambas, precisamente para poder reportar una ejecución que nunca inició o que murió
temprano.

### No estoy escuchando nada de una ejecución saludable

El valor por defecto de `notify.min_severity` es `warning`, que una ejecución limpia
puede no alcanzar nunca. Define `min_severity = "info"` si quieres el relato paso a
paso. `task.completed` siempre se notifica sin importar el umbral.

---

## Sigo atascado

Reúne esto antes de abrir un issue:

```bash
cosmo --version
cosmo doctor
cosmo config show
cosmo report --run <run_id>
cosmo queue failures <task_id>
cosmo events tail --run <run_id> --payload --limit 200
```

**Redacta antes de publicar.** Los payloads de eventos y el detalle de fallos pueden
contener rutas, nombres de rama, texto de error de tu código fuente y contenido de
archivos.
