"""Build web/airports.json (used by the airport search box) from OurAirports data.

Usage:
    curl -sO https://raw.githubusercontent.com/davidmegginson/ourairports-data/main/airports.csv
    curl -sO https://raw.githubusercontent.com/davidmegginson/ourairports-data/main/countries.csv
    python scripts/build_airports.py airports.csv countries.csv web/airports.json

Keeps airports that have an IATA code and either scheduled passenger service or are large
airports. OurAirports data is in the public domain.
"""

from __future__ import annotations

import csv
import json
import sys

SIZE = {"large_airport": 2, "medium_airport": 1, "small_airport": 0}


def build(airports_csv: str, countries_csv: str) -> dict:
    with open(countries_csv, encoding="utf-8") as fh:
        countries = {row["code"]: row["name"] for row in csv.DictReader(fh)}
    rows = []
    seen = set()
    with open(airports_csv, encoding="utf-8") as fh:
        for a in csv.DictReader(fh):
            code = a["iata_code"].strip().upper()
            if len(code) != 3 or not code.isalpha() or a["type"] not in SIZE or code in seen:
                continue
            if a["scheduled_service"] != "yes" and a["type"] != "large_airport":
                continue
            seen.add(code)
            keywords = ", ".join(k.strip() for k in a["keywords"].split(",")[:4] if k.strip())[:60]
            rows.append([
                code,
                a["name"].strip(),
                a["municipality"].strip(),
                a["iso_country"].strip(),
                round(float(a["latitude_deg"]), 2),
                round(float(a["longitude_deg"]), 2),
                SIZE[a["type"]],
                keywords,
            ])
    rows.sort(key=lambda r: (-r[6], r[0]))
    used = {r[3] for r in rows}
    return {
        "source": "OurAirports (public domain), https://ourairports.com/data/",
        "fields": ["iata", "name", "city", "country", "lat", "lon", "size", "keywords"],
        "countries": {c: n for c, n in sorted(countries.items()) if c in used},
        "airports": rows,
    }


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    data = build(sys.argv[1], sys.argv[2])
    with open(sys.argv[3], "w", encoding="utf-8") as out:
        json.dump(data, out, ensure_ascii=False, separators=(",", ":"))
    print(f"{len(data['airports'])} airports written to {sys.argv[3]}")
