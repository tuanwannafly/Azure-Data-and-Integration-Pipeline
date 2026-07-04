-- =============================================================================
-- Azure SQL schema for Azure Data Integration project
-- Tables: staging, company_revenue_yearly (transformed), live_ticks (streaming)
-- Run via: sqlcmd /S <server> /d <db> /U <user> /P <pass> -i schema.sql
-- All tables use upsert (MERGE) for idempotency (BR-02).
-- =============================================================================

-- ---------------------------------------------------------------------------
-- staging: raw SEC EDGAR facts after ADF Copy Activity (Blob -> SQL)
-- ---------------------------------------------------------------------------
IF OBJECT_ID(N'dbo.staging', N'U') IS NOT NULL DROP TABLE dbo.staging;
GO

CREATE TABLE dbo.staging (
    cik            BIGINT         NOT NULL,
    company_name   NVARCHAR(255)  NULL,
    tag_used       NVARCHAR(255)  NOT NULL,
    fiscal_year    INT            NOT NULL,
    fiscal_period  NVARCHAR(8)    NOT NULL,   -- FY, Q1, Q2, Q3, Q4
    form_type      NVARCHAR(16)   NULL,        -- 10-K, 10-Q, 10-K/A
    end_date       DATE           NULL,
    filed_date     DATE           NULL,
    value          DECIMAL(38, 4) NULL,
    unit           NVARCHAR(16)   NULL,
    ingested_at    DATETIME2(3)   NOT NULL CONSTRAINT DF_staging_ingested DEFAULT (SYSDATETIME()),
    CONSTRAINT PK_staging PRIMARY KEY CLUSTERED (cik, fiscal_year, fiscal_period, tag_used)
);
GO

CREATE INDEX IX_staging_filed ON dbo.staging (filed_date DESC);
GO

-- ---------------------------------------------------------------------------
-- company_revenue_yearly: transformed output (Databricks), one row per
-- company / fiscal year with year-over-year growth.
-- ---------------------------------------------------------------------------
IF OBJECT_ID(N'dbo.company_revenue_yearly', N'U') IS NOT NULL DROP TABLE dbo.company_revenue_yearly;
GO

CREATE TABLE dbo.company_revenue_yearly (
    cik             BIGINT         NOT NULL,
    company_name    NVARCHAR(255)  NULL,
    fiscal_year     INT            NOT NULL,
    revenue         DECIMAL(38, 4) NOT NULL,
    unit            NVARCHAR(16)   NOT NULL CONSTRAINT DF_cry_unit DEFAULT (N'USD'),
    prev_revenue    DECIMAL(38, 4) NULL,
    yoy_growth_pct  DECIMAL(10, 4) NULL,      -- NULL for the first year on record
    transformed_at  DATETIME2(3)   NOT NULL CONSTRAINT DF_cry_ts DEFAULT (SYSDATETIME()),
    CONSTRAINT PK_company_revenue_yearly PRIMARY KEY CLUSTERED (cik, fiscal_year)
);
GO

-- ---------------------------------------------------------------------------
-- live_ticks: latest market tick per symbol, upserted by the Service Bus
-- subscriber Azure Function (Finnhub streaming -> Service Bus -> Function).
-- One row per symbol keeps the newest tick (BR-02 idempotency).
-- ---------------------------------------------------------------------------
IF OBJECT_ID(N'dbo.live_ticks', N'U') IS NOT NULL DROP TABLE dbo.live_ticks;
GO

CREATE TABLE dbo.live_ticks (
    symbol            NVARCHAR(32)   NOT NULL,
    price             DECIMAL(18, 6) NULL,
    volume            DECIMAL(20, 4) NULL,
    trade_timestamp   BIGINT         NULL,    -- Finnhub epoch milliseconds
    received_at       DATETIME2(3)   NULL,
    updated_at        DATETIME2(3)   NOT NULL CONSTRAINT DF_lt_updated DEFAULT (SYSDATETIME()),
    CONSTRAINT PK_live_ticks PRIMARY KEY CLUSTERED (symbol)
);
GO

-- ---------------------------------------------------------------------------
-- Seed a few watched CIKs for the SEC EDGAR adapter (config-driven, but this
-- gives a default set: Apple, Microsoft, Tesla).
-- ---------------------------------------------------------------------------
IF OBJECT_ID(N'dbo.watched_ciks', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.watched_ciks (
        cik          BIGINT        NOT NULL,
        company_name NVARCHAR(255) NULL,
        CONSTRAINT PK_watched_ciks PRIMARY KEY CLUSTERED (cik)
    );

    INSERT INTO dbo.watched_ciks (cik, company_name) VALUES
        (320193,  N'Apple Inc.'),
        (789019,  N'Microsoft Corporation'),
        (1318605, N'Tesla, Inc.');
END
GO

PRINT N'Schema created: staging, company_revenue_yearly, live_ticks, watched_ciks.';
GO
