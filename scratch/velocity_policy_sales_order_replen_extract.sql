/*
Investigation-only extraction for velocity-policy calibration.

Purpose:
  Return only replenishment allocations used by sales-order demand work.
  Preserve the raw WHSREPLENWORKLINK grain so downstream analysis can derive:
    1. allocation facts: one row per replenishment-to-demand link;
    2. physical-touch facts: distinct ReplenWorkId + ReplenLineNum.

Quantity semantics:
  Keep the linked reserve-pick quantity, allocated demand quantity, and final
  put quantity separately. The final put quantity is the authoritative
  historical quantity moved into the forward-pick destination.

  Current work templates normally put that movement on line 5 because a Print
  step was introduced at line 2. Older templates had four lines. Select the
  last WORKTYPE = 2 line semantically instead of hard-coding a line number.

Validated against DAX_PROD and DAX_Archive on 2026-06-01.

Important index paths:
  DAX_PROD.dbo.WHSWORKTABLE
    I_102778STATUSCLOSEDDATECREATEDDATEIDX:
      PARTITION, DATAAREAID, WORKCLOSEDUTCDATETIME, CREATEDDATETIME, WORKSTATUS
    I_102778WORKIDX:
      PARTITION, DATAAREAID, WORKID

  WHSREPLENWORKLINK (PROD and archive)
    I_102706REPLENWORKLINEDEMANDWORKLINEIDX:
      PARTITION, DATAAREAID, REPLENWORKID, REPLENLINENUM,
      DEMANDWORKID, DEMANDLINENUM, WORKBUILDID

  WHSWORKLINE (PROD and archive)
    I_102773WORKIDLINENUMIDX:
      PARTITION, DATAAREAID, WORKID, LINENUM

The archive keeps the clustered work-ID access paths but does not keep all PROD
secondary indexes. Split the window at the archive boundary as the existing
12-month picking extractor does.
*/

SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

-- Preferred calibration horizon: three years for seasonal comparison.
DECLARE @StartUtc datetime2 = DATEADD(year, -3, CONVERT(date, GETUTCDATE()));
DECLARE @EndUtc datetime2 = DATEADD(day, 1, CONVERT(date, GETUTCDATE()));
DECLARE @ArchiveBoundaryUtc datetime2;

SELECT @ArchiveBoundaryUtc = CONVERT(date, MAX(wt.CREATEDDATETIME))
FROM DAX_Archive.arc.WHSWORKTABLE wt WITH (NOLOCK)
WHERE wt.[PARTITION] = 5637144576
  AND wt.DATAAREAID = 'ha';

WITH ReplenHeaders AS (
    SELECT
        'DAX_Archive' AS SourceDatabase,
        wt.[PARTITION],
        wt.DATAAREAID,
        wt.WORKID,
        wt.WORKBUILDID,
        wt.WORKTEMPLATECODE,
        wt.CREATEDDATETIME,
        wt.WORKCLOSEDUTCDATETIME
    FROM DAX_Archive.arc.WHSWORKTABLE wt WITH (NOLOCK)
    WHERE wt.[PARTITION] = 5637144576
      AND wt.DATAAREAID = 'ha'
      AND wt.CREATEDDATETIME >= @StartUtc
      AND wt.CREATEDDATETIME < @ArchiveBoundaryUtc
      AND wt.WORKSTATUS = 4
      AND wt.WORKTRANSTYPE = 11
      AND wt.INVENTLOCATIONID = '4010'

    UNION ALL

    SELECT
        'DAX_PROD',
        wt.[PARTITION],
        wt.DATAAREAID,
        wt.WORKID,
        wt.WORKBUILDID,
        wt.WORKTEMPLATECODE,
        wt.CREATEDDATETIME,
        wt.WORKCLOSEDUTCDATETIME
    FROM DAX_PROD.dbo.WHSWORKTABLE wt WITH (NOLOCK)
    WHERE wt.[PARTITION] = 5637144576
      AND wt.DATAAREAID = 'ha'
      AND wt.CREATEDDATETIME >= @ArchiveBoundaryUtc
      AND wt.CREATEDDATETIME < @EndUtc
      AND wt.WORKCLOSEDUTCDATETIME >= @ArchiveBoundaryUtc
      AND wt.WORKCLOSEDUTCDATETIME < DATEADD(day, 1, @EndUtc)
      AND wt.WORKSTATUS = 4
      AND wt.WORKTRANSTYPE = 11
      AND wt.INVENTLOCATIONID = '4010'
),
ArchiveLinks AS (
    SELECT
        rh.SourceDatabase,
        rh.WORKID AS ReplenWorkId,
        rh.WORKBUILDID AS WorkBuildId,
        rh.WORKTEMPLATECODE AS ReplenTemplate,
        rh.CREATEDDATETIME AS ReplenCreatedDateTimeUtc,
        rh.WORKCLOSEDUTCDATETIME AS ReplenClosedDateTimeUtc,
        link.REPLENLINENUM AS ReplenLineNum,
        link.DEMANDWORKID AS DemandWorkId,
        link.DEMANDLINENUM AS DemandLineNum,
        link.INVENTQTY AS AllocatedInventQty,
        demand.ORDERNUM AS SalesOrderNum,
        demand.CREATEDDATETIME AS DemandCreatedDateTimeUtc,
        source_line.ITEMID AS ItemId,
        dim.INVENTCOLORID AS ColorId,
        dim.INVENTSIZEID AS SizeId,
        source_line.WMSLOCATIONID AS SourceLocation,
        source_line.INVENTQTYWORK AS ReplenTouchInventQty,
        demand_line.WMSLOCATIONID AS DemandPickLocation,
        final_put.LINENUM AS FinalPutLineNum,
        final_put.WMSLOCATIONID AS FinalTargetLocation,
        final_put.INVENTQTYWORK AS FinalPutInventQty
    FROM ReplenHeaders rh
    INNER JOIN DAX_Archive.arc.WHSREPLENWORKLINK link WITH (NOLOCK)
        ON rh.SourceDatabase = 'DAX_Archive'
       AND link.[PARTITION] = rh.[PARTITION]
       AND link.DATAAREAID = rh.DATAAREAID
       AND link.REPLENWORKID = rh.WORKID
    INNER JOIN DAX_Archive.arc.WHSWORKTABLE demand WITH (NOLOCK)
        ON demand.[PARTITION] = link.[PARTITION]
       AND demand.DATAAREAID = link.DATAAREAID
       AND demand.WORKID = link.DEMANDWORKID
       AND demand.WORKTRANSTYPE = 2
    INNER JOIN DAX_Archive.arc.WHSWORKLINE source_line WITH (NOLOCK)
        ON source_line.[PARTITION] = link.[PARTITION]
       AND source_line.DATAAREAID = link.DATAAREAID
       AND source_line.WORKID = link.REPLENWORKID
       AND source_line.LINENUM = link.REPLENLINENUM
       AND source_line.WORKTYPE = 1
    INNER JOIN DAX_Archive.arc.WHSWORKLINE demand_line WITH (NOLOCK)
        ON demand_line.[PARTITION] = link.[PARTITION]
       AND demand_line.DATAAREAID = link.DATAAREAID
       AND demand_line.WORKID = link.DEMANDWORKID
       AND demand_line.LINENUM = link.DEMANDLINENUM
       AND demand_line.WORKTYPE = 1
    INNER JOIN DAX_Archive.arc.INVENTDIM dim WITH (NOLOCK)
        ON dim.[PARTITION] = source_line.[PARTITION]
       AND dim.DATAAREAID = source_line.DATAAREAID
       AND dim.INVENTDIMID = source_line.INVENTDIMID
    OUTER APPLY (
        SELECT TOP (1)
            put_line.LINENUM,
            put_line.WMSLOCATIONID,
            put_line.INVENTQTYWORK
        FROM DAX_Archive.arc.WHSWORKLINE put_line WITH (NOLOCK)
        WHERE put_line.[PARTITION] = link.[PARTITION]
          AND put_line.DATAAREAID = link.DATAAREAID
          AND put_line.WORKID = link.REPLENWORKID
          AND put_line.WORKTYPE = 2
        ORDER BY put_line.LINENUM DESC
    ) final_put
),
ProdLinks AS (
    SELECT
        rh.SourceDatabase,
        rh.WORKID AS ReplenWorkId,
        rh.WORKBUILDID AS WorkBuildId,
        rh.WORKTEMPLATECODE AS ReplenTemplate,
        rh.CREATEDDATETIME AS ReplenCreatedDateTimeUtc,
        rh.WORKCLOSEDUTCDATETIME AS ReplenClosedDateTimeUtc,
        link.REPLENLINENUM AS ReplenLineNum,
        link.DEMANDWORKID AS DemandWorkId,
        link.DEMANDLINENUM AS DemandLineNum,
        link.INVENTQTY AS AllocatedInventQty,
        demand.ORDERNUM AS SalesOrderNum,
        demand.CREATEDDATETIME AS DemandCreatedDateTimeUtc,
        source_line.ITEMID AS ItemId,
        dim.INVENTCOLORID AS ColorId,
        dim.INVENTSIZEID AS SizeId,
        source_line.WMSLOCATIONID AS SourceLocation,
        source_line.INVENTQTYWORK AS ReplenTouchInventQty,
        demand_line.WMSLOCATIONID AS DemandPickLocation,
        final_put.LINENUM AS FinalPutLineNum,
        final_put.WMSLOCATIONID AS FinalTargetLocation,
        final_put.INVENTQTYWORK AS FinalPutInventQty
    FROM ReplenHeaders rh
    INNER JOIN DAX_PROD.dbo.WHSREPLENWORKLINK link WITH (NOLOCK)
        ON rh.SourceDatabase = 'DAX_PROD'
       AND link.[PARTITION] = rh.[PARTITION]
       AND link.DATAAREAID = rh.DATAAREAID
       AND link.REPLENWORKID = rh.WORKID
    INNER JOIN DAX_PROD.dbo.WHSWORKTABLE demand WITH (NOLOCK)
        ON demand.[PARTITION] = link.[PARTITION]
       AND demand.DATAAREAID = link.DATAAREAID
       AND demand.WORKID = link.DEMANDWORKID
       AND demand.WORKTRANSTYPE = 2
    INNER JOIN DAX_PROD.dbo.WHSWORKLINE source_line WITH (NOLOCK)
        ON source_line.[PARTITION] = link.[PARTITION]
       AND source_line.DATAAREAID = link.DATAAREAID
       AND source_line.WORKID = link.REPLENWORKID
       AND source_line.LINENUM = link.REPLENLINENUM
       AND source_line.WORKTYPE = 1
    INNER JOIN DAX_PROD.dbo.WHSWORKLINE demand_line WITH (NOLOCK)
        ON demand_line.[PARTITION] = link.[PARTITION]
       AND demand_line.DATAAREAID = link.DATAAREAID
       AND demand_line.WORKID = link.DEMANDWORKID
       AND demand_line.LINENUM = link.DEMANDLINENUM
       AND demand_line.WORKTYPE = 1
    INNER JOIN DAX_PROD.dbo.INVENTDIM dim WITH (NOLOCK)
        ON dim.[PARTITION] = source_line.[PARTITION]
       AND dim.DATAAREAID = source_line.DATAAREAID
       AND dim.INVENTDIMID = source_line.INVENTDIMID
    OUTER APPLY (
        SELECT TOP (1)
            put_line.LINENUM,
            put_line.WMSLOCATIONID,
            put_line.INVENTQTYWORK
        FROM DAX_PROD.dbo.WHSWORKLINE put_line WITH (NOLOCK)
        WHERE put_line.[PARTITION] = link.[PARTITION]
          AND put_line.DATAAREAID = link.DATAAREAID
          AND put_line.WORKID = link.REPLENWORKID
          AND put_line.WORKTYPE = 2
        ORDER BY put_line.LINENUM DESC
    ) final_put
)
SELECT
    raw.SourceDatabase,
    raw.ReplenWorkId,
    raw.ReplenLineNum,
    CONCAT(raw.ReplenWorkId, '|', CONVERT(varchar(20), CONVERT(int, raw.ReplenLineNum))) AS TouchKey,
    CASE
        WHEN raw.ReplenTemplate IN ('Fwd Wave Demand', 'Fwd Rush Wave Demand') THEN 'Demand'
        WHEN raw.ReplenTemplate = 'Forward Replen' THEN 'MinMaxUsedBySalesOrder'
        WHEN raw.ReplenTemplate = 'Reset Replenishment' THEN 'ResetUsedBySalesOrder'
        ELSE 'OtherUsedBySalesOrder'
    END AS ReplenCategory,
    raw.ReplenTemplate,
    raw.WorkBuildId,
    raw.ReplenCreatedDateTimeUtc,
    raw.ReplenClosedDateTimeUtc,
    raw.DemandWorkId,
    raw.DemandLineNum,
    raw.SalesOrderNum,
    raw.DemandCreatedDateTimeUtc,
    raw.ItemId,
    raw.ColorId,
    raw.SizeId,
    CONCAT(raw.ItemId, '-', raw.ColorId, '-', raw.SizeId) AS SKU,
    raw.SourceLocation,
    raw.DemandPickLocation,
    raw.FinalPutLineNum,
    raw.FinalTargetLocation,
    raw.ReplenTouchInventQty,
    raw.FinalPutInventQty,
    raw.AllocatedInventQty
FROM (
    SELECT * FROM ArchiveLinks
    UNION ALL
    SELECT * FROM ProdLinks
) raw
ORDER BY raw.ReplenCreatedDateTimeUtc, raw.ReplenWorkId, raw.ReplenLineNum,
         raw.DemandWorkId, raw.DemandLineNum;
