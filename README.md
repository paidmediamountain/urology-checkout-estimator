# Urology checkout estimator

An attempt to collect, at checkout, half of the patient's bill that would otherwise
go unpaid.

## Why

A medical office usually learns what a patient owes only after the insurer processes
the claim, days or weeks after the visit. By then the patient has gone home, and a
bill sent later often goes unpaid. The front desk can't ask for money at checkout
because nobody knows the amount yet.

This project works the amount out while the patient is still at the desk. After a
urology visit closes in an Epic-style patient record, it estimates what the visit will be billed, what
insurance should pay, and what the patient owes. It then asks for:

- **half of the estimated patient share** when the insurer is commercial, since the
  estimate can be off and the rest is billed once the insurer responds;
- **the full patient share** under Medicare, where the amount is exact;
- **nothing** when the patient may not legally be charged, when a supplement plan
  covers the share, or when the estimate can't be trusted.

Every amount comes with a plain-language reason the front desk can read to the
patient, and each price is checked against historic Medicare billing for the same
procedure before anything is collected.

It only ever asks for part of what the patient owes, never the insurer's portion.

## Pricing

The hospital pays **$5.00 for each call at the billing stage that ends with money
collected from the patient**. Calls that collect nothing are free: a blocked visit, a
bill-later visit, or a patient whose supplement plan covers their share.

**Example: one urology location.** With 60 to 80 patients a day, the fee comes to at
most $300 to $400 a day, about $75,000 to $100,000 a year over 250 working days. In
the six sample visits, four collect money, so the fee is $20.00 against $445.09 collected.

**Goal: a hospital.** Across a hospital's outpatient clinics, with more specialties and
more billing codes than the urology example covers, the goal is $8,000 to $20,000 a
day. At $5.00 that is 1,600 to 4,000 collecting visits a day. Getting there means
extending the fee schedule data and rules beyond the 12 urology codes built so far,
and adding hospital outpatient pricing.

Every call is logged in `MONITOR.USAGE_EVENT`, and `MONITOR.V_DAILY_USAGE_FEES` shows
calls, collections and fees per location per day.

Status: the pricing and patient-share engine runs locally on six sample visits,
covering Medicare, supplement plans and commercial insurance. The LLM coding step
is written as Snowflake SQL using OpenAI GPT-5; none of the SQL has been run against
a live Snowflake account.

## Run it

```bash
python3 -m estimator
```

```bash
python3 -m unittest discover tests
```

No dependencies beyond Python 3.10+.

## Scoring the coding step

`evals/cases.jsonl` holds 24 synthetic visit notes with the correct codes, including
traps: procedures that were only planned, procedures that include another, shorthand
notes, and an instruction planted in a note. Any coder is scored on the codes and on
what its mistakes would cost a patient at checkout.

```bash
python3 -m evals baseline
```

```bash
python3 -m evals score predictions.jsonl gpt-5
```

`python3 -m evals prompts` writes the exact prompt for each case, so the same test can
be run through any model. A predictions file has one JSON object per line:
`{"id": "C01", "lines": [{"hcpcs_cd": "99214", "modifier": "25"}]}`.

Keyword baseline, the number a model has to beat:

| Measure | Keyword baseline |
|---|---|
| Visits coded exactly right | 75.0% (18 of 24) |
| Code precision / recall | 87.5% / 92.1% |
| Mean patient-dollar error | $5.39 |
| Largest patient-dollar error | $51.09 |
| Visits that would over-collect | 13.0% |
| Visits that would under-collect | 8.7% |

The baseline is deliberately simple. It gets plain visits, lab tests, procedures that
were planned but not done, and the planted instruction right. It misses the two things
keyword rules can't do: recognising when one procedure includes another (0 of 4) and
reading shorthand notes (0 of 2). Those six cases are the target for the LLM step.

Next steps:

- Score GPT-5 on the same 24 cases through `snowflake/03_llm_coding.sql` and add its
  column to the table above.
- Have the answer key peer reviewed; it is currently the author's own.

## What is here

| Path | What it is |
|---|---|
| `estimator/engine.py` | Checkout rules: hard stops, fee schedule formula, deductible and coinsurance |
| `estimator/load.py` | Reads the sample files; the piece to swap for Snowflake queries |
| `evals/` | Gold set of coded notes, the scorer, the model prompt and a keyword baseline |
| `tests/` | Rule tests plus the six sample visits end to end |
| `snowflake/01_schema.sql` | Schemas and tables: RAW, REF, CLINICAL, BILLING, MONITOR |
| `snowflake/02_load_and_flatten.sql` | Loads the sample files and flattens Epic-style FHIR JSON into tables |
| `snowflake/03_llm_coding.sql` | Codes each visit from the doctor's note with GPT-5 inside Snowflake and flags disagreements with the clinician |
| `sample_data/epic_fhir/` | Six synthetic visits as Epic-style FHIR R4 bundles |
| `sample_data/epic_charges/` | Clinician-entered charges and eligibility results for those visits |
| `sample_data/fee_schedule/` | 2026 relative values for 12 urology codes, and every locality adjustment |
| `sample_data/contracts/` | Invented commercial rates, as a percent of the Medicare price |
| `sample_data/medicare/` | Historic Medicare averages for the same codes: national 2022-2024, by state 2024 |
| `scripts/` | Regenerates the sample visits and re-fetches the Medicare data |

## How a visit is decided

1. **Hard stops.** A patient with Medicaid cost-sharing protection (QMB) is never
   charged. A procedure documented in the Epic-style record but missing from the charges blocks the visit.
2. **Price.** Each code is priced with Medicare's formula: work, practice-expense and
   malpractice relative values, each times the clinic locality's adjustment, times the
   conversion factor. When two surgical procedures share a visit, the cheaper is paid at half.
   A commercial insurer's price is the Medicare price times that insurer's contract percent.
3. **Patient share.** Deductible first, then coinsurance. Under Medicare, lab tests carry
   no cost-sharing. A supplement plan is applied by its letter: most pay all of the
   coinsurance, Plan N leaves up to $20 of an office visit, K and L pay part, and C and F
   also pay the deductible. An unlisted plan is billed later instead of guessed.
   On a commercial plan the office visit costs the flat copay, and the total stops at
   what is left of the out-of-pocket maximum.
4. **Cross-check.** Each formula price is compared with the 2024 Medicare average for
   the same code in the same state (the national average when the state reported fewer
   than 1,000 services). A price outside 0.6x to 1.6x of it blocks collection. Surgical
   codes may go up to 3.2x, because their past averages mix full-price payments with
   half-price second procedures.
5. **Decision.** Medicare shares are exact and collected in full. Commercial shares are
   estimates, so half is taken at checkout as a deposit and the rest billed after the
   insurer responds (`UPFRONT_PCT` in `estimator/engine.py`). Otherwise bill later or block.
6. **Reasons.** Every estimate carries a plain-language list of how the amount was
   reached, for the front desk to read to the patient.

## The six sample visits

Priced for an office in Minnesota (one statewide Medicare locality).

| Visit | Coverage | Codes | Allowed | Decision |
|---|---|---|---|---|
| 1. Blood in urine, cystoscopy | Medicare only | 99214-25, 52000 | $349.94 | Collect $69.99 |
| 2. Elevated PSA, bladder scan | Medicare + supplement | 99213-25, 51798 | $107.06 | Collect $0 |
| 3. Recurrent infection | Medicare + Medicaid (QMB) | 99213, 81003 | not priced | Blocked |
| 4. Vasectomy | Commercial, $1,400 deductible left | 55250 | $411.95 | Patient owes $411.95; take $205.98 now |
| 5. Blood on urine test, cystoscopy | Blue Cross PPO, $40 copay, $250 deductible left | 99214-25, 52000 | $472.42 | Patient owes $298.24; take $149.12 now |
| 6. Kidney stone follow-up | Medicare + supplement Plan N | 99214 | $134.25 | Collect $20.00 |

## Not built yet

- The pricing rule for several endoscopies in one visit, bilateral procedures, and
  facility (hospital) pricing. Visits that need the first are sent to bill later.
- The lab fee schedule; lab lines use the historic average and cost the patient nothing.
- Real commercial contract rates and plan-specific benefit rules; the sample rates are invented.
- A calibrated range around each estimate, which needs estimate-versus-actual history.
- Running and scoring an LLM on the coding step; the SQL exists but is untested.
- A sharper historic comparison for surgical codes: the wide allowance stops false
  alarms but would miss a surgical price that is wrong by less than about 3x.
- History for codes introduced in 2026, such as the new prostate biopsy codes.
- Supplement plans from states with their own designs (Minnesota, Massachusetts, Wisconsin).
- Reconciliation against the insurer's actual payment.

## Data rules

- Every patient is synthetic. No real patient data belongs in this repo.
- "Epic-style" means the sample records follow FHIR R4, the public format Epic's API
  uses. They were generated for this project, not exported from an Epic system, and
  the project is not affiliated with or tested against Epic.
- Procedure code descriptions are copyrighted by the American Medical Association.
  The extracts here keep the codes and drop the descriptions; the raw Medicare
  download stays in `data/`, which is git-ignored.

## Regenerate

```bash
python3 scripts/make_sample_visits.py
python3 scripts/fetch_medicare_history.py
```

```bash
python3 -I scripts/build_fee_schedule.py
```
