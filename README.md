# Jira Worklog para Herdr

Plugin de [Herdr](https://herdr.dev) que arma el borrador del worklog diario de Jira a partir de lo que ya hiciste con tus agentes (omp, Claude Code, Codex). Deja un archivo por día, dividido por ticket, con el tiempo repartido y un resumen de lo hecho. Vos lo revisás y lo cargás en cada ticket.

No envía nada a Jira ni a ningún servidor: lee archivos locales y escribe en `~/worklog/`.

## Requisitos

- Herdr 0.9.5 o superior.
- `python3` (alcanza el que trae macOS, 3.9+) y `git`.
- Usar alguno de estos agentes: omp, Claude Code, Codex.

## Instalación

```bash
herdr plugin install valenvivaldi/herdr-jira-worklog
```

Para actualizar a la última versión: `herdr plugin update etendo.jira-worklog`.

Para modificarlo, trabajá sobre una copia local enlazada:

```bash
git clone https://github.com/valenvivaldi/herdr-jira-worklog.git
herdr plugin link "$PWD/herdr-jira-worklog"
```

Para verificar que quedó instalado: `herdr plugin action list --plugin etendo.jira-worklog`.

## Uso

Desde Herdr, con el plugin activo:

| Acción | Qué hace |
|---|---|
| `Worklog: ver hoy` | Regenera y muestra el worklog de hoy en un popup (`q` para cerrar). |
| `Worklog: ver día hábil anterior` | Lo mismo para el día hábil anterior (el lunes muestra el viernes). |
| `Worklog: abrir archivo de hoy` | Regenera y abre `~/worklog/AAAA-MM-DD.md` con la app por defecto. |

Para tenerlo a mano en un atajo, en el `config.toml` de Herdr:

```toml
[[keys.command]]
key = "prefix+w"
type = "plugin_action"
command = "etendo.jira-worklog.today"
description = "worklog de hoy"
```

Además, cada vez que un agente termina un pedido, el plugin regenera el archivo del día (como mucho una vez por minuto). El archivo se va completando solo a lo largo del día.

También se puede correr sin Herdr:

```bash
python3 worklog.py generate --date today --print
python3 worklog.py generate --date 2026-10-27
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

La primera ejecución crea el archivo de configuración con todas las opciones comentadas. Para ver dónde quedó:

```bash
herdr plugin config-dir etendo.jira-worklog   # el archivo es config.env
```

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
