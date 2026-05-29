"""
OPTIONAL / NON-CORE: Neo4j graph layer (TECH_SPEC §6).

This module is an optional, non-core integration that loads the scheduling
data into a Neo4j graph database for advanced graph queries and dashboards.
It is NOT wired into the main scheduling engine (engine.py), the desktop
app (app.py), or the live data pipeline (build_live_workbook.py). Running
the product does not require or call this module. It requires a separately
installed and running Neo4j instance to do anything useful beyond --dry-run.

Builds the graph the solver and dashboards query against:

  (:Program)-[:REQUIRES_COURSE {recommended_semester}]->(:Course)
  (:Course)-[:HAS_PREREQ {group}]->(:Course)          # prereq edges (OR-groups)
  (:Section)-[:SECTION_OF]->(:Course)
  (:Section)-[:OFFERED_IN]->(:Term)

Usage:
  export NEO4J_URI=bolt://localhost:7687
  export NEO4J_USER=neo4j
  export NEO4J_PASSWORD=yourpassword
  python load_neo4j.py            # load
  python load_neo4j.py --clear    # wipe scheduling nodes first, then load
  python load_neo4j.py --dry-run  # parse + print, no DB connection

No PII: instructor fields are never loaded.
"""
import os
import sys
import pandas as pd

SECTIONS = "path/to/sections.xlsx"
CATALOG  = "path/to/catalog.csv"
PROGRAMS = "path/to/programs.csv"


def parse_prereq(s):
    if pd.isna(s) or not str(s).strip():
        return []
    return [[c.strip() for c in grp.strip().strip("()").split(" OR ")]
            for grp in str(s).split(" AND ")]


def load_frames():
    sec = pd.read_excel(SECTIONS)
    cat = pd.read_csv(CATALOG)
    prog = pd.read_csv(PROGRAMS)
    return sec, cat, prog


def build_payloads(sec, cat, prog):
    """Return plain-Python payloads ready for parameterized Cypher UNWIND."""
    season_of = lambda t: "Fall" if str(t).endswith("8") else "Spring"

    courses = [{
        "id": r["Course ID"], "subject": r["Subject"], "catalog": str(r["Catalog"]),
        "title": r["Title"], "units": float(r["Units"]),
        "igetc": (r["IGETC Area"] if not pd.isna(r["IGETC Area"]) else ""),
        "oer": (str(r["OER"]) == "Y"), "discipline": r["Discipline"],
    } for _, r in cat.iterrows()]

    prereq_edges = []
    for _, r in cat.iterrows():
        for gi, grp in enumerate(parse_prereq(r["Prerequisites (structured)"])):
            for p in grp:
                prereq_edges.append({"course": r["Course ID"], "prereq": p, "group": gi})

    terms = [{"code": str(t),
              "descr": sec[sec["Term"] == t]["Descr"].iloc[0],
              "season": season_of(t),
              "year": int(str(t)[1:3]) + 2000}
             for t in sorted(sec["Term"].unique())]

    sections = []
    for _, r in sec.iterrows():
        sections.append({
            "crn": int(r["Class Nbr"]), "course": r["CLASS"], "term": str(r["Term"]),
            "section": r["Section"], "mode": r["Mode"],
            "in_person": (str(r["IN_PERSON"]) == "Y"),
            "days": ("" if pd.isna(r["DAYS"]) else str(r["DAYS"])),
            "start": ("" if pd.isna(r["Mtg Start"]) else str(r["Mtg Start"])),
            "end": ("" if pd.isna(r["Mtg End"]) else str(r["Mtg End"])),
            "cap": int(r["Cap Enrl"]), "enrl": int(r["Tot Enrl"]),
            "wait": int(r["Wait Tot"]), "status": r["Class Status"],
            "fill": float(r["FILLD"]),
            "pacoima": (str(r["Pacoima"]) == "Y"),
        })

    prog_edges = [{
        "program": r["Program Code"], "title": r["Program Title"],
        "ge": r["GE Pattern"], "course": r["Course ID"],
        "semester": (int(r["Recommended Semester"])
                     if not pd.isna(r["Recommended Semester"]) else None),
    } for _, r in prog.iterrows()]

    return courses, prereq_edges, terms, sections, prog_edges


CONSTRAINTS = [
    "CREATE CONSTRAINT course_id IF NOT EXISTS FOR (c:Course) REQUIRE c.id IS UNIQUE",
    "CREATE CONSTRAINT term_code IF NOT EXISTS FOR (t:Term) REQUIRE t.code IS UNIQUE",
    "CREATE CONSTRAINT prog_code IF NOT EXISTS FOR (p:Program) REQUIRE p.code IS UNIQUE",
    "CREATE CONSTRAINT section_crn IF NOT EXISTS FOR (s:Section) REQUIRE (s.crn, s.term) IS UNIQUE",
]

CYPHER = {
"courses": """
UNWIND $rows AS row
MERGE (c:Course {id: row.id})
SET c.subject=row.subject, c.catalog=row.catalog, c.title=row.title,
    c.units=row.units, c.igetc=row.igetc, c.oer=row.oer, c.discipline=row.discipline
""",
"prereqs": """
UNWIND $rows AS row
MATCH (c:Course {id: row.course})
MERGE (p:Course {id: row.prereq})
MERGE (c)-[r:HAS_PREREQ]->(p) SET r.group=row.group
""",
"terms": """
UNWIND $rows AS row
MERGE (t:Term {code: row.code})
SET t.descr=row.descr, t.season=row.season, t.year=row.year
""",
"sections": """
UNWIND $rows AS row
MATCH (c:Course {id: row.course})
MATCH (t:Term {code: row.term})
MERGE (s:Section {crn: row.crn, term: row.term})
SET s.section=row.section, s.mode=row.mode, s.in_person=row.in_person,
    s.days=row.days, s.start=row.start, s.end=row.end, s.cap=row.cap,
    s.enrl=row.enrl, s.wait=row.wait, s.status=row.status, s.fill=row.fill,
    s.pacoima=row.pacoima
MERGE (s)-[:SECTION_OF]->(c)
MERGE (s)-[:OFFERED_IN]->(t)
""",
"programs": """
UNWIND $rows AS row
MERGE (p:Program {code: row.program})
SET p.title=row.title, p.ge_pattern=row.ge
WITH p, row WHERE row.course IS NOT NULL
MATCH (c:Course {id: row.course})
MERGE (p)-[r:REQUIRES_COURSE]->(c) SET r.recommended_semester=row.semester
""",
}

# Handy queries to run in Neo4j Browser after loading
EXAMPLE_QUERIES = """
// Rotation gaps: required courses not offered every term
MATCH (t:Term) WITH count(DISTINCT t) AS nterms
MATCH (p:Program)-[:REQUIRES_COURSE]->(c:Course)
OPTIONAL MATCH (s:Section {status:'Active'})-[:SECTION_OF]->(c)
WITH c, nterms, count(DISTINCT s.term) AS offered
WHERE offered < nterms
RETURN c.id, offered, nterms ORDER BY offered;

// Modality mismatch: required courses with chronic low fill
MATCH (p:Program)-[:REQUIRES_COURSE]->(c:Course)<-[:SECTION_OF]-(s:Section {status:'Active'})
WITH c, sum(s.enrl)*1.0/sum(s.cap) AS fill
WHERE fill < 0.55 RETURN c.id, round(fill*100) AS fill_pct ORDER BY fill_pct;

// Single-section risk per term
MATCH (p:Program)-[:REQUIRES_COURSE]->(c:Course)<-[:SECTION_OF]-(s:Section {status:'Active'})
WITH c, s.term AS term, count(s) AS secs
WITH c, min(secs) AS fewest WHERE fewest = 1
RETURN c.id, fewest;
"""


def main():
    clear = "--clear" in sys.argv
    dry = "--dry-run" in sys.argv

    sec, cat, prog = load_frames()
    courses, prereqs, terms, sections, prog_edges = build_payloads(sec, cat, prog)

    print(f"Parsed: {len(courses)} courses, {len(prereqs)} prereq edges, "
          f"{len(terms)} terms, {len(sections)} sections, "
          f"{len(prog_edges)} program-course edges")

    if dry:
        print("\n--dry-run: no DB connection. Sample payloads:")
        print(" course :", courses[0])
        print(" prereq :", prereqs[0] if prereqs else "none")
        print(" term   :", terms[0])
        print(" section:", sections[0])
        print(" program:", prog_edges[0])
        print("\nExample queries to run after a real load:")
        print(EXAMPLE_QUERIES)
        return

    from neo4j import GraphDatabase
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    pw = os.environ.get("NEO4J_PASSWORD", "neo4j")

    driver = GraphDatabase.driver(uri, auth=(user, pw))
    with driver.session() as s:
        if clear:
            print("Clearing existing scheduling nodes...")
            s.run("MATCH (n) WHERE n:Course OR n:Section OR n:Term OR n:Program "
                  "DETACH DELETE n")
        for c in CONSTRAINTS:
            s.run(c)
        s.run(CYPHER["courses"],   rows=courses)
        s.run(CYPHER["prereqs"],   rows=prereqs)
        s.run(CYPHER["terms"],     rows=terms)
        s.run(CYPHER["sections"],  rows=sections)
        s.run(CYPHER["programs"],  rows=prog_edges)
        counts = s.run(
            "MATCH (c:Course) WITH count(c) AS courses "
            "MATCH (s:Section) WITH courses, count(s) AS sections "
            "MATCH (p:Program) RETURN courses, sections, count(p) AS programs"
        ).single()
        print(f"Loaded -> {counts['courses']} courses, {counts['sections']} sections, "
              f"{counts['programs']} programs")
    driver.close()
    print("Done. See EXAMPLE_QUERIES in this file for Neo4j Browser queries.")


if __name__ == "__main__":
    main()
