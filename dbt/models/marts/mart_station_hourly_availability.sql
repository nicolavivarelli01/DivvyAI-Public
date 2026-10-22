{{ config(materialized="view") }}

SELECT
    station_id,
    TIMESTAMP_TRUNC(
        status_received_at, HOUR, 'UTC'
    ) AS hour_started_at_utc,
    COUNT(*) AS snapshot_count,
    COUNT(bikes_available) AS bike_sample_count,
    COUNT(docks_available) AS dock_sample_count,
    AVG(bikes_available) AS avg_bikes_available,
    AVG(docks_available) AS avg_docks_available
FROM {{ ref('fct_station_snapshots') }}
GROUP BY station_id, hour_started_at_utc