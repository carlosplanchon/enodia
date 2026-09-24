# Every flag

`enodia --help` is the authoritative list, and the one that cannot go out of date. `enodia
--version` says which Enodia this is. Installed with `uv tool install enodia` the command is
`enodia`. From a clone of the repository it is `uv run enodia`, and everything below reads
the same. This page groups the flags by the command you reach for
them with, and says what each is for and what it does when you leave it out.

Short forms: `-i` is `--interface` (repeatable, and without it every Wi-Fi interface is
watched), `-t` is `--interval`, `-l` is `--log`.

## On the walk

```bash
enodia                                    # every Wi-Fi interface, every 5 s, one log per outing
enodia -i wlan0 -t 10                     # one interface, every 10 s
enodia -l walk.jsonl                      # one log file of your choosing
enodia --dir ~/walks                     # one file per outing, in that directory
enodia --voice pico                       # engine: auto (espeak-ng, else Pico) | espeak | pico | none
enodia --lang en-GB --ssid-lang es-ES        # language of the announcements, and of the network names
enodia --say-status                       # also say "Scanning" and the time every cycle
enodia --say-status --say-signal          # ...and the signal quality, which seldom changes
enodia --say-time-every 30                # the time every 30 s, never skipped, with or without --say-status
enodia --quiet --say-time-every 30        # only the time, the button's marks and the failures
enodia --quiet --no-hour                  # only the button's marks and the failures
enodia --say-names 0                      # read only the open networks' names, never the rest
enodia --button /dev/input/event7         # mark crossings with that device's button. 'list', 'off'
enodia --no-fresh                         # read the daemon's view instead of scanning
enodia --no-log-every-scan                # log only connection changes and new networks
enodia --resume                           # carry on with the outing under way, if its log is under 30 minutes old
enodia --cycles 3                         # stop after 3 scans instead of running until Ctrl+C
```

## Before the walk

```bash
enodia --preflight                        # check everything an outing needs and exit
```

## Reading a log back

```bash
enodia --reconcile LOG notebook.txt                            # the report
enodia --reconcile LOG notebook.txt --pace clock               # share out each stretch on time
enodia --reconcile LOG notebook.txt --path-loss 2               # weigh the strongest sightings more
enodia --reconcile LOG notebook.txt --outing 3f9a2b10           # one walk of a file that holds several
enodia --reconcile LOG notebook.txt --scans                     # also list where every scan landed
enodia --reconcile LOG notebook.txt --csv networks.csv --geojson walk.geojson
enodia --reconcile LOG notebook.geo.txt --streets streets.jsonl                 # along the streets
enodia --reconcile LOG notebook.geo.txt --streets streets.jsonl --svg plan.svg
enodia --reconcile LOG notebook.geo.txt --streets streets.jsonl --svg plan.svg --svg-names   # and the names
enodia --open-networks LOG                                     # the unencrypted ones, strongest first
```

`--pace movement` is the default and reads the pace from how much the networks in view turn
over, so that a stop stays a stop. A hole in the scans, or a cycle that only one of two cards
answered, is no evidence either way and is filled at the pace of the rest of the stretch.
`--pace clock` interpolates on time instead, assuming a steady walk. Which one is better for
your route is not a matter of opinion: `--check-pace` below measures it.

`--path-loss N` is the exponent the sightings are weighed with when an access point is placed,
`10 ** (RSSI / 10n)`. The default is 3, a street with buildings on both sides. Lower makes the
strongest sighting count for more, and at 1 it is weighing by received power. It moves the
access points and what `--check-passes` compares, and nothing about where the scans are, and
the report says which exponent was used whenever it is not the default. On the first real
outing, six access points of one house at known coordinates came out 28 m off on average at
3 and 22 m at 1. One house, which is why it is a flag and the default has not moved.

`--outing TOKEN` picks one walk out of a file that holds several, which is what `--log
walk.jsonl` reused every week produces. Without it the last walk in the file is read, and which
one that was is printed whenever there is a choice.

`--streets FILE` places each scan along the street's real shape instead of on the straight line
between two crossings, which is what happens without it, and the report says how many blocks
followed the drawing. `--svg FILE` draws the walk as a plan from that same geometry, with no
tiles fetched, and needs coordinates on the crossings. `--svg-names` writes each pinned
network's name beside its dot, in small type: a plan of a few hundred networks is dense and
the names overlap, but the file is vector and zooms, and a hidden network has no name to
write. The plan then carries your neighbours' network names, as the CSV and the GeoJSON
already do.

`--csv`, `--geojson` and `--svg` each add a line to the report saying where they wrote. The
report itself goes to standard output, so capturing it wants a run without them.

## Does any of it work

```bash
enodia --reconcile LOG notebook.geo.txt --check-pace            # which pace method finds the crossings again
enodia --reconcile LOG notebook.txt --check-passes              # how far apart two passes put the same networks
enodia --check-map                                             # hold out a pass and locate it from the rest
enodia --check-map --match signal                              # the same, scored on signal as well
enodia --check-map --weigh alike                               # the same, every network counted alike
enodia --check-map --card-offset -6                            # heard as a card reading 6 dB lower would
```

`--check-pace` needs coordinates on the crossings, since it measures in metres along the walk.
`--check-passes` needs none: it compares two passes over one stretch against each other.
`--check-map` needs a map with at least two passes in it, and holds out one outing at a time,
or one pass down one stretch when the map holds a single outing, never a single scan, since a
scan's neighbour was taken five seconds later and sees almost the same networks. Its table has
six columns: each scan alone by networks, by signal as well, by signal with the card calibrated
on the run, with the scans before it settling a tie or choosing the path, the two ways
`--sequence` names, and at a walking pace, the way `--locate --watch` runs. `--card-offset DB`
hears every held-out scan as a card reading that many dB higher, or lower when negative, would
hear it, which is what another card costs each column and what calibrating wins back. Its row
"pulled in, near a corner" is how far the answers to scans taken near a corner land towards the
middle of the block, on average, and negative past the corner, the answers that went into the
block next door counted too. [The methodology notes](methodology.md) say what each of the three
can and cannot tell you.

## The map, and finding yourself again

```bash
enodia --map-add LOG notebook.txt                               # add an outing to the map
enodia --map-add LOG notebook.txt --streets streets.jsonl        # along the street shapes
enodia --locate                                                # scan now: where am I?
enodia --locate LOG                                            # or a log's last scan, with the ones before it
enodia --locate LOG --sequence path                            # let the scans before it choose the path
enodia --locate --watch                                        # keep scanning: where am I, as it changes
enodia --locate --match signal                                 # score on signal strength too
enodia --locate --weigh alike                                  # count every network the same
enodia --locate --along levels                                 # experimental: place it by the levels
enodia --map other.jsonl --locate                               # a map somewhere else
```

`--match networks` is the default and matches on which networks are in view, each weighed by how
rare it is in the map: a router heard everywhere says less than one heard on one block. `--match
signal` also weighs how strongly each came in, which is more precise and less portable between
radios, since two cards report different numbers for the same room. `--match signal` is barely
tested, and what little there is says it does worse: on the first real walk made to locate, it
answered "not on the map" for 38 of 111 scans taken on streets the map knows, where `--match
networks` lost none, and where both answered it was no more exact. It is there to be measured,
not to be relied on. `--weigh rarity` is that weighing, and the default. `--weigh alike` counts
every network the same, which is what the matching did before the weights, and is there so that
a real map can measure them the way the sample did: run `--check-map` with each and compare the
two tables. An outing already in the map is not added twice.

`--along levels` is experimental. The place along the stretch is otherwise the middle of the
fingerprints that matched; with it, each network heard on the stretch gets a curve of how its
level rises and falls along the block, fitted from the map, and the answer is where the scan's
levels fit the curves best. The stretch is still the one the matching chose. The fit is on the
shape of the levels, not on what they read, so another card lands in the same place, and it
needs no `--match signal`. Run `--check-map` with it and without it and compare the two tables:
it stays or goes on what walks measure.

An answer within 15 m of a mark names it, `at the corner of "Rivera y Soca"`, rather than saying
97% of the way: the error is typically about that size, and a few metres from a corner the
corner is the true answer. A mark that is not a corner of two streets, a plaza, is said as `at
"Plaza Independencia"`. Two stretches that meet at the corner the answer is at are not a doubt
about where you are, so a tie between them is not reported as one.

A fresh scan stands alone. `--locate LOG` has the scans before the last one, and when two
stretches match that scan about as well, the stretch the scans before it were on, or one next to
it, settles which: a walk does not jump a block in five seconds. They choose between two answers
and never make one up, so a scan the map does not know stays unknown however sure the scans
before it were.

A map that is not there is an error for `--locate`, not an empty map: "not on the map" is a
sentence about the street, and a path with a typo in it used to get that answer every cycle
of a `--watch`, for as long as you cared to walk. `--map-add` is what creates a map.

`--locate --watch` keeps the fresh scans coming, one every `--interval` seconds, and places each
with the ones before it the way a log's last scan is placed, so a tie is settled by the walk as
it happens. It prints one line per scan and speaks what changes: a new stretch, or the map
losing you or finding you again, is said in full, and another tenth of the way along the same
stretch is said only when the voice is free, since a percentage said late is another place. A
fresh scan takes about five seconds per card, so an interval shorter than that is not kept.
`--cycles N` stops it after N scans, and Ctrl+C whenever.

Each answer's place along its stretch is kept to a walking pace: a scan that lands thirty metres
from the last one, five seconds on, moves the answer part of the way, and a run of them take it
all the way, which is the difference between one noisy scan and having turned round. The stretch
is still the one the scans chose, and only the place along it is kept. It makes the answer
steadier, not more exact: `--check-map` measures both, in its last column. `--no-walking-pace`
takes each scan's place as it comes. The pace is 2.5 m/s at most, and `--max-speed` raises it:
on a bicycle, `--max-speed 7`, or the dot trails behind you and reaches every corner late. A map
built on foot and used from a bicycle matches a little worse whatever the pace, since a scan
takes about five seconds and a bicycle covers a quarter of a block in that time.

With `--match signal` the run also calibrates the card against the map. Two cards hear the same
network several dB apart, and comparing levels takes that for a poorer match. So wherever
matching on the networks alone is sure of the place, the levels this card heard are set against
the ones the map kept there, and once there are thirty such pairs, about half a minute of walk,
their median corrects every level before it is compared. It is said once: `Calibrated against
the map: this card reads 6 dB below the map's card, and is corrected for that`. It is learned
afresh every run and never kept, since the difference is against the card that built this map,
and a difference past 20 dB is not a card reading differently: it is reported and not corrected.
`--match networks` compares no levels and has nothing to calibrate, and what `--log` records is
what the card heard, uncorrected.

```bash
enodia --locate --watch --log watch.jsonl
enodia --locate watch.jsonl --map other.jsonl
```

With `--log FILE` it also records what it scanned, one scan record per cycle in the walk's own
format, so the run can be located again later with `--locate FILE`, against another map or with
other flags. Without it nothing is written, and `--dir` is refused: a run that only locates is
not an outing, and does not belong among them.

```bash
enodia --locate --watch --live-map live.html --streets streets.jsonl
```

`--live-map FILE` draws the same run: every cycle it writes a page with the map's own
fingerprints as grey dots, where you are as a large one with your last few answers fading behind
it, and the line the terminal printed on top. Open it once in a browser and leave it open, since
the page reloads itself every `--interval`. An answer the scans could not settle is drawn in
another colour, and when the map loses you the last place it knew stays greyed. `--streets` adds
the streets, and whatever `--surroundings` brought: the water, the parks and every named street.
It needs a map whose fingerprints carry coordinates, which is one built from a notebook that
`--geocode` has been through. A `--streets` file that is not there is an error, as a missing map
is, and not a page quietly drawn with no streets. The mouse wheel zooms where the pointer is, a
drag moves the view, and the buttons zoom in, out, back to the whole map, or follow you: zoomed
in, each reload keeps you in the middle until you drag the map away. The keys `+`, `-`, `0` and
`f` do the same. The zoom is kept in the page's address, `live.html#x,y,width`, which is how it
lasts from one reload to the next.

`--sequence tie` is that, and the default. `--sequence path` asks the scans before it every
time, not only on a tie: it chooses the likeliest path through all of them, staying on a stretch
for nothing, stepping onto one that shares a mark for a little, jumping anywhere else for a lot,
and the answer is where that path ends. It can overrule the last scan, in both directions, and
the price of a jump is a number no walk has measured yet, which is why it is a flag:
`--check-map` reports both. It is barely tested and so far no better: on the sample and on the
first real outing it put fewer scans on the right stretch than `--sequence tie`, 135 against 136
and 55 against 69, and on the one walk made with it, it came onto a new block two scans late
once in eighteen turns and was never right where the default was wrong.

## Putting the notebook on the map

```bash
enodia --geocode notebook.txt --area Montevideo                                 # look the corners up
enodia --geocode notebook.txt --area Montevideo --out notebook.geo.txt           # name the output
enodia --geocode notebook.txt --area Montevideo --marks LOG                     # time the untimed lines
enodia --geocode notebook.txt --area Montevideo --proxy socks5://127.0.0.1:9050
enodia --geocode notebook.txt --area Montevideo --streets streets.jsonl          # keep the street shapes
enodia --geocode notebook.txt --area Montevideo --streets streets.jsonl --surroundings
enodia --geocode notebook.txt --area Montevideo --overpass-url https://overpass.kumi.systems/api/interpreter
enodia --geocode notebook.txt --area Montevideo --marks LOG --max-speed 6         # an outing by bicycle
```

This is the one command in Enodia that goes online, and only when you type it. `--area` is not
optional: without it `Freire` matches a street in Chile. It takes a place as OpenStreetMap names
it, or four numbers `s,w,n,e` for a bounding box.

A public Overpass instance is often too busy to answer, and says so with a 429, a 504 or a page
saying it is too busy. That is asked again three times, after 15, 30 and 60 seconds (or however
long the server asks for, up to two minutes), with a line saying so each time. Anything else it
answers is not asked again, and an error comes out as the sentence the server wrote, not its
HTML. If it is still busy after that, try again later or name another instance with
`--overpass-url`.

`--streets FILE` keeps the shapes of the streets the notebook names, from the same one request.
`--surroundings` adds a second request for the neighbourhood around the corners it found, 300 m
on every side: the buildings, the water, the parks and every named street, walked or not, all
into the same file for `--svg` and `--live-map` to draw. The streets nobody walked are kept
apart and never used to place a scan. The data is OpenStreetMap's, under the Open Database
License, and every picture drawn from it credits it at the foot. A line that already carries
coordinates is never looked up again, but its streets are still asked for, so running
`--geocode` on a notebook it has already been through, the `.geo.txt` itself, is how to fetch
the shapes or the neighbourhood afterwards. When there is no street to ask about, the file is
left as it was rather than written empty.

The original notebook is never touched. Without `--out`, the result goes beside it with `.geo`
before the suffix. `--proxy` sends the one request through SOCKS5 and the proxy resolves the
hostname, never this machine. `ALL_PROXY` and the rest of the environment are deliberately never
read, and if the proxy cannot be reached nothing is sent. It needs the `socks` extra:
`uv tool install "enodia[socks]"`, or `uv sync --extra socks` in a clone.

The notebook's own times check the answers: a stretch the coordinates say was covered faster
than anyone walks, 2.5 metres a second, is taken for a wrong lookup and neither of its corners
is written. `--max-speed` moves that ceiling, in metres a second, for an outing ridden rather
than walked. [The methodology notes](methodology.md) say what the check can and cannot catch.

## What is spoken

Every message is printed as `Say (Silent: ...) > text`. Events are spoken. The per-cycle status
is only printed unless you pass `--say-status`.

| Message | Spoken by default | With `--say-status` |
|---|---|---|
| "Now connected to ..." and "Now disconnected" | yes | yes |
| "Roaming on ...", between access points of one network | no, printed only | yes |
| "New network found", or "12 new networks" and "2 open" | yes | yes |
| The names of the new networks: all of them up to `--say-names` per cycle (default 3), only the open ones past that | yes, as many as speech keeps up with | same |
| "Interface not found." | yes | yes |
| "Radio blocked on wlan0", when rfkill has the Wi-Fi radio switched off, and then "Still no scan" and "Scanning again" as for any failed scan | yes | yes |
| "Scan failed on wlan0", then "Still no scan on wlan0" once a minute while it lasts, then "Scanning again on wlan0" | yes | yes |
| "The laptop slept for 12 minutes", on waking from a suspend | yes | yes |
| "Mark 7", on each press of the headset button. "Button ready", "Button unavailable", "Button lost" | yes | yes |
| "Battery at 20 percent", then at 10, while discharging | yes | yes |
| "Scanning", at the start of every cycle | no, printed only | yes |
| "Signal quality is N" | no, printed only | no, printed only. `--say-signal` says it |
| The time, at the end of every cycle, on the 24-hour clock | no, printed only | yes |
| The time every `--say-time-every` seconds, never skipped | with the flag | with the flag, instead of the per-cycle one |

Speech runs in its own thread and never holds up a scan. It is slow (espeak-ng takes nearly four
seconds just to say the time), so when there is more to say than time to say it, network names
are dropped and everything else still gets through. The per-cycle status ("Scanning", the
signal, the time) is said only when the voice is free. Said late it would be wrong, and the time
is what the notebook is written from, so a status that arrives while speech is behind is skipped
and the next cycle brings a current one. Events are never skipped.

`--quiet` turns the table around: nothing about networks or connections is spoken (it is all in
the log), and what remains is the time, every cycle or every `--say-time-every` seconds, the
button's marks, and the failures, which you cannot afford to miss. `--no-hour` silences the time
everywhere, `--quiet` included: with a headset button the marks carry it, and
`--quiet --no-hour` leaves nothing but the marks and the failures. If you would rather have the
time on a fixed beat, `--say-time-every 30` says it every thirty seconds as an event, never
skipped, at the cost of hearing it a few seconds late when it lands in the middle of a name.

Scans keep the interval asked for: a cycle sleeps only what is left of it after the scanning is
done. Every message is printed whether or not it is spoken, and the log always has every
network, so nothing is lost but the audio.

## Sharing a walk

```bash
enodia --export-public walk.jsonl notebook.txt --out published/
enodia --export-public walk.jsonl notebook.txt --ssid pseudonym         # names kept apart, not dropped
enodia --export-public walk.jsonl notebook.txt --ssid keep              # names as they are
enodia --export-public walk.jsonl notebook.txt --mac-shaped             # d2:17:43:.. rather than ap-1c8a74f992ae
enodia --export-public walk.jsonl notebook.txt --key-file ~/keys/enodia.key
enodia --export-public walk.jsonl notebook.txt --outing 3f9a2b10          # one walk of a file that holds several
enodia --export-public walk.jsonl notebook.txt --keep-places              # the streets as they are, only the networks hidden
enodia --export-public walk.jsonl notebook.txt --keep-time                # the day and the hour as they were
```

Both files at once, and `--out` names the directory, `public/` by default. The directory is the
export rather than a place to put files: it is built beside where it goes and moved into place
whole. So `--out` must not already exist, not even empty, since the second run is usually the
one after somebody has read the first line by line. Two exports aimed at one directory are safe
for the same reason: the first to claim the name wins and the second is told the place is taken.
The export is written privately and opened up only when it is complete. To read it privately
before deciding whether to publish it, `chmod 700` it afterwards.

`--keep-places` leaves the crossings named and placed as they are and substitutes only the
networks, for somebody content to publish the streets they walked: the route is then on the map
for anyone to see, and so is roughly where each access point along it stands. The clock is moved
all the same, to 1970-01-01, and `--keep-time` leaves it as it was: the day and the hour of the
walk are then published with it. [Publishing a walk](export.md) says what each choice gives up.

An `event` this does not recognise is left out of the export with the rest of its record, and a
`security` label the backends are not known to write is withheld. Both are counted in the
report. If a real walk reports withheld labels, the daemon is saying something this has not met
and the values are worth passing on.

Every export ends by searching what it is about to write for every address, network name,
interface and street name the two source files carry, and names anything it finds. That is a
substring search over the bytes themselves, so an ordinary network name matches something
innocent and the block says so: what it is really for is the field somebody adds to the log
next year and forgets to substitute. Nothing replaces reading the file before publishing it,
which the command says too.

The key lives in `$XDG_CONFIG_HOME/enodia/export.key`, made once with permissions only its
owner can read, and it is what makes two exports months apart agree with each other. Keeping it
is the whole point. Lose it and the next export gives the same router a different name.
[Publishing a walk](export.md) says what the export promises, what it cannot, and why each rule
is there.

## The menu, if you would rather not remember any of this

```bash
enodia --assistant                                 # the whole workflow, one screen at a time
enodia --assistant --dir ~/walks --map other.jsonl          # over a different directory and map
enodia --assistant --log walk.jsonl                # over one file, and every walk inside it
enodia --assistant -i wlan0 --voice pico           # and every walk it starts uses these
```

`--assistant` takes the configuration flags and refuses the command ones: `--preflight`,
`--reconcile`, `--map-add`, `--locate`, `--check-map`, `--geocode`, `--open-networks`,
`--export-public` and `--button list` are each a command of their own, and the assistant is the
menu that runs them.
Asking for both at once exits with status 2, the way any other contradiction on the command
line does.

The screens, and where each goes:

```
ENODIA                       the machine, the suggested step, and eight ways on
  1  Start a new outing  ->  the walk, then OUTING COMPLETE
  2  Carry on            ->  the same, resuming the outing under way
  3  Reconcile           ->  WHICH OUTING -> NOTEBOOK FOR ... -> RECONCILE
  4  Add to the map      ->  WHICH OUTING -> NOTEBOOK FOR ... -> runs it
  5  Locate              ->  LOCATE: scan now, from a log's last scan, or follow along
  6  Measure the map     ->  --check-map, or why there is nothing to measure yet
  7  Look at the outings ->  OUTINGS
  8  Preflight           ->  all eight checks
  q  Quit
```

Three keys mean the same thing everywhere. Enter takes the step the screen suggests, which on
the main menu is the one the rules picked and on every other screen is the obvious one. `b`
goes back where back is somewhere else, and `m` returns to the top from a screen where it is
not. A key that is none of these says so and asks again rather than picking the nearest.

Ctrl+C at a prompt abandons the question and returns to the menu. Ctrl+C during a walk ends the
outing and hands the menu back, with the log just written already chosen. Ctrl+D leaves.

## The ones that only make sense together

Enodia refuses a combination that cannot mean anything rather than ignoring half of it, and
says which flag is the odd one.

| flag | needs |
|---|---|
| `--area`, `--marks`, `--proxy`, `--overpass-url` | `--geocode` |
| `--max-speed` | `--geocode` or `--locate --watch`, and not with `--no-walking-pace` |
| `--out` | `--geocode` or `--export-public` |
| `--ssid`, `--mac-shaped`, `--key-file`, `--keep-places`, `--keep-time` | `--export-public` |
| `--streets` | `--geocode`, `--reconcile`, `--map-add` or `--live-map` |
| `--surroundings` | `--geocode` and `--streets` |
| `--live-map`, `--no-walking-pace` | `--locate --watch` |
| `--card-offset` | `--check-map` |
| `--svg`, `--check-pace`, `--check-passes`, `--path-loss`, `--csv`, `--geojson`, `--scans` | `--reconcile` |
| `--svg-names` | `--svg` |
| `--pace` | `--reconcile` or `--map-add` |
| `--map`, `--match`, `--weigh`, `--along`, `--sequence` | `--map-add`, `--locate`, `--check-map` or `--assistant` |
| `--outing` | `--reconcile`, `--map-add`, `--export-public`, `--locate LOG` or `--geocode --marks` |
| `--watch` | `--locate` scanning live, not `--locate LOG`, and never with `--dir` |

Each of those exits with status 2, the way a bad command line does.

## Where things are written

Without `--log`, an outing writes to `$XDG_DATA_HOME/enodia/` (that is `~/.local/share/enodia/`
unless you set the variable), or to the directory `--dir` names, in a file named by the time it
started, and the button's marks count from one. The path is printed when the outing starts and
again when it stops, since by then the first line is long gone. `--resume` carries on with the
outing under way instead, when the newest log there was written to less than thirty minutes ago:
a laptop that ran out of battery, a walk stopped by mistake. Older than that, it starts a new
outing like any other run. `--log FILE` appends to one file of your choosing, whatever its age,
and there the marks start again from one inside the same file, so restarting mid-outing with
`--log` wants `--resume` too. The fingerprint map lives in
`$XDG_DATA_HOME/enodia/map/map.jsonl`, in a directory of its own so that `--resume` never
mistakes it for an outing's log, and `--map FILE` puts it anywhere you like.

See [the README](../README.md) for what these commands do, and [the design notes](design.md)
for why several of them refuse more than they answer.
