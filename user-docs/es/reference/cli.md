# Referencia de la CLI

> Nota: esta traducción puede no estar actualizada. El inglés es la fuente canónica de esta documentación — consulta la [versión en inglés](../../en/reference/cli.md).

Generado a partir del árbol de comandos publicado. Cada comando y flag a
continuación es lo que `cosmo --help` realmente expone.

## Global

```
cosmo [OPTIONS] COMMAND [ARGS]...
```

| Opción | Descripción |
| --- | --- |
| `--version` | Imprime la versión y sale. |
| `--help` | Muestra la ayuda y sale. |

### Opciones comunes

Estas se repiten entre comandos en lugar de ser globales:

| Opción | Aplica a | Descripción |
| --- | --- | --- |
| `--config`, `-c <path>` | todos los comandos excepto `harness list`, `templates list` | Archivo de configuración superpuesto sobre los valores por defecto incluidos. Solo para `notify config`, esta es además *la ruta donde se escribe*. |
| `--harness <str>` | `doctor`, `init`, `harness probe`, `spec add`, `project register`, `run`, `run resume` | Sobrescribe el harness resuelto para esta invocación. |
| `--repo <path>` | `spec add`, `spec queue`, `queue retry`, `run`, `run resume` | Repositorio objetivo. Por defecto, el directorio actual. |

Orden de resolución del harness: flag `--harness` → registro del proyecto →
`harness.name` de la configuración.

### Códigos de salida

| Código | Significado |
| --- | --- |
| `0` | Éxito. Para `cosmo run`, solo una detención `completed` o `queue_empty`. |
| `1` | La operación falló, o la ejecución se detuvo/pausó por cualquier otro motivo (circuit breaker, cuota, límite de costo, disco, `blocked_remaining`). |
| `2` | La configuración no pudo cargarse o validarse. |

---

## `cosmo doctor`

Verifica que este host pueda ejecutar Cosmo. Reporta las verificaciones
principales (agnósticas del harness) y las del harness por separado. Sale
con código distinto de cero si alguna verificación es bloqueante.

| Opción | Descripción |
| --- | --- |
| `--config`, `-c <path>` | Archivo de configuración. |
| `--harness <str>` | Sobrescribe el harness. |
| `--project-path <path>` | Un repositorio objetivo registrado — provee el nivel de proyecto de la resolución del harness. |

Verificaciones principales: `python`, `git`, `docker`, `openspec`,
`gitleaks`, `disk space`, `state dirs writable`, `work dir filesystem`,
`event/state store`, `leaked gate containers`.

Verificaciones del adaptador de Claude: `claude cli`, `subscription billing`
(falla si `ANTHROPIC_API_KEY` está configurada), `permission mode`.

## `cosmo init TARGET_PATH`

Inicializa un repositorio objetivo: `git init` y la rama base si hace falta,
`openspec/`, `docs/`, `.agent/<harness>/`, symlinks en la raíz, registro del
proyecto.

| Argumento | Descripción |
| --- | --- |
| `target_path` | Ruta al repositorio objetivo. Ejecuta `git init` por su cuenta si aún no lo es. |

| Opción | Por defecto | Descripción |
| --- | --- | --- |
| `--harness <str>` | resuelto | Sobrescribe el harness. |
| `--project-template <str>` | `_blank` | Plantilla de documentación del proyecto. Ver `cosmo templates list`. |
| `--force` / `--no-force` | `--no-force` | Sobrescribe archivos de `docs/` ya presentes. Pide confirmación. |
| `--git-author-name <str>` | — | Identidad de Git a configurar localmente en el repositorio objetivo. Se combina con `--git-author-email`; si se dan ambas juntas, se salta el prompt interactivo. |
| `--git-author-email <str>` | — | Ver `--git-author-name`. |
| `--config`, `-c <path>` | — | Archivo de configuración. |

## `cosmo validate WORKTREE`

Ejecuta el gate de validación de Docker de forma independiente contra un
worktree. Es un punto de entrada de diagnóstico — nunca toca el store, así
que el worktree no necesita corresponder a una tarea encolada.

| Argumento | Descripción |
| --- | --- |
| `worktree` | Ruta al worktree a validar. |

| Opción | Por defecto | Descripción |
| --- | --- | --- |
| `--task-id <str>` | **obligatorio** | Identificador de la tarea, para las etiquetas del contenedor y la atribución. |
| `--task-branch <str>` | la rama actual del worktree | Rama bajo prueba. |
| `--base-branch <str>` | `git.base_branch` | Rama contra la cual comparar. |
| `--allow-test-edits` / `--no-allow-test-edits` | `--no-allow-test-edits` | Omite las verificaciones de integridad de tests del diff gate. |
| `--run-id <str>` | — | Solo adjunta etiquetas al contenedor del gate. |
| `--config`, `-c <path>` | — | Archivo de configuración. |

## `cosmo report`

Triaje posterior a una ejecución: la fila `run_state` de una ejecución más su
payload `run.summary` — estado, motivo de detención/pausa, conteos de
completadas y bloqueadas por motivo, costo, duración.

| Opción | Por defecto | Descripción |
| --- | --- | --- |
| `--run <str>` | ejecución iniciada más recientemente | Qué ejecución renderizar. |
| `--follow`, `-f` | desactivado | Sigue re-renderizando hasta que la ejecución alcance un estado terminal. |
| `--config`, `-c <path>` | — | Archivo de configuración. |

---

## `cosmo config`

### `cosmo config show`

Imprime la configuración resuelta.

| Opción | Descripción |
| --- | --- |
| `--paths` | Muestra solo dónde viven la configuración, el estado, el trabajo y los logs. |
| `--config`, `-c <path>` | Archivo de configuración. |

---

## `cosmo harness`

### `cosmo harness list`

Lista los adaptadores registrados y sus capacidades declaradas. Sin opciones
más allá de `--help`.

### `cosmo harness probe`

Prueba de humo del harness resuelto con un prompt en crudo. Aplica un
timeout externo desde la capa de orquestación, no dentro del adaptador.

| Opción | Por defecto | Descripción |
| --- | --- | --- |
| `--prompt <str>` | **obligatorio** | Prompt en crudo a enviar. |
| `--harness <str>` | resuelto | Sobrescribe el harness. |
| `--timeout <float>` | `timeouts.proposing_wall` | Segundos antes de cancelar. |
| `--config`, `-c <path>` | — | Archivo de configuración. |

---

## `cosmo queue`

### `cosmo queue add SPEC_PATH`

Encola un cambio de OpenSpec redactado a mano.

| Argumento | Descripción |
| --- | --- |
| `spec_path` | Ruta al cambio de OpenSpec, relativa al repositorio objetivo. |

| Opción | Por defecto | Descripción |
| --- | --- | --- |
| `--task-id <str>` | el componente final de la ruta del spec | Identificador de la tarea. |
| `--depends-on <str>` | — | Un `task_id` del cual depende esta tarea. Repetible. |
| `--priority <int>` | `0` | Desempate suave entre tareas simultáneamente elegibles; el valor más alto se ejecuta primero. Nunca invalida una dependencia. |
| `--max-attempts <int>` | `retries.max_attempts` | Presupuesto de reintentos por tarea. |
| `--allow-test-edits` / `--no-allow-test-edits` | `--no-allow-test-edits` | Evita la protección de rutas de test para esta tarea. |
| `--config`, `-c <path>` | — | Archivo de configuración. |

Los ciclos de dependencias se rechazan en el momento de encolar.

### `cosmo queue ls`

Lista las tareas encoladas: `task_id`, `status`, `attempts`, `depends_on`,
`priority`, `blocked_reason`, `spec_path`.

| Opción | Descripción |
| --- | --- |
| `--status <str>` | Filtra por estado (ver [valores de estado](#task-status-values)). |
| `--config`, `-c <path>` | Archivo de configuración. |

### `cosmo queue show TASK_ID`

Fila completa de `task_queue` para una tarea: `spec_path`, `depends_on`,
`priority`, `status`, `attempt_count`, `max_attempts`, `last_error`,
`blocked_reason`, `allow_test_edits`, `worktree_path`, `session_id`,
`created_at`, `updated_at`, `spec_batch_id`, `resume_at_stage`.

| Opción | Descripción |
| --- | --- |
| `--config`, `-c <path>` | Archivo de configuración. |

### `cosmo queue failures TASK_ID`

Historial de fallos por intento. Es la única superficie de la CLI para
`error_detail` — el texto real de las aserciones y los extractos de stack de
un fallo del gate. Los payloads de eventos nunca lo incluyen.

| Opción | Descripción |
| --- | --- |
| `--run <str>` | Acota a un `run_id`. |
| `--config`, `-c <path>` | Archivo de configuración. |

### `cosmo queue retry TASK_ID`

Restablece una tarea `blocked` a `queued`. `attempt_count` se reinicia a 0.

Si el worktree todavía conserva el commit que hizo `PROPOSING`, solo se
descarta la implementación fallida (`git reset --hard` a ese commit, luego
`git clean -fdx`) — el worktree y el cambio de OpenSpec válido sobreviven,
así que la siguiente ejecución retoma en `IMPLEMENTING`. De lo contrario, el
worktree y la rama se eliminan y la tarea comienza de nuevo.

**Protección contra bloqueos repetidos**: una tarea cuyo bloqueo más
reciente repite `retries.repeat_block_threshold` bloqueos previos por el
mismo motivo es rechazada en lugar de concedérsele silenciosamente otro
presupuesto de intentos.

| Opción | Por defecto | Descripción |
| --- | --- | --- |
| `--repo <path>` | directorio actual | Repositorio objetivo en el que vive el worktree. |
| `--force` | desactivado | Continúa más allá de la protección contra bloqueos repetidos. |
| `--config`, `-c <path>` | — | Archivo de configuración. |

### `cosmo queue block TASK_ID`

Bloquea una tarea manualmente.

| Opción | Descripción |
| --- | --- |
| `--reason <str>` | **obligatorio.** Un valor de `blocked_reason` (ver [más abajo](#blocked-reason-values)). |
| `--config`, `-c <path>` | Archivo de configuración. |

---

## `cosmo spec`

### `cosmo spec add NAME`

Enriquece y descompone `docs/specs/<name>-spec.md` en
`docs/specs/<name>-spec/tasks/*.md`, y luego imprime una vista previa. **Solo
una vista previa** — no toca la cola ni `openspec/`. Los archivos escritos
son contenido real, versionado en git, que puedes editar a mano.

Si los archivos de tareas ya existen, se te pregunta si quieres volver a
ejecutar el harness (no es gratis) o reutilizarlos.

| Argumento | Descripción |
| --- | --- |
| `name` | Nombre corto en kebab-case para este spec. |

| Opción | Por defecto | Descripción |
| --- | --- | --- |
| `--repo <path>` | directorio actual | Repositorio objetivo. |
| `--from <path>` | — | Copia este archivo como `docs/specs/<name>-spec.md` si aún no existe. |
| `--harness <str>` | resuelto | Sobrescribe el harness. |
| `--timeout <float>` | `timeouts.proposing_wall` | Segundos antes de cancelar. |
| `--config`, `-c <path>` | — | Archivo de configuración. |

### `cosmo spec queue NAME`

Inserta una tarea por cada archivo `docs/specs/<name>-spec/tasks/*.md` en la
cola, etiquetada con `spec_batch_id=<name>-spec`. Los ids de tarea y los
bordes `depends_on` dentro del lote se namespacean como `<name>-<task_id>`.
Volver a ejecutar sobre un lote ya encolado no tiene efecto.

La ventana de edición entre `spec add` y este comando es el paso de
confirmación; no existe una UI de aprobación separada.

| Argumento | Descripción |
| --- | --- |
| `name` | El nombre del spec que un `cosmo spec add` previo produjo. |

| Opción | Por defecto | Descripción |
| --- | --- | --- |
| `--repo <path>` | directorio actual | Repositorio objetivo. |
| `--config`, `-c <path>` | — | Archivo de configuración. |

---

## `cosmo events`

### `cosmo events tail`

Imprime los eventos recientes. La tabla incluye `seq`, `timestamp`,
`severity`, `event_type`, `run_id`, `task_id`.

| Opción | Por defecto | Descripción |
| --- | --- | --- |
| `--run <str>` | — | Filtra por `run_id`. |
| `--task <str>` | — | Filtra por `task_id`. |
| `--severity <str>` | — | Filtra por severidad: `info`, `warning`, `error`, `critical`. |
| `--type <str>` | — | Filtra por tipo de evento, p. ej. `task.blocked`. |
| `--payload` | desactivado | Imprime el payload JSON completo de cada evento debajo de su fila. |
| `--limit <int>` | `50` | Los N eventos más recientes. |
| `--follow`, `-f` | desactivado | Sigue consultando nuevos eventos e imprime cada uno a medida que llega. |
| `--config`, `-c <path>` | — | Archivo de configuración. |

---

## `cosmo project`

### `cosmo project register TARGET_PATH`

Registra un repositorio objetivo para que su harness pueda resolverse por
ruta, sin ejecutar un `cosmo init` completo.

| Opción | Descripción |
| --- | --- |
| `--harness <str>` | Sobrescribe el harness. |
| `--project-template <str>` | Plantilla de proyecto usada en la inicialización, si la hubo. |
| `--config`, `-c <path>` | Archivo de configuración. |

### `cosmo project list`

Lista los proyectos registrados.

| Opción | Descripción |
| --- | --- |
| `--config`, `-c <path>` | Archivo de configuración. |

---

## `cosmo templates`

### `cosmo templates list`

Nombres disponibles bajo `templates/harness/` y `templates/projects/`. Sin
opciones más allá de `--help`.

Disponibles actualmente: harness `claude`; plantillas de proyecto `_blank`,
`java-spring-react`, `vite-react-local`.

---

## `cosmo run`

Impulsa la cola de tareas. Sin `--task`, drena todo el DAG una tarea a la
vez hasta que la cola se vacía, el circuit breaker se dispara, un límite de
costo o cuota interviene, o `timeouts.run_wall` expira.

| Opción | Por defecto | Descripción |
| --- | --- | --- |
| `--repo <path>` | directorio actual | El checkout propio de Cosmo del repositorio objetivo, mantenido en la rama base. |
| `--task <str>` | — | Impulsa solo esta tarea encolada en lugar del DAG completo. |
| `--base-branch <str>` | `git.base_branch` | Rama de destino del merge. |
| `--harness <str>` | resuelto | Sobrescribe el harness. |
| `--dry-run` | desactivado | Imprime el orden de ejecución resuelto y sale. Se ignora con `--task`. |
| `--config`, `-c <path>` | — | Archivo de configuración. |

### `cosmo run resume [RUN_ID]`

Se reconecta a una ejecución `PAUSED` existente en lugar de iniciar una
nueva. La contabilidad de costos, el barrido de reconciliación de arranque y
el bloqueo de proceso aplican exactamente igual que en un `cosmo run` nuevo.

| Argumento | Por defecto | Descripción |
| --- | --- | --- |
| `run_id` | ejecución pausada más recientemente | Ejecución a reanudar. |

| Opción | Por defecto | Descripción |
| --- | --- | --- |
| `--repo <path>` | directorio actual | Checkout del repositorio objetivo. |
| `--harness <str>` | resuelto | Sobrescribe el harness. |
| `--yes` | desactivado | Omite el prompt de confirmación. |
| `--config`, `-c <path>` | — | Archivo de configuración. |

---

## `cosmo notify`

### `cosmo notify config`

Configuración interactiva de una sola vez para notificaciones de Telegram:
solicita un token de bot, descubre el chat id automáticamente (guiándote
primero a enviarle un mensaje al bot, ya que los bots no pueden escribir
primero), escribe la tabla `[notify]` de tu archivo de configuración de
usuario, y envía un mensaje de prueba real antes de declarar éxito.

| Opción | Descripción |
| --- | --- |
| `--config`, `-c <path>` | **Dónde escribir.** A diferencia del uso de solo lectura de `--config` en cualquier otro comando, una ruta que aún no existe es el caso normal de primer uso de este comando. |

### `cosmo notify watch`

El watcher siempre activo: consulta la tabla `events` y reenvía cualquier
cosa que valga la pena notificar al destino configurado. Se rehúsa a
iniciar si `notify.enabled` es falso o faltan credenciales. Se ejecuta como
su propio proceso de larga duración (`deploy/cosmo-notify.service`), nunca
integrado en `cosmo run` — un destino dentro del proceso de la ejecución no
puede reportar el propio crash del proceso de la ejecución.

| Opción | Descripción |
| --- | --- |
| `--config`, `-c <path>` | Archivo de configuración. |

---

## Valores enumerados

### Valores de estado de tarea {#task-status-values}

Usados por `queue ls --status` y reportados por `queue show`.

`queued`, `proposing`, `proposed`, `implementing`, `validating`, `reviewing`,
`committing`, `merging`, `finishing`, `done`, `failed_retry`, `blocked`

### Valores de motivo de bloqueo {#blocked-reason-values}

Usados por `queue block --reason`.

`code_failure`, `cost`, `merge_conflict`, `environment`, `timeout`,
`flaky_unresolved`

### Valores de tipo de fallo

`code_error`, `environment_error`, `timeout`, `flaky`

### Valores de etapa de fallo

`propose`, `implement`, `build`, `unit_tests`, `e2e_tests`,
`test_integrity`, `secrets`, `adversarial_review`, `commit`, `merge`

### Estado de ejecución y motivos de detención/pausa

Estado de ejecución: `idle`, `running`, `paused`, `stopped`

Motivos de detención: `completed`, `max_time`, `queue_empty`,
`cost_limit_reached`, `manual`, `quota_exhausted_weekly`, `disk_low`,
`crashed`, `blocked_remaining`

Motivos de pausa: `circuit_breaker`, `quota_exhausted_5h`,
`quota_exhausted_weekly`

## Variables de entorno

| Variable | Leída por | Efecto |
| --- | --- | --- |
| `COSMO_CONFIG` | Cosmo | Ruta al archivo de configuración de usuario, sobrescribiendo el valor por defecto de XDG. |
| `XDG_CONFIG_HOME` | Cosmo | La configuración vive en `$XDG_CONFIG_HOME/cosmo/config.toml`. |
| `XDG_DATA_HOME` | Cosmo | Los valores por defecto de `data_dir`/`work_dir`/`log_dir` derivan de `$XDG_DATA_HOME/cosmo`. |
| `NOTIFY_SOCKET` | Cosmo | Configurada por systemd; habilita los pings de disponibilidad y watchdog de `sd_notify`. |
| `ANTHROPIC_API_KEY` | Adaptador de Claude | **Debe estar sin configurar.** `cosmo doctor` falla si está presente, y el adaptador la elimina del entorno del proceso hijo — cambia la facturación de suscripción a tarifas de API por token. |
| `COSMO_TASK_ID`, `COSMO_DB_PATH` | hooks de guardrail | Configuradas por el adaptador de Claude en el proceso hijo para que los hooks `PreToolUse` puedan leer el flag `allow_test_edits` de la tarea en ejecución. No es algo que configures tú mismo. |
