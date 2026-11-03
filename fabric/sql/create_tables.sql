-- Run once in PMG_DWH (Fabric Warehouse, T-SQL). Does not touch any Dim* table.
-- The Spark connector can append to Warehouse tables but cannot UPDATE or upsert, so enrichment
-- is stored append-only and read back through the *_latest view.

CREATE TABLE dbo.datamoon_enrichment
(
    contact_key       VARCHAR(512)  NOT NULL,   -- li:<linkedin> | em:<email> | ph:<digits>
    person_id         VARCHAR(128)  NULL,       -- DimPrimary.id when the contact came from the warehouse
    job_id            VARCHAR(64)   NOT NULL,
    enriched_at       DATETIME2(6)  NOT NULL,
    lookup_endpoint   VARCHAR(40)   NULL,
    status            VARCHAR(20)   NOT NULL,   -- enriched | no_match | error
    match_confidence  VARCHAR(16)   NULL,       -- strong | medium | weak | conflict
    match_reasons     VARCHAR(1000) NULL,
    mapped_json       VARCHAR(MAX)  NULL,       -- fields mapped onto the template (what we overwrite with)
    datamoon_json     VARCHAR(MAX)  NULL        -- raw identity as returned; see README "Sensitive data"
);

CREATE TABLE dbo.datamoon_jobs
(
    job_id            VARCHAR(64)   NOT NULL,
    mode              VARCHAR(10)   NOT NULL,   -- upload | specs
    client_name       VARCHAR(100)  NULL,
    status            VARCHAR(12)   NOT NULL,   -- complete | failed
    rows_in           INT           NULL,
    enriched          INT           NULL,
    skipped_recent    INT           NULL,
    no_match          INT           NULL,
    errors            INT           NULL,
    template_rows     INT           NULL,
    qc_pass           INT           NULL,
    qc_review         INT           NULL,
    qc_flag           INT           NULL,
    qc_not_run        INT           NULL,
    spec_json         VARCHAR(MAX)  NULL,
    finished_at       DATETIME2(6)  NOT NULL
);
GO

-- Latest enrichment per contact that actually returned data. Drives the 3-month rule:
-- a contact is re-enriched only when enriched_at is older than 90 days.
CREATE VIEW dbo.datamoon_enrichment_latest AS
SELECT contact_key, person_id, job_id, enriched_at, lookup_endpoint, status,
       match_confidence, match_reasons, mapped_json
FROM (
    SELECT e.*, ROW_NUMBER() OVER (PARTITION BY contact_key ORDER BY enriched_at DESC) AS rn
    FROM dbo.datamoon_enrichment e
    WHERE e.status IN ('enriched', 'no_match')
) x
WHERE rn = 1;
GO
