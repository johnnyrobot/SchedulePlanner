"""
MVP bottleneck analysis — runs on the synthetic data.
Demonstrates the core analysis the real tool will perform:
  - rotation gaps (course not offered every term)
  - single-section risk on required courses
  - modality mismatch (required course stuck in low-fill modality)
  - chronic waitlisting (under-supplied)
  - 2-year sequence feasibility per program
"""
import pandas as pd

sec = pd.read_excel("path/to/sections.xlsx")
cat = pd.read_csv("path/to/catalog.csv")
prog = pd.read_csv("path/to/programs.csv")

active = sec[sec["Class Status"] == "Active"].copy()
terms = sorted(sec["Term"].unique())
n_terms = len(terms)
required = set(prog["Course ID"])

print("="*68)
print("LAMC SCHEDULING BOTTLENECK REPORT  (synthetic demo data)")
print(f"{n_terms} terms analyzed | {len(required)} courses required across {prog['Program Code'].nunique()} programs")
print("="*68)

# ---- 1. Rotation gaps: required course missing in some terms -------------
print("\n[1] ROTATION GAPS — required courses not offered every term")
for cid in sorted(required):
    offered_terms = active[active["CLASS"] == cid]["Term"].nunique()
    if offered_terms < n_terms:
        missing = n_terms - offered_terms
        progs = ", ".join(prog[prog["Course ID"]==cid]["Program Code"].unique())
        print(f"   {cid:10s} offered {offered_terms}/{n_terms} terms "
              f"(missing {missing})  -> blocks {progs}")

# ---- 2. Single-section risk ---------------------------------------------
print("\n[2] SINGLE-SECTION RISK — required course w/ only 1 section in a term")
flagged = set()
for cid in sorted(required):
    per_term = active[active["CLASS"]==cid].groupby("Term").size()
    if (per_term == 1).any() and cid not in flagged:
        worst = per_term.min()
        print(f"   {cid:10s} as few as {worst} section(s) in a term — no redundancy")
        flagged.add(cid)

# ---- 3. Modality mismatch: required course mostly low-fill modality ------
print("\n[3] MODALITY MISMATCH — required courses with chronic low fill")
fill = (active.groupby("CLASS")
        .apply(lambda d: d["Tot Enrl"].sum()/max(1,d["Cap Enrl"].sum()))
        .rename("fill"))
for cid in sorted(required):
    if cid in fill.index and fill[cid] < 0.55:
        modes = active[active["CLASS"]==cid]["Mode"].value_counts().to_dict()
        print(f"   {cid:10s} fill {fill[cid]*100:4.0f}%  modes={modes}  "
              f"-> demand exists but offered in modality/time students avoid")

# ---- 4. Chronic waitlisting: under-supplied -----------------------------
print("\n[4] UNDER-SUPPLY — required courses that waitlist repeatedly")
wl = active.groupby("CLASS")["Wait Tot"].sum()
for cid in sorted(required):
    if cid in wl.index and wl[cid] > 15:
        print(f"   {cid:10s} {int(wl[cid])} students waitlisted across terms "
              f"-> add capacity")

# ---- 5. 2-year sequence feasibility per program -------------------------
print("\n[5] 2-YEAR FEASIBILITY — can the recommended map actually be followed?")
season = {t: ("Fall" if str(t).endswith("8") else "Spring") for t in terms}
for pcode, g in prog.groupby("Program Code"):
    title = g["Program Title"].iloc[0]
    issues = []
    for _, r in g.iterrows():
        cid, semester = r["Course ID"], r["Recommended Semester"]
        if pd.isna(semester): continue
        want_season = "Fall" if semester in (1,3) else "Spring"
        avail = active[active["CLASS"]==cid]
        seasons_offered = {season[t] for t in avail["Term"].unique()}
        if want_season not in seasons_offered:
            issues.append(f"{cid} needed in {want_season} (sem {int(semester)}) but only {seasons_offered or 'never'}")
    status = "FEASIBLE" if not issues else f"AT RISK ({len(issues)} conflict(s))"
    print(f"\n   {pcode} — {title}: {status}")
    for i in issues:
        print(f"       - {i}")

print("\n" + "="*68)
print("Swap the three input files for real PeopleSoft/eLumen/ProgramMapper")
print("exports and this same analysis runs unchanged.")
print("="*68)
