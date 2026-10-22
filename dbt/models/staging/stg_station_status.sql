{{ config(materialized="view") }}

SELECT
    capture_id,
    station_id,
    received_at,
    SAFE_CAST(
        JSON_VALUE(payload, '$.num_bikes_available') AS INT64
    ) AS bikes_available,
    SAFE_CAST(
        JSON_VALUE(payload, '$.num_docks_available') AS INT64
    ) AS docks_available
FROM {{ source('divvy', 'station_records') }} AS records
WHERE records.feed_name = 'station_status'
  AND records._PARTITIONDATE = CURRENT_DATE()