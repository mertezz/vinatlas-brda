-- Raw Vivino tables: 1:1 with the captured JSONL response envelopes (store-raw-then-parse).
-- One row = one HTTP response: fetched_at, endpoint, params, http_status, payload (full unparsed JSON).
-- Rebuilt idempotently by build_db.py; paths are relative to the project root.

CREATE OR REPLACE TABLE raw_vivino_regions AS
SELECT *
FROM read_json_auto('data/raw/vivino/regions/*.jsonl',
                    format = 'newline_delimited', union_by_name = true, sample_size = -1);

CREATE OR REPLACE TABLE raw_vivino_explore AS
SELECT *
FROM read_json_auto('data/raw/vivino/explore/*.jsonl',
                    format = 'newline_delimited', union_by_name = true, sample_size = -1);

CREATE OR REPLACE TABLE raw_vivino_winery_wines AS
SELECT *
FROM read_json_auto('data/raw/vivino/winery_wines/*.jsonl',
                    format = 'newline_delimited', union_by_name = true, sample_size = -1);

CREATE OR REPLACE TABLE raw_vivino_winery_vintages AS
SELECT *
FROM read_json_auto('data/raw/vivino/winery_vintages/*.jsonl',
                    format = 'newline_delimited', union_by_name = true, sample_size = -1);

CREATE OR REPLACE TABLE raw_vivino_reviews AS
SELECT *
FROM read_json_auto('data/raw/vivino/reviews/*.jsonl',
                    format = 'newline_delimited', union_by_name = true, sample_size = -1);