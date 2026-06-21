# Forecast DB Date Profile

- Captured at: `2026-06-17T21:19:40.6873410Z`
- Server: `azprodfcast01.572f3811ca67.database.windows.net`
- Database: `Forecast`
- User context: `labreu@hannaandersson.com`

Date profiles are aggregate min/max checks only. Recent job rows come from the process log.

## Date Ranges

| Schema | Table | Column | Min | Max | Status |
| --- | --- | --- | --- | --- | --- |
| dbo | Channel_Offer_SKU_Forecast | CalendarDate | 1900-01-01 | 2028-12-24 | ok |
| dbo | Channel_Offer_SKU_Forecast_Archive | CalendarDate | 1900-01-01 | 2026-09-27 | ok |
| dbo | Channel_Offer_SKU_Forecast_Archive | Archive_Date | 2022-03-28 11:00:54.383000 | 2026-04-18 18:00:00.990000 | ok |
| dbo | Channel_Offer_Forecast | CalendarDate | 1900-01-01 | 2028-12-24 | ok |
| dbo | Channel_Offer_Forecast_Frozen | CalendarDate | 2020-01-12 | 2028-12-24 | ok |
| dbo | Channel_Offer_Forecast_Frozen | Frozen_Date | 2020-01-09 15:08:40.083000 | 2026-06-11 12:02:27.910000 | ok |
| dbo | Offer_SKU_Inventory_Forecast | CalendarDate | 2026-06-14 | 2027-12-12 | ok |
| dbo | Offer_SKU_Inventory_Forecast | Last_Updated_Date | 2026-06-16 20:09:26.567000 | 2026-06-17 13:30:20.320000 | ok |
| dbo | Offer_Inventory_Forecast | CalendarDate | 2019-09-29 | 2027-12-12 | ok |
| dbo | Offer_Inventory_Forecast_Frozen | Frozen_Date | 2020-01-09 15:08:58.840000 | 2026-06-11 12:02:28.340000 | ok |
| dbo | Channel_Offer_Demand_History | CalendarDate | 2025-05-11 | 2026-06-07 | ok |
| dbo | Channel_SKU_SIZE_Weekly_Demand_History | FISCALWEEKSTARTDATE | 2024-06-09 00:00:00 | 2026-06-14 00:00:00 | ok |
| dbo | Product_Dimensions_Hierarchy_Attributes | dbt_loaded_at | 2026-06-10 11:00:51.961000 | 2026-06-10 11:00:51.961000 | ok |
| dbo | Current_SKU_Available_DC_Inventory | CalendarDate |  |  | error: ('42S22', "[42S22] [Microsoft][ODBC Driver 18 for SQL Server][SQL Server]Invalid column name 'CalendarDate'. (207) (SQLExecDirectW); [42S22] [Microsoft][ODBC Driver 18 for SQL Server][SQL Server]Invalid column name 'CalendarDate'. (207)") |
| dbo | Current_Offer_Inventory | CalendarDate |  |  | error: ('42S22', "[42S22] [Microsoft][ODBC Driver 18 for SQL Server][SQL Server]Invalid column name 'CalendarDate'. (207) (SQLExecDirectW); [42S22] [Microsoft][ODBC Driver 18 for SQL Server][SQL Server]Invalid column name 'CalendarDate'. (207)") |
| dbo | On_Order | CalendarDate | 2018-02-25 | 2027-01-17 | ok |
| dbo | Forecast_Job_Log | Process_Date | 2019-08-11 19:00:05.917000 | 2026-06-17 14:00:02.273000 | ok |

## Recent Forecast Job Log

| Process Date | Description |
| --- | --- |
| 2026-06-17 14:00:02.273000 | PFS_028C: setting sku level post stop date values- PFS_028c_Create_Post_Stop_Date_Base_Forecast_For_Recently_Changed_OfferID |
| 2026-06-17 14:00:02.240000 | PFS_028C: re-setting sku level post stop date values- PFS_028c_Create_Post_Stop_Date_Base_Forecast_For_Recently_Changed_OfferID |
| 2026-06-17 14:00:02.210000 | PFS_028C:setting offer level post stop date to zero- PFS_028c_Create_Post_Stop_Date_Base_Forecast_For_Recently_Changed_OfferID |
| 2026-06-17 14:00:02.193000 | PFS_028C:resetting post stop date to zero- PFS_028c_Create_Post_Stop_Date_Base_Forecast_For_Recently_Changed_OfferID |
| 2026-06-17 14:00:02.173000 | PFS_028C:Starting PFS_028c_Create_Post_Stop_Date_Base_Forecast_For_Recently_Changed_OfferID |
| 2026-06-17 13:30:14.500000 | Complete - PFS_032c_AUTO_CALC_RECEIPT_PLAN_FOR_RECENTLY_CHANGED_OFFERIDS |
| 2026-06-17 13:30:11.830000 | Setting new planned receipts - sku level - PFS_032c_AUTO_CALC_RECEIPT_PLAN_FOR_RECENTLY_CHANGED_OFFERIDS |
| 2026-06-17 13:30:11.557000 | Resetting planned receipts to zero sku level- PFS_032c_AUTO_CALC_RECEIPT_PLAN_FOR_RECENTLY_CHANGED_OFFERIDS |
| 2026-06-17 13:30:03.373000 | Setting new planned receipts offerid level - PFS_032c_AUTO_CALC_RECEIPT_PLAN_FOR_RECENTLY_CHANGED_OFFERIDS |
| 2026-06-17 13:30:02.733000 | Resetting planned receipts to zero - PFS_032c_AUTO_CALC_RECEIPT_PLAN_FOR_RECENTLY_CHANGED_OFFERIDS |
| 2026-06-17 13:30:01.347000 | Starting - PFS_032c_AUTO_CALC_RECEIPT_PLAN_FOR_RECENTLY_CHANGED_OFFERIDS |
| 2026-06-17 13:02:22.403000 | Procedure :PFS_009b1_Update_Kubix_Attributes_Table: Process Ended: 00:00:17. |
| 2026-06-17 13:02:22.400000 | Procedure :PFS_009b_Update_Kubix_Attributes_Table: Rows Loaded=17141 |
| 2026-06-17 13:02:05.220000 | Procedure :PFS_09b_Update_Kubix_Attributes_Table: Start |
| 2026-06-17 13:02:05.167000 | Procedure :PFS_009c_Update_Product_Dimensions_Hierarchy_Attributes_Table: Process Ended: 00:05:57. |
| 2026-06-17 13:02:05.163000 | Procedure :PFS_009c_Update_Product_Dimensions_Hierarchy_Attributes_Table: Rows Loaded=477206 |
| 2026-06-17 13:00:05.133000 | PFS_028C: setting sku level post stop date values- PFS_028c_Create_Post_Stop_Date_Base_Forecast_For_Recently_Changed_OfferID |
| 2026-06-17 13:00:04.730000 | PFS_028C: re-setting sku level post stop date values- PFS_028c_Create_Post_Stop_Date_Base_Forecast_For_Recently_Changed_OfferID |
| 2026-06-17 13:00:04.413000 | PFS_028C:setting offer level post stop date to zero- PFS_028c_Create_Post_Stop_Date_Base_Forecast_For_Recently_Changed_OfferID |
| 2026-06-17 13:00:04.330000 | PFS_028C:resetting post stop date to zero- PFS_028c_Create_Post_Stop_Date_Base_Forecast_For_Recently_Changed_OfferID |
| 2026-06-17 13:00:04.307000 | PFS_028C:Starting PFS_028c_Create_Post_Stop_Date_Base_Forecast_For_Recently_Changed_OfferID |
| 2026-06-17 12:56:08.977000 | Procedure :PFS_009c_Update_Product_Dimensions_Hierarchy_Attributes_Table: Start |
| 2026-06-17 12:38:16.403000 | Procedure :PFS_009c_Update_Product_Dimensions_Hierarchy_Attributes_Table: Process Ended: 00:05:56. |
| 2026-06-17 12:38:16.400000 | Procedure :PFS_009c_Update_Product_Dimensions_Hierarchy_Attributes_Table: Rows Loaded=477206 |
| 2026-06-17 12:32:20.587000 | Procedure :PFS_009c_Update_Product_Dimensions_Hierarchy_Attributes_Table: Start |
