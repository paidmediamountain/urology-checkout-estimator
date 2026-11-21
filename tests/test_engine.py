"""Run:  python3 -m unittest discover tests"""
import unittest
from decimal import Decimal as D

from estimator.engine import ChargeLine, Price, Visit, estimate_visit, pfs_allowed, split_cost
from estimator.load import load_price_book, load_visits


def visit(**over):
    base = dict(
        encounter_id="E1", payer_type="MEDICARE_B", secondary_type=None, is_qmb=False,
        deductible_remaining=D(0), coinsurance_pct=D(20),
        charges=[ChargeLine(1, "99214", "25"), ChargeLine(2, "52000")],
        procedures_documented=["52000"],
    )
    return Visit(**{**base, **over})


BOOK = {
    "99214": Price(D("100.00"), "test"),
    "52000": Price(D("200.00"), "test", "2", D("190.00")),
    "55707": Price(D("150.00"), "test", "2"),
    "52332": Price(D("300.00"), "test", "3"),
    "52356": Price(D("400.00"), "test", "3"),
    "81003": Price(D("2.19"), "test"),
}


def book(code, modifier=""):
    return BOOK.get(code)


class Formula(unittest.TestCase):
    def test_relative_values_times_locality_times_conversion_factor(self):
        # 2026 values for an established-patient visit (99214) in Minnesota.
        got = pfs_allowed(D("1.92"), D("2.00"), D("0.14"), D("1.000"), D("1.029"), D("0.296"), D("33.4009"))
        self.assertEqual(got, D("134.25"))


class SplitCost(unittest.TestCase):
    def test_coinsurance_only(self):
        self.assertEqual(split_cost([("52000", D("200.00"))], 0, 20), [(D("0.00"), D("40.00"), D("40.00"))])

    def test_deductible_spills_across_lines(self):
        got = split_cost([("99214", D("100.00")), ("52000", D("200.00"))], D("150.00"), 20)
        self.assertEqual(got[0], (D("100.00"), D("0.00"), D("100.00")))
        self.assertEqual(got[1], (D("50.00"), D("30.00"), D("80.00")))

    def test_lab_has_no_cost_sharing_and_leaves_deductible_alone(self):
        got = split_cost([("81003", D("2.19")), ("99214", D("100.00"))], D("50.00"), 20)
        self.assertEqual(got[0], (D("0.00"), D("0.00"), D("0.00")))
        self.assertEqual(got[1], (D("50.00"), D("10.00"), D("60.00")))

    def test_rounds_half_up_to_cents(self):
        self.assertEqual(split_cost([("99213", D("81.82"))], 0, 20)[0][2], D("16.36"))


class Decisions(unittest.TestCase):
    def test_medicare_only_collects_twenty_percent(self):
        e = estimate_visit(visit(), book)
        self.assertEqual((e.decision, e.total_allowed, e.patient_share, e.collect_now),
                         ("COLLECT", D("300.00"), D("60.00"), D("60.00")))
        self.assertEqual(e.insurer_expected, D("240.00"))

    def test_second_surgical_procedure_is_paid_at_half(self):
        charges = [ChargeLine(1, "55707"), ChargeLine(2, "52000"), ChargeLine(3, "99214", "25")]
        e = estimate_visit(visit(charges=charges, procedures_documented=["52000", "55707"]), book)
        self.assertEqual([(l.allowed_amt, l.mult_proc_discount) for l in e.lines],
                         [(D("75.00"), True), (D("200.00"), False), (D("100.00"), False)])
        self.assertEqual(e.patient_share, D("75.00"))

    def test_two_endoscopies_from_one_family_bill_later(self):
        charges = [ChargeLine(1, "52332"), ChargeLine(2, "52356")]
        e = estimate_visit(visit(charges=charges, procedures_documented=[]), book)
        self.assertEqual((e.decision, e.block_reason), ("BILL_LATER", "UNSUPPORTED_PRICING_RULE"))

    def supplement(self, plan, **over):
        return estimate_visit(visit(secondary_type="MEDIGAP", secondary_plan=plan, **over), book)

    def test_plan_g_pays_coinsurance_but_not_deductible(self):
        e = self.supplement("G")
        self.assertEqual((e.decision, e.patient_share), ("COLLECT", D("0.00")))
        self.assertEqual(e.reasons[-1], "Nothing is due from the patient.")
        self.assertEqual(self.supplement("G", deductible_remaining=D("75.00")).patient_share, D("75.00"))

    def test_plan_f_also_pays_the_deductible(self):
        self.assertEqual(self.supplement("F", deductible_remaining=D("75.00")).patient_share, D("0.00"))

    def test_plan_n_leaves_up_to_twenty_dollars_of_the_office_visit(self):
        # Office visit coinsurance is exactly $20; the cystoscopy coinsurance is fully paid.
        self.assertEqual([l.patient_amt for l in self.supplement("N").lines], [D("20.00"), D("0.00")])
        cheap = {"99214": Price(D("60.00"), "test")}
        e = estimate_visit(visit(secondary_type="MEDIGAP", secondary_plan="N", charges=[ChargeLine(1, "99214")],
                                 procedures_documented=[]), lambda code, modifier="": cheap.get(code))
        self.assertEqual(e.patient_share, D("12.00"))

    def test_plans_k_and_l_pay_part_of_the_coinsurance(self):
        self.assertEqual(self.supplement("K").patient_share, D("30.00"))
        self.assertEqual(self.supplement("L").patient_share, D("15.00"))

    def test_unlisted_supplement_plan_bills_later(self):
        for plan in (None, "BASIC"):
            e = self.supplement(plan)
            self.assertEqual((e.decision, e.block_reason, e.collect_now), ("BILL_LATER", "SUPPLEMENT_PLAN_UNKNOWN", D("0.00")))

    def test_every_estimate_explains_itself(self):
        blocked = estimate_visit(visit(is_qmb=True), book)
        self.assertIn("QMB", blocked.reasons[0])
        full = estimate_visit(visit(), book)
        self.assertEqual(full.reasons[-1], "The patient owes $60.00, collected in full today.")
        half = estimate_visit(visit(payer_type="COMMERCIAL", contract_pct=D(150)), book)
        self.assertIn("50% ($45.00) is taken today", half.reasons[-1])

    def test_qmb_is_blocked_before_anything_is_priced(self):
        e = estimate_visit(visit(is_qmb=True), lambda code, modifier: self.fail("priced a QMB visit"))
        self.assertEqual((e.decision, e.block_reason, e.collect_now), ("BLOCKED", "QMB", D("0.00")))

    def test_documented_procedure_missing_from_charges_blocks(self):
        e = estimate_visit(visit(procedures_documented=["52000", "51798"]), book)
        self.assertEqual((e.decision, e.block_reason), ("BLOCKED", "CODING_DISAGREEMENT"))

    def test_commercial_without_contract_rate_bills_later(self):
        e = estimate_visit(visit(payer_type="COMMERCIAL"), book)
        self.assertEqual((e.decision, e.block_reason, e.collect_now), ("BILL_LATER", "NO_CONTRACT_RATE", D("0.00")))

    def test_commercial_prices_at_contract_rate_and_takes_half_upfront(self):
        e = estimate_visit(visit(payer_type="COMMERCIAL", contract_pct=D(150)), book)
        self.assertEqual((e.total_allowed, e.patient_share), (D("450.00"), D("90.00")))
        self.assertEqual((e.decision, e.collect_now), ("DEPOSIT", D("45.00")))

    def test_commercial_copay_replaces_cost_sharing_on_the_office_visit(self):
        v = visit(payer_type="COMMERCIAL", contract_pct=D(100), copay_amt=D(40), deductible_remaining=D(50))
        e = estimate_visit(v, book)
        # Office visit: $40 copay. Cystoscopy: $50 deductible + 20% of the other $150.
        self.assertEqual([l.patient_amt for l in e.lines], [D("40.00"), D("80.00")])
        self.assertEqual(e.collect_now, D("60.00"))

    def test_patient_share_stops_at_out_of_pocket_maximum(self):
        v = visit(payer_type="COMMERCIAL", contract_pct=D(100), deductible_remaining=D(1000), oop_remaining=D(120))
        e = estimate_visit(v, book)
        self.assertEqual((e.patient_share, e.insurer_expected, e.collect_now), (D("120.00"), D("180.00"), D("60.00")))

    def test_commercial_lab_is_not_exempt_from_cost_sharing(self):
        v = visit(payer_type="COMMERCIAL", contract_pct=D(100), charges=[ChargeLine(1, "81003")], procedures_documented=[])
        self.assertEqual(estimate_visit(v, book).patient_share, D("0.44"))

    def test_nothing_owed_stays_a_plain_collect(self):
        v = visit(payer_type="COMMERCIAL", contract_pct=D(100), oop_remaining=D(0))
        e = estimate_visit(v, book)
        self.assertEqual((e.decision, e.collect_now), ("COLLECT", D("0.00")))

    def test_unknown_code_bills_later(self):
        e = estimate_visit(visit(charges=[ChargeLine(1, "00000")], procedures_documented=[]), book)
        self.assertEqual((e.decision, e.block_reason), ("BILL_LATER", "NO_PRICE"))

    def benchmark(self, allowed, historic, mult_proc_ind):
        def odd(code, modifier=""):
            return Price(D(allowed), "test", mult_proc_ind, D(historic)) if code == "52000" else book(code)
        e = estimate_visit(visit(), odd)
        return e.decision, e.block_reason, e.collect_now

    def test_price_far_from_past_average_blocks(self):
        blocked = ("BLOCKED", "BENCHMARK_OUTLIER", D("0.00"))
        self.assertEqual(self.benchmark("200.00", "100.00", "0"), blocked)   # 2.0x, not surgical
        self.assertEqual(self.benchmark("50.00", "100.00", "2"), blocked)    # 0.5x, too low for any code
        self.assertEqual(self.benchmark("330.00", "100.00", "2"), blocked)   # 3.3x, too high even for surgical

    def test_surgical_price_may_sit_up_to_twice_as_far_above_past_average(self):
        # Past averages for surgical codes mix full-price and half-price payments.
        self.assertEqual(self.benchmark("200.00", "100.00", "2")[0], "COLLECT")
        self.assertEqual(self.benchmark("300.00", "100.00", "3")[0], "COLLECT")

    def test_non_medigap_secondary_bills_later(self):
        e = estimate_visit(visit(secondary_type="MEDICAID"), book)
        self.assertEqual((e.decision, e.block_reason, e.collect_now), ("BILL_LATER", "SECONDARY_PAYER_UNKNOWN", D("0.00")))


class SampleVisits(unittest.TestCase):
    """The six synthetic Epic-style visits, priced with the 2026 fee schedule for Minnesota."""

    @classmethod
    def setUpClass(cls):
        cls.price_for = staticmethod(load_price_book("MN", "00", "27"))
        cls.by_id = {v.encounter_id: estimate_visit(v, cls.price_for) for v in load_visits()}

    def test_decisions(self):
        got = {k: (e.decision, e.block_reason) for k, e in self.by_id.items()}
        self.assertEqual(got, {
            "eSYN-ENC-0001": ("COLLECT", None),
            "eSYN-ENC-0002": ("COLLECT", None),
            "eSYN-ENC-0003": ("BLOCKED", "QMB"),
            "eSYN-ENC-0004": ("DEPOSIT", None),
            "eSYN-ENC-0005": ("DEPOSIT", None),
            "eSYN-ENC-0006": ("COLLECT", None),
        })

    def test_plan_n_visit_owes_twenty_dollars(self):
        self.assertEqual(self.by_id["eSYN-ENC-0006"].collect_now, D("20.00"))

    def test_vasectomy_owes_full_contract_price_under_deductible(self):
        e = self.by_id["eSYN-ENC-0004"]
        self.assertEqual((e.total_allowed, e.patient_share, e.collect_now), (D("411.95"), D("411.95"), D("205.98")))

    def test_bcbs_cystoscopy_copay_plus_deductible_and_coinsurance(self):
        e = self.by_id["eSYN-ENC-0005"]
        self.assertEqual([l.allowed_amt for l in e.lines], [D("181.24"), D("291.18")])
        self.assertEqual([l.patient_amt for l in e.lines], [D("40.00"), D("258.24")])
        self.assertEqual(e.collect_now, D("149.12"))

    def test_cystoscopy_visit_amounts(self):
        e = self.by_id["eSYN-ENC-0001"]
        self.assertEqual([l.allowed_amt for l in e.lines], [D("134.25"), D("215.69")])
        self.assertEqual(e.patient_share, D("26.85") + D("43.14"))
        self.assertFalse(e.benchmark_flag)

    def test_supplement_visit_collects_nothing(self):
        self.assertEqual(self.by_id["eSYN-ENC-0002"].collect_now, D("0.00"))

    def test_lab_code_prices_from_history_not_the_fee_schedule(self):
        self.assertIn("historic", self.price_for("81003").source)

    def test_stent_insertion_is_not_blocked_by_thin_state_history(self):
        # Minnesota reported only 54 office stent insertions in 2024, so the national average is used.
        self.assertEqual(self.price_for("52332").historic_avg, D("198.61"))
        e = estimate_visit(visit(charges=[ChargeLine(1, "52332")], procedures_documented=[]), self.price_for)
        self.assertEqual((e.decision, e.benchmark_flag), ("COLLECT", False))

    def test_modifier_with_its_own_row_is_used(self):
        self.assertLess(self.price_for("76872", "26").allowed, self.price_for("76872").allowed)


if __name__ == "__main__":
    unittest.main()
