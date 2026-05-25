#!/usr/bin/env python3
"""
Convert a folder of GPX files into a single GeoJSON FeatureCollection.

Each GPX file becomes one LineString feature, with properties:
    - name: filename (stem)
    - day:  if --start-date is provided, the day number of the first track point
    - date: ISO date of the first track point
    - distance_km: rough Haversine distance

USAGE
-----
    python gpx_to_geojson.py \
        --input  ~/Downloads/italy_gpx \
        --output ../data/routes.json \
        --start-date 2025-06-10
"""

import argparse
import json
import math
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

NS = {"gpx": "http://www.topografix.com/GPX/1/1"}


def parse_gpx(path):
    """Return list of (lon, lat, time_str_or_None) for every track point."""
    tree = ET.parse(path)
    root = tree.getroot()
    # Detect default namespace
    if root.tag.startswith("{"):
        ns_uri = root.tag.split("}")[0].strip("{")
        ns = {"gpx": ns_uri}
    else:
        ns = {}

    points = []
    trkpts = root.findall(".//gpx:trkpt", ns) if ns else root.findall(".//trkpt")
    for pt in trkpts:
        lat = float(pt.get("lat"))
        lon = float(pt.get("lon"))
        time_el = pt.find("gpx:time", ns) if ns else pt.find("time")
        time_str = time_el.text if time_el is not None else None
        ele_el = pt.find("gpx:ele", ns) if ns else pt.find("ele")
        ele = float(ele_el.text) if ele_el is not None and ele_el.text else None
        points.append((lon, lat, ele, time_str))
    return points


def haversine_km(p1, p2):
    R = 6371.0
    lon1, lat1 = math.radians(p1[0]), math.radians(p1[1])
    lon2, lat2 = math.radians(p2[0]), math.radians(p2[1])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Folder of .gpx files")
    ap.add_argument("--output", required=True, help="Path to write GeoJSON FeatureCollection")
    ap.add_argument("--start-date", help="Trip start date (YYYY-MM-DD) for day numbering")
    args = ap.parse_args()

    in_dir = Path(args.input).expanduser()
    gpx_files = sorted(in_dir.glob("*.gpx"))
    if not gpx_files:
        sys.exit(f"No .gpx files found in {in_dir}")

    start_date = None
    if args.start_date:
        start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()

    features = []
    for path in gpx_files:
        print(f"  {path.name}")
        points = parse_gpx(path)
        if len(points) < 2:
            print("    skipped (no track points)")
            continue

        # GeoJSON coords are [lng, lat, ele?]
        coords = []
        for lon, lat, ele, _ in points:
            coords.append([lon, lat, ele] if ele is not None else [lon, lat])

        dist = 0.0
        for a, b in zip(coords, coords[1:]):
            dist += haversine_km(a, b)

        eles = [ele for _, _, ele, _ in points if ele is not None]
        ele_min = round(min(eles), 0) if eles else None
        ele_max = round(max(eles), 0) if eles else None
        ascent = 0.0
        for a, b in zip(eles, eles[1:]):
            if b > a:
                ascent += b - a

        first_time = next((t for _, _, _, t in points if t), None)
        date_iso = None
        day = None
        if first_time:
            try:
                d = datetime.fromisoformat(first_time.replace("Z", "+00:00")).date()
                date_iso = d.isoformat()
                if start_date:
                    day = (d - start_date).days + 1
            except ValueError:
                pass

        features.append({
            "type": "Feature",
            "properties": {
                "name": path.stem,
                "day": day,
                "date": date_iso,
                "start_time": first_time,
                "distance_km": round(dist, 1),
                "ele_min_m": ele_min,
                "ele_max_m": ele_max,
                "ascent_m": round(ascent, 0) if eles else None,
            },
            "geometry": {
                "type": "LineString",
                "coordinates": coords,
            },
        })

    # Sort by date if available, else by filename
    features.sort(key=lambda f: (f["properties"].get("date") or "", f["properties"]["name"]))

    out_path = Path(args.output).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, indent=2)

    total = sum(f["properties"]["distance_km"] for f in features)
    print(f"\nWrote {len(features)} routes ({total:.1f} km total) to {out_path}")


if __name__ == "__main__":
    main()
