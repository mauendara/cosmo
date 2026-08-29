# Referencia de configuración

> Nota: esta traducción puede no estar actualizada. El inglés es la fuente canónica de esta documentación — consulta la [versión en inglés](../../en/reference/config-schema.md).

Cada clave que Cosmo lee, con su valor por defecto incluido.

## De dónde viene la configuración

Tres capas, de menor a mayor precedencia:

1. `src/cosmo/config/defaults.toml`, incluido en el paquete.
2. Un archivo de configuración de usuario: `$COSMO_CONFIG` si está definida,
   o si no, `$XDG_CONFIG_HOME/cosmo/config.toml` (es decir,
   `~/.config/cosmo/config.toml`).
3. Sobrescrituras explícitas pasadas por la CLI (`--config` apunta a un
   archivo distinto para la capa 2; no agrega una cuarta capa).

El apilamiento es un deep merge por tabla, así que un archivo de usuario
solo necesita las claves que cambia.

```bash
cosmo config show          # la configuración totalmente resuelta
cosmo config show --paths  # solo dónde viven config, state, work y logs
```

La validación ocurre al momento de cargar. Un valor incorrecto hace fallar
el comando inmediatamente (código de salida `2`) en lugar de a mitad de una
ejecución a las 3am. Las claves desconocidas se rechazan — el modelo prohíbe
extras, así que una clave mal escrita es un error, no un no-op silencioso.

---

## `[harness]`

El único lugar en el núcleo de Cosmo que nombra un harness específico.

| Clave | Tipo | Por defecto | Descripción |
| --- | --- | --- | --- |
| `name` | string | `"claude"` | Qué adaptador usar. Orden de resolución: flag `--harness` → registro del proyecto → esta clave. |
| `permission_mode` | string | `"dontAsk"` | Postura de permisos pasada al harness. El adaptador de Claude acepta `dontAsk` o `auto`, y rechaza `bypassPermissions` de plano. |
| `max_turns` | int > 0 | `80` | Tope de turnos por llamada al harness. |
| `model` | string | `"claude-sonnet-5"` | Fijado para que el modelo de una ejecución no varíe según lo que la CLI del host tenga por defecto en cada momento. |

## `[timeouts]`

Todos los valores en segundos, todos deben ser > 0.

| Clave | Por defecto | Descripción |
| --- | --- | --- |
| `proposing_wall` | `900` | Reloj de pared para la llamada de propose. También el `--timeout` por defecto de `harness probe` y `spec add`. |
| `implementing_wall` | `5400` | Reloj de pared para la llamada de implement. |
| `implementing_stall` | `1200` | Sin actividad observada durante este tiempo en `IMPLEMENTING` termina la llamada. |
| `validating_wall` | `2700` | Reloj de pared para `VALIDATING`. |
| `validating_stall` | `600` | Temporizador de estancamiento para `VALIDATING`. |
| `reviewing_wall` | `900` | Reloj de pared para la llamada de revisión adversarial. Una sola llamada acotada, así que no hay variante de estancamiento. |
| `committing_wall` | `300` | Reloj de pared para `COMMITTING`. |
| `merging_wall` | `300` | Reloj de pared para `MERGING`. |
| `run_wall` | `36000` | Reloj de pared de toda la ejecución (10 horas). Al expirar, detiene la ejecución con `max_time`. |
| `kill_grace` | `20` | Segundos entre `SIGTERM` y `SIGKILL` sobre el grupo de procesos. |

**Validado**: `implementing_stall` debe ser menor que `implementing_wall`, y
`validating_stall` menor que `validating_wall`. Un temporizador de
estancamiento que sobrevive a su reloj de pared nunca puede dispararse,
desactivando silenciosamente la única protección contra un harness colgado.

## `[retries]`

| Clave | Tipo | Por defecto | Descripción |
| --- | --- | --- | --- |
| `max_attempts` | int > 0 | `2` | Intentos a nivel de código antes de que una tarea se bloquee. Con el valor por defecto, el tercer fallo a nivel de código bloquea. |
| `delay_min` | int ≥ 0 | `30` | Límite inferior del retraso aleatorizado entre reintentos, en segundos. |
| `delay_max` | int ≥ 0 | `60` | Límite superior. |
| `repeat_block_threshold` | int > 0 | `2` | `cosmo queue retry` se rehúsa una vez que el bloqueo terminal más reciente de la tarea coincide con esta cantidad de bloqueos previos por el mismo motivo. `--force` lo anula. |

**Validado**: `delay_min` no debe superar a `delay_max`.

Solo los intentos que representan un juicio genuino a nivel de código
incrementan el contador — un veredicto del gate de `code_error` o
`test_integrity`, o un timeout de `IMPLEMENTING`. Un `environment_error`
nunca lo hace.

## `[circuit_breaker]`

| Clave | Tipo | Por defecto | Descripción |
| --- | --- | --- | --- |
| `consecutive_blocked_threshold` | int > 0 | `3` | Tareas distintas que se bloquean consecutivamente antes de que la ejecución se pause. Una tarea `DONE` reinicia la racha. |
| `environment_error_threshold` | int > 0 | `3` | Peso acumulado de errores de entorno entre tareas distintas antes de que la ejecución se pause. |
| `reap_failure_weight` | int > 0 | `2` | Peso que aporta un fallo de reap de proceso. Un pool de procesos filtrado envenena cada tarea posterior, así que dispara el breaker más rápido. |

Los bloqueos por `merge_conflict` y `flaky_unresolved` se excluyen por
completo del conteo consecutivo — señalan contención de cola sobre archivos
compartidos, no un entorno roto.

## `[cost]`

| Clave | Tipo | Por defecto | Descripción |
| --- | --- | --- | --- |
| `max_cost_per_run_usd` | float ≥ 0 | `0.0` | Tope duro para una ejecución. `0.0` significa sin tope duro — la postura correcta para un harness facturado por suscripción. |
| `max_cost_per_task_usd` | float ≥ 0 | `0.0` | Tope duro por tarea. `0.0` lo desactiva. |
| `warn_at_fraction` | float en (0, 1] | `0.8` | Fracción de `max_cost_per_run_usd` en la que se dispara un evento `run.cost_warning`. |

Una tarea bloqueada por `cost` se reevalúa contra el tope *actual* al inicio
de la siguiente ejecución y se desbloquea automáticamente si un humano lo
elevó o lo desactivó mientras tanto.

## `[gate]`

| Clave | Tipo | Por defecto | Descripción |
| --- | --- | --- | --- |
| `playwright_image` | string | `"mcr.microsoft.com/playwright:v1.49.0-noble"` | Imagen de la etapa e2e. |
| `playwright_npm_version` | string | `"1.49.0"` | La versión de `@playwright/test` a la que tu repositorio debe fijarse para coincidir con los binarios de navegador de la imagen. |
| `shm_size` | string | `"2gb"` | `--shm-size` en cada contenedor del gate. |
| `ipc_host` | bool | `true` | `--ipc=host` en cada contenedor del gate. |
| `backend_image` | string | `"maven:3.9.9-eclipse-temurin-21"` | Imagen de build/test del backend. |
| `backend_dir` | string | `"backend"` | Directorio del backend, relativo a la raíz del worktree. Si está ausente, se omiten las etapas de backend. |
| `frontend_image` | string | `"node:24.19-bookworm"` | Imagen de build/test del frontend. |
| `frontend_dir` | string | `"frontend"` | Directorio del frontend, relativo a la raíz del worktree. Si está ausente, se omite la etapa e2e. |
| `stage_timeout_seconds` | int > 0 | `1800` | Presupuesto de `docker run` por etapa serial (build, unit, e2e). Distinto de `timeouts.validating_wall`. |
| `diff_gate_test_path_patterns` | list[string], no vacía | ver abajo | Patrones glob que identifican archivos de test en el diff. |
| `diff_gate_skip_annotations` | list[string], no vacía | ver abajo | Subcadenas que marcan un test como omitido o deshabilitado. |
| `diff_gate_loc_drop_threshold` | int > 0 | `20` | Líneas netas eliminadas de un archivo de test antes de que el diff gate lo marque. |
| `flaky_rerun_limit` | int > 0 | `3` | Reintentos aislados de un test e2e fallido no puesto en cuarentena antes de considerarlo un fallo real. |
| `flaky_quarantine_candidate_threshold` | int > 0 | `3` | Clasificaciones como flaky en *ejecuciones distintas* antes de que un test se agregue al archivo de candidatos para revisión humana. |
| `quarantine_file` | ruta o null | `null` | Ruta a `quarantine.yml`. `null` usa la copia incluida con Cosmo. |
| `quarantine_candidates_file` | ruta o null | `null` | Ruta a `quarantine-candidates.yml`. `null` usa la copia incluida. |
| `error_detail_max_chars` | int > 0 | `4000` | Tope sobre el `error_detail` almacenado, para que siga siendo consumible por el modelo en lugar de archivístico. |

**Validado**: `playwright_image` debe estar fijado a un tag explícito. Un
tag `:latest`, o ningún tag, se rechaza — una actualización silenciosa
upstream convierte una suite verde en roja de la noche a la mañana y se
manifiesta como una regresión fantasma que el agente intentará "arreglar".

Valores por defecto para las dos claves de tipo lista:

```toml
diff_gate_test_path_patterns = [
    "**/src/test/**",
    "**/*.test.*",
    "**/*.spec.*",
    "**/e2e/**",
]
diff_gate_skip_annotations = [
    "@Disabled", "@Ignore", ".skip(", ".only(",
    "xit(", "xdescribe(", "test.skip(", "describe.skip(",
]
```

## `[knowledge]`

| Clave | Tipo | Por defecto | Descripción |
| --- | --- | --- | --- |
| `max_file_lines` | int > 0 | `400` | Tope de líneas en cada archivo de conocimiento `docs/**/*.md` que una tarea toca. Superarlo hace fallar `COMMITTING` y vuelve a `IMPLEMENTING`. La compactación nunca es automática. |

## `[review]`

| Clave | Tipo | Por defecto | Descripción |
| --- | --- | --- | --- |
| `enabled` | bool | `true` | La revisión adversarial de sesión fresca entre `VALIDATING` y `COMMITTING`. `false` omite `REVIEWING` por completo. Una revisión rechazada se reintenta contra `retries.max_attempts`, no un presupuesto separado. |

## `[progress]`

| Clave | Tipo | Por defecto | Descripción |
| --- | --- | --- | --- |
| `poll_interval_seconds` | int > 0 | `7` | Intervalo para observar el `tasks.md` del cambio (y para el sondeo de progreso nativo en un adaptador que lo reporte). |

## `[quota]`

| Clave | Tipo | Por defecto | Descripción |
| --- | --- | --- | --- |
| `result_error_subtypes` | list[string], no vacía | `["error_rate_limit"]` | Subtipos de error terminal de resultado tratados como agotamiento de cuota (detección secundaria). |
| `heuristic_consecutive_threshold` | int > 0 | `3` | Tareas distintas consecutivas que fallan casi instantáneamente con cero llamadas a herramientas antes de que se dispare la heurística de reloj de pared. |
| `heuristic_max_duration_seconds` | float > 0 | `5.0` | Qué cuenta como "casi instantáneamente". |
| `default_5h_resume_delay_seconds` | int > 0 | `18000` | Retraso de reanudación cuando una señal confirmada de cinco horas no trae hora de reinicio. |
| `bypass_5h_with_credits` | bool | `false` | No pausar ante una señal confirmada de cinco horas — seguir gastando créditos de uso más allá de la asignación incluida. |

**Validado**: `bypass_5h_with_credits = true` requiere un
`cost.max_cost_per_run_usd` distinto de cero. Cosmo se rehúsa a iniciar en
caso contrario — el bypass no debe existir sin el tope de gasto que crea la
necesidad de él.

La heurística de reloj de pared nunca se reporta como una señal confirmada,
y siempre se trata como la ventana de cinco horas más corta y segura; no hay
forma de inferir una ventana semanal solo a partir del tiempo.

## `[notify]`

| Clave | Tipo | Por defecto | Descripción |
| --- | --- | --- | --- |
| `enabled` | bool | `false` | Interruptor maestro. `cosmo notify watch` se rehúsa a iniciar cuando es falso. |
| `telegram_bot_token` | string o null | `null` | Token del bot. Mantenlo en el archivo de configuración de usuario, con `chmod 600`, fuera de cualquier repositorio. |
| `telegram_chat_id` | string o null | `null` | Chat de destino. |
| `min_severity` | `info` \| `warning` \| `error` \| `critical` | `"warning"` | Piso de severidad para el reenvío. `task.completed` siempre se notifica sin importar esto. |
| `stale_after_seconds` | int > 0 | `1800` | Sin nueva actividad a nivel de ejecución durante este tiempo, con la ejecución fuera de un estado terminal, se trata en sí misma como una señal de crash. |

`cosmo notify config` escribe esta tabla por ti y envía un mensaje de prueba
real antes de declarar éxito.

## `[disk]`

| Clave | Tipo | Por defecto | Descripción |
| --- | --- | --- | --- |
| `min_free_gb` | float > 0 | `10.0` | Verificado antes de que inicie cada ejecución. Por debajo de esto, la ejecución aborta con `disk_low` en lugar de hacer fallar cada tarea a mitad de camino. |

## `[log_retention]`

| Clave | Tipo | Por defecto | Descripción |
| --- | --- | --- | --- |
| `done_days` | int > 0 | `7` | Cuánto tiempo se conservan los logs crudos del harness de una tarea `DONE`. |
| `blocked_days` | int > 0 | `30` | Cuánto tiempo se conservan los logs de una tarea `BLOCKED`. |

Determinado por el estado *actual* de la tarea, no por el estado al momento
en que se escribió cada archivo — una tarea que más tarde llega a `DONE`
hace que los logs de sus intentos anteriores expiren en la ventana más
corta.

## `[git]`

| Clave | Tipo | Por defecto | Descripción |
| --- | --- | --- | --- |
| `base_branch` | string | `"develop"` | La rama de integración del **repositorio objetivo**, y la única rama a la que Cosmo hace merge automáticamente alguna vez. No relacionada con la rama del propio repositorio de Cosmo. Hacer merge a `main`/`master` siempre es manual. |
| `commit_author_name` | string | `"Cosmo"` | Identidad para los commits que Cosmo hace por su cuenta (escalera de merge, log de decisiones). También la identidad local por defecto de `cosmo init` para un repositorio objetivo sin ninguna configurada. |
| `commit_author_email` | string | `"cosmo@entropiainversa.com"` | Ver arriba. |
| `unified_identity` | bool | `false` | `false`: los commits propios de Cosmo usan la identidad de arriba, visiblemente distinta de los commits de código de la aplicación. `true`: los commits propios de Cosmo heredan la configuración local de git del repositorio — una sola identidad para cada commit. |

Cosmo pasa su identidad por invocación (`-c user.name=...`), nunca
escribiendo en la configuración global de git.

## `[paths]`

Los valores por defecto se calculan al momento de cargar a partir del
esquema XDG, no se incluyen en `defaults.toml`, porque dependen del host.

| Clave | Tipo | Por defecto | Descripción |
| --- | --- | --- | --- |
| `data_dir` | ruta | `$XDG_DATA_HOME/cosmo` (es decir, `~/.local/share/cosmo`) | Raíz del estado. La base de datos SQLite vive en `<data_dir>/cosmo.db`. |
| `work_dir` | ruta | `<data_dir>/work` | Dónde se crean los worktrees de las tareas, como `<work_dir>/<run_id>/<task_id>`. |
| `log_dir` | ruta | `<data_dir>/logs` | Logs crudos del harness, rotados según `[log_retention]`. |

La ruta de la base de datos se deriva de `data_dir` y no es configurable por
separado.

Un despliegue en servidor típicamente apunta las tres a algo como
`/var/cosmo`:

```toml
[paths]
data_dir = "/var/cosmo"
work_dir = "/var/cosmo/work"
log_dir  = "/var/cosmo/logs"
```

Bajo WSL2, mantén `work_dir` fuera de `/mnt/c`. `cosmo doctor` advierte
sobre esto: los builds en el puente 9p son lo bastante lentos como para
distorsionar cada timeout de arriba.

---

## Una configuración mínima de usuario

```toml
# ~/.config/cosmo/config.toml

[git]
base_branch = "develop"

[cost]
max_cost_per_run_usd = 25.0

[notify]
enabled = true
telegram_bot_token = "..."
telegram_chat_id = "..."
min_severity = "info"
```

```bash
chmod 600 ~/.config/cosmo/config.toml
```
