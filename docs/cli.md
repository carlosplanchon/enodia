# Every flag

`enodia --help` is the authoritative list, and the one that cannot go out of date.
This one groups the flags by when you reach for them, and says what each is for.

## On the walk

```bash
uv run enodia                             # every Wi-Fi interface, every 5 s, one log per outing
uv run enodia -i wlan0 -t 10              # one interface, every 10 s
uv run enodia -l wifi.jsonl               # one log file of your choosing
uv run enodia --dir ~/paseos              # one file per outing, in that directory
uv run enodia --voice pico                # engine: auto (espeak-ng, else Pico) | espeak | pico | none
uv run enodia --lang en-GB --ssid-lang es-ES # language of the announcements, and of the network names
uv run enodia --say-status                # also say "Scanning" and the time every cycle
uv run enodia --say-status --say-signal   # ...and the signal quality, which seldom changes
uv run enodia --say-time-every 30         # the time every 30 s, never skipped, with or without --say-status
uv run enodia --quiet --say-time-every 30 # only the time, the button's marks and the failures
uv run enodia --quiet --no-hour           # only the button's marks and the failures
uv run enodia --say-names 0               # read only the open networks' names, never the rest
uv run enodia --button /dev/input/event7  # mark crossings with that device's button. 'list', 'off'
uv run enodia --no-fresh                  # read the daemon's view instead of scanning
uv run enodia --no-log-every-scan         # log only connection changes and new networks
uv run enodia --resume                    # carry on with the outing under way, if its log is under 30 minutes old
uv run enodia --cycles 3                  # stop after 3 scans instead of running until Ctrl+C
```

## Before the walk

```bash
uv run enodia --preflight                 # check everything an outing needs and exit
```

## Afterwards

```bash
uv run enodia --open-networks LOG         # the unencrypted networks in a log, strongest first
uv run enodia --reconcile LOG NOTEBOOK    # place every network along the route (see Back home)
uv run enodia --map-add LOG NOTEBOOK      # add an outing to the fingerprint map
uv run enodia --locate                    # scan now, on every radio, and say where on the map you are
uv run enodia --check-map                 # hold out a walk and measure how well the map finds it
uv run enodia --geocode libreta.txt --area Montevideo    # look the corners up on OpenStreetMap
uv run enodia --geocode libreta.txt --area Montevideo --proxy socks5://127.0.0.1:9050  # through Tor
uv run enodia --geocode libreta.txt --area Montevideo --streets calles.jsonl   # keep the street shapes
uv run enodia --reconcile LOG libreta.geo.txt --streets calles.jsonl   # place the scans along them
uv run enodia --geocode libreta.txt --area Montevideo --streets calles.jsonl --buildings  # and the blocks
uv run enodia --reconcile LOG libreta.geo.txt --streets calles.jsonl --svg plano.svg   # draw it
uv run enodia --reconcile LOG libreta.txt --outing 3f9a2b10   # one walk of a file that holds several
```

## What is spoken

Every message is printed as `Say (Silent: ...) > text`. Events are spoken. The per-cycle status is only printed unless you pass `--say-status`.

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

Speech runs in its own thread and never holds up a scan. It is slow (espeak-ng takes nearly four seconds just to say the time), so when there is more to say than time to say it, network names are dropped and everything else still gets through. The per-cycle status ("Scanning", the signal, the time) is said only when the voice is free. Said late it would be wrong, and the time is what the notebook is written from, so a status that arrives while speech is behind is skipped and the next cycle brings a current one. Events are never skipped. `--quiet` turns the table around: nothing about networks or connections is spoken (it is all in the log), and what remains is the time, every cycle or every `--say-time-every` seconds, the button's marks, and the failures, which you cannot afford to miss. `--no-hour` silences the time everywhere, `--quiet` included: with a headset button the marks carry it, and `--quiet --no-hour` leaves nothing but the marks and the failures. If you would rather have the time on a fixed beat, `--say-time-every 30` says it every thirty seconds as an event, never skipped, at the cost of hearing it a few seconds late when it lands in the middle of a name. Scans keep the interval asked for: a cycle sleeps only what is left of it after the scanning is done. Every message is printed whether or not it is spoken, and the log always has every network, so nothing is lost but the audio.

## Sharing a walk

```bash
uv run enodia --export-public paseo.jsonl libreta.txt --out samples/
uv run enodia --export-public paseo.jsonl libreta.txt --ssid pseudonym  # names kept apart, not dropped
uv run enodia --export-public paseo.jsonl libreta.txt --ssid keep       # names as they are
uv run enodia --export-public paseo.jsonl libreta.txt --mac-shaped      # d2:17:43:.. rather than ap-1c8a74f992ae
uv run enodia --export-public paseo.jsonl libreta.txt --key-file ~/keys/enodia.key
uv run enodia --export-public walk.jsonl libreta.txt --outing 3f9a2b10   # one walk of a file that holds several
```

Both files at once, and `--out` names the directory, `public/` by default. The directory is the
export rather than a place to put files: it is built beside where it goes and moved into place
whole, so `--out` must not already exist, since
the second run is usually the one after somebody has read the first line by line. Two of these
aimed at one directory are safe for the same reason. The first to finish wins and the second is
told the place is taken. Not even an empty one: `rename` replaces
those, along with their permissions, their owner and their ACLs. To read an export privately
before deciding to publish it, `chmod 700` it afterwards, which keeps all three. It is written
privately in the first place and opened up only when it is complete.

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

## The menu, if you would rather not remember any of this

```bash
uv run enodia --assistant                          # the whole workflow, one screen at a time
uv run enodia --assistant --dir ~/paseos --map otro.jsonl   # over a different directory and map
uv run enodia --assistant --log walk.jsonl         # over one file, and every walk inside it
uv run enodia --assistant -i wlan0 --voice pico    # and every walk it starts uses these
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
says which flag is the odd one. `--area`, `--out`, `--marks`, `--proxy` and `--overpass` need
`--geocode`. `--streets` needs one of `--geocode`, `--reconcile` or `--map-add`, and
`--buildings` needs `--geocode --streets`. `--svg` needs `--reconcile`. `--outing` needs one of
`--reconcile`, `--map-add`, `--locate LOG` or `--geocode --marks`. Each of those exits with
status 2, the way a bad command line does.

## Where things are written

Without `--log`, an outing writes to `$XDG_DATA_HOME/enodia/` (that is `~/.local/share/enodia/`
unless you set the variable), or to the directory `--dir` names, in a file named by the time it
started, and the button's marks count from one. The path is printed when the outing starts and
again when it stops, since by then the first line is long gone. `--resume` carries on with the
outing under way instead, when the newest log there was written to less than thirty minutes
ago: a laptop that ran out of battery, a walk stopped by mistake. Older than that, it starts a
new outing like any other run. `--log FILE` appends to one file of your choosing, whatever its
age, and there the marks start again from one inside the same file, so restarting mid-outing
with `--log` wants `--resume` too. The fingerprint map lives in `$XDG_DATA_HOME/enodia/map/map.jsonl`, in a directory of
its own so that `--resume` never mistakes it for an outing's log, and `--map FILE` puts it
anywhere you like.

See [the README](../README.md) for what these commands do, and [the design notes](design.md)
for why several of them refuse more than they answer.
