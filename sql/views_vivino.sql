-- Thin flattening views over the raw tables: pure unpacking of JSON paths into
-- readable columns, no modelling decisions. Re-fetched entities are deduped to
-- the latest fetched_at per natural id; nothing else is conformed.
-- type_id mapping: only 1=red and 2=white are verified; everything else stays raw.

CREATE OR REPLACE VIEW v_wineries AS
WITH catalog_wines AS (
    SELECT fetched_at, unnest(payload.wines) AS wine
    FROM raw_vivino_winery_wines
),

wineries_extracted AS (
    SELECT DISTINCT
        wine.winery.id                            AS winery_id,
        wine.winery.name                          AS winery_name,
        wine.winery.seo_name                      AS winery_seo_name,
        wine.winery.statistics.ratings_average    AS ratings_average,
        wine.winery.statistics.ratings_count      AS ratings_count,
        wine.winery.statistics.labels_count       AS labels_count,
        wine.winery.statistics.wines_count        AS wines_count,
        fetched_at
    FROM catalog_wines
)

SELECT *
FROM wineries_extracted
QUALIFY row_number() OVER (PARTITION BY winery_id ORDER BY fetched_at DESC) = 1;


CREATE OR REPLACE VIEW v_wines AS
WITH catalog_wines AS (
    SELECT fetched_at, unnest(payload.wines) AS wine
    FROM raw_vivino_winery_wines
)

SELECT
    wine.id                              AS wine_id,
    wine.name                            AS wine_name,
    wine.seo_name                        AS wine_seo_name,
    wine.type_id                         AS type_id,
    CASE wine.type_id
        WHEN 1 THEN 'red'
        WHEN 2 THEN 'white'
    END                                  AS wine_type,
    wine.is_natural                      AS is_natural,
    wine.statistics.ratings_average      AS ratings_average,
    wine.statistics.ratings_count        AS ratings_count,
    wine.statistics.labels_count         AS labels_count,
    wine.statistics.vintages_count       AS vintages_count,
    wine.region.id                       AS region_id,
    wine.region.name                     AS region_name,
    wine.region.country.code             AS country_code,
    wine.winery.id                       AS winery_id,
    wine.winery.name                     AS winery_name,
    fetched_at
FROM catalog_wines
QUALIFY row_number() OVER (PARTITION BY wine.id ORDER BY fetched_at DESC) = 1;


CREATE OR REPLACE VIEW v_vintages AS
WITH explore_matches AS (
    SELECT fetched_at, unnest(payload.explore_vintage.matches) AS m
    FROM raw_vivino_explore
),

vintages_from_explore AS (
    SELECT
        'explore'                            AS source,
        m.vintage.id                         AS vintage_id,
        m.vintage.name                       AS vintage_name,
        -- Vivino encodes non-vintage as 0 or '' -> NULL
        nullif(try_cast(m.vintage.year AS INTEGER), 0) AS year,
        m.vintage.statistics.ratings_average AS ratings_average,
        m.vintage.statistics.ratings_count   AS ratings_count,
        m.vintage.wine.id                    AS wine_id,
        m.vintage.wine.name                  AS wine_name,
        m.vintage.wine.winery.id             AS winery_id,
        m.vintage.wine.winery.name           AS winery_name,
        fetched_at
    FROM explore_matches
),

winery_vintage_entries AS (
    SELECT fetched_at, params, unnest(payload.vintages) AS v
    FROM raw_vivino_winery_vintages
),

vintages_from_winery AS (
    SELECT
        'winery_vintages'              AS source,
        v.id                           AS vintage_id,
        v.name                         AS vintage_name,
        -- Vivino encodes non-vintage as 0 or '' -> NULL
        nullif(try_cast(v.year AS INTEGER), 0) AS year,
        v.statistics.ratings_average   AS ratings_average,
        v.statistics.ratings_count     AS ratings_count,
        v.wine.id                      AS wine_id,
        v.wine.name                    AS wine_name,
        params.winery_id               AS winery_id,
        NULL                           AS winery_name,
        fetched_at
    FROM winery_vintage_entries
)

SELECT *
FROM (
    SELECT * FROM vintages_from_explore
    UNION ALL BY NAME
    SELECT * FROM vintages_from_winery
)
QUALIFY row_number() OVER (PARTITION BY source, vintage_id ORDER BY fetched_at DESC) = 1;


CREATE OR REPLACE VIEW v_reviews AS
WITH review_entries AS (
    SELECT fetched_at, params, unnest(payload.reviews) AS r
    FROM raw_vivino_reviews
)

SELECT
    r.id                                 AS review_id,
    coalesce(r.vintage.wine.id, params.wine_id) AS wine_id,
    r.rating                             AS rating,
    r.note                               AS note,
    r.language                           AS language,
    r.created_at                         AS created_at,
    r.vintage.id                         AS vintage_id,
    -- Vivino encodes non-vintage as 0 or '' -> NULL
    nullif(try_cast(r.vintage.year AS INTEGER), 0) AS vintage_year,
    r.user.id                            AS user_id,
    r.user.alias                         AS user_alias,
    r.activity.statistics.likes_count    AS likes_count,
    fetched_at
FROM review_entries
QUALIFY row_number() OVER (PARTITION BY r.id ORDER BY fetched_at DESC) = 1;