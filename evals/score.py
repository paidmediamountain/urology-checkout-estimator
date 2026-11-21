"""Score predicted billing codes against the gold set, in codes and in dollars."""
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from estimator.engine import ChargeLine, Visit, estimate_visit

CASES_FILE = Path(__file__).resolve().parent / "cases.jsonl"
CENT = Decimal("0.01")


def load_cases(path=CASES_FILE):
    cases = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    for c in cases:
        c["gold"] = [{"hcpcs_cd": code, "modifier": modifier} for code, modifier in c["gold"]]
    return cases


def patient_share(lines, price_for):
    """What a Medicare-only patient with the deductible met would owe, or None if unpriceable."""
    if not lines:
        return Decimal("0.00")
    visit = Visit(
        encounter_id="eval", payer_type="MEDICARE_B", secondary_type=None, is_qmb=False,
        deductible_remaining=Decimal(0), coinsurance_pct=Decimal(20),
        charges=[ChargeLine(i, l["hcpcs_cd"], l.get("modifier", "")) for i, l in enumerate(lines, start=1)],
        procedures_documented=[],
    )
    estimate = estimate_visit(visit, price_for)
    # Set whenever the lines could be priced, even if the engine would hold collection.
    return estimate.patient_share


def score(cases, predictions, price_for, allowed):
    """`predictions` maps case id -> list of {"hcpcs_cd", "modifier"}. Returns a metrics dict."""
    tp = fp = fn = exact = modifier_checked = modifier_right = outside = 0
    errors, unpriced, missing, wrong = [], [], [], []
    by_tag = defaultdict(lambda: [0, 0])

    for case in cases:
        if case["id"] not in predictions:
            missing.append(case["id"])
        predicted = predictions.get(case["id"], [])
        gold = {l["hcpcs_cd"]: l["modifier"] for l in case["gold"]}
        pred = {l["hcpcs_cd"]: l.get("modifier") or "" for l in predicted}

        tp += len(gold.keys() & pred.keys())
        fp += len(pred.keys() - gold.keys())
        fn += len(gold.keys() - pred.keys())
        outside += sum(code not in allowed for code in pred)
        for code in gold.keys() & pred.keys():
            modifier_checked += 1
            modifier_right += gold[code] == pred[code]

        is_exact = gold == pred
        exact += is_exact
        for tag in case["tags"]:
            by_tag[tag][0] += is_exact
            by_tag[tag][1] += 1
        if not is_exact:
            wrong.append({"id": case["id"], "gold": sorted(gold.items()), "predicted": sorted(pred.items())})

        gold_share = patient_share(case["gold"], price_for)
        pred_share = patient_share(predicted, price_for)
        if pred_share is None:
            unpriced.append(case["id"])
        else:
            errors.append(pred_share - gold_share)

    n = len(cases)
    ratio = lambda a, b: round(a / b, 3) if b else None
    precision, recall = ratio(tp, tp + fp), ratio(tp, tp + fn)
    money = lambda x: str(Decimal(x).quantize(CENT))
    return {
        "cases": n,
        "visits_exactly_right": ratio(exact, n),
        "code_precision": precision,
        "code_recall": recall,
        "code_f1": ratio(2 * tp, 2 * tp + fp + fn),
        "modifier_accuracy": ratio(modifier_right, modifier_checked),
        "codes_outside_allowed_list": outside,
        "visits_priced": len(errors),
        "mean_abs_patient_dollar_error": money(sum(abs(e) for e in errors) / len(errors)) if errors else None,
        "mean_signed_patient_dollar_error": money(sum(errors) / len(errors)) if errors else None,
        "max_abs_patient_dollar_error": money(max(abs(e) for e in errors)) if errors else None,
        "over_collect_rate": ratio(sum(e > 0 for e in errors), len(errors)),
        "under_collect_rate": ratio(sum(e < 0 for e in errors), len(errors)),
        "unpriceable_visits": unpriced,
        "missing_predictions": missing,
        "exact_by_tag": {tag: f"{right}/{total}" for tag, (right, total) in sorted(by_tag.items())},
        "wrong": wrong,
    }


def report(name, m):
    pct = lambda x: "n/a" if x is None else f"{x:.1%}"
    rows = [
        ("Visits coded exactly right", pct(m["visits_exactly_right"])),
        ("Code precision / recall", f'{pct(m["code_precision"])} / {pct(m["code_recall"])}'),
        ("Modifier accuracy", pct(m["modifier_accuracy"])),
        ("Codes outside the allowed list", str(m["codes_outside_allowed_list"])),
        ("Mean patient-dollar error", f'${m["mean_abs_patient_dollar_error"]}'),
        ("Largest patient-dollar error", f'${m["max_abs_patient_dollar_error"]}'),
        ("Visits that would over-collect", pct(m["over_collect_rate"])),
        ("Visits that would under-collect", pct(m["under_collect_rate"])),
        ("Visits it could not price", str(len(m["unpriceable_visits"]))),
    ]
    out = [f"### {name} ({m['cases']} cases)", "", "| Measure | Result |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in rows]
    out += ["", "| Case type | Exactly right |", "|---|---|"]
    out += [f"| {tag} | {v} |" for tag, v in m["exact_by_tag"].items()]
    if m["wrong"]:
        out += ["", "Misses:"]
        fmt = lambda pairs: ", ".join(f"{c}-{mod}" if mod else c for c, mod in pairs) or "nothing"
        out += [f"- {w['id']}: expected {fmt(w['gold'])}; got {fmt(w['predicted'])}" for w in m["wrong"]]
    return "\n".join(out) + "\n"
