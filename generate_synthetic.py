"""
LAMC synthetic scheduling data generator (MVP / demo).

Produces three artifacts matching the real data spec schema:
  1. catalog.csv    - course master with structured prerequisites
  2. programs.csv   - program requirements + recommended sequence
  3. sections.xlsx  - 8 terms of section offerings (combined, one row per section)

Bottlenecks are deliberately planted so the analysis layer has something to find.
All instructor PII fields are intentionally absent.
"""

import argparse
import os
import random
import pandas as pd
from datetime import date

random.seed(42)  # reproducible demo

# --------------------------------------------------------------------------
# Term scheme.  Confirmed from real data: Fall 2024 = 2248.
# Pattern: "2" + last-two-digits-of-year + term digit (2=Spring, 6=Summer, 8=Fall)
# --------------------------------------------------------------------------
TERMS = [
    ("2228", "2022 Fall",   "Fall",   2022, "08/29/2022", "12/18/2022"),
    ("2232", "2023 Spring", "Spring", 2023, "02/06/2023", "06/05/2023"),
    ("2238", "2023 Fall",   "Fall",   2023, "08/28/2023", "12/17/2023"),
    ("2242", "2024 Spring", "Spring", 2024, "02/05/2024", "06/03/2024"),
    ("2248", "2024 Fall",   "Fall",   2024, "08/26/2024", "12/15/2024"),
    ("2252", "2025 Spring", "Spring", 2025, "02/03/2025", "06/02/2025"),
    ("2258", "2025 Fall",   "Fall",   2025, "08/25/2025", "12/14/2025"),
    ("2262", "2026 Spring", "Spring", 2026, "02/02/2026", "06/01/2026"),
]

# --------------------------------------------------------------------------
# Course catalog.  prereq is structured logic (list of AND-groups, each an OR-list).
#   e.g. [["MATH 245"]]              -> requires MATH 245
#        [["CHEM 101"], ["MATH 245"]] -> requires CHEM 101 AND MATH 245
# --------------------------------------------------------------------------
CATALOG = [
    # id, title, units, prereqs, igetc_area, oer, discipline, acad_org
    ("ENGL 101", "College Reading & Composition I", 3, [], "1A", True,  "English",        "ENGLISH"),
    ("ENGL 102", "College Reading & Composition II", 3, [["ENGL 101"]], "1B", True, "English", "ENGLISH"),
    ("MATH 245", "Calculus I",                  5, [], "2A", False, "Mathematics",     "MATH"),
    ("MATH 246", "Calculus II",                 5, [["MATH 245"]], "2A", False, "Mathematics", "MATH"),
    ("MATH 247", "Linear Algebra",              3, [["MATH 246"]], "2A", False, "Mathematics", "MATH"),
    ("MATH 236", "Statistics",                  4, [], "2A", True,  "Mathematics",     "MATH"),
    ("CS 101",   "Intro to Programming",        3, [], None, True,  "Computer Science","COMPSCI"),
    ("CS 102",   "Data Structures",             3, [["CS 101"]], None, False, "Computer Science", "COMPSCI"),
    ("CS 103",   "Computer Architecture",       3, [["CS 102"]], None, False, "Computer Science", "COMPSCI"),
    ("PHYS 101", "Physics for Scientists I",    4, [["MATH 245"]], "5A", False, "Physics",   "PHYSICS"),
    ("PHYS 102", "Physics for Scientists II",   4, [["PHYS 101"]], "5A", False, "Physics",   "PHYSICS"),
    ("CHEM 101", "General Chemistry I",         5, [], "5A", False, "Chemistry",       "CHEM"),
    ("CHEM 102", "General Chemistry II",        5, [["CHEM 101"]], "5A", False, "Chemistry", "CHEM"),
    ("CHEM 211", "Organic Chemistry I",         5, [["CHEM 102"]], "5A", False, "Chemistry", "CHEM"),
    ("CHEM 212", "Organic Chemistry II",        5, [["CHEM 211"]], "5A", False, "Chemistry", "CHEM"),
    ("BIOL 6",   "Cell & Molecular Biology",    4, [], "5B", False, "Biology",         "BIOLOGY"),
    ("BIOL 7",   "Organismal Biology",          4, [["BIOL 6"]], "5B", False, "Biology",   "BIOLOGY"),
    ("ACCTG 1",  "Financial Accounting",        5, [], None, False, "Accounting",      "ACCTG"),
    ("ACCTG 2",  "Managerial Accounting",       5, [["ACCTG 1"]], None, False, "Accounting", "ACCTG"),
    ("ECON 1",   "Principles of Macroeconomics",3, [], "4", True,  "Economics",        "ECON"),
    ("ECON 2",   "Principles of Microeconomics",3, [], "4", True,  "Economics",        "ECON"),
    ("BUS 1",    "Introduction to Business",    3, [], None, True,  "Business",        "BUS"),
    ("BUS 5",    "Business Law I",              3, [], None, False, "Business",        "BUS"),
    ("COMM 101", "Public Speaking",             3, [], "1C", True,  "Communication",   "COMM"),
    ("HIST 11",  "Political & Social History US",3,[], "3B", True,  "History",         "HISTORY"),
    ("PSYC 1",   "General Psychology",          3, [], "4", True,   "Psychology",      "PSYCH"),
    # 3-deep chain all Fall-only -> deliberately infeasible in 4 terms (demo of minfix)
    ("ENGR 101", "Intro to Engineering",        3, [], None, False, "Engineering",     "ENGR"),
    ("ENGR 102", "Engineering Graphics",        3, [["ENGR 101"]], None, False, "Engineering", "ENGR"),
    ("ENGR 103", "Statics",                     3, [["ENGR 102"]], None, False, "Engineering", "ENGR"),
]

# --------------------------------------------------------------------------
# Programs: required course lists + the official recommended 4-semester map.
# --------------------------------------------------------------------------
PROGRAMS = {
    "AS-T-CSCI": {
        "title": "Computer Science AS-T",
        "ge_pattern": "IGETC",
        "required": ["MATH 245","MATH 246","MATH 247","CS 101","CS 102","CS 103",
                     "PHYS 101","PHYS 102","ENGL 101","ENGL 102","COMM 101","HIST 11"],
        "sequence": {1:["MATH 245","CS 101","ENGL 101","HIST 11"],
                     2:["MATH 246","CS 102","ENGL 102","PHYS 101"],
                     3:["MATH 247","CS 103","COMM 101"],
                     4:["PHYS 102","PSYC 1"]},
    },
    "AS-T-BUS": {
        "title": "Business Administration AS-T",
        "ge_pattern": "CSU GE-Breadth",
        "required": ["ACCTG 1","ACCTG 2","ECON 1","ECON 2","BUS 1","BUS 5",
                     "MATH 236","ENGL 101","COMM 101"],
        "sequence": {1:["ACCTG 1","ECON 1","ENGL 101","BUS 1"],
                     2:["ACCTG 2","ECON 2","MATH 236","COMM 101"],
                     3:["BUS 5","HIST 11"],
                     4:["PSYC 1"]},
    },
    "AS-T-BIOL": {
        "title": "Biology AS-T",
        "ge_pattern": "IGETC",
        "required": ["BIOL 6","BIOL 7","CHEM 101","CHEM 102","CHEM 211","CHEM 212",
                     "MATH 245","PHYS 101","ENGL 101"],
        "sequence": {1:["BIOL 6","CHEM 101","MATH 245","ENGL 101"],
                     2:["BIOL 7","CHEM 102","PHYS 101"],
                     3:["CHEM 211","HIST 11"],
                     4:["CHEM 212","COMM 101"]},
    },
    "AS-T-ENGR": {
        "title": "Engineering AS-T",
        "ge_pattern": "IGETC",
        "required": ["ENGR 101","ENGR 102","ENGR 103","MATH 245","PHYS 101","ENGL 101"],
        "sequence": {1:["ENGR 101","MATH 245","ENGL 101"],
                     2:["ENGR 102","PHYS 101"],
                     3:["ENGR 103"],
                     4:[]},
    },
}

# --------------------------------------------------------------------------
# Offering rules per course: base sections, term restriction, modality mix,
# and a "bottleneck" tag that distorts supply/fill to create discoverable issues.
# --------------------------------------------------------------------------
# offered: "both" | "fall" | "spring"
# bottleneck types:
#   "fall_only_chain"  - restricts BIOL 7 to Fall, breaking the BIOL6->BIOL7 cadence
#   "single_inperson"  - one section, in-person, early morning, fills + waitlists
#   "bad_modality"     - required course offered only in-person at unpopular time, low fill
#   "spring_only"      - CS 103 only Spring, stalls CS sequence
#   "oversubscribed"   - ENGL 101 always waitlists heavily
OFFERING = {
    "ENGL 101": dict(base=8, offered="both", mix="balanced",   bottleneck="oversubscribed"),
    "ENGL 102": dict(base=5, offered="both", mix="balanced"),
    "MATH 245": dict(base=5, offered="both", mix="balanced"),
    "MATH 246": dict(base=2, offered="both", mix="inperson",   bottleneck="bad_modality"),
    "MATH 247": dict(base=2, offered="both", mix="balanced"),
    "MATH 236": dict(base=4, offered="both", mix="online_heavy"),
    "CS 101":   dict(base=4, offered="both", mix="online_heavy"),
    "CS 102":   dict(base=2, offered="both", mix="balanced"),
    "CS 103":   dict(base=1, offered="spring", mix="inperson", bottleneck="spring_only"),
    "PHYS 101": dict(base=2, offered="both", mix="inperson"),
    "PHYS 102": dict(base=1, offered="both", mix="inperson",   bottleneck="single_inperson"),
    "CHEM 101": dict(base=3, offered="both", mix="inperson"),
    "CHEM 102": dict(base=2, offered="both", mix="inperson"),
    "CHEM 211": dict(base=1, offered="both", mix="inperson",   bottleneck="single_inperson"),
    "CHEM 212": dict(base=1, offered="spring", mix="inperson", bottleneck="spring_only"),
    "BIOL 6":   dict(base=3, offered="both", mix="balanced"),
    "BIOL 7":   dict(base=2, offered="fall", mix="balanced",   bottleneck="fall_only_chain"),
    "ACCTG 1":  dict(base=3, offered="both", mix="balanced"),
    "ACCTG 2":  dict(base=2, offered="both", mix="inperson",   bottleneck="bad_modality"),
    "ECON 1":   dict(base=3, offered="both", mix="online_heavy"),
    "ECON 2":   dict(base=2, offered="both", mix="online_heavy"),
    "BUS 1":    dict(base=4, offered="both", mix="online_heavy"),
    "BUS 5":    dict(base=2, offered="both", mix="balanced"),
    "COMM 101": dict(base=4, offered="both", mix="balanced"),
    "HIST 11":  dict(base=5, offered="both", mix="online_heavy"),
    "PSYC 1":   dict(base=5, offered="both", mix="online_heavy"),
    "ENGR 101": dict(base=1, offered="fall", mix="inperson", bottleneck="fall_only_chain"),
    "ENGR 102": dict(base=1, offered="fall", mix="inperson", bottleneck="fall_only_chain"),
    "ENGR 103": dict(base=1, offered="fall", mix="inperson", bottleneck="fall_only_chain"),
}

# modality codes (mirrors PeopleSoft Mode) and target fill rates from real Spring data
MODES = {
    "P":  ("In Person",      0.71, True),
    "H":  ("Hybrid",         0.76, False),
    "OA": ("Online Async",   0.82, False),
    "OS": ("Online Synch",   0.62, False),
    "HY": ("Hyflex",         0.45, True),
}
MIX = {
    "balanced":     ["OA","OA","P","H","OS"],
    "online_heavy": ["OA","OA","OA","H","OS"],
    "inperson":     ["P","P","P","H","HY"],
}

TIME_BLOCKS = [   # (start, end, days, dayblock label)
    ("08:00","09:25","TR","TR-AM"),
    ("09:35","11:00","MW","MW-AM"),
    ("11:10","12:35","MWF","MWF-MID"),
    ("13:00","14:25","TR","TR-PM"),
    ("14:35","16:00","MW","MW-PM"),
    ("18:00","20:50","T","T-EVE"),
    ("18:00","20:50","W","W-EVE"),
    (None,None,"TBA","ASYNC"),
]
DAY_FLAGS = ["M","T","W","R","F","S","N","TBA"]
BUILDINGS = ["INST","CSB","SCI","AHS","CMS"]


def day_cols(days):
    m = {"M":"M","T":"T","W":"W","R":"R","F":"F","S":"S"}
    out = {c:"" for c in DAY_FLAGS}
    if days == "TBA":
        out["TBA"]="Y"; out["N"]="Y"
        return out
    for ch in days:
        if ch in m: out[m[ch]]="Y"
    return out


def pick_block(mode, bottleneck):
    if mode in ("OA",):                       # async = no meeting
        return TIME_BLOCKS[-1]
    if bottleneck in ("single_inperson","bad_modality"):
        return TIME_BLOCKS[0]                 # 8am TR — unpopular slot
    return random.choice(TIME_BLOCKS[:-1])


def fill_for(mode, bottleneck, cap):
    base = MODES[mode][1]
    if bottleneck == "oversubscribed": base = 1.05
    elif bottleneck == "single_inperson": base = 1.08
    elif bottleneck == "bad_modality": base = 0.42
    base += random.uniform(-0.06, 0.06)
    enr = int(round(cap * base))
    enr = max(0, min(enr, int(cap*1.15)))
    wait = max(0, enr - cap)
    enr = min(enr, cap)
    return enr, wait


rows = []
crn = 10000
for term, descr, season, year, sdate, edate in TERMS:
    for cid, title, units, prereqs, igetc, oer, disc, org in CATALOG:
        rule = OFFERING[cid]
        offered = rule["offered"]
        if offered == "fall" and season != "Fall":   continue
        if offered == "spring" and season != "Spring": continue
        bottleneck = rule.get("bottleneck")
        n = rule["base"]
        if season == "Summer": n = max(1, n//2)
        modes = MIX[rule["mix"]]
        subj, cat = cid.split(" ")
        for i in range(n):
            crn += 1
            mode = modes[i % len(modes)]
            mname, _, inperson = MODES[mode]
            # course-wide bottlenecks affect every section; supply bottlenecks only the first
            course_wide = bottleneck if bottleneck in ("bad_modality","oversubscribed") else None
            supply_bn = bottleneck if bottleneck in ("single_inperson",) and i==0 else None
            start,end,days,block = pick_block(mode, course_wide or supply_bn)
            cap = random.choice([30,35,40,45]) if mode!="P" else random.choice([24,28,32])
            enr, wait = fill_for(mode, course_wide or supply_bn, cap)
            cancelled = (random.random() < 0.03 and enr < cap*0.3)
            pacoima = "Y" if random.random() < 0.08 else ""
            dc = day_cols(days)
            rows.append({
                "Term": term, "Descr": descr, "Campus": "LAMC",
                "Class Nbr": crn, "Subject": subj, "Catalog": cat,
                "Section": f"M{i+1:02d}", "Session": "1",
                "Class Type": "E", "Component": "LEC",
                "Assoc": 1, "Comb Sects ID": "",
                "Class Status": "Cancelled" if cancelled else "Active",
                "Cancel Dt": sdate if cancelled else "",
                "Mode": mode, "IN_PERSON": "Y" if inperson else "",
                "Location": "Pacoima" if pacoima else "Main",
                "BUILDING": random.choice(BUILDINGS) if start else "ONLINE",
                "Room Descr": f"{random.choice(BUILDINGS)}-{random.randint(100,399)}" if start else "ONLINE",
                "Facil ID": f"F{random.randint(1000,9999)}" if start else "",
                "Pacoima": pacoima,
                "Mtg Start": start or "", "Mtg End": end or "",
                "Meetings": days, **dc, "TBA Hours": "" if start else units*16,
                "DAYBLOCK": block,
                "HOURS": f"{start}-{end}" if start else "TBA",
                "STARTEND": f"{sdate}-{edate}",
                "Class Start Date": sdate, "Class End Date": edate,
                "Nbr Mtgs": 16 if start else 0,
                "LATE-START": "",
                "Cap Enrl": cap, "Tot Enrl": 0 if cancelled else enr,
                "Wait Cap": 10, "Wait Tot": 0 if cancelled else wait,
                "Combined Cap Enrl":"", "Combined Tot Enrl":"",
                "FILLD": 0 if cancelled else round(enr/cap,3),
                ".FILLPERCNT": 0 if cancelled else round(enr/cap*100,1),
                "ENRL": 0 if cancelled else enr, "LMT": cap,
                "Acad Org": org, "Dep": disc, "Discipline": disc,
                "IGETC": igetc or "", "OER": "Y" if oer else "",
                "FTE": round(enr*units/525,3) if not cancelled else 0,
                "Class Workload Hrs": units,
                "LEVEL": "UG",
                "CLASS": cid, "SEC": f"M{i+1:02d}",
                "DAYS": days, "ROOM": "ONLINE" if not start else f"{random.choice(BUILDINGS)}-{random.randint(100,399)}",
            })

sections = pd.DataFrame(rows)

# catalog frame (prereqs serialized as readable structured string)
def prereq_str(p):
    if not p: return ""
    return " AND ".join("(" + " OR ".join(grp) + ")" for grp in p)

catalog_df = pd.DataFrame([{
    "Course ID": cid, "Subject": cid.split(" ")[0], "Catalog": cid.split(" ")[1],
    "Title": title, "Units": units,
    "Prerequisites (structured)": prereq_str(prereqs),
    "Prerequisites (raw)": prereq_str(prereqs).replace("(","").replace(")",""),
    "IGETC Area": igetc or "", "OER": "Y" if oer else "",
    "Discipline": disc, "Acad Org": org, "Status": "Active",
} for cid,title,units,prereqs,igetc,oer,disc,org in CATALOG])

# programs frame (one row per program-course with sequence term)
prog_rows = []
for pcode, p in PROGRAMS.items():
    seq_lookup = {c:t for t,cs in p["sequence"].items() for c in cs}
    for c in p["required"]:
        prog_rows.append({
            "Program Code": pcode, "Program Title": p["title"],
            "GE Pattern": p["ge_pattern"], "Course ID": c,
            "Requirement Type": "Required",
            "Recommended Semester": seq_lookup.get(c, ""),
        })
programs_df = pd.DataFrame(prog_rows)

# write output
_DEFAULT_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "files", "lamc_data.xlsx")
_parser = argparse.ArgumentParser(description="Generate the LAMC synthetic demo workbook.")
_parser.add_argument("--out", default=_DEFAULT_OUT, help="output .xlsx path (3 sheets)")
_args = _parser.parse_args()
os.makedirs(os.path.dirname(_args.out) or ".", exist_ok=True)
with pd.ExcelWriter(_args.out) as _xl:
    sections.to_excel(_xl, sheet_name="sections", index=False)
    catalog_df.to_excel(_xl, sheet_name="catalog", index=False)
    programs_df.to_excel(_xl, sheet_name="programs", index=False)
print(f"Wrote {_args.out}: {len(sections)} sections, {len(catalog_df)} courses, {len(programs_df)} program-course rows")
print("\nModality fill (sanity check vs real Spring data):")
act = sections[sections['Class Status']=='Active']
print(act.groupby('Mode').apply(lambda d:(d['Tot Enrl'].sum()/d['Cap Enrl'].sum())).round(2).to_string())
