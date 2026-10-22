{{ config(materialized="view") }}

SELECT
    capture_id,
    station_id,
    received_at,
    JSON_VALUE(payload, '$.name') AS station_name,
    SAFE_CAST(JSON_VALUE(payload, '$.lat') AS FLOAT64) AS latitude,
    SAFE_CAST(JSON_VALUE(payload, '$.lon') AS FLOAT64) AS longitude,
    SAFE_CAST(JSON_VALUE(payload, '$.capacity') AS INT64) AS capacity
FROM {{ source('divvy', 'station_records') }} AS records
WHERE records.feed_name = 'station_information'
  AND records._PARTITIONDATE = CURRENT_DATE()