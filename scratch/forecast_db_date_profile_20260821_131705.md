# Forecast DB Date Profile

- Captured at: `2026-08-21T17:17:09.2623455Z`
- Server: `azprodfcast01.572f3811ca67.database.windows.net`
- Database: `Forecast`
- User context: `labreu@hannaandersson.com`

Date profiles are aggregate min/max checks only. Recent job rows come from the process log.

## Date Ranges

| Schema | Table | Column | Min | Max | Status |
| --- | --- | --- | --- | --- | --- |
| dbo | Channel_Offer_SKU_Forecast | CalendarDate | 1900-01-01 | 2029-01-21 | ok |
| dbo | Channel_Offer_SKU_Forecast_Archive | CalendarDate | 1900-01-01 | 2026-12-20 | ok |
| dbo | Channel_Offer_SKU_Forecast_Archive | Archive_Date | 2022-03-28 11:00:54.383000 | 2026-07-11 18:00:01.560000 | ok |
| dbo | Channel_Offer_Forecast | CalendarDate | 1900-01-01 | 2029-01-21 | ok |
| dbo | Channel_Offer_Forecast_Frozen | CalendarDate | 2020-01-12 | 2029-01-21 | ok |
| dbo | Channel_Offer_Forecast_Frozen | Frozen_Date | 2020-01-09 15:08:40.083000 | 2026-08-20 11:27:31.813000 | ok |
| dbo | Offer_SKU_Inventory_Forecast | CalendarDate | 2026-08-16 | 2028-02-13 | ok |
| dbo | Offer_SKU_Inventory_Forecast | Last_Updated_Date | 2026-08-20 20:09:53.300000 | 2026-08-20 22:45:42.147000 | ok |
| dbo | Offer_Inventory_Forecast | CalendarDate | 2019-09-29 | 2028-02-13 | ok |
| dbo | Offer_Inventory_Forecast_Frozen | Frozen_Date | 2020-01-09 15:08:58.840000 | 2026-08-20 11:27:32.230000 | ok |
| dbo | Channel_Offer_Demand_History | CalendarDate | 2025-07-13 | 2026-08-09 | ok |
| dbo | Channel_SKU_SIZE_Weekly_Demand_History | FISCALWEEKSTARTDATE | 2024-08-11 00:00:00 | 2026-08-16 00:00:00 | ok |
| dbo | Channel_Offer_SKU_Inventory_History | CalendarDate | 2026-02-15 | 2026-08-16 | ok |
| dbo | Inventory_History | AsOfDate | 2019-09-07 00:00:00 | 2026-08-01 00:00:00 | ok |
| dbo | Product_Dimensions_Hierarchy_Attributes | dbt_loaded_at | 2026-06-10 11:00:51.961000 | 2026-06-10 11:00:51.961000 | ok |
| dbo | Current_SKU_Available_DC_Inventory | LastUpdatedDate | 2026-08-21 06:01:04.780000 | 2026-08-21 06:01:04.780000 | ok |
| dbo | Current_Offer_Inventory | LastUpdatedDate | 2026-08-21 06:01:01.650000 | 2026-08-21 06:01:01.650000 | ok |
| dbo | On_Order | CalendarDate | 2018-02-25 | 2027-04-04 | ok |
| dbo | Forecast_Job_Log | Process_Date | 2019-08-11 19:00:05.917000 | 2026-08-21 10:00:01.803000 | ok |

## Recent Forecast Job Log

| Process Date | Description |
| --- | --- |
| 2026-08-21 10:00:01.803000 | PFS_028C: setting sku level post stop date values- PFS_028c_Create_Post_Stop_Date_Base_Forecast_For_Recently_Changed_OfferID |
| 2026-08-21 10:00:01.787000 | PFS_028C: re-setting sku level post stop date values- PFS_028c_Create_Post_Stop_Date_Base_Forecast_For_Recently_Changed_OfferID |
| 2026-08-21 10:00:01.780000 | PFS_028C:setting offer level post stop date to zero- PFS_028c_Create_Post_Stop_Date_Base_Forecast_For_Recently_Changed_OfferID |
| 2026-08-21 10:00:01.770000 | PFS_028C:resetting post stop date to zero- PFS_028c_Create_Post_Stop_Date_Base_Forecast_For_Recently_Changed_OfferID |
| 2026-08-21 10:00:01.750000 | PFS_028C:Starting PFS_028c_Create_Post_Stop_Date_Base_Forecast_For_Recently_Changed_OfferID |
| 2026-08-21 09:30:00.610000 | Complete - PFS_032c_AUTO_CALC_RECEIPT_PLAN_FOR_RECENTLY_CHANGED_OFFERIDS |
| 2026-08-21 09:30:00.607000 | Setting new planned receipts - sku level - PFS_032c_AUTO_CALC_RECEIPT_PLAN_FOR_RECENTLY_CHANGED_OFFERIDS |
| 2026-08-21 09:30:00.603000 | Resetting planned receipts to zero sku level- PFS_032c_AUTO_CALC_RECEIPT_PLAN_FOR_RECENTLY_CHANGED_OFFERIDS |
| 2026-08-21 09:30:00.597000 | Setting new planned receipts offerid level - PFS_032c_AUTO_CALC_RECEIPT_PLAN_FOR_RECENTLY_CHANGED_OFFERIDS |
| 2026-08-21 09:30:00.590000 | Resetting planned receipts to zero - PFS_032c_AUTO_CALC_RECEIPT_PLAN_FOR_RECENTLY_CHANGED_OFFERIDS |
| 2026-08-21 09:30:00.563000 | Starting - PFS_032c_AUTO_CALC_RECEIPT_PLAN_FOR_RECENTLY_CHANGED_OFFERIDS |
| 2026-08-21 09:00:01.677000 | PFS_028C: setting sku level post stop date values- PFS_028c_Create_Post_Stop_Date_Base_Forecast_For_Recently_Changed_OfferID |
| 2026-08-21 09:00:01.670000 | PFS_028C: re-setting sku level post stop date values- PFS_028c_Create_Post_Stop_Date_Base_Forecast_For_Recently_Changed_OfferID |
| 2026-08-21 09:00:01.667000 | PFS_028C:setting offer level post stop date to zero- PFS_028c_Create_Post_Stop_Date_Base_Forecast_For_Recently_Changed_OfferID |
| 2026-08-21 09:00:01.663000 | PFS_028C:resetting post stop date to zero- PFS_028c_Create_Post_Stop_Date_Base_Forecast_For_Recently_Changed_OfferID |
| 2026-08-21 09:00:01.640000 | PFS_028C:Starting PFS_028c_Create_Post_Stop_Date_Base_Forecast_For_Recently_Changed_OfferID |
| 2026-08-21 08:30:02.047000 | Complete - PFS_032c_AUTO_CALC_RECEIPT_PLAN_FOR_RECENTLY_CHANGED_OFFERIDS |
| 2026-08-21 08:30:01.923000 | Setting new planned receipts - sku level - PFS_032c_AUTO_CALC_RECEIPT_PLAN_FOR_RECENTLY_CHANGED_OFFERIDS |
| 2026-08-21 08:30:01.917000 | Resetting planned receipts to zero sku level- PFS_032c_AUTO_CALC_RECEIPT_PLAN_FOR_RECENTLY_CHANGED_OFFERIDS |
| 2026-08-21 08:30:01.713000 | Setting new planned receipts offerid level - PFS_032c_AUTO_CALC_RECEIPT_PLAN_FOR_RECENTLY_CHANGED_OFFERIDS |
| 2026-08-21 08:30:01.703000 | Resetting planned receipts to zero - PFS_032c_AUTO_CALC_RECEIPT_PLAN_FOR_RECENTLY_CHANGED_OFFERIDS |
| 2026-08-21 08:30:01.560000 | Starting - PFS_032c_AUTO_CALC_RECEIPT_PLAN_FOR_RECENTLY_CHANGED_OFFERIDS |
| 2026-08-21 08:00:01.910000 | PFS_028C: setting sku level post stop date values- PFS_028c_Create_Post_Stop_Date_Base_Forecast_For_Recently_Changed_OfferID |
| 2026-08-21 08:00:01.907000 | PFS_028C: re-setting sku level post stop date values- PFS_028c_Create_Post_Stop_Date_Base_Forecast_For_Recently_Changed_OfferID |
| 2026-08-21 08:00:01.903000 | PFS_028C:setting offer level post stop date to zero- PFS_028c_Create_Post_Stop_Date_Base_Forecast_For_Recently_Changed_OfferID |
