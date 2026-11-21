"""Download Medicare's physician fee schedule and extract the tables the engine needs.

Source: CMS PFS Relative Value Files, release RVU26D (October 2026).
The raw file carries procedure descriptions copyrighted by the American Medical
Association, so it stays in data/ (git-ignored). The extract written to
sample_data/fee_schedule/ keeps relative values for the urology codes only and
drops the descriptions.

Run:  python3 -I scripts/build_fee_schedule.py
"""
import csv
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "cms_rvu26d"
OUT_DIR = ROOT / "sample_data" / "fee_schedule"
ZIP_URL = "https://www.cms.gov/files/zip/rvu26d-updated-08-26-2026.zip"
FEE_YEAR = 2026
# nonQPP = the rate for clinicians who are not in an advanced alternative payment model.
RVU_FILE, GPCI_FILE = "PPRRVU2026_Oct_nonQPP.csv", "GPCI2026.csv"
# 55707 replaced the old prostate biopsy code 55700, which was retired for 2026.
CODES = {"99203", "99204", "99213", "99214", "52000", "51798", "76872", "55707", "55250", "81003", "52332", "52356"}


def fetch_raw():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    archive = RAW_DIR / "rvu26d.zip"
    if not archive.exists():
        subprocess.run(["curl", "-sS", "--fail", "-L", "-A", "Mozilla/5.0", "-o", str(archive), ZIP_URL], check=True)
    with zipfile.ZipFile(archive) as z:
        for name in (RVU_FILE, GPCI_FILE):
            (RAW_DIR / name).write_bytes(z.read(name))


def read(name):
    with open(RAW_DIR / name, newline="", encoding="latin-1") as f:
        return list(csv.reader(f))


def write(name, header, rows):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_DIR / name, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print(f"wrote {len(rows)} rows to sample_data/fee_schedule/{name}")


def main():
    fetch_raw()

    rows = read(RVU_FILE)
    start = next(i for i, r in enumerate(rows) if r and r[0] == "HCPCS") + 1
    # Columns: 0 code, 1 modifier, 3 status, 5 work, 6 office PE, 8 facility PE, 10 malpractice,
    # 18 multiple-procedure indicator, 25 conversion factor.
    rvu = [[FEE_YEAR, r[0], r[1], r[3], r[5], r[6], r[8], r[10], r[18], r[25]]
           for r in rows[start:] if r and r[0] in CODES]
    write(f"pfs_rvu_{FEE_YEAR}.csv",
          ["fee_year", "hcpcs_cd", "modifier", "status_cd", "work_rvu", "pe_rvu_nonfacility",
           "pe_rvu_facility", "mp_rvu", "mult_proc_ind", "conversion_factor"], rvu)

    gpci = [[FEE_YEAR, r[0], r[1], r[2], r[3].rstrip("* ").strip(), r[4], r[5], r[6]]
            for r in read(GPCI_FILE) if r and r[0].strip().isdigit() and len(r) >= 7]
    write(f"pfs_gpci_{FEE_YEAR}.csv",
          ["fee_year", "mac", "state", "locality_cd", "locality_name", "work_gpci", "pe_gpci", "mp_gpci"], gpci)


if __name__ == "__main__":
    main()
