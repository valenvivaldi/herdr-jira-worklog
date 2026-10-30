# Jira Worklog para Herdr

Mod de [Herdr](https://herdr.dev) que arma el borrador del worklog diario de Jira a partir de lo que ya hiciste con tus agentes (omp, Claude Code, Codex). Deja un archivo por día, dividido por ticket, con el tiempo repartido y un resumen de lo hecho. Vos lo revisás y lo cargás en cada ticket.

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

`cmd-shift-j` abre el worklog de hoy en un diálogo, regenerado en el momento. Adentro:

| Tecla | Qué hace |
|---|---|
| `t` | Worklog de hoy. |
| `y` | Día hábil anterior (el lunes muestra el viernes). |
| `r` | Regenerar. |
| `o` | Abrir `~/worklog/AAAA-MM-DD.md` con la app por defecto. |
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

Desde ahí, cada vez que un agente pasa de trabajando a terminado, se regenera el archivo de hoy, como mucho una vez por minuto (si terminan varios seguidos, corre una vez más al cumplirse el minuto). Solo funciona mientras se muestra ese layout. Un clon no recibe las actualizaciones del layout original de Herdr.

También se puede correr sin Herdr:

```bash
python3 mods/jira-worklog/worklog.py generate --date today --print
python3 mods/jira-worklog/worklog.py generate --date 2026-10-27
python3 mods/jira-worklog/worklog.py open --date yesterday
```

## Qué contiene el archivo

Para cada ticket:

- **Tiempo**: la jornada (8h por defecto) repartida entre los tickets en proporción a la actividad de los agentes, redondeada a 15 minutos. Suma siempre la jornada completa.
- **Comentario sugerido**: títulos de las sesiones y commits del día, como base del comentario del worklog.
- **Commits** tuyos del día que nombran el ticket.
- **Detalle**: hora, pedido que le hiciste al agente y primera línea de su respuesta.

Lo que no se pudo asociar a un ticket queda en **Sin ticket**, con su actividad, pero no recibe horas.

El archivo se regenera desde cero cada vez: copiá lo que necesites, no lo edites ahí.

## Cómo decide el ticket

Para cada pedido al agente, en este orden:

1. Si el pedido nombra **una sola** clave (`ETP-1234`), ese ticket pasa a ser el de la sesión, también para los pedidos siguientes. Un pedido que lista varias claves no cambia el ticket.
2. La clave del título de la sesión.
3. La clave de la rama git (`feature/ETP-1234`).
4. La clave en la ruta del directorio (worktrees por ticket).
5. Último recurso: la clave más mencionada en la sesión, marcada como *ticket inferido*. Revisala.

Para que salga bien sin pensar: mencioná el ticket en el primer pedido o trabajá en una rama `feature/ETP-XXXX`.

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
- El archivo incluye tus pedidos textuales a los agentes. Queda solo en tu máquina, con permisos `600`.

## Desarrollo

```bash
python3 -m unittest discover -s tests
```
