# Dolores, a real outing

The first real outing, walked on foot in Dolores, Soriano, on 2026-09-22 from 19:04 to 19:48: 31
blocks, 39 corners marked with the headset button, 530 scans and 652 access points. Published
with `--export-public --keep-places --keep-time`, so the streets and the times are real and the
networks are not.

## What is real, and what is not

Real: the streets and their corners, looked up with `--geocode` on OpenStreetMap, the
coordinates, the day and the time of every scan and every press of the button, every level every
network was heard at, and which network is which, since a pseudonym is stable and one appearing
eighty times is one router eighty times.

Not real: the name and the address of every network, substituted by pseudonyms made with the
walker's own key (`ap-` and twelve hexadecimal digits, and the names removed), and the name of
the radio. The export cannot be run again by anybody else, since the key is the walker's and is
what makes a second outing's pseudonyms agree with these.

What that still gives away is written in the export's own report and in [Publishing a
walk](../../docs/export.md): the route is on the map, and so is roughly where each access point
along it stands, and so is when it was walked. The walker chose that.

## The files

- `dolores-outing.jsonl`, `dolores-notebook.txt`: the outing, as `--export-public` wrote it

  ```bash
  enodia --export-public 2026-09-22T19-03-58.jsonl libreta.geo.txt --keep-places --keep-time \
    --out public
  ```

- `dolores-streets.jsonl`: every way of the streets the notebook names, and the neighbourhood
  around them, as `--geocode --streets --surroundings` wrote it. The street data is
  © OpenStreetMap contributors, available under the Open Database License (ODbL); see
  <https://www.openstreetmap.org/copyright>.

- `dolores-map.jsonl`: the fingerprint map of the outing

  ```bash
  rm -f samples/dolores/dolores-map.jsonl
  enodia --map-add samples/dolores/dolores-outing.jsonl samples/dolores/dolores-notebook.txt \
    --streets samples/dolores/dolores-streets.jsonl --map samples/dolores/dolores-map.jsonl
  ```

- `dolores-report.txt`: the reconciliation of the outing, with every scan placed

  ```bash
  enodia --reconcile samples/dolores/dolores-outing.jsonl samples/dolores/dolores-notebook.txt \
    --streets samples/dolores/dolores-streets.jsonl --scans > samples/dolores/dolores-report.txt
  ```

- `dolores-networks.csv`, `dolores-walk.geojson`, `dolores-plan.svg`: the located access points,
  the walk for a map, and the walk as a plan over the neighbourhood. One run writes all three,
  apart from the one above because each of these flags adds a line to the report naming the file
  it wrote. No network is named in any of them, since the export removed the names.

  ```bash
  enodia --reconcile samples/dolores/dolores-outing.jsonl samples/dolores/dolores-notebook.txt \
    --streets samples/dolores/dolores-streets.jsonl --csv samples/dolores/dolores-networks.csv \
    --geojson samples/dolores/dolores-walk.geojson --svg samples/dolores/dolores-plan.svg
  ```

- `dolores-pace-check.txt`: each middle crossing held out and found again, by movement and by
  the clock. On this walk the clock found them better, 4 m against 9 m.

  ```bash
  enodia --reconcile samples/dolores/dolores-outing.jsonl samples/dolores/dolores-notebook.txt \
    --streets samples/dolores/dolores-streets.jsonl --check-pace > samples/dolores/dolores-pace-check.txt
  ```

- `dolores-pass-check.txt`: the seven blocks walked twice, one pass against the other. They
  disagree by 16% of a stretch on average, and on five of them the passes are shifted 6 to 12 m
  in the direction of travel, which is the lag of a scan.

  ```bash
  enodia --reconcile samples/dolores/dolores-outing.jsonl samples/dolores/dolores-notebook.txt \
    --streets samples/dolores/dolores-streets.jsonl --check-passes > samples/dolores/dolores-pass-check.txt
  ```

- `dolores-map-check.txt`, `dolores-map-check-levels.txt`: each pass held out and located from
  the rest, without and with the experimental `--along levels`

  ```bash
  enodia --check-map --map samples/dolores/dolores-map.jsonl > samples/dolores/dolores-map-check.txt
  enodia --check-map --map samples/dolores/dolores-map.jsonl --along levels > samples/dolores/dolores-map-check-levels.txt
  ```

It is one outing, so what `--check-map` holds out is a pass down one stretch and not a day, and
it says so: the answers are the map recognising a walk rather than a place. 24 of the 31 blocks
were walked once, and holding that pass out leaves nothing on the block, which is why most
answers land across a mark. A second outing over the same streets, exported with the same key,
is what turns this into the honest test.

`tests/test_samples.py` rebuilds every one of these files from the outing and compares it byte
for byte, and checks that no network in the outing or the map has anything but a pseudonym. The
map checks take minutes and the pace check a few seconds, so those three run only when asked
for, as one job of the CI does on every push:

```bash
ENODIA_SLOW=1 uv run pytest tests/test_samples.py --no-cov
```
