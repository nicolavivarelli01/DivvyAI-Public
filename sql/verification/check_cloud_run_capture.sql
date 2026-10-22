SELECT
  feed_name,
  COUNT(*) AS rows_loaded,
  COUNT(DISTINCT station_id) AS distinct_stations,
  SUM(COUNT(*)) OVER () AS capture_rows,
  COUNTIF(
    JSON_VALUE(payload, '$.station_id') IS DISTINCT FROM station_id
  ) AS payload_id_mismatches,
  COUNTIF(
    archive_object_name IS DISTINCT FROM
    'raw/date=2026-10-13/hour=06/e2356ba95b41db44a7fb55b64dc5627d7736807b773ecdd3b438cc93670f1c25.zip'
  ) AS archive_reference_mismatches
FROM `divvy-ai.divvyai_raw.station_records`
WHERE _PARTITIONDATE BETWEEN DATE '2026-10-13' AND CURRENT_DATE()
  AND capture_id =
    'e2356ba95b41db44a7fb55b64dc5627d7736807b773ecdd3b438cc93670f1c25'
GROUP BY feed_name
ORDER BY feed_name;