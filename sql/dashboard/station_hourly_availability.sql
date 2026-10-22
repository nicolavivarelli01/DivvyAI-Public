SELECT
    station_id,
    hour_started_at_utc,
    snapshot_count,
    bike_sample_count,
    dock_sample_count,
    avg_bikes_available,
    avg_docks_available
FROM `divvy-ai.divvyai_analytics.mart_station_hourly_availability`
WHERE station_id = '03a1610c-3ba0-4b85-b512-bc26791cb7ee'
  AND DATE(hour_started_at_utc, 'UTC') = CURRENT_DATE('UTC')
ORDER BY hour_started_at_utc;