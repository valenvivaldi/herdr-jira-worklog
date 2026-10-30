# Jira Worklog para Herdr

Mod de [Herdr](https://herdr.dev) que arma el borrador del worklog diario de Jira a partir de lo que ya hiciste con tus agentes (omp, Claude Code, Codex). Deja una carpeta por día con un resumen por ticket (tiempo repartido y comentario sugerido) y un archivo por sesión con lo que hiciste en ella. Vos lo revisás y lo cargás en cada ticket.

No envía nada a Jira ni a ningún servidor: lee archivos locales y escribe en `~/worklog/`.

## Requisitos

- La app Herdr (`herdr-app`) 0.1.9 o superior. Ya no es un plugin del `herdr` viejo.
- `python3` (alcanza el que trae macOS, 3.9+) y `git`.
- Usar alguno de estos agentes: omp, Claude Code, Codex.

## Instalación

El mod es la carpeta `mods/jira-worklog/`. Clonás el repo y la enlazás en la carpeta de mods de Herdr, así `git pull` alcanza para actualizar:

```bash
git clone https://github.com/valenvivaldi/herdr-jira-worklog.git
ln -s "$PWD/herdr-jira-worklog/mods/jira-worklog" ~/.config/herdr-app/mods/jira-worklog
herdr-app check ~/.config/herdr-app/mods/jira-worklog
```

Después agregá el atajo de `keys.json` de este repo a `~/.config/herdr-app/keys.json`, dentro de `"global"` (si ya usás `cmd-shift-j`, elegí otra tecla):

```json
"cmd-shift-j": "mod.open:jira-worklog"
```

Sin atajo, desde cualquier terminal: `herdr-app do mod.open --name jira-worklog`.

### Venís del plugin del Herdr viejo

La configuración ahora vive en `~/.config/jira-worklog/config.env`. Si habías cambiado algo, movela:

```bash
mkdir -p ~/.config/jira-worklog
mv ~/.config/herdr/plugins/config/etendo.jira-worklog/config.env ~/.config/jira-worklog/
```

Los atajos `prefix+t` / `prefix+y` de `~/.config/herdr/config.toml` ya no hacen nada; se pueden borrar.

## Uso

`cmd-shift-j` abre el resumen de hoy en un diálogo, regenerado en el momento. Adentro:

| Tecla | Qué hace |
|---|---|
| `t` | Worklog de hoy. |
| `y` | Día hábil anterior (el lunes muestra el viernes). |
| `r` | Regenerar. |
| `o` | Abrir la carpeta del día (`~/worklog/AAAA-MM-DD/`). |
| `Esc` | Cerrar. |

### Regenerar solo cuando un agente termina

Para que el archivo del día se vaya completando solo, el mod tiene que estar vivo en tu layout. Los layouts que trae Herdr no se pueden editar, así que primero clonás el tuyo (en el ejemplo, `main`):

```bash
herdr-app do layout.clone '{"name":"main","as":"mi-layout"}'
```

y en `~/.config/herdr-app/layouts/mi-layout.json` agregás este bloque flotante en el nodo raíz (no dibuja nada):

```json
"floating": [
  { "id": "jira-worklog-sync", "content": { "mod": "jira-worklog", "props": { "sync": true } }, "edge": "bottom", "align": "end" }
]
```

Desde ahí, cada vez que un agente pasa de trabajando a terminado, se regenera la carpeta de hoy, como mucho una vez por minuto (si terminan varios seguidos, corre una vez más al cumplirse el minuto). Solo funciona mientras se muestra ese layout. Un clon no recibe las actualizaciones del layout original de Herdr.

También se puede correr sin Herdr:

```bash
python3 mods/jira-worklog/worklog.py generate --date today --print
python3 mods/jira-worklog/worklog.py generate --date 2026-10-27
python3 mods/jira-worklog/worklog.py open --date yesterday
```

## Qué contiene la carpeta del día

```
~/worklog/2026-10-30/
  resumen.md                                   horas por ticket, para cargar en Jira
  0915-omp-etp-1234-login-a1b2c3.md            una sesión: solo lo que hizo ese día
  1100-claude-schema-forge-mcp-barrido-9f8e7d.md
```

**`resumen.md`**, para cada ticket:

- **Tiempo**: la jornada (8h por defecto) repartida entre los tickets en proporción a la actividad de los agentes, redondeada a 15 minutos. Suma siempre la jornada completa.
- **Comentario sugerido**: títulos de las sesiones y commits del día, como base del comentario del worklog.
- **Commits** tuyos del día que nombran el ticket.
- **Sesiones**: link al archivo de cada sesión que trabajó en ese ticket.

Lo que no se pudo asociar a un ticket queda en **Sin ticket**, con su actividad, pero no recibe horas.

**Un archivo por sesión**, con su ticket, el agente y la carpeta, el tiempo activo del día y cada pedido de ese día (hora, lo que pediste y la primera línea de la respuesta). El nombre empieza con la hora en que la sesión arrancó ese día, así quedan en orden. Si retomás hoy una sesión de ayer, en la carpeta de hoy aparece su archivo solo con los pedidos de hoy; lo de ayer queda en la carpeta de ayer.

La carpeta se regenera desde cero cada vez (un archivo de una sesión que renombraste se reemplaza por el del nombre nuevo): copiá lo que necesites, no la edites.

## Cómo decide el ticket

**Lo más confiable: ponele a la sesión el nombre de su ticket** (`/rename ETP-1234 login` en omp, `/rename` en Claude Code). Si el nombre tiene una clave, esa es el ticket de toda la sesión, sin importar lo que digan los pedidos. Así una sesión es una tarea y no se mezcla con otras.

Sin clave en el nombre, para cada pedido al agente, en este orden:

1. Si el pedido nombra **una sola** clave (`ETP-1234`), ese ticket pasa a ser el de la sesión, también para los pedidos siguientes. Un pedido que lista varias claves no cambia el ticket.
2. La clave de la rama git (`feature/ETP-1234`).
3. La clave en la ruta del directorio (worktrees por ticket).
4. Último recurso: la clave más mencionada en la sesión, marcada como *ticket inferido*. Revisala.

## Configuración

La primera ejecución crea `~/.config/jira-worklog/config.env` con todas las opciones comentadas (otra carpeta: variable `WORKLOG_CONFIG_DIR`).

| Clave | Por defecto | Para qué |
|---|---|---|
| `WORKDAY_HOURS` | `8` | Horas a repartir por día. |
| `ROUND_MINUTES` | `15` | Redondeo de cada ticket. |
| `OUTPUT_DIR` | `~/worklog` | Dónde se escriben los archivos. |
| `JIRA_PROJECTS` | proyecto de jira-cli, si está | Prefijos válidos, ej. `ETP`. Evita confundir `UTF-8` con un ticket. |
| `JIRA_URL` | server de jira-cli, si está | Para poner el link a cada ticket. |
| `EXCLUDE_PATHS` | `/tmp`, `~/Library/Application Support` | Rutas que nunca cuentan como trabajo. |
| `INCLUDE_PATHS` | vacío (todo) | Si se completa, solo cuentan las sesiones bajo esas rutas (ej. `~/Workspace`). |
| `IDLE_GAP_MINUTES` | `15` | Pausa entre respuesta y próximo pedido a partir de la cual no se cuenta. |
| `TURN_GAP_CAP_MINUTES` | `60` | Tope para una espera dentro de un mismo pedido. |

Si tenés [jira-cli](https://github.com/ankitpokhrel/jira-cli) configurado, el proyecto y la URL se toman de ahí y no hace falta tocar nada.

## Limitaciones

- El tiempo es una estimación proporcional, no un registro de horas reales: no ve reuniones ni trabajo hecho fuera de los agentes.
- Solo lee los transcripts de omp, Claude Code y Codex en esta máquina.
- Los archivos incluyen tus pedidos textuales a los agentes. Quedan solo en tu máquina: la carpeta del día con permisos `700` y cada archivo con `600`.

## Desarrollo

```bash
python3 -m unittest discover -s tests
```
