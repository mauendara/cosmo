# Cómo ejecutar Cosmo en Windows mediante WSL2

> Nota: esta traducción puede no estar actualizada. El inglés es la fuente canónica de esta documentación — consulta la [versión en inglés](../../en/how-to/setup-wsl2.md).

Cosmo se comporta de forma idéntica en un VPS Linux y bajo WSL2. Hay dos
cosas específicas de WSL2 y ambas te van a jugar en contra si te las saltas:
**la elección del sistema de archivos** y **systemd**.

## 1. Mantén todo en el sistema de archivos de WSL2

Ubica el checkout de Cosmo, su `work_dir`, y el repositorio objetivo bajo
`/home/...` dentro de la distribución WSL2 — **nunca** bajo `/mnt/c/...`.

Los archivos en `/mnt/c` pasan por el puente 9p hacia el sistema de archivos
de Windows. El I/O de Maven y `node_modules` ahí es lo suficientemente lento
como para distorsionar cada timeout en la configuración de Cosmo, y
periódicamente es inestable bajo Docker. Un build que toma cuatro minutos de
forma nativa puede tomar veinte en `/mnt/c`, lo que significa que tus valores
de `implementing_stall` y `stage_timeout_seconds` ahora están mal, y vas a
pasar una noche viendo cómo las tareas expiran por timeout sin ninguna razón
que un log pueda explicar.

`cosmo doctor` advierte sobre esto explícitamente:

```
warn  work dir filesystem  /mnt/c/cosmo/work is on a Windows drive mount;
                           builds there are slow enough to distort the
                           timeouts. Prefer a path inside the WSL2 filesystem.
```

Es una advertencia, no un fallo, así que no detendrá una ejecución. Trátala
como si lo hiciera.

Igual puedes abrir el repositorio desde Windows — el remoto WSL de VS Code
funciona bien contra `/home/...`, y `\\wsl$\<distro>\home\you\...` es
navegable desde el Explorador. Simplemente no lo conviertas en la ubicación
de almacenamiento.

## 2. Habilita systemd

WSL2 solo ejecuta un systemd real (como PID 1, no un shim de compatibilidad)
si se lo pides explícitamente:

```ini
# /etc/wsl.conf
[boot]
systemd=true
```

Luego, desde Windows:

```powershell
wsl --shutdown
```

y vuelve a iniciar la distribución. Verifica:

```bash
ps -p 1 -o comm=      # debería imprimir: systemd
systemctl --version
```

Sin esto, las unidades systemd en `deploy/` no funcionarán y necesitarías
otro supervisor — un gestor de procesos diferente, o el habitual recurso de
WSL2 de un script disparado al iniciar sesión. Eso está fuera del alcance de
este documento, pero verifícalo en cualquier máquina *nueva* antes de asumir
que las unidades funcionan sin modificaciones.

## 3. Docker

Cualquiera de las dos opciones funciona:

- **Docker Desktop** con la integración de WSL2 habilitada para tu
  distribución.
- **Docker Engine instalado dentro de la distribución** (`sudo apt install
  docker.io`), lo cual es más simple si no quieres tener Docker Desktop
  corriendo.

Confírmalo desde dentro de WSL2, no desde PowerShell:

```bash
docker run --rm hello-world
```

`cosmo doctor` verifica que `docker` esté en el `PATH`; el gate necesita que
realmente pueda iniciar contenedores, así que ejecuta también lo anterior.

## 4. Instala y arranca

Idéntico a cualquier host Linux:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# instala gitleaks, openspec y tu CLI de harness según sus propias instrucciones

git clone <this repo> ~/cosmo && cd ~/cosmo
uv sync
uv tool install --editable .

cosmo doctor
cosmo init ~/code/my-app --project-template vite-react-local
cosmo doctor --project-path ~/code/my-app
```

Para la primera ejecución completa, sigue el [tutorial](../tutorial.md).

## 5. Ejecuta bajo systemd

Las unidades en `deploy/` están escritas para una instalación a nivel de
sistema, pero una sesión de usuario suele ser lo que quieres en una máquina
personal:

```bash
mkdir -p ~/.config/systemd/user
cp ~/cosmo/deploy/cosmo-run.service ~/cosmo/deploy/cosmo-notify.service \
   ~/.config/systemd/user/
```

Edita la sección `[Service]` de cada copia para tus rutas:

```ini
WorkingDirectory=/home/you/cosmo
Environment=COSMO_CONFIG=/home/you/.config/cosmo/config.toml
ExecStart=/home/you/cosmo/.venv/bin/cosmo run --repo /home/you/code/my-app
```

Luego:

```bash
systemctl --user daemon-reload
systemctl --user enable --now cosmo-run.service cosmo-notify.service
systemctl --user status cosmo-run.service
journalctl --user -u cosmo-run.service -f
```

Mantén la sesión de usuario activa a través del cierre de sesión para que
una ejecución nocturna sobreviva a que cierres la terminal:

```bash
sudo loginctl enable-linger $USER
```

La semántica de reinicio es la misma que en un VPS y vale la pena
entenderla antes de depender de ella — consulta
[setup-vps, paso 7](setup-vps.md#7-understand-the-restart-semantics).

Una nota específica de WSL2 sobre los archivos de unidad: `StartLimitIntervalSec`
y `StartLimitBurst` deben estar bajo `[Unit]`, no bajo `[Service]`. El
systemd reciente los rechaza bajo `[Service]` con "Unknown key … ignoring"
en el journal, y el límite de tormenta de reinicios silenciosamente nunca se
aplica. Las unidades incluidas ya los tienen en el lugar correcto.

## 6. Peculiaridades específicas de Windows

**La máquina entrando en suspensión.** Una ejecución nocturna termina en el
momento en que Windows se suspende. Configura el plan de energía para que
nunca entre en suspensión mientras esté conectada a la corriente, o
ejecútalo en una máquina que no lo haga.

**`wsl --shutdown` mata todo.** Incluyendo una ejecución en curso. Cosmo se
recupera de forma limpia en el siguiente arranque — las tareas en curso se
emiten como `task.interrupted` y se vuelven a encolar, y la fila
`run_state` abandonada se cierra como `crashed` — pero el trabajo en
progreso se pierde.

**Memoria.** WSL2 toma por defecto una fracción de la RAM del host. Un build
de Maven más Playwright más Chromium no es una carga de trabajo pequeña. Si
los contenedores están siendo eliminados por OOM, aumenta el límite:

```ini
# %UserProfile%\.wslconfig  (on Windows, then `wsl --shutdown`)
[wsl2]
memory=12GB
```

**Finales de línea.** Si Windows Git hizo el checkout del repositorio con
CRLF, los scripts de shell y los hooks dentro de él se comportarán mal bajo
WSL2. Haz el clone desde dentro de WSL2, o configura `core.autocrlf=input`
en el repositorio objetivo.

**Disco.** El disco virtual de WSL2 crece pero no se reduce por sí solo. Las
imágenes Docker, los worktrees y los logs se van acumulando.
`disk.min_free_gb` aborta una ejecución antes de que comience en lugar de
dejar que un disco lleno haga fallar cada tarea con errores que se leen
como errores de código — pero de todas formas tienes que podar las
imágenes Docker tú mismo.

**Desfase del reloj tras la reanudación.** Una VM suspendida puede
despertar con el reloj desfasado, lo cual se manifiesta como timestamps y
duraciones de eventos sin sentido. Si lo ves, ejecuta `sudo hwclock -s`
desde dentro de WSL2.
