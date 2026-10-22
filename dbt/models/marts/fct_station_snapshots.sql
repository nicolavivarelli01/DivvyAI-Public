{{ config(materialized="view") }}

SELECT
    status.capture_id,
    status.station_id,
    status.received_at AS status_received_at,
    information.received_at AS information_received_at,
    information.station_id IS NOT NULL AS has_station_information,
    information.station_name,
    information.latitude,
    information.longitude,
    information.capacity,
    status.bikes_available,
    status.docks_available
FROM {{ ref('stg_station_status') }} AS status
LEFT JOIN {{ ref('stg_station_information') }} AS information
    ON status.capture_id = information.capture_id
    AND status.station_id = information.station_id