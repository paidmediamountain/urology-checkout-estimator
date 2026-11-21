-- Urology checkout estimator: Snowflake schemas and tables.
-- Run top to bottom in a worksheet. Not yet executed against a live account.
--
-- Layers
--   RAW       files exactly as they arrive (Epic-style FHIR JSON, Medicare CSV)
--   REF       Medicare reference data: fee schedule and billing history
--   CLINICAL  one row per patient / visit / diagnosis / procedure, flattened from RAW
--   BILLING   coded lines, estimates, money collected, insurer remittances
--   MONITOR   audit trail and estimate-vs-actual views

CREATE DATABASE IF NOT EXISTS UROLOGY_BILLING;
USE DATABASE UROLOGY_BILLING;

CREATE SCHEMA IF NOT EXISTS RAW;
CREATE SCHEMA IF NOT EXISTS REF;
CREATE SCHEMA IF NOT EXISTS CLINICAL;
CREATE SCHEMA IF NOT EXISTS BILLING;
CREATE SCHEMA IF NOT EXISTS MONITOR;

------------------------------------------------------------------------------
-- RAW
------------------------------------------------------------------------------

-- One row per FHIR bundle (one bundle = one closed visit in Epic-style FHIR format).
CREATE TABLE IF NOT EXISTS RAW.FHIR_BUNDLE (
    file_name      STRING,
    loaded_at      TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    payload        VARIANT
);

-- Charges the clinician entered at the visit. In a real Epic site these come
-- from the reporting database extract, not from the FHIR API.
CREATE TABLE IF NOT EXISTS RAW.CLINICIAN_CHARGE (
    encounter_id   STRING,
    line_no        NUMBER(3),
    hcpcs_cd       STRING,
    modifier       STRING,
    units          NUMBER(5),
    icd10_cd       STRING,
    entered_by_npi STRING,
    entered_at     TIMESTAMP_NTZ
);

------------------------------------------------------------------------------
-- REF: Medicare reference data
------------------------------------------------------------------------------

-- Physician fee schedule relative values (CMS RVU file, one row per code+modifier).
CREATE TABLE IF NOT EXISTS REF.PFS_RVU (
    fee_year           NUMBER(4),
    hcpcs_cd           STRING,
    modifier           STRING,
    status_cd          STRING,          -- A = active, payable
    work_rvu           NUMBER(8,2),
    pe_rvu_nonfacility NUMBER(8,2),     -- practice expense, office setting
    pe_rvu_facility    NUMBER(8,2),     -- practice expense, hospital setting
    mp_rvu             NUMBER(8,2),     -- malpractice
    mult_proc_ind      STRING,          -- drives the second-procedure discount
    conversion_factor  NUMBER(8,4),
    PRIMARY KEY (fee_year, hcpcs_cd, modifier)
);

-- Geographic adjustment per Medicare locality.
CREATE TABLE IF NOT EXISTS REF.PFS_GPCI (
    fee_year       NUMBER(4),
    mac            STRING,
    state          STRING,
    locality_cd    STRING,
    locality_name  STRING,
    work_gpci      NUMBER(6,3),
    pe_gpci        NUMBER(6,3),
    mp_gpci        NUMBER(6,3),
    PRIMARY KEY (fee_year, mac, locality_cd)
);

-- What each commercial insurer pays, as a percent of the Medicare price.
-- Sample values are invented; real ones come from the clinic's contracts.
CREATE TABLE IF NOT EXISTS REF.PAYER_CONTRACT (
    payer_name       STRING PRIMARY KEY,
    pct_of_medicare  NUMBER(6,2)
);

-- Historic Medicare billing by procedure (CMS "by Geography and Service").
CREATE TABLE IF NOT EXISTS REF.MEDICARE_SERVICE_HISTORY (
    data_year        NUMBER(4),
    geo_level        STRING,            -- National or State
    geo_cd           STRING,
    hcpcs_cd         STRING,
    place_of_service STRING,            -- O = office, F = facility
    tot_providers    NUMBER,
    tot_benes        NUMBER,
    tot_services     NUMBER(18,1),      -- CMS reports fractional service counts
    avg_submitted    NUMBER(12,2),
    avg_allowed      NUMBER(12,2),
    avg_paid         NUMBER(12,2)
);

-- Historic Medicare billing per urologist per procedure (CMS "by Provider and Service").
CREATE TABLE IF NOT EXISTS REF.UROLOGIST_SERVICE (
    data_year        NUMBER(4),
    npi              STRING,
    state            STRING,
    hcpcs_cd         STRING,
    place_of_service STRING,
    tot_benes        NUMBER,
    tot_services     NUMBER(18,1),      -- CMS reports fractional service counts
    avg_submitted    NUMBER(12,2),
    avg_allowed      NUMBER(12,2),
    avg_paid         NUMBER(12,2)
);

-- Patient age and sex mix per urologist (CMS "by Provider"). This is the only
-- public place age bands appear, so peer comparisons join through here.
CREATE TABLE IF NOT EXISTS REF.UROLOGIST_PROFILE (
    data_year       NUMBER(4),
    npi             STRING,
    state           STRING,
    bene_avg_age    NUMBER(5,1),
    bene_age_lt_65  NUMBER,
    bene_age_65_74  NUMBER,
    bene_age_75_84  NUMBER,
    bene_age_gt_84  NUMBER,
    bene_female     NUMBER,
    bene_male       NUMBER,
    PRIMARY KEY (data_year, npi)
);

------------------------------------------------------------------------------
-- CLINICAL: flattened from RAW.FHIR_BUNDLE
------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS CLINICAL.PATIENT (
    patient_id   STRING PRIMARY KEY,   -- Epic-style FHIR id, never the MRN
    birth_date   DATE,
    sex          STRING,
    zip5         STRING
);

CREATE TABLE IF NOT EXISTS CLINICAL.COVERAGE (
    coverage_id     STRING PRIMARY KEY,
    patient_id      STRING,
    coverage_order  NUMBER(2),         -- 1 = primary, 2 = secondary
    payer_name      STRING,
    payer_type      STRING,            -- MEDICARE_B, MEDIGAP, MEDICAID, COMMERCIAL
    plan_class      STRING,            -- e.g. QMB, PLAN_G, HDHP
    status          STRING,
    period_start    DATE
);

CREATE TABLE IF NOT EXISTS CLINICAL.ENCOUNTER (
    encounter_id   STRING PRIMARY KEY,
    patient_id     STRING,
    provider_npi   STRING,
    status         STRING,             -- finished = visit closed
    class_cd       STRING,             -- AMB = office visit
    started_at     TIMESTAMP_NTZ,
    ended_at       TIMESTAMP_NTZ,
    location_name  STRING
);

CREATE TABLE IF NOT EXISTS CLINICAL.DIAGNOSIS (
    condition_id   STRING PRIMARY KEY,
    encounter_id   STRING,
    icd10_cd       STRING,
    description    STRING
);

CREATE TABLE IF NOT EXISTS CLINICAL.PROCEDURE_PERFORMED (
    procedure_id   STRING PRIMARY KEY,
    encounter_id   STRING,
    hcpcs_cd       STRING,
    description    STRING,
    performed_at   TIMESTAMP_NTZ
);

CREATE TABLE IF NOT EXISTS CLINICAL.CLINICAL_NOTE (
    note_id        STRING PRIMARY KEY,
    encounter_id   STRING,
    note_type      STRING,
    authored_at    TIMESTAMP_NTZ,
    note_text      STRING
);

-- Result of the insurance eligibility check run at checkout. Not part of the Epic-style FHIR data.
CREATE TABLE IF NOT EXISTS CLINICAL.ELIGIBILITY_SNAPSHOT (
    encounter_id          STRING,
    coverage_id           STRING,
    checked_at            TIMESTAMP_NTZ,
    deductible_remaining  NUMBER(10,2),
    coinsurance_pct       NUMBER(5,2),
    copay_amt             NUMBER(10,2),
    oop_remaining         NUMBER(10,2), -- left before the out-of-pocket maximum; NULL if none
    is_qmb                BOOLEAN,     -- patient may not be charged cost-sharing
    has_secondary         BOOLEAN
);

------------------------------------------------------------------------------
-- BILLING
------------------------------------------------------------------------------

-- Final code list for the visit, with where each line came from.
CREATE TABLE IF NOT EXISTS BILLING.CODED_LINE (
    encounter_id           STRING,
    line_no                NUMBER(3),
    hcpcs_cd               STRING,
    modifier               STRING,
    units                  NUMBER(5),
    icd10_cd               STRING,
    source                 STRING,      -- CLINICIAN, LLM, CODER
    llm_confidence         NUMBER(4,3),
    agrees_with_clinician  BOOLEAN,
    model_version          STRING,
    prompt_version         STRING,
    created_at             TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    PRIMARY KEY (encounter_id, line_no)
);

CREATE TABLE IF NOT EXISTS BILLING.ESTIMATE (
    estimate_id        STRING PRIMARY KEY,
    encounter_id       STRING,
    created_at         TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    total_allowed      NUMBER(10,2),
    insurer_expected   NUMBER(10,2),
    patient_share      NUMBER(10,2),
    collect_now        NUMBER(10,2),    -- what the front desk is told to take today
    range_low          NUMBER(10,2),    -- unused until estimate-vs-actual errors exist to calibrate it
    range_high         NUMBER(10,2),
    confidence         NUMBER(4,3),
    decision           STRING,          -- COLLECT, DEPOSIT (part upfront), BILL_LATER, BLOCKED
    block_reason       STRING,          -- why not a plain COLLECT, e.g. QMB, NO_CONTRACT_RATE, BENCHMARK_OUTLIER
    benchmark_flag     BOOLEAN,
    fee_year           NUMBER(4),
    engine_version     STRING,
    explanation        STRING,          -- plain-language reasons shown to staff and patient
    approved_by        STRING
);

CREATE TABLE IF NOT EXISTS BILLING.ESTIMATE_LINE (
    estimate_id         STRING,
    line_no             NUMBER(3),
    hcpcs_cd            STRING,
    modifier            STRING,
    units               NUMBER(5),
    allowed_amt         NUMBER(10,2),
    mult_proc_discount  BOOLEAN,
    deductible_applied  NUMBER(10,2),
    coinsurance_amt     NUMBER(10,2),
    patient_amt         NUMBER(10,2),
    price_source        STRING,         -- which REF row the price came from
    PRIMARY KEY (estimate_id, line_no)
);

CREATE TABLE IF NOT EXISTS BILLING.COLLECTION (
    collection_id   STRING PRIMARY KEY,
    estimate_id     STRING,
    collected_at    TIMESTAMP_NTZ,
    amount          NUMBER(10,2),
    method          STRING,
    collected_by    STRING
);

-- What the insurer actually decided, days later.
CREATE TABLE IF NOT EXISTS BILLING.REMITTANCE (
    remittance_id         STRING PRIMARY KEY,
    encounter_id          STRING,
    received_at           TIMESTAMP_NTZ,
    payer_name            STRING,
    allowed_actual        NUMBER(10,2),
    paid_actual           NUMBER(10,2),
    patient_resp_actual   NUMBER(10,2)
);

------------------------------------------------------------------------------
-- MONITOR
------------------------------------------------------------------------------

-- Append-only log of every tool call, model call and human approval.
CREATE TABLE IF NOT EXISTS MONITOR.AUDIT_EVENT (
    event_id      STRING,
    event_at      TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    actor         STRING,               -- agent, engine, or a staff user id
    encounter_id  STRING,
    action        STRING,
    detail        VARIANT
);

-- One row each time an estimate is requested at billing. The hospital is invoiced
-- a flat fee only for calls that end with money collected from the patient.
CREATE TABLE IF NOT EXISTS MONITOR.USAGE_EVENT (
    event_id      STRING,
    called_at     TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    location_name STRING,
    encounter_id  STRING,
    estimate_id   STRING,
    decision      STRING,
    collected_amt NUMBER(10,2),
    fee_amt       NUMBER(6,2)           -- 5.00 when collected_amt > 0, otherwise 0
);

-- Calls and fees per location per day. The goal for a multi-specialty hospital is
-- $8,000 to $20,000 a day (1,600 to 4,000 collecting calls at $5.00); a single urology
-- location with 60 to 80 patients a day tops out at $300 to $400.
CREATE OR REPLACE VIEW MONITOR.V_DAILY_USAGE_FEES AS
SELECT
    location_name,
    called_at::DATE                     AS usage_date,
    COUNT(*)                            AS calls,
    SUM(fee_amt)                        AS fees,
    COUNT_IF(collected_amt > 0)         AS calls_that_collected,
    SUM(collected_amt)                  AS collected_from_patients,
    SUM(fee_amt) >= 8000                AS met_hospital_goal
FROM MONITOR.USAGE_EVENT
GROUP BY location_name, called_at::DATE;

-- Estimate vs. what the insurer finally said. Positive error = over-estimated.
CREATE OR REPLACE VIEW MONITOR.V_ESTIMATE_ACCURACY AS
SELECT
    e.estimate_id,
    e.encounter_id,
    e.decision,
    e.patient_share                                   AS estimated_share,
    r.patient_resp_actual                             AS actual_share,
    e.patient_share - r.patient_resp_actual           AS signed_error,
    ABS(e.patient_share - r.patient_resp_actual)      AS abs_error,
    r.patient_resp_actual BETWEEN e.range_low AND e.range_high AS inside_range,
    COALESCE(c.collected, 0)                          AS collected,
    GREATEST(COALESCE(c.collected, 0) - r.patient_resp_actual, 0) AS refund_due
FROM BILLING.ESTIMATE e
JOIN BILLING.REMITTANCE r ON r.encounter_id = e.encounter_id
LEFT JOIN (
    SELECT estimate_id, SUM(amount) AS collected
    FROM BILLING.COLLECTION
    GROUP BY estimate_id
) c ON c.estimate_id = e.estimate_id;

-- Each priced line next to the national Medicare average for the same code
-- in the office setting. A large ratio is a reason to hold collection.
CREATE OR REPLACE VIEW MONITOR.V_BENCHMARK_CHECK AS
SELECT
    l.estimate_id,
    l.line_no,
    l.hcpcs_cd,
    l.allowed_amt,
    h.avg_allowed                        AS national_avg_allowed,
    l.allowed_amt / NULLIF(h.avg_allowed, 0) AS ratio_to_national,
    h.data_year
FROM BILLING.ESTIMATE_LINE l
LEFT JOIN REF.MEDICARE_SERVICE_HISTORY h
       ON h.hcpcs_cd = l.hcpcs_cd
      AND h.geo_level = 'National'
      AND h.place_of_service = 'O'
QUALIFY ROW_NUMBER() OVER (PARTITION BY l.estimate_id, l.line_no ORDER BY h.data_year DESC) = 1;
