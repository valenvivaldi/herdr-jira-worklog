#!/usr/bin/env python3
"""Daily Jira worklog draft built from local coding-agent transcripts.

Reads the transcripts that omp, Claude Code and Codex already keep on disk,
groups the day's work by Jira key, splits the workday proportionally to the
recorded activity and writes one Markdown file per day. Nothing is sent to
Jira: the person copies each section into the matching ticket.

Python 3.9+ standard library only, so it runs on a stock macOS install.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Tuple

PLUGIN_ID = "etendo.jira-worklog"
NO_TICKET = "Sin ticket"
WEEKDAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
# Uppercase tokens that look like Jira keys but are not (UTF-8, SHA-256, ...).
GENERIC_KEY_STOPWORDS = {
    "UTF", "SHA", "ISO", "RFC", "CVE", "HTTP", "HTTPS", "TLS", "SSL", "MD", "AES",
    "RSA", "ECMA", "IEEE", "GPT", "CLAUDE", "OPUS", "SONNET", "HAIKU", "PEP", "WCAG",
}
PRIVATE_USE = re.compile("[\ue000-\uf8ff\U000f0000-\U0010ffff]")
TITLE_CATEGORY = re.compile(r"^[A-Z]{2,10}:\s+")
MARKDOWN_NOISE = re.compile(r"[*_`#>]+")

DEFAULT_CONFIG = """\
# Configuración de jira-worklog. Formato CLAVE=valor; las líneas con # se ignoran.
# Borrá el # de una línea para cambiar su valor.

# Horas de la jornada que se reparten entre los tickets del día.
#WORKDAY_HOURS=8

# Redondeo del tiempo de cada ticket, en minutos.
#ROUND_MINUTES=15

# Carpeta donde se escribe un archivo YYYY-MM-DD.md por día.
#OUTPUT_DIR=~/worklog

# Prefijos de proyecto Jira, separados por coma (ej. ETP,EPL).
# Vacío: se toma el proyecto configurado en jira-cli, o cualquier clave ABC-123.
#JIRA_PROJECTS=

# URL base de Jira para los links. Vacío: se toma de jira-cli si está configurado.
#JIRA_URL=

# Rutas (prefijos, separados por coma) que nunca cuentan como trabajo.
#EXCLUDE_PATHS=/tmp,/private/tmp,~/Library/Application Support

# Si tiene valor, solo cuentan las sesiones bajo estas rutas (ej. ~/Workspace).
#INCLUDE_PATHS=

# Una pausa más larga que esto entre que el agente responde y el próximo
# pedido se considera fuera del trabajo.
#IDLE_GAP_MINUTES=15

# Tope para una espera dentro de un mismo pedido (agente trabajando o bloqueado).
#TURN_GAP_CAP_MINUTES=60

# Carpetas de transcripts. Vacío: ubicación por defecto de cada agente.
#OMP_SESSIONS_DIR=
#CLAUDE_PROJECTS_DIR=
#CODEX_SESSIONS_DIR=
"""


# --------------------------------------------------------------------------- config


@dataclass
class Config:
    workday_minutes: int = 8 * 60
    round_minutes: int = 15
    output_dir: Path = Path.home() / "worklog"
    key_pattern: re.Pattern = re.compile(r"\b([A-Z][A-Z0-9]{1,9})-(\d+)\b")
    generic_keys: bool = True
    jira_url: str = ""
    exclude_paths: Tuple[str, ...] = ()
    include_paths: Tuple[str, ...] = ()
    idle_gap: timedelta = timedelta(minutes=15)
    turn_gap_cap: timedelta = timedelta(minutes=60)
    omp_dir: Path = Path.home() / ".omp" / "agent" / "sessions"
    claude_dir: Path = Path.home() / ".claude" / "projects"
    codex_dir: Path = Path.home() / ".codex" / "sessions"


def expand(path: str) -> str:
    return os.path.normpath(os.path.expanduser(path.strip()))


def split_paths(raw: str) -> Tuple[str, ...]:
    return tuple(expand(p) for p in raw.split(",") if p.strip())


def parse_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def jira_cli_settings() -> Dict[str, str]:
    """Server URL and project key from ankitpokhrel/jira-cli, when present."""
    path = Path(os.environ.get("JIRA_CONFIG_FILE") or Path.home() / ".config" / ".jira" / ".config.yml")
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    found: Dict[str, str] = {}
    server = re.search(r"^server:\s*(\S+)", text, re.M)
    if server:
        found["url"] = server.group(1).rstrip("/")
    project = re.search(r"^project:\s*\n(?:[ \t]+.*\n)*?[ \t]+key:\s*(\S+)", text, re.M)
    if project:
        found["project"] = project.group(1)
    return found


def config_dir() -> Optional[Path]:
    env_dir = os.environ.get("HERDR_PLUGIN_CONFIG_DIR")
    if env_dir:
        return Path(env_dir)
    herdr = os.environ.get("HERDR_BIN_PATH") or "herdr"
    try:
        out = subprocess.run([herdr, "plugin", "config-dir", PLUGIN_ID], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None


def load_config() -> Config:
    values: Dict[str, str] = {}
    cdir = config_dir()
    if cdir is not None:
        cfile = cdir / "config.env"
        if not cfile.exists():
            cdir.mkdir(parents=True, exist_ok=True)
            cfile.write_text(DEFAULT_CONFIG, encoding="utf-8")
        values = parse_env_file(cfile)
    return build_config(values, jira_cli_settings())


def build_config(values: Dict[str, str], jira_cli: Dict[str, str]) -> Config:
    cfg = Config()

    def get(key: str) -> str:
        return (values.get(key) or "").strip()

    if get("WORKDAY_HOURS"):
        cfg.workday_minutes = round(float(get("WORKDAY_HOURS").replace(",", ".")) * 60)
    if get("ROUND_MINUTES"):
        cfg.round_minutes = max(1, int(get("ROUND_MINUTES")))
    if get("OUTPUT_DIR"):
        cfg.output_dir = Path(expand(get("OUTPUT_DIR")))
    projects = [p.strip().upper() for p in (get("JIRA_PROJECTS") or jira_cli.get("project", "")).split(",") if p.strip()]
    if projects:
        alternatives = "|".join(re.escape(p) for p in projects)
        cfg.key_pattern = re.compile(r"(?<![A-Za-z0-9])(" + alternatives + r")-(\d+)\b", re.I)
        cfg.generic_keys = False
    cfg.jira_url = (get("JIRA_URL") or jira_cli.get("url", "")).rstrip("/")
    cfg.exclude_paths = split_paths(get("EXCLUDE_PATHS") if "EXCLUDE_PATHS" in values else "/tmp,/private/tmp,~/Library/Application Support")
    cfg.include_paths = split_paths(get("INCLUDE_PATHS"))
    if get("IDLE_GAP_MINUTES"):
        cfg.idle_gap = timedelta(minutes=float(get("IDLE_GAP_MINUTES")))
    if get("TURN_GAP_CAP_MINUTES"):
        cfg.turn_gap_cap = timedelta(minutes=float(get("TURN_GAP_CAP_MINUTES")))
    claude_home = os.environ.get("CLAUDE_CONFIG_DIR")
    codex_home = os.environ.get("CODEX_HOME")
    if claude_home:
        cfg.claude_dir = Path(claude_home) / "projects"
    if codex_home:
        cfg.codex_dir = Path(codex_home) / "sessions"
    for key, attr in (("OMP_SESSIONS_DIR", "omp_dir"), ("CLAUDE_PROJECTS_DIR", "claude_dir"), ("CODEX_SESSIONS_DIR", "codex_dir")):
        if get(key):
            setattr(cfg, attr, Path(expand(get(key))))
    return cfg


# --------------------------------------------------------------------------- model


@dataclass
class Turn:
    """One user request and everything the agent did until the next request."""

    start: datetime
    prompt: str
    branch: str = ""
    stamps: List[datetime] = field(default_factory=list)
    result: str = ""

    @property
    def end(self) -> datetime:
        return self.stamps[-1] if self.stamps else self.start


@dataclass
class Session:
    agent: str
    path: Path
    cwd: str = ""
    title: str = ""
    branch: str = ""
    turns: List[Turn] = field(default_factory=list)

    def open_turn(self, ts: datetime, prompt: str, branch: str = "") -> Turn:
        turn = Turn(start=ts, prompt=prompt, branch=branch or self.branch, stamps=[ts])
        self.turns.append(turn)
        return turn

    def touch(self, ts: datetime) -> Optional[Turn]:
        if not self.turns:
            return None
        turn = self.turns[-1]
        if ts >= turn.end:
            turn.stamps.append(ts)
        return turn


def parse_ts(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or len(value) < 19:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        # Python 3.9 only accepts 3 or 6 fractional digits.
        match = re.match(r"(.{19})(?:\.(\d+))?(Z|[+-]\d\d:\d\d)?$", value)
        if not match:
            return None
        frac = (match.group(2) or "0")[:6].ljust(6, "0")
        tz = match.group(3) or "+00:00"
        try:
            parsed = datetime.fromisoformat(f"{match.group(1)}.{frac}{tz.replace('Z', '+00:00')}")
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def read_jsonl(path: Path) -> Iterator[dict]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                yield record


def text_blocks(content: object, kinds: Iterable[str] = ("text",)) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    wanted = set(kinds)
    return "\n".join(
        block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") in wanted and isinstance(block.get("text"), str)
    )


def is_injected(text: str) -> bool:
    """Harness-generated user messages (instructions, command echoes, reminders)."""
    stripped = text.lstrip()
    return not stripped or stripped.startswith("<") or stripped.startswith("# AGENTS.md") or stripped.startswith("Caveat:")


# --------------------------------------------------------------------------- parsers


def parse_omp(path: Path) -> Session:
    session = Session(agent="omp", path=path)
    for rec in read_jsonl(path):
        kind = rec.get("type")
        if kind == "session":
            session.cwd = rec.get("cwd") or session.cwd
            continue
        if kind in ("title", "title_change") and isinstance(rec.get("title"), str):
            session.title = rec["title"]
            continue
        if kind != "message":
            continue
        ts = parse_ts(rec.get("timestamp"))
        msg = rec.get("message") or {}
        if ts is None or not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "user" and msg.get("attribution", "user") == "user":
            prompt = text_blocks(msg.get("content"))
            if not is_injected(prompt):
                session.open_turn(ts, prompt)
                continue
        turn = session.touch(ts)
        if turn is not None and role == "assistant":
            text = text_blocks(msg.get("content"))
            if text.strip():
                turn.result = text
    return session


def parse_claude(path: Path) -> Session:
    session = Session(agent="claude", path=path)
    custom_title = ""
    for rec in read_jsonl(path):
        kind = rec.get("type")
        if kind == "custom-title" and rec.get("customTitle"):
            custom_title = rec["customTitle"]
            continue
        if kind == "ai-title" and rec.get("aiTitle"):
            session.title = rec["aiTitle"]
            continue
        if kind not in ("user", "assistant") or rec.get("isSidechain"):
            continue
        ts = parse_ts(rec.get("timestamp"))
        if ts is None:
            continue
        session.cwd = rec.get("cwd") or session.cwd
        if rec.get("gitBranch"):
            session.branch = rec["gitBranch"]
        msg = rec.get("message") or {}
        content = msg.get("content") if isinstance(msg, dict) else None
        if kind == "user" and not rec.get("isMeta"):
            prompt = text_blocks(content)
            if not is_injected(prompt):
                session.open_turn(ts, prompt, rec.get("gitBranch") or "")
                continue
        turn = session.touch(ts)
        if turn is not None and kind == "assistant":
            text = text_blocks(content)
            if text.strip():
                turn.result = text
    if custom_title:
        session.title = custom_title
    return session


def parse_codex(path: Path) -> Session:
    session = Session(agent="codex", path=path)
    for rec in read_jsonl(path):
        kind = rec.get("type")
        payload = rec.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        if kind in ("session_meta", "turn_context"):
            session.cwd = payload.get("cwd") or session.cwd
            git = payload.get("git") or {}
            if isinstance(git, dict) and git.get("branch"):
                session.branch = git["branch"]
            continue
        ts = parse_ts(rec.get("timestamp"))
        if ts is None:
            continue
        if kind == "response_item" and payload.get("type") == "message" and payload.get("role") == "user":
            prompt = text_blocks(payload.get("content"), ("input_text", "text"))
            if not is_injected(prompt):
                session.open_turn(ts, prompt)
                continue
        turn = session.touch(ts)
        if turn is None:
            continue
        if kind == "event_msg" and payload.get("type") == "task_complete" and payload.get("last_agent_message"):
            turn.result = payload["last_agent_message"]
        elif kind == "response_item" and payload.get("type") == "message" and payload.get("role") == "assistant":
            text = text_blocks(payload.get("content"), ("output_text", "text"))
            if text.strip():
                turn.result = text
    return session


def transcript_files(cfg: Config, since: datetime) -> Iterator[Tuple[Callable[[Path], Session], Path]]:
    """Top-level transcripts touched since `since`; subagent transcripts are skipped."""
    cutoff = since.timestamp()
    sources: List[Tuple[Callable[[Path], Session], Iterable[Path]]] = []
    if cfg.omp_dir.is_dir():
        sources.append((parse_omp, cfg.omp_dir.glob("*/*.jsonl")))
    if cfg.claude_dir.is_dir():
        sources.append((parse_claude, cfg.claude_dir.glob("*/*.jsonl")))
    if cfg.codex_dir.is_dir():
        sources.append((parse_codex, cfg.codex_dir.rglob("rollout-*.jsonl")))
    for parser, paths in sources:
        for path in paths:
            try:
                if path.stat().st_mtime >= cutoff:
                    yield parser, path
            except OSError:
                continue


# --------------------------------------------------------------------------- attribution


def find_keys(text: str, cfg: Config) -> List[str]:
    keys: List[str] = []
    for match in cfg.key_pattern.finditer(text or ""):
        prefix = match.group(1).upper()
        if cfg.generic_keys and prefix in GENERIC_KEY_STOPWORDS:
            continue
        key = f"{prefix}-{int(match.group(2))}"
        if key not in keys:
            keys.append(key)
    return keys


def first_key(text: str, cfg: Config) -> Optional[str]:
    keys = find_keys(text, cfg)
    return keys[0] if keys else None


def turn_tickets(session: Session, cfg: Config, current_branch: str = "") -> List[Tuple[str, bool]]:
    """Ticket for each turn, plus whether it was inferred.

    A request that names exactly one key switches the session to it, and the
    following requests keep it. A request naming several keys is a reference
    list, not a switch. Before any switch, the session title, the git branch
    and the working directory decide. As a last resort the key the session
    mentions most (requests and answers) is used and flagged as inferred.
    """
    title_key = first_key(clean_title(session.title), cfg)
    cwd_key = first_key(session.cwd, cfg)
    mentions: Counter = Counter()
    for turn in session.turns:
        mentions.update(set(find_keys(turn.prompt, cfg)) | set(find_keys(turn.result, cfg)))
    inferred = mentions.most_common(1)[0][0] if mentions else None
    carried: Optional[str] = None
    tickets: List[Tuple[str, bool]] = []
    for turn in session.turns:
        named = find_keys(turn.prompt, cfg)
        if len(named) == 1:
            carried = named[0]
        known = carried or title_key or first_key(turn.branch or current_branch, cfg) or cwd_key
        if known:
            tickets.append((known, False))
        else:
            tickets.append((inferred, True) if inferred else (NO_TICKET, False))
    return tickets


def clip(start: datetime, end: datetime, lo: datetime, hi: datetime) -> timedelta:
    a, b = max(start, lo), min(end, hi)
    return b - a if b > a else timedelta(0)


def turn_activity(session: Session, cfg: Config, lo: datetime, hi: datetime) -> List[timedelta]:
    """Active time of each turn inside [lo, hi).

    Gaps inside a turn (agent working, waiting on tools or subagents) count up
    to `turn_gap_cap`. The gap between the agent's last record and the next
    request is human reading/typing time and counts only when shorter than
    `idle_gap`.
    """
    result = []
    for index, turn in enumerate(session.turns):
        total = timedelta(0)
        stamps = turn.stamps
        for a, b in zip(stamps, stamps[1:]):
            total += min(clip(a, b, lo, hi), cfg.turn_gap_cap)
        if index + 1 < len(session.turns):
            following = session.turns[index + 1].start
            if following - turn.end <= cfg.idle_gap:
                total += clip(turn.end, following, lo, hi)
        result.append(total)
    return result


def path_allowed(cwd: str, cfg: Config) -> bool:
    if not cwd:
        return not cfg.include_paths
    path = os.path.normpath(cwd)

    def under(prefix: str) -> bool:
        return path == prefix or path.startswith(prefix.rstrip(os.sep) + os.sep)

    if any(under(p) for p in cfg.exclude_paths):
        return False
    return not cfg.include_paths or any(under(p) for p in cfg.include_paths)


# --------------------------------------------------------------------------- report


@dataclass
class Entry:
    start: datetime
    prompt: str
    result: str
    agent: str
    workdir: str


@dataclass
class Ticket:
    key: str
    activity: timedelta = timedelta(0)
    entries: List[Entry] = field(default_factory=list)
    sessions: Dict[str, str] = field(default_factory=dict)  # transcript path -> label
    titles: List[str] = field(default_factory=list)
    commits: List[Tuple[str, str]] = field(default_factory=list)
    minutes: int = 0


def git(cwd: str, *args: str) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def repo_root(cwd: str, cache: Dict[str, Optional[str]]) -> Optional[str]:
    if cwd not in cache:
        cache[cwd] = (git(cwd, "rev-parse", "--show-toplevel") or "").strip() or None if os.path.isdir(cwd) else None
    return cache[cwd]


def day_commits(repo: str, lo: datetime, hi: datetime) -> List[Tuple[str, str]]:
    email = (git(repo, "config", "user.email") or "").strip()
    if not email:
        return []
    out = git(repo, "log", "--all", "--no-merges", f"--author={email}", f"--since={lo.isoformat()}", f"--until={hi.isoformat()}", "--format=%h%x09%s")
    commits = []
    for line in (out or "").splitlines():
        sha, _, subject = line.partition("\t")
        if sha:
            commits.append((sha, subject))
    return commits


def build_report(cfg: Config, day: date, now: Optional[datetime] = None) -> Dict[str, Ticket]:
    lo = datetime.combine(day, datetime.min.time()).astimezone()
    hi = lo + timedelta(days=1)
    tickets: Dict[str, Ticket] = {}
    repos: Dict[str, Optional[str]] = {}
    branches: Dict[str, str] = {}
    active_repos: Dict[str, None] = {}

    for parser, path in transcript_files(cfg, lo):
        try:
            session = parser(path)
        except OSError:
            continue
        if not session.turns or not path_allowed(session.cwd, cfg):
            continue
        activity = turn_activity(session, cfg, lo, hi)
        in_day = [i for i, turn in enumerate(session.turns) if lo <= turn.start < hi or activity[i] > timedelta(0)]
        if not in_day:
            continue
        root = repo_root(session.cwd, repos) if session.cwd else None
        if root:
            active_repos[root] = None
        current_branch = ""
        if session.agent != "claude" and root:
            if root not in branches:
                branches[root] = (git(root, "branch", "--show-current") or "").strip()
            current_branch = branches[root]
        keys = turn_tickets(session, cfg, current_branch)
        workdir = os.path.basename(root or session.cwd) or "?"
        title = clean_title(session.title)
        label = f"{session.agent} en {workdir}" + (f" «{title}»" if title else "")
        title_key = first_key(title, cfg)
        for i in in_day:
            turn = session.turns[i]
            key, inferred = keys[i]
            ticket = tickets.setdefault(key, Ticket(key))
            ticket.activity += activity[i]
            ticket.sessions[str(path)] = label + (" (ticket inferido por las menciones en la sesión)" if inferred else "")
            if title and title_key in (None, key) and title not in ticket.titles:
                ticket.titles.append(title)
            if lo <= turn.start < hi:
                ticket.entries.append(Entry(turn.start, turn.prompt, turn.result, session.agent, workdir))

    seen = set()
    for repo in active_repos:
        for sha, subject in day_commits(repo, lo, hi):
            if sha in seen:
                continue
            seen.add(sha)
            key = first_key(subject, cfg) or NO_TICKET
            tickets.setdefault(key, Ticket(key)).commits.append((sha, subject))

    distribute(tickets, cfg)
    return tickets


def distribute(tickets: Dict[str, Ticket], cfg: Config) -> None:
    """Split the workday across real tickets, proportional to activity.

    Whole rounding units are assigned by largest remainder, so the total is
    exactly the workday. A ticket with negligible activity can end with 0m.
    With no recorded activity at all (only commits), the day is split evenly.
    """
    real = [t for k, t in tickets.items() if k != NO_TICKET]
    for ticket in tickets.values():
        ticket.minutes = 0
    if not real:
        return
    units = max(1, round(cfg.workday_minutes / cfg.round_minutes))
    weights = [t.activity.total_seconds() for t in real]
    total = sum(weights)
    shares = [(w / total if total else 1 / len(real)) * units for w in weights]
    alloc = [int(s) for s in shares]
    order = sorted(range(len(real)), key=lambda i: (shares[i] - alloc[i], weights[i]), reverse=True)
    for i in order[: units - sum(alloc)]:
        alloc[i] += 1
    for ticket, count in zip(real, alloc):
        ticket.minutes = count * cfg.round_minutes


# --------------------------------------------------------------------------- render


def clean_title(title: str) -> str:
    title = PRIVATE_USE.sub("", title or "").strip()
    return TITLE_CATEGORY.sub("", title).strip()


def one_line(text: str, limit: int) -> str:
    for line in (text or "").splitlines():
        line = MARKDOWN_NOISE.sub("", line).strip(" -:\t")
        if line:
            text = re.sub(r"\s+", " ", line)
            return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
    return ""


def fmt_minutes(minutes: int) -> str:
    hours, mins = divmod(int(minutes), 60)
    if hours and mins:
        return f"{hours}h {mins}m"
    return f"{hours}h" if hours else f"{mins}m"


def fmt_delta(delta: timedelta) -> str:
    return fmt_minutes(round(delta.total_seconds() / 60))


def suggested_comment(ticket: Ticket) -> List[str]:
    lines: List[str] = list(ticket.titles)
    for _, subject in ticket.commits:
        subject = re.sub(r"^(Feature|Hotfix|Issue|Epic)\s+[A-Z][A-Z0-9]+-\d+:\s*", "", subject)
        if subject not in lines:
            lines.append(subject)
    return lines


def render(tickets: Dict[str, Ticket], cfg: Config, day: date, generated: datetime) -> str:
    real = sorted((t for k, t in tickets.items() if k != NO_TICKET), key=lambda t: (-t.minutes, t.key))
    loose = tickets.get(NO_TICKET)
    out = [f"# Worklog {day.isoformat()} ({WEEKDAYS[day.weekday()]})", ""]
    if real:
        out.append(f"Jornada de {fmt_minutes(cfg.workday_minutes)} repartida en {len(real)} ticket(s) según la actividad de los agentes.")
    else:
        out.append("No hay trabajo asociado a tickets de Jira en este día.")
    out += [f"Generado {generated.strftime('%Y-%m-%d %H:%M')}. Se regenera solo: copiá cada sección a su ticket, no edites este archivo.", ""]
    if real:
        out += ["| Ticket | Tiempo | Actividad registrada |", "|---|---|---|"]
        total_activity = sum((t.activity for t in real), timedelta(0))
        for t in real:
            pct = round(100 * t.activity / total_activity) if total_activity else 0
            out.append(f"| {t.key} | {fmt_minutes(t.minutes)} | {fmt_delta(t.activity)} ({pct}%) |")
        out.append("")
    for t in real:
        out += render_ticket(t, cfg, with_hours=True)
    if loose and (loose.entries or loose.commits):
        out += ["---", "", f"## {NO_TICKET} · {fmt_delta(loose.activity)} de actividad (no se reparte)", "",
                "Mencioná la clave en el pedido al agente o trabajá en una rama `feature/ABC-123` para que se asigne sola.", ""]
        out += render_ticket(loose, cfg, with_hours=False)[2:]
    return "\n".join(out).rstrip() + "\n"


def render_ticket(t: Ticket, cfg: Config, with_hours: bool) -> List[str]:
    out = [f"## {t.key} · {fmt_minutes(t.minutes)}" if with_hours else f"## {t.key}", ""]
    if with_hours and cfg.jira_url:
        out += [f"{cfg.jira_url}/browse/{t.key}", ""]
    comment = suggested_comment(t)
    if with_hours and comment:
        out += ["**Comentario sugerido:**", ""] + [f"- {line}" for line in comment] + [""]
    if t.commits:
        out += ["**Commits:**", ""] + [f"- `{sha}` {subject}" for sha, subject in t.commits] + [""]
    if t.sessions:
        out += ["**Sesiones:** " + "; ".join(sorted(set(t.sessions.values()))), ""]
    if t.entries:
        out += ["**Detalle:**", ""]
        for entry in sorted(t.entries, key=lambda e: e.start):
            stamp = entry.start.astimezone().strftime("%H:%M")
            out.append(f"- {stamp} ({entry.agent}, {entry.workdir}) {one_line(entry.prompt, 160) or '(sin texto)'}")
            result = one_line(entry.result, 220)
            if result:
                out.append(f"  → {result}")
        out.append("")
    return out


# --------------------------------------------------------------------------- commands


def parse_day(value: str) -> date:
    today = date.today()
    value = (value or "today").strip().lower()
    if value in ("today", "hoy"):
        return today
    if value in ("yesterday", "ayer"):
        previous = today - timedelta(days=1)
        while previous.weekday() >= 5:
            previous -= timedelta(days=1)
        return previous
    return date.fromisoformat(value)


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)
    os.replace(tmp, path)


def generate(day: date, cfg: Optional[Config] = None) -> Path:
    cfg = cfg or load_config()
    tickets = build_report(cfg, day)
    path = cfg.output_dir / f"{day.isoformat()}.md"
    write_atomic(path, render(tickets, cfg, day, datetime.now()))
    return path


def cmd_generate(args: argparse.Namespace) -> int:
    path = generate(parse_day(args.date))
    if args.print:
        sys.stdout.write(path.read_text(encoding="utf-8"))
    else:
        print(path)
    return 0


def cmd_view(args: argparse.Namespace) -> int:
    path = generate(parse_day(args.date or os.environ.get("WORKLOG_DATE", "today")))
    pager = os.environ.get("WORKLOG_PAGER") or "less"
    try:
        return subprocess.call([pager, str(path)])
    except OSError:
        sys.stdout.write(path.read_text(encoding="utf-8"))
        input("\nEnter para cerrar ")
        return 0


def cmd_open(args: argparse.Namespace) -> int:
    path = generate(parse_day(args.date))
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    return subprocess.call([opener, str(path)])


def cmd_show(args: argparse.Namespace) -> int:
    """Plugin action: open the viewer as a Herdr popup for the requested day."""
    herdr = os.environ.get("HERDR_BIN_PATH") or "herdr"
    plugin = os.environ.get("HERDR_PLUGIN_ID") or PLUGIN_ID
    return subprocess.call([
        herdr, "plugin", "pane", "open", "--plugin", plugin, "--entrypoint", "view",
        "--placement", "popup", "--width", "90%", "--height", "90%",
        "--env", f"WORKLOG_DATE={args.date}",
    ])


def cmd_hook(args: argparse.Namespace) -> int:
    """Event hook: refresh today's file when an agent finishes, at most once a minute."""
    try:
        event = json.loads(os.environ.get("HERDR_PLUGIN_EVENT_JSON") or "{}")
    except ValueError:
        event = {}
    data = event.get("data") if isinstance(event, dict) else None
    status = (data or {}).get("agent_status") if isinstance(data, dict) else None
    if status not in ("done", "idle"):
        return 0
    state = Path(os.environ.get("HERDR_PLUGIN_STATE_DIR") or tempfile.gettempdir()) / "last-hook-run"
    try:
        if time.time() - state.stat().st_mtime < args.throttle:
            return 0
    except OSError:
        pass
    state.parent.mkdir(parents=True, exist_ok=True)
    state.touch()
    generate(date.today())
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="worklog", description="Borrador diario de worklog de Jira a partir de los transcripts de agentes.")
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate", help="Regenerar el archivo de un día")
    gen.add_argument("--date", default="today", help="today | yesterday | YYYY-MM-DD")
    gen.add_argument("--print", action="store_true", help="Mostrar el contenido en vez de la ruta")
    gen.set_defaults(func=cmd_generate)
    view = sub.add_parser("view", help="Regenerar y mostrar en el pager")
    view.add_argument("--date", default=None)
    view.set_defaults(func=cmd_view)
    opener = sub.add_parser("open", help="Regenerar y abrir con la app por defecto")
    opener.add_argument("--date", default="today")
    opener.set_defaults(func=cmd_open)
    show = sub.add_parser("show", help="Abrir el visor como popup de Herdr")
    show.add_argument("--date", default="today")
    show.set_defaults(func=cmd_show)
    hook = sub.add_parser("hook", help="Hook de pane.agent_status_changed")
    hook.add_argument("--throttle", type=float, default=60.0)
    hook.set_defaults(func=cmd_hook)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
