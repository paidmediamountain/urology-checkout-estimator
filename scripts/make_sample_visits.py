"""Generate synthetic Epic-style FHIR R4 bundles, one per closed urology visit.

Every patient here is invented. The resource shapes follow FHIR R4 as Epic
exposes it (Patient, Coverage, Encounter, Condition, Procedure,
DocumentReference); a real Epic site will differ in ids, extensions and how
note text is delivered (Epic returns a Binary reference, inlined here).

Run:  python3 scripts/make_sample_visits.py
"""
import base64
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FHIR_DIR = ROOT / "sample_data" / "epic_fhir"
CHARGE_DIR = ROOT / "sample_data" / "epic_charges"
NPI = "1999999984"  # made-up provider id
SYNTHETIC_TAG = {"system": "urn:synthetic", "code": "SYNTHETIC", "display": "Synthetic test data"}

VISITS = [
    {
        "n": 1, "slug": "medicare_cystoscopy",
        "birth": "1955-03-14", "sex": "male", "zip": "55344", "date": "2026-11-03",
        "coverages": [("Medicare Part B", "MEDICARE_B", None)],
        "dx": [("R31.0", "Gross hematuria")],
        "procedures": [("52000", "Cystoscopy, diagnostic")],
        "charges": [("99214", "25", "R31.0"), ("52000", "", "R31.0")],
        "elig": {"deductible_remaining": 0, "coinsurance_pct": 20, "copay_amt": 0, "oop_remaining": "", "is_qmb": False, "has_secondary": False},
        "note": "71M established patient, two episodes of visible blood in urine over the past month, "
                "no pain, former smoker. Reviewed CT urogram from outside facility: no upper tract lesion. "
                "Discussed need for bladder evaluation; patient consented. Flexible cystoscopy performed in "
                "office today: urethra normal, moderate prostatic enlargement, bladder mucosa normal, no tumor "
                "or stone. Plan: urine cytology, return in 6 months. Moderate complexity decision making.",
    },
    {
        "n": 2, "slug": "medicare_medigap_psa",
        "birth": "1958-07-22", "sex": "male", "zip": "55345", "date": "2026-11-04",
        "coverages": [("Medicare Part B", "MEDICARE_B", None), ("Medicare Supplement Plan G", "MEDIGAP", "PLAN_G")],
        "dx": [("R97.20", "Elevated prostate specific antigen"), ("N40.1", "Enlarged prostate with urinary symptoms")],
        "procedures": [("51798", "Post-void residual bladder scan")],
        "charges": [("99213", "25", "R97.20"), ("51798", "", "N40.1")],
        "elig": {"deductible_remaining": 0, "coinsurance_pct": 20, "copay_amt": 0, "oop_remaining": "", "is_qmb": False, "has_secondary": True},
        "note": "68M follow-up for PSA of 5.8, up from 4.9 a year ago. Weak stream and nighttime urination "
                "twice nightly, stable on tamsulosin. Bladder scan after voiding: 62 mL residual. Exam: "
                "prostate enlarged, smooth, no nodule. Discussed MRI versus repeat PSA in 3 months; patient "
                "prefers repeat PSA. Low complexity decision making.",
    },
    {
        "n": 3, "slug": "qmb_recurrent_uti",
        "birth": "1952-01-09", "sex": "female", "zip": "55343", "date": "2026-11-05",
        "coverages": [("Medicare Part B", "MEDICARE_B", None), ("State Medicaid", "MEDICAID", "QMB")],
        "dx": [("N39.0", "Urinary tract infection, site not specified")],
        "procedures": [("81003", "Urinalysis, automated, without microscopy")],
        "charges": [("99213", "", "N39.0"), ("81003", "", "N39.0")],
        "elig": {"deductible_remaining": 0, "coinsurance_pct": 20, "copay_amt": 0, "oop_remaining": "", "is_qmb": True, "has_secondary": True},
        "note": "74F with third urinary infection this year, burning and frequency for 3 days, no fever or "
                "flank pain. Office urinalysis positive for leukocytes and nitrite. Culture sent. Started "
                "nitrofurantoin for 5 days. Discussed vaginal estrogen for prevention. Low complexity "
                "decision making.",
    },
    {
        "n": 4, "slug": "commercial_vasectomy",
        "birth": "1981-09-30", "sex": "male", "zip": "55347", "date": "2026-11-06",
        "coverages": [("Example Health Plan", "COMMERCIAL", "HDHP")],
        "dx": [("Z30.2", "Encounter for sterilization")],
        "procedures": [("55250", "Vasectomy")],
        "charges": [("55250", "", "Z30.2")],
        "elig": {"deductible_remaining": 1400, "coinsurance_pct": 20, "copay_amt": 0, "oop_remaining": 4000, "is_qmb": False, "has_secondary": False},
        "note": "45M returns for scheduled vasectomy, counseled at prior visit, consent on file. No-scalpel "
                "vasectomy performed under local anesthetic, both sides, segments removed and ends "
                "cauterized. Tolerated well. Semen analysis in 12 weeks; continue contraception until cleared.",
    },
    {
        "n": 5, "slug": "bcbs_cystoscopy",
        "birth": "1968-05-02", "sex": "female", "zip": "55346", "date": "2026-11-09",
        "coverages": [("Blue Cross Blue Shield PPO", "COMMERCIAL", "PPO")],
        "dx": [("R31.29", "Microscopic hematuria")],
        "procedures": [("52000", "Cystoscopy, diagnostic")],
        "charges": [("99214", "25", "R31.29"), ("52000", "", "R31.29")],
        "elig": {"deductible_remaining": 250, "coinsurance_pct": 20, "copay_amt": 40, "oop_remaining": 3000, "is_qmb": False, "has_secondary": False},
        "note": "58F referred for blood found on three routine urine tests, no visible blood, no pain, "
                "never smoked. Kidney ultrasound normal. Discussed evaluation options; patient chose "
                "cystoscopy today. Flexible cystoscopy performed in office: urethra and bladder normal, "
                "no tumor, stone or inflammation. Plan: repeat urine test in 12 months. Moderate "
                "complexity decision making.",
    },
    {
        "n": 6, "slug": "medicare_plan_n_stone_followup",
        "birth": "1957-11-18", "sex": "male", "zip": "55344", "date": "2026-11-10",
        "coverages": [("Medicare Part B", "MEDICARE_B", None), ("Medicare Supplement Plan N", "MEDIGAP", "PLAN_N")],
        "dx": [("N20.0", "Kidney stone")],
        "procedures": [],
        "charges": [("99214", "", "N20.0")],
        "elig": {"deductible_remaining": 0, "coinsurance_pct": 20, "copay_amt": 0, "oop_remaining": "", "is_qmb": False, "has_secondary": True},
        "note": "69M follow-up after passing a 4 mm kidney stone last month. Stone analysis: calcium "
                "oxalate. Reviewed 24-hour urine results showing low urine volume and high sodium. "
                "Counseled on fluid intake and diet; started potassium citrate. Repeat imaging in 6 months. "
                "Moderate complexity decision making.",
    },
]


def entry(resource):
    resource.setdefault("meta", {})["tag"] = [SYNTHETIC_TAG]
    return {"fullUrl": f"urn:synthetic:{resource['resourceType']}/{resource['id']}", "resource": resource}


def build_bundle(v):
    n = v["n"]
    pid, eid = f"eSYN-PT-{n:04d}", f"eSYN-ENC-{n:04d}"
    start, end = f"{v['date']}T09:00:00-06:00", f"{v['date']}T09:40:00-06:00"
    subject, enc_ref = {"reference": f"Patient/{pid}"}, {"reference": f"Encounter/{eid}"}

    entries = [entry({
        "resourceType": "Patient", "id": pid,
        "gender": v["sex"], "birthDate": v["birth"],
        "address": [{"postalCode": v["zip"], "state": "MN"}],
    })]

    for order, (payer, payer_type, plan_class) in enumerate(v["coverages"], start=1):
        cov = {
            "resourceType": "Coverage", "id": f"eSYN-COV-{n:04d}-{order}",
            "status": "active", "order": order, "beneficiary": subject,
            "type": {"coding": [{"system": "urn:synthetic:payer-type", "code": payer_type}], "text": payer},
            "payor": [{"display": payer}],
            "period": {"start": "2026-01-01"},
        }
        if plan_class:
            cov["class"] = [{
                "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/coverage-class", "code": "plan"}]},
                "value": plan_class,
            }]
        entries.append(entry(cov))

    entries.append(entry({
        "resourceType": "Encounter", "id": eid, "status": "finished",
        "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB", "display": "ambulatory"},
        "subject": subject,
        "participant": [{"individual": {"identifier": {"system": "http://hl7.org/fhir/sid/us-npi", "value": NPI},
                                        "display": "Synthetic Urologist, MD"}}],
        "period": {"start": start, "end": end},
        "location": [{"location": {"display": "Example Urology Clinic"}}],
    }))

    for i, (code, text) in enumerate(v["dx"], start=1):
        entries.append(entry({
            "resourceType": "Condition", "id": f"eSYN-DX-{n:04d}-{i}",
            "subject": subject, "encounter": enc_ref,
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-category",
                                      "code": "encounter-diagnosis"}]}],
            "code": {"coding": [{"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": code}], "text": text},
        }))

    for i, (code, text) in enumerate(v["procedures"], start=1):
        entries.append(entry({
            "resourceType": "Procedure", "id": f"eSYN-PROC-{n:04d}-{i}", "status": "completed",
            "subject": subject, "encounter": enc_ref,
            "code": {"coding": [{"system": "http://www.ama-assn.org/go/cpt", "code": code}], "text": text},
            "performedDateTime": start,
        }))

    entries.append(entry({
        "resourceType": "DocumentReference", "id": f"eSYN-NOTE-{n:04d}", "status": "current",
        "type": {"coding": [{"system": "http://loinc.org", "code": "11506-3", "display": "Progress note"}]},
        "subject": subject, "date": end, "context": {"encounter": [enc_ref]},
        "content": [{"attachment": {"contentType": "text/plain",
                                    "data": base64.b64encode(v["note"].encode()).decode()}}],
    }))

    return {"resourceType": "Bundle", "id": f"eSYN-BUNDLE-{n:04d}", "type": "collection", "entry": entries}


def main():
    FHIR_DIR.mkdir(parents=True, exist_ok=True)
    CHARGE_DIR.mkdir(parents=True, exist_ok=True)
    charges, eligibility = [], []
    for v in VISITS:
        n = v["n"]
        eid = f"eSYN-ENC-{n:04d}"
        path = FHIR_DIR / f"visit_{n:02d}_{v['slug']}.json"
        # One bundle per file; Snowflake loads each file as one VARIANT row.
        path.write_text(json.dumps(build_bundle(v), indent=2) + "\n")
        for line_no, (code, modifier, icd) in enumerate(v["charges"], start=1):
            charges.append([eid, line_no, code, modifier, 1, icd, NPI, f"{v['date']} 09:45:00"])
        eligibility.append([eid, f"eSYN-COV-{n:04d}-1", f"{v['date']} 09:42:00", *v["elig"].values()])
        print("wrote", path.relative_to(ROOT))

    with open(CHARGE_DIR / "clinician_charges.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["encounter_id", "line_no", "hcpcs_cd", "modifier", "units", "icd10_cd", "entered_by_npi", "entered_at"])
        w.writerows(charges)
    with open(CHARGE_DIR / "eligibility_snapshots.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["encounter_id", "coverage_id", "checked_at", "deductible_remaining", "coinsurance_pct",
                    "copay_amt", "oop_remaining", "is_qmb", "has_secondary"])
        w.writerows(eligibility)
    print("wrote clinician_charges.csv and eligibility_snapshots.csv")


if __name__ == "__main__":
    main()
