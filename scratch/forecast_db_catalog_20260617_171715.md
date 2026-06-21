# Forecast DB Catalog

- Captured at: `2026-06-17T21:17:32.3579973Z`
- Server: `azprodfcast01.572f3811ca67.database.windows.net`
- Database: `Forecast`
- User context: `labreu@hannaandersson.com`
- User tables: `92`
- User columns: `1,256`
- Approximate user-table rows from metadata: `97,124,960`

This catalog uses SQL Server metadata only. Row counts come from
`sys.partitions`, not `COUNT(*)`, and no business rows are exported.

## Output Files

- tables: `scratch\forecast_db_catalog_20260617_171715_tables.csv`
- columns: `scratch\forecast_db_catalog_20260617_171715_columns.csv`
- indexes: `scratch\forecast_db_catalog_20260617_171715_indexes.csv`
- foreign keys: `scratch\forecast_db_catalog_20260617_171715_foreign_keys.csv`

## User Tables

| Schema | Table | Rows | Columns | Created | Modified | FK Out | FK In |
| --- | --- | --- | --- | --- | --- | --- | --- |
| dbo | Channel_Offer_SKU_Forecast_Archive | 24,635,292 | 22 | 2022-03-28 09:54:15.873000 | 2022-03-28 09:54:15.873000 | 0 | 0 |
| dbo | channel_offer_sku_store_forecast_test | 12,969,248 | 8 | 2020-01-07 14:35:31.713000 | 2020-01-07 14:35:31.713000 | 0 | 0 |
| dbo | Channel_Offer_SKU_Forecast | 11,538,283 | 21 | 2019-08-20 15:55:57.303000 | 2025-03-25 03:39:01.563000 | 0 | 0 |
| dbo | Store_Distribution_Sales | 6,997,430 | 6 | 2019-09-19 05:56:48.037000 | 2019-09-19 05:56:53.837000 | 0 | 0 |
| dbo | Offer_Inventory_Forecast | 5,163,971 | 14 | 2019-12-09 06:02:23.997000 | 2019-12-18 04:10:12.030000 | 0 | 0 |
| dbo | Offer_Inventory_Forecast_backup_20250324 | 4,209,435 | 14 | 2025-03-24 15:45:15.200000 | 2025-03-24 15:45:15.200000 | 0 | 0 |
| dbo | Offer_SKU_Inventory_Forecast | 4,186,131 | 15 | 2019-12-09 06:07:10.200000 | 2026-06-16 22:32:03.180000 | 0 | 0 |
| dbo | Offer_Inventory_Forecast_Frozen | 4,121,706 | 13 | 2019-10-25 12:36:25.650000 | 2026-03-28 09:39:35.943000 | 0 | 0 |
| dbo | sku_hist | 3,675,846 | 7 | 2020-05-01 11:28:13.113000 | 2020-05-01 11:28:13.113000 | 0 | 0 |
| dbo | Offer_SKU_Inventory_Forecast_backup | 3,642,216 | 15 | 2025-03-24 15:47:26.893000 | 2025-03-24 15:47:26.893000 | 0 | 0 |
| dbo | Channel_Offer_Forecast_Frozen | 3,451,008 | 22 | 2019-10-25 12:40:20.577000 | 2026-03-28 09:39:14.347000 | 0 | 0 |
| dbo | Channel_Offer_Forecast_Archive | 2,736,183 | 21 | 2022-03-28 09:22:31.550000 | 2022-03-28 09:38:41.807000 | 0 | 0 |
| dbo | Channel_Offer_Forecast | 1,568,932 | 20 | 2019-08-26 15:03:48.263000 | 2019-10-14 13:11:34.657000 | 0 | 0 |
| dbo | Channel_SKU_SIZE_Weekly_Demand_History | 1,367,399 | 11 | 2020-02-10 06:50:58.130000 | 2020-02-10 06:50:58.130000 | 0 | 0 |
| dbo | Channel_Offer_Demand_History_BACKUP | 1,238,149 | 18 | 2019-10-04 13:29:56.130000 | 2019-10-04 13:29:56.130000 | 0 | 0 |
| dbo | Promo_Rank_Calculations | 856,700 | 17 | 2022-03-21 15:35:00.597000 | 2022-03-21 15:35:05.490000 | 0 | 0 |
| dbo | Channel_Offer_Demand_History | 798,916 | 18 | 2019-08-20 09:44:18.997000 | 2019-09-11 13:40:48.187000 | 0 | 0 |
| dbo | Allocation_Minimums | 745,037 | 5 | 2019-08-13 13:45:56.513000 | 2019-09-11 13:37:38.723000 | 0 | 0 |
| dbo | channel_offer_sku_store_control_test | 611,674 | 6 | 2020-01-07 14:39:59.700000 | 2020-01-07 14:39:59.700000 | 0 | 0 |
| dbo | Product_Dimensions_Hierarchy_Attributes | 477,206 | 45 | 2026-06-17 11:50:37.640000 | 2026-06-17 11:50:37.817000 | 0 | 0 |
| dbo | Channel_Offer_SKU_Inventory_History | 448,102 | 5 | 2020-01-16 11:46:02.600000 | 2020-01-16 11:46:03.743000 | 0 | 0 |
| dbo | Store_Distribution_LIbrary | 224,117 | 10 | 2019-09-19 06:01:18.103000 | 2019-09-19 06:01:18.243000 | 0 | 0 |
| dbo | Forecast_Job_Log | 221,032 | 2 | 2021-12-14 19:54:26.097000 | 2021-12-14 19:56:58.260000 | 0 | 0 |
| dbo | AutoLoad_Store_Groups | 217,693 | 11 | 2019-09-28 03:18:59.463000 | 2019-09-28 03:18:59.463000 | 0 | 0 |
| dbo | Seasonal_Profile_Index_Library | 169,100 | 3 | 2019-07-25 12:37:19.947000 | 2019-07-25 12:37:19.967000 | 0 | 0 |
| dbo | Channel_Offer_SKU_Control | 112,212 | 5 | 2019-08-20 11:44:38.793000 | 2019-08-20 12:04:53.817000 | 0 | 0 |
| dbo | Offers | 93,435 | 21 | 2019-09-28 04:19:04.997000 | 2022-05-26 10:07:02.140000 | 0 | 0 |
| dbo | Offers_Backup | 93,435 | 21 | 2020-06-05 13:42:15.760000 | 2020-06-05 13:42:15.760000 | 0 | 0 |
| dbo | Size_Distribution_Sales | 55,671 | 10 | 2019-09-19 08:25:57.443000 | 2019-09-19 10:59:03.853000 | 0 | 0 |
| dbo | Offer_Control_Table | 41,531 | 59 | 2019-09-18 07:50:27.370000 | 2022-08-04 08:56:23.637000 | 0 | 0 |
| dbo | Size_Distribution_Library | 36,770 | 17 | 2020-03-17 06:01:30.873000 | 2020-03-17 06:01:31.760000 | 0 | 0 |
| dbo | Current_SKU_Available_DC_Inventory_FirstOfWeek | 34,713 | 6 | 2019-10-18 06:03:56.450000 | 2019-10-18 06:03:56.450000 | 0 | 0 |
| dbo | Current_SKU_Available_DC_Inventory | 34,388 | 6 | 2019-10-18 05:59:42.120000 | 2026-06-17 06:01:22.643000 | 0 | 0 |
| dbo | Current_SKU_Available_DC_Inventory_backup | 32,164 | 3 | 2019-10-04 14:32:12.410000 | 2019-10-04 14:32:12.410000 | 0 | 0 |
| dbo | Seasonal_Profile_Sales_old | 30,234 | 59 | 2019-09-18 10:18:31.810000 | 2019-09-20 08:32:21.623000 | 0 | 0 |
| dbo | on_order_backup | 29,262 | 10 | 2019-10-04 15:06:03.657000 | 2019-10-04 15:06:03.657000 | 0 | 0 |
| dbo | Size_Distribution_Library_Backup_20200317 | 26,786 | 15 | 2020-03-17 05:59:34.897000 | 2020-03-17 05:59:34.897000 | 0 | 0 |
| dbo | Seasonal_Profile_Sales | 23,844 | 61 | 2019-09-20 07:40:26.823000 | 2019-09-20 08:32:33.747000 | 0 | 0 |
| dbo | On_Order | 23,413 | 11 | 2019-12-09 05:48:24.110000 | 2019-12-09 05:48:25.037000 | 0 | 0 |
| dbo | AutoLoad_Size_Distribution_Library | 22,381 | 14 | 2019-09-12 12:33:45.640000 | 2019-09-12 13:40:27.547000 | 0 | 0 |
| dbo | Inventory_History | 22,147 | 7 | 2019-07-31 08:05:26.707000 | 2019-07-31 08:05:51.110000 | 0 | 0 |
| dbo | offer_control_table_backup | 19,914 | 57 | 2022-03-22 12:05:23.350000 | 2022-03-22 12:05:23.350000 | 0 | 0 |
| dbo | Kubix_Attributes | 17,141 | 33 | 2024-11-17 06:45:36.503000 | 2024-11-17 06:45:36.503000 | 0 | 0 |
| dbo | Current_Offer_Inventory_backup | 13,767 | 6 | 2019-10-04 14:08:45.897000 | 2019-10-04 14:08:45.897000 | 0 | 0 |
| dbo | Promo_Model_Detail | 12,696 | 3 | 2019-08-19 08:28:35.930000 | 2026-03-29 10:45:07.440000 | 0 | 0 |
| dbo | offer_control_table_backup_12142019 | 11,460 | 51 | 2019-12-14 04:47:35.310000 | 2019-12-14 04:47:35.310000 | 0 | 0 |
| dbo | Current_Offer_Inventory_FirstOfWeek | 7,699 | 8 | 2019-10-18 06:03:01.970000 | 2019-10-18 06:03:01.970000 | 0 | 0 |
| dbo | Current_Offer_Inventory | 7,669 | 8 | 2019-10-18 05:58:49.380000 | 2026-06-17 06:01:14.287000 | 0 | 0 |
| dbo | Calendar_Lookup | 6,937 | 8 | 2019-07-29 13:41:07.833000 | 2019-08-11 19:53:41.227000 | 0 | 0 |
| dbo | SalesDatabyMonth | 6,544 | 9 | 2019-07-31 07:16:17.230000 | 2019-07-31 07:16:17.323000 | 0 | 0 |
| dbo | Size_Distribution_Sales_old | 5,244 | 8 | 2019-09-18 12:29:58.913000 | 2019-09-19 10:58:51.220000 | 0 | 0 |
| dbo | Size_Range_Groups | 4,918 | 5 | 2019-07-25 11:24:50.653000 | 2019-07-25 11:24:50.747000 | 0 | 0 |
| dbo | Receipt_History_Backup | 4,068 | 8 | 2019-10-16 15:38:14.573000 | 2019-10-16 15:38:14.573000 | 0 | 0 |
| dbo | Promo_Model_Headers | 4,060 | 12 | 2019-08-19 08:26:47.927000 | 2026-03-29 10:44:38.427000 | 0 | 0 |
| dbo | Autoload_offer_apw_2 | 3,751 | 6 | 2019-09-28 18:31:14.780000 | 2019-09-28 18:31:14.780000 | 0 | 0 |
| dbo | Receipt_History_Sept2019 | 3,679 | 8 | 2019-10-16 15:53:18.517000 | 2019-10-16 15:53:18.517000 | 0 | 0 |
| dbo | Seasonal_Profile_Library | 3,194 | 12 | 2019-09-18 11:00:37.993000 | 2019-09-18 11:00:38.083000 | 0 | 0 |
| dbo | OfferID_to_reset_planned_receipts | 1,583 | 2 | 2019-11-06 09:15:19.730000 | 2019-11-06 09:15:19.730000 | 0 | 0 |
| dbo | offerid_to_reset_planned_receipts_backup | 1,583 | 2 | 2019-11-06 09:20:04.073000 | 2019-11-06 09:20:04.073000 | 0 | 0 |
| dbo | Allocation_Day_Of_Week_Contributions | 1,561 | 4 | 2019-08-19 14:46:44.643000 | 2019-08-19 14:46:44.660000 | 0 | 0 |
| dbo | Offer_SKU_Inventory_Forecast_Working | 1,264 | 15 | 2019-12-10 09:38:58.080000 | 2019-12-10 09:38:58.663000 | 0 | 0 |
| dbo | Receipt_History | 1,149 | 8 | 2019-07-31 15:11:18.177000 | 2019-07-31 15:11:18.177000 | 0 | 0 |
| dbo | Monthly_On_Order | 519 | 10 | 2019-09-09 12:37:32.863000 | 2019-09-09 12:42:34.640000 | 0 | 0 |
| dbo | Amazon_Launch | 343 | 8 | 2019-07-24 16:46:19.567000 | 2019-07-24 16:46:19.567000 | 0 | 0 |
| dbo | Store_Master_Groups_Control | 329 | 2 | 2019-08-21 07:23:54.560000 | 2019-08-21 07:23:54.577000 | 0 | 0 |
| dbo | No_Size_Exception_Offers | 194 | 1 | 2019-12-18 15:36:10.330000 | 2019-12-18 15:37:16.913000 | 0 | 0 |
| dbo | Offer_Inventory_Forecast_Working | 158 | 12 | 2019-12-10 09:58:41.297000 | 2019-12-10 09:58:47.343000 | 0 | 0 |
| dbo | Allocation_Parameter_Control_Table | 104 | 13 | 2019-08-12 08:44:54.653000 | 2019-08-12 09:45:47.550000 | 0 | 0 |
| dbo | OTB_Month_Plans | 85 | 25 | 2019-07-31 13:31:59.797000 | 2019-07-31 13:31:59.867000 | 0 | 0 |
| dbo | Store_Master_Control | 71 | 9 | 2019-08-21 07:22:02.527000 | 2019-08-21 07:51:48.853000 | 0 | 0 |
| dbo | Promo_Model_Headers_Additional_Waves | 38 | 10 | 2026-05-29 14:07:49.360000 | 2026-05-29 14:07:49.840000 | 0 | 0 |
| dbo | Markdown_Model_Pricing_Detail | 13 | 4 | 2019-09-16 14:49:02.337000 | 2019-09-16 14:49:02.337000 | 0 | 0 |
| dbo | Allocation_Progression_Adjustment_Control_Table | 8 | 6 | 2019-08-16 08:27:38.773000 | 2019-08-16 08:27:39.103000 | 0 | 0 |
| dbo | Markdown_Model_Product_Detail | 8 | 3 | 2019-09-16 14:46:56.517000 | 2019-09-16 14:46:56.517000 | 0 | 0 |
| dbo | offer_rounded_forecast_staging | 8 | 13 | 2019-09-24 18:28:48.793000 | 2019-09-24 18:28:48.793000 | 0 | 0 |
| dbo | OTB_Plan_Parameters | 4 | 80 | 2019-07-31 07:15:50.840000 | 2019-07-31 07:15:50.923000 | 0 | 0 |
| dbo | Markdown_Model_Headers | 3 | 7 | 2019-09-16 14:45:30.070000 | 2019-09-16 14:45:30.070000 | 0 | 0 |
| dbo | Allocation_System_Parameters | 1 | 4 | 2019-07-24 16:13:14.613000 | 2019-07-24 16:13:14.630000 | 0 | 0 |
| dbo | PFS_System_Parameters | 1 | 1 | 2019-08-15 08:03:15.930000 | 2019-08-15 08:03:16.177000 | 0 | 0 |
| dbo | AutoLoad_Offer_APW | 0 | 6 | 2019-09-12 13:40:11.057000 | 2019-09-12 13:40:11.057000 | 0 | 0 |
| dbo | autoload_offer_average_apw | 0 | 9 | 2019-09-28 17:59:20.813000 | 2019-09-28 17:59:20.813000 | 0 | 0 |
| dbo | Calculated_Immediate_Need_to_Cut_not_Available | 0 | 10 | 2019-08-19 18:27:32.727000 | 2019-08-19 18:27:33.007000 | 0 | 0 |
| dbo | Calculated_Immediate_Order_Need | 0 | 4 | 2019-08-16 14:58:28.857000 | 2019-08-16 14:58:28.940000 | 0 | 0 |
| dbo | Channel_Offer_Forecast_Backup | 0 | 5 | 2019-08-10 06:58:39.430000 | 2019-08-10 06:58:39.430000 | 0 | 0 |
| dbo | Channel_Offer_SKU_Store_Control | 0 | 6 | 2019-08-20 11:45:30.600000 | 2019-08-20 12:18:20.450000 | 0 | 0 |
| dbo | Channel_Offer_SKU_Store_Forecast | 0 | 8 | 2019-08-05 16:08:04.437000 | 2019-11-18 13:26:36.033000 | 0 | 0 |
| dbo | Current_Store_Available_Inventory | 0 | 5 | 2019-08-14 17:01:41.483000 | 2019-08-15 07:18:31.423000 | 0 | 0 |
| dbo | Current_Store_Available_Inventory_Adjustments | 0 | 6 | 2019-09-12 09:54:01.877000 | 2019-09-12 09:54:01.877000 | 0 | 0 |
| dbo | DB_Errors | 0 | 9 | 2019-09-12 16:41:56.490000 | 2019-09-12 16:41:56.490000 | 0 | 0 |
| dbo | OfferID_Xref_Control | 0 | 2 | 2019-10-04 11:48:35.450000 | 2019-10-04 11:48:35.533000 | 0 | 0 |
| dbo | Sku_Store_Allocation_Control | 0 | 12 | 2019-08-14 14:51:14.817000 | 2019-08-16 13:43:41.510000 | 0 | 0 |
| dbo | SKU_Store_Demand_History | 0 | 19 | 2019-08-20 12:31:42.900000 | 2019-08-20 12:31:42.980000 | 0 | 0 |

## Forecast-Relevant Candidates

Candidate ranking is a keyword scan across table and column names. It is a starting point for review, not proof of business meaning.

| Schema | Table | Rows | Columns | Keyword Matches |
| --- | --- | --- | --- | --- |
| dbo | Offer_Control_Table | 41,531 | 59 | allocation, channel, color, forecast, inventory, item, order, plan, receipt, season, size, week |
| dbo | offer_control_table_backup | 19,914 | 57 | allocation, channel, color, forecast, inventory, item, order, plan, receipt, season, size, week |
| dbo | offer_control_table_backup_12142019 | 11,460 | 51 | allocation, channel, color, forecast, inventory, item, order, plan, receipt, season, size, week |
| dbo | Channel_Offer_SKU_Forecast_Archive | 24,635,292 | 22 | channel, forecast, inventory, plan, sales, sku, week |
| dbo | Channel_Offer_SKU_Forecast | 11,538,283 | 21 | channel, forecast, inventory, plan, sales, sku, week |
| dbo | Channel_SKU_SIZE_Weekly_Demand_History | 1,367,399 | 11 | channel, demand, item, sales, size, sku, week |
| dbo | Offer_SKU_Inventory_Forecast | 4,186,131 | 15 | forecast, inventory, order, plan, receipt, sku |
| dbo | Offer_SKU_Inventory_Forecast_backup | 3,642,216 | 15 | forecast, inventory, order, plan, receipt, sku |
| dbo | Channel_Offer_Forecast_Frozen | 3,451,008 | 22 | channel, forecast, inventory, plan, sales, week |
| dbo | Channel_Offer_Forecast_Archive | 2,736,183 | 21 | channel, forecast, inventory, plan, sales, week |
| dbo | Channel_Offer_Forecast | 1,568,932 | 20 | channel, forecast, inventory, plan, sales, week |
| dbo | Product_Dimensions_Hierarchy_Attributes | 477,206 | 45 | color, item, season, size, sku, style |
| dbo | Seasonal_Profile_Sales | 23,844 | 61 | channel, order, sales, season, style, week |
| dbo | Offer_SKU_Inventory_Forecast_Working | 1,264 | 15 | forecast, inventory, order, plan, receipt, sku |
| dbo | channel_offer_sku_store_forecast_test | 12,969,248 | 8 | channel, forecast, sales, sku, week |
| dbo | Offer_Inventory_Forecast | 5,163,971 | 14 | forecast, inventory, order, plan, receipt |
| dbo | Offer_Inventory_Forecast_backup_20250324 | 4,209,435 | 14 | forecast, inventory, order, plan, receipt |
| dbo | Offer_Inventory_Forecast_Frozen | 4,121,706 | 13 | forecast, inventory, order, plan, receipt |
| dbo | Channel_Offer_Demand_History_BACKUP | 1,238,149 | 18 | channel, demand, order, sku, week |
| dbo | Channel_Offer_Demand_History | 798,916 | 18 | channel, demand, order, sku, week |
| dbo | Offers_Backup | 93,435 | 21 | color, item, sales, season, size |
| dbo | Offers | 93,435 | 21 | color, item, sales, season, size |
| dbo | Size_Distribution_Sales | 55,671 | 10 | channel, sales, size, style, week |
| dbo | Seasonal_Profile_Sales_old | 30,234 | 59 | channel, order, sales, season, week |
| dbo | SalesDatabyMonth | 6,544 | 9 | channel, demand, plan, sales, season |
| dbo | Offer_Inventory_Forecast_Working | 158 | 12 | forecast, inventory, order, plan, receipt |
| dbo | OTB_Month_Plans | 85 | 25 | channel, plan, receipt, sales, season |
| dbo | SKU_Store_Demand_History | 0 | 19 | channel, demand, order, sku, week |
| dbo | Channel_Offer_SKU_Store_Forecast | 0 | 8 | channel, forecast, sales, sku, week |
| dbo | On_Order | 23,413 | 11 | channel, order, sku, week |

## Columns By Table

### dbo.Allocation_Day_Of_Week_Contributions

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Division | varchar(50) | Y |  |  |
| 2 | Department | varchar(50) | Y |  |  |
| 3 | DayOfWeek | int | Y |  |  |
| 4 | DayOfWeek_Percent_Contribution | decimal(18,4) | Y |  |  |

### dbo.Allocation_Minimums

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Channel | varchar(50) | Y |  |  |
| 2 | OfferID | varchar(50) | Y |  |  |
| 3 | Min_Type | varchar(50) | Y |  |  |
| 4 | SizeID | varchar(50) | Y |  |  |
| 5 | Min_Value | float | Y |  |  |

### dbo.Allocation_Parameter_Control_Table

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Parameter_Level | varchar(50) | N |  |  |
| 2 | Parameter_Value | varchar(50) | N |  |  |
| 3 | Initial_Allocation_WOS | varchar(50) | Y |  |  |
| 4 | Allocation_Max_WOS | varchar(50) | Y |  |  |
| 5 | Allocation_Min_Qty | varchar(50) | Y |  |  |
| 6 | Allocation_Max_Limit_Qty | varchar(50) | Y |  |  |
| 7 | Allocation_Avail_Inventory_Threshold | varchar(50) | Y |  |  |
| 8 | Allocaiton_Cutoff_Threshold | varchar(50) | Y |  |  |
| 9 | Allocation_Start_Date | date | Y |  |  |
| 10 | Allocation_Stop_Date | date | Y |  |  |
| 11 | Last Updated Date | datetime | Y |  |  |
| 12 | Last Update User | nvarchar(50) | Y |  |  |
| 13 | Status | nvarchar(50) | Y |  |  |

### dbo.Allocation_Progression_Adjustment_Control_Table

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Channel | nvarchar(50) | Y |  |  |
| 2 | Division | nvarchar(50) | Y |  |  |
| 3 | Department | nvarchar(50) | Y |  |  |
| 4 | Intended_Percent_Complete_Start | float | Y |  |  |
| 5 | Intended_Percent_Complete_End | float | Y |  |  |
| 6 | Forecast_Adjustment_FActor | float | Y |  |  |

### dbo.Allocation_System_Parameters

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Allocation_CostOfMoney | float | Y |  |  |
| 2 | Allocation_Process_Active_Flag | nchar(10) | Y |  |  |
| 3 | Allocation_Max_WOS | float | Y |  |  |
| 4 | Allocation_Min_Store_Qty | float | Y |  |  |

### dbo.Amazon_Launch

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Style | nvarchar(50) | Y |  |  |
| 2 | CC | nvarchar(50) | Y |  |  |
| 3 | Division | nvarchar(50) | Y |  |  |
| 4 | Department | nvarchar(50) | Y |  |  |
| 5 | Size_Range | nvarchar(50) | Y |  |  |
| 6 | Size_Count | tinyint | Y |  |  |
| 7 | Amazon_Total_Forecast | int | Y |  |  |
| 8 | Amazon_Launch_Forecast | int | Y |  |  |

### dbo.AutoLoad_Offer_APW

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Offer | nvarchar(50) | Y |  |  |
| 2 | Division | nvarchar(50) | Y |  |  |
| 3 | Department | nvarchar(50) | Y |  |  |
| 4 | DirectAvgQtyPerWeek | float | Y |  |  |
| 5 | RetailAvgQtyPerWeek | float | Y |  |  |
| 6 | TotalAvgQtyPerWeek | float | Y |  |  |

### dbo.AutoLoad_Size_Distribution_Library

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Model_ID | nvarchar(50) | Y |  |  |
| 2 | Name | nvarchar(200) | Y |  |  |
| 3 | Division | nvarchar(50) | Y |  |  |
| 4 | Department | nvarchar(50) | Y |  |  |
| 5 | sizeid | nvarchar(50) | Y |  |  |
| 6 | DirectAvgQtyPerWeek | float | Y |  |  |
| 7 | DirectPercentOfSales | float | Y |  |  |
| 8 | RetailAvgQtyPerWeek | float | Y |  |  |
| 9 | RetailPercentOfSales | float | Y |  |  |
| 10 | TotalAvgQtyPerWeek | float | Y |  |  |
| 11 | TotalPercentOfSales | float | Y |  |  |
| 12 | Notes | nvarchar(500) | Y |  |  |
| 13 | Last Updated Date | datetime | Y |  |  |
| 14 | Last Update User | nvarchar(50) | Y |  |  |

### dbo.AutoLoad_Store_Groups

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | MODEL_ID | nvarchar(43) | N |  |  |
| 2 | NAME | nvarchar(31) | Y |  |  |
| 3 | DIVISION | nvarchar(500) | Y |  |  |
| 4 | DEPARTMENT | nvarchar(500) | Y |  |  |
| 5 | CLASS | nvarchar(500) | Y |  |  |
| 6 | storeid | nvarchar(20) | N |  |  |
| 7 | RETAIL AVG QTY PER WEEK | float | Y |  |  |
| 8 | RETAIL SIZE DIST PERCENT | float | Y |  |  |
| 9 | NOTES | varchar(10) | N |  |  |
| 10 | LAST UPDATED DATE | datetime | N |  |  |
| 11 | LAST UPDATE USER | varchar(11) | N |  |  |

### dbo.Autoload_offer_apw_2

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | OFFER | nvarchar(41) | N |  |  |
| 2 | DIVISION | nvarchar(500) | Y |  |  |
| 3 | DEPARTMENT | nvarchar(500) | Y |  |  |
| 4 | DIRECT AVG QTY PER WEEK | float | Y |  |  |
| 5 | RETAIL AVG QTY PER WEEK | float | Y |  |  |
| 6 | TOTAL AVG QTY PER WEEK | float | Y |  |  |

### dbo.Calculated_Immediate_Need_to_Cut_not_Available

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | sku | nvarchar(50) | Y |  |  |
| 2 | total_raw_need | int | Y |  |  |
| 3 | Avail_Inv | decimal(18,0) | Y |  |  |
| 4 | OfferID | nvarchar(50) | N |  |  |
| 5 | Final_Avail_Inventory_Threshold | float | Y |  |  |
| 6 | Final_Avail_Inventory | float | Y |  |  |
| 7 | StoreID | nvarchar(50) | Y |  |  |
| 8 | Raw_WOS_Forecast | float | Y |  |  |
| 9 | Raw_Order_Qty | int | Y |  |  |
| 10 | Raw_Running_Need | int | Y |  |  |

### dbo.Calculated_Immediate_Order_Need

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | SKU | nvarchar(50) | Y |  |  |
| 2 | StoreID | nvarchar(50) | Y |  |  |
| 3 | Raw_Order_Qty | int | Y |  |  |
| 4 | Calculated_On_Date | datetime | Y |  |  |

### dbo.Calendar_Lookup

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | CalendarDate | date | Y |  |  |
| 2 | FiscalPeriodMonth | tinyint | Y |  |  |
| 3 | FiscalWeekOfYear | tinyint | Y |  |  |
| 4 | FiscalYear | smallint | Y |  |  |
| 5 | FirstDayOfWeek | tinyint | Y |  |  |
| 6 | DayofMonth | tinyint | Y |  |  |
| 7 | FirstDayOfMonthRank | tinyint | Y |  |  |
| 8 | LastDayOfMonthRank | nchar(10) | Y |  |  |

### dbo.Channel_Offer_Demand_History

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Channel | nvarchar(20) | N |  |  |
| 2 | sku | varchar(50) | N |  |  |
| 3 | offer | nvarchar(41) | N |  |  |
| 4 | order_month | tinyint | Y |  |  |
| 5 | order_year | smallint | Y |  |  |
| 6 | order_week | tinyint | Y |  |  |
| 7 | CalendarDate | date | Y |  |  |
| 8 | Total_qty | numeric(38,16) | Y |  |  |
| 9 | Total_Demand_Amt | numeric(38,16) | Y |  |  |
| 10 | Total_Demand_Price_Amt | numeric(38,16) | Y |  |  |
| 11 | Total_Demand_Cost_Amt | numeric(38,16) | Y |  |  |
| 12 | Total_Demand_Margin_Dollar_Amt | numeric(38,16) | Y |  |  |
| 13 | Total_Return_Qty | numeric(38,16) | Y |  |  |
| 14 | Total_Return_Amt | numeric(38,16) | Y |  |  |
| 15 | Total_Net_Qty | numeric(38,16) | Y |  |  |
| 16 | Total_Net_Amt | numeric(38,16) | Y |  |  |
| 17 | Total_Net_Cost_Amt | numeric(38,16) | Y |  |  |
| 18 | Total_Net_Margin_Dollar_Amt | numeric(38,16) | Y |  |  |

### dbo.Channel_Offer_Demand_History_BACKUP

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Channel | nvarchar(20) | N |  |  |
| 2 | sku | varchar(50) | N |  |  |
| 3 | offer | nvarchar(41) | N |  |  |
| 4 | order_month | tinyint | Y |  |  |
| 5 | order_year | smallint | Y |  |  |
| 6 | order_week | tinyint | Y |  |  |
| 7 | CalendarDate | date | Y |  |  |
| 8 | Total_qty | numeric(38,16) | Y |  |  |
| 9 | Total_Demand_Amt | numeric(38,16) | Y |  |  |
| 10 | Total_Demand_Price_Amt | numeric(38,16) | Y |  |  |
| 11 | Total_Demand_Cost_Amt | numeric(38,16) | Y |  |  |
| 12 | Total_Demand_Margin_Dollar_Amt | numeric(38,16) | Y |  |  |
| 13 | Total_Return_Qty | numeric(38,16) | Y |  |  |
| 14 | Total_Return_Amt | numeric(38,16) | Y |  |  |
| 15 | Total_Net_Qty | numeric(38,16) | Y |  |  |
| 16 | Total_Net_Amt | numeric(38,16) | Y |  |  |
| 17 | Total_Net_Cost_Amt | numeric(38,16) | Y |  |  |
| 18 | Total_Net_Margin_Dollar_Amt | numeric(38,16) | Y |  |  |

### dbo.Channel_Offer_Forecast

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Channel | nvarchar(50) | N |  |  |
| 2 | OfferID | nvarchar(50) | N |  |  |
| 3 | CalendarDate | date | Y |  |  |
| 4 | FiscalWeekOfYear | tinyint | Y |  |  |
| 5 | Base_Sales_Unit_Forecast | float | Y |  |  |
| 6 | Initial_Unit_Forecast | float | Y |  |  |
| 7 | Promo_Adjustment | float | Y |  |  |
| 8 | Promo_Unit_Forecast | float | Y |  |  |
| 9 | Returns_Est_Percent | float | Y |  |  |
| 10 | Returns_Unit_Forecast | float | Y |  |  |
| 11 | Markdown_Adjustment | float | Y |  |  |
| 12 | Markdown_Unit_Forecast | float | Y |  |  |
| 13 | Net_Sales_Unit_Forecast | float | Y |  |  |
| 14 | Planned_Retail_Price | float | Y |  |  |
| 15 | Planned_Discount_Rate | float | Y |  |  |
| 16 | Estimated_AUR | float | Y |  |  |
| 17 | Net_Sales_Dollar_Forecast | float | Y |  |  |
| 18 | Planned_Margin | float | Y |  |  |
| 40 | Net_Inventory_Adjusted_Sales_Unit_Forecast | float | Y |  |  |
| 41 | Net_Unit_Forecast_Manual_Override | float | Y |  |  |

### dbo.Channel_Offer_Forecast_Archive

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Channel | nvarchar(50) | N |  |  |
| 2 | OfferID | nvarchar(50) | N |  |  |
| 3 | CalendarDate | date | Y |  |  |
| 4 | FiscalWeekOfYear | tinyint | Y |  |  |
| 5 | Base_Sales_Unit_Forecast | float | Y |  |  |
| 6 | Initial_Unit_Forecast | float | Y |  |  |
| 7 | Promo_Adjustment | float | Y |  |  |
| 8 | Promo_Unit_Forecast | float | Y |  |  |
| 9 | Returns_Est_Percent | float | Y |  |  |
| 10 | Returns_Unit_Forecast | float | Y |  |  |
| 11 | Markdown_Adjustment | float | Y |  |  |
| 12 | Markdown_Unit_Forecast | float | Y |  |  |
| 13 | Net_Sales_Unit_Forecast | float | Y |  |  |
| 14 | Planned_Retail_Price | float | Y |  |  |
| 15 | Planned_Discount_Rate | float | Y |  |  |
| 16 | Estimated_AUR | float | Y |  |  |
| 17 | Net_Sales_Dollar_Forecast | float | Y |  |  |
| 18 | Planned_Margin | float | Y |  |  |
| 19 | Net_Inventory_Adjusted_Sales_Unit_Forecast | float | Y |  |  |
| 20 | Net_Unit_Forecast_Manual_Override | float | Y |  |  |
| 21 | Archive_Date | datetime | Y |  |  |

### dbo.Channel_Offer_Forecast_Backup

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Channel | nvarchar(50) | N |  |  |
| 2 | OfferID | nvarchar(50) | N |  |  |
| 3 | CalendarDate | date | Y |  |  |
| 4 | FiscalWeekOfYear | tinyint | Y |  |  |
| 5 | Base_Sales_Unit_Forecast | float | Y |  |  |

### dbo.Channel_Offer_Forecast_Frozen

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Channel | nvarchar(50) | N |  |  |
| 2 | OfferID | nvarchar(50) | N |  |  |
| 3 | CalendarDate | date | Y |  |  |
| 4 | FiscalWeekOfYear | tinyint | Y |  |  |
| 5 | Base_Sales_Unit_Forecast | float | Y |  |  |
| 6 | Initial_Unit_Forecast | float | Y |  |  |
| 7 | Promo_Adjustment | float | Y |  |  |
| 8 | Promo_Unit_Forecast | float | Y |  |  |
| 9 | Returns_Est_Percent | float | Y |  |  |
| 10 | Returns_Unit_Forecast | float | Y |  |  |
| 11 | Markdown_Adjustment | float | Y |  |  |
| 12 | Markdown_Unit_Forecast | float | Y |  |  |
| 13 | Net_Sales_Unit_Forecast | float | Y |  |  |
| 14 | Planned_Retail_Price | float | Y |  |  |
| 15 | Planned_Discount_Rate | float | Y |  |  |
| 16 | Estimated_AUR | float | Y |  |  |
| 17 | Net_Sales_Dollar_Forecast | float | Y |  |  |
| 18 | Planned_Margin | float | Y |  |  |
| 19 | Net_Inventory_Adjusted_Sales_Unit_Forecast | float | Y |  |  |
| 20 | Net_Unit_Forecast_Manual_Override | float | Y |  |  |
| 21 | Frozen_Date | datetime | Y |  |  |
| 22 | Frozen_Type | nvarchar(50) | Y |  |  |

### dbo.Channel_Offer_SKU_Control

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | channel | nvarchar(50) | N |  |  |
| 2 | offerid | nvarchar(50) | N |  |  |
| 3 | sku | nvarchar(50) | N |  |  |
| 4 | Current_APW_Units | float | Y |  |  |
| 5 | Updated_APW_Units | float | Y |  |  |

### dbo.Channel_Offer_SKU_Forecast

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Channel | nvarchar(50) | N |  |  |
| 2 | OfferID | nvarchar(50) | N |  |  |
| 3 | SKU | nvarchar(50) | N |  |  |
| 4 | CalendarDate | date | Y |  |  |
| 5 | FiscalWeekOfYear | tinyint | Y |  |  |
| 6 | Base_Sales_Unit_Forecast | float | Y |  |  |
| 7 | Initial_Unit_Forecast | float | Y |  |  |
| 8 | Promo_Adjustment | float | Y |  |  |
| 9 | Promo_Unit_Forecast | float | Y |  |  |
| 10 | Returns_Est_Percent | float | Y |  |  |
| 11 | Returns_Unit_Forecast | float | Y |  |  |
| 12 | Markdown_Adjustment | float | Y |  |  |
| 13 | Markdown_Unit_Forecast | float | Y |  |  |
| 14 | Net_Sales_Unit_Forecast | float | Y |  |  |
| 15 | Planned_Retail_Price | float | Y |  |  |
| 16 | Planned_Discount_Rate | float | Y |  |  |
| 17 | Estimated_AUR | float | Y |  |  |
| 18 | Net_Sales_Dollar_Forecast | float | Y |  |  |
| 19 | Planned_Margin | float | Y |  |  |
| 45 | Net_Inventory_Adjusted_Sales_Unit_Forecast | float | Y |  |  |
| 46 | Net_Unit_Forecast_Manual_Override | float | Y |  |  |

### dbo.Channel_Offer_SKU_Forecast_Archive

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Channel | nvarchar(50) | N |  |  |
| 2 | OfferID | nvarchar(50) | N |  |  |
| 3 | SKU | nvarchar(50) | N |  |  |
| 4 | CalendarDate | date | Y |  |  |
| 5 | FiscalWeekOfYear | tinyint | Y |  |  |
| 6 | Base_Sales_Unit_Forecast | float | Y |  |  |
| 7 | Initial_Unit_Forecast | float | Y |  |  |
| 8 | Promo_Adjustment | float | Y |  |  |
| 9 | Promo_Unit_Forecast | float | Y |  |  |
| 10 | Returns_Est_Percent | float | Y |  |  |
| 11 | Returns_Unit_Forecast | float | Y |  |  |
| 12 | Markdown_Adjustment | float | Y |  |  |
| 13 | Markdown_Unit_Forecast | float | Y |  |  |
| 14 | Net_Sales_Unit_Forecast | float | Y |  |  |
| 15 | Planned_Retail_Price | float | Y |  |  |
| 16 | Planned_Discount_Rate | float | Y |  |  |
| 17 | Estimated_AUR | float | Y |  |  |
| 18 | Net_Sales_Dollar_Forecast | float | Y |  |  |
| 19 | Planned_Margin | float | Y |  |  |
| 20 | Net_Inventory_Adjusted_Sales_Unit_Forecast | float | Y |  |  |
| 21 | Net_Unit_Forecast_Manual_Override | float | Y |  |  |
| 22 | Archive_Date | datetime | N |  |  |

### dbo.Channel_Offer_SKU_Inventory_History

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | CalendarDate | date | N |  |  |
| 2 | CHANNEL | varchar(6) | N |  |  |
| 3 | OFFERID | nvarchar(50) | N |  |  |
| 4 | SKU | nvarchar(50) | N |  |  |
| 5 | Avail_OH | numeric(18,0) | Y |  |  |

### dbo.Channel_Offer_SKU_Store_Control

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | channel | nvarchar(50) | N |  |  |
| 2 | offerid | nvarchar(50) | N |  |  |
| 3 | sku | nvarchar(50) | N |  |  |
| 4 | locationid | smallint | N |  |  |
| 5 | Current_APW_Units | float | Y |  |  |
| 6 | Updated_APW_Units | float | Y |  |  |

### dbo.Channel_Offer_SKU_Store_Forecast

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Channel | nvarchar(50) | N |  |  |
| 2 | OfferID | nvarchar(50) | N |  |  |
| 3 | SKU | nvarchar(50) | N |  |  |
| 4 | LocationID | smallint | N |  |  |
| 5 | CalendarDate | date | Y |  |  |
| 6 | FiscalWeekOfYear | tinyint | Y |  |  |
| 7 | Base_Sales_Unit_Forecast | float | Y |  |  |
| 8 | Initial_Unit_Forecast | float | Y |  |  |

### dbo.Channel_SKU_SIZE_Weekly_Demand_History

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | CHANNEL | nvarchar(20) | N |  |  |
| 2 | DIVISION | nvarchar(500) | Y |  |  |
| 3 | DEPARTMENT | nvarchar(500) | Y |  |  |
| 4 | CLASS | nvarchar(500) | Y |  |  |
| 5 | ITEMID | nvarchar(20) | N |  |  |
| 6 | OFFER | nvarchar(41) | N |  |  |
| 7 | SKU | nvarchar(62) | N |  |  |
| 8 | SIZEID | nvarchar(10) | N |  |  |
| 9 | FISCALWEEKSTARTDATE | datetime2(7) | Y |  |  |
| 10 | UNIT_SALES_QTY | numeric(38,16) | Y |  |  |
| 11 | SALES_WEEK_ROW_NUMBER | bigint | Y |  |  |

### dbo.Current_Offer_Inventory

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | OfferID | nvarchar(50) | N |  |  |
| 2 | Total_Inv | numeric(38,16) | Y |  |  |
| 3 | Avail_Inv | numeric(38,16) | Y |  |  |
| 4 | Retail_Store_Inv | numeric(38,16) | Y |  |  |
| 5 | Outlet_Store_Inv | numeric(38,16) | Y |  |  |
| 6 | LandedCost | numeric(38,16) | Y |  |  |
| 7 | UnitPrice | numeric(38,16) | Y |  |  |
| 8 | LastUpdatedDate | datetime | Y |  |  |

### dbo.Current_Offer_Inventory_FirstOfWeek

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | OfferID | nvarchar(50) | N |  |  |
| 2 | Total_Inv | numeric(38,16) | Y |  |  |
| 3 | Avail_Inv | numeric(38,16) | Y |  |  |
| 4 | Retail_Store_Inv | numeric(38,16) | Y |  |  |
| 5 | Outlet_Store_Inv | numeric(38,16) | Y |  |  |
| 6 | LandedCost | numeric(38,16) | Y |  |  |
| 7 | UnitPrice | numeric(38,16) | Y |  |  |
| 8 | LastUpdatedDate | datetime | Y |  |  |

### dbo.Current_Offer_Inventory_backup

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | OfferID | nvarchar(50) | N |  |  |
| 2 | Total_Inv | numeric(38,16) | Y |  |  |
| 3 | Avail_Inv | numeric(38,16) | Y |  |  |
| 4 | LandedCost | numeric(38,16) | Y |  |  |
| 5 | UnitPrice | numeric(38,16) | Y |  |  |
| 6 | LastUpdatedDate | datetime | Y |  |  |

### dbo.Current_SKU_Available_DC_Inventory

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | SKU | nvarchar(50) | Y |  |  |
| 2 | Total_Inv | decimal(18,0) | Y |  |  |
| 3 | Avail_Inv | decimal(18,0) | Y |  |  |
| 4 | Retail_Store_Inv | numeric(38,16) | Y |  |  |
| 5 | Outlet_Store_Inv | numeric(38,16) | Y |  |  |
| 6 | LastUpdatedDate | datetime | Y |  |  |

### dbo.Current_SKU_Available_DC_Inventory_FirstOfWeek

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | SKU | nvarchar(50) | Y |  |  |
| 2 | Total_Inv | decimal(18,0) | Y |  |  |
| 3 | Avail_Inv | decimal(18,0) | Y |  |  |
| 4 | Retail_Store_Inv | numeric(38,16) | Y |  |  |
| 5 | Outlet_Store_Inv | numeric(38,16) | Y |  |  |
| 6 | LastUpdatedDate | datetime | Y |  |  |

### dbo.Current_SKU_Available_DC_Inventory_backup

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | SKU | nvarchar(50) | Y |  |  |
| 2 | Avail_Inv | decimal(18,0) | Y |  |  |
| 3 | LastUpdatedDate | datetime | Y |  |  |

### dbo.Current_Store_Available_Inventory

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | StoreID | nvarchar(50) | Y |  |  |
| 2 | SKU | nvarchar(50) | Y |  |  |
| 3 | Onhand | float | Y |  |  |
| 4 | OnOrder | float | Y |  |  |
| 5 | LastUpdateDate | datetime | Y |  |  |

### dbo.Current_Store_Available_Inventory_Adjustments

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | StoreID | nvarchar(50) | Y |  |  |
| 2 | SKU | nvarchar(50) | Y |  |  |
| 3 | Onhand_Adjustment | float | Y |  |  |
| 4 | Status | nvarchar(10) | Y |  |  |
| 5 | LastUpdateDate | datetime | Y |  |  |
| 6 | LastUpdateUser | nvarchar(50) | Y |  |  |

### dbo.DB_Errors

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | ErrorID | int | N |  |  |
| 2 | UserName | varchar(100) | Y |  |  |
| 3 | ErrorNumber | int | Y |  |  |
| 4 | ErrorState | int | Y |  |  |
| 5 | ErrorSeverity | int | Y |  |  |
| 6 | ErrorLine | int | Y |  |  |
| 7 | ErrorProcedure | varchar(max) | Y |  |  |
| 8 | ErrorMessage | varchar(max) | Y |  |  |
| 9 | ErrorDateTime | datetime | Y |  |  |

### dbo.Forecast_Job_Log

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Process_Date | datetime | Y |  |  |
| 2 | Process_Description | varchar(500) | Y |  |  |

### dbo.Inventory_History

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | AsOfDate | datetime2(7) | Y |  |  |
| 2 | Avail_OH | float | Y |  |  |
| 3 | Division | nvarchar(50) | Y |  |  |
| 4 | DEPARTMENT | nvarchar(50) | Y |  |  |
| 5 | CHANNEL | nvarchar(50) | Y |  |  |
| 6 | Avail_Cost_OH | float | Y |  |  |
| 7 | SEASONPARENTCODE | nvarchar(50) | Y |  |  |

### dbo.Kubix_Attributes

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Offer | varchar(255) | N |  |  |
| 2 | Season | varchar(255) | Y |  |  |
| 3 | Sub_Season | varchar(255) | Y |  |  |
| 4 | Season_Code | varchar(255) | Y |  |  |
| 5 | Life_cycle | varchar(255) | Y |  |  |
| 6 | Flow_Date | varchar(255) | Y |  |  |
| 7 | Start_Date | varchar(255) | Y |  |  |
| 8 | End_Date | varchar(255) | Y |  |  |
| 9 | Vendor | varchar(255) | Y |  |  |
| 10 | Gender | varchar(255) | Y |  |  |
| 11 | Color_Group | varchar(255) | Y |  |  |
| 12 | Family_Match | varchar(255) | Y |  |  |
| 13 | Collection | varchar(255) | Y |  |  |
| 14 | Theme | varchar(255) | Y |  |  |
| 15 | Royalty_Code | varchar(255) | Y |  |  |
| 16 | Product_Detail | varchar(255) | Y |  |  |
| 17 | Pack_Size | varchar(255) | Y |  |  |
| 18 | Silhouette | varchar(255) | Y |  |  |
| 19 | Licensor | varchar(255) | Y |  |  |
| 20 | Licensed_Property | varchar(255) | Y |  |  |
| 21 | GOTS_Cert_Date | varchar(255) | Y |  |  |
| 22 | GRS_Cert_Date | varchar(255) | Y |  |  |
| 23 | Dropped | varchar(255) | Y |  |  |
| 24 | Drp | varchar(255) | Y |  |  |
| 25 | Date_Added_UTC | datetime | Y |  |  |
| 26 | Description | varchar(255) | Y |  |  |
| 27 | Division | varchar(255) | Y |  |  |
| 28 | Department | varchar(255) | Y |  |  |
| 29 | Class | varchar(255) | Y |  |  |
| 30 | ColorName | varchar(255) | Y |  |  |
| 31 | MSRP | varchar(255) | Y |  |  |
| 32 | Material_Group | varchar(255) | Y |  |  |
| 33 | Fabric_Group | varchar(255) | Y |  |  |

### dbo.Markdown_Model_Headers

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | MD_ID | varchar(50) | Y |  |  |
| 2 | MD_Channel | varchar(50) | Y |  |  |
| 3 | MD_Name | varchar(200) | Y |  |  |
| 4 | MD_Notes | varchar(1000) | Y |  |  |
| 5 | Last_Update_Date | datetime | Y |  |  |
| 6 | Last_Update_User | varchar(50) | Y |  |  |
| 7 | Status | nvarchar(50) | Y |  |  |

### dbo.Markdown_Model_Pricing_Detail

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | MD_ID | varchar(50) | Y |  |  |
| 2 | MD_Effective_Date | date | Y |  |  |
| 3 | MD_Calendar_Date_Week_Begin | date | Y |  |  |
| 4 | Ticketed_Price | decimal(18,2) | Y |  |  |

### dbo.Markdown_Model_Product_Detail

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | MD_ID | varchar(50) | Y |  |  |
| 2 | MD_Level | varchar(50) | Y |  |  |
| 3 | Level_Value | varchar(50) | Y |  |  |

### dbo.Monthly_On_Order

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Channel | nvarchar(50) | Y |  |  |
| 2 | Division | nvarchar(50) | Y |  |  |
| 3 | Department | nvarchar(50) | Y |  |  |
| 4 | SeasonParentCode | nvarchar(50) | Y |  |  |
| 5 | Fiscal_Year | smallint | Y |  |  |
| 6 | Fiscal_Month | tinyint | Y |  |  |
| 7 | Total_Line_Amount | float | Y |  |  |
| 8 | Total_Ordered_Qty | float | Y |  |  |
| 9 | Total_Remaining_Order_Qty | float | Y |  |  |
| 10 | Total_Remaining_Order_Amt | float | Y |  |  |

### dbo.No_Size_Exception_Offers

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | OfferID | varchar(50) | Y |  |  |

### dbo.OTB_Month_Plans

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Channel | nvarchar(50) | Y |  |  |
| 2 | Division | nvarchar(50) | Y |  |  |
| 3 | Department | nvarchar(50) | Y |  |  |
| 4 | SeasonParentCode | nvarchar(50) | Y |  |  |
| 5 | FiscalYear | smallint | Y |  |  |
| 6 | FiscalMonth | tinyint | Y |  |  |
| 7 | FiscalDate | date | Y |  |  |
| 8 | Sales_Budget_$ | float | Y |  |  |
| 9 | Sales_Plan_$ | float | Y |  |  |
| 10 | Sales_Plan_Units | float | Y |  |  |
| 11 | Cost_Plan_$ | float | Y |  |  |
| 12 | GM_Budget_$ | float | Y |  |  |
| 13 | GM_Plan_$ | float | Y |  |  |
| 14 | GM_Budget_% | float | Y |  |  |
| 15 | GM_Plan_% | float | Y |  |  |
| 16 | AUR_Plan | float | Y |  |  |
| 17 | AUC_Plan | float | Y |  |  |
| 18 | Receipt_Plan_$ | float | Y |  |  |
| 19 | Receipt_Plan_Units | float | Y |  |  |
| 20 | Inv_Plan_$ | float | Y |  |  |
| 21 | Inv_Plan_Units | float | Y |  |  |
| 22 | Inv_Plan_Transfer_$ | float | Y |  |  |
| 23 | Inv_Plan_Transfer_Units | float | Y |  |  |
| 24 | LastUpdatedDate | datetime | Y |  |  |
| 25 | LastUpdatedUser | nvarchar(50) | Y |  |  |

### dbo.OTB_Plan_Parameters

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Channel | varchar(50) | Y |  |  |
| 2 | Division | varchar(50) | Y |  |  |
| 3 | Department | varchar(50) | Y |  |  |
| 4 | SeasonParentCode | varchar(50) | Y |  |  |
| 5 | StartCalendarDate | date | Y |  |  |
| 6 | Sales_Index_1 | float | Y |  |  |
| 7 | Sales_Index_2 | float | Y |  |  |
| 8 | Sales_Index_3 | float | Y |  |  |
| 9 | Sales_Index_4 | float | Y |  |  |
| 10 | Sales_Index_5 | float | Y |  |  |
| 11 | Sales_Index_6 | float | Y |  |  |
| 12 | Sales_Index_7 | float | Y |  |  |
| 13 | Sales_Index_8 | float | Y |  |  |
| 14 | Sales_Index_9 | float | Y |  |  |
| 15 | Sales_Index_10 | float | Y |  |  |
| 16 | Sales_Index_11 | float | Y |  |  |
| 17 | Sales_Index_12 | float | Y |  |  |
| 18 | Unit_Index_1 | float | Y |  |  |
| 19 | Unit_Index_2 | float | Y |  |  |
| 20 | Unit_Index_3 | float | Y |  |  |
| 21 | Unit_Index_4 | float | Y |  |  |
| 22 | Unit_Index_5 | float | Y |  |  |
| 23 | Unit_Index_6 | float | Y |  |  |
| 24 | Unit_Index_7 | float | Y |  |  |
| 25 | Unit_Index_8 | float | Y |  |  |
| 26 | Unit_Index_9 | float | Y |  |  |
| 27 | Unit_Index_10 | float | Y |  |  |
| 28 | Unit_Index_11 | float | Y |  |  |
| 29 | Unit_Index_12 | float | Y |  |  |
| 30 | Cost_Index_1 | float | Y |  |  |
| 31 | Cost_Index_2 | float | Y |  |  |
| 32 | Cost_Index_3 | float | Y |  |  |
| 33 | Cost_Index_4 | float | Y |  |  |
| 34 | Cost_Index_5 | float | Y |  |  |
| 35 | Cost_Index_6 | float | Y |  |  |
| 36 | Cost_Index_7 | float | Y |  |  |
| 37 | Cost_Index_8 | float | Y |  |  |
| 38 | Cost_Index_9 | float | Y |  |  |
| 39 | Cost_Index_10 | float | Y |  |  |
| 40 | Cost_Index_11 | float | Y |  |  |
| 41 | Cost_Index_12 | float | Y |  |  |
| 42 | Sales_Trend_Factor | float | Y |  |  |
| 43 | Units_Trend_Factor | float | Y |  |  |
| 44 | Cost_Trend_Factor | float | Y |  |  |
| 45 | Sales_Adj_1 | float | Y |  |  |
| 46 | Sales_Adj_2 | float | Y |  |  |
| 47 | Sales_Adj_3 | float | Y |  |  |
| 48 | Sales_Adj_4 | float | Y |  |  |
| 49 | Sales_Adj_5 | float | Y |  |  |
| 50 | Sales_Adj_6 | float | Y |  |  |
| 51 | Sales_Adj_7 | float | Y |  |  |
| 52 | Sales_Adj_8 | float | Y |  |  |
| 53 | Sales_Adj_9 | float | Y |  |  |
| 54 | Sales_Adj_10 | float | Y |  |  |
| 55 | Sales_Adj_11 | float | Y |  |  |
| 56 | Sales_Adj_12 | float | Y |  |  |
| 57 | Units_Adj_1 | float | Y |  |  |
| 58 | Units_Adj_2 | float | Y |  |  |
| 59 | Units_Adj_3 | float | Y |  |  |
| 60 | Units_Adj_4 | float | Y |  |  |
| 61 | Units_Adj_5 | float | Y |  |  |
| 62 | Units_Adj_6 | float | Y |  |  |
| 63 | Units_Adj_7 | float | Y |  |  |
| 64 | Units_Adj_8 | float | Y |  |  |
| 65 | Units_Adj_9 | float | Y |  |  |
| 66 | Units_Adj_10 | float | Y |  |  |
| 67 | Units_Adj_11 | float | Y |  |  |
| 68 | Units_Adj_12 | float | Y |  |  |
| 69 | Cost_Adj_1 | float | Y |  |  |
| 70 | Cost_Adj_2 | float | Y |  |  |
| 71 | Cost_Adj_3 | float | Y |  |  |
| 72 | Cost_Adj_4 | float | Y |  |  |
| 73 | Cost_Adj_5 | float | Y |  |  |
| 74 | Cost_Adj_6 | float | Y |  |  |
| 75 | Cost_Adj_7 | float | Y |  |  |
| 76 | Cost_Adj_8 | float | Y |  |  |
| 77 | Cost_Adj_9 | float | Y |  |  |
| 78 | Cost_Adj_10 | float | Y |  |  |
| 79 | Cost_Adj_11 | float | Y |  |  |
| 80 | Cost_Adj_12 | float | Y |  |  |

### dbo.OfferID_Xref_Control

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Master_OfferID | nvarchar(50) | Y |  |  |
| 2 | Child_OfferID | nvarchar(50) | Y |  |  |

### dbo.OfferID_to_reset_planned_receipts

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | OfferID | nvarchar(50) | N |  |  |
| 2 | Import_Date | date | N |  |  |

### dbo.Offer_Control_Table

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Channel | nvarchar(50) | Y |  |  |
| 2 | OfferID | nvarchar(50) | Y |  |  |
| 3 | Division | nvarchar(50) | Y |  |  |
| 4 | Department | nvarchar(50) | Y |  |  |
| 5 | SeasonCode | nvarchar(50) | Y |  |  |
| 6 | Unit_Cost | float | Y |  |  |
| 7 | TicketedRetail | float | Y |  |  |
| 8 | Planning_APW | float | Y |  |  |
| 9 | Initial_APW_Units | float | Y |  |  |
| 10 | Initialized_Date | date | Y |  |  |
| 11 | Target_Sell_Thru | float | Y |  |  |
| 12 | Start_Date | date | Y |  |  |
| 13 | Stop_Date | date | Y |  |  |
| 14 | Intended_Weeks | float | Y |  |  |
| 15 | Size_Range_Model | varchar(50) | Y |  |  |
| 16 | Seasonal_Profile_Model | varchar(50) | Y |  |  |
| 17 | Store_Distribution_Model | varchar(50) | Y |  |  |
| 18 | GroupName | nvarchar(50) | Y |  |  |
| 19 | Returns_Model | float | Y |  |  |
| 20 | Markdown_Model | varchar(50) | Y |  |  |
| 21 | Out_Of_Stock_Date | date | Y |  |  |
| 22 | Retrend_Forecast | nvarchar(5) | Y |  |  |
| 23 | Retrend_Model | nvarchar(25) | Y |  |  |
| 24 | Replenish_Flag | nvarchar(5) | Y |  |  |
| 25 | Start_Replenish_Date | date | Y |  |  |
| 26 | Stop_Replenish_Date | date | Y |  |  |
| 27 | Replenish_Target_WOS | float | Y |  |  |
| 28 | Replenish_Lead_Time | float | Y |  |  |
| 29 | Replenish_Cycle_Time | float | Y |  |  |
| 30 | Initial_Allocation_WOS | float | Y |  |  |
| 31 | AutoAllocate_Flag | nvarchar(50) | Y |  |  |
| 32 | Start_Allocate_Date | date | Y |  |  |
| 33 | Stop_Allocate_Date | date | Y |  |  |
| 34 | Allocation_Max_WOS | float | Y |  |  |
| 35 | Allocation_Max_Limit_Qty | float | Y |  |  |
| 36 | Allocation_Avail_Inventory_Threshold | float | Y |  |  |
| 37 | Allocation_Cutoff_Threshold | float | Y |  |  |
| 38 | Last Updated Date | datetime | Y |  |  |
| 39 | Last Update User | varchar(50) | Y |  |  |
| 40 | Status | varchar(50) | Y |  |  |
| 41 | Current_APW_Units | float | Y |  |  |
| 42 | Updated_APW_Units | float | Y |  |  |
| 43 | Supplier_Min_Order_Qty | float | Y |  |  |
| 44 | Initial_Est_Purchase_Units | int | Y |  |  |
| 45 | Final_Est_Purchase_Units | int | Y |  |  |
| 46 | Notes | nvarchar(1000) | Y |  |  |
| 47 | Auto_Create_Receipt_Plan | nvarchar(50) | Y |  |  |
| 48 | Master_Forecast_Flag | nvarchar(50) | Y |  |  |
| 49 | ColorName | varchar(50) | Y |  |  |
| 50 | On_Order_Auto_Receipt_Days | numeric(18,0) | Y |  |  |
| 51 | itemname | nvarchar(60) | Y |  |  |
| 52 | Initial_Order_Flow_Type | varchar(50) | Y |  |  |
| 53 | Initial_Order_Percent_Qty | float | Y |  |  |
| 54 | Balance_Order_Flow_Type | varchar(50) | Y |  |  |
| 55 | Balance_Order_Weeks_From_Initial | int | Y |  |  |
| 56 | Min_Balance_To_Flow_Qty | int | Y |  |  |
| 57 | Use_Offer_Min_Size_Qtys_Flag | varchar(3) | Y |  |  |
| 58 | SizeRange | nvarchar(50) | Y |  |  |
| 59 | ForecastNotTrendWeeksOutFactor | int | Y |  |  |

### dbo.Offer_Inventory_Forecast

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | OfferID | varchar(50) | Y |  |  |
| 2 | CalendarDate | date | Y |  |  |
| 3 | BOP_Inventory_Units | float | Y |  |  |
| 4 | Planned_Receipts | float | Y |  |  |
| 5 | Direct_Planned_Receipts | float | Y |  |  |
| 6 | Retail_Planned_Receipts | float | Y |  |  |
| 7 | Other_Planned_Receipts | float | Y |  |  |
| 8 | Actual_On_Order | float | Y |  |  |
| 9 | EOP_Inventory_Units | float | Y |  |  |
| 10 | Pure_Inventory_Shortage | float | Y |  |  |
| 11 | WOS_Inventory_Target | float | Y |  |  |
| 12 | WOS_Inventory_Shortage | float | Y |  |  |
| 13 | Last_Updated_Date | datetime | Y |  |  |
| 14 | Last_Updated_User | varchar(50) | Y |  |  |

### dbo.Offer_Inventory_Forecast_Frozen

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | OfferID | varchar(50) | Y |  |  |
| 2 | CalendarDate | date | Y |  |  |
| 3 | BOP_Inventory_Units | float | Y |  |  |
| 4 | Planned_Receipts | float | Y |  |  |
| 5 | Actual_On_Order | float | Y |  |  |
| 6 | EOP_Inventory_Units | float | Y |  |  |
| 7 | Pure_Inventory_Shortage | float | Y |  |  |
| 8 | WOS_Inventory_Target | float | Y |  |  |
| 9 | WOS_Inventory_Shortage | float | Y |  |  |
| 10 | Last_Updated_Date | datetime | Y |  |  |
| 11 | Last_Updated_User | varchar(50) | Y |  |  |
| 12 | Frozen_Date | datetime | Y |  |  |
| 13 | Frozen_Type | nvarchar(50) | Y |  |  |

### dbo.Offer_Inventory_Forecast_Working

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | OfferID | varchar(50) | Y |  |  |
| 2 | CalendarDate | date | Y |  |  |
| 3 | BOP_Inventory_Units | float | Y |  |  |
| 4 | Planned_Receipts | float | Y |  |  |
| 5 | Direct_Planned_Receipts | float | Y |  |  |
| 6 | Retail_Planned_Receipts | float | Y |  |  |
| 7 | Other_Planned_Receipts | float | Y |  |  |
| 8 | Actual_On_Order | float | Y |  |  |
| 9 | EOP_Inventory_Units | float | Y |  |  |
| 10 | Pure_Inventory_Shortage | float | Y |  |  |
| 11 | WOS_Inventory_Target | float | Y |  |  |
| 12 | WOS_Inventory_Shortage | float | Y |  |  |

### dbo.Offer_Inventory_Forecast_backup_20250324

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | OfferID | varchar(50) | Y |  |  |
| 2 | CalendarDate | date | Y |  |  |
| 3 | BOP_Inventory_Units | float | Y |  |  |
| 4 | Planned_Receipts | float | Y |  |  |
| 5 | Direct_Planned_Receipts | float | Y |  |  |
| 6 | Retail_Planned_Receipts | float | Y |  |  |
| 7 | Other_Planned_Receipts | float | Y |  |  |
| 8 | Actual_On_Order | float | Y |  |  |
| 9 | EOP_Inventory_Units | float | Y |  |  |
| 10 | Pure_Inventory_Shortage | float | Y |  |  |
| 11 | WOS_Inventory_Target | float | Y |  |  |
| 12 | WOS_Inventory_Shortage | float | Y |  |  |
| 13 | Last_Updated_Date | datetime | Y |  |  |
| 14 | Last_Updated_User | varchar(50) | Y |  |  |

### dbo.Offer_SKU_Inventory_Forecast

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | OfferID | varchar(50) | Y |  |  |
| 2 | SKU | varchar(50) | Y |  |  |
| 3 | CalendarDate | date | Y |  |  |
| 4 | BOP_Inventory_Units | float | Y |  |  |
| 5 | Planned_Receipts | float | Y |  |  |
| 6 | Direct_Planned_Receipts | float | Y |  |  |
| 7 | Retail_Planned_Receipts | float | Y |  |  |
| 8 | Other_Planned_Receipts | float | Y |  |  |
| 9 | Actual_On_Order | float | Y |  |  |
| 10 | EOP_Inventory_Units | float | Y |  |  |
| 11 | Pure_Inventory_Shortage | float | Y |  |  |
| 12 | WOS_Inventory_Target | float | Y |  |  |
| 13 | WOS_Inventory_Shortage | float | Y |  |  |
| 14 | Last_Updated_Date | datetime | Y |  |  |
| 15 | Last_Updated_User | varchar(50) | Y |  |  |

### dbo.Offer_SKU_Inventory_Forecast_Working

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | OfferID | varchar(50) | Y |  |  |
| 2 | SKU | varchar(50) | Y |  |  |
| 3 | CalendarDate | date | Y |  |  |
| 4 | BOP_Inventory_Units | float | Y |  |  |
| 5 | Planned_Receipts | float | Y |  |  |
| 6 | Direct_Planned_Receipts | float | Y |  |  |
| 7 | Retail_Planned_Receipts | float | Y |  |  |
| 8 | Other_Planned_Receipts | float | Y |  |  |
| 9 | Actual_On_Order | float | Y |  |  |
| 10 | EOP_Inventory_Units | float | Y |  |  |
| 11 | Pure_Inventory_Shortage | float | Y |  |  |
| 12 | WOS_Inventory_Target | float | Y |  |  |
| 13 | WOS_Inventory_Shortage | float | Y |  |  |
| 14 | Last_Updated_Date | datetime | Y |  |  |
| 15 | Last_Updated_User | varchar(50) | Y |  |  |

### dbo.Offer_SKU_Inventory_Forecast_backup

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | OfferID | varchar(50) | Y |  |  |
| 2 | SKU | varchar(50) | Y |  |  |
| 3 | CalendarDate | date | Y |  |  |
| 4 | BOP_Inventory_Units | float | Y |  |  |
| 5 | Planned_Receipts | float | Y |  |  |
| 6 | Direct_Planned_Receipts | float | Y |  |  |
| 7 | Retail_Planned_Receipts | float | Y |  |  |
| 8 | Other_Planned_Receipts | float | Y |  |  |
| 9 | Actual_On_Order | float | Y |  |  |
| 10 | EOP_Inventory_Units | float | Y |  |  |
| 11 | Pure_Inventory_Shortage | float | Y |  |  |
| 12 | WOS_Inventory_Target | float | Y |  |  |
| 13 | WOS_Inventory_Shortage | float | Y |  |  |
| 14 | Last_Updated_Date | datetime | Y |  |  |
| 15 | Last_Updated_User | varchar(50) | Y |  |  |

### dbo.Offers

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | OfferID | nvarchar(31) | N |  |  |
| 2 | division | nvarchar(500) | Y |  |  |
| 3 | department | nvarchar(500) | Y |  |  |
| 4 | ItemName | nvarchar(60) | N |  |  |
| 5 | ColorName | nvarchar(60) | Y |  |  |
| 6 | Class | nvarchar(50) | Y |  |  |
| 7 | SizeRange | nvarchar(50) | Y |  |  |
| 8 | Avg_Landed_Cost | float | Y |  |  |
| 9 | Max_MSRP | float | Y |  |  |
| 10 | DirectSellStartDate | datetime | Y |  |  |
| 11 | DirectSellEndDate | datetime | Y |  |  |
| 12 | DirectSeasonParentCode | nvarchar(10) | Y |  |  |
| 13 | RetailSellStartDate | datetime | Y |  |  |
| 14 | RetailSellEndDate | datetime | Y |  |  |
| 15 | RetailSeasonParentCode | nvarchar(10) | Y |  |  |
| 16 | WholesaleSellStartDate | datetime | Y |  |  |
| 17 | WholesaleSellEndDate | datetime | Y |  |  |
| 18 | WholesaleSeasonParentCode | nvarchar(10) | Y |  |  |
| 19 | OutletSellStartDate | datetime | Y |  |  |
| 20 | OutletSellEndDate | datetime | Y |  |  |
| 21 | OutletSeasonParentCode | nvarchar(10) | Y |  |  |

### dbo.Offers_Backup

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | OfferID | nvarchar(31) | N |  |  |
| 2 | division | nvarchar(500) | Y |  |  |
| 3 | department | nvarchar(500) | Y |  |  |
| 4 | ItemName | nvarchar(60) | N |  |  |
| 5 | ColorName | nvarchar(60) | Y |  |  |
| 6 | Class | nvarchar(50) | Y |  |  |
| 7 | SizeRange | nvarchar(50) | Y |  |  |
| 8 | Avg_Landed_Cost | float | Y |  |  |
| 9 | Max_MSRP | float | Y |  |  |
| 10 | DirectSellStartDate | datetime | Y |  |  |
| 11 | DirectSellEndDate | datetime | Y |  |  |
| 12 | DirectSeasonParentCode | nvarchar(10) | Y |  |  |
| 13 | RetailSellStartDate | datetime | Y |  |  |
| 14 | RetailSellEndDate | datetime | Y |  |  |
| 15 | RetailSeasonParentCode | nvarchar(10) | Y |  |  |
| 16 | WholesaleSellStartDate | datetime | Y |  |  |
| 17 | WholesaleSellEndDate | datetime | Y |  |  |
| 18 | WholesaleSeasonParentCode | nvarchar(10) | Y |  |  |
| 19 | OutletSellStartDate | datetime | Y |  |  |
| 20 | OutletSellEndDate | datetime | Y |  |  |
| 21 | OutletSeasonParentCode | nvarchar(10) | Y |  |  |

### dbo.On_Order

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | CalendarDate | date | Y |  |  |
| 2 | ActualDeliveryDate | date | Y |  |  |
| 3 | Channel | nvarchar(50) | Y |  |  |
| 4 | Offer | nvarchar(50) | Y |  |  |
| 5 | SKU | nvarchar(50) | Y |  |  |
| 6 | Total_Line_Amount | float | Y |  |  |
| 7 | Total_Ordered_Qty | float | Y |  |  |
| 8 | Total_Remaining_Order_Qty | float | Y |  |  |
| 9 | Total_Remaining_Order_Amt | float | Y |  |  |
| 10 | FiscalYear | smallint | Y |  |  |
| 11 | FiscalWeek | tinyint | Y |  |  |

### dbo.PFS_System_Parameters

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Inventory_Carry_Cost | float | Y |  |  |

### dbo.Product_Dimensions_Hierarchy_Attributes

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | sku | nvarchar(60) | Y |  |  |
| 2 | offer | nvarchar(40) | Y |  |  |
| 3 | style | nvarchar(20) | Y |  |  |
| 4 | colorid | nvarchar(20) | Y |  |  |
| 5 | sizeid | nvarchar(20) | Y |  |  |
| 6 | itemname | nvarchar(60) | Y |  |  |
| 7 | artwork_name | nvarchar(255) | Y |  |  |
| 8 | product_detail | nvarchar(255) | Y |  |  |
| 9 | colorname | nvarchar(60) | Y |  |  |
| 10 | gender | nvarchar(255) | Y |  |  |
| 11 | life_cycle | nvarchar(255) | Y |  |  |
| 12 | material_group | nvarchar(255) | Y |  |  |
| 13 | fabric_group | nvarchar(255) | Y |  |  |
| 14 | sleeve_length | nvarchar(255) | Y |  |  |
| 15 | family_match | nvarchar(255) | Y |  |  |
| 16 | pack_size | nvarchar(255) | Y |  |  |
| 17 | vendor | nvarchar(255) | Y |  |  |
| 18 | licensed_property | nvarchar(255) | Y |  |  |
| 19 | licensor | nvarchar(255) | Y |  |  |
| 20 | division | nvarchar(500) | Y |  |  |
| 21 | division_specific | nvarchar(255) | Y |  |  |
| 22 | department | nvarchar(500) | Y |  |  |
| 23 | class | nvarchar(500) | Y |  |  |
| 24 | key_category_view | nvarchar(255) | Y |  |  |
| 25 | exec_view | nvarchar(255) | Y |  |  |
| 26 | pyramid | nvarchar(255) | Y |  |  |
| 27 | season | nvarchar(255) | Y |  |  |
| 28 | season_code | nvarchar(255) | Y |  |  |
| 29 | sub_season | nvarchar(255) | Y |  |  |
| 30 | theme | nvarchar(255) | Y |  |  |
| 31 | capsule | nvarchar(255) | Y |  |  |
| 32 | collection | nvarchar(255) | Y |  |  |
| 33 | start_date | date | Y |  |  |
| 34 | end_date | date | Y |  |  |
| 35 | original_start_date | date | Y |  |  |
| 36 | flow_date | nvarchar(255) | Y |  |  |
| 37 | go_live_date | nvarchar(255) | Y |  |  |
| 38 | gots_cert_date | date | Y |  |  |
| 39 | grs_cert_date | date | Y |  |  |
| 40 | created_date | nvarchar(255) | Y |  |  |
| 41 | lastmodified | nvarchar(255) | Y |  |  |
| 42 | royalty_code | nvarchar(255) | Y |  |  |
| 43 | _fivetran_deleted | nvarchar(255) | Y |  |  |
| 44 | _fivetran_synced | nvarchar(255) | Y |  |  |
| 45 | dbt_loaded_at | datetime2(7) | Y |  |  |

### dbo.Promo_Model_Detail

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Promo_ID | varchar(50) | Y |  |  |
| 2 | Promo_Level | varchar(50) | Y |  |  |
| 3 | Level_Value | varchar(50) | Y |  |  |

### dbo.Promo_Model_Headers

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Promo_ID | varchar(50) | Y |  |  |
| 2 | Promo_Channel | varchar(50) | Y |  |  |
| 3 | Promo_Name | varchar(200) | Y |  |  |
| 4 | Promo_type | varchar(50) | Y |  |  |
| 5 | Promo_Implied_Value | decimal(18,2) | Y |  |  |
| 6 | Promo_Start | date | Y |  |  |
| 7 | Promo_End | date | Y |  |  |
| 8 | Promo_Lift | decimal(18,2) | Y |  |  |
| 9 | Promo_Notes | varchar(1000) | Y |  |  |
| 10 | Last_Update_Date | datetime | Y |  |  |
| 11 | Last_Update_User | varchar(50) | Y |  |  |
| 12 | Status | nvarchar(50) | Y |  |  |

### dbo.Promo_Model_Headers_Additional_Waves

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Promo_ID | varchar(50) | Y |  |  |
| 2 | Promo_type | varchar(50) | Y |  |  |
| 3 | Promo_Implied_Value | decimal(18,2) | Y |  |  |
| 4 | Promo_Start | date | Y |  |  |
| 5 | Promo_End | date | Y |  |  |
| 6 | Promo_Lift | decimal(18,2) | Y |  |  |
| 7 | Last_Update_Date | datetime | Y |  |  |
| 8 | Last_Update_User | varchar(50) | Y |  |  |
| 9 | Status | nvarchar(50) | Y |  |  |
| 10 | ID | int | N |  |  |

### dbo.Promo_Rank_Calculations

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | promo_id | varchar(50) | Y |  |  |
| 2 | channel | varchar(50) | Y |  |  |
| 3 | offerid | nvarchar(50) | Y |  |  |
| 4 | division | nvarchar(50) | Y |  |  |
| 5 | department | nvarchar(50) | Y |  |  |
| 6 | style | nvarchar(5) | Y |  |  |
| 7 | promo_lift | decimal(18,2) | Y |  |  |
| 8 | level | int | Y |  |  |
| 9 | promo_start | date | Y |  |  |
| 10 | promo_end | date | Y |  |  |
| 11 | Promo_Implied_Value | float | Y |  |  |
| 12 | fiscalyear | smallint | Y |  |  |
| 13 | fiscalWeekOfYear | tinyint | Y |  |  |
| 14 | Nbr_Week_Days | int | Y |  |  |
| 15 | Percent_of_Week | decimal(38,4) | Y |  |  |
| 16 | Final_Week_Promo_Lift | decimal(38,6) | Y |  |  |
| 17 | Promo_Rank | bigint | Y |  |  |

### dbo.Receipt_History

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | FiscalYear | smallint | Y |  |  |
| 2 | FiscalPeriodMonth | tinyint | Y |  |  |
| 3 | Inventory_Status | nvarchar(50) | Y |  |  |
| 4 | Division | nvarchar(50) | Y |  |  |
| 5 | Department | nvarchar(50) | Y |  |  |
| 6 | SeasonParentCode | nvarchar(50) | Y |  |  |
| 7 | Received_Qty | float | Y |  |  |
| 8 | Received_Amt | float | Y |  |  |

### dbo.Receipt_History_Backup

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | FiscalYear | smallint | Y |  |  |
| 2 | FiscalPeriodMonth | tinyint | Y |  |  |
| 3 | Inventory_Status | nvarchar(50) | Y |  |  |
| 4 | Division | nvarchar(50) | Y |  |  |
| 5 | Department | nvarchar(50) | Y |  |  |
| 6 | SeasonParentCode | nvarchar(50) | Y |  |  |
| 7 | Received_Qty | float | Y |  |  |
| 8 | Received_Amt | float | Y |  |  |

### dbo.Receipt_History_Sept2019

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | FiscalYear | smallint | Y |  |  |
| 2 | FiscalPeriodMonth | tinyint | Y |  |  |
| 3 | Inventory_Status | nvarchar(50) | Y |  |  |
| 4 | Division | nvarchar(50) | Y |  |  |
| 5 | Department | nvarchar(50) | Y |  |  |
| 6 | SeasonParentCode | nvarchar(50) | Y |  |  |
| 7 | Received_Qty | float | Y |  |  |
| 8 | Received_Amt | float | Y |  |  |

### dbo.SKU_Store_Demand_History

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Channel | nvarchar(20) | N |  |  |
| 2 | sku | varchar(50) | N |  |  |
| 3 | offer | nvarchar(41) | N |  |  |
| 4 | LocationID | nvarchar(50) | Y |  |  |
| 5 | order_month | tinyint | Y |  |  |
| 6 | order_year | smallint | Y |  |  |
| 7 | order_week | tinyint | Y |  |  |
| 8 | CalendarDate | date | Y |  |  |
| 9 | Total_qty | numeric(38,16) | Y |  |  |
| 10 | Total_Demand_Amt | numeric(38,16) | Y |  |  |
| 11 | Total_Demand_Price_Amt | numeric(38,16) | Y |  |  |
| 12 | Total_Demand_Cost_Amt | numeric(38,16) | Y |  |  |
| 13 | Total_Demand_Margin_Dollar_Amt | numeric(38,16) | Y |  |  |
| 14 | Total_Return_Qty | numeric(38,16) | Y |  |  |
| 15 | Total_Return_Amt | numeric(38,16) | Y |  |  |
| 16 | Total_Net_Qty | numeric(38,16) | Y |  |  |
| 17 | Total_Net_Amt | numeric(38,16) | Y |  |  |
| 18 | Total_Net_Cost_Amt | numeric(38,16) | Y |  |  |
| 19 | Total_Net_Margin_Dollar_Amt | numeric(38,16) | Y |  |  |

### dbo.SalesDatabyMonth

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | merchplanningchannel | nvarchar(50) | Y |  |  |
| 2 | SeasonParentCode | nvarchar(50) | Y |  |  |
| 3 | division | nvarchar(50) | Y |  |  |
| 4 | Department | nvarchar(50) | Y |  |  |
| 5 | Fiscal_Year | smallint | Y |  |  |
| 6 | Fiscal_Month | tinyint | Y |  |  |
| 7 | Demand_Qty | int | Y |  |  |
| 8 | Retail_Amt | float | Y |  |  |
| 9 | Cost_Amt | float | Y |  |  |

### dbo.Seasonal_Profile_Index_Library

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | ModelID | nvarchar(50) | Y |  |  |
| 2 | Fiscal_Week | bigint | N |  |  |
| 3 | Index_Value | float | Y |  |  |

### dbo.Seasonal_Profile_Library

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | ModelID | nvarchar(50) | N |  |  |
| 2 | Status | nvarchar(50) | Y |  |  |
| 3 | Name | nvarchar(200) | Y |  |  |
| 4 | Channel | nvarchar(50) | Y |  |  |
| 5 | Division | nvarchar(50) | Y |  |  |
| 6 | Department | nvarchar(50) | Y |  |  |
| 7 | Class | nvarchar(50) | Y |  |  |
| 8 | Life Weeks Range | nvarchar(50) | Y |  |  |
| 9 | Order Month | nvarchar(50) | Y |  |  |
| 10 | Notes | nvarchar(500) | Y |  |  |
| 11 | Last Updated Date | datetime | Y |  |  |
| 12 | Last Updated Userid | nvarchar(50) | Y |  |  |

### dbo.Seasonal_Profile_Sales

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Channel | nvarchar(50) | Y |  |  |
| 2 | division | nvarchar(50) | Y |  |  |
| 3 | department | nvarchar(50) | Y |  |  |
| 4 | Class | nvarchar(50) | Y |  |  |
| 5 | Style | nvarchar(50) | Y |  |  |
| 6 | OfferID | nvarchar(50) | Y |  |  |
| 7 | Life Weeks Range | varchar(61) | N |  |  |
| 8 | order_month | int | Y |  |  |
| 9 | offer_count | int | Y |  |  |
| 10 | 1 | numeric(38,16) | Y |  |  |
| 11 | 2 | numeric(38,16) | Y |  |  |
| 12 | 3 | numeric(38,16) | Y |  |  |
| 13 | 4 | numeric(38,16) | Y |  |  |
| 14 | 5 | numeric(38,16) | Y |  |  |
| 15 | 6 | numeric(38,16) | Y |  |  |
| 16 | 7 | numeric(38,16) | Y |  |  |
| 17 | 8 | numeric(38,16) | Y |  |  |
| 18 | 9 | numeric(38,16) | Y |  |  |
| 19 | 10 | numeric(38,16) | Y |  |  |
| 20 | 11 | numeric(38,16) | Y |  |  |
| 21 | 12 | numeric(38,16) | Y |  |  |
| 22 | 13 | numeric(38,16) | Y |  |  |
| 23 | 14 | numeric(38,16) | Y |  |  |
| 24 | 15 | numeric(38,16) | Y |  |  |
| 25 | 16 | numeric(38,16) | Y |  |  |
| 26 | 17 | numeric(38,16) | Y |  |  |
| 27 | 18 | numeric(38,16) | Y |  |  |
| 28 | 19 | numeric(38,16) | Y |  |  |
| 29 | 20 | numeric(38,16) | Y |  |  |
| 30 | 21 | numeric(38,16) | Y |  |  |
| 31 | 22 | numeric(38,16) | Y |  |  |
| 32 | 23 | numeric(38,16) | Y |  |  |
| 33 | 24 | numeric(38,16) | Y |  |  |
| 34 | 25 | numeric(38,16) | Y |  |  |
| 35 | 26 | numeric(38,16) | Y |  |  |
| 36 | 27 | numeric(38,16) | Y |  |  |
| 37 | 28 | numeric(38,16) | Y |  |  |
| 38 | 29 | numeric(38,16) | Y |  |  |
| 39 | 30 | numeric(38,16) | Y |  |  |
| 40 | 31 | numeric(38,16) | Y |  |  |
| 41 | 32 | numeric(38,16) | Y |  |  |
| 42 | 33 | numeric(38,16) | Y |  |  |
| 43 | 34 | numeric(38,16) | Y |  |  |
| 44 | 35 | numeric(38,16) | Y |  |  |
| 45 | 36 | numeric(38,16) | Y |  |  |
| 46 | 37 | numeric(38,16) | Y |  |  |
| 47 | 38 | numeric(38,16) | Y |  |  |
| 48 | 39 | numeric(38,16) | Y |  |  |
| 49 | 40 | numeric(38,16) | Y |  |  |
| 50 | 41 | numeric(38,16) | Y |  |  |
| 51 | 42 | numeric(38,16) | Y |  |  |
| 52 | 43 | numeric(38,16) | Y |  |  |
| 53 | 44 | numeric(38,16) | Y |  |  |
| 54 | 45 | numeric(38,16) | Y |  |  |
| 55 | 46 | numeric(38,16) | Y |  |  |
| 56 | 47 | numeric(38,16) | Y |  |  |
| 57 | 48 | numeric(38,16) | Y |  |  |
| 58 | 49 | numeric(38,16) | Y |  |  |
| 59 | 50 | numeric(38,16) | Y |  |  |
| 60 | 51 | numeric(38,16) | Y |  |  |
| 61 | 52 | numeric(38,16) | Y |  |  |

### dbo.Seasonal_Profile_Sales_old

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Channel | nvarchar(50) | Y |  |  |
| 2 | division | nvarchar(50) | Y |  |  |
| 3 | department | nvarchar(50) | Y |  |  |
| 4 | Class | nvarchar(50) | Y |  |  |
| 5 | Life Weeks Range | varchar(61) | N |  |  |
| 6 | order_month | int | Y |  |  |
| 7 | offer_count | int | Y |  |  |
| 8 | 1 | numeric(38,16) | Y |  |  |
| 9 | 2 | numeric(38,16) | Y |  |  |
| 10 | 3 | numeric(38,16) | Y |  |  |
| 11 | 4 | numeric(38,16) | Y |  |  |
| 12 | 5 | numeric(38,16) | Y |  |  |
| 13 | 6 | numeric(38,16) | Y |  |  |
| 14 | 7 | numeric(38,16) | Y |  |  |
| 15 | 8 | numeric(38,16) | Y |  |  |
| 16 | 9 | numeric(38,16) | Y |  |  |
| 17 | 10 | numeric(38,16) | Y |  |  |
| 18 | 11 | numeric(38,16) | Y |  |  |
| 19 | 12 | numeric(38,16) | Y |  |  |
| 20 | 13 | numeric(38,16) | Y |  |  |
| 21 | 14 | numeric(38,16) | Y |  |  |
| 22 | 15 | numeric(38,16) | Y |  |  |
| 23 | 16 | numeric(38,16) | Y |  |  |
| 24 | 17 | numeric(38,16) | Y |  |  |
| 25 | 18 | numeric(38,16) | Y |  |  |
| 26 | 19 | numeric(38,16) | Y |  |  |
| 27 | 20 | numeric(38,16) | Y |  |  |
| 28 | 21 | numeric(38,16) | Y |  |  |
| 29 | 22 | numeric(38,16) | Y |  |  |
| 30 | 23 | numeric(38,16) | Y |  |  |
| 31 | 24 | numeric(38,16) | Y |  |  |
| 32 | 25 | numeric(38,16) | Y |  |  |
| 33 | 26 | numeric(38,16) | Y |  |  |
| 34 | 27 | numeric(38,16) | Y |  |  |
| 35 | 28 | numeric(38,16) | Y |  |  |
| 36 | 29 | numeric(38,16) | Y |  |  |
| 37 | 30 | numeric(38,16) | Y |  |  |
| 38 | 31 | numeric(38,16) | Y |  |  |
| 39 | 32 | numeric(38,16) | Y |  |  |
| 40 | 33 | numeric(38,16) | Y |  |  |
| 41 | 34 | numeric(38,16) | Y |  |  |
| 42 | 35 | numeric(38,16) | Y |  |  |
| 43 | 36 | numeric(38,16) | Y |  |  |
| 44 | 37 | numeric(38,16) | Y |  |  |
| 45 | 38 | numeric(38,16) | Y |  |  |
| 46 | 39 | numeric(38,16) | Y |  |  |
| 47 | 40 | numeric(38,16) | Y |  |  |
| 48 | 41 | numeric(38,16) | Y |  |  |
| 49 | 42 | numeric(38,16) | Y |  |  |
| 50 | 43 | numeric(38,16) | Y |  |  |
| 51 | 44 | numeric(38,16) | Y |  |  |
| 52 | 45 | numeric(38,16) | Y |  |  |
| 53 | 46 | numeric(38,16) | Y |  |  |
| 54 | 47 | numeric(38,16) | Y |  |  |
| 55 | 48 | numeric(38,16) | Y |  |  |
| 56 | 49 | numeric(38,16) | Y |  |  |
| 57 | 50 | numeric(38,16) | Y |  |  |
| 58 | 51 | numeric(38,16) | Y |  |  |
| 59 | 52 | numeric(38,16) | Y |  |  |

### dbo.Size_Distribution_Library

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Model_ID | nvarchar(50) | Y |  |  |
| 2 | Name | nvarchar(200) | Y |  |  |
| 3 | Division | nvarchar(50) | Y |  |  |
| 4 | Department | nvarchar(50) | Y |  |  |
| 5 | Class | nvarchar(50) | Y |  |  |
| 6 | sizeid | nvarchar(50) | Y |  |  |
| 7 | DirectAvgQtyPerWeek | float | Y |  |  |
| 8 | DirectPercentOfSales | float | Y |  |  |
| 9 | RetailAvgQtyPerWeek | float | Y |  |  |
| 10 | RetailPercentOfSales | float | Y |  |  |
| 11 | OutletAvgQtyPerWeek | float | Y |  |  |
| 12 | OutletPercentOfSales | float | Y |  |  |
| 13 | TotalAvgQtyPerWeek | float | Y |  |  |
| 14 | TotalPercentOfSales | float | Y |  |  |
| 15 | Notes | nvarchar(500) | Y |  |  |
| 16 | Last Updated Date | datetime | Y |  |  |
| 17 | Last Update User | nvarchar(50) | Y |  |  |

### dbo.Size_Distribution_Library_Backup_20200317

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Model_ID | nvarchar(50) | Y |  |  |
| 2 | Name | nvarchar(200) | Y |  |  |
| 3 | Division | nvarchar(50) | Y |  |  |
| 4 | Department | nvarchar(50) | Y |  |  |
| 5 | Class | nvarchar(50) | Y |  |  |
| 6 | sizeid | nvarchar(50) | Y |  |  |
| 7 | DirectAvgQtyPerWeek | float | Y |  |  |
| 8 | DirectPercentOfSales | float | Y |  |  |
| 9 | RetailAvgQtyPerWeek | float | Y |  |  |
| 10 | RetailPercentOfSales | float | Y |  |  |
| 11 | TotalAvgQtyPerWeek | float | Y |  |  |
| 12 | TotalPercentOfSales | float | Y |  |  |
| 13 | Notes | nvarchar(500) | Y |  |  |
| 14 | Last Updated Date | datetime | Y |  |  |
| 15 | Last Update User | nvarchar(50) | Y |  |  |

### dbo.Size_Distribution_Sales

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Channel | varchar(50) | Y |  |  |
| 2 | Division | varchar(50) | Y |  |  |
| 3 | Department | varchar(50) | Y |  |  |
| 4 | Class | varchar(50) | Y |  |  |
| 5 | Style | varchar(50) | Y |  |  |
| 6 | Offer | varchar(50) | Y |  |  |
| 7 | SizeID | varchar(50) | Y |  |  |
| 8 | Total_Qty | float | Y |  |  |
| 9 | Nbr of Weeks | float | Y |  |  |
| 10 | Avg Qty Per Week | float | Y |  |  |

### dbo.Size_Distribution_Sales_old

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Channel | varchar(50) | Y |  |  |
| 2 | Division | varchar(50) | Y |  |  |
| 3 | Department | varchar(50) | Y |  |  |
| 4 | Class | varchar(50) | Y |  |  |
| 5 | SizeID | varchar(50) | Y |  |  |
| 6 | Total_Qty | float | Y |  |  |
| 7 | Nbr of Weeks | float | Y |  |  |
| 8 | Avg Qty Per Week | float | Y |  |  |

### dbo.Size_Range_Groups

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | ModelID | nvarchar(50) | Y |  |  |
| 2 | Division | nvarchar(50) | Y |  |  |
| 3 | Department | nvarchar(50) | Y |  |  |
| 4 | SizeRange | nvarchar(50) | Y |  |  |
| 5 | sizeid | nvarchar(50) | Y |  |  |

### dbo.Sku_Store_Allocation_Control

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | SKU | nvarchar(50) | Y |  |  |
| 2 | StoreID | nvarchar(50) | Y |  |  |
| 3 | OUTL | float | Y |  |  |
| 4 | Start_Alloc_Date | date | Y |  |  |
| 5 | Stop_Alloc_Date | date | Y |  |  |
| 6 | Intended_Weeks | int | Y |  |  |
| 7 | Completed_Weeks | int | Y |  |  |
| 8 | Progression_Factor | float | Y |  |  |
| 9 | Raw_WOS_Forecast | float | Y |  |  |
| 10 | Net_WOS_Forecast | float | Y |  |  |
| 11 | WOS_Target | float | Y |  |  |
| 12 | Last_Updated_Date | datetime | Y |  |  |

### dbo.Store_Distribution_LIbrary

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Model_ID | nvarchar(50) | Y |  |  |
| 2 | Name | nvarchar(200) | Y |  |  |
| 3 | Division | nvarchar(50) | Y |  |  |
| 4 | Department | nvarchar(50) | Y |  |  |
| 5 | Class | nvarchar(50) | Y |  |  |
| 6 | storeid | smallint | Y |  |  |
| 7 | Percent_Contribution | float | Y |  |  |
| 8 | Notes | nvarchar(500) | Y |  |  |
| 9 | Last_Updated_Date | date | Y |  |  |
| 10 | Last_Update_User | nvarchar(50) | Y |  |  |

### dbo.Store_Distribution_Sales

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Division | nvarchar(50) | Y |  |  |
| 2 | Department | nvarchar(50) | Y |  |  |
| 3 | Class | nvarchar(50) | Y |  |  |
| 4 | StoreID | nvarchar(50) | Y |  |  |
| 5 | FiscalMonth | tinyint | Y |  |  |
| 6 | Total_Sales_Qty | float | Y |  |  |

### dbo.Store_Master_Control

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | StoreID | nvarchar(50) | Y |  |  |
| 2 | Name | nvarchar(50) | Y |  |  |
| 3 | TransitDays | int | Y |  |  |
| 4 | ModeOfDelivery | nvarchar(50) | Y |  |  |
| 5 | Tier | nvarchar(50) | Y |  |  |
| 6 | Channel | nchar(10) | Y |  |  |
| 7 | Status | nchar(10) | Y |  |  |
| 8 | Last_Updated_Date | datetime | Y |  |  |
| 9 | Last_Update_User | nvarchar(50) | Y |  |  |

### dbo.Store_Master_Groups_Control

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | GroupName | nvarchar(50) | Y |  |  |
| 2 | StoreID | nvarchar(50) | Y |  |  |

### dbo.autoload_offer_average_apw

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | MODEL_ID | nvarchar(53) | N |  |  |
| 2 | NAME | nvarchar(41) | N |  |  |
| 3 | DIVISION | nvarchar(500) | Y |  |  |
| 4 | DEPARTMENT | nvarchar(500) | Y |  |  |
| 5 | DIRECT AVG QTY PER WEEK | float | Y |  |  |
| 6 | RETAIL AVG QTY PER WEEK | float | Y |  |  |
| 7 | NOTES | varchar(10) | N |  |  |
| 8 | LAST UPDATED DATE | datetime | N |  |  |
| 9 | LAST UPDATE USER | varchar(11) | N |  |  |

### dbo.channel_offer_sku_store_control_test

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | channel | nvarchar(50) | N |  |  |
| 2 | offerid | nvarchar(50) | N |  |  |
| 3 | sku | nvarchar(50) | N |  |  |
| 4 | locationid | smallint | N |  |  |
| 5 | Current_APW_Units | float | Y |  |  |
| 6 | Updated_APW_Units | float | Y |  |  |

### dbo.channel_offer_sku_store_forecast_test

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Channel | nvarchar(50) | N |  |  |
| 2 | OfferID | nvarchar(50) | N |  |  |
| 3 | SKU | nvarchar(50) | N |  |  |
| 4 | LocationID | smallint | N |  |  |
| 5 | CalendarDate | date | Y |  |  |
| 6 | FiscalWeekOfYear | tinyint | Y |  |  |
| 7 | Base_Sales_Unit_Forecast | float | Y |  |  |
| 8 | Initial_Unit_Forecast | float | Y |  |  |

### dbo.offer_control_table_backup

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Channel | nvarchar(50) | Y |  |  |
| 2 | OfferID | nvarchar(50) | Y |  |  |
| 3 | Division | nvarchar(50) | Y |  |  |
| 4 | Department | nvarchar(50) | Y |  |  |
| 5 | SeasonCode | nvarchar(50) | Y |  |  |
| 6 | Unit_Cost | float | Y |  |  |
| 7 | TicketedRetail | float | Y |  |  |
| 8 | Planning_APW | float | Y |  |  |
| 9 | Initial_APW_Units | float | Y |  |  |
| 10 | Initialized_Date | date | Y |  |  |
| 11 | Target_Sell_Thru | float | Y |  |  |
| 12 | Start_Date | date | Y |  |  |
| 13 | Stop_Date | date | Y |  |  |
| 14 | Intended_Weeks | float | Y |  |  |
| 15 | Size_Range_Model | varchar(50) | Y |  |  |
| 16 | Seasonal_Profile_Model | varchar(50) | Y |  |  |
| 17 | Store_Distribution_Model | varchar(50) | Y |  |  |
| 18 | GroupName | nvarchar(50) | Y |  |  |
| 19 | Returns_Model | float | Y |  |  |
| 20 | Markdown_Model | varchar(50) | Y |  |  |
| 21 | Out_Of_Stock_Date | date | Y |  |  |
| 22 | Retrend_Forecast | nvarchar(5) | Y |  |  |
| 23 | Retrend_Model | nvarchar(25) | Y |  |  |
| 24 | Replenish_Flag | nvarchar(5) | Y |  |  |
| 25 | Start_Replenish_Date | date | Y |  |  |
| 26 | Stop_Replenish_Date | date | Y |  |  |
| 27 | Replenish_Target_WOS | float | Y |  |  |
| 28 | Replenish_Lead_Time | float | Y |  |  |
| 29 | Replenish_Cycle_Time | float | Y |  |  |
| 30 | Initial_Allocation_WOS | float | Y |  |  |
| 31 | AutoAllocate_Flag | nvarchar(50) | Y |  |  |
| 32 | Start_Allocate_Date | date | Y |  |  |
| 33 | Stop_Allocate_Date | date | Y |  |  |
| 34 | Allocation_Max_WOS | float | Y |  |  |
| 35 | Allocation_Max_Limit_Qty | float | Y |  |  |
| 36 | Allocation_Avail_Inventory_Threshold | float | Y |  |  |
| 37 | Allocation_Cutoff_Threshold | float | Y |  |  |
| 38 | Last Updated Date | datetime | Y |  |  |
| 39 | Last Update User | varchar(50) | Y |  |  |
| 40 | Status | varchar(50) | Y |  |  |
| 41 | Current_APW_Units | float | Y |  |  |
| 42 | Updated_APW_Units | float | Y |  |  |
| 43 | Supplier_Min_Order_Qty | float | Y |  |  |
| 44 | Initial_Est_Purchase_Units | int | Y |  |  |
| 45 | Final_Est_Purchase_Units | int | Y |  |  |
| 46 | Notes | nvarchar(1000) | Y |  |  |
| 47 | Auto_Create_Receipt_Plan | nvarchar(50) | Y |  |  |
| 48 | Master_Forecast_Flag | nvarchar(50) | Y |  |  |
| 49 | ColorName | varchar(50) | Y |  |  |
| 50 | On_Order_Auto_Receipt_Days | numeric(18,0) | Y |  |  |
| 51 | itemname | varchar(50) | Y |  |  |
| 52 | Initial_Order_Flow_Type | varchar(50) | Y |  |  |
| 53 | Initial_Order_Percent_Qty | float | Y |  |  |
| 54 | Balance_Order_Flow_Type | varchar(50) | Y |  |  |
| 55 | Balance_Order_Weeks_From_Initial | int | Y |  |  |
| 56 | Min_Balance_To_Flow_Qty | int | Y |  |  |
| 57 | Use_Offer_Min_Size_Qtys_Flag | varchar(3) | Y |  |  |

### dbo.offer_control_table_backup_12142019

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | Channel | nvarchar(50) | Y |  |  |
| 2 | OfferID | nvarchar(50) | Y |  |  |
| 3 | Division | nvarchar(50) | Y |  |  |
| 4 | Department | nvarchar(50) | Y |  |  |
| 5 | SeasonCode | nvarchar(50) | Y |  |  |
| 6 | Unit_Cost | float | Y |  |  |
| 7 | TicketedRetail | float | Y |  |  |
| 8 | Planning_APW | float | Y |  |  |
| 9 | Initial_APW_Units | float | Y |  |  |
| 10 | Initialized_Date | date | Y |  |  |
| 11 | Target_Sell_Thru | float | Y |  |  |
| 12 | Start_Date | date | Y |  |  |
| 13 | Stop_Date | date | Y |  |  |
| 14 | Intended_Weeks | float | Y |  |  |
| 15 | Size_Range_Model | varchar(50) | Y |  |  |
| 16 | Seasonal_Profile_Model | varchar(50) | Y |  |  |
| 17 | Store_Distribution_Model | varchar(50) | Y |  |  |
| 18 | GroupName | nvarchar(50) | Y |  |  |
| 19 | Returns_Model | float | Y |  |  |
| 20 | Markdown_Model | varchar(50) | Y |  |  |
| 21 | Out_Of_Stock_Date | date | Y |  |  |
| 22 | Retrend_Forecast | nvarchar(5) | Y |  |  |
| 23 | Retrend_Model | nvarchar(25) | Y |  |  |
| 24 | Replenish_Flag | nvarchar(5) | Y |  |  |
| 25 | Start_Replenish_Date | date | Y |  |  |
| 26 | Stop_Replenish_Date | date | Y |  |  |
| 27 | Replenish_Target_WOS | float | Y |  |  |
| 28 | Replenish_Lead_Time | float | Y |  |  |
| 29 | Replenish_Cycle_Time | float | Y |  |  |
| 30 | Initial_Allocation_WOS | float | Y |  |  |
| 31 | AutoAllocate_Flag | nvarchar(50) | Y |  |  |
| 32 | Start_Allocate_Date | date | Y |  |  |
| 33 | Stop_Allocate_Date | date | Y |  |  |
| 34 | Allocation_Max_WOS | float | Y |  |  |
| 35 | Allocation_Max_Limit_Qty | float | Y |  |  |
| 36 | Allocation_Avail_Inventory_Threshold | float | Y |  |  |
| 37 | Allocation_Cutoff_Threshold | float | Y |  |  |
| 38 | Last Updated Date | datetime | Y |  |  |
| 39 | Last Update User | varchar(50) | Y |  |  |
| 40 | Status | varchar(50) | Y |  |  |
| 41 | Current_APW_Units | float | Y |  |  |
| 42 | Updated_APW_Units | float | Y |  |  |
| 43 | Supplier_Min_Order_Qty | float | Y |  |  |
| 44 | Initial_Est_Purchase_Units | int | Y |  |  |
| 45 | Final_Est_Purchase_Units | int | Y |  |  |
| 46 | Notes | nvarchar(1000) | Y |  |  |
| 47 | Auto_Create_Receipt_Plan | nvarchar(50) | Y |  |  |
| 48 | Master_Forecast_Flag | nvarchar(50) | Y |  |  |
| 49 | ColorName | varchar(50) | Y |  |  |
| 50 | On_Order_Auto_Receipt_Days | numeric(18,0) | Y |  |  |
| 51 | itemname | varchar(50) | Y |  |  |

### dbo.offer_rounded_forecast_staging

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | channel | nvarchar(50) | Y |  |  |
| 2 | division | nvarchar(50) | Y |  |  |
| 3 | department | nvarchar(50) | Y |  |  |
| 4 | offerid | nvarchar(50) | Y |  |  |
| 5 | sizeid | nvarchar(50) | Y |  |  |
| 6 | balanced_size_percent | float | Y |  |  |
| 7 | store_distribution_model | varchar(50) | Y |  |  |
| 8 | start_date | date | Y |  |  |
| 9 | stop_date | date | Y |  |  |
| 10 | target_sell_thru | float | Y |  |  |
| 11 | final_offer_size_min | float | Y |  |  |
| 12 | offer_extra_qty | float | Y |  |  |
| 13 | Final_Initial_Order_Qty | float | Y |  |  |

### dbo.offerid_to_reset_planned_receipts_backup

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | OfferID | nvarchar(50) | N |  |  |
| 2 | Import_Date | date | N |  |  |

### dbo.on_order_backup

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | CalendarDate | date | Y |  |  |
| 2 | ActualDeliveryDate | date | Y |  |  |
| 3 | Offer | nvarchar(50) | Y |  |  |
| 4 | SKU | nvarchar(50) | Y |  |  |
| 5 | Total_Line_Amount | float | Y |  |  |
| 6 | Total_Ordered_Qty | float | Y |  |  |
| 7 | Total_Remaining_Order_Qty | float | Y |  |  |
| 8 | Total_Remaining_Order_Amt | float | Y |  |  |
| 9 | FiscalYear | smallint | Y |  |  |
| 10 | FiscalWeek | tinyint | Y |  |  |

### dbo.sku_hist

| # | Column | Type | Nullable | PK | References |
| --- | --- | --- | --- | --- | --- |
| 1 | calendar_date | date | Y |  |  |
| 2 | channel | nvarchar(20) | Y |  |  |
| 3 | offerid | nvarchar(50) | Y |  |  |
| 4 | sku | nvarchar(50) | Y |  |  |
| 5 | avail_oh | numeric(18,0) | Y |  |  |
| 6 | total_demand_amt | numeric(38,16) | Y |  |  |
| 7 | total_net_qty | numeric(38,16) | Y |  |  |

## Relationship Summary

- Indexes cataloged: `21`
- Foreign-key column mappings cataloged: `0`
