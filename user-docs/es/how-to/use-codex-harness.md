# Usar el harness de Codex

> Nota: el inglés es la fuente canónica; consulta la [versión en
> inglés](../../en/how-to/use-codex-harness.md).

Cosmo puede ejecutar propose, implement y review mediante la CLI no interactiva
de Codex. La integración se validó con `codex-cli 0.153.0`; repite el probe al
actualizar Codex porque sus flags, eventos y sandbox pueden cambiar.

## Instalar, autenticar y seleccionar Codex

Instala Codex, inicia sesión de forma interactiva con la cuenta cuya cuota
quieres usar y verifica el acceso:

```bash
codex login
codex --version
cosmo harness probe --harness codex --prompt "Confirma brevemente que el probe funciona."
```

Cosmo conserva la autenticación guardada en `CODEX_HOME`, pero ignora la
configuración personal de comportamiento en cada etapa. Hooks personales,
servidores MCP, apps, plugins e instrucciones no pueden cambiar silenciosamente
una ejecución desatendida.

```bash
cosmo init ~/code/my-app --harness codex --project-template _blank
cosmo doctor --harness codex --project-path ~/code/my-app
cosmo run --harness codex --repo ~/code/my-app
```

El bootstrap instala política y hooks en `.agent/codex` y expone skills en
`.agents/skills`. No crea `.codex`: una ejecución real con 0.153.0 rechazó ese
symlink raíz como un montaje inseguro del sandbox. Los hooks se inyectan de
forma explícita.

## Configurar el modelo

El modelo global incluido es un id de Claude. Configura un modelo disponible en
tu cuenta de Codex dentro de la tabla específica del harness:

```toml
[harness.overrides.codex]
model = "gpt-5.6-sol"
# Opcionales:
# propose_model = "..."
# implement_model = "..."
# review_model = "..."
```

Los nombres disponibles pueden cambiar. Confirma el modelo con `cosmo harness
probe --harness codex` antes de una ejecución desatendida. Codex solo admite
`permission_mode = "dontAsk"`; preflight rechaza cualquier otro modo.

## Facturación, coste y cuota

Este adaptador usa deliberadamente la sesión guardada. Desconfigura
`CODEX_API_KEY`: `cosmo doctor --harness codex` falla si existe, evitando un
cambio silencioso a uso de API facturado aparte. No hay un modo Codex de API
facturada explícito.

El JSONL validado informa tokens de entrada, caché, salida y razonamiento, pero
no coste USD autoritativo, ventana de cuota ni hora de reinicio. Por ello:

- `reports_native_cost` es falso y Cosmo no puede aplicar sus topes USD usando
  gasto nativo de Codex.
- La cuota incluida y los límites externos se administran en la cuenta Codex.
- La detección de cuota solo puede usar los fallos terminales y heurísticas de
  tiempo de Cosmo; no identifica una ventana semanal desde el stream.

Los tokens del log son evidencia de uso, no un libro contable en dólares.

## Seguridad y limitaciones residuales

Codex usa `workspace-write`, aprobaciones no interactivas, sesiones efímeras,
hooks de Cosmo inyectados explícitamente y funciones alojadas innecesarias
desactivadas. La validación hostil real confirmó bloqueos para cambios de tests
protegidos, anotaciones prohibidas, Git destructivo y push, procesos en segundo
plano, escrituras de código durante review y lecturas de secretos conocidos.
El sandbox también rechazó una escritura fuera del worktree. Cosmo declara
`supports_gating = true` para este perfil validado.

Los hooks son defensa en profundidad, no una frontera de confidencialidad. El
sandbox de Codex contiene las escrituras. No habilites herramientas alojadas ni
el bypass irrestricto sin repetir la validación adversarial.

El sandbox protege además los metadatos Git del worktree enlazado. Durante
`IMPLEMENTING`, Codex edita archivos sin hacer stage ni commit. Tras una llamada
exitosa, Cosmo agrega la implementación (excluyendo `.agent`, `.agents` y
`.cosmo`) y crea el commit antes de validar. Es comportamiento esperado.

Referencias oficiales: [modo no interactivo](https://learn.chatgpt.com/docs/non-interactive-mode),
[hooks](https://learn.chatgpt.com/docs/hooks),
[configuración](https://learn.chatgpt.com/docs/config-file/config-reference) y
[skills](https://learn.chatgpt.com/docs/build-skills).
