"""Fetch national Medicare billing history for the urology codes in the sample visits.

Source: CMS "Medicare Physician & Other Practitioners - by Geography and Service"
(public, no key needed). Code descriptions are deliberately not saved: they are
copyrighted by the American Medical Association.

Run:  python3 scripts/fetch_medicare_history.py
"""
import csv
import json
import subprocess
import urllib.parse
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "sample_data" / "medicare"
STATE_YEAR = 2024  # state-level rows are fetched for the latest year only

# data year -> CMS dataset id
DATASETS = {
    2022: "87304f15-9ed0-41dc-a141-6141a0327453",
    2023: "ddee9e22-7889-4bef-975a-7853e4cd0fbb",
    2024: "6fea9d79-0129-4e4c-b1b8-23cd86a4f435",
}
CODES = ["99203", "99204", "99213", "99214", "52000", "51798", "76872", "55700", "55250", "81003", "52332", "52356"]


def fetch(dataset_id, code, level):
    query = urllib.parse.urlencode({"filter[HCPCS_Cd]": code, "filter[Rndrng_Prvdr_Geo_Lvl]": level, "size": 5000})
    url = f"https://data.cms.gov/data-api/v1/dataset/{dataset_id}/data?{query}"
    # curl uses the system certificate store; python.org builds on macOS often lack one.
    out = subprocess.run(["curl", "-sS", "--fail", "--max-time", "60", url], check=True, capture_output=True)
    return json.loads(out.stdout)


HEADER = ["data_year", "geo_level", "geo_cd", "hcpcs_cd", "place_of_service", "tot_providers",
          "tot_benes", "tot_services", "avg_submitted", "avg_allowed", "avg_paid"]


def to_row(year, r):
    return [
        year, r["Rndrng_Prvdr_Geo_Lvl"], r["Rndrng_Prvdr_Geo_Cd"], r["HCPCS_Cd"], r["Place_Of_Srvc"],
        r["Tot_Rndrng_Prvdrs"], r["Tot_Benes"], r["Tot_Srvcs"],
        f'{float(r["Avg_Sbmtd_Chrg"]):.2f}', f'{float(r["Avg_Mdcr_Alowd_Amt"]):.2f}',
        f'{float(r["Avg_Mdcr_Pymt_Amt"]):.2f}',
    ]


def write(name, rows):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_DIR / name, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)
    print(f"wrote {len(rows)} rows to {name}")


def main():
    national = [to_row(year, r) for year, ds in DATASETS.items() for code in CODES for r in fetch(ds, code, "National")]
    write("national_service_history.csv", national)
    # geo_cd on state rows is the state FIPS code (27 = Minnesota).
    state = [to_row(STATE_YEAR, r) for code in CODES for r in fetch(DATASETS[STATE_YEAR], code, "State")]
    write(f"state_service_history_{STATE_YEAR}.csv", state)


if __name__ == "__main__":
    main()
