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
enodia --reconcile LOG notebook.txt --outing 3f9a2b10           # one walk of a file that holds several
enodia --reconcile LOG notebook.txt --scans                     # also list where every scan landed
enodia --reconcile LOG notebook.txt --csv networks.csv --geojson walk.geojson
enodia --reconcile LOG notebook.geo.txt --streets streets.jsonl                 # along the streets
enodia --reconcile LOG notebook.geo.txt --streets streets.jsonl --svg plan.svg
enodia --open-networks LOG                                     # the unencrypted ones, strongest first
```

`--pace movement` is the default and reads the pace from how much the networks in view turn
over, so that a stop stays a stop. A hole in the scans, or a cycle that only one of two cards
answered, is no evidence either way and is filled at the pace of the rest of the stretch.
`--pace clock` interpolates on time instead, assuming a steady walk. Which one is better for
your route is not a matter of opinion: `--check-pace` below measures it.

`--outing TOKEN` picks one walk out of a file that holds several, which is what `--log
walk.jsonl` reused every week produces. Without it the last walk in the file is read, and which
one that was is printed whenever there is a choice.

`--streets FILE` places each scan along the street's real shape instead of on the straight line
between two crossings, which is what happens without it, and the report says how many blocks
followed the drawing. `--svg FILE` draws the walk as a plan from that same geometry, with no
tiles fetched, and needs coordinates on the crossings.

`--csv`, `--geojson` and `--svg` each add a line to the report saying where they wrote. The
report itself goes to standard output, so capturing it wants a run without them.

## Does any of it work

```bash
enodia --reconcile LOG notebook.geo.txt --check-pace            # which pace method finds the crossings again
enodia --reconcile LOG notebook.txt --check-passes              # how far apart two passes put the same networks
enodia --check-map                                             # hold out a pass and locate it from the rest
enodia --check-map --match signal                              # the same, scored on signal as well
```

`--check-pace` needs coordinates on the crossings, since it measures in metres. `--check-passes`
needs none: it compares two passes over one stretch against each other. `--check-map` needs a
map with at least two passes in it, and holds out one outing at a time, or one pass down one
stretch when the map holds a single outing, never a single scan, since a scan's neighbour was
taken five seconds later and sees almost the same networks. Its table has four columns: each
scan alone by networks, by signal as well, and with the scans before it settling a tie or
choosing the path, the two ways `--sequence` names.
[The methodology notes](methodology.md) say what each of the three can and cannot tell you.

## The map, and finding yourself again

```bash
enodia --map-add LOG notebook.txt                               # add an outing to the map
enodia --map-add LOG notebook.txt --streets streets.jsonl        # along the street shapes
enodia --locate                                                # scan now: where am I?
enodia --locate LOG                                            # or a log's last scan, with the ones before it
enodia --locate LOG --sequence path                            # let the scans before it choose the path
enodia --locate --match signal                                 # score on signal strength too
enodia --map other.jsonl --locate                               # a map somewhere else
```

`--match networks` is the default and matches on which networks are in view. `--match signal`
also weighs how strongly each came in, which is more precise and less portable between radios,
since two cards report different numbers for the same room. An outing already in the map is not
added twice.

A fresh scan stands alone. `--locate LOG` has the scans before the last one, and when two
stretches match that scan about as well, the stretch the scans before it were on, or one next to
it, settles which: a walk does not jump a block in five seconds. They choose between two answers
and never make one up, so a scan the map does not know stays unknown however sure the scans
before it were.

`--sequence tie` is that, and the default. `--sequence path` asks the scans before it every
time, not only on a tie: it chooses the likeliest path through all of them, staying on a
stretch for nothing, stepping onto one that shares a mark for a little, jumping anywhere else
for a lot, and the answer is where that path ends. It can overrule the last scan, in both
directions, and the price of a jump is a number no walk has measured yet, which is why it is a
flag: `--check-map` reports both.

## Putting the notebook on the map

```bash
enodia --geocode notebook.txt --area Montevideo                                 # look the corners up
enodia --geocode notebook.txt --area Montevideo --out notebook.geo.txt           # name the output
enodia --geocode notebook.txt --area Montevideo --marks LOG                     # time the untimed lines
enodia --geocode notebook.txt --area Montevideo --proxy socks5://127.0.0.1:9050
enodia --geocode notebook.txt --area Montevideo --streets streets.jsonl          # keep the street shapes
enodia --geocode notebook.txt --area Montevideo --streets streets.jsonl --buildings
enodia --geocode notebook.txt --area Montevideo --overpass-url https://overpass.kumi.systems/api/interpreter
```

This is the one command in Enodia that goes online, and only when you type it. `--area` is not
optional: without it `Freire` matches a street in Chile. It takes a place as OpenStreetMap names
it, or four numbers `s,w,n,e` for a bounding box.

The original notebook is never touched. Without `--out`, the result goes beside it with `.geo`
before the suffix. `--proxy` sends the one request through SOCKS5 and the proxy resolves the
hostname, never this machine. `ALL_PROXY` and the rest of the environment are deliberately never
read, and if the proxy cannot be reached nothing is sent. It needs the `socks` extra:
`uv tool install "enodia[socks]"`, or `uv sync --extra socks` in a clone.

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
```

Both files at once, and `--out` names the directory, `public/` by default. The directory is the
export rather than a place to put files: it is built beside where it goes and moved into place
whole. So `--out` must not already exist, not even empty, since the second run is usually the
one after somebody has read the first line by line. Two exports aimed at one directory are safe
for the same reason: the first to claim the name wins and the second is told the place is taken.
The export is written privately and opened up only when it is complete. To read it privately
before deciding whether to publish it, `chmod 700` it afterwards.

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
  5  Locate              ->  LOCATE: scan now, or from a log's last scan
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
| `--out` | `--geocode` or `--export-public` |
| `--ssid`, `--mac-shaped`, `--key-file` | `--export-public` |
| `--streets` | `--geocode`, `--reconcile` or `--map-add` |
| `--buildings` | `--geocode` and `--streets` |
| `--svg`, `--check-pace`, `--check-passes`, `--csv`, `--geojson`, `--scans` | `--reconcile` |
| `--pace` | `--reconcile` or `--map-add` |
| `--map`, `--match`, `--sequence` | `--map-add`, `--locate`, `--check-map` or `--assistant` |
| `--outing` | `--reconcile`, `--map-add`, `--export-public`, `--locate LOG` or `--geocode --marks` |

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
