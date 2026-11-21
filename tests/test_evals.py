import unittest
from decimal import Decimal as D

from estimator.load import load_price_book
from evals.coding import allowed_codes, baseline_coder, build_prompt
from evals.score import load_cases, patient_share, score


class GoldSet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = load_cases()
        cls.price_for = staticmethod(load_price_book("MN", "00", "27"))
        cls.allowed = set(allowed_codes())

    def run_score(self, predictions):
        return score(self.cases, predictions, self.price_for, self.allowed)

    def test_ids_are_unique_and_every_gold_code_is_allowed_and_priceable(self):
        self.assertEqual(len({c["id"] for c in self.cases}), len(self.cases))
        for c in self.cases:
            self.assertTrue({l["hcpcs_cd"] for l in c["gold"]} <= self.allowed, c["id"])
            self.assertIsNotNone(patient_share(c["gold"], self.price_for), c["id"])

    def test_gold_scored_against_itself_is_perfect(self):
        m = self.run_score({c["id"]: c["gold"] for c in self.cases})
        self.assertEqual((m["visits_exactly_right"], m["code_f1"], m["modifier_accuracy"]), (1.0, 1.0, 1.0))
        self.assertEqual((m["mean_abs_patient_dollar_error"], m["wrong"]), ("0.00", []))

    def test_an_extra_code_counts_as_over_collection(self):
        predictions = {c["id"]: c["gold"] for c in self.cases}
        predictions["C06"] = predictions["C06"] + [{"hcpcs_cd": "52000", "modifier": ""}]
        m = self.run_score(predictions)
        self.assertEqual(m["over_collect_rate"], round(1 / len(self.cases), 3))
        self.assertEqual(m["under_collect_rate"], 0.0)
        # 20% of the $215.69 cystoscopy.
        self.assertEqual(m["max_abs_patient_dollar_error"], "43.14")
        self.assertEqual([w["id"] for w in m["wrong"]], ["C06"])

    def test_wrong_modifier_fails_the_visit_but_not_the_codes(self):
        predictions = {c["id"]: c["gold"] for c in self.cases}
        predictions["C01"] = [{"hcpcs_cd": "99214", "modifier": ""}, {"hcpcs_cd": "52000", "modifier": ""}]
        m = self.run_score(predictions)
        self.assertEqual(m["code_f1"], 1.0)
        self.assertLess(m["modifier_accuracy"], 1.0)
        self.assertLess(m["visits_exactly_right"], 1.0)

    def test_invented_and_missing_predictions_are_reported(self):
        predictions = {c["id"]: c["gold"] for c in self.cases if c["id"] != "C02"}
        predictions["C04"] = [{"hcpcs_cd": "12345", "modifier": ""}]
        m = self.run_score(predictions)
        self.assertEqual((m["codes_outside_allowed_list"], m["unpriceable_visits"], m["missing_predictions"]),
                         (1, ["C04"], ["C02"]))

    def test_patient_share_of_nothing_is_zero(self):
        self.assertEqual(patient_share([], self.price_for), D("0.00"))


class Coding(unittest.TestCase):
    def test_baseline_skips_procedures_that_were_only_planned(self):
        note = "Established patient. Discussed cystoscopy; cystoscopy scheduled for next week. Moderate complexity decision making."
        self.assertEqual(baseline_coder(note), [{"hcpcs_cd": "99214", "modifier": ""}])

    def test_baseline_adds_modifier_25_for_a_procedure_but_not_for_a_lab(self):
        scan = baseline_coder("Established patient. Bladder scan performed: 40 mL. Low complexity decision making.")
        lab = baseline_coder("Established patient. Office urinalysis performed: negative. Low complexity decision making.")
        self.assertEqual((scan[0]["modifier"], lab[0]["modifier"]), ("25", ""))

    def test_prompt_lists_the_allowed_codes_and_ends_with_the_note(self):
        prompt = build_prompt("NOTE TEXT")
        self.assertIn(", ".join(allowed_codes()), prompt)
        self.assertTrue(prompt.endswith("VISIT NOTE:\nNOTE TEXT"))


if __name__ == "__main__":
    unittest.main()
