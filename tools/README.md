# Italy Bike Trip — Build Workflow

The page at `/italy-bike-trip/` reads two static files:

- `../data/routes.json` — GeoJSON FeatureCollection of the bike routes (with
  per-point elevation)
- `../data/photos.json` — list of photos with GPS coords, datetime, day, and
  whether the location came from EXIF or was interpolated from the GPX track

Photos themselves are hosted on S3 at
`https://jgracie.s3.us-east-2.amazonaws.com/italy-bike-trip/`. The JSON
references them by relative path (e.g. `photos/IMG_9897.jpg`) and the page
joins those paths with `PHOTO_BASE_URL` defined in `index.html`.

## Authoritative folder locations

| What | Where |
|------|-------|
| Source GPX files | `~/Library/CloudStorage/Dropbox/italy-bike-trip/gpx/` |
| Source photos (originals, all phones) | `~/Library/CloudStorage/Dropbox/italy-bike-trip/photos/` |
| AWS CLI binary | `~/Library/Python/3.9/bin/aws` (installed via `pip install --user awscli`; not on PATH) |
| S3 destination | `s3://jgracie/italy-bike-trip/photos/` and `.../thumbs/` |

## One-time setup

```bash
python3 -m pip install --user Pillow pillow-heif awscli
~/Library/Python/3.9/bin/aws configure   # IAM user: jgracie-s3-uploader, region us-east-2
```

The `jgracie` bucket already has a public-read policy on the
`italy-bike-trip/*` prefix, so newly uploaded objects are immediately readable
on the web — no per-object ACL needed.

## Refreshing the data

### Photos (run when you add new originals)

```bash
cd italy-bike-trip/tools
python3 prep_photos.py \
  --input        ~/Library/CloudStorage/Dropbox/italy-bike-trip/photos \
  --output       ./build \
  --json         ../data/photos.json \
  --start-date   2026-05-17 \
  --max-day      5 \
  --gpx-dir      ~/Library/CloudStorage/Dropbox/italy-bike-trip/gpx
```

Flags:
- `--start-date` — first day of the trip; each photo gets `day = (date - start) + 1`
- `--max-day` — clamp the day number (so May 22 photos roll into Day 5 instead of Day 6)
- `--gpx-dir` — fallback: if a photo lacks EXIF GPS, look up the nearest GPX
  point in time and place it there. Tagged `gps_source: "interpolated"`.
- `--photo-tz` — defaults to `+02:00` (CEST). Override if a friend's phone was
  set to another timezone when their photos were taken.
- `--match-window` — minutes (default 30); photos outside this window of any
  GPX point get no location and appear only in the sidebar.

Outputs:
- `./build/photos/<stem>.jpg` (≤ 1600px, JPEG q85)
- `./build/thumbs/<stem>.jpg` (≤ 400px, JPEG q80)
- `../data/photos.json`

### GPX routes (run if you re-edit any GPX file)

The original Dropbox folder has a few files with awkward names like
`Afternoon_Ride.gpx copy` from Finder duplication. The conversion script
matches anything containing `.gpx`, so they're picked up too.

```bash
python3 gpx_to_geojson.py \
  --input      ~/Library/CloudStorage/Dropbox/italy-bike-trip/gpx \
  --output     ../data/routes.json \
  --start-date 2026-05-17
```

Each GPX becomes one feature with properties: `day`, `date`, `start_time`,
`distance_km`, `ele_min_m`, `ele_max_m`, `ascent_m`. The page groups features
by `day`, so Day 3's two segments merge into one entry in the sidebar.

## Uploading to S3

```bash
AWS=~/Library/Python/3.9/bin/aws

$AWS s3 sync ./build/photos s3://jgracie/italy-bike-trip/photos \
  --content-type image/jpeg --cache-control "public, max-age=31536000"

$AWS s3 sync ./build/thumbs s3://jgracie/italy-bike-trip/thumbs \
  --content-type image/jpeg --cache-control "public, max-age=31536000"
```

`s3 sync` only uploads new/changed files, so adding 10 new photos is cheap.

If you ever delete photos and want S3 to match, add `--delete`:
```bash
$AWS s3 sync ./build/photos s3://jgracie/italy-bike-trip/photos --delete ...
```

## Test locally

```bash
bundle exec jekyll serve
# open http://localhost:4000/italy-bike-trip/
```

A hard refresh (⌘⇧R) is sometimes needed if `photos.json` was just regenerated.

## Commit and push

```bash
git add italy-bike-trip robots.txt _config.yml
git commit -m "Update Italy bike trip data"
git push
```

The page lives at `https://jamiegracie.com/italy-bike-trip/`. It is not linked
from anywhere on the site and is disallowed in `robots.txt`. Anyone with the
URL can still visit it. The photos load directly from S3, not GitHub Pages.

## Notes & troubleshooting

- **Photo has no GPS and didn't match a track point.** Most likely a restaurant,
  hotel, or post-ride photo. It appears in the sidebar grid but has no map
  marker. To force a location, hand-edit `photos.json`.
- **Filename collisions** (two phones produced `IMG_1234.HEIC`). The script
  warns and renames the second one to `IMG_1234__1.jpg`.
- **Friend's photo on wrong timezone.** Re-run with `--photo-tz +01:00` (or
  whatever their TZ was).
- **Captions.** Add a `"caption"` field to any entry in `photos.json` and it
  shows in the lightbox.
- **S3 returns 403 on a newly uploaded file.** The bucket policy covers the
  `italy-bike-trip/*` prefix only — uploading outside that prefix will not be
  publicly readable.
- **`aws: command not found`.** It's not on PATH. Use the full path
  `~/Library/Python/3.9/bin/aws` or alias it in `~/.zshrc`.
