# Cosmo

[🇬🇧 English](README.md) | 🇪🇸 Español

> Nota: esta traducción puede no estar actualizada. El inglés es la fuente canónica de esta documentación — consulta la [versión en inglés](README.md).

> **v0.1.1 — no listo para producción.** Las pruebas en
> el mundo real hasta ahora se han limitado al harness de Claude Code con
> una suscripción Pro de $20/mes, contra proyectos greenfield pequeños
> construidos sobre la plantilla `vite-react-local` (stack solo de
> frontend). Otros harnesses, repositorios más grandes o brownfield, y las
> plantillas que incluyen backend están implementados pero no probados a
> fondo. Espera asperezas fuera de ese camino.

**Un agente de codificación que trabaja toda la noche te dirá que terminó. Cosmo no
se queda con su palabra.**

Cosmo ejecuta una cola de tareas de desarrollo guiadas por specs contra tu repositorio
mientras duermes. Cada tarea se construye en su propio `git worktree` y tiene que
sobrevivir a un build de Docker real, pruebas unitarias y una ejecución de Playwright
antes de que una sola línea llegue a tu rama. El propio reporte de éxito del agente se
trata como telemetría, no como evidencia.

```console
$ cosmo init ~/code/my-app --project-template vite-react-local
harness: claude (from config default)
project template: vite-react-local
git branch: git init, then created and checked out 'develop'
openspec/ created
docs/: created 7, skipped (already exists) 0
.agent/claude/: synced (template_version=da0446ae5a99)
  created CLAUDE.md -> .agent/claude/CLAUDE.md
  created .claude -> .agent/claude
  created agents -> .agent/claude/agents
  created skills -> .agent/claude/skills
registered project my-app-997f83de

$ cosmo spec add add-login --repo ~/code/my-app --from ./login-idea.md
# ... enriquecimiento + descomposición, luego una vista previa de los archivos
#     de tarea escritos bajo docs/specs/add-login-spec/tasks/ -- nada se encola todavía

$ cosmo spec queue add-login --repo ~/code/my-app
$ cosmo run --repo ~/code/my-app
01:54:20Z >> run.started
01:54:31Z >> task.state_changed [add-login] queued -> proposing
02:11:07Z >> task.state_changed [add-login] implementing -> validating
02:19:44Z >> task.validation_result [add-login] passed=True, unit=pass (14p/0f/0s), e2e=pass (3p/0f/0s)
02:20:12Z >> task.completed [add-login]
...
stopped (queue_empty)
completed=3 blocked=0 requeued=0 retried=1
```

![Cosmo ejecutando una cola de tareas durante la noche](assets/cosmo-demo.gif)

## Por qué existe esto

Cuatro cosas fallan en una configuración ingenua de "dejar el agente trabajando toda
la noche", y Cosmo está construido alrededor de las cuatro:

- **Las pruebas se manipulan.** Un agente que no puede hacer que una prueba pase
  siempre puede eliminarla, marcarla con `@Disabled` o hacerle `.skip(`. Cosmo bloquea
  esas ediciones *antes de que ocurran* con hooks `PreToolUse`, y luego cuenta las
  aserciones en el diff y falla la tarea si disminuyeron. Ver
  [validation-gate-and-guardrails](user-docs/es/concepts/validation-gate-and-guardrails.md).
- **Los procesos se filtran.** Un proceso de Maven, Node, Chromium o Docker que se
  mata pero mantiene vivos a sus hijos consume la memoria del host hasta que cada
  tarea posterior falla por razones no relacionadas. Cosmo mata todo el grupo de
  procesos y luego barre en busca de contenedores huérfanos y titulares de worktree.
- **Una prueba e2e inestable agota el presupuesto de reintentos.** Cosmo vuelve a
  ejecutar una prueba e2e no puesta en cuarentena que falló, de forma aislada, antes de
  creerla, y mantiene una lista de cuarentena versionada donde cada entrada debe tener
  un responsable y una fecha de vencimiento — una entrada vencida rompe el gate en
  lugar de proteger silenciosamente a una prueba muerta.
- **El estado se filtra entre tareas.** Cada tarea obtiene su propio `git worktree` y
  su propia rama. Sin cambios de rama, sin trabajo a medio aplicar de la tarea
  anterior.

## Instalación

```bash
git clone <this repo> cosmo && cd cosmo
uv sync
uv tool install --editable .    # opcional: pone `cosmo` en tu PATH
```

Luego `cosmo doctor` para verificar que el host tiene git, Docker, `openspec`,
`gitleaks` y un harness funcional. Prerrequisitos completos y primera ejecución:
[el tutorial](user-docs/es/tutorial.md).

## Cómo funciona, de un vistazo

1. `cosmo init <repo>` prepara un repositorio objetivo — `openspec/`, una plantilla de
   `docs/`, y la política operativa del harness y los hooks de guardrail bajo
   `.agent/<harness>/`.
2. Metes trabajo en la cola escribiendo un spec preliminar y dejando que Cosmo lo
   enriquezca y descomponga (`cosmo spec add` → `cosmo spec queue`), o redactando a
   mano un cambio de [OpenSpec](https://github.com/Fission-AI/OpenSpec) y
   encolándolo directamente (`cosmo queue add`).
3. `cosmo run` vacía la cola en orden de dependencias, una tarea a la vez: worktree
   nuevo → proponer → implementar → **gate de validación** → revisión adversarial por
   una sesión sin memoria de la implementación → merge a tu rama de integración.
4. Un fallo reintenta con el detalle real del error retroalimentado; una tarea que no
   se puede arreglar queda como `BLOCKED` y la cola sigue adelante. Suficientes
   bloqueos distintos activan un circuit breaker y pausan la ejecución para un humano.
5. Todo queda registrado en SQLite local más un registro de eventos de solo
   anexado, de modo que `cosmo report`, `cosmo events tail` y `cosmo queue failures`
   pueden reconstruir la noche sin que tengas que leer logs en bruto.

## Documentación

- **[Tutorial](user-docs/es/tutorial.md)** — primer proyecto, primera tarea, de
  principio a fin.
- **Guías prácticas** — [configuración de VPS](user-docs/es/how-to/setup-vps.md) ·
  [configuración de WSL2](user-docs/es/how-to/setup-wsl2.md) ·
  [cuotas y gasto](user-docs/es/how-to/configure-quotas.md) ·
  [agregar una plantilla de proyecto](user-docs/es/how-to/add-project-template.md) ·
  [escribir un adaptador de harness](user-docs/es/how-to/write-a-new-adapter.md)
- **Referencia** — [CLI](user-docs/es/reference/cli.md) ·
  [esquema de configuración](user-docs/es/reference/config-schema.md) ·
  [esquema de eventos](user-docs/es/reference/event-schema.md)
- **Conceptos** — [resumen de arquitectura](user-docs/es/concepts/architecture-overview.md) ·
  [gate de validación y guardrails](user-docs/es/concepts/validation-gate-and-guardrails.md) ·
  [modelo de cuotas y seguridad](user-docs/es/concepts/quota-and-safety-model.md)
- [FAQ](FAQ.es.md) · [Solución de problemas](TROUBLESHOOTING.es.md) ·
  [Contribuir](CONTRIBUTING.md) · [Seguridad](SECURITY.md)

## Agnóstico de harness por diseño

Cosmo nunca invoca directamente un CLI de agente de codificación. Cada llamada pasa
por una única interfaz de adaptador, y ningún código de orquestación se ramifica
según qué harness esté configurado. **Claude Code es el único adaptador implementado
hoy** — eso es un punto de partida, no el techo. Escribir otro es una sola clase:
[write-a-new-adapter](user-docs/es/how-to/write-a-new-adapter.md).

## Tus skills y agentes propios se sobrescriben — lee esto antes de confiar en alguno

Cosmo controla [OpenSpec](https://github.com/Fission-AI/OpenSpec)
internamente para cada paso de propose/apply/archive. Es una dependencia que
`cosmo doctor` verifica, no algo que configures tú.

Cosmo también es dueño de `.agent/<harness>/` en el repositorio objetivo — el
directorio hacia el que apuntan los symlinks `.claude/agents` y
`.claude/skills`. En cada `cosmo init` **y en cada creación de worktree por
tarea**, ese directorio completo se borra y se recrea desde la propia
`templates/harness/<harness>/` de Cosmo. Cualquier skill de OpenSpec, agente
propio, o skill de terceros para Claude Code que hayas instalado a mano en
`.claude/agents/` o `.claude/skills/` de ese repositorio no sobrevive a la
siguiente sincronización — no hay merge, y esto aplica a cualquier adaptador
de harness, no solo a Claude Code.

Si quieres que una capacidad se mantenga, agrégala a la propia
`templates/harness/<name>/` de Cosmo para que forme parte de lo que se
sincroniza — ver
[write-a-new-adapter](user-docs/es/how-to/write-a-new-adapter.md#la-plantilla-del-harness).

El corolario: no apuntes `git.base_branch` a la rama donde haces tu propio
trabajo interactivo con el agente de codificación, con skills, agentes o
hooks propios comprometidos en `.claude/`. Dale a Cosmo una rama de
integración dedicada que nunca cargue tu configuración personal del
harness, para que una sincronización desatendida — y el merge de vuelta a
esa rama — no pueda borrarla silenciosamente.

## Hoja de ruta

Nada de lo siguiente existe todavía. Se documenta aquí para que la intención
sea visible, no como un compromiso:

- **Un wrapper de MCP** alrededor de la cola y el control de ejecución, para
  que un editor u otro agente pueda controlar Cosmo sin invocar el CLI
  directamente.
- **Un adaptador y una plantilla de harness para Cursor** — una segunda
  implementación de `HarnessAdapter`, para probar el diseño agnóstico de
  harness contra una segunda herramienta real.
- **Una pequeña webapp para monitorear ejecuciones** — una vista de solo
  lectura sobre el registro de eventos y el estado de la cola, para seguir
  una ejecución nocturna sin usar `cosmo events tail` en una terminal.

¿Quieres construir alguna? [write-a-new-adapter](user-docs/es/how-to/write-a-new-adapter.md)
es el punto de partida para el adaptador de Cursor; abre un issue para
discutir las otras dos.

## Licencia

Apache License 2.0 — ver [LICENSE](LICENSE). Las secciones 7 y 8 de esa
licencia ya renuncian a toda garantía y limitan la responsabilidad — el
software se entrega "TAL CUAL" ("AS IS"), y tú asumes el riesgo de usarlo.

## El nombre

Cosmo, de *kosmos* — orden a partir del caos. Es lo que más necesita una cola
desatendida de trabajo de agentes, y lo que el gate de validación está ahí para
imponer.

## Autor

Mauricio Endara —
[mauricioendara.com](https://mauricioendara.com) ·
[entropiainversa.com](https://entropiainversa.com) ·
[LinkedIn](https://www.linkedin.com/in/mauricio-endara-leon/) ·
[mauendara@gmail.com](mailto:mauendara@gmail.com)
