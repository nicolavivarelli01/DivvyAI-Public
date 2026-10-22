import json


def parse_station_feed(raw: bytes) -> list[dict[str, object]]:
    """Extract station objects after validating their structure and IDs."""
    try:
        document: object = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Feed body must be UTF-8 JSON.") from exc

    if not isinstance(document, dict):
        raise ValueError("Feed root must be a JSON object.")

    data: object = document.get("data")
    if not isinstance(data, dict):
        raise ValueError("Feed data must be a JSON object.")

    stations: object = data.get("stations")
    if not isinstance(stations, list):
        raise ValueError("Feed data.stations must be a JSON array.")

    records: list[dict[str, object]] = []
    seen_ids: set[str] = set()

    for index, station in enumerate(stations):
        if not isinstance(station, dict):
            raise ValueError(f"Station at index {index} must be a JSON object.")

        station_id: object = station.get("station_id")
        if not isinstance(station_id, str) or not station_id.strip():
            raise ValueError(
                f"Station at index {index} must have a nonempty string station_id."
            )

        if station_id in seen_ids:
            raise ValueError(f"Duplicate station_id: {station_id!r}.")

        seen_ids.add(station_id)
        records.append(station)

    return records
