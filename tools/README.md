# Backroads Puglia Bike Trip — Build Workflow

This is a standalone GitHub Pages site at
**https://backroads-puglia-may-2026.github.io** (org and repo both named
`backroads-puglia-may-2026`; repo is `backroads-puglia-may-2026.github.io`).

## What lives where

| Item | Location |
|------|----------|
| The page | `index.html` (root of this repo) |
| Map routes (GeoJSON) | `data/routes.json` |
| Photo list with GPS, day, datetime | `data/photos.json` |
| Source GPX files (not in repo) | `~/Library/CloudStorage/Dropbox/italy-bike-trip/gpx/` |
| Source photos (not in repo) | `~/Library/CloudStorage/Dropbox/italy-bike-trip/photos/` |
| Processed photo JPEGs | **S3** — `s3://jgracie/italy-bike-trip/photos/` (≤ 1600px) |
| Processed thumbnails | **S3** — `s3://jgracie/italy-bike-trip/thumbs/` (≤ 400px) |
| Public photo URL prefix | `https://jgracie.s3.us-east-2.amazonaws.com/italy-bike-trip/` |
| AWS CLI binary | `~/Library/Python/3.9/bin/aws` (pip install, not on PATH) |

The page in `index.html` reads `data/routes.json` + `data/photos.json` at
load time. `photos.json` stores paths like `photos/IMG_9897.jpg` which the
page joins with `PHOTO_BASE_URL` (defined near the top of `index.html`,
currently set to the S3 URL above).

`index.html` keeps a Jekyll front matter block (`---\nlayout: null\n…\n---`)
so it can be served locally with `bundle exec jekyll serve` and on GitHub
Pages without changes.

## One-time setup (already done on Jamie's Mac)

```bash
python3 -m pip install --user Pillow pillow-heif awscli
~/Library/Python/3.9/bin/aws configure   # IAM user: jgracie-s3-uploader, region us-east-2
bundle install                            # Jekyll deps (Gemfile in repo root)
```

The `jgracie` S3 bucket already has a public-read policy on the
`italy-bike-trip/*` prefix, so newly uploaded objects are immediately
readable on the web — no per-object ACL needed.

## Common task: add new photos

This is the workflow that runs after Jamie drops new photos (from her phone
or shared by friends) into the Dropbox photos folder.

```bash
# from the repo root
cd tools

# Step 1 — regenerate resized images + photos.json
python3 prep_photos.py \
  --input        ~/Library/CloudStorage/Dropbox/italy-bike-trip/photos \
  --output       ./build \
  --json         ../data/photos.json \
  --start-date   2026-05-17 \
  --max-day      5 \
  --gpx-dir      ~/Library/CloudStorage/Dropbox/italy-bike-trip/gpx

# Step 2 — upload new/changed files to S3 (sync only transfers what changed)
AWS=~/Library/Python/3.9/bin/aws
$AWS s3 sync ./build/photos s3://jgracie/italy-bike-trip/photos \
  --content-type image/jpeg --cache-control "public, max-age=31536000"
$AWS s3 sync ./build/thumbs s3://jgracie/italy-bike-trip/thumbs \
  --content-type image/jpeg --cache-control "public, max-age=31536000"

# Step 3 — clean up the build folder (it's gitignored, but no reason to keep it)
rm -rf ./build

# Step 4 — commit the updated photos.json and push
cd ..
git add data/photos.json
git commit -m "Add N more photos (now M total)"
git push
```

GitHub Pages publishes in 1–2 min. Browser cache may need a hard refresh
(⌘⇧R).

## Less common: update routes

Only needed if you re-edit the GPX files (e.g. trimming a track).

```bash
cd tools
python3 gpx_to_geojson.py \
  --input      ~/Library/CloudStorage/Dropbox/italy-bike-trip/gpx \
  --output     ../data/routes.json \
  --start-date 2026-05-17
cd ..
git add data/routes.json && git commit -m "Update routes" && git push
```

`routes.json` is committed to the repo (small — ~2 MB).

## Local development

```bash
bundle exec jekyll serve & sleep 1; open http://localhost:4000
```

A hard refresh is sometimes needed after re-generating `data/photos.json`.

## Reference: prep_photos.py flags

| Flag | What it does |
|------|--------------|
| `--input` | folder of source photos (`.jpg`, `.heic`, `.png`) |
| `--output` | folder for resized JPEGs and thumbnails (writes `./photos/` and `./thumbs/` under this) |
| `--json` | where to write `photos.json` |
| `--start-date YYYY-MM-DD` | trip Day 1. Each photo gets `day = (taken - start) + 1` |
| `--max-day N` | clamp the day number (so May 22 photos roll into Day 5 instead of Day 6 when trip was 5 days) |
| `--gpx-dir` | if a photo lacks EXIF GPS, look up the nearest GPX point in time and place it there. Tagged `gps_source: "interpolated"` |
| `--photo-tz +HH:MM` | timezone of EXIF wall-clock times (default `+02:00` for CEST). Used only when interpolating from GPX (which is UTC) |
| `--match-window` | minutes (default 30); photos outside this window of any GPX point get no location and appear only in the sidebar |
| `--max-photo` | max long edge for full-size copies (default 1600) |
| `--max-thumb` | max long edge for thumbnails (default 400) |

## Photo record shape (photos.json)

```json
{
  "src":        "photos/IMG_9897.jpg",
  "thumb":      "thumbs/IMG_9897.jpg",
  "datetime":   "2026-05-17T16:11:16",
  "lat":        40.116975,
  "lng":        18.508383,
  "gps_source": "exif",              // or "interpolated", or null
  "day":        1,
  "caption":    "optional, shows in lightbox"
}
```

Hand-edit allowed — re-running `prep_photos.py` will overwrite, so any
hand-edits to `photos.json` (e.g. captions, manual GPS) need to be re-applied
or kept somewhere alongside the input photos.

## Troubleshooting

- **Photo has no GPS and didn't match a track point.** Most likely a
  restaurant, hotel, or post-ride photo. It appears in the sidebar grid but
  has no map marker. To force a location, hand-edit `photos.json`.
- **Photo with EXIF stripped (e.g. shared via Google Photos / WhatsApp web).**
  The script falls back to a sibling `<stem>.xmp` sidecar file if present, and
  pulls the datetime from `photoshop:DateCreated`. XMP timezone offsets are
  honored and converted to `--photo-tz` so day numbering stays consistent.
- **Filename collisions** (two phones produced `IMG_1234.HEIC`). The script
  warns and renames the second one to `IMG_1234__1.jpg` in the output.
- **Friend's photo on wrong timezone.** Re-run with `--photo-tz +01:00` (or
  whatever their TZ was).
- **S3 returns 403 on a newly uploaded file.** The bucket policy covers only
  the `italy-bike-trip/*` prefix. Uploading outside that prefix will not be
  publicly readable.
- **`aws: command not found`.** Not on PATH. Use the full path
  `~/Library/Python/3.9/bin/aws` (or alias in `~/.zshrc`).
- **Old GPX filenames with spaces** like `Afternoon_Ride.gpx copy` (from
  Finder duplication) are handled — `gpx_to_geojson.py` matches anything
  containing `.gpx` in the name, not just the extension.
- **HEIC handling.** iPhone HEICs need `pillow-heif`. The script registers
  it at import time, so any newly-installed Python may need
  `pip install pillow-heif` again.
