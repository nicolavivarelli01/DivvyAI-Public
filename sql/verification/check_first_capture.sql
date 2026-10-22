SELECT
  feed_name,
  COUNT(*) AS rows_loaded,
  COUNT(DISTINCT station_id) AS distinct_stations,
  COUNTIF(
    JSON_VALUE(payload, '$.station_id') IS DISTINCT FROM station_id
  ) AS payload_id_mismatches,
  COUNTIF(
    archive_object_name !=
    'raw/date=2026-10-10/hour=22/d857585ea5fe17a4648ce26813d8eb73411d8cf2de0be2e0564f43f3a7d00342.zip'
  ) AS archive_reference_mismatches
FROM `divvy-ai.divvyai_raw.station_records`
WHERE _PARTITIONDATE BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY)
                         AND CURRENT_DATE()
  AND capture_id =
    'd857585ea5fe17a4648ce26813d8eb73411d8cf2de0be2e0564f43f3a7d00342'
GROUP BY feed_name
ORDER BY feed_name;