"""Read the sample files into engine inputs. Swap this module for Snowflake queries later."""
import csv
import json
from decimal import Decimal
from pathlib import Path

from .engine import ChargeLine, Price, Visit, is_medicare_lab, pfs_allowed

ROOT = Path(__file__).resolve().parent.parent / "sample_data"
FEE_YEAR = "2026"
HISTORY_YEAR = "2024"
OFFICE = "O"
MIN_STATE_SERVICES = 1000   # below this a state average is too noisy; use the national one


def _rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _history(state_fips, root):
    """Past Medicare average allowed per code in the office setting: state first, then national."""
    avg = {
        r["hcpcs_cd"]: Decimal(r["avg_allowed"])
        for r in _rows(root / "medicare" / "national_service_history.csv")
        if r["data_year"] == HISTORY_YEAR and r["place_of_service"] == OFFICE
    }
    for r in _rows(root / "medicare" / f"state_service_history_{HISTORY_YEAR}.csv"):
        if (r["place_of_service"] == OFFICE and r["geo_cd"] == state_fips
                and Decimal(r["tot_services"]) >= MIN_STATE_SERVICES):
            avg[r["hcpcs_cd"]] = Decimal(r["avg_allowed"])
    return avg


def load_price_book(state, locality_cd, state_fips, root=ROOT):
    """Return price_for(hcpcs_cd, modifier) for an office in the given Medicare locality."""
    gpci = next(
        r for r in _rows(root / "fee_schedule" / f"pfs_gpci_{FEE_YEAR}.csv")
        if r["state"] == state and r["locality_cd"] == locality_cd
    )
    rvu = {(r["hcpcs_cd"], r["modifier"]): r for r in _rows(root / "fee_schedule" / f"pfs_rvu_{FEE_YEAR}.csv")}
    history = _history(state_fips, root)

    def price_for(code, modifier=""):
        # Most modifiers (such as 25) have no row of their own and price as the bare code.
        row = rvu.get((code, modifier)) or rvu.get((code, ""))
        if row and row["status_cd"] == "A":
            allowed = pfs_allowed(
                Decimal(row["work_rvu"]), Decimal(row["pe_rvu_nonfacility"]), Decimal(row["mp_rvu"]),
                Decimal(gpci["work_gpci"]), Decimal(gpci["pe_gpci"]), Decimal(gpci["mp_gpci"]),
                Decimal(row["conversion_factor"]),
            )
            return Price(allowed, f"CMS PFS {FEE_YEAR} {state}-{locality_cd} office", row["mult_proc_ind"], history.get(code))
        if is_medicare_lab(code) and code in history:
            # Lab tests are paid from a separate schedule that is not loaded; the patient
            # owes nothing on them, so the past average only informs the insurer total.
            return Price(history[code], f"CMS {HISTORY_YEAR} historic office avg")
        return None

    return price_for


def load_visits(root=ROOT):
    charges = {}
    for r in _rows(root / "epic_charges" / "clinician_charges.csv"):
        charges.setdefault(r["encounter_id"], []).append(
            ChargeLine(int(r["line_no"]), r["hcpcs_cd"], r["modifier"], int(r["units"])))
    eligibility = {r["encounter_id"]: r for r in _rows(root / "epic_charges" / "eligibility_snapshots.csv")}
    contracts = {r["payer_name"]: Decimal(r["pct_of_medicare"]) for r in _rows(root / "contracts" / "payer_contracts.csv")}

    visits = []
    for path in sorted((root / "epic_fhir").glob("*.json")):
        resources = [e["resource"] for e in json.loads(path.read_text())["entry"]]
        of_type = lambda t: [r for r in resources if r["resourceType"] == t]
        encounter_id = of_type("Encounter")[0]["id"]
        coverage = {c["order"]: c["type"]["coding"][0]["code"] for c in of_type("Coverage")}
        plans = {c["order"]: c["class"][0]["value"] for c in of_type("Coverage") if c.get("class")}
        primary_payer = next(c["payor"][0]["display"] for c in of_type("Coverage") if c["order"] == 1)
        elig = eligibility[encounter_id]
        visits.append(Visit(
            encounter_id=encounter_id,
            payer_type=coverage[1],
            secondary_type=coverage.get(2),
            is_qmb=elig["is_qmb"] == "True",
            deductible_remaining=Decimal(elig["deductible_remaining"]),
            coinsurance_pct=Decimal(elig["coinsurance_pct"]),
            charges=charges.get(encounter_id, []),
            procedures_documented=[p["code"]["coding"][0]["code"] for p in of_type("Procedure")],
            copay_amt=Decimal(elig["copay_amt"]),
            oop_remaining=Decimal(elig["oop_remaining"]) if elig["oop_remaining"] else None,
            contract_pct=contracts.get(primary_payer),
            secondary_plan=plans.get(2, "").removeprefix("PLAN_") or None,
        ))
    return visits
