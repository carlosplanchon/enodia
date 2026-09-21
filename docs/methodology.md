# How Enodia argues, and how it checks itself

The [README](../README.md) says what each command does. This says how the estimates are made,
what the three self-checks measure, and what comes out of them. Every number below comes from a
synthetic route built to have a known answer, not from a walk: see *Limits* in [the README](../README.md).

## Where the access point is

Every network gets the position of the scan where its signal was strongest: that is where *you* were. It also gets an estimate of where the *access point* stands, worked out from every place it was heard, which is a different question and a better answer. The estimate is a weighted centroid: under the log-distance model a signal falls off as `RSSI = A - 10 n log d`, so a sighting counts as `10 ** (RSSI / 10n)`, and the unknown transmit power cancels when the weights are normalised. The default path loss exponent is 3, a street with buildings on both sides. At n=1 the strongest sighting would swamp the rest and the estimate would add nothing to it, which is the whole reason for not taking it alone.

With coordinates on the crossings the centroid is a latitude and longitude, and `spread_m` says in metres how far the sightings sat from it. Without them it still runs, in the one dimension a notebook always has: how far along a stretch between two named crossings you were. A notebook of bare crossing names therefore places every access point too, as a fraction of one block, and the spread comes out as a fraction as well.

Sightings from two different stretches are not averaged together. The route is a path, not a ruler: walk a block and turn back, and the same doorway is covered twice at two very different distances from the start, so averaging those puts every access point at the corner you turned round at, the one place none of them is. An access point belongs to the stretch it was loudest on, and only the sightings made there place it. Every pass over that stretch does count, including one that ran the other way, which is what makes the next section possible.

Most of what a walk hears is not on the street it walked. It is inside the block, or a street over, heard faintly from wherever you happened to be, and the estimate still puts a point on the map for it. So each estimate is measured against the same sightings read with the signal ignored, and what is compared is how tightly they gather, not where their middle lands. The sightings have a spread around the weighted estimate and a spread around their own plain middle, and the signal has to bring the first down to three quarters of the second or less. Walk past an access point and its signal peaks sharply, pulling the sightings in around one point. Hear one from a block away and every sighting weighs about the same, the cloud is as wide as the stretch you walked, and the estimate says nothing whatever about where the thing is. The second kind is marked **barely pinned down**, in the report, in the `barely_pinned` column of the CSV and in the GeoJSON properties, and the summary says how many of them there were.

On a synthetic grid walk this separates cleanly: an access point walked straight past came out exactly where it stood and unmarked, while one sitting 100 metres off the route was placed on a street it is not on, 152 metres from the truth, and marked. Before this, the two lines of the report looked alike.

Two biases are worth knowing before you draw anything on a map. A walk down one street sees an access point from a line, and a line says nothing about which side of the street it is on: the estimate lands on the street you walked. Walking a neighbourhood in a grid rather than one street end to end is the cure, since the sightings then spread in two dimensions instead of lying along one. And a centroid is pulled towards the middle of its sightings, so an access point near either end of your route comes out nearer the middle than it is. When the strongest sighting is at the edge of the walk, treat it as a floor on how far away the access point can be, not as a number to average away from.

## Does reading the pace actually help?

The notebook is the only ground truth there is, so it is also the test. `--check-pace` holds out each crossing in turn, reconciles without it, and measures how far each method puts the scan nearest that crossing's time from where the crossing actually was. On a **synthetic** two-block route built with a known six-minute stop in the first block (not a real outing, see *Limits* in [the README](../README.md)) it reports:

```
Crossings held out, and how far each method put the nearest scan from them:
  crossing                            by movement   by clock
  Avenida Uno y Calle B                       8 m       50 m  <-

  mean error: 8 m by movement, 50 m by clock
  Reading the pace wins by 41 m on average.
```

It needs coordinates on at least three crossings in a row, since without them there is no distance to measure. If the clock wins on your route the report says so, and `--pace clock` is one flag away. That the question is answerable with the walk you already did matters more than which way it comes out: neither method has been checked against a real outing yet.

## The same block, walked twice

Walk a block, turn round at the corner and walk it back, and you have left a small controlled experiment inside an ordinary outing: the same access points, the same street, the pace and the direction the only things that changed. `--check-passes` finds any stretch the notebook shows walked more than once (two crossings named the same pair of names, whichever way round) and compares what each pass said. It needs no coordinates.

```
Stretches walked more than once, and how far apart the passes put the networks:

  "Treinta y Tres" to "Solari", 180 m
    17:00:05 to 17:01:55, walked there
    17:02:05 to 17:03:55, walked back
    4 networks heard on more than one pass
    the passes disagree by 4% of the stretch (7 m) on average
    shift in the direction of travel: 2% (3 m), which is what a scan's lag looks like

  Mean disagreement over 1 stretch: 4% of a stretch.
```

(A **synthetic** out and back built with three seconds of scan lag in it, not a real outing. See *Limits* in [the README](../README.md).)

Two numbers come out, and they mean different things. The **disagreement** is how far apart the two passes put the same access point. It is a measure of consistency and not of accuracy: it is taken against nothing but the walk itself, so two passes can agree closely and both be displaced from where the access point really stands. Anything that biases both passes the same way is invisible to it, and the straight-street case below is exactly that. The **shift** is systematic rather than random, and it is the more interesting one. A scan sweeps its channels over seconds and is stamped when it finishes, so an access point was always heard a little before it was recorded, which places it a little further along than it really is, in whichever direction you happened to be walking. Walk back and that error reverses. The two estimates therefore straddle the truth, half the gap between them is the lag, and their midpoint cancels it. It is the same reasoning as a reciprocal levelling in surveying.

Read the shift as an upper bound rather than a measurement: it also absorbs whatever else differed between the passes, such as which side of the street you took or which shoulder the laptop hung from.

## What the map file is

One JSON object per line, like everything else Enodia writes.

```json
{"time": "2026-09-14T17:03:24-03:00", "outing": "2026-09-14T17:00:30-03:00/sample-a14", "walk": "2026-09-14T17:00:30-03:00/sample-a14#0",
 "from": "Avenida Agraciada y Doctor Salvador García Pintos", "to": "Avenida Agraciada y San Fructuoso", "fraction": 0.68,
 "lat": -34.880341, "lon": -56.195664, "length_m": 148.0,
 "networks": [{"ssid": "sample-ap-04", "bssid": "02:00:00:00:00:04", "signal_dbm": -41}]}
```

That is an abbreviated object copied from the checked-in sample map: the place and time are
real outputs of the sample pipeline, while the `sample-*` radio identity is explicitly fictitious.

`outing` names the walk by when it began and by the token it wrote on its own scans, never by the log's filename. A `walk` is one outing down one stretch, `outing#segment`, which is the unit `--check-map` holds out. `from`, `to` and `fraction` are in the sorted frame, while the names stay as the notebook wrote them, and only what matching uses is kept: not the security and not the band, since neither says anything about where you are. [The design notes](design.md) say why the clock alone was not enough to name an outing.

A network with nothing to identify it, no BSSID and no name, is kept in the log and left out of the matching: the empty key it would have is the same empty key every other anonymous network in the world has. [The design notes](design.md) say what that cost before it was caught.

Be clear about what this file is. It is a geolocation database of your neighbours' routers, names included, keyed to the street corners they sit near, small enough to mail and easy to grep. A map Enodia creates is readable only by you, and one you have set permissions on keeps them. That is exactly what makes it work and exactly why it is worth keeping to yourself. `.gitignore` excludes `*.jsonl` already. Keeping the SSID is deliberate, because a map you can read by eye is a map you can check.

Every number in the map is checked on the way back in rather than trusted, since it is a file that gets copied about and edited by hand, and a BSSID arrives in one spelling of itself. [The design notes](design.md) say what each check refuses and which bug asked for it.

## Does the map actually find you?

`--check-map` holds out one outing at a time and locates every scan of it from the other outings. A map with a single outing holds out one pass down one stretch instead, which is what a `walk` is in the map, so an outing that doubled back is tested on the pass it did not train on, over the same street: the thing worth knowing, from data an ordinary walk already produced, and the report says that such a result is the map recognising a walk rather than a place. Holding out one scan at a time would be worthless: its neighbour was taken five seconds and six metres later and sees almost exactly the same networks, so the map would be scoring itself on a copy of the question.

```
Map: 12 fingerprints, 2 walks over 1 stretches, 2 outings.

Each outing held out in turn, and its scans located from the other outings:

                                by networks  and by signal   in sequence
  scans held out                         12             12            12
  placed on the right stretch            12             12            12
  placed across a mark                    0              0             0
  landed on the wrong stretch             0              0             0
  not on the map                          0              0             0
  mean error, of a stretch               7%             2%            7%
  median error, of a stretch             5%             2%            5%
  mean error in metres                 10 m            3 m          10 m
  median error in metres                7 m            3 m           7 m
```

(Two **synthetic** passes over the real 148 m Agraciada geometry in [`samples/`](../samples/README.md), not real outings. See *Limits* in [the README](../README.md).) The error is given twice on purpose. A fraction of a block is comparable between any two stretches, and metres are only known for the stretches whose crossings carry coordinates, so a map that mixes notebooks with and without them says how many of the answers the distances actually cover instead of averaging the measurable half and calling it the whole. Signal wins here because the synthetic levels were made to vary smoothly along the block. Only a real outing can say whether that survives different radios, bodies and days. The important structural point is that each pass is held out against the other outing, so none of these answers is the map recognising the walk it trained on. A scan answered on the stretch next door, a few metres past the mark, is placed across a mark and measured through it. Only a stretch with no mark in common is a different street. The third column places each scan with the scans before it, the way `--locate LOG` does: when two stretches match a scan about as well, the stretch the scans before it were on, or one sharing a mark with it, is taken, since a walk does not jump. A scan the map does not know stays unknown in every column.

## What comes out

`--csv FILE` writes the located networks as a table, one row per network, with where you were when it came in strongest, the access point estimate along the route (`estimated_from`, `estimated_to`, `estimated_fraction`, `estimated_spread`), whether the walk pinned it down at all (`barely_pinned`) and, when the crossings have coordinates, the estimate on the map and its spread in metres. `--geojson FILE` writes all of it as GeoJSON, which uMap or QGIS draw as it is: a Point per network at the estimate, or at the strongest sighting when there is none (`placed_by` says which), a Point per crossing, and the route as a LineString.

## The lookup refuses more than it resolves, on purpose

A corner placed on the wrong street moves every scan of that block, and the reconciliation then comes out confidently wrong, which is worse than coming out short. So each of these is reported by line number and left alone:

- a name that is not two streets ("Plaza Independencia", and "Treinta y Tres" is one street, not a corner of Treinta and Tres)
- a street the area does not have under the name you wrote
- two streets that cross in two places, with both places and the distance between them named
- an answer the server admits is incomplete
- a corner the walk says could not have been reached on foot

That last one is the interesting one. **Your own walk checks the lookup.** The notebook has the times, so the coordinates imply a speed for every stretch, and nobody covers eight hundred metres in ninety seconds. A stretch that says they did means one of its two corners is in the wrong place, and since there is no telling which, neither is written. Only a high speed counts: a slow stretch is someone standing still, which is ordinary and is why `place_by_movement` exists. A notebook written with the headset button carries no times of its own, so pass `--marks` with the outing's log and the check runs off the button presses instead. Without it the report says plainly that nothing was checked.

One request goes out for the whole notebook, whatever it costs in corners, and the crossings are worked out here from the answer. Streets are asked for by exact name and never by a pattern, because Overpass matches with POSIX regexes which do not do multibyte Unicode, so `Yaguarón` in a pattern is a coin toss. The tolerance for case and accents is applied at home instead, with the same folding rule the rest of Enodia uses, and a street is recognised under `alt_name` and the other names the map may carry it under.

## Through Tor, if you want

```bash
uv run enodia --geocode notebook.txt --area Montevideo --proxy socks5://127.0.0.1:9050
```

The list of corners somebody is about to walk is not nothing, so the one request can go through a SOCKS5 proxy. `9050` is the Tor daemon, `9150` is Tor Browser. Needs `uv sync --extra socks`.

Four things about it are worth stating rather than assuming. The proxy resolves the hostname, never this machine, so the DNS lookup does not announce what is about to be asked. **If the proxy cannot be reached the command fails and sends nothing**, because a quiet fall back to a direct connection would undo the only thing the flag was for. `ALL_PROXY`, `HTTPS_PROXY` and the rest of the environment are never read, so what the command says is where the request goes: this is structural rather than a rule somebody has to keep remembering, since the module uses `http.client`, which has no idea the environment exists. And `--overpass-url` picks the endpoint, which matters here because several public instances turn Tor exits away.

Coordinates from OpenStreetMap are ODbL. In your own notebook for your own walk that is nothing to think about. Publishing a database derived from them comes with share-alike obligations.

## Following the street instead of the chord

A scan between two crossings sits some fraction of the way along, and without knowing what the block looks like that has to be a straight line from one corner to the other. The two ends come out right and the middle drifts wherever the block bends.

The lookup already knows better and was throwing it away. The one Overpass request comes back with every way of every street the notebook names, node by node, which is the street drawn. `--streets FILE` keeps it:

```bash
uv run enodia --geocode notebook.txt --area Montevideo --streets streets.jsonl
uv run enodia --reconcile walk.jsonl notebook.geo.txt --streets streets.jsonl
uv run enodia --map-add walk.jsonl notebook.geo.txt --streets streets.jsonl
```

Written by `--geocode`, read by `--reconcile` and `--map-add`, one JSON object per way, coordinates inline so the file stands on its own and nothing is asked of OpenStreetMap again.

A file that is not there yet is no geometry, which is the ordinary case before `--geocode` has written it. A file that is there and cannot be read is an error that stops the command. The difference matters more than it looks: reporting an unreadable file as an empty one puts every block back on the chord, and that answer arrives looking exactly like a good one. Within a file, a shape that is not a shape is skipped, so a coordinate that is not two finite numbers on the earth costs its own point and a line left with fewer than two points is not a line.

On a **synthetic** block that bends north and comes back, the middle scan moves 78 metres: onto the street, from the chord it was on before. The block also stops being 456 metres of straight line and becomes the 491 metres somebody actually walks. That length is what `--check-map` reports distances in and what turns a spread in fractions into metres, so it is not only the picture that improves.

Everything downstream follows without being asked: the access point centroid, the GeoJSON, the coordinates the fingerprint map stores, the distances `--check-pace` measures.

It is entirely optional and it never guesses. Without a streets file, or for a block Enodia has no drawing of, or where the drawing cannot be matched to both crossings within 25 metres, or where the way between them runs more than three times the straight distance (a loop, or the long way round a one-way pair), the position falls back to the straight line, which is what it has always done. The ways of one street are chained end to end before a block is looked for, since OpenStreetMap starts a new way wherever a tag changes, and the report says how many blocks followed the drawing. A mark is matched to the nearest point of the way rather than to a vertex, so a mark halfway down a straight block gets its drawing too.

## Drawing it

```bash
uv run enodia --geocode notebook.txt --area Montevideo --streets streets.jsonl --buildings
uv run enodia --reconcile walk.jsonl notebook.geo.txt --streets streets.jsonl --svg plan.svg
```

An SVG, written once, that opens in any browser with nothing fetched. The lines are the ones OpenStreetMap draws, rendered here rather than there. `--buildings` is a second request, asked for separately because the box to ask about is not known until the crossings are, and it turns the picture from lines into blocks.

No tiles, and that is deliberate rather than lazy. The OSM wiki says of the standard tile layer that it is *"not designed and suited for heavily used applications"* and asks that bulk downloading be respected, so a distributed tool that fetches tiles is the case the policy is about. Drawing the vector data offline sidesteps all of it, keeps the reconciliation offline, and keeps the geometry measurable instead of only visible.

If you would rather have a real slippy map with a basemap, that already works and always did: `--geojson` writes the walk out and uMap, QGIS or geojson.io draw it over OSM's own map, under their tile arrangements rather than Enodia's.

**What the picture refuses to do is flatter itself.** A dot on a plan of real streets reads as a fact, and most of what a walk hears was never established. So an access point the walk pinned down is a dot, one it did not is a dashed ring the size of how far its sightings were spread, with nothing in the middle, and the legend says how many of each there were. Open networks get their own colour. There is a scale bar, because a plan you cannot measure is a drawing.
