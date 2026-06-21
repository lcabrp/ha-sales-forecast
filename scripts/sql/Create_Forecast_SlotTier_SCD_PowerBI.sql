/*
Optional shared SQL Server contract for Power BI.

The maintained local source is:
  Output/Monitoring/exports/forecast_slottier_scd_confirmed.csv

Load the confirmed CSV export into reporting.ForecastSlotTierSCD after each
AX-effective forecast update. Power BI should query the view, not the raw AX
HAFORECASTREPLENISHMENTTABLE when reporting historical replenishment accuracy.
*/

IF SCHEMA_ID('reporting') IS NULL
    EXEC('CREATE SCHEMA reporting');
GO

IF OBJECT_ID('reporting.ForecastSnapshotVersion', 'U') IS NULL
BEGIN
    CREATE TABLE reporting.ForecastSnapshotVersion (
        SnapshotId char(64) NOT NULL,
        SourceFile nvarchar(260) NOT NULL,
        SourcePath nvarchar(1000) NOT NULL,
        SourceSha256 char(64) NOT NULL,
        EffectiveFromEST datetimeoffset(0) NOT NULL,
        ObservedEffectiveFromEST datetimeoffset(0) NOT NULL,
        EffectiveAtSource varchar(40) NOT NULL,
        IsObservedLocalOutput bit NOT NULL,
        IsConfirmedAXUpload bit NOT NULL,
        ImportedAtUTC datetimeoffset(0) NOT NULL,
        RowsImported int NOT NULL,
        DistinctSKUs int NOT NULL,
        ActiveSKUCount int NOT NULL
            CONSTRAINT DF_ForecastSnapshotVersion_ActiveSKUCount DEFAULT (0),
        ReserveSKUCount int NOT NULL
            CONSTRAINT DF_ForecastSnapshotVersion_ReserveSKUCount DEFAULT (0),
        OffsiteSKUCount int NOT NULL
            CONSTRAINT DF_ForecastSnapshotVersion_OffsiteSKUCount DEFAULT (0),
        OtherPutawayIndicatorCount int NOT NULL
            CONSTRAINT DF_ForecastSnapshotVersion_OtherPutawayIndicatorCount DEFAULT (0),
        QualityWarning nvarchar(250) NOT NULL
            CONSTRAINT DF_ForecastSnapshotVersion_QualityWarning DEFAULT (N''),
        Notes nvarchar(1000) NOT NULL,
        CONSTRAINT PK_ForecastSnapshotVersion PRIMARY KEY (SnapshotId)
    );

    CREATE INDEX IX_ForecastSnapshotVersion_Effective
        ON reporting.ForecastSnapshotVersion (IsConfirmedAXUpload, EffectiveFromEST);
END;
GO

IF OBJECT_ID('reporting.ForecastSlotTierSCD', 'U') IS NULL
BEGIN
    CREATE TABLE reporting.ForecastSlotTierSCD (
        Timeline varchar(40) NOT NULL,
        SKU nvarchar(100) NOT NULL,
        ValidFromEST datetimeoffset(0) NOT NULL,
        ValidToEST datetimeoffset(0) NULL,
        IsCurrent bit NOT NULL,
        SnapshotId char(64) NOT NULL,
        SlotTier nvarchar(20) NOT NULL,
        ProductGroupCode nvarchar(10) NOT NULL,
        SizeGroupCode nvarchar(10) NOT NULL,
        Velocity nvarchar(4) NOT NULL,
        ChangeType varchar(20) NOT NULL,
        CONSTRAINT PK_ForecastSlotTierSCD
            PRIMARY KEY (Timeline, SKU, ValidFromEST)
    );

    CREATE INDEX IX_ForecastSlotTierSCD_AsOf
        ON reporting.ForecastSlotTierSCD (Timeline, SKU, ValidFromEST, ValidToEST);
END;
GO

CREATE OR ALTER VIEW reporting.vw_ForecastSlotTierAsOf
AS
SELECT
    SKU,
    ValidFromEST,
    ValidToEST,
    IsCurrent,
    SnapshotId,
    SlotTier,
    ProductGroupCode,
    SizeGroupCode,
    Velocity,
    ChangeType
FROM reporting.ForecastSlotTierSCD
WHERE Timeline = 'confirmed_ax_upload';
GO
