import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mods" / "jira-worklog"))

import worklog  # noqa: E402
from worklog import NO_TICKET, Session, Ticket, Turn  # noqa: E402

UTC = timezone.utc


def at(hour, minute=0):
    return datetime(2026, 10, 27, hour, minute, tzinfo=UTC)


def etp_config(**values):
    return worklog.build_config({"JIRA_PROJECTS": "ETP", **values}, {})


def session_with(prompts, title="", branch="", cwd="/work/repo", results=None):
    session = Session(agent="omp", path=Path("/x.jsonl"), cwd=cwd, title=title)
    for index, prompt in enumerate(prompts):
        turn = session.open_turn(at(9, index * 10), prompt, branch)
        turn.result = (results or {}).get(index, "")
    return session


class DistributeTests(unittest.TestCase):
    def test_total_is_exactly_the_workday_and_follows_activity(self):
        cfg = etp_config(WORKDAY_HOURS="8", ROUND_MINUTES="15")
        tickets = {
            "ETP-1": Ticket("ETP-1", activity=timedelta(minutes=100)),
            "ETP-2": Ticket("ETP-2", activity=timedelta(minutes=50)),
            "ETP-3": Ticket("ETP-3", activity=timedelta(minutes=50)),
        }
        worklog.distribute(tickets, cfg)
        self.assertEqual(sum(t.minutes for t in tickets.values()), 480)
        self.assertEqual(tickets["ETP-1"].minutes, 240)
        self.assertTrue(all(t.minutes % 15 == 0 for t in tickets.values()))

    def test_unassigned_work_takes_no_hours(self):
        cfg = etp_config()
        tickets = {
            "ETP-1": Ticket("ETP-1", activity=timedelta(minutes=10)),
            NO_TICKET: Ticket(NO_TICKET, activity=timedelta(hours=5)),
        }
        worklog.distribute(tickets, cfg)
        self.assertEqual(tickets["ETP-1"].minutes, 480)
        self.assertEqual(tickets[NO_TICKET].minutes, 0)

    def test_commit_only_day_is_split_evenly(self):
        cfg = etp_config(WORKDAY_HOURS="6")
        tickets = {"ETP-1": Ticket("ETP-1"), "ETP-2": Ticket("ETP-2")}
        worklog.distribute(tickets, cfg)
        self.assertEqual([t.minutes for t in tickets.values()], [180, 180])


class TicketAttributionTests(unittest.TestCase):
    def test_single_key_in_request_switches_and_carries_over(self):
        cfg = etp_config()
        session = session_with(["arrancamos", "pasemos a ETP-7", "dale"], title="ETP: Review ETP-5")
        self.assertEqual(
            [k for k, _ in worklog.turn_tickets(session, cfg)], ["ETP-5", "ETP-7", "ETP-7"]
        )

    def test_request_listing_several_keys_does_not_switch(self):
        cfg = etp_config()
        session = session_with(["revisá ETP-10, ETP-11 y ETP-12", "seguimos"], branch="feature/ETP-5")
        self.assertEqual([k for k, _ in worklog.turn_tickets(session, cfg)], ["ETP-5", "ETP-5"])

    def test_title_beats_branch_and_branch_beats_directory(self):
        cfg = etp_config()
        with_title = session_with(["x"], title="Fix ETP-1", branch="feature/ETP-2", cwd="/w/ETP-3")
        with_branch = session_with(["x"], branch="feature/etp-2", cwd="/w/ETP-3")
        only_cwd = session_with(["x"], cwd="/w/ETP-3")
        self.assertEqual(worklog.turn_tickets(with_title, cfg), [("ETP-1", False)])
        self.assertEqual(worklog.turn_tickets(with_branch, cfg), [("ETP-2", False)])
        self.assertEqual(worklog.turn_tickets(only_cwd, cfg), [("ETP-3", False)])

    def test_mentions_are_a_flagged_last_resort(self):
        cfg = etp_config()
        session = session_with(
            ["armá el bot", "probalo"],
            results={0: "Creé ETP-9 para esto", 1: "Actualicé ETP-9; ETP-4 no aplica"},
        )
        self.assertEqual(worklog.turn_tickets(session, cfg), [("ETP-9", True), ("ETP-9", True)])
        self.assertEqual(worklog.turn_tickets(session_with(["hola"]), cfg), [(NO_TICKET, False)])

    def test_configured_projects_ignore_lookalikes(self):
        cfg = etp_config()
        self.assertEqual(worklog.find_keys("UTF-8, SHA-256, M-13 y etp-0042", cfg), ["ETP-42"])
        generic = worklog.build_config({}, {})
        self.assertEqual(worklog.find_keys("UTF-8 en ABC-12", generic), ["ABC-12"])


class ActivityTests(unittest.TestCase):
    def test_long_human_pause_is_not_work_and_agent_waits_are_capped(self):
        cfg = etp_config(IDLE_GAP_MINUTES="15", TURN_GAP_CAP_MINUTES="60")
        session = Session(agent="omp", path=Path("/x"))
        first = session.open_turn(at(9), "a")
        session.touch(at(9, 10))
        session.touch(at(11, 10))  # 2h waiting on a tool: capped at 60m
        session.open_turn(at(13), "b")  # 110m after the last answer: not work
        session.touch(at(13, 5))
        lo, hi = at(0), at(0) + timedelta(days=1)
        self.assertEqual(worklog.turn_activity(session, cfg, lo, hi), [timedelta(minutes=70), timedelta(minutes=5)])
        self.assertEqual(first.end, at(11, 10))

    def test_activity_is_clipped_to_the_day(self):
        cfg = etp_config()
        session = Session(agent="omp", path=Path("/x"))
        session.open_turn(at(23, 50), "a")
        session.touch(at(23, 50) + timedelta(minutes=20))
        lo, hi = at(0), at(0) + timedelta(days=1)
        self.assertEqual(worklog.turn_activity(session, cfg, lo, hi), [timedelta(minutes=10)])


class ParserTests(unittest.TestCase):
    def write(self, records):
        handle = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        with handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")
        self.addCleanup(Path(handle.name).unlink)
        return Path(handle.name)

    def test_omp_requests_answers_and_title(self):
        path = self.write([
            {"type": "session", "cwd": "/w/repo", "timestamp": "2026-10-27T09:00:00.000Z"},
            {"type": "title", "title": "\U000f04c2 ETP: Review ETP-5"},
            {"type": "message", "timestamp": "2026-10-27T09:00:01.000Z", "message": {"role": "user", "content": [{"type": "text", "text": "revisá"}]}},
            {"type": "message", "timestamp": "2026-10-27T09:00:05.000Z", "message": {"role": "toolResult", "content": []}},
            {"type": "message", "timestamp": "2026-10-27T09:01:00.000Z", "message": {"role": "assistant", "content": [{"type": "text", "text": "Listo"}]}},
        ])
        session = worklog.parse_omp(path)
        self.assertEqual((session.cwd, worklog.clean_title(session.title)), ("/w/repo", "Review ETP-5"))
        self.assertEqual([(t.prompt, t.result, len(t.stamps)) for t in session.turns], [("revisá", "Listo", 3)])

    def test_claude_skips_tool_results_injected_text_and_sidechains(self):
        base = {"cwd": "/w/repo", "gitBranch": "feature/ETP-8"}
        path = self.write([
            {**base, "type": "user", "timestamp": "2026-10-27T09:00:00Z", "message": {"role": "user", "content": "<command-name>/clear</command-name>"}},
            {**base, "type": "user", "timestamp": "2026-10-27T09:00:01Z", "message": {"role": "user", "content": "arreglá el filtro"}},
            {**base, "type": "assistant", "timestamp": "2026-10-27T09:00:02Z", "message": {"role": "assistant", "content": [{"type": "tool_use"}]}},
            {**base, "type": "user", "timestamp": "2026-10-27T09:00:03Z", "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}},
            {**base, "type": "user", "isSidechain": True, "timestamp": "2026-10-27T09:00:04Z", "message": {"role": "user", "content": "subtarea"}},
            {**base, "type": "assistant", "timestamp": "2026-10-27T09:00:09Z", "message": {"role": "assistant", "content": [{"type": "text", "text": "Arreglado"}]}},
            {"type": "custom-title", "customTitle": "filtro"},
        ])
        session = worklog.parse_claude(path)
        self.assertEqual(session.title, "filtro")
        self.assertEqual([(t.prompt, t.result, t.branch) for t in session.turns], [("arreglá el filtro", "Arreglado", "feature/ETP-8")])

    def test_codex_skips_injected_instructions(self):
        path = self.write([
            {"type": "session_meta", "timestamp": "2026-10-27T09:00:00Z", "payload": {"cwd": "/w/repo", "git": {"branch": "feature/ETP-3"}}},
            {"type": "response_item", "timestamp": "2026-10-27T09:00:01Z", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "# AGENTS.md instructions"}]}},
            {"type": "response_item", "timestamp": "2026-10-27T09:00:02Z", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "agregá la columna"}]}},
            {"type": "event_msg", "timestamp": "2026-10-27T09:03:00Z", "payload": {"type": "task_complete", "last_agent_message": "Agregada"}},
        ])
        session = worklog.parse_codex(path)
        self.assertEqual(session.branch, "feature/ETP-3")
        self.assertEqual([(t.prompt, t.result) for t in session.turns], [("agregá la columna", "Agregada")])


if __name__ == "__main__":
    unittest.main()
