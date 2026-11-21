-- Load the sample files, then flatten the Epic-style FHIR bundles into CLINICAL tables.
-- Run from SnowSQL in the project root (PUT needs a local client).
-- Not yet executed against a live account.

USE DATABASE UROLOGY_BILLING;

CREATE STAGE IF NOT EXISTS RAW.LANDING;
CREATE FILE FORMAT IF NOT EXISTS RAW.CSV_HEADER TYPE = CSV SKIP_HEADER = 1 FIELD_OPTIONALLY_ENCLOSED_BY = '"' EMPTY_FIELD_AS_NULL = TRUE;

PUT file://sample_data/epic_fhir/*.json                      @RAW.LANDING/fhir     AUTO_COMPRESS = TRUE;
PUT file://sample_data/epic_charges/clinician_charges.csv    @RAW.LANDING/charges  AUTO_COMPRESS = TRUE;
PUT file://sample_data/epic_charges/eligibility_snapshots.csv @RAW.LANDING/elig    AUTO_COMPRESS = TRUE;
PUT file://sample_data/medicare/*.csv                        @RAW.LANDING/medicare AUTO_COMPRESS = TRUE;
PUT file://sample_data/fee_schedule/pfs_rvu_2026.csv         @RAW.LANDING/pfs_rvu  AUTO_COMPRESS = TRUE;
PUT file://sample_data/fee_schedule/pfs_gpci_2026.csv        @RAW.LANDING/pfs_gpci AUTO_COMPRESS = TRUE;
PUT file://sample_data/contracts/payer_contracts.csv         @RAW.LANDING/contracts AUTO_COMPRESS = TRUE;

------------------------------------------------------------------------------
-- RAW and REF loads
------------------------------------------------------------------------------

COPY INTO RAW.FHIR_BUNDLE (file_name, payload)
FROM (SELECT METADATA$FILENAME, $1 FROM @RAW.LANDING/fhir)
FILE_FORMAT = (TYPE = JSON);

COPY INTO RAW.CLINICIAN_CHARGE
FROM @RAW.LANDING/charges FILE_FORMAT = (FORMAT_NAME = RAW.CSV_HEADER);

COPY INTO CLINICAL.ELIGIBILITY_SNAPSHOT
FROM @RAW.LANDING/elig FILE_FORMAT = (FORMAT_NAME = RAW.CSV_HEADER);

COPY INTO REF.MEDICARE_SERVICE_HISTORY
FROM @RAW.LANDING/medicare FILE_FORMAT = (FORMAT_NAME = RAW.CSV_HEADER);

COPY INTO REF.PFS_RVU
FROM @RAW.LANDING/pfs_rvu FILE_FORMAT = (FORMAT_NAME = RAW.CSV_HEADER);

COPY INTO REF.PFS_GPCI
FROM @RAW.LANDING/pfs_gpci FILE_FORMAT = (FORMAT_NAME = RAW.CSV_HEADER);

COPY INTO REF.PAYER_CONTRACT
FROM @RAW.LANDING/contracts FILE_FORMAT = (FORMAT_NAME = RAW.CSV_HEADER);

------------------------------------------------------------------------------
-- Flatten FHIR: one row per resource, then one insert per resource type
------------------------------------------------------------------------------

CREATE OR REPLACE TEMPORARY TABLE RAW.FHIR_RESOURCE AS
SELECT
    e.value:resource:resourceType::STRING AS resource_type,
    e.value:resource                      AS r
FROM RAW.FHIR_BUNDLE b,
     LATERAL FLATTEN(input => b.payload:entry) e;

INSERT INTO CLINICAL.PATIENT (patient_id, birth_date, sex, zip5)
SELECT r:id::STRING, r:birthDate::DATE, r:gender::STRING, r:address[0]:postalCode::STRING
FROM RAW.FHIR_RESOURCE WHERE resource_type = 'Patient';

INSERT INTO CLINICAL.COVERAGE (coverage_id, patient_id, coverage_order, payer_name, payer_type, plan_class, status, period_start)
SELECT
    r:id::STRING,
    SPLIT_PART(r:beneficiary:reference::STRING, '/', 2),
    r:"order"::NUMBER,
    r:payor[0]:display::STRING,
    r:type:coding[0]:code::STRING,
    r:class[0]:value::STRING,
    r:status::STRING,
    r:period:start::DATE
FROM RAW.FHIR_RESOURCE WHERE resource_type = 'Coverage';

INSERT INTO CLINICAL.ENCOUNTER (encounter_id, patient_id, provider_npi, status, class_cd, started_at, ended_at, location_name)
SELECT
    r:id::STRING,
    SPLIT_PART(r:subject:reference::STRING, '/', 2),
    r:participant[0]:individual:identifier:value::STRING,
    r:status::STRING,
    r:class:code::STRING,
    r:period:start::TIMESTAMP_TZ::TIMESTAMP_NTZ,
    r:period:"end"::TIMESTAMP_TZ::TIMESTAMP_NTZ,
    r:location[0]:location:display::STRING
FROM RAW.FHIR_RESOURCE WHERE resource_type = 'Encounter';

INSERT INTO CLINICAL.DIAGNOSIS (condition_id, encounter_id, icd10_cd, description)
SELECT
    r:id::STRING,
    SPLIT_PART(r:encounter:reference::STRING, '/', 2),
    r:code:coding[0]:code::STRING,
    r:code:text::STRING
FROM RAW.FHIR_RESOURCE WHERE resource_type = 'Condition';

INSERT INTO CLINICAL.PROCEDURE_PERFORMED (procedure_id, encounter_id, hcpcs_cd, description, performed_at)
SELECT
    r:id::STRING,
    SPLIT_PART(r:encounter:reference::STRING, '/', 2),
    r:code:coding[0]:code::STRING,
    r:code:text::STRING,
    r:performedDateTime::TIMESTAMP_TZ::TIMESTAMP_NTZ
FROM RAW.FHIR_RESOURCE WHERE resource_type = 'Procedure';

INSERT INTO CLINICAL.CLINICAL_NOTE (note_id, encounter_id, note_type, authored_at, note_text)
SELECT
    r:id::STRING,
    SPLIT_PART(r:context:encounter[0]:reference::STRING, '/', 2),
    r:type:coding[0]:display::STRING,
    r:date::TIMESTAMP_TZ::TIMESTAMP_NTZ,
    BASE64_DECODE_STRING(r:content[0]:attachment:data::STRING)
FROM RAW.FHIR_RESOURCE WHERE resource_type = 'DocumentReference';

------------------------------------------------------------------------------
-- Example: what the checkout screen needs for each visit
------------------------------------------------------------------------------

SELECT
    en.encounter_id,
    DATEDIFF(year, p.birth_date, en.started_at) AS age_at_visit,
    cov.payer_type,
    el.is_qmb,
    el.has_secondary,
    ch.hcpcs_cd,
    ch.modifier,
    h.avg_allowed AS national_avg_allowed_office
FROM CLINICAL.ENCOUNTER en
JOIN CLINICAL.PATIENT p               ON p.patient_id = en.patient_id
JOIN CLINICAL.COVERAGE cov            ON cov.patient_id = en.patient_id AND cov.coverage_order = 1
JOIN CLINICAL.ELIGIBILITY_SNAPSHOT el ON el.encounter_id = en.encounter_id
JOIN RAW.CLINICIAN_CHARGE ch          ON ch.encounter_id = en.encounter_id
LEFT JOIN REF.MEDICARE_SERVICE_HISTORY h
       ON h.hcpcs_cd = ch.hcpcs_cd AND h.geo_level = 'National'
      AND h.place_of_service = 'O' AND h.data_year = 2024
ORDER BY en.encounter_id, ch.line_no;
