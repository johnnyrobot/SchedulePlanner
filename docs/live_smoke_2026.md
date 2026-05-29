# Live smoke (LACCD network) — transcript

- Captured: 2026-05-29T20:37:00Z
- Host: Darwin 25.2.0 arm64
- Command: `python3 build_live_workbook.py --campus LAMC --program Biology --terms 2264,2266,2268 --out /tmp/live_LAMC.xlsx`
- Result: **PASS — reached LACCD live sources and ran engine.run() on the live workbook.**

## Deterministic offline stand-in

The network-independent equivalent of this smoke is
`tests/test_live_offline_pipeline.py` (green regardless of network):

```bash
python3 -m pytest -q tests/test_live_offline_pipeline.py
```

## Full transcript

```text
Wrote /tmp/live_LAMC.xlsx: 2659 sections across 3 terms; program 'Biology' (7 courses).
Course reconciliation: 7 matched, 0 unmatched (not offered in fetched terms): []
NOTE: Cap/Tot/Wait = 0 -> modality_mismatch and under_supply detectors are INERT (need the IR PeopleSoft enrollment export, PRD M4). Prerequisites are blank (need eLumen) -> solver runs without ordering constraints.
{
  "campus": "LAMC",
  "terms": [
    2264,
    2266,
    2268
  ],
  "section_count": 2659,
  "program": {
    "code": "BIOLOGY",
    "title": "Biology",
    "award": "Associate in Science for Transfer",
    "course_count": 7
  },
  "reconciliation": {
    "matched": [
      "BIOLOGY 006",
      "BIOLOGY 007",
      "CHEM 065",
      "CHEM 101",
      "CHEM 102",
      "PHYSICS 006",
      "PHYSICS 007"
    ],
    "unmatched": [],
    "matched_count": 7,
    "unmatched_count": 0,
    "note": "'unmatched' = program courses not offered in the fetched terms"
  },
  "inert_detectors": [
    {
      "detector": "modality_mismatch",
      "reason": "the LACCD schedule API returns no enrollment/capacity counts (Cap Enrl / Tot Enrl = 0), so fill ratio cannot be computed",
      "remedy": "load the IR PeopleSoft enrollment export (PRD M4)"
    },
    {
      "detector": "under_supply",
      "reason": "the LACCD schedule API returns no waitlist counts (Wait Tot = 0), so waitlist pressure cannot be measured",
      "remedy": "load the IR PeopleSoft enrollment export (PRD M4)"
    },
    {
      "detector": "prerequisite_ordering",
      "reason": "prerequisites are blank (Program Mapper does not expose them); the solver runs without ordering constraints",
      "remedy": "wire eLumen prerequisite data into the catalog sheet"
    }
  ],
  "results": {
    "terms_in_data": 3,
    "analysis": {
      "rotation_gaps": [
        {
          "course": "BIOLOGY 007",
          "offered": 2,
          "of": 3
        },
        {
          "course": "CHEM 102",
          "offered": 2,
          "of": 3
        },
        {
          "course": "PHYSICS 006",
          "offered": 1,
          "of": 3
        },
        {
          "course": "PHYSICS 007",
          "offered": 1,
          "of": 3
        }
      ],
      "single_section": [],
      "modality_mismatch": [],
      "under_supply": []
    },
    "programs": {
      "BIOLOGY": {
        "title": "Biology",
        "official_map_issues": [],
        "cohorts": {
          "full_time": {
            "terms_used": 2,
            "plan": {
              "1": [
                "CHEM 101",
                "CHEM 102",
                "PHYSICS 006"
              ],
              "2": [
                "BIOLOGY 006",
                "BIOLOGY 007",
                "CHEM 065",
                "PHYSICS 007"
              ]
            },
            "fixes": []
          },
          "part_time": {
            "terms_used": 4,
            "plan": {
              "1": [
                "CHEM 065",
                "CHEM 101"
              ],
              "2": [
                "BIOLOGY 007",
                "PHYSICS 007"
              ],
              "3": [
                "CHEM 102",
                "PHYSICS 006"
              ],
              "4": [
                "BIOLOGY 006"
              ]
            },
            "fixes": []
          }
        }
      }
    }
  },
  "error": null,
  "workbook": "/tmp/live_LAMC.xlsx"
}
```
