-- plan 4f6ab03f7218  main.sales.orders  v1 -> v2
-- verdict PROCEED

-- [preflight] enable_type_widening enable Delta type widening
ALTER TABLE main.sales.orders SET TBLPROPERTIES ('delta.enableTypeWidening' = 'true');

-- [expand] add_columns add 3 nullable column(s)
ALTER TABLE main.sales.orders ADD COLUMNS (`promo_code` STRING COMMENT 'schema_evolver 4f6ab03f7218', `customer_id` STRING COMMENT 'schema_evolver 4f6ab03f7218', `_rescued_data` STRING COMMENT 'schema_evolver 4f6ab03f7218');

-- [expand] widen_amount widen amount float to double
ALTER TABLE main.sales.orders ALTER COLUMN `amount` TYPE DOUBLE;

-- [expand] widen_loyalty_points widen loyalty_points int to long
ALTER TABLE main.sales.orders ALTER COLUMN `loyalty_points` TYPE BIGINT;

-- [backfill] backfill_customer_id copy history cust_id into customer_id
UPDATE main.sales.orders SET `customer_id` = CAST(`cust_id` AS STRING) WHERE `customer_id` IS NULL AND `cust_id` IS NOT NULL;

-- [switch] switch_view repoint contract view (old names kept as aliases)
CREATE OR REPLACE VIEW main.sales.orders_v AS SELECT `order_id`, COALESCE(`customer_id`, CAST(`cust_id` AS STRING)) AS `customer_id`, COALESCE(`customer_id`, CAST(`cust_id` AS STRING)) AS `cust_id`, `order_ts`, `qty`, `amount`, `status`, `region`, `loyalty_points`, `promo_code` FROM main.sales.orders;

-- [contract] enable_column_mapping column mapping is required for DROP COLUMNS; may affect streaming reads and CDF (DEFERRED, run only after consumers migrate)
ALTER TABLE main.sales.orders SET TBLPROPERTIES ('delta.columnMapping.mode' = 'name');

-- [contract] relax_not_null_cust_id drop NOT NULL on cust_id before writers stop dual writing (DEFERRED, run only after consumers migrate)
ALTER TABLE main.sales.orders ALTER COLUMN `cust_id` DROP NOT NULL;

-- [contract] drop_old_columns drop renamed source columns (DEFERRED, run only after consumers migrate)
ALTER TABLE main.sales.orders DROP COLUMNS (`cust_id`);

-- [contract] drop_view_aliases remove old-name aliases from view (DEFERRED, run only after consumers migrate)
CREATE OR REPLACE VIEW main.sales.orders_v AS SELECT `order_id`, `customer_id`, `order_ts`, `qty`, `amount`, `status`, `region`, `loyalty_points`, `promo_code` FROM main.sales.orders;
