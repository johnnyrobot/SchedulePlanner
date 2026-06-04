"""
report_export.py — render a build's results into one self-contained HTML file.

Produces a single, offline, dependency-free HTML document (inline CSS + a little
inline JS, no external requests) that mirrors the app's on-screen analysis and is
WCAG 2.1 Level A/AA compliant: semantic landmarks, a skip link, a single h1 with
ordered headings, table headers with scope, keyboard-operable controls with
accessible names, visible focus, AA-tuned colour contrast, a reader text-size
control, a light/dark theme toggle, and a responsive (reflowing) layout.

This is a *frozen snapshot* renderer: it takes the same results dict the pywebview
UI renders (engine.run output, optionally flattened with the live-data panels) and
emits a standalone page that opens in any browser with no app bridge. It mirrors
ui.html's structure but targets a different runtime, so the two are kept separate.

Pure Python, stdlib only (``html`` for escaping) — no new dependency, nothing to
bundle. All data-derived strings are escaped (workbook / live-API data is
untrusted, exactly as ui.html's escapeHtml treats it); static markup is literal.
"""
from __future__ import annotations
import html
import math

# --------------------------------------------------------------------------- CSS
# Tokens copy the app's palette (ui.html :root + [data-theme="dark"]) but every
# text/background pair used here is verified >= 4.5:1 (AA, normal text) and every
# control/focus boundary >= 3:1 (AA, non-text). Three on-tint accents are darkened
# from the app's values so badge/flag TEXT clears 4.5:1:
#   light amber 6.29 · red 5.18 · green 5.26   dark red 5.19 (app's 3.79/4.37/3.84)
# Type is rem-based and multiplied by --scale (the reader text-size control), so it
# scales with both the control and browser zoom (1.4.4 / 1.4.10 / 1.4.12).
CSS = """
:root{
  --scale:1;
  --bg:#f3f2ea; --panel:#ffffff; --line:#dcdacb; --ctl:#8a8c7a; --ink:#23271c;
  --dim:#666b59; --amber:#6d4d0c; --green:#3f6a26; --red:#9c3a26; --blue:#3a6a82;
  --header:linear-gradient(180deg,#eceadd,#f3f2ea);
  --ok-bg:#e1eed2; --warn-bg:#f4e7c6; --bad-bg:#f4dacf;
  --ok-ink:#3f6a26; --warn-ink:#6d4d0c; --bad-ink:#9c3a26;
  --hair:#00000012; --dots:#00000007; --focus:#6d4d0c;
  --mono:ui-monospace,"SFMono-Regular","JetBrains Mono",Menlo,Consolas,monospace;
  --sans:ui-sans-serif,-apple-system,"Segoe UI",system-ui,sans-serif;
}
[data-theme="dark"]{
  --bg:#11140f; --panel:#181c14; --line:#2c3326; --ctl:#687158; --ink:#e8ead8;
  --dim:#8b9178; --amber:#d9a441; --green:#7faa57; --red:#d98162; --blue:#6f97a8;
  --header:linear-gradient(180deg,#1a1f15,#13160f);
  --ok-bg:#27331c; --warn-bg:#3a2f17; --bad-bg:#3a201a;
  --ok-ink:#7faa57; --warn-ink:#d9a441; --bad-ink:#d98162;
  --hair:#ffffff12; --dots:#ffffff08; --focus:#d9a441;
}
*{box-sizing:border-box;margin:0;padding:0}
html{font-size:calc(100% * var(--scale))}
body{background:var(--bg);color:var(--ink);font-family:var(--sans);
  font-size:.875rem;line-height:1.55;
  background-image:radial-gradient(circle at 1px 1px,var(--dots) 1px,transparent 0);
  background-size:1.375rem 1.375rem}
a{color:var(--blue)}
.skip{position:absolute;left:-9999px;top:0;z-index:50;background:var(--panel);
  color:var(--ink);border:2px solid var(--focus);border-radius:4px;padding:.5rem .75rem;
  font-family:var(--mono);font-size:.8125rem;text-decoration:none}
.skip:focus{left:.5rem;top:.5rem}
a:focus-visible,button:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
header{display:flex;align-items:center;justify-content:space-between;gap:1rem;
  flex-wrap:wrap;padding:1.1rem 1.6rem;border-bottom:1px solid var(--line);
  background:var(--header)}
.brand{display:flex;align-items:baseline;gap:.75rem;flex-wrap:wrap}
.brand h1{font-family:var(--mono);font-size:1rem;font-weight:600;letter-spacing:.04em}
.brand .tag{font-family:var(--mono);font-size:.6875rem;color:var(--dim);
  text-transform:uppercase;letter-spacing:.16em}
.meta{font-family:var(--mono);font-size:.75rem;color:var(--dim);margin-top:.15rem}
.toolbar{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap}
.toolbar .grp{display:flex;align-items:center;gap:.25rem;border:1px solid var(--ctl);
  border-radius:3px;padding:.15rem}
.tbtn{font-family:var(--mono);font-size:.75rem;background:transparent;color:var(--ink);
  border:1px solid var(--ctl);border-radius:3px;padding:.35rem .6rem;cursor:pointer;
  font-weight:600;letter-spacing:.02em;min-height:1.6rem}
.toolbar .grp .tbtn{border:none}
.tbtn:hover{border-color:var(--amber);color:var(--amber)}
main{padding:1.6rem;max-width:60rem;margin:0 auto}
.card{background:var(--panel);border:1px solid var(--line);border-radius:4px;
  padding:1.1rem 1.25rem;margin-top:.9rem}
.card h2{font-family:var(--mono);font-size:.95rem;letter-spacing:.02em;
  display:flex;justify-content:space-between;align-items:center;gap:.6rem;flex-wrap:wrap}
.card h3{font-family:var(--mono);font-size:.8125rem;color:var(--blue);
  text-transform:uppercase;letter-spacing:.1em;margin-bottom:.4rem}
.badge{font-family:var(--mono);font-size:.625rem;padding:.2rem .5rem;border-radius:2px;
  text-transform:uppercase;letter-spacing:.1em;white-space:nowrap}
.b-ok{background:var(--ok-bg);color:var(--ok-ink)}
.b-warn{background:var(--warn-bg);color:var(--warn-ink)}
.b-bad{background:var(--bad-bg);color:var(--bad-ink)}
.cohorts{display:grid;grid-template-columns:1fr 1fr;gap:.75rem;margin-top:.9rem}
.cohort{border:1px solid var(--line);border-radius:3px;padding:.75rem .9rem}
.cohort .lab{font-family:var(--mono);font-size:.6875rem;color:var(--dim);
  text-transform:uppercase;letter-spacing:.12em}
.cohort .big{font-family:var(--mono);font-size:1.5rem;margin:.25rem 0}
.cohort .yr{font-family:var(--mono);font-size:.6875rem;color:var(--dim)}
.term{font-family:var(--mono);font-size:.75rem;color:var(--dim);
  padding:.2rem 0;border-top:1px solid var(--hair)}
.term b{color:var(--ink);font-weight:600}
.withfix{color:var(--red)}
.issues{list-style:none;margin:.5rem 0 0}
.issue{font-family:var(--mono);font-size:.75rem;color:var(--amber);margin-top:.25rem}
.fix{font-family:var(--mono);font-size:.75rem;color:var(--green);margin-top:.4rem}
.analysis{display:grid;grid-template-columns:1fr 1fr;gap:.5rem 1.5rem;margin-top:.6rem}
.analysis .a h3{margin-bottom:.25rem}
.analysis ul{list-style:none}
.analysis li{font-family:var(--mono);font-size:.75rem;color:var(--dim);padding:.12rem 0}
.note{font-size:.8125rem;line-height:1.55;color:var(--dim);margin:.3rem 0}
.recon{font-family:var(--mono);font-size:.75rem;color:var(--dim);margin-top:.4rem}
.recon b{color:var(--ink);font-weight:600}
.codes{display:flex;flex-wrap:wrap;gap:.4rem;margin-top:.5rem}
.code{font-family:var(--mono);font-size:.6875rem;border:1px solid var(--line);
  border-radius:2px;padding:.2rem .45rem;color:var(--amber)}
.det{border-top:1px solid var(--hair);padding:.6rem 0;margin-top:.25rem}
.det:first-of-type{border-top:none}
.det .name{font-family:var(--mono);font-size:.75rem;color:var(--amber)}
.det .name.on{color:var(--green)}
.det .why,.det .rem,.det .join{font-family:var(--mono);font-size:.75rem;margin-top:.2rem}
.det .why{color:var(--dim)} .det .rem{color:var(--green)} .det .join{color:var(--blue)}
.disclosure{border-left:3px solid var(--blue);padding:.1rem 0 .1rem .9rem}
.disclosure p{font-size:.8125rem;color:var(--dim);line-height:1.55}
.draft{font-size:.8rem;line-height:1.5;color:var(--warn-ink);background:var(--warn-bg);
  border:1px solid var(--hair);border-radius:6px;padding:.6rem .8rem;margin:.3rem 0 .6rem}
.draft b{font-weight:600}
.ge-sub{font-family:var(--mono);font-size:.6875rem;color:var(--dim);margin:.1rem 0 .6rem}
.tablewrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;margin-top:.2rem}
caption{text-align:left;font-family:var(--mono);font-size:.6875rem;color:var(--dim);
  text-transform:uppercase;letter-spacing:.1em;padding-bottom:.5rem}
th{font-family:var(--mono);font-size:.625rem;text-transform:uppercase;letter-spacing:.1em;
  color:var(--dim);text-align:left;font-weight:600;padding:0 1rem .5rem 0;
  border-bottom:1px solid var(--line)}
td{font-size:.8125rem;color:var(--ink);padding:.55rem 1rem .55rem 0;
  border-bottom:1px solid var(--hair);vertical-align:top}
tr:last-child td{border-bottom:none}
td.area{font-family:var(--mono);color:var(--dim);white-space:nowrap}
.plan-shared,.plan-concrete{color:var(--green);font-family:var(--mono);font-size:.75rem}
.plan-reserve{color:var(--dim);font-family:var(--mono);font-size:.75rem}
.flag{display:inline-block;font-family:var(--mono);font-size:.625rem;color:var(--warn-ink);
  background:var(--warn-bg);border:1px solid var(--hair);border-radius:4px;
  padding:.05rem .4rem;margin:0 .25rem .15rem 0;white-space:nowrap}
.ge-major{font-family:var(--mono);font-size:.75rem;color:var(--dim);margin-top:.9rem;line-height:1.7}
.ge-major b{color:var(--green);font-weight:600}
.briefing{white-space:pre-wrap;font-family:var(--mono);font-size:.8125rem;line-height:1.6;
  color:var(--ink);margin-top:.4rem}
footer{max-width:60rem;margin:1.5rem auto 2.5rem;padding:0 1.6rem;
  font-family:var(--mono);font-size:.6875rem;color:var(--dim);line-height:1.6}
@media (max-width:40rem){
  .cohorts{grid-template-columns:1fr}
  .analysis{grid-template-columns:1fr}
}
@media print{
  body{background:#fff}
  .toolbar{display:none}
  .card{break-inside:avoid}
}
"""

# Inline, dependency-free behaviour: reader text-size (--scale, persisted) and the
# light/dark theme toggle (persisted). The page is fully readable with JS off — the
# controls are progressive enhancement; default light theme + base size render
# server-side.
JS = """
(function(){
  var root=document.documentElement;
  function clamp(v){return Math.max(0.8,Math.min(1.6,Math.round(v*100)/100));}
  function readScale(){var v=parseFloat(localStorage.getItem('report-scale'));return isNaN(v)?1:clamp(v);}
  function applyScale(v){v=clamp(v);root.style.setProperty('--scale',v);try{localStorage.setItem('report-scale',v);}catch(e){}}
  function applyTheme(t){if(t!=='dark')t='light';root.setAttribute('data-theme',t);
    try{localStorage.setItem('report-theme',t);}catch(e){}
    var b=document.getElementById('themeBtn');
    if(b){b.setAttribute('aria-pressed', t==='dark'?'true':'false');b.textContent=(t==='light')?'◐ Dark':'◑ Light';}}
  try{applyScale(readScale());}catch(e){}
  var t='light';try{t=localStorage.getItem('report-theme')||'light';}catch(e){}
  applyTheme(t);
  var dec=document.getElementById('decBtn'),inc=document.getElementById('incBtn'),
      rst=document.getElementById('resetBtn'),thm=document.getElementById('themeBtn');
  if(dec)dec.addEventListener('click',function(){applyScale(readScale()-0.1);});
  if(inc)inc.addEventListener('click',function(){applyScale(readScale()+0.1);});
  if(rst)rst.addEventListener('click',function(){applyScale(1);});
  if(thm)thm.addEventListener('click',function(){applyTheme((root.getAttribute('data-theme')||'light')==='light'?'dark':'light');});
})();
"""

_DET_LABELS = {
    "modality_mismatch": "Capacity / fill-rate analysis",
    "prerequisite_ordering": "Prerequisite ordering",
    "time_block_conflict": "Time-block conflicts",
}
_PLAN_LABELS = {"shared": "met by major", "concrete": "concrete", "reserve": "reserve"}
_GE_PATTERN_NAMES = {"igetc": "IGETC", "cal-getc": "Cal-GETC", "csu-ge": "CSU GE"}
_GE_FLAG_LABELS = {"no_assist_data": "no ASSIST data", "no_offering": "not offered this window",
                   "unknown_area": "unknown area", "pm_no_ge": "no program GE"}


def _esc(s) -> str:
    """Escape an untrusted value for HTML text/attribute context."""
    return html.escape("" if s is None else str(s))


# ------------------------------------------------------------------ panel helpers
def _badge(p: dict):
    """Status (css_class, text) for a program — mirrors ui.html badge()."""
    ft, pt = p.get("cohorts", {}).get("full_time"), p.get("cohorts", {}).get("part_time")
    issues = p.get("official_map_issues") or []
    if not ft and not pt:
        return "b-bad", "no plan"
    if issues and ft and ft.get("needs_fix"):
        return "b-bad", "needs fix"
    if issues:
        return "b-warn", "map broken"
    return "b-ok", "on track"


def _cohort(label: str, c) -> str:
    """One cohort block — mirrors ui.html cohortCard()."""
    if not c:
        return (f'<div class="cohort"><div class="lab">{_esc(label)}</div>'
                '<div class="big">—</div></div>')
    yrs = math.ceil(c.get("terms_used", 0) / 2) if c.get("terms_used") else 0
    terms = "".join(
        f'<div class="term"><b>T{_esc(t)}</b> {_esc(", ".join(cs))}</div>'
        for t, cs in sorted(c.get("plan", {}).items(), key=lambda kv: int(kv[0])))
    fixes = c.get("fixes") or []
    fix = ""
    if fixes:
        parts = ", ".join(f'+ {_esc(f.get("course"))} in {_esc(f.get("season"))}' for f in fixes)
        fix = f'<div class="fix">fix: {parts}</div>'
    tag = ' <span class="withfix">(with fix)</span>' if c.get("needs_fix") else ""
    return (f'<div class="cohort"><div class="lab">{_esc(label)}</div>'
            f'<div class="big">{_esc(c.get("terms_used"))} terms{tag}</div>'
            f'<div class="yr">~{yrs} year{"s" if yrs != 1 else ""}</div>{terms}{fix}</div>')


def _programs(results: dict) -> str:
    progs = results.get("programs") or {}
    if not progs:
        return ('<section class="card" aria-labelledby="noprog"><h2 id="noprog">No programs found</h2>'
                '<p class="note">The data had no program rows, so there is nothing to schedule.</p></section>')
    out = []
    for _code, p in progs.items():
        cls, txt = _badge(p)
        issues = p.get("official_map_issues") or []
        issues_html = ""
        if issues:
            lis = "".join(f'<li class="issue"><span aria-hidden="true">! </span>{_esc(i)}</li>' for i in issues)
            issues_html = f'<ul class="issues">{lis}</ul>'
        cohorts = p.get("cohorts", {})
        out.append(
            f'<section class="card" aria-label="Program: {_esc(p.get("title"))}">'
            f'<h2>{_esc(p.get("title"))} <span class="badge {cls}">{_esc(txt)}</span></h2>'
            f'{issues_html}'
            f'<div class="cohorts">{_cohort("Full-time", cohorts.get("full_time"))}'
            f'{_cohort("Part-time", cohorts.get("part_time"))}</div></section>')
    return "".join(out)


def _diagnostics(results: dict) -> str:
    a = results.get("analysis") or {}
    def block(title, items, fmt):
        lis = "".join(f"<li>{fmt(x)}</li>" for x in items) if items else "<li>none</li>"
        return f'<div class="a"><h3>{title}</h3><ul>{lis}</ul></div>'

    def under(x):
        if x.get("waitlisted", 0) > 0:
            tail = f'{_esc(x["waitlisted"])} waitlisted'
        elif x.get("sections_waitlisted") is not None:
            tail = f'{_esc(x["sections_waitlisted"])}/{_esc(x.get("sections_total"))} sections waitlisted'
        else:
            tail = "waitlisted"
        return f'{_esc(x.get("course"))} — {tail}'

    body = (
        block("Rotation gaps", a.get("rotation_gaps", []),
              lambda x: f'{_esc(x.get("course"))} — {_esc(x.get("offered"))}/{_esc(x.get("of"))}')
        + block("Single-section risk", a.get("single_section", []),
                lambda x: _esc(x.get("course")))
        + block("Modality mismatch", a.get("modality_mismatch", []),
                lambda x: f'{_esc(x.get("course"))} — {_esc(x.get("fill_pct"))}% fill')
        + block("Under-supply", a.get("under_supply", []), under)
        + block("Time conflicts", a.get("time_block_collisions", []),
                lambda x: _esc(x.get("summary")))
        + block("Off-grid sections", a.get("off_grid_sections", []),
                lambda x: _esc(x.get("summary"))))
    n = _esc(results.get("terms_in_data"))
    return (f'<section class="card" aria-labelledby="diag"><h2 id="diag">Supply diagnostics '
            f'({n} terms)</h2><div class="analysis">{body}</div></section>')


def _buildability(results: dict) -> str:
    """Program-map buildability audit (F1): per-program structural-feasibility
    score + the blocking reasons. Empty when the audit is absent (non-live
    reports). Honest inert note when nothing could be audited."""
    block = (results.get("analysis") or {}).get("buildability")
    if not block:
        return ""
    label = _esc(block.get("label", ""))
    if block.get("status") != "active":
        return ('<section class="card" aria-labelledby="build"><h2 id="build">Program buildability</h2>'
                f'<p>Not computed: {_esc(block.get("reason", "no program / sections to audit"))}</p>'
                f'<p class="muted">{label}</p></section>')

    def reasons(p):
        out = []
        if p.get("missing"):
            out.append("Not offered: " + ", ".join(_esc(c) for c in p["missing"]))
        if p.get("dead_requirements"):
            out.append("De-catalogued: " + ", ".join(_esc(c) for c in p["dead_requirements"]))
        tc = p.get("time_conflict") or {}
        for pair in tc.get("pairwise_hard", []):
            out.append("Time conflict: " + " &amp; ".join(_esc(c) for c in pair))
        for tcl in tc.get("term_clashes", []):
            out.append(f'Term {_esc(tcl.get("recommended_semester"))} clash: '
                       + ", ".join(_esc(c) for c in tcl.get("courses", [])))
        for ch in p.get("choice_groups", []):
            if ch.get("slack", 0) < 0:
                out.append("Unsatisfiable choice: " + ", ".join(_esc(o) for o in ch.get("options", [])))
        if p.get("single_section_required"):
            out.append("Single-section risk: "
                       + ", ".join(_esc(c) for c in p["single_section_required"]))
        for m in p.get("season_mismatches", []):
            out.append(f'{_esc(m.get("course"))} mapped to {_esc(m.get("recommended_season"))} '
                       f'but offered {", ".join(_esc(s) for s in m.get("offered_seasons", []))}')
        for sp in p.get("seat_pressure", []):
            fp = sp.get("fill_pct")
            out.append(f'{_esc(sp.get("course"))} seat pressure'
                       + (f' ({_esc(fp)}% fill)' if fp is not None else " (closed/waitlisted)"))
        if p.get("by_design_excluded"):
            out.append("By-design (not flagged): "
                       + ", ".join(_esc(c) for c in p["by_design_excluded"]))
        return out

    cards = []
    for p in block.get("programs", []):
        lis = "".join(f"<li>{r}</li>" for r in reasons(p)) or "<li>no blockers found</li>"
        cards.append(
            '<div class="a">'
            f'<h3>{_esc(p.get("title") or p.get("code"))} — score {_esc(p.get("score"))}/100</h3>'
            f'<p>{_esc(p.get("summary"))}</p>'
            f'<ul>{lis}</ul></div>')
    terms = ", ".join(_esc(t) for t in block.get("horizon_terms", []))
    return (f'<section class="card" aria-labelledby="build"><h2 id="build">Program buildability '
            f'(terms {terms})</h2>'
            f'<p class="muted">{label}</p>'
            f'<div class="analysis">{"".join(cards)}</div></section>')


def _reconciliation(results: dict) -> str:
    rec = results.get("reconciliation")
    if not rec:
        return ""
    campus = results.get("campus") or "live"
    terms = ", ".join(_esc(t) for t in (results.get("live_terms") or []))
    prog = ""
    pi = results.get("program_info")
    if pi:
        award = f' ({_esc(pi.get("award"))})' if pi.get("award") else ""
        prog = f' · {_esc(pi.get("title"))}{award}'
    codes = "".join(f'<span class="code">{_esc(c)}</span>' for c in (rec.get("unmatched") or []))
    codes_html = (f'<p class="note">Not offered in the fetched terms:</p>'
                  f'<div class="codes">{codes}</div>') if codes else ""
    disclosure = (
        '<section class="card"><div class="disclosure"><h2>What live data covers</h2>'
        '<p>Live mode reads the public LACCD schedule and program map: the full 2-year '
        'schedule plus rotation, single-section and waitlist signals. Enrollment / capacity '
        'counts are not published by any LACCD API, so capacity / fill-rate analysis needs '
        'those numbers — add an enrollment export on the live form (experimental), or use '
        'a workbook that already includes them.</p></div></section>')
    return (disclosure +
            f'<section class="card" aria-labelledby="recon"><h2 id="recon">Live data reconciliation</h2>'
            f'<div class="recon">{_esc(campus)} · terms {terms}{prog}</div>'
            f'<div class="recon"><b>{_esc(rec.get("matched_count"))}</b> program courses offered in the '
            f'fetched terms · <b>{_esc(rec.get("unmatched_count"))}</b> not offered</div>'
            f'{codes_html}</section>')


def _detectors(results: dict) -> str:
    inert = results.get("inert_detectors") or []
    if not inert:
        return ""
    rows = []
    for d in inert:
        active = d.get("status") == "active"
        label = _DET_LABELS.get(d.get("detector"), d.get("detector"))
        why = d.get("reason") or d.get("label") or ""
        rem = d.get("remedy") or d.get("metric") or ""
        join = ""
        if d.get("matched_sections") is not None and d.get("total_sections") is not None:
            extra = " — counts not applied" if d.get("matched_sections") == 0 else ""
            join += (f'<div class="join">joined {_esc(d.get("matched_sections"))}/'
                     f'{_esc(d.get("total_sections"))} sections{extra}</div>')
        ps = d.get("prereq_summary")
        if active and ps:
            n = (ps.get("with_hard_prereq_count") or 0) + (ps.get("fallback_count") or 0)
            relaxed = (f' ({_esc(ps.get("fallback_count"))} relaxed)'
                       if ps.get("fallback_count") else "")
            join += (f'<div class="join">{_esc(n)} prerequisite{"" if n == 1 else "s"} '
                     f'applied{relaxed}</div>')
        mark = "✓ " if active else "⊘ "
        state = " — on" if active else " — needs more data"
        why_html = f'<div class="why">{_esc(why)}</div>' if why else ""
        rem_html = (f'<div class="rem">{"how: " if active else "to enable: "}{_esc(rem)}</div>'
                    if rem else "")
        rows.append(
            f'<div class="det"><div class="name{" on" if active else ""}">'
            f'<span aria-hidden="true">{mark}</span>{_esc(label)}{state}</div>'
            f'{join}{why_html}{rem_html}</div>')
    return (f'<section class="card" aria-labelledby="meas"><h2 id="meas">What this live build can '
            f'measure</h2><p class="note">These depend on data the public schedule API does not '
            f'include. An empty result here means “not yet measurable,” not “all clear.”</p>'
            f'{"".join(rows)}</section>')


def _ge(results: dict) -> str:
    ge = results.get("ge_coverage")
    if not ge or not ge.get("requested"):
        return ""
    pname = _GE_PATTERN_NAMES.get(ge.get("pattern"), str(ge.get("pattern") or "").upper())
    draft = ""
    if ge.get("draft_warning"):
        msg = str(ge["draft_warning"]).replace("Draft — unverified: ", "")
        draft = (f'<p class="draft"><span aria-hidden="true">⚠️ </span>'
                 f'<b>Draft — unverified.</b> {_esc(msg)}</p>')
    err = f'<p class="note">{_esc(ge.get("error"))}</p>' if ge.get("error") else ""
    caveat = ge.get("assist_caveat") or ""
    caveat_html = f'<p class="note">{_esc(caveat)}</p>' if caveat else ""
    rows = []
    for a in (ge.get("areas") or []):
        r = str(a.get("resolution") or "")
        flags = "".join(f'<span class="flag">{_esc(_GE_FLAG_LABELS.get(f, str(f).replace("_", " ")))}</span>'
                        for f in (a.get("flags") or []))
        rows.append(
            f'<tr><td class="area">{_esc(a.get("area"))}</td>'
            f'<td>{_esc(a.get("title"))}</td>'
            f'<td>{_esc(a.get("required"))}</td>'
            f'<td><span class="plan-{_esc(r)}">{_esc(_PLAN_LABELS.get(r, r))}</span></td>'
            f'<td>{flags}</td></tr>')
    table = (
        '<div class="tablewrap" tabindex="0" role="region" aria-label="General Education coverage table">'
        f'<table><caption>General Education coverage — {_esc(pname)}</caption><thead><tr>'
        '<th scope="col">Area</th><th scope="col">Title</th><th scope="col">Need</th>'
        '<th scope="col">Plan</th><th scope="col">Notes</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>')
    shared = ge.get("shared_with_major") or []
    shared_html = ""
    if shared:
        areas = sorted({s.get("area") for s in shared})
        by_subj = {}
        for c in sorted({s.get("course") for s in shared}):
            i = str(c).rfind(" ")
            subj, num = (c[:i], c[i + 1:]) if i >= 0 else (c, "")
            by_subj.setdefault(subj, []).append(num)
        courses = ", ".join(f'{_esc(s)} {"/".join(_esc(n) for n in sorted(by_subj[s]))}'
                            for s in sorted(by_subj))
        shared_html = (f'<p class="ge-major"><b><span aria-hidden="true">✓ </span>Met by your major</b> '
                       f'— areas {_esc(" / ".join(a for a in areas if a))}: {courses}</p>')
    return (f'<section class="card" aria-labelledby="ge"><h2 id="ge">General Education — {_esc(pname)}</h2>'
            f'{draft}<p class="ge-sub">Live from ASSIST · status: {_esc(ge.get("assist_status"))}</p>'
            f'{caveat_html}{err}{table}{shared_html}</section>')


def _meta_line(results: dict) -> str:
    if results.get("campus"):
        terms = ", ".join(_esc(t) for t in (results.get("live_terms") or []))
        pi = results.get("program_info") or {}
        award = f' ({_esc(pi.get("award"))})' if pi.get("award") else ""
        prog = f' · {_esc(pi.get("title"))}{award}' if pi.get("title") else ""
        return f'Live LACCD data · {_esc(results.get("campus"))} · terms {terms}{prog}'
    return "Workbook analysis"


# ------------------------------------------------------------------------- public
def render_report(results: dict, *, briefing: str = "", generated_at: str = "") -> str:
    """Render ``results`` into a complete, self-contained HTML document string.

    ``results`` is the engine.run output, optionally flattened with the live-data
    panels (reconciliation / inert_detectors / ge_coverage / campus / live_terms /
    program_info) exactly as Api.fetch_live returns it. ``briefing`` is the optional
    plain-language admin summary; ``generated_at`` a display timestamp. No I/O.
    """
    results = results or {}
    pi = results.get("program_info") or {}
    subject = pi.get("title") or ("live data" if results.get("campus") else "analysis")
    title = f"Schedule Planner report — {subject}"

    meta = _meta_line(results)
    gen = f' · generated {_esc(generated_at)}' if generated_at else ""

    toolbar = (
        '<div class="toolbar">'
        '<div class="grp" role="group" aria-label="Text size">'
        '<button type="button" id="decBtn" class="tbtn" aria-label="Decrease text size">A−</button>'
        '<button type="button" id="resetBtn" class="tbtn" aria-label="Reset text size">A</button>'
        '<button type="button" id="incBtn" class="tbtn" aria-label="Increase text size">A+</button>'
        '</div>'
        '<button type="button" id="themeBtn" class="tbtn" aria-label="Switch colour theme" '
        'aria-pressed="false">◐ Dark</button>'
        '</div>')

    briefing_html = ""
    if briefing and briefing.strip():
        briefing_html = (f'<section class="card" aria-labelledby="brief"><h2 id="brief">Admin briefing</h2>'
                         f'<div class="briefing">{_esc(briefing)}</div></section>')

    body = (
        '<a class="skip" href="#main">Skip to report</a>'
        '<header><div class="brand"><h1>SCHEDULE&nbsp;PLANNER</h1>'
        '<span class="tag">2-Year Completion · LAMC</span>'
        f'<div class="meta">{meta}{gen}</div></div>{toolbar}</header>'
        '<main id="main">'
        f'{_programs(results)}'
        f'{_diagnostics(results)}'
        f'{_buildability(results)}'
        f'{_reconciliation(results)}'
        f'{_detectors(results)}'
        f'{_ge(results)}'
        f'{briefing_html}'
        '</main>'
        '<footer>Generated by edgesched · Schedule Planner. Live data is from LACCD’s public '
        'class-schedule API and Program Mapper; the analysis is section- and aggregate-level only '
        '(no student-level records). Prerequisite / GE coverage is a planning aid — confirm with a '
        'counselor.</footer>')

    return ("<!DOCTYPE html>\n"
            '<html lang="en" data-theme="light">\n<head>\n'
            '<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'<title>{_esc(title)}</title>\n'
            f'<style>{CSS}</style>\n</head>\n<body>\n'
            f'{body}\n'
            f'<script>{JS}</script>\n</body>\n</html>\n')
