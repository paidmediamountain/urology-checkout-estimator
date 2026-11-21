"""Pricing and patient-share rules. Pure functions, no file or network access."""
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

ENGINE_VERSION = "0.4.0"

# Medicare supplement plans by letter:
# (percent of the Part B coinsurance paid, pays the Part B deductible, office-visit copay left to the patient).
# States with their own plan designs (Minnesota, Massachusetts, Wisconsin) are not listed;
# an unlisted plan is billed later instead of guessed.
MEDIGAP_RULES = {
    **{plan: (Decimal(100), False, Decimal(0)) for plan in "ABDGM"},
    **{plan: (Decimal(100), True, Decimal(0)) for plan in "CF"},
    "N": (Decimal(100), False, Decimal(20)),
    "K": (Decimal(50), False, Decimal(0)),
    "L": (Decimal(75), False, Decimal(0)),
}

STOP_TEXT = {
    "QMB": "The patient has Medicaid cost-sharing protection (QMB), so federal rules bar charging them anything.",
    "CODING_DISAGREEMENT": "A procedure documented in the chart is missing from the charges; a coder must review before any collection.",
    "NO_CONTRACT_RATE": "There is no contract rate on file for this insurer, so the price is unknown until the claim is processed.",
    "NO_PRICE": "At least one billed code has no price on file.",
    "UNSUPPORTED_PRICING_RULE": "Several scope procedures in one visit are paid by a rule this tool does not calculate.",
    "SUPPLEMENT_PLAN_UNKNOWN": "The supplement plan's benefits are not on file, so what it leaves to the patient is unknown.",
    "SECONDARY_PAYER_UNKNOWN": "A second insurer will pay part of the patient's share; the amount is unknown until it responds.",
    "BENCHMARK_OUTLIER": "A calculated price is far from what Medicare has historically paid for this code; review before collecting.",
}
# Share of the patient's estimated amount to take at checkout. Medicare amounts are
# exact, so they are taken in full; commercial estimates are taken at half.
UPFRONT_PCT = {"MEDICARE_B": Decimal(100), "COMMERCIAL": Decimal(50)}
BENCHMARK_RATIO_LIMITS = (Decimal("0.6"), Decimal("1.6"))
# Surgical codes are often paid at half as a second procedure, so their past average sits
# anywhere between half and full price. The full price may therefore be up to twice as far above it.
SURGICAL_UPPER_FACTOR = 2
CENT = Decimal("0.01")
HALF = Decimal("0.5")


def cents(x):
    return Decimal(x).quantize(CENT, rounding=ROUND_HALF_UP)


def pfs_allowed(work_rvu, pe_rvu, mp_rvu, work_gpci, pe_gpci, mp_gpci, conversion_factor):
    """Medicare physician fee schedule amount: each relative value times its
    locality adjustment, summed, times the dollar conversion factor."""
    return cents((work_rvu * work_gpci + pe_rvu * pe_gpci + mp_rvu * mp_gpci) * conversion_factor)


@dataclass
class Price:
    allowed: Decimal
    source: str                         # which reference row produced the price
    mult_proc_ind: str = "0"            # CMS multiple-procedure indicator
    historic_avg: Decimal | None = None  # past Medicare average for the same code, if known


@dataclass
class ChargeLine:
    line_no: int
    hcpcs_cd: str
    modifier: str = ""
    units: int = 1


@dataclass
class Visit:
    encounter_id: str
    payer_type: str                 # primary coverage: MEDICARE_B, COMMERCIAL, ...
    secondary_type: str | None      # MEDIGAP, MEDICAID, or None
    is_qmb: bool
    deductible_remaining: Decimal
    coinsurance_pct: Decimal
    charges: list[ChargeLine]
    procedures_documented: list[str]  # codes on the Epic-style Procedure records
    copay_amt: Decimal = Decimal("0")
    oop_remaining: Decimal | None = None   # left before the plan's out-of-pocket maximum
    contract_pct: Decimal | None = None    # commercial rate as a percent of Medicare
    secondary_plan: str | None = None      # supplement plan letter, e.g. "G"


@dataclass
class EstimateLine:
    line_no: int
    hcpcs_cd: str
    modifier: str
    units: int
    allowed_amt: Decimal
    mult_proc_discount: bool
    deductible_applied: Decimal
    coinsurance_amt: Decimal
    patient_amt: Decimal
    price_source: str
    ratio_to_historic: Decimal | None


@dataclass
class Estimate:
    encounter_id: str
    decision: str                   # COLLECT, DEPOSIT, BILL_LATER, BLOCKED
    block_reason: str | None = None
    total_allowed: Decimal | None = None
    insurer_expected: Decimal | None = None
    patient_share: Decimal | None = None
    collect_now: Decimal = Decimal("0.00")
    benchmark_flag: bool = False
    engine_version: str = ENGINE_VERSION
    reasons: list[str] = field(default_factory=list)   # plain-language explanation, in order
    lines: list[EstimateLine] = field(default_factory=list)


def is_medicare_lab(hcpcs_cd):
    """Clinical lab tests carry no deductible or coinsurance under Medicare Part B.

    Approximated by code range until the lab fee schedule is loaded.
    """
    return hcpcs_cd.isdigit() and 80047 <= int(hcpcs_cd) <= 87999


def is_office_visit(hcpcs_cd):
    return hcpcs_cd.isdigit() and 99202 <= int(hcpcs_cd) <= 99215


def split_cost(lines, deductible_remaining, coinsurance_pct, copay_amt=0, medicare=True):
    """Apply the deductible line by line, then coinsurance on what is left.

    `lines` is a list of (hcpcs_cd, allowed). Returns per-line
    (deductible_applied, coinsurance, patient_amt) tuples. On a commercial plan
    with a copay, the office visit costs the flat copay and skips the deductible.
    """
    remaining = Decimal(deductible_remaining)
    rate = Decimal(coinsurance_pct) / 100
    copay = Decimal(copay_amt)
    zero = Decimal("0.00")
    out = []
    for code, allowed in lines:
        if medicare and is_medicare_lab(code):
            out.append((zero, zero, zero))
            continue
        if not medicare and copay and is_office_visit(code):
            out.append((zero, zero, cents(min(copay, allowed))))
            continue
        deductible = min(remaining, allowed)
        remaining -= deductible
        coinsurance = cents((allowed - deductible) * rate)
        out.append((cents(deductible), coinsurance, cents(deductible + coinsurance)))
    return out


def discounted_lines(charges, prices):
    """Line numbers paid at 50% under the multiple-procedure rule.

    When several surgical procedures (indicator 2 or 3) are done in one visit,
    the highest-priced is paid in full and the rest at half.
    """
    surgical = [c for c in charges if prices[c.line_no].mult_proc_ind in ("2", "3")]
    ranked = sorted(surgical, key=lambda c: prices[c.line_no].allowed, reverse=True)
    return {c.line_no for c in ranked[1:]}


def estimate_visit(visit, price_for):
    """Decide what to collect at checkout.

    `price_for(hcpcs_cd, modifier)` returns a Price, or None when there is none.
    """
    def stop(decision, reason):
        return Estimate(visit.encounter_id, decision, reason, reasons=[STOP_TEXT[reason]])

    # Hard stops come first: nothing is priced for a patient who may not be charged.
    if visit.is_qmb:
        return stop("BLOCKED", "QMB")
    charged = {c.hcpcs_cd for c in visit.charges}
    if not set(visit.procedures_documented) <= charged:
        return stop("BLOCKED", "CODING_DISAGREEMENT")
    medicare = visit.payer_type == "MEDICARE_B"
    if medicare:
        rate = Decimal(1)
    elif visit.payer_type == "COMMERCIAL" and visit.contract_pct is not None:
        rate = visit.contract_pct / 100
    else:
        return stop("BILL_LATER", "NO_CONTRACT_RATE")
    medigap = None
    if visit.secondary_type == "MEDIGAP":
        medigap = MEDIGAP_RULES.get(visit.secondary_plan)
        if medigap is None:
            return stop("BILL_LATER", "SUPPLEMENT_PLAN_UNKNOWN")

    prices = {c.line_no: price_for(c.hcpcs_cd, c.modifier) for c in visit.charges}
    if any(p is None for p in prices.values()):
        return stop("BILL_LATER", "NO_PRICE")
    # Several endoscopies from one family are paid by a different rule that is not built.
    if sum(p.mult_proc_ind == "3" for p in prices.values()) > 1:
        return stop("BILL_LATER", "UNSUPPORTED_PRICING_RULE")

    halved = discounted_lines(visit.charges, prices)
    priced = [
        (c.hcpcs_cd, cents(prices[c.line_no].allowed * rate * c.units * (HALF if c.line_no in halved else 1)))
        for c in visit.charges
    ]
    split = split_cost(priced, visit.deductible_remaining, visit.coinsurance_pct, visit.copay_amt, medicare)

    lines = []
    oop_left = visit.oop_remaining
    capped = False
    for charge, (_, allowed), (deductible, coinsurance, patient) in zip(visit.charges, priced, split):
        p = prices[charge.line_no]
        if medigap:
            paid_pct, pays_deductible, visit_copay = medigap
            left = cents(coinsurance * (100 - paid_pct) / 100)
            if visit_copay and is_office_visit(charge.hcpcs_cd):
                left = min(visit_copay, coinsurance)
            patient = (Decimal("0.00") if pays_deductible else deductible) + left
        if oop_left is not None:
            capped = capped or patient > oop_left
            patient = min(patient, oop_left)
            oop_left -= patient
        ratio = (p.allowed / p.historic_avg).quantize(Decimal("0.001")) if p.historic_avg else None
        lines.append(EstimateLine(
            charge.line_no, charge.hcpcs_cd, charge.modifier, charge.units, allowed,
            charge.line_no in halved, deductible, coinsurance, patient, p.source, ratio,
        ))

    low, high = BENCHMARK_RATIO_LIMITS

    def off_benchmark(line):
        surgical = prices[line.line_no].mult_proc_ind in ("2", "3")
        ceiling = high * SURGICAL_UPPER_FACTOR if surgical else high
        return line.ratio_to_historic is not None and not low <= line.ratio_to_historic <= ceiling

    total_allowed = sum(l.allowed_amt for l in lines)
    patient_share = sum(l.patient_amt for l in lines)
    est = Estimate(
        visit.encounter_id, "COLLECT",
        total_allowed=total_allowed,
        insurer_expected=total_allowed - patient_share,
        patient_share=patient_share,
        collect_now=patient_share,
        benchmark_flag=any(off_benchmark(l) for l in lines),
        lines=lines,
    )

    # Explain the amount in the order it was worked out.
    say = est.reasons.append
    if medicare:
        say(f"Medicare's fee schedule allows ${total_allowed:,.2f} for this visit.")
    else:
        say(f"The insurer's contract pays {visit.contract_pct:f}% of the Medicare price, so ${total_allowed:,.2f} is allowed.")
    if halved:
        say("The lower-priced second procedure is paid at half.")
    if medicare and any(is_medicare_lab(l.hcpcs_cd) for l in lines):
        say("Lab tests cost the patient nothing under Medicare.")
    if not medicare and visit.copay_amt and any(is_office_visit(l.hcpcs_cd) for l in lines):
        say(f"The office visit costs the plan's flat ${visit.copay_amt:,.2f} copay.")
    deductible_total = sum(l.deductible_applied for l in lines)
    coinsurance_total = sum(l.coinsurance_amt for l in lines)
    if deductible_total:
        say(f"${deductible_total:,.2f} of unmet deductible is applied first.")
    else:
        say("The deductible is already met.")
    if coinsurance_total:
        say(f"The patient's {visit.coinsurance_pct:f}% coinsurance on the rest is ${coinsurance_total:,.2f}.")
    if medigap:
        paid_pct, pays_deductible, visit_copay = medigap
        covers = "all" if paid_pct == 100 else f"{paid_pct:f}%"
        text = f"Supplement Plan {visit.secondary_plan} pays {covers} of that coinsurance"
        if visit_copay:
            text += f", except up to ${visit_copay:,.2f} of an office visit"
        text += ", and the deductible." if pays_deductible else "; it does not pay the deductible."
        say(text)
    if capped:
        say(f"The plan's out-of-pocket maximum limits the patient to ${patient_share:,.2f}.")

    def hold(decision, reason):
        est.decision, est.block_reason, est.collect_now = decision, reason, Decimal("0.00")
        say(STOP_TEXT[reason])

    upfront = UPFRONT_PCT.get(visit.payer_type, Decimal(100))
    if visit.secondary_type not in (None, "MEDIGAP"):
        hold("BILL_LATER", "SECONDARY_PAYER_UNKNOWN")
    elif est.benchmark_flag:
        hold("BLOCKED", "BENCHMARK_OUTLIER")
    elif patient_share == 0:
        say("Nothing is due from the patient.")
    elif upfront < 100:
        est.decision, est.collect_now = "DEPOSIT", cents(patient_share * upfront / 100)
        say(f"The patient owes an estimated ${patient_share:,.2f}; {upfront:f}% (${est.collect_now:,.2f}) is taken today "
            "and the rest is billed after the insurer responds.")
    else:
        say(f"The patient owes ${patient_share:,.2f}, collected in full today.")
    return est
