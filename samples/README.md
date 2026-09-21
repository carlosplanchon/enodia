# Agraciada sample

This is a runnable, privacy-safe example of Enodia's map and `--locate` flow.

The street names, crossing coordinates and street geometry are real OpenStreetMap data:

- Avenida Agraciada y Doctor Salvador García Pintos: `-34.8794754, -56.1959854`
  (OSM node `917370075`)
- Avenida Agraciada y San Fructuoso: `-34.8807492, -56.1955157`
  (OSM node `917554864`)
- the block between them is OSM way `270514503`, 147.98 m along its four vertices

They were verified against OpenStreetMap on 2026-09-19. The street data is available under
the Open Database License (ODbL); see <https://www.openstreetmap.org/copyright>.

The Wi-Fi observations are deliberately synthetic. The SSIDs say `sample-ap-*`, the BSSIDs
use the locally administered `02:` range, and no outing represented here actually happened.
Keeping that boundary explicit makes the geographic output honest without publishing a radio
fingerprint of somebody's street.

Build the sample map from two synthetic passes over the real block:

```bash
rm -f /tmp/enodia-agraciada-map.jsonl
enodia --map-add samples/agraciada-2026-09-14.jsonl \
  samples/agraciada-2026-09-14.txt --streets samples/agraciada-streets.jsonl \
  --pace clock --map /tmp/enodia-agraciada-map.jsonl
enodia --map-add samples/agraciada-2026-09-17.jsonl \
  samples/agraciada-2026-09-17.txt --streets samples/agraciada-streets.jsonl \
  --pace clock --map /tmp/enodia-agraciada-map.jsonl
enodia --locate samples/agraciada-query.jsonl \
  --map /tmp/enodia-agraciada-map.jsonl --voice none
```

Or run the last command against the checked-in `agraciada-map.jsonl` directly. Regenerating it
is still worth showing: it proves that the output is derived from the logs and notebooks rather
than hand-written to agree with the README.

The remaining files are ordinary Enodia outputs, and the commands that make them are here for
the same reason the map's are: a checked-in artifact nobody can regenerate is indistinguishable
from one written by hand to agree with the documentation. `tests/test_samples.py` runs these on
every test run and compares the results to the files beside it, so they cannot drift.

- `agraciada-report.txt`: reconciliation report for the first pass

  ```bash
  enodia --reconcile samples/agraciada-2026-09-14.jsonl \
    samples/agraciada-2026-09-14.txt --streets samples/agraciada-streets.jsonl \
    --pace clock --scans > samples/agraciada-report.txt
  ```

- `agraciada-networks.csv`: located synthetic access points
- `agraciada-walk.geojson`: route, crossings and access-point estimates
- `agraciada-plan.svg`: the same route as Enodia's offline vector plan

  One reconciliation writes all three. It is a separate run from the one above because each of
  these flags adds a line to the report saying where it wrote its file.

  ```bash
  enodia --reconcile samples/agraciada-2026-09-14.jsonl \
    samples/agraciada-2026-09-14.txt --streets samples/agraciada-streets.jsonl \
    --pace clock --csv samples/agraciada-networks.csv \
    --geojson samples/agraciada-walk.geojson --svg samples/agraciada-plan.svg
  ```

- `agraciada-map-check.txt`: validation of the two-pass map, each outing held out and located from the other

  ```bash
  enodia --check-map --map samples/agraciada-map.jsonl > samples/agraciada-map-check.txt
  ```
