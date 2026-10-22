WITH checked_snapshots AS (
    SELECT
        capture_id,
        station_id,
        status_received_at,
        bikes_available,
        docks_available,
        COUNT(*) OVER (
            PARTITION BY capture_id, station_id
        ) AS rows_per_key
    FROM {{ ref('fct_station_snapshots') }}
)

SELECT
    capture_id,
    station_id,
    status_received_at,
    bikes_available,
    docks_available,
    rows_per_key
FROM checked_snapshots
WHERE NULLIF(TRIM(capture_id), '') IS NULL
    OR NULLIF(TRIM(station_id), '') IS NULL
    OR status_received_at IS NULL
    OR rows_per_key <> 1
    OR bikes_available IS NULL
    OR bikes_available < 0
    OR docks_available IS NULL
    OR docks_available < 0