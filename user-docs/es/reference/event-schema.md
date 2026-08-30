# Referencia del esquema de eventos

> Nota: esta traducción puede no estar actualizada. El inglés es la fuente canónica de esta documentación — consulta la [versión en inglés](../../en/reference/event-schema.md).

Cada evento que Cosmo escribe, su envelope, y su payload.

Los eventos viven en la tabla `events` de `<data_dir>/cosmo.db` y se leen
con `cosmo events tail` (agrega `--payload` para el cuerpo JSON). El log es
de solo anexado.

## Envelope

Cada fila lleva estas columnas, sin importar el tipo:

| Campo | Tipo | Descripción |
| --- | --- | --- |
| `event_id` | string | Clave primaria. |
| `run_id` | string o null | Null para eventos fuera de una ejecución (p. ej. `agent_assets.synced` en el momento de `cosmo init`). |
| `task_id` | string o null | Null para eventos a nivel de ejecución. |
| `timestamp` | string | ISO 8601 con desfase horario, p. ej. `2026-08-29T01:54:20.259+00:00`. |
| `sequence` | int | Monotónico dentro de un ámbito — el `run_id`, o `''` para eventos sin ejecución. Escrito en la misma transacción que el evento, así que el orden sobrevive a un crash. |
| `event_type` | string | Uno de los tipos de abajo. |
| `severity` | `info` \| `warning` \| `error` \| `critical` | |
| `schema_version` | int | Actualmente `1`. Presente desde el primer día para que la tabla pueda migrar sin arqueología. |
| `payload` | objeto JSON | Específico del tipo; documentado por tipo abajo. |

---

## Eventos a nivel de ejecución

### `run.started` — `info`

| Campo | Tipo |
| --- | --- |
| `harness` | string |
| `permission_mode` | string |
| `max_turns` | int |
| `base_branch` | string |
| `run_wall_seconds` | int |
| `max_cost_per_run_usd` | float |

### `run.paused` — `warning`

Emitido para ambas causas de pausa; los campos presentes difieren.

| Campo | Tipo | Notas |
| --- | --- | --- |
| `reason` | string o null | Un motivo de pausa: `circuit_breaker`, `quota_exhausted_5h`, `quota_exhausted_weekly`. |
| `triggering_task` | string | Solo para pausas por circuit breaker. |
| `resume_delay_seconds` | number | Solo para pausas por cuota — segundos hasta la reanudación automática. |
| `confirmed` | bool | Solo para pausas por cuota. `false` significa que se disparó la heurística de reloj de pared, no una señal real de la conexión. |

### `run.resumed` — `info`

Payload vacío.

### `run.stopped` — severidad variable

`info` normalmente; `critical` para un aborto por disco o un ciclo del DAG
al arranque.

| Campo | Tipo | Notas |
| --- | --- | --- |
| `reason` | string o null | Un motivo de detención (ver abajo). |
| `detail` | string | Presente en `disk_low` — el mensaje propio de la verificación de disco. |
| `error` | string | Presente cuando un ciclo de dependencias detuvo la ejecución. |

Motivos de detención: `completed`, `max_time`, `queue_empty`,
`cost_limit_reached`, `manual`, `quota_exhausted_weekly`, `disk_low`,
`crashed`, `blocked_remaining`.

`blocked_remaining` se elige en lugar de `queue_empty` siempre que al menos
una tarea se haya bloqueado realmente durante la ejecución, así una
ejecución que terminó solo porque todo está atascado nunca se reporta como
un éxito.

### `run.summary` — `info`

El payload que `cosmo report` renderiza.

| Campo | Tipo |
| --- | --- |
| `completed` | int |
| `blocked` | int |
| `blocked_by_reason` | objeto — motivo de bloqueo → conteo |
| `requeued` | int |
| `retried` | int |
| `flaky_detected` | list[string] — ids de test |
| `repeated_merge_conflict_tasks` | list[string] |
| `knowledge_files_near_cap` | list[string] |
| `stalled_queued_tasks` | list[string] — encoladas pero no programables (dependencias no satisfechas) |
| `total_duration_seconds` | number |
| `total_cost_usd` | number |

### `run.cost_warning` — `warning`

Se dispara al alcanzar `cost.warn_at_fraction` de
`cost.max_cost_per_run_usd`.

| Campo | Tipo |
| --- | --- |
| `total_cost_usd` | float |
| `limit_usd` | float |

### `quota.bypassed` — `warning`

Una señal confirmada de cuota de cinco horas *no* provocó una pausa, porque
`quota.bypass_5h_with_credits` está activo. El operador ha optado por gastar
créditos de uso reales más allá de la asignación incluida.

| Campo | Tipo |
| --- | --- |
| `resets_at` | string o null — UTC ISO 8601 |
| `run_cost_so_far_usd` | float |

---

## Eventos a nivel de tarea

### `task.state_changed` — `info`

Emitido solo en una transición de estado real, nunca en heartbeats.

| Campo | Tipo |
| --- | --- |
| `from_state` | string o null |
| `to_state` | string |
| `attempt_number` | int |

Valores de estado: `queued`, `proposing`, `proposed`, `implementing`,
`validating`, `reviewing`, `committing`, `merging`, `finishing`, `done`,
`failed_retry`, `blocked`.

### `task.validation_result` — `info` cuando pasa, `warning` cuando no

El veredicto del gate.

| Campo | Tipo |
| --- | --- |
| `passed` | bool |
| `duration_seconds` | number |
| `unit` | objeto de etapa o null |
| `e2e` | objeto de etapa o null |
| `flaky_detected` | list[string] — ids de test reclasificados como flaky |
| `quarantined_skipped` | list[string] — ids de test excluidos por la lista de cuarentena |

Cada objeto de etapa:

| Campo | Tipo |
| --- | --- |
| `passed` | bool |
| `duration_seconds` | number |
| `passed_count` | int o null |
| `failed_count` | int o null |
| `skipped_count` | int o null |
| `failing_tests` | list[string] — ids de test |

**Los tests que fallan se nombran aquí; su texto de aserción no.** Ese
detalle vive solo en la tabla `task_failures`, accesible vía `cosmo queue
failures <task_id>`.

### `task.completed` — `info`

| Campo | Tipo |
| --- | --- |
| `rebase_attempted` | bool — si la escalera de merge necesitó su paso de rebase |

Siempre se notifica sin importar `notify.min_severity`.

### `task.blocked` — `warning`

| Campo | Tipo |
| --- | --- |
| `blocked_reason` | uno de `code_failure`, `cost`, `merge_conflict`, `environment`, `timeout`, `flaky_unresolved` |
| `note` | string o null — contexto en texto libre, cuando el sitio de bloqueo tiene alguno |
| `rebase_attempted` | bool — solo bloqueos de la escalera de merge |

### `task.failed` — `critical`

**Emitido solo por la ruta de reap de procesos.** Un fallo de tarea
ordinario se registra en la tabla `task_failures` y se expone a través de
`cosmo queue failures`, no a través de este evento.

| Campo | Tipo |
| --- | --- |
| `failure_type` | `environment_error` |
| `error_detail` | string |
| `circuit_breaker_weight` | int — `circuit_breaker.reap_failure_weight` |
| `containers_removed` | list |
| `worktree_holder_pids` | list[int] |

### `task.progress` — `info`

Leído del `tasks.md` del cambio, no de nada que el agente afirme.

| Campo | Tipo |
| --- | --- |
| `completed` | int |
| `total` | int |
| `last_label` | string o null |

El numerador y el denominador se almacenan por separado, nunca como un
porcentaje precalculado: el total no es constante, y el progreso puede
legítimamente retroceder.

### `task.heartbeat` — `info`

| Campo | Tipo |
| --- | --- |
| `state` | string — el estado de la tarea que se observa |
| `source` | `stream` \| `file` \| `mtime` |

### `task.interrupted` — `warning`

Una tarea encontrada a medio camino por el barrido de reconciliación de
arranque, porque el proceso que la impulsaba se cayó o fue eliminado.
Emitido una vez por tarea reconciliada, antes de que vuelva a encolarse.

| Campo | Tipo |
| --- | --- |
| `previous_status` | string |

### `task.cost_requeued` — `info`

Una tarea bloqueada por `cost` ya no supera el `max_cost_per_task_usd`
*actual* — un humano elevó o desactivó el tope entre ejecuciones — así que
el bloqueo se limpió. Aquí no falló nada.

| Campo | Tipo |
| --- | --- |
| `task_cost_usd` | float |

### `task.finishing_failed` — `warning`

El paso `openspec archive` de mejor esfuerzo de `FINISHING` falló. Siempre
es un warning: `FINISHING` nunca bloquea una tarea a la que ya se le hizo
merge exitosamente. Esta es una señal de observabilidad para la revisión
posterior a la ejecución, nada más.

| Campo | Tipo |
| --- | --- |
| `spec_id` | string |
| `error` | string |

### `task.guardrail_tripped`

**Declarado pero no emitido.** El tipo existe en la enumeración de eventos;
ninguna ruta de código lo escribe hoy. Las denegaciones de guardrail
actualmente aparecen en el propio log de la sesión del harness y en el
fallo resultante, no como un evento de este tipo. No construyas alertas
sobre él.

---

## Eventos a nivel de proyecto

### `agent_assets.synced` — `info`

La política operativa, agentes, skills y hooks del harness se copiaron a un
repositorio objetivo o worktree. `run_id` es null cuando esto ocurre en el
momento de `cosmo init`.

| Campo | Tipo |
| --- | --- |
| `harness` | string |
| `template_version` | string — hash de contenido del árbol de plantilla sincronizado |
| `target_path` | string |

---

## Eventos sintéticos

### `watch.stale`

No es un tipo de evento almacenado. `cosmo notify watch` lo construye en
memoria y lo reenvía cuando la tabla `events` ha estado en silencio durante
`notify.stale_after_seconds` mientras la ejecución no está en un estado
terminal — la única señal que puede reportar la propia muerte del bucle de
ejecución.

| Campo | Tipo |
| --- | --- |
| `stale_after_seconds` | int |

---

## Tablas relacionadas

El log de eventos no es el único registro. Estas son consultables en la
misma base de datos SQLite y contienen detalle que los eventos
deliberadamente no llevan:

| Tabla | Contenido | Superficie de la CLI |
| --- | --- | --- |
| `task_queue` | estado actual de cada tarea | `cosmo queue ls` / `show` |
| `task_failures` | por intento: `failure_type`, `failure_stage`, `error_summary`, **`error_detail`**, `files_touched`, `will_retry`, `next_action`, `failure_signature` | `cosmo queue failures` |
| `task_transitions` | rastro de solo anexado de cambios de estado | — |
| `run_state`, `run_cost`, `task_cost` | estado y gasto de la ejecución | `cosmo report` |
| `task_progress`, `task_heartbeat` | progreso y vitalidad más recientes, una fila por tarea | — |
| `projects` | repositorios objetivo registrados | `cosmo project list` |
