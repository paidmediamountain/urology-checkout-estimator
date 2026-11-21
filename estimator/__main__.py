"""Run the engine over the sample visits:  python3 -m estimator"""
import json
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from .engine import estimate_visit
from .load import load_price_book, load_visits

# The clinic's Medicare locality. Minnesota is a single statewide locality (00); FIPS 27.
CLINIC = {"state": "MN", "locality_cd": "00", "state_fips": "27"}
# What the hospital pays the software vendor for each call at billing that ends with
# money collected from the patient. Calls that collect nothing are free.
USAGE_FEE_PER_CALL = Decimal("5.00")
EXAMPLE_CLINIC_PATIENTS = (60, 80)                 # a single urology location, per day
DAILY_FEE_GOAL = (Decimal(8000), Decimal(20000))   # a hospital with many specialties, per day
OUT = Path(__file__).resolve().parent.parent / "out" / "estimates.json"


def money(x):
    return "-" if x is None else f"${x:,.2f}"


def main():
    price_for = load_price_book(**CLINIC)
    estimates = [estimate_visit(v, price_for) for v in load_visits()]

    print(f"{'visit':<15}{'decision':<12}{'reason':<20}{'allowed':>10}{'insurer':>10}{'patient':>10}{'collect':>10}")
    for e in estimates:
        print(f"{e.encounter_id:<15}{e.decision:<12}{e.block_reason or '':<20}{money(e.total_allowed):>10}"
              f"{money(e.insurer_expected):>10}{money(e.patient_share):>10}{money(e.collect_now):>10}")
        for l in e.lines:
            code = f"{l.hcpcs_cd}-{l.modifier}" if l.modifier else l.hcpcs_cd
            print(f"    {code:<9}{money(l.allowed_amt):>9}  vs past avg x{l.ratio_to_historic or '-'}  [{l.price_source}]")
        for reason in e.reasons:
            print(f"    - {reason}")

    low, high = (int(goal / USAGE_FEE_PER_CALL) for goal in DAILY_FEE_GOAL)
    billable = sum(e.collect_now > 0 for e in estimates)
    few, many = EXAMPLE_CLINIC_PATIENTS
    print(f"\nusage fee: {billable} of {len(estimates)} calls collected money x {money(USAGE_FEE_PER_CALL)} = "
          f"{money(billable * USAGE_FEE_PER_CALL)}")
    print(f"  one urology location, {few} to {many} patients a day: up to {money(few * USAGE_FEE_PER_CALL)} to "
          f"{money(many * USAGE_FEE_PER_CALL)} a day")
    print(f"  hospital goal of {money(DAILY_FEE_GOAL[0])} to {money(DAILY_FEE_GOAL[1])} a day: "
          f"{low:,} to {high:,} collecting visits a day")

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps([asdict(e) for e in estimates], indent=2, default=str) + "\n")
    print(f"\nwrote {OUT.relative_to(OUT.parent.parent)}")


if __name__ == "__main__":
    main()
