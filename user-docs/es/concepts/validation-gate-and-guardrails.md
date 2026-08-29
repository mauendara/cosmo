# El gate de validación y los guardrails contra las trampas

> Nota: esta traducción puede no estar actualizada. El inglés es la fuente canónica de esta documentación — consulta la [versión en inglés](../../en/concepts/validation-gate-and-guardrails.md).

Este es el documento a leer si estás decidiendo si confiarle a Cosmo una
noche sin supervisión.

## La premisa

Un agente que trabaja sin supervisión tiene una manera confiable de hacer
que una suite en rojo se vuelva verde: cambiar la suite. Borrar el test que
falla. Agregar `@Disabled`. Cambiar `test(` por `test.skip(`. Aflojar la
aserción hasta que no pueda fallar. Ninguna de estas es exótica — son el
camino más corto de "la tarea no está terminada" a "la tarea se ve
terminada", y un agente que optimiza para marcar una casilla las va a
encontrar.

La respuesta de Cosmo es estructural, no exhortativa. Al agente se le dice
que no haga esto, pero nada depende de que obedezca:

> **El gate de validación (el gate) es la única fuente de verdad sobre la
> corrección.**

Una casilla marcada en `tasks.md`, un "listo" en stdout, un resumen final
confiado — todo eso es *telemetría de vivacidad*. Le dice a Cosmo que el
proceso sigue vivo y más o menos en qué punto está. Nunca avanza una tarea
hacia `DONE`. Lo único que lo hace es un build real, una corrida de tests
real, y un pase de Playwright real en un contenedor que el propio Cosmo
inició, fuera de la sesión del agente, después de que el proceso del agente
ya terminó.

---

## Lo que el gate realmente ejecuta

`VALIDATING` ejecuta cinco cosas, en serie, deteniéndose en el primer fallo:

```
diff gate → gitleaks scan → build → unit tests → e2e
```

**1. El diff gate.** Se ejecuta *antes de que corra cualquier test*, contra
`git diff <base_branch>...<task_branch>` calculado en fresco a partir del
worktree de la tarea. Detallado más abajo.

**2. El escaneo de gitleaks.** Un respaldo para el hook de pre-commit. Un
binario de `gitleaks` faltante hace fallar la etapa en lugar de saltarla
silenciosamente — el escaneo no es opcional.

**3. Build.** `mvn -B -q -DskipTests package` en `gate.backend_image` si el
repositorio tiene un `backend/`; el build del frontend en
`gate.frontend_image` si tiene un `frontend/`.

**4. Tests unitarios.** Ambos lados, en sus propios contenedores.

**5. E2E.** El frontend (y el backend, si lo hay) arrancan como contenedores
de larga duración en una red Docker privada. Playwright corre contra ellos
por nombre de host del contenedor, en la imagen fijada
`mcr.microsoft.com/playwright` — igualando la forma en que la aplicación se
despliega realmente, sin depender del networking del host. Tu
`playwright.config.ts` debe leer `process.env.BASE_URL` y escribir un
reporter `json` en `playwright-report/results.json`, o el gate no tiene nada
que analizar.

Un repositorio sin `backend/` no se salta la etapa e2e — Playwright
simplemente corre solo contra el frontend. Saltarse e2e cada vez que falta
un backend haría que "pasara" silenciosamente con cero tests ejecutados en
cada proyecto solo-frontend, que es exactamente el hueco que el gate existe
para cerrar.

Cada contenedor recibe `--shm-size` y `--ipc=host` (Chromium colapsa sin
ellos) y corre como un usuario sin privilegios con `HOME=/tmp`, así que
`node_modules`, `dist` y `target` no terminan siendo propiedad de root
dentro del worktree montado por bind, donde la sesión del agente sin
privilegios nunca podría limpiarlos.

Cada etapa tiene su propio presupuesto (`gate.stage_timeout_seconds`, por
defecto 30 minutos), así que un contenedor colgado no puede bloquear una
ejecución para siempre. Un timeout de etapa se clasifica como
`environment_error`, lo cual por diseño **no consume el presupuesto de
reintentos a nivel de código** de la tarea.

---

## Capa 1: prevención — hooks `PreToolUse`

La defensa más fuerte es que la edición nunca ocurra. `cosmo init` instala
hooks de guardrail en el repositorio objetivo bajo `.agent/<harness>/hooks/`,
conectados a través de los propios ajustes del harness. Se ejecutan antes de
que la llamada a la herramienta se ejecute y pueden denegarla.

**`test_path_guard.py`** — bloquea `Edit`/`Write` bajo rutas de test
protegidas:

```
src/test/**        (repo-root anchored)
e2e/**             (repo-root anchored)
**/*.spec.ts   **/*.test.ts
**/*.spec.tsx  **/*.test.tsx
**/*.spec.jsx  **/*.test.jsx
```

Los patrones `.tsx`/`.jsx` no son decorativos: un test de componente React
que renderiza JSX *debe* ser `.tsx`, así que proteger solo
`**/*.test.ts` deja sin protección cada test de componente en un proyecto
TypeScript+JSX.

El guardián se evita solo cuando la propia fila de cola de la tarea tiene
`allow_test_edits: true` — fijado por tarea al momento de encolar
(`cosmo queue add --allow-test-edits`) o en el frontmatter del archivo de la
tarea. El hook lee ese flag directamente de la base de datos de Cosmo,
porque un hook es un proceso del sistema operativo separado sin otra manera
de preguntar.

**`annotation_guard.py`** — bloquea *introducir* una anotación de skip o
disable: `@Disabled`, `@Ignore`, `.skip(`, `.only(`, `xit(`, `xdescribe(` y
similares. Debilitar un test de esta manera es funcionalmente idéntico a
borrarlo, y esto lo detecta dentro de archivos que el guardián de rutas no
cubre.

"Introducir" se juzga comparando los conteos antes y después de la edición
propuesta, no mediante una búsqueda plana de subcadena — un archivo que ya
contiene legítimamente uno de estos tokens no debe bloquear una edición no
relacionada al mismo archivo.

**`commit_integrity_guard.py`** — bloquea comandos de git que evaden
controles de integridad o que son trabajo de Cosmo:

- `git commit ... --no-verify` — evade el escaneo de secretos de
  pre-commit.
- `git push` en cualquier forma — hacer push es trabajo de Cosmo. Bloquear
  todo el subcomando cubre cada variante de force-push como un subconjunto.
- `git reset --hard` — puede descartar trabajo silenciosamente.

**`background_task_guard.py`** — bloquea llamadas `Bash` con
`run_in_background: true`. Este se descubrió a mano tres veces antes de que
existiera: una sesión pone `npm install` en segundo plano, luego pasa el
resto de su presupuesto de turno sondeando el PID (bucles de `kill -0`,
`sleep`, `ps`), no hace ningún progreso, y el temporizador de estancamiento
la mata veinte minutos después. Una llamada headless `claude -p` retorna
exactamente una vez; no hay un "después" al cual regresar. Denegar las
*herramientas* de programación nunca cerró esto — también había que denegar
el parámetro que crea el trabajo desprendido (detached).

Los hooks son defensa en profundidad de la capa de prevención, no la única
capa. Usan coincidencia de expresiones regulares, no un análisis consciente
de shell, y la evasión adversarial queda fuera de alcance para algo
presupuestado en menos de dos segundos.

## Capa 2: detección — el diff gate

No todo harness puede bloquear una llamada a herramienta antes de que se
ejecute (`HarnessCapabilities.supports_gating`), y un hook puede evadirse.
El diff gate atrapa lo que se filtra — y se ejecuta *antes de que corran los
tests*, así que una suite debilitada nunca tiene la oportunidad de reportar
verde.

Lee el diff completo contra la rama base, selecciona los archivos que
coinciden con `gate.diff_gate_test_path_patterns`, y marca cinco cosas:

| Violación | Condición |
| --- | --- |
| `test_path_deleted` | Se borró un archivo de test existente |
| `test_path_modified` | Un archivo de test existente se modificó **de cualquier forma** |
| `assertion_count_decreased` | El conteo neto de aserciones en todo el diff bajó |
| `skip_annotation_introduced` | Una subcadena de `gate.diff_gate_skip_annotations` aparece en una línea agregada dentro de un archivo de test |
| `test_loc_dropped` | Un archivo de test perdió más de `gate.diff_gate_loc_drop_threshold` líneas netas (por defecto 20) |

Dos propiedades vale la pena entender antes de que te sorprendan.

**Un archivo de test recién *agregado* no se marca.** Escribir sus propios
tests es exactamente lo que un agente bien portado debería hacer, y una
versión anterior de este gate que rechazaba cualquier tarea que agregara un
test e2e derrotaba el propósito por completo. Los archivos nuevos siguen
sujetos a las comprobaciones de conteo de aserciones, anotación de skip y
LOC — un test agregado pero inmediatamente deshabilitado sigue siendo
sospechoso.

**Pero que un archivo de test existente se modifique, sea como sea, es una
violación.** No "modificado de forma sospechosa" — modificado. Si una tarea
legítimamente necesita cambiar un test existente, esa tarea necesita
`allow_test_edits`, lo cual evade el diff gate por completo para ella. Esta
es una regla deliberadamente contundente: distinguir una actualización
honesta de un test de una interesada es precisamente el juicio que no se
puede confiar que un agente sin supervisión haga en su propio nombre, así
que se escala a una decisión humana tomada al momento de encolar.

El conteo de aserciones es por diff, no por archivo: los sitios de llamada
se cuentan en líneas agregadas versus eliminadas a través de cada archivo de
test — `assertThat(` (AssertJ), `assertX(` (JUnit), `expect(` (Vitest,
Playwright) — y solo el total neto tiene que no bajar.

El conteo de aserciones es una heurística por conteo de líneas, no un
parser real, y está deliberadamente sesgado: solo llega a *sub*contar
eliminaciones, nunca a confundir una línea no relacionada con una aserción
eliminada. El peor caso es que una violación real se escape
ocasionalmente, no que se bloquee trabajo honesto por un falso positivo. Un
parser específico por lenguaje es una mejora futura conocida, no algo que se
finge ya resuelto.

Una violación del diff gate se registra con `failure_stage=test_integrity`,
y a diferencia de un error de entorno, **sí** consume un intento de
reintento.

## Capa 3: inspección a posteriori

Para un adaptador que reporta `supports_gating: false`, el mismo diff gate
es la única defensa — detección sola, sin prevención por delante. Es
estrictamente más débil, y así se declara: un adaptador sin bloqueo previo a
la ejecución obtiene una garantía peor, y eso queda visible en
`cosmo harness list` en lugar de quedar disimulado.

---

## Tests flaky: la otra mitad de confiar en el gate

Un gate que falla por ruido es un gate que vas a aprender a saltarte. Un
solo test flaky de Playwright puede quemar todo el presupuesto de
reintentos de una tarea persiguiendo un bug que no existe, y luego
bloquearla por `code_failure` cuando nunca fue código.

Cosmo maneja esto en tres partes.

**Confirmar mediante reintento.** Cuando un test e2e no puesto en
cuarentena falla, se vuelve a ejecutar *en aislamiento*, hasta
`gate.flaky_rerun_limit` veces (por defecto 3). El primer pase gana: el
fallo se reclasifica como `flaky`, y — de manera crítica — **no consume
ningún intento de reintento**. Agotar todos los reintentos sin un pase
significa que es un `code_error` genuino, y el gate lo trata como tal.

Delimitado a e2e deliberadamente. La inestabilidad de los tests unitarios es
un problema distinto y esto no pretende resolverlo.

**La lista de cuarentena.** `quarantine.yml` nombra los tests excluidos de
hacer fallar el gate. Cada entrada requiere un propietario y una fecha de
vencimiento:

```yaml
entries:
  - test_id: "com.example.FooTest#flakyUnderLoad"
    owner: "someone@example.com"
    expiry: "2026-12-31"
    reason: "Times out under CI contention, tracked in JIRA-1234"
```

`test_id` coincide exactamente con lo que reporta el gate — `ClassName#method`
para JUnit, el título completo para Playwright. `expiry` es una fecha ISO
8601.

**Una entrada vencida rompe el archivo.** No "se ignora" — cargar el archivo
lanza una excepción, y el gate se detiene. Una lista de cuarentena sin
propietario y sin vencimiento es precisamente cómo una suite de tests deja
de probar cualquier cosa en silencio, y una entrada obsoleta protegiendo
silenciosamente un test muerto es el modo de fallo que este mecanismo entero
existe para prevenir. Renovar una entrada tiene que ser un acto deliberado
de una persona nombrada.

**Candidatos, nunca promoción automática.** Un test clasificado como flaky
en `gate.flaky_quarantine_candidate_threshold` *ejecuciones distintas* (por
defecto 3) se agrega a `quarantine-candidates.yml` para revisión humana.
Cosmo nunca promueve un candidato a `quarantine.yml` por sí mismo. Hacerlo
sería el mismo modo de fallo autodebilitante que el diff gate existe para
atrapar, solo que ejecutado por el orquestador en lugar del agente.

Ejecuciones distintas, no eventos distintos — un test marcado dos veces
dentro de una misma ejecución cuenta una vez.

---

## La revisión adversarial

Entre `VALIDATING` y `COMMITTING`, si `review.enabled` es verdadero (lo es
por defecto), Cosmo hace una llamada más al harness. Es una sesión
genuinamente fresca: sin reanudación de sesión, sin contexto de reintento,
sin ninguna memoria de cómo llegó a existir el diff. Recibe exactamente dos
cosas — `git diff <base>...HEAD` y el cambio de OpenSpec que describe qué se
pidió — y juzga solo eso.

Esto importa porque la alternativa, pedirle a la sesión implementadora que
revise su propio trabajo, es una sesión calificando su propia tarea con
memoria completa de cada atajo que justificó en el camino.

El veredicto no se lee del mensaje final de la sesión. Analizar prosa en
busca de una señal está prohibido: un modelo al que se le pide decir
"aprobado" va a encontrar la manera de decirlo. En cambio, el revisor
escribe un archivo estructurado en el worktree:

```json
{"verdict": "approved"}
{"verdict": "rejected", "reason": "<specific enough to act on>"}
```

Cosmo lee ese archivo de vuelta después de que la llamada retorna. Un
archivo faltante, ilegible, malformado, o sin veredicto se trata como un
**problema de entorno con la llamada de revisión**, nunca como un rechazo —
un revisor roto no debe aprobar silenciosamente, y tampoco debe condenar
silenciosamente.

Un rechazo se reintenta contra el mismo presupuesto de `retries.max_attempts`
que un fallo del gate, con la razón devuelta como contexto.

---

## Qué le pasa a un fallo

Los fallos se clasifican en cuatro tipos, y la clasificación determina si le
cuesta un intento a la tarea:

| Tipo | ¿Cuenta contra `max_attempts`? | Causa típica |
| --- | --- | --- |
| `code_error` | **sí** | Fallo de build, test que falla y que los reintentos no pudieron resolver |
| `test_integrity` (etapa) | **sí** | Violación del diff gate |
| `timeout` | sí, en `IMPLEMENTING` | Se disparó el reloj de pared o el temporizador de estancamiento |
| `environment_error` | **no** | Docker no disponible, timeout de etapa, proceso del harness murió, llamada de revisión rota |
| `flaky` | **no** | Confirmado por reintento |

Que `environment_error` nunca consuma el presupuesto de código es lo que
hace que "un entorno roto no debe agotar los reintentos de una tarea" sea
cierto de forma estructural, en lugar de cierto solo en la opinión del
clasificador. Aun así recibe un bucle de reintento local acotado, porque uno
sin límite sobre un host permanentemente roto sería peor.

Cada fallo se escribe en `task_failures` con el detalle real — texto de
aserción, extractos de stack, archivos tocados — y el prompt del siguiente
intento lo lleva consigo:

```
Attempt 2 failed at stage e2e_tests (code_error): 1 test failed
  LoginPage › redirects logged-out users
  Expected URL to contain "/login", received "/dashboard"

Previous attempts:
- attempt 1 (unit_tests): 3 tests failed
```

Léelo tú mismo con `cosmo queue failures <task_id>`. Ese texto no tiene
ninguna otra superficie de CLI — los payloads de eventos llevan los
*nombres* de los tests que fallan, no su texto de aserción, a propósito.

---

## Dónde se detienen los secretos

Tres capas independientes, ninguna de las cuales confía en la que está
encima de ella:

1. **`permissions.deny`** en rutas con forma de secreto dentro de los
   ajustes del harness — `.env*`, `secrets/**`, `*.pem`, `id_rsa*`. El
   agente no puede leerlas.
2. **Un hook de pre-commit de gitleaks**, instalado en cada creación de
   worktree. Falla cerrado: un binario de `gitleaks` faltante bloquea el
   commit en lugar de saltarse el escaneo. `commit_integrity_guard.py`
   deniega el propio `--no-verify` del agente.
3. **El propio escaneo de gitleaks del gate**, antes del build, atrapando
   cualquier cosa que de todos modos haya llegado a un commit.

Un hallazgo se registra con `failure_stage=secrets` — deliberadamente su
propia etapa en lugar de plegarse dentro de `test_integrity`, para que
consultar el historial de fallos después no sea ambiguo.

## Qué no afirma esto

- La heurística de conteo de aserciones puede ser engañada con un esfuerzo
  decidido. Sube el costo de hacer trampa; no lo hace imposible.
- Las expresiones regulares de los hooks no son conscientes de shell y no
  sobrevivirán a un entrecomillado adversarial.
- Un test que siempre estuvo mal seguirá pasando. El gate prueba que la
  suite corre y se mantiene tan fuerte como era — no que la suite sea buena.
- Nada aquí sustituye leer el diff antes de que llegue a `main`. Cosmo
  hace merge hacia tu rama de integración, nunca hacia `main` ni `master`.
