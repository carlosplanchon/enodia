# The file you write, and the one Enodia writes

A walk produces a log, written by Enodia. You produce a notebook, written by hand. Everything afterwards is the two of them joined. This is what each one is, exactly.

The fingerprint map is a third format, and it is described in [the methodology notes](methodology.md#what-the-map-file-is), beside what it is for.

## The notebook

Positioning is a paper notebook, no GPS. Each time you pass a street crossing you note the time Enodia just said and the crossing. Afterwards, offline, you transcribe it one crossing per line:

```
17:45:00 Rivera y Avenida Doctor Francisco Soca
17:47 Rivera y Brito del Pino                     # seconds are optional
17:49:00 Rivera y Simón Bolívar @ -34.903101, -56.159047  # coordinates, if you look them up later
Kiosco                                            # no time: takes the next button mark
#7 Rivera y Obligado                              # button mark 7, when one was skipped
date 2026-09-06                                   # the lines below belong to another day
03:25:00 McDonald's Paso Molino
2026-09-07 03:40:00 Rivera y Brito del Pino       # or the date on the line itself
```

Seconds are optional. Coordinates go after an `@`, latitude then longitude, and they are what turns a fraction of a block into a point on a map. Times without a date belong to the day of the first scan in the log, a `date` line switches the day for the lines below it, a date written on a line switches it from there on too, and a time earlier than the previous one rolls over to the next day. A walk goes forwards, so a crossing dated earlier than the one above it is refused rather than placed, and so is a `date` line that would take the walk back past the crossing before it. A `#` starts a comment, except a leading `#7`, which names a button mark.

Name each crossing the same way every time. "Rivera y Obligado" one week and "Obligado y Rivera" the next are two different corners as far as Enodia is concerned. It is caught rather than corrected: `--reconcile` and `--check-map` name the pairs written both ways round and ask you to settle on one spelling, and they do not merge them, because "Treinta y Tres" is one street and not the corner of Treinta and Tres, and no rule can tell those two shapes apart from a name on its own. Case, accents and extra spaces are forgiven.

A line that cannot be right stops the reconciliation with its number and its reason rather than being read as best it can. [The design notes](design.md) say which lines are refused and why each refusal earns its keep.

### The headset button

A headset has a button, and Linux shows it as an input device that reports every press with the time the kernel stamped it with. Press it at each crossing and the time is the machine's. Enodia answers *"Mark 7"*, and the notebook only needs the crossing's name next to that number. When the kernel says its own queue overran, Enodia says so too and writes a `button_lost` record, and the numbers carry on afterwards as if nothing had gone missing, so the paper and the log agree with each other and both are short a corner. [The design notes](design.md) say why a press is timed by the kernel and not by Enodia, and why a lost press is written down.

With marks in the log the notebook can drop the times. A line is just the crossing's name and takes the next mark in order, or `#7 Rivera y Obligado` names mark 7 outright when one was pressed by mistake and skipped. Timed and untimed lines can be mixed.

By default (`--button auto`) Enodia listens to every input device that has media keys: a headset's play button, and the laptop's own keyboard, which does no harm from inside a closed backpack. `--button /dev/input/eventN` names one device and takes any key on it. `--button list` shows what the kernel sees. `--button off` disables it. Presses closer together than a second count as one, and with `--resume` the marks carry on across a restart of the same outing. [Setting up the machine](setup.md) covers the permissions and how to find out whether your headset's button reaches the kernel at all.

## Log format

The log is [JSON Lines](https://jsonlines.org/): one JSON object per line, appended as the walk goes. Eight kinds of record are written: `connected` and `disconnected` when an interface's association changes, `scan` with every access point seen on each cycle, `new` with the ones seen for the first time, `scan_failed` when the daemon could not scan (with the reason), `suspended` on waking from a suspend (with the seconds asleep), `mark` on each press of the headset button (with its number), and `button_lost` when the kernel says its input queue overran and a press may have gone missing. Only `scan` records count as observations.

```
{"time": "2026-09-05T00:14:03-03:00", "event": "scan", "outing": "3f9a2b10", "interface": "wlan0", "networks": [{"ssid": "Home", "bssid": "aa:bb:cc:dd:ee:ff", "security": "psk", "frequency": 5180, "signal_dbm": -47, "signal_percent": 100, "connected": true}, {"ssid": "Cafe libre", "bssid": null, "security": "open", "frequency": null, "signal_dbm": null, "signal_percent": 38, "connected": false}]}
```

One line per record is what the format is for. The log is written from a laptop that may lose power mid-line, and a line that was never finished costs only itself. And the most hostile thing in the file is the SSIDs, which whoever owns the network chooses and which may hold commas, quotes or newlines. JSON escapes all of it, and accented and non-Latin names stay readable because nothing is escaped into `\u` sequences.

Frequencies are in MHz, as the backend reports them, not channel numbers: a channel is derived and ambiguous across bands, and which band an access point is on is what says how far a given signal strength puts it. Anything a backend cannot report is `null`, not a placeholder to parse back, with one exception: a network that hides its name is written `"ssid": ""`, because that is how the reader will hand it back and the file says what it is going to mean rather than something that has to be turned into it. Every record carries the `outing` it belongs to, a token for one run of the loop, and a scan record also carries the `cycle`, the pass of the loop it came from. Both exist because the clock has one second of resolution and two of anything can share a second. [The design notes](design.md) tell that story, along with what is checked when a log is read back and why.

Because every record is a line of JSON, the log works with ordinary tools:

```bash
jq -r 'select(.event=="scan") | .networks[] | select(.security=="open") | .ssid' 2026-09-14T17-45-03.jsonl | sort -u
wc -l 2026-09-14T17-45-03.jsonl          # how many records the outing produced
```

See [the CLI reference](cli.md) for the flags that read and write these.
