# Cómo ejecutar Cosmo de forma desatendida en un VPS

> Nota: esta traducción puede no estar actualizada. El inglés es la fuente canónica de esta documentación — consulta la [versión en inglés](../../en/how-to/setup-vps.md).

Objetivo: `cosmo run` vacía la cola durante la noche bajo systemd, se
reinicia solo si se traba, *no* se reinicia solo cuando un humano necesita
intervenir, y te cuenta lo que pasó.

Se asume un host de la familia Debian/Ubuntu con systemd y acceso root.

## 1. Instala las dependencias

```bash
sudo apt update
sudo apt install -y git docker.io curl
sudo systemctl enable --now docker
```

Luego, como el usuario bajo el cual correrá Cosmo:

```bash
# uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# gitleaks y openspec: instálalos según las instrucciones de su propio proyecto
# tu CLI de harness (Claude Code hoy): según sus propias instrucciones
```

Agrega el usuario de ejecución al grupo `docker` para que el gate pueda
iniciar contenedores sin sudo:

```bash
sudo usermod -aG docker cosmo
```

## 2. Instala Cosmo

El archivo de unidad asume el propio checkout de Cosmo en `/opt/cosmo`:

```bash
sudo git clone <this repo> /opt/cosmo
sudo chown -R cosmo:cosmo /opt/cosmo
cd /opt/cosmo
uv sync
```

Eso crea `/opt/cosmo/.venv/bin/cosmo`, que es lo que invoca la unidad.
Conserva el checkout — Cosmo lee sus plantillas de proyecto y de harness
desde `templates/` en el repositorio, no desde un wheel instalado.

## 3. Elige tus rutas

Los valores por defecto de XDG ponen el estado bajo el home del usuario de
ejecución. En un servidor, ponlo en un lugar explícito y dimensionado para
ello:

```bash
sudo mkdir -p /var/cosmo/{work,logs} /etc/cosmo
sudo chown -R cosmo:cosmo /var/cosmo /etc/cosmo
```

```toml
# /etc/cosmo/config.toml
[paths]
data_dir = "/var/cosmo"
work_dir = "/var/cosmo/work"
log_dir  = "/var/cosmo/logs"

[git]
base_branch = "develop"

[disk]
min_free_gb = 20.0
```

```bash
sudo chmod 600 /etc/cosmo/config.toml
sudo chown cosmo:cosmo /etc/cosmo/config.toml
```

El `600` importa — este archivo contendrá el token de tu bot de
notificaciones. Mantenlo fuera de cualquier repositorio.

Dimensiona el volumen para lo que realmente se acumula: un worktree por
cada tarea en curso, imágenes Docker (la imagen de Playwright por sí sola
pesa varios gigabytes), y logs del harness. `disk.min_free_gb` aborta una
ejecución antes de que comience en lugar de dejar que un disco lleno haga
fallar cada tarea con errores que se leen como errores de código.

## 4. Configura el repositorio objetivo

Cosmo necesita su **propio checkout** del repositorio sobre el que trabaja.
Nunca lo apuntes a un directorio en el que también trabaje un humano —
mantiene ese checkout en la rama base en todo momento para que la escalera
de merge pueda correr directamente contra él.

```bash
sudo -u cosmo git clone <your project> /var/cosmo/target-repo
sudo -u cosmo /opt/cosmo/.venv/bin/cosmo init /var/cosmo/target-repo \
    --project-template java-spring-react \
    --git-author-name "Cosmo" --git-author-email cosmo@yourdomain
```

Configura `COSMO_CONFIG=/etc/cosmo/config.toml` en tu shell, o pasa
`--config /etc/cosmo/config.toml`, para cada invocación manual.

## 5. Verifica antes de automatizar

```bash
sudo -u cosmo COSMO_CONFIG=/etc/cosmo/config.toml \
    /opt/cosmo/.venv/bin/cosmo doctor --project-path /var/cosmo/target-repo
```

Corrige cada `FAIL`. Los más comunes en una máquina nueva:

- `docker` — el usuario de ejecución no está en el grupo `docker`, o el
  cambio de grupo aún no ha tenido efecto en esta sesión.
- `subscription billing` — `ANTHROPIC_API_KEY` está exportada en algún
  lugar del perfil del usuario. Elimínala; cambia silenciosamente a
  facturación por token.
- `disk space` — por debajo de `disk.min_free_gb`.

Luego haz una prueba de humo del harness en sí, ya que una CLI que
funciona en tu laptop no implica que funcione aquí:

```bash
sudo -u cosmo COSMO_CONFIG=/etc/cosmo/config.toml \
    /opt/cosmo/.venv/bin/cosmo harness probe --prompt "reply with the word ok"
```

## 6. Instala las unidades de systemd

Se incluyen dos unidades en `deploy/`. Ninguna de las dos se instala con
ningún comando de Cosmo — las copias tú mismo.

```bash
sudo cp /opt/cosmo/deploy/cosmo-run.service \
        /opt/cosmo/deploy/cosmo-notify.service \
        /etc/systemd/system/
sudo systemctl daemon-reload
```

Antes de habilitarlas, edita los tres valores específicos del host en la
sección `[Service]` de `cosmo-run.service` — los únicos ajustes que un
archivo de unidad no puede obtener de la configuración de Cosmo:

| Directiva | Configúrala como |
| --- | --- |
| `WorkingDirectory` | el propio checkout de Cosmo (`/opt/cosmo`) |
| `Environment=COSMO_CONFIG` | tu archivo de configuración (`/etc/cosmo/config.toml`) |
| la ruta `--repo` en `ExecStart` | el repositorio **objetivo** (`/var/cosmo/target-repo`) |

Agrega una línea `User=` si no estás corriendo como root.
`cosmo-notify.service` necesita el mismo `WorkingDirectory` y
`COSMO_CONFIG`, y nada más.

```bash
sudo systemctl enable --now cosmo-run.service cosmo-notify.service
sudo systemctl status cosmo-run.service
sudo journalctl -u cosmo-run.service -f
```

## 7. Comprender la semántica de reinicio {#7-understand-the-restart-semantics}

Esta es la parte que vale la pena configurar bien, porque la configuración
ingenua convierte "un humano necesita revisar esto" en un bucle infinito.

```ini
Restart=on-failure
RestartPreventExitStatus=1
RestartSec=30
```

`cosmo run` sale con `0` solo ante una parada limpia por `completed` o
`queue_empty`. Cada parada deliberada — una pausa por circuit breaker, un
agotamiento de cuota confirmado, un techo de costo, un abort por disco, un
ciclo en el DAG de arranque — sale con `1`. Ninguno de esos casos se
soluciona reiniciando: una nueva ejecución arranca cada contador desde
cero, así que un techo de costo o de disco simplemente se volvería a
alcanzar. `RestartPreventExitStatus=1` hace que systemd los deje en paz.

Un proceso genuinamente trabado es un caso distinto. Nunca llega a
`sys.exit` en absoluto — `WatchdogSec` se dispara y systemd lo mata con una
*señal*, que no es un código de salida, así que la exclusión no aplica y sí
se reinicia.

```ini
Type=notify
WatchdogSec=10800
```

El bucle envía un ping al watchdog en cada transición de estado a nivel de
ejecución y una vez por cada tarea que el planificador recoge. Eso es
deliberadamente grueso: una sola tarea saludable puede legítimamente correr
por más de dos horas con los timeouts por defecto sin ningún ping en el
medio, así que `WatchdogSec` se configura bien por encima de ese peor caso.
La consecuencia es que una única tarea trabada se detecta en el siguiente
ping de límite de tarea después del timeout, no de inmediato. **Reajusta
`WatchdogSec` si reajustas `timeouts.*`** — los dos están acoplados.

```ini
StartLimitIntervalSec=3600
StartLimitBurst=5
```

Estos van en `[Unit]`, no en `[Service]`. systemd los rechaza bajo
`[Service]` (verás "Unknown key … ignoring" en el journal) y el límite de
tormenta de reinicios silenciosamente nunca se aplica.

```ini
OOMPolicy=stop
MemoryAccounting=yes
# MemoryMax=4G
```

Nunca vuelvas a lanzar silenciosamente hacia la misma presión de memoria
que acaba de matarlo. Una ejecución de diez horas manejando Docker,
almacenando en búfer JSON en streaming y corriendo Playwright es
exactamente la carga de trabajo a vigilar por una fuga lenta. `MemoryMax`
se deja comentado en lugar de adivinado — dimensiónalo una vez que tengas
números de uso reales.

## 8. Configura las notificaciones

No vas a estar mirando el journal a las 3am.

```bash
sudo -u cosmo COSMO_CONFIG=/etc/cosmo/config.toml \
    /opt/cosmo/.venv/bin/cosmo notify config
```

El asistente pregunta por un token de bot, descubre el id del chat mediante
`getUpdates` (guiándote primero para que le escribas al bot — los bots no
pueden escribir primero), escribe `[notify]` en tu archivo de
configuración, y envía un mensaje de prueba real antes de declarar éxito.
Luego:

```bash
sudo systemctl restart cosmo-notify.service
```

`cosmo-notify.service` es una unidad separada sin **ninguna dependencia de
orden** respecto de `cosmo-run.service`. Eso es deliberado: todo su valor
está en vigilar la *ausencia* de actividad, incluyendo el caso en que la
unidad de ejecución nunca arrancó o murió antes de escribir nada. La
entrega desde dentro del bucle de ejecución nunca podría reportar el
crash del propio bucle de ejecución.

Considera `min_severity = "info"` para un primer despliegue — el valor por
defecto `warning` es lo bastante silencioso como para que quizás no
recibas noticias de una ejecución saludable en absoluto.

## 9. Operarlo

```bash
# cómo terminó la noche
sudo -u cosmo COSMO_CONFIG=/etc/cosmo/config.toml cosmo report

# qué sigue atascado
cosmo queue ls --status blocked
cosmo queue failures <task_id>

# reanudar tras una pausa por circuit breaker, una vez arreglada la causa
cosmo run resume
```

Una ejecución `PAUSED` necesita un humano. `cosmo run resume` se vuelve a
conectar a ella con la contabilidad de costos, el barrido de reconciliación
y el bloqueo de proceso aplicándose exactamente igual que en una ejecución
nueva.

Encola el trabajo de mañana cuando quieras — `cosmo spec add` /
`cosmo spec queue` son seguros de ejecutar mientras una ejecución está en
curso; el planificador recalcula el conjunto elegible en cada pasada.

## Notas de hardening

- El host contiene credenciales reales, así que trátalo como tal. Cosmo
  nunca usa `bypassPermissions`; mantenlo así.
- Cosmo solo hace merge hacia `git.base_branch`. Hacer merge hacia
  `main`/`master` sigue siendo un paso humano, y nada en una ejecución
  desatendida debería tener acceso de push a esa rama.
- Mantén `/etc/cosmo/config.toml` en modo `600`. Contiene el token de tu
  bot.
- Consulta [SECURITY.md](../../../SECURITY.md) para el modelo de amenazas
  completo.
