#!/usr/bin/env python3
"""
Prepare iPhone photos for the Italy bike trip page.

Reads a folder of .jpg / .heic photos, extracts GPS coordinates and capture
datetime from EXIF, optionally assigns each photo to a "day" based on date,
resizes them to a web-friendly version + a thumbnail, and writes a photos.json
file the page can consume.

USAGE
-----
    pip install Pillow pillow-heif
    python prep_photos.py \
        --input  ~/Downloads/italy_photos \
        --output ./build \
        --json   ../data/photos.json

This writes:
    ./build/photos/<filename>.jpg   (max 1600px, web-quality)
    ./build/thumbs/<filename>.jpg   (400px square-ish)
    ../data/photos.json

You then upload ./build/photos/  and  ./build/thumbs/  to S3 under
    s3://jgracie/italy/photos/
    s3://jgracie/italy/thumbs/
and set PHOTO_BASE_URL in index.html to:
    https://jgracie.s3.us-east-2.amazonaws.com/italy/

The photos.json file references paths like "photos/IMG_1234.jpg" and
"thumbs/IMG_1234.jpg" so the page joins them with PHOTO_BASE_URL.
"""

import argparse
import bisect
import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from PIL import Image, ExifTags
except ImportError:
    sys.exit("Pillow is required:  pip install Pillow")

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pillow_heif = None


EXIF_IFD = 0x8769
GPS_IFD = 0x8825
TAG_DATETIME_ORIGINAL = 0x9003


def _decode(v):
    return v.decode("ascii", errors="ignore") if isinstance(v, bytes) else v


def to_decimal(coord, ref):
    """Convert EXIF GPS (degrees, minutes, seconds) to decimal degrees."""
    if not coord or len(coord) < 3:
        return None
    try:
        deg, minutes, sec = [float(x) for x in coord]
    except (TypeError, ValueError):
        return None
    val = deg + minutes / 60 + sec / 3600
    if _decode(ref) in ("S", "W"):
        val = -val
    return val


def extract_meta(path):
    """Return dict with lat, lng, datetime (ISO string) or None values.

    Uses Pillow's modern getexif() API, which works for both JPEG and HEIC
    (with pillow-heif registered).
    """
    img = Image.open(path)
    exif = img.getexif()

    dt_iso = None
    try:
        exif_data = exif.get_ifd(EXIF_IFD)
        dt_str = exif_data.get(TAG_DATETIME_ORIGINAL)
        if dt_str:
            dt_iso = datetime.strptime(dt_str, "%Y:%m:%d %H:%M:%S").isoformat()
    except (KeyError, ValueError, AttributeError):
        pass

    lat = lng = None
    try:
        gps = exif.get_ifd(GPS_IFD)
        if gps:
            lat = to_decimal(gps.get(2), gps.get(1))   # GPSLatitude, GPSLatitudeRef
            lng = to_decimal(gps.get(4), gps.get(3))   # GPSLongitude, GPSLongitudeRef
    except (KeyError, AttributeError):
        pass

    return {"datetime": dt_iso, "lat": lat, "lng": lng, "_img": img}


def read_xmp_datetime(photo_path, photo_tz):
    """If a sibling <stem>.xmp file exists, parse a creation datetime from it.

    Returns a tz-naive ISO string in `photo_tz` (matching the format used for
    EXIF datetimes), or None.

    Looks for <photoshop:DateCreated>, <xmp:CreateDate>, or <exif:DateTimeOriginal>.
    XMP datetimes commonly carry a timezone offset (e.g. `-04:00`). When present,
    the value is converted to `photo_tz` before being stored, so day numbering
    stays consistent with EXIF-based photos.
    """
    xmp = photo_path.with_suffix(".xmp")
    if not xmp.exists():
        xmp = photo_path.parent / (photo_path.stem + ".xmp")
        if not xmp.exists():
            return None
    try:
        text = xmp.read_text(errors="ignore")
    except OSError:
        return None
    import re
    pat = re.compile(r"(?:photoshop:DateCreated|xmp:CreateDate|exif:DateTimeOriginal)>([^<]+)<")
    m = pat.search(text)
    if not m:
        return None
    raw = m.group(1).strip()
    # Normalize "Z" → "+00:00" so fromisoformat accepts it (Python ≥3.7)
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        # Some XMP files use "YYYY:MM:DD HH:MM:SS" — try that too
        try:
            dt = datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
        except ValueError:
            return None
    if dt.tzinfo is None:
        # No TZ in XMP — treat as already in photo_tz
        return dt.isoformat()
    # Convert to photo_tz local wall-clock, drop tzinfo to match EXIF format
    return dt.astimezone(photo_tz).replace(tzinfo=None).isoformat()


def load_gpx_timeline(gpx_dir):
    """Read all GPX files, return a list of (utc_datetime, lat, lng) sorted by time."""
    points = []
    for path in sorted(Path(gpx_dir).iterdir()):
        # Match .gpx as well as quirky names like "X.gpx copy" from Finder
        if ".gpx" not in path.name.lower():
            continue
        if not path.is_file():
            continue
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        ns_uri = root.tag.split("}")[0].strip("{") if root.tag.startswith("{") else None
        ns = {"gpx": ns_uri} if ns_uri else {}
        trkpts = root.findall(".//gpx:trkpt", ns) if ns else root.findall(".//trkpt")
        for pt in trkpts:
            time_el = pt.find("gpx:time", ns) if ns else pt.find("time")
            if time_el is None or not time_el.text:
                continue
            try:
                t = datetime.fromisoformat(time_el.text.replace("Z", "+00:00"))
            except ValueError:
                continue
            points.append((t, float(pt.get("lat")), float(pt.get("lon"))))
    points.sort(key=lambda p: p[0])
    return points


def parse_tz_offset(s):
    """Parse '+02:00' / '-05:30' etc. into a datetime.timezone."""
    sign = 1 if s.startswith("+") else -1 if s.startswith("-") else None
    if sign is None or ":" not in s:
        raise ValueError(f"Bad --photo-tz value: {s!r} (expected '+HH:MM' or '-HH:MM')")
    hh, mm = s[1:].split(":")
    return timezone(sign * timedelta(hours=int(hh), minutes=int(mm)))


def find_nearest_track_point(gpx_points, photo_local_iso, photo_tz, window_min):
    """Given a photo's local ISO time and tz, find the nearest GPX point in UTC.
    Returns (lat, lng, diff_minutes) or None if outside window."""
    if not gpx_points or not photo_local_iso:
        return None
    photo_local = datetime.fromisoformat(photo_local_iso)
    photo_utc = photo_local.replace(tzinfo=photo_tz).astimezone(timezone.utc)
    times = [p[0] for p in gpx_points]
    idx = bisect.bisect_left(times, photo_utc)
    candidates = []
    if idx > 0:
        candidates.append(gpx_points[idx - 1])
    if idx < len(gpx_points):
        candidates.append(gpx_points[idx])
    if not candidates:
        return None
    best = min(candidates, key=lambda p: abs((p[0] - photo_utc).total_seconds()))
    diff_min = abs((best[0] - photo_utc).total_seconds()) / 60
    if diff_min > window_min:
        return None
    return best[1], best[2], diff_min


def save_resized(img, path, max_dim, quality=85):
    img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=quality, optimize=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Folder of source photos (.jpg/.heic)")
    ap.add_argument("--output", required=True, help="Folder to write resized photos and thumbs")
    ap.add_argument("--json", required=True, help="Path to write photos.json")
    ap.add_argument("--start-date", help="Trip start date (YYYY-MM-DD). If given, each photo gets a 'day' number relative to this.")
    ap.add_argument("--max-day", type=int, help="Clamp the assigned day number to this maximum (e.g. 5 to roll the morning-after photos into the last day).")
    ap.add_argument("--gpx-dir", help="Folder of .gpx files. If a photo has no EXIF GPS, its location is interpolated by matching its timestamp to the nearest track point.")
    ap.add_argument("--photo-tz", default="+02:00", help="Timezone offset of photo EXIF timestamps, e.g. '+02:00' for CEST. Used only when interpolating from GPX (which is in UTC). Default: +02:00.")
    ap.add_argument("--match-window", type=int, default=30, help="Max minutes between a photo and the nearest GPX point for time-based interpolation. Default: 30.")
    ap.add_argument("--max-photo", type=int, default=1600)
    ap.add_argument("--max-thumb", type=int, default=400)
    args = ap.parse_args()

    in_dir = Path(args.input).expanduser()
    out_dir = Path(args.output).expanduser()
    photos_dir = out_dir / "photos"
    thumbs_dir = out_dir / "thumbs"

    start_date = None
    if args.start_date:
        start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()

    exts = {".jpg", ".jpeg", ".heic", ".heif", ".png"}
    files = sorted(p for p in in_dir.iterdir() if p.suffix.lower() in exts)

    if not files:
        sys.exit(f"No photos found in {in_dir}")
    if any(p.suffix.lower() in {".heic", ".heif"} for p in files) and pillow_heif is None:
        sys.exit("HEIC files detected. Install pillow-heif:  pip install pillow-heif")

    photo_tz = parse_tz_offset(args.photo_tz)
    gpx_points = []
    if args.gpx_dir:
        gpx_points = load_gpx_timeline(args.gpx_dir)
        print(f"Loaded {len(gpx_points)} GPX track points for time-based lookup (tz {args.photo_tz})\n")

    records = []
    seen_names = set()
    n_interp = 0
    n_no_match = 0
    for src in files:
        print(f"  {src.name}")
        try:
            meta = extract_meta(src)
        except Exception as e:
            print(f"    skipped ({e})")
            continue

        # If EXIF had no datetime, look for an XMP sidecar (some shared photos
        # have EXIF stripped but keep an XMP file alongside)
        if meta["datetime"] is None:
            xmp_dt = read_xmp_datetime(src, photo_tz)
            if xmp_dt:
                meta["datetime"] = xmp_dt
                print(f"    datetime recovered from XMP sidecar: {xmp_dt}")

        out_name = src.stem + ".jpg"
        if out_name in seen_names:
            # Avoid silent overwrite if two phones produced the same IMG_NNNN
            out_name = f"{src.stem}__{len(seen_names)}.jpg"
            print(f"    filename collision, renaming to {out_name}")
        seen_names.add(out_name)

        save_resized(meta["_img"], photos_dir / out_name, args.max_photo, quality=85)
        save_resized(meta["_img"], thumbs_dir / out_name, args.max_thumb, quality=80)

        record = {
            "src": f"photos/{out_name}",
            "thumb": f"thumbs/{out_name}",
            "datetime": meta["datetime"],
            "lat": meta["lat"],
            "lng": meta["lng"],
            "gps_source": "exif" if meta["lat"] is not None else None,
        }

        # If no GPS, try interpolating from GPX track by timestamp
        if record["lat"] is None and gpx_points and meta["datetime"]:
            hit = find_nearest_track_point(gpx_points, meta["datetime"], photo_tz, args.match_window)
            if hit:
                lat, lng, diff_min = hit
                record["lat"] = lat
                record["lng"] = lng
                record["gps_source"] = "interpolated"
                n_interp += 1
                print(f"    interpolated location ({diff_min:.1f} min from nearest track point)")
            else:
                n_no_match += 1
                print(f"    no GPS, no GPX match within {args.match_window} min")

        if start_date and meta["datetime"]:
            taken = datetime.fromisoformat(meta["datetime"]).date()
            day = (taken - start_date).days + 1
            if args.max_day:
                day = min(day, args.max_day)
            record["day"] = day
        records.append(record)

    records.sort(key=lambda r: r.get("datetime") or "")

    json_path = Path(args.json).expanduser()
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w") as f:
        json.dump(records, f, indent=2)

    print(f"\nWrote {len(records)} photos to {json_path}")
    print(f"Resized files in {out_dir}")
    n_exif = sum(1 for r in records if r.get("gps_source") == "exif")
    print(f"  {n_exif} located from EXIF GPS")
    if gpx_points:
        print(f"  {n_interp} located by GPX time match")
        print(f"  {n_no_match} without location (no EXIF GPS and no GPX match)")


if __name__ == "__main__":
    main()
