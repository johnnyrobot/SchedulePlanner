# Demo data — 100% synthetic

**Every file in this folder is synthetic.** It is fabricated sample data used
only to demonstrate and test SchedulePlanner. It contains **no real student
records, no personally identifiable information (PII), and no real enrollment
figures** — all names, IDs, course offerings, and counts are invented.

The primary demo workbook (`lamc_data.xlsx`) is generated deterministically by
[`generate_synthetic.py`](../generate_synthetic.py); the other `.csv` / `.xlsx`
files are likewise synthetic fixtures. Although they use LACCD-style labels
(e.g. "LAMC", course subjects), the data does **not** come from any real LACCD
system.

## Using your own data instead

You don't need anything in this folder to use SchedulePlanner for real. Point
the app or the engine at your own workbook or CSVs — see the main
[README](../README.md). To regenerate the synthetic demo workbook:

```bash
python3 generate_synthetic.py
```
