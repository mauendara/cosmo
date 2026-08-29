# Cómo escribir un adaptador de harness

> Nota: esta traducción puede no estar actualizada. El inglés es la fuente canónica de esta documentación — consulta la [versión en inglés](../../en/how-to/write-a-new-adapter.md).

Un **harness** es el agente de codificación que Cosmo controla — lo que
realmente propone y escribe código. Claude Code es el único adaptador
implementado hasta hoy. Este documento especifica la interfaz con la
precisión suficiente para que puedas agregar otro (Codex CLI, OpenCode, un
agente propio) sin tener que preguntarle nada a nadie.

Las contribuciones de nuevos adaptadores son explícitamente bienvenidas.
Consulta [CONTRIBUTING.md](../../../CONTRIBUTING.md) para conocer las
convenciones de los PR.

## Lo que Cosmo te garantiza

- El código central de orquestación **nunca ramifica según qué harness esté
  configurado**. Una prueba obliga a que solo el propio módulo de tu
  adaptador pueda nombrar tu binario, sus flags o sus variables de entorno.
- A tu adaptador nunca se le pide que valide nada. La validación evita el
  harness por completo — es una invocación directa a Docker, de modo que tu
  agente no puede influir en su propio veredicto.
- A tu adaptador nunca se le pide que resuelva un conflicto de merge.
- Cosmo es dueño de los timeouts, los reintentos, la máquina de estados, la
  contabilidad de costos y de cada decisión sobre qué ocurre después de que
  una llamada retorna.

Tu trabajo es acotado: **invocar al agente y reportar lo que pasó de manera
uniforme.**

## Los tres archivos que escribes

```
src/cosmo/harness/mytool/
  __init__.py     # exporta la clase del adaptador
  adapter.py      # la implementación
```

más una línea que lo registra:

```python
# src/cosmo/harness/registry.py
from cosmo.harness.mytool import MyToolAdapter

_REGISTRY: dict[str, type[HarnessAdapter]] = {
    ClaudeCodeAdapter.name: ClaudeCodeAdapter,
    FakeHarnessAdapter.name: FakeHarnessAdapter,
    MyToolAdapter.name: MyToolAdapter,       # ← agrega esto
}
```

Luego los usuarios lo seleccionan con `harness.name = "mytool"` en la
configuración, con `--harness mytool` en un comando, o por proyecto mediante
`cosmo init --harness mytool`.

## La plantilla del harness

El adaptador en Python es solo la mitad del trabajo. La otra mitad vive
fuera de `src/`, en un directorio que Cosmo trata como datos, no como
código:

```
templates/harness/mytool/
  CLAUDE.md          # o el equivalente de tu herramienta a una política operativa
  settings.json      # modo de permisos, herramientas en la lista blanca, etc.
  agents/            # las definiciones de los agentes implementador/revisor
  skills/            # las skills de flujo de OpenSpec y enriquecimiento de specs
  hooks/             # los scripts de guardrail para PreToolUse/PostToolUse
```

Tómalo de `templates/harness/claude/` — ese es el diseño de referencia, no
uno específico de Claude. Sea cual sea el mecanismo de tu herramienta para
política operativa, subagentes, skills y hooks de llamadas a herramientas,
va aquí.

Este directorio es lo que `cosmo init` y cada creación de worktree por tarea
sincronizan hacia `.agent/mytool/` en el repositorio objetivo (hacia donde
apuntan symlinks equivalentes a `.claude/agents` y `.claude/skills`). La
sincronización es **completa, no un merge**: el destino se borra y se
recrea desde esta plantilla en cada sincronización, para cualquier
adaptador de harness por igual. Nada que un usuario edite o instale a mano
en ese árbol, en su repositorio objetivo, sobrevive a la siguiente tarea. Si
tu plantilla busca empaquetar una capacidad (una skill de OpenSpec, un
agente propio) para los usuarios, distribúyela aquí — no le digas a los
usuarios que la agreguen ellos mismos al repositorio objetivo, porque Cosmo
la eliminará.

Mantén la política operativa y los guardrails aquí estrictamente separados
del código del adaptador en `src/cosmo/harness/mytool/`: la plantilla es lo
que el agente lee y se le indica seguir; el adaptador es lo que invoca al
agente y analiza su salida. Mezclarlos — por ejemplo, escribiendo una
política directamente en `adapter.py` en vez de en `CLAUDE.md` — hace que la
política sea invisible para `cosmo doctor` e imposible de auditar para un
usuario sin leer Python.

## Los tipos de datos

### `HarnessCapabilities`

A nivel de clase, declarado una sola vez. Cada flag nombra el respaldo
("fallback") que Cosmo adopta cuando es `False` — así que una declaración
conservadora siempre es segura, solo te da una garantía más débil.

```python
@dataclass(frozen=True, slots=True)
class HarnessCapabilities:
    reports_native_progress: bool    # False -> Cosmo observa el tasks.md del change
    supports_retry_context: bool     # False -> Cosmo arma un prompt de reintento sintético
    has_internal_timeout: bool       # False -> Cosmo impone un timeout externo
    reports_native_cost: bool        # False -> estima desde tokens, o desactiva el corte por costo
    supports_gating: bool            # False -> solo inspección post-hoc del diff (más débil)
    supports_structured_stream: bool # False -> liveness por mtime del archivo; el timeout
                                     #          de estancamiento es entonces la única guarda
```

**Declara con honestidad.** Que `supports_gating=True` cuando tu harness en
realidad no puede denegar una llamada a una herramienta antes de que se
ejecute significa que Cosmo cree tener una prevención que no tiene.
`cosmo harness list` imprime esta tabla para que los usuarios vean qué están
obteniendo.

Si tu harness no tiene forma de bloquear la edición de un archivo antes de
que ocurra, declara `supports_gating=False`. Cosmo recurre entonces solo al
gate de diff, que es más débil pero honesto.

### `HarnessResult`

El tipo de retorno uniforme para cada método. Nada específico del harness se
filtra más allá de este límite.

```python
@dataclass(frozen=True, slots=True)
class HarnessResult:
    success: bool                    # obligatorio
    output_summary: str              # obligatorio: etiqueta corta, de salida estructurada
    raw_log_path: Path | None        # obligatorio: dónde escribiste el log de la sesión en crudo
    files_changed: list[str]         # obligatorio (puede estar vacío)
    duration_seconds: float          # obligatorio
    total_cost_usd: float | None     # obligatorio (None si se desconoce)
    exit_code: int | None            # obligatorio (None si no está basado en un proceso)
    session_id: str | None           # obligatorio (None si tu harness no tiene ese concepto)
    quota_window: str | None = None      # "five_hour" | "weekly" | None
    quota_resets_at: str | None = None   # UTC ISO 8601, o None
    tool_call_count: int = 0
```

Notas que importan:

- **`success` es solo una señal de cero contra distinto de cero** para un
  harness basado en procesos. Nunca ramifiques según un código de salida
  *específico* — el clasificador de Cosmo asume una señal binaria.
- **`output_summary` debe provenir de una salida estructurada, no de
  prosa.** Lee el propio campo tipo `subtype` del objeto de resultado
  terminal. Está prohibido analizar el mensaje final en texto libre del
  modelo: un modelo al que se le pide decir "success" dirá "success".
- **`quota_window`** es tu señal principal de límite de tasa, y solo tiene
  sentido en una llamada *fallida*. Un aviso de límite de tasa visto a mitad
  del stream no significa que la llamada haya fallado — muchas CLIs
  reintentan internamente y de todos modos tienen éxito. Si tu harness no
  tiene esa señal, déjalo en `None`; Cosmo recurre a sus detectores
  secundario y terciario.
- **`tool_call_count`** alimenta la heurística de cuota por tiempo de reloj
  ("falló instantáneamente con cero llamadas a herramientas"). `0` está bien
  si no puedes contarlas.

### `CheckResult`

```python
from cosmo.checks import CheckResult, check_executable, ok, warn, fail

ok("check name", "detail")     # informativo
warn("check name", "detail")   # visible, no bloqueante
fail("check name", "detail")   # bloqueante: cosmo doctor sale con código distinto de cero
```

## La interfaz

```python
from cosmo.harness.base import HarnessAdapter, HarnessCapabilities, HarnessResult

class MyToolAdapter(HarnessAdapter):
    name: ClassVar[str] = "mytool"
    capabilities: ClassVar[HarnessCapabilities] = HarnessCapabilities(...)
```

`name` y `capabilities` están a nivel de clase para que el registro pueda
reportarlos sin instanciar ni ejecutar nada.

El `__init__` base toma `(config: CosmoConfig, *, cwd: Path | None = None)`
y almacena ambos. `cwd` es el worktree de la tarea — el directorio en el que
tu agente debe ejecutarse, y la ruta que el barrido de huérfanos
("orphan sweep") verifica en busca de procesos que aún la retengan. Extiende
el constructor si necesitas más (el adaptador de Claude toma `binary`,
`run_id` y `emitter`), pero mantén esos parámetros solo por palabra clave y
con valores por defecto.

### `preflight() -> list[CheckResult]`

Precondiciones ambientales específicas de tu harness, para `cosmo doctor`.

**Debe ser económico y libre de efectos secundarios**: como mucho una
búsqueda en el `PATH`. Ningún subproceso más allá de eso, ninguna llamada de
red. `cosmo doctor` se ejecuta antes de cada despliegue y dentro de scripts.

```python
def preflight(self) -> list[CheckResult]:
    results = [check_executable("mytool cli", self._binary, "running the harness")]
    if os.environ.get("MYTOOL_API_KEY"):
        results.append(fail("billing", "MYTOOL_API_KEY switches to metered billing"))
    mode = self.config.harness.permission_mode
    if mode in MY_FORBIDDEN_MODES:
        results.append(fail("permission mode", f"{mode!r} is never permitted"))
    return results
```

Verifica cualquier cosa que pudiera hacer que una ejecución desatendida
resulte silenciosamente costosa o silenciosamente insegura. El adaptador de
Claude falla de forma estricta si `ANTHROPIC_API_KEY` está configurada,
exactamente por esa razón.

### `probe(prompt, *, on_activity=None) -> HarnessResult`

Ejecuta un prompt en crudo. Respalda a `cosmo harness probe`, la prueba de
humo agnóstica al harness que no presupone un cambio de OpenSpec en disco.

Impleméntalo como un envoltorio delgado sobre tu función auxiliar de
invocación. Es lo primero que cualquiera ejecuta cuando tu adaptador no
funciona.

### `propose(spec_path, context, *, on_activity=None) -> HarnessResult`

Conduce el flujo de trabajo `propose` de OpenSpec para el cambio en
`spec_path`.

`context` es un `dict[str, Any]` que lleva al menos:

| Clave | Significado |
| --- | --- |
| `task_id` | El id de tarea de la cola. Recurre a `spec_path.stem` si falta. |
| `spec_id` | **El nombre exacto que debe tener el cambio de OpenSpec creado.** |

**`spec_id` no es opcional/orientativo.** Todo lo que viene después — el
paso `openspec archive` en `FINISHING`, la verificación de reutilización de
worktree en un reintento — asume que el cambio se llama exactamente así.
Fíjalo en el prompt, con énfasis:

```python
prompt = (
    f"Run OpenSpec's propose workflow for the change at {spec_path}. "
    f"Name the change exactly {spec_id!r} (`openspec new change {spec_id}`) -- "
    f"do not pick a different name, even a shorter or more natural-looking one. "
    f"Follow this repository's operating policy for how to invoke OpenSpec."
)
```

Esta redacción es el resultado de un fallo real: sin ella, una sesión
razonablemente eliminó el sufijo `-task` del archivo de una tarea, y cada
paso posterior pasó por alto en silencio el cambio real.

### `implement(task_id, spec_path, retry_context=None, *, on_activity=None) -> HarnessResult`

Implementa el cambio. En un reintento, `retry_context` lleva el detalle real
del fallo del intento anterior:

```
Attempt 2 failed at stage e2e_tests (code_error): 1 test failed
  LoginPage › redirects logged-out users
  Expected URL to contain "/login", received "/dashboard"

Previous attempts:
- attempt 1 (unit_tests): 3 tests failed
```

Agrégalo al prompt, o pásalo a través del mecanismo de reintento nativo de
tu harness si tiene uno — eso es lo que declara `supports_retry_context`.

### `review(task_id, spec_path, base_branch, *, on_activity=None) -> HarnessResult`

La revisión adversarial. **Esto debe ser una llamada genuinamente nueva**:
sin reanudación de sesión, sin contexto de reintento, sin memoria de la
implementación. Ese es todo el sentido — de lo contrario es la misma sesión
calificando su propio trabajo.

El veredicto **no** se devuelve en `HarnessResult`. No tiene un espacio
agnóstico al harness allí, y está prohibido leerlo de la prosa de la
sesión. En su lugar, indícale al revisor que escriba un archivo JSON en el
worktree, en `.cosmo/review-result.json`:

```json
{"verdict": "approved"}
{"verdict": "rejected", "reason": "<why, specific enough to act on>"}
```

```python
from cosmo.task.review import REVIEW_RESULT_RELATIVE_PATH

prompt = (
    f"Review this branch's implementation for task {task_id}. Run "
    f"`git diff {base_branch}...HEAD` to see the diff and read the OpenSpec "
    f"change at {spec_path} for what was asked -- you have no memory of the "
    f"implementation session, judge only what these show. When done, write "
    f"your verdict to `{REVIEW_RESULT_RELATIVE_PATH.as_posix()}` as JSON: "
    f'`{{"verdict": "approved"}}` or '
    f'`{{"verdict": "rejected", "reason": "<why>"}}`.'
)
```

Importa la constante en lugar de codificar la ruta directamente. Cosmo lee
el archivo de vuelta después de que tu llamada retorna; un archivo
faltante, ilegible, malformado o sin veredicto se trata como un
**problema del entorno con la llamada de revisión**, nunca como un rechazo.

### `get_progress(task_id) -> tuple[int, int]`

Subtareas completadas y totales. **Nunca un porcentaje precalculado** — el
total no es constante y el progreso legítimamente puede retroceder, así que
el numerador y el denominador se almacenan por separado.

Si declaraste `reports_native_progress=False`, lanza `NotImplementedError`
con un mensaje que lo indique. Cosmo observa entonces el `tasks.md` del
cambio y nunca llama a este método.

### `cancel(task_id) -> None`

Termina la ejecución **y todo su grupo de procesos**.

Esto no es un detalle opcional. En POSIX, enviar la señal solo al hijo
directo deja a Maven, Node, Vite, clientes de `docker` y al Chromium de
Playwright reasignados como hijos de init, donde siguen corriendo y
reteniendo puertos y memoria hasta que el host colapsa — horas después de
que terminó la ejecución que los generó.

Usa el propio `ManagedProcess` de Cosmo, que maneja esto correctamente:

```python
from cosmo.proc import ManagedProcess, cancel_and_reap

process = ManagedProcess(
    argv,
    raw_log_path=raw_log_path,
    cwd=self.cwd,
    env=env,
    on_stdout_chunk=reader.feed,   # opcional: callback de streaming
)
exit_code = process.wait()
```

`ManagedProcess` inicia el hijo con `start_new_session=True` (su propio
grupo de procesos y sesión), drena stdout y stderr en hilos separados hacia
tu log en crudo, y al cancelar escala SIGTERM → `timeouts.kill_grace`
segundos → SIGKILL contra el **grupo de procesos**. No declara la victoria
cuando `Popen.wait()` retorna — eso solo recolecta a tu hijo directo — sino
cuando `killpg(pgid, 0)` lanza `ProcessLookupError`, lo que prueba que todo
el grupo ha desaparecido.

```python
def cancel(self, task_id: str) -> None:
    with self._lock:
        process = self._running.get(task_id)
    if process is None:
        return
    if self._emitter is not None:
        cancel_and_reap(
            process, run_id=self._run_id or "", task_id=task_id,
            worktree_path=self.cwd, config=self.config, emitter=self._emitter,
        )
    else:
        process.cancel(grace_s=self.config.timeouts.kill_grace)
```

`cancel_and_reap` agrega el barrido de huérfanos — contenedores Docker
sobrantes emparejados por sus etiquetas `orchestrator.run_id`/
`orchestrator.task_id`, y procesos que aún mantienen abierto el worktree — y
emite un evento `task.failed` de nivel `critical` si el barrido falla.

`cancel()` se llama **desde otro hilo** mientras tu `wait()` está
bloqueado. Mantén un lock alrededor del registro de procesos en ejecución.

## El hook `on_activity`

Cada método de llamada toma `on_activity: Callable[[str], None] | None`.
Llámalo con una línea corta y legible por humanos por cada evento en vivo
notable — una llamada a una herramienta, el inicio de una sesión — para que
un `cosmo run` en primer plano no sea una terminal en blanco durante
cuarenta minutos.

Es **solo para visualización**. Ninguna decisión de clasificación,
reintento o programación lee jamás de ahí. Es deliberadamente una cadena de
texto plana, no un tipo de evento específico del harness, para que la
máquina de estados se mantenga agnóstica al harness.

No repitas cada heartbeat; el progreso ya se rastrea por separado.

## Timeouts

Si declaraste `has_internal_timeout=False` (la respuesta honesta para la
mayoría de las CLIs), **no impongas un timeout dentro de tu adaptador.** Tu
adaptador no sabe qué reloj de estado aplica — `proposing_wall`,
`implementing_wall` y `validating_wall` son todos distintos, y solo la capa
de orquestación sabe en qué estado se encuentra.

Bloquéate en `wait()`. La capa de orquestación de Cosmo llama a `cancel()`
desde otro hilo cuando expira el reloj correspondiente, lo que desbloquea tu
`wait()` matando efectivamente al hijo.

## Salida estructurada, no prosa

Cosmo es estricto en esto, y los adaptadores también deben serlo:

- **Nunca analices la salida en texto libre del modelo** como señal de
  éxito, fallo, veredicto o clasificación. Un modelo al que se le pide
  indicar éxito indicará éxito.
- **Sí** lee los campos estructurados que la propia CLI define: el estado de
  salida de un objeto de resultado terminal, su subtype, la cifra de costo,
  el id de sesión, el aviso de límite de tasa.
- La distinción es de autoría. Un campo que emite la *herramienta* es dato.
  Una frase que escribió el *modelo* no lo es.

## Postura de seguridad

Sean cuales sean los equivalentes de tu harness:

- **Nunca uses un modo de "saltar todos los permisos".** Verifica su
  ausencia en el argv construido en lugar de simplemente omitirlo, para que
  una futura edición no pueda reintroducirlo en silencio. El adaptador de
  Claude verifica tanto `--dangerously-skip-permissions` como
  `bypassPermissions`.
- **Falla de forma cerrada.** Solo deberían ejecutarse herramientas
  explícitamente en la lista de permitidas.
- **Depura las variables de entorno que cambian el modo de facturación**
  del entorno del hijo en lugar de asumir su ausencia, y usa `fail()` sobre
  ellas en `preflight()`.
- **Carga solo la configuración del proyecto.** No incorpores la
  configuración global del operador en una ejecución desatendida — hooks,
  plugins y servidores MCP personales arbitrarios con costo y efectos
  secundarios desconocidos.
- **Si habilitas telemetría, desactiva explícitamente el registro de
  contenido.** Los prompts y el contenido de archivos en un backend de
  telemetría son una vía de exfiltración de datos para un código base
  privado.

## Cómo probar tu adaptador

`FakeHarnessAdapter` (`cosmo.harness.fake`) es la referencia para la *forma*
del contrato — resultados guionizados sin un subproceso real. Léelo primero;
es corto.

Para tu propio adaptador, inyecta la ruta del binario para que las pruebas
puedan apuntarlo a un script de fixture:

```python
def __init__(self, config, *, cwd=None, binary: str = BINARY) -> None:
    super().__init__(config, cwd=cwd)
    self._binary = binary
```

Luego:

1. **Prueba de límite.** Verifica que ningún módulo fuera de
   `cosmo/harness/mytool/` nombre tu binario o tus variables de entorno.
   Las pruebas de límite existentes muestran el patrón basado en `ast`.
2. **Prueba de permisos.** Verifica que tu modo prohibido nunca aparezca en
   el argv construido, desde fuera.
3. **Prueba de cancelación.** Genera un fixture que bifurca (`fork`) un
   hijo que ignora SIGTERM, cancélalo y verifica que todo el grupo de
   procesos haya desaparecido. Este es el fallo que le cuesta un host a
   alguien, y es el que con más facilidad se hace mal.
4. **Prueba de mapeo de resultados.** Pasa una salida grabada por tu parser
   y verifica los campos de `HarnessResult`.

Luego, en condiciones reales:

```bash
cosmo doctor --harness mytool
cosmo harness list                                # tabla de capacidades
cosmo harness probe --harness mytool --prompt "reply with the word ok"
cosmo run --repo /tmp/test-project --harness mytool --task some-task
```

## Lista de verificación

- [ ] `templates/harness/mytool/` escrito, tomando como modelo
      `templates/harness/claude/`
- [ ] `name` y `capabilities` declarados a nivel de clase, con honestidad
- [ ] `preflight()` es económico, libre de efectos secundarios, y falla ante
      variables de entorno que cambian el modo de facturación
- [ ] `propose()` fija el nombre del cambio a `context["spec_id"]` textual
- [ ] `review()` es una sesión nueva y escribe su veredicto en
      `REVIEW_RESULT_RELATIVE_PATH`, importado, no codificado directamente
- [ ] `cancel()` mata todo el grupo de procesos y es thread-safe
- [ ] Ningún timeout dentro del adaptador cuando `has_internal_timeout=False`
- [ ] `success` deriva únicamente de cero contra distinto de cero en el
      código de salida
- [ ] Ningún análisis de prosa en ningún lugar
- [ ] Se escribe un log en crudo y se devuelve su ruta
- [ ] Registrado en `registry.py`
- [ ] La prueba de límite pasa: nada fuera de tu módulo nombra tu binario
