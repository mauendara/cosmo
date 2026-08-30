# FAQ

[🇬🇧 English](FAQ.md) | 🇪🇸 Español

> Nota: esta traducción puede no estar actualizada. El inglés es la fuente canónica de esta documentación — consulta la [versión en inglés](FAQ.md).

## ¿Qué es Cosmo, en una frase?

Un orquestador que ejecuta, sin supervisión, una cola ordenada por dependencias de
tareas de desarrollo guiadas por specs contra tu repositorio, y que solo hace
merge del trabajo que pasa un gate real de build, pruebas y e2e en Docker.

## ¿En qué se diferencia esto de simplemente dejar un agente corriendo toda la noche?

La afirmación del agente de que terminó nunca es lo que hace avanzar una tarea. Un
build real, pruebas unitarias y una ejecución de Playwright ocurren después, en
contenedores que Cosmo inició, fuera de la sesión del agente, después de que su
proceso terminara. Además de eso: worktrees de git por tarea, hooks `PreToolUse` que
bloquean ediciones de archivos de prueba antes de que ocurran, un gate de diff que
falla una tarea cuyo conteo de aserciones bajó, confirmación por reejecución de
pruebas inestables, y terminaciones correctas de grupos de procesos.

## ¿Con qué agentes funciona?

Con Claude Code, por ahora. La interfaz de adaptador es real y está reforzada por
pruebas — ningún código de orquestación se ramifica según qué harness esté
configurado — pero Claude Code es la única implementación. Escribir otra es una sola
clase: [write-a-new-adapter](user-docs/es/how-to/write-a-new-adapter.md).

## ¿Funciona con mi stack?

Las plantillas de proyecto incluidas y la configuración del gate apuntan a
Java/Spring Boot más Vite/TypeScript/React/Tailwind con MariaDB o SQLite. Las
imágenes y directorios del gate son configuración (`gate.backend_image`,
`gate.backend_dir`, y sus equivalentes de frontend), y el sistema de plantillas es
solo directorios de markdown — ninguno de los dos está codificado a ese stack de
forma fija.

El límite honesto: los *comandos* de build por etapa aún no son configurables. Un
backend en Go o Rails puede usar el sistema de plantillas para documentación hoy,
pero sus etapas de build necesitan trabajo en el gate. Ver
[add-project-template](user-docs/es/how-to/add-project-template.md).

## ¿Puede un agente controlar Cosmo mediante MCP?

Todavía no. Un servidor MCP delgado sobre el mismo contrato de CLI — que permita a
una herramienta encolar, verificar estado, cancelar y leer logs — es una capacidad
planeada, distinta de que Cosmo *use* un agente como harness. **Hoy no existe tal
servidor.** Usa el CLI.

## ¿Necesita una suscripción o una clave de API?

Una suscripción. `cosmo doctor` **falla** si `ANTHROPIC_API_KEY` está definida, y el
adaptador la elimina del entorno del proceso hijo. Su presencia cambia
silenciosamente la facturación de tu suscripción a tarifas de API por token, lo cual
es algo costoso de descubrir después de una noche sin supervisión.

## ¿Cuánto cuesta ejecutarlo?

Cosmo no fija el precio; tu harness lo hace. Los topes de costo por defecto son
`0.0`, lo que significa sin corte forzoso — correcto para facturación por
suscripción, donde la restricción vinculante son las ventanas de límite de tasa, no
los dólares. En facturación medida, define `cost.max_cost_per_run_usd` y
`cost.max_cost_per_task_usd`. Ver
[configure-quotas](user-docs/es/how-to/configure-quotas.md).

## ¿Qué pasa cuando alcanzo un límite de tasa a mitad de una ejecución?

La ejecución se pausa y programa una reanudación automática: en el momento de
reinicio reportado, o en `quota.default_5h_resume_delay_seconds` (5 horas por
defecto) cuando la señal no trae un momento de reinicio. El agotamiento semanal
pausa o detiene según si el presupuesto restante de la ejecución podría sobrevivirlo.

Si tu cuenta tiene créditos de uso y prefieres gastarlos en lugar de esperar,
`quota.bypass_5h_with_credits = true` — que Cosmo se niega a cargar sin un
`cost.max_cost_per_run_usd` distinto de cero.

## ¿Puede ejecutar tareas en paralelo?

No, por diseño. Los worktrees aíslan el *código*, no el runtime — puertos, bases de
datos y `/dev/shm` siguen siendo compartidos, así que tareas concurrentes competirían
por los tres. Paralelismo significa resolver eso primero. Un bloqueo de proceso
impone una sola ejecución a la vez.

## ¿A qué rama hace merge?

`git.base_branch`, por defecto `develop`. **Nunca `main` ni `master`.** Promover a tu
rama de release siempre es un paso humano.

## ¿Hace push de algo?

No. Los hooks de guardrail bloquean `git push` en cualquier forma — eso también
cubre toda variante de force-push, ya que se bloquea el subcomando entero. Cosmo
hace merge localmente en su propio checkout del repositorio.

## ¿Puede el agente eliminar mis pruebas para hacer que el build pase?

Ese es el fallo específico alrededor del cual está construido esto. Tres capas: los
hooks `PreToolUse` deniegan ediciones bajo rutas de prueba protegidas y deniegan
introducir anotaciones tipo `@Disabled`/`.skip(`; el gate de diff cuenta aserciones
en líneas agregadas versus eliminadas y falla la tarea si disminuyeron; y para un
harness que no puede aplicar un gate a una llamada de herramienta antes de su
ejecución, el mismo gate de diff funciona solo como detección posterior. Detalle
completo:
[validation-gate-and-guardrails](user-docs/es/concepts/validation-gate-and-guardrails.md).

## ¿Y una tarea legítima cuyo trabajo entero es escribir o cambiar pruebas?

Define `allow_test_edits` en esa tarea — `cosmo queue add --allow-test-edits`, o
`allow_test_edits: true` en el frontmatter del archivo de tarea. Sin eso, el guardia
rechaza correctamente cada escritura y el agente no entrega nada, lo que parece una
implementación vacía sin explicación.

Ten en cuenta que agregar un archivo de prueba *nuevo* siempre está permitido. Es
modificar o eliminar uno *existente* lo que necesita la bandera — distinguir una
actualización honesta de pruebas de una interesada es exactamente el juicio que un
agente sin supervisión no puede hacer sobre su propio trabajo, así que se escala a
una decisión humana en el momento de encolar.

## ¿Una prueba e2e inestable agota el presupuesto de reintentos?

No. Una prueba e2e no puesta en cuarentena que falla se vuelve a ejecutar de forma
aislada hasta `gate.flaky_rerun_limit` veces (3 por defecto). Si pasa, el fallo se
reclasifica como `flaky` y **no consume ningún intento de reintento**. Solo cuando
cada reejecución falla es un error de código genuino.

Una prueba marcada como inestable en tres *ejecuciones distintas* se agrega a
`quarantine-candidates.yml` para revisión humana. Cosmo nunca promueve por sí mismo
un candidato a la lista de cuarentena — eso sería el mismo fallo autodebilitante que
el gate de diff existe para detectar, solo que realizado por el orquestador.

## ¿Por qué una entrada de cuarentena vencida rompe el gate en lugar de ser ignorada?

Porque una entrada de cuarentena obsoleta que protege silenciosamente a una prueba
muerta es exactamente el modo de fallo que el mecanismo de cuarentena existe para
prevenir. Cada entrada necesita un responsable y un vencimiento, y renovar una tiene
que ser un acto deliberado de una persona identificada. Una lista de cuarentena sin
dueño y sin vencimiento es cómo una suite deja de probar algo silenciosamente.

## ¿Dónde vive el estado?

`$XDG_DATA_HOME/cosmo/` por defecto (`~/.local/share/cosmo`) — `cosmo.db` contiene el
estado y los eventos, `work/` contiene los worktrees por tarea, `logs/` contiene los
logs en bruto del harness. La configuración está en `~/.config/cosmo/config.toml`, o
`$COSMO_CONFIG`. `cosmo config show --paths` imprime las ubicaciones reales
resueltas.

## ¿Usa una base de datos vectorial o embeddings para la memoria?

No, y eso es deliberado. La continuidad entre tareas proviene de tres fuentes
deterministas: registros de eventos estructurados, tablas de estado en SQLite, y
archivos de conocimiento en markdown versionados en el repositorio objetivo. Las tres
son consultables, comparables por diff, e idénticas en una relectura. Una capa de
recuperación haría el recuerdo más difuso justo donde un ciclo desatendido más
necesita reproducibilidad, y un recuerdo erróneo a las 3 de la mañana es un bug que
nadie está despierto para detectar.

## ¿Puedo editar las tareas antes de que se ejecuten?

Ese es el flujo de trabajo previsto. `cosmo spec add` escribe archivos de tarea
reales, rastreados por git, e imprime una vista previa — no encola nada. La ventana
entre eso y `cosmo spec queue` *es* el paso de aprobación; no hay una interfaz
separada. Edita el alcance, la redacción, las dependencias o `allow_test_edits` en
esos archivos primero.

## ¿Por qué se renombraron los ids de mis tareas?

`cosmo spec queue` antepone el nombre del spec a cada id de tarea (`api` →
`add-login-api`) y reescribe los bordes `depends_on` dentro del lote para que
coincidan. `task_queue.task_id` es una clave global única entre todos los proyectos
que comparten una misma base de datos de Cosmo — dos proyectos descomponiendo ambos
a `scaffold-app` colisionarían, y un borde de dependencia se resolvería contra la
tarea terminada del proyecto equivocado.

## ¿Puedo encolar trabajo mientras una ejecución está en curso?

Sí. El planificador recalcula el conjunto elegible completo en cada pasada, así que
las tareas nuevas se recogen tan pronto sus dependencias lo permiten.

## ¿Qué pasa si la máquina se reinicia a mitad de una ejecución?

Al inicio de la siguiente ejecución, cada tarea encontrada en un estado no terminal
se emite como `task.interrupted` y se vuelve a encolar, y la fila `run_state`
abandonada se cierra como `crashed`. El trabajo en curso se pierde; la cola no.

## ¿Por qué mi ejecución salió con código 1 cuando decía que la cola estaba vacía?

Probablemente `blocked_remaining`, no `queue_empty`. Esa razón de parada se elige
cuando al menos una tarea realmente se bloqueó durante la ejecución — una ejecución
que terminó solo porque todo quedó atascado nunca debería parecer un éxito.
`cosmo queue ls --status blocked` las mostrará.

## ¿Tengo que usar OpenSpec?

Sí — el flujo de propose/apply/archive es lo que Cosmo impulsa. Sin embargo, no
tienes que *redactar* cambios de OpenSpec a mano: `cosmo spec add` toma un archivo
markdown preliminar y produce archivos de tarea, y cada tarea crea su propio cambio
de OpenSpec de forma diferida la primera vez que se ejecuta.

## ¿Puedo desactivar la revisión adversarial?

`review.enabled = false`. Elimina una llamada al harness por tarea, así que es
significativo para el tiempo y el gasto. También elimina la única verificación que
lee el diff sin memoria de cómo fue escrito. Desactívala con conocimiento de causa.

## ¿Cómo sé qué pasó durante la noche?

```bash
cosmo report                     # cómo terminó la ejecución
cosmo queue ls --status blocked  # qué está atascado
cosmo queue failures <task_id>   # el texto real del error de una tarea
cosmo events tail --payload      # todo, en orden
```

Y configura notificaciones — `cosmo notify config` es un asistente de un solo paso
que envía un mensaje de prueba real antes de declarar éxito.

## ¿El propio código de Cosmo está escrito por IA?

Sustancialmente, sí, con revisión humana en todo momento. Los commits no se atribuyen
a una IA como autor o coautor; la divulgación de la asistencia de IA va en la
descripción del PR o en el cuerpo del commit en su lugar. Ver
[CONTRIBUTING.md](CONTRIBUTING.md).

## ¿Bajo qué licencia está?

Apache License 2.0 — ver [LICENSE](LICENSE). Las secciones 7 y 8 de esa
licencia renuncian a toda garantía y limitan la responsabilidad: el software
se entrega "TAL CUAL" ("AS IS"), y tú asumes el riesgo de usarlo.

## ¿De dónde viene el nombre?

*Kosmos* — orden a partir del caos. Que es lo que más necesita una cola desatendida
de trabajo de agentes, y lo que el gate de validación existe para imponer.
