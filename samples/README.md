# Rivera sample

A runnable example of Enodia's whole flow, from two logs and their notebooks to a map that
says where a scan was taken. The streets are real. The radios are not.

## What is real

The four marks are corners of Avenida Rivera in Montevideo, looked up with `--geocode` on
2026-09-21 and written into the notebooks as it wrote them:

| mark | OpenStreetMap | coordinates |
|---|---|---|
| Rivera y Avenida Doctor Francisco Soca | the middle of nodes `917539424` and `917609667`, the two carriageways of Soca | `-34.903126, -56.156023` |
| Rivera y Brito del Pino | node `917610585` | `-34.903131, -56.157581` |
| Rivera y Simón Bolívar | node `917722502` | `-34.903101, -56.159047` |
| Rivera y Obligado | node `917426380` | `-34.903011, -56.160359` |

The blocks between them are OSM ways `179065360`, `179065362` and `319683739` of "Avenida
General Rivera": 142.1 m, 133.8 m and 120.2 m along their vertices from mark to mark, 396 m in
all. `rivera-streets.jsonl` holds every way of the five streets the notebook names, as
`--geocode --streets` wrote it:

```bash
enodia --geocode notebook.txt --area "-34.925,-56.175,-34.895,-56.135" \
  --streets samples/rivera-streets.jsonl
```

The names are the shortest forms the lookup resolves. OSM calls the avenue "Avenida General
Rivera" and answers to "Rivera", and calls Brito del Pino "General José Esteban Brito del
Pino", but Soca is "Avenida Doctor Francisco Soca" and nothing shorter, so the mark says so. The
street data is available under the Open Database License (ODbL); see
<https://www.openstreetmap.org/copyright>.

## What is invented

Every network, by `make_rivera.py`, deterministically: run it twice and the bytes are the same.

```bash
uv run python samples/make_rivera.py
```

Twenty routers with SSIDs `sample-ap-*` and locally administered `02:` addresses, placed in a
frame of metres around the first mark: a few per block inside the buildings, one at each of the
two middle corners a little way up the cross street, and six far enough into the blocks to be
heard faintly and placed badly. Their signal falls off with a path loss exponent of 2.5, each
router with its own transmit power, a decibel of noise on every sighting and a little drift
between days, and nothing weaker than -90 dBm is heard. Enodia estimates with an exponent of 3,
so the world is not the model looking at itself. Every scan hears the world from where the
walker was three seconds before its timestamp, the way a real sweep ends after it began.

- `rivera-2026-09-14`: Soca to Obligado at 1.3 m/s, stopping for half a minute forty percent
  of the way down the middle block. 67 scans.
- `rivera-2026-09-17`: Obligado to Soca at 1.4 m/s, then back to Brito del Pino, so that one
  block is walked twice in one outing, once each way. 77 scans, five marks.
- `rivera-query.jsonl`: one scan taken on the middle block, sixty percent of the way from
  Brito del Pino towards Simón Bolívar, on 2026-09-21.

The timestamps in the notebooks are the moments the walk reached each mark, so log and
notebook agree by construction. No outing represented here happened, and the README says so
wherever the numbers are shown: this demonstrates the workflow, not measured accuracy on a
real walk.

## What is derived

Everything else is what the commands below produce from those files, and
`tests/test_samples.py` runs each of them and compares the result byte for byte, so none of
them can drift from the code: a checked-in artifact nobody can regenerate is indistinguishable
from one written by hand to agree with the documentation. The generator is held to the same
standard, against the logs.

- `rivera-report.txt`: the reconciliation of the first outing, with every scan placed

  ```bash
  enodia --reconcile samples/rivera-2026-09-14.jsonl samples/rivera-2026-09-14.txt \
    --streets samples/rivera-streets.jsonl --scans > samples/rivera-report.txt
  ```

- `rivera-networks.csv`, `rivera-walk.geojson`, `rivera-plan.svg`: the located routers, the
  walk for a map, and the offline plan. One run writes all three, separate from the one above
  because each of these flags adds a line to the report naming the file it wrote.

  ```bash
  enodia --reconcile samples/rivera-2026-09-14.jsonl samples/rivera-2026-09-14.txt \
    --streets samples/rivera-streets.jsonl --csv samples/rivera-networks.csv \
    --geojson samples/rivera-walk.geojson --svg samples/rivera-plan.svg
  ```

- `rivera-pace-check.txt`: each middle mark held out of the first outing and found again, by
  movement and by the clock

  ```bash
  enodia --reconcile samples/rivera-2026-09-14.jsonl samples/rivera-2026-09-14.txt \
    --streets samples/rivera-streets.jsonl --check-pace > samples/rivera-pace-check.txt
  ```

- `rivera-pass-check.txt`: the block the second outing walked twice, one pass against the other

  ```bash
  enodia --reconcile samples/rivera-2026-09-17.jsonl samples/rivera-2026-09-17.txt \
    --streets samples/rivera-streets.jsonl --check-passes > samples/rivera-pass-check.txt
  ```

- `rivera-map.jsonl`: the fingerprint map of both outings

  ```bash
  rm -f samples/rivera-map.jsonl
  enodia --map-add samples/rivera-2026-09-14.jsonl samples/rivera-2026-09-14.txt \
    --streets samples/rivera-streets.jsonl --map samples/rivera-map.jsonl
  enodia --map-add samples/rivera-2026-09-17.jsonl samples/rivera-2026-09-17.txt \
    --streets samples/rivera-streets.jsonl --map samples/rivera-map.jsonl
  ```

- `rivera-map-check.txt`: each outing held out and located from the other

  ```bash
  enodia --check-map --map samples/rivera-map.jsonl > samples/rivera-map-check.txt
  ```

And the answer the README shows, the query scan located against that map:

```bash
enodia --locate samples/rivera-query.jsonl --map samples/rivera-map.jsonl --voice none
```

It comes out 53% of the way from Brito del Pino to Simón Bolívar. The scan was taken at 60%.
