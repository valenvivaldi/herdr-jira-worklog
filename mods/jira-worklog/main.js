// Jira Worklog: the daily worklog built by worklog.py (next to this file) into ~/worklog/<day>/:
// resumen.md (hours per ticket, shown here) plus one file per session.
// Dialog (mod.open:jira-worklog): t today, y previous workday, r regenerate, o open the day's
// folder, Esc close.
// With props {sync: true} (an invisible floating block in the layout) it regenerates today's
// folder whenever an agent finishes, at most once a minute.

import { View, div } from "gpui-kit";
import { v_flex, h_flex, TextView } from "gpui-base";
import { exec, appHome, props, closeOverlay, theme, agents } from "herdr";

const SCRIPT = `${appHome()}/mods/jira-worklog/worklog.py`;

const DAYS = [["today", "Hoy", "t"], ["yesterday", "Día hábil anterior", "y"]];

function worklog(args) {
  return exec("python3", [SCRIPT, ...args]);
}

const THROTTLE_MS = 60_000;
const BUSY = new Set(["working", "blocked"]);

class Sync {
  constructor() {
    this.status = new Map(agents().map((a) => [a.address, a.status]));
    this.last = 0;
    this.pending = false;
  }

  // Called on every render, i.e. whenever Herdr's live data changes.
  observe(cx) {
    let finished = false;
    for (const a of agents()) {
      if (BUSY.has(this.status.get(a.address)) && !BUSY.has(a.status)) finished = true;
      this.status.set(a.address, a.status);
    }
    if (!finished || this.pending) return;
    // Throttled with a trailing run, so the last finish of a burst is always included.
    this.pending = true;
    cx.timer.after(Math.max(0, this.last + THROTTLE_MS - Date.now()), (cx) => {
      this.pending = false;
      this.last = Date.now();
      cx.spawn(async () => {
        const r = await worklog(["generate", "--date", "today"]).catch((e) => ({ code: -1, stderr: String(e) }));
        if (r.code !== 0) console.log(`jira-worklog: generate failed: ${(r.stderr || r.stdout || "").trim()}`);
      });
    });
  }
}

export default class JiraWorklog extends View {
  init(_props, cx) {
    if (props()?.sync) { this.sync = new Sync(); return; }
    this.day = "today";
    this.text = "";
    this.error = null;
    this.loading = false;
    this.handle = cx.focus_handle();
    this.handle.focus();
    this.load(cx);
  }

  load(cx) {
    const day = this.day;
    this.loading = true;
    cx.spawn(async (cx) => {
      try {
        const r = await worklog(["generate", "--date", day, "--print"]);
        if (day !== this.day) return;
        if (r.code !== 0) throw new Error((r.stderr || r.stdout).trim() || `worklog.py salió con código ${r.code}`);
        this.text = r.stdout;
        this.error = null;
      } catch (e) {
        if (day !== this.day) return;
        this.error = String(e?.message ?? e);
      }
      this.loading = false;
      cx.notify();
    });
  }

  select(day, cx) {
    if (day === this.day) return;
    this.day = day;
    this.text = "";
    this.load(cx);
    cx.notify();
  }

  openFile(cx) {
    cx.spawn(async (cx) => {
      try {
        const r = await worklog(["open", "--date", this.day]);
        if (r.code !== 0) throw new Error((r.stderr || r.stdout).trim());
      } catch (e) {
        this.error = String(e?.message ?? e);
        cx.notify();
      }
    });
  }

  button(id, label, active, onClick) {
    const t = theme();
    return div()
      .id(id)
      .h(28)
      .px(12)
      .flex()
      .items_center()
      .rounded(7)
      .text_size(12.5)
      .bg(active ? t.selected : t.raised)
      .text_color(active ? t.foreground : t.muted)
      .hover((s) => s.bg(t.hover))
      .on_click(onClick)
      .child(label);
  }

  render(cx) {
    if (this.sync) { this.sync.observe(cx); return div(); }
    const t = theme();
    const body = this.error
      ? div().p(16).text_color(t.blocked).child(this.error)
      : !this.text
        ? div().p(16).text_color(t.dim).child("Generando…")
        : TextView.markdown(`worklog-${this.day}`, this.text).selectable(true).scrollable(true).size_full();
    return v_flex()
      .id("jira-worklog")
      .track_focus(this.handle)
      .on_key_down((e, cx) => {
        if (e.key === "escape") return closeOverlay(props().overlay);
        if (e.key === "t") this.select("today", cx);
        else if (e.key === "y") this.select("yesterday", cx);
        else if (e.key === "r") { this.load(cx); cx.notify(); }
        else if (e.key === "o") this.openFile(cx);
      })
      .size_full()
      .min_h(0)
      .p(18)
      .gap(12)
      .bg(t.popover)
      .text_color(t.foreground)
      .child(
        h_flex()
          .gap(8)
          .items_center()
          .child(div().text_size(15).font_semibold().mr(8).child("Jira Worklog"))
          .children(DAYS.map(([day, label, key]) =>
            this.button(`day-${day}`, `${label}  ${key}`, this.day === day, (_e, cx) => this.select(day, cx))))
          .child(div().flex_1())
          .child(div().text_size(11.5).text_color(t.dim).child(this.loading ? "Regenerando…" : ""))
          .child(this.button("reload", "Regenerar  r", false, (_e, cx) => { this.load(cx); cx.notify(); }))
          .child(this.button("open", "Abrir carpeta  o", false, (_e, cx) => this.openFile(cx)))
          .child(this.button("close", "Esc", false, () => closeOverlay(props().overlay))),
      )
      .child(div().flex_1().min_h(0).rounded(9).border_1().border_color(t.border).bg(t.background).p(14).child(body));
  }
}
