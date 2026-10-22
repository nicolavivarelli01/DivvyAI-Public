{{ config(materialized="view") }}

WITH captures AS (
    SELECT
        feed_name,
        capture_id,
        MAX(received_at) AS capture_received_at,
        COUNT(*) AS station_record_count
    FROM {{ source('divvy', 'station_records') }}
    WHERE _PARTITIONDATE BETWEEN
        DATE_SUB(CURRENT_DATE('UTC'), INTERVAL 1 DAY)
        AND CURRENT_DATE('UTC')
      AND DATE(received_at, 'UTC') BETWEEN
        DATE_SUB(CURRENT_DATE('UTC'), INTERVAL 1 DAY)
        AND CURRENT_DATE('UTC')
      AND feed_name IN ('station_information', 'station_status')
    GROUP BY feed_name, capture_id
),

intervals AS (
    SELECT
        feed_name,
        capture_id,
        capture_received_at,
        station_record_count,
        LAG(capture_received_at) OVER (
            PARTITION BY feed_name
            ORDER BY capture_received_at, capture_id
        ) AS previous_received_at
    FROM captures
)

SELECT
    feed_name,
    capture_id,
    capture_received_at,
    previous_received_at,
    station_record_count,
    TIMESTAMP_DIFF(
        capture_received_at, previous_received_at, SECOND
    ) / 60.0 AS gap_minutes
FROM intervals