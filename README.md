![Enodia: a plan of four city blocks with a walked route across them, each scan along the way labelled with its signal strength, the street crossings numbered M01 to M04, and a position given as 0.63 of the way between M02 and M03 with no GPS involved.](https://raw.githubusercontent.com/carlosplanchon/enodia/main/assets/enodia_banner.jpg)

# Enodia

*Wardriving on foot, without GPS: a talking Wi-Fi scanner in a backpack, a paper notebook of street crossings, and a reconciliation that places every access point along your walk.*

[![tests](https://github.com/carlosplanchon/enodia/actions/workflows/tests.yml/badge.svg)](https://github.com/carlosplanchon/enodia/actions/workflows/tests.yml)
[![PyPI version](https://img.shields.io/pypi/v/enodia.svg)](https://pypi.org/project/enodia/)
[![Python versions](https://img.shields.io/pypi/pyversions/enodia.svg)](https://pypi.org/project/enodia/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/carlosplanchon/enodia)

## Why Enodia?

The city is full of radios. Enodia uses them as landmarks.

Walk down a street and networks appear, signals grow stronger, fade and disappear. A laptop in your backpack records that changing landscape.

At each crossing, write its name and the time you hear in a paper notebook. Or press the headset button: “Mark 1.” “Mark 2.” Enodia records the time. You write the crossing beside the mark number. The log keeps the moment. The notebook gives it a place.

Back home, Enodia joins the two: scans placed along your route, estimates of where access points stand, and a fingerprint of each stretch of street.

Walk it again, and Enodia can recognise a stretch from a new scan. The answer comes from your own walks: between these crossings, this far along.

No GPS. No online positioning service. A laptop, headphones, a notebook, and a map built from what the street broadcasts.

## How it works

- **Capture.** Enodia scans every few seconds and speaks through the headphones: a heartbeat so you know it is alive, the time so you can write it down, and the name of every network it had not seen before.
- **Positioning.** Each time you pass a street crossing you note the crossing and the time Enodia just said, or you press a button on the headset and Enodia notes the time for you. That is the whole positioning system, and it is robust precisely because it is primitive.
- **Reconciliation.** Afterwards, `enodia --reconcile` joins the transcribed notebook with the log. Every scan gets a place between two crossings, every network the place where its signal was strongest, and, with coordinates, an estimate of where the access point itself stands.

```
                         THE WALK
                             |
              +--------------+--------------+
              |                             |
         Wi-Fi scans                    crossings
      every few seconds           notebook, or the headset button
              |                             |
              +--------------+--------------+
                             |
                        --reconcile
                             |
                      every scan placed
                             |
              +--------------+--------------+
              |                             |
      where each access point         fingerprints
          probably stands                   |
                                        --map-add
                                            |
                                    a scan, another day
                                            |
                                        --locate
                                            |
                                    "you are here"
```

Walk once, and the log is a list of networks and times. Walk once with the notebook, and it is a
route with every access point placed along it. Walk it twice, and the second walk can ask the
first where it is, without a GPS, without the internet, and without asking anybody.

## Quick start

```bash
uv sync
uv run enodia --preflight                              # is this machine ready to walk?
uv run enodia --say-status                             # the walk itself. Ctrl+C when home
uv run enodia --reconcile <log> libreta.txt            # the report
uv run enodia --map-add <log> libreta.txt              # keep it as a fingerprint map
uv run enodia --locate                                 # and later: where am I?
```

`--locate` is the one worth seeing first:

```
You are between "Avenida Agraciada y Doctor Salvador García Pintos" and "Avenida Agraciada y San Fructuoso", 63% of the way
  around [-34.88028, -56.19569]
  5 fingerprints agree, best similarity 100%, spread 11% of the stretch (17 m)
  from evidence last gathered 2026-09-17 17:03
```

The crossings, coordinates and 148 m street geometry in that answer are real OpenStreetMap
data. The two passes and their `sample-ap-*` radio observations are synthetic, so the example
has a known answer without publishing anybody's Wi-Fi fingerprint. Every input, the built map
and the generated CSV and GeoJSON are in [`samples/`](samples/README.md), together with commands
that rebuild this output. How well it works on a real street is still being worked out, and
*Limits* below says what has and has not been walked yet.

No GPS was involved, and no coordinates are needed for the first line of that answer. It is the
same walk you already did, read backwards.

### Or let it ask

```bash
uv run enodia --assistant
```

A guided menu over these same commands: one decision per screen, the state of your own data on the way in, and Enter always on the step the state suggests. It works over whichever outings the flags point it at, so `--log walk.jsonl` makes that file its whole world, and every walk inside it is offered separately. It runs exactly what the flags run and prints exactly what they print, so it is a way of filling the commands in rather than a second, smaller Enodia. The walk happens inside it too, and Ctrl+C ends the outing and hands you back the menu with the log you just wrote already chosen.

```
ENODIA

This machine
  [ok]   interfaces  wlan0 (radio on)
  [ok]   lid         HandleLidSwitch=ignore
  [warn] battery     31%, discharging

Suggested next step
  Add 8d91f3ac to the map. It is not on it yet.

  Enter  take it
  1      Start a new outing
  3      Reconcile an outing
  5      Locate: where am I?
  q      Quit
```

Nothing there is generated. Every suggestion is a rule over a file that exists, and the rules are short enough to read: [the design notes](docs/design.md) list them, along with the three questions it refuses to answer because Enodia does not record what they would need. It needs a terminal to ask on, and says so rather than reading a pipe.

## Limits

- It is niche. If you want a map with a GPS track, use a phone.
- A scan between two crossings sits on the straight line between them unless `--streets` gives Enodia the shape of the block.
- The map is on paper unless the notebook carries coordinates. You write them in by hand, or `--geocode` looks the corners up on OpenStreetMap. That one command is the only thing in Enodia that goes online, it only goes when you type it, it goes to an endpoint you can see and change, and it can go through Tor. Nothing else does, ever.
- One real outing has been walked, and it earned its keep by falsifying an assumption the code had made about what a down interface means. The pace, access point and map estimates themselves are still argued from a model and tested on synthetic observations built to have a known answer. The bundled Agraciada sample uses real OpenStreetMap coordinates and geometry, but it is not a real radio walk. `--check-pace`, `--check-passes` and `--check-map` exist so that real outings settle each claim either way.
- The map only finds you where you have already walked, it rots as routers are replaced and moved, and its accuracy can be no better than the reconciliation that placed its fingerprints. A map built from one outing recognises that walk, not the place, and `--check-map` says so when that is what happened.
- The value is mostly personal and educational until it is written up. `--export-public` makes a shareable copy of a real outing. The bundled sample proves that the files and commands fit together; it cannot replace a public, pseudonymised real outing for validating the model against a street.

## Requirements

- Linux, Python 3.10 or newer.
- For scanning, a running Wi-Fi daemon: iwd, NetworkManager or wpa_supplicant. The user must be allowed on its D-Bus (for example, a member of the `wheel` or `network` group).
- For voice, `espeak-ng`. Or SVOX Pico plus a WAV player (`paplay`, `pw-play` or `aplay`): Pico comes as `libttspico-utils` on Debian and Ubuntu (`pico2wave`) and as `pico-tts` from AUR on Arch. Without any engine, Enodia prints instead of speaking.
- To use a headset button, permission to read `/dev/input`, usually membership of the `input` group. See [Setting up the machine](docs/setup.md).
- A lid that closes without suspending the laptop. The default on systemd is to suspend, which ends the walk when the zip closes. See [Setting up the machine](docs/setup.md).
- For `--geocode --proxy socks5://...`, PySocks: `uv sync --extra socks`. Everything else, `--geocode` without a proxy included, runs without it.

```bash
uv sync              # runtime, plus the development tools (see Development)
uv sync --no-dev     # runtime only
uv sync --extra socks   # and the SOCKS5 proxy support for --geocode
```

Network facts come from [ifpeek](https://pypi.org/project/ifpeek/) (netlink and D-Bus, no root). Speech uses `espeak-ng` by default, or SVOX Pico when espeak-ng is missing or when asked with `--voice pico`.

## Your first outing

Six steps, each of which tells you something the previous one did not. Do the first three at a desk, with headphones on.

**1. Check that everything works.**

```bash
uv run enodia --preflight
```

Eight lines, `OK`, `WARN` or `FAIL`: a Wi-Fi interface whose radio is on, a daemon that answers a real scan on every radio the walk will use, a voice engine that actually speaks, what the lid does when closed, whether the address your scans go out under is randomised, whether the headset button can be read, how much battery there is, and which log the outing will write to. The exit status is 1 if anything fails. Fix the failures ([Setting up the machine](docs/setup.md) covers the two that need the system's cooperation) and run it again until it says `Ready to go.` Everything it checks is a failure you would otherwise discover two hours in, with the screen shut inside a bag.

**2. A few cycles at the desk.**

```bash
uv run enodia --cycles 3 --say-status
```

You should hear "Scanning", "Now connected to" and the name of your network, how many new networks there are and some of their names, and the time. Then look at what it wrote. The path was printed on the first line, and again on the last, once it stopped.

**3. The headset button.** `uv run enodia --button list` shows the input devices the kernel sees and which of them have media keys. Then run `uv run enodia --say-status`, press the button, and hear "Mark 1". The laptop's own play key marks too, which is a way to test the whole chain without a headset.

**4. Two blocks.** Laptop closed in the backpack, `uv run enodia --say-status`, and out. At each crossing, the button. Without one, write down the time you just heard and the name of the crossing. Two or three blocks are enough for a first outing: it is there to show that the whole system works, not to map anything. If you stop somewhere on the way, better, since that is what reading the pace from the networks has to get right.

**5. Transcribe the notebook**, one crossing per line, in the format below. With the button, only the names in order. Without it, the time and the name. With coordinates if you look them up afterwards, which is what enables everything interesting on the way back.

**6. Reconcile.**

```bash
uv run enodia --reconcile <log> libreta.txt --scans          # where every scan and every network fell
uv run enodia --reconcile <log> libreta.txt --check-pace     # does reading the pace beat the clock on this route?
uv run enodia --reconcile <log> libreta.txt --geojson walk.geojson   # and draw it: umap, QGIS
```

## On the walk

Enodia was built to run inside a backpack, with the laptop closed and the operator listening through headphones. In that setting the voice is the whole interface, and `--say-status` turns on the two cues that make it work: "Scanning" every cycle is the heartbeat that tells you Enodia is still alive, and the time said after every scan is what you write down. The time is said on the 24-hour clock ("17 hours, 52 minutes, 10 seconds" is written `17:52:10`), which is how the notebook is read.

Speech runs in its own thread and never holds up a scan. It is slow, so when there is more to say than time to say it, network names are dropped and everything else still gets through. Events are never skipped. Said late, the time would be wrong, so a status that arrives while speech is behind is skipped and the next cycle brings a current one. Every message is printed whether or not it is spoken, and the log always has every network, so nothing is lost but the audio.

`--quiet` turns the table around: nothing about networks or connections is spoken, and what remains is the time, the button's marks, and the failures, which you cannot afford to miss. `--say-time-every 30` puts the time on a fixed beat instead. `--no-hour` silences it everywhere, which with a headset button is enough, since the marks carry the time themselves.

Some things go wrong in a backpack that nothing on a closed screen can tell you about, so Enodia says them: a suspend it woke up from and how long it lasted, a scan the daemon refused, a radio switched off by rfkill, a headset button that went away, the battery at 20 % and at 10 %. Each is announced once, reminded once a minute while it lasts, closed with "Scanning again" when it recovers, and written into the log as a `scan_failed` record with its reason, so that afterwards a hole is a hole and an empty scan is an empty street.

**One outing, one log, and every run is a new outing.** Without `--log`, Enodia writes to `$XDG_DATA_HOME/enodia/`, a file named by the time it starts, and the button's marks count from one. `--resume` carries on with the outing under way instead: the networks already in it count as seen so nothing is announced twice, and the marks count on from where they were, so the numbers never collide with the ones already on the paper.

Every cycle asks the daemon for a fresh scan. It takes about five seconds, which is what the default interval allows. `--no-fresh` reads the daemon's current view instead: cheaper, but that view is whatever the last full scan saw, and a laptop that travelled asleep can carry it for days. A bus terminal 300 km away once showed up as nine open networks in a living room.

### What your own scans give away

Asking for a fresh scan is not only listening. The card sends probe requests, and a probe request carries the sender's address, so an outing lays down a trail under whatever MAC the card is using, every few seconds, along a route. It is the same fact as the log recording other people's BSSIDs, pointed the other way.

Both Wi-Fi daemons can randomise the address used for scanning, and NetworkManager does it by default. `--preflight` reads what yours was told and says so on the `scan mac` line. It says `OK` for one thing only: NetworkManager, set once, in a section NetworkManager reads device properties from, to a value NetworkManager documents, with no mask. Everything else is a warning that says what it could not establish. Enodia does not change the setting, on purpose: that needs root, which nothing else here does, and it fights the daemon that owns the interface. So this catches rather than corrects, the way the lid and the corner names do. [The design notes](docs/design.md) say what each of those warnings is guarding against.

## The notebook and the headset button

![A walker at night on a Montevideo street, seen from behind: a laptop in the backpack, a hand on the headset button, and an open paper notebook of street crossings in the other hand, with the networks overhead labelled by name and signal strength.](https://raw.githubusercontent.com/carlosplanchon/enodia/main/assets/enodia_walk.jpg)

Positioning is a paper notebook, no GPS. Each time you pass a street crossing you note the time Enodia just said and the crossing. Afterwards, offline, you transcribe it one crossing per line:

```
17:45:00 18 de Julio y Eduardo Acevedo
17:52 18 de Julio y Yaguarón                      # seconds are optional
18:25:00 Plaza Independencia @ -34.9066, -56.2001  # coordinates, if you look them up later
Plaza Cagancha                                    # no time: takes the next button mark
#7 Plaza Fabini                                   # button mark 7, when one was skipped
date 2026-09-06                                   # the lines below belong to another day
03:25:00 McDonald's Paso Molino
```

Seconds are optional. Coordinates go after an `@`, latitude then longitude, and they are what turns a fraction of a block into a point on a map. Times without a date belong to the day of the first scan in the log, a `date` line switches the day for the lines below it, a date written on a line switches it from there on too, and a time earlier than the previous one rolls over to the next day. A walk goes forwards, so a crossing dated earlier than the one above it is refused rather than placed, and so is a `date` line that would take the walk back past the crossing before it. A `#` starts a comment, except a leading `#7`, which names a button mark.

Name each crossing the same way every time. "Avenida Agraciada y San Fructuoso" one week and "San Fructuoso y Avenida Agraciada" the next are two different corners as far as Enodia is concerned. It is caught rather than corrected: `--reconcile` and `--check-map` name the pairs written both ways round and ask you to settle on one spelling, and they do not merge them, because "Treinta y Tres" is one street and not the corner of Treinta and Tres, and no rule can tell those two shapes apart from a name on its own. Case, accents and extra spaces are forgiven.

A line that cannot be right stops the reconciliation with its number and its reason rather than being read as best it can. The 31st of February is not a date, a latitude of 999 is not a place on the earth, and `17:0 A` is a time typed wrong rather than a crossing named `17:0 A`. [The design notes](docs/design.md) say why each of those refusals earns its keep.

### The headset button

The notebook's weak point is the time: heard through headphones, written by hand, one misheard digit moves a whole stretch of the route. A headset has a button, and Linux shows it as an input device that reports every press with the time the kernel stamped it with, which is when the press happened rather than when anything got round to reading it. Press it at each crossing and the time is the machine's. Enodia answers *"Mark 7"*, and the notebook only needs the crossing's name next to that number. When the kernel says its own queue overran, which is a thing it says out loud, Enodia says so too and writes it down: a press that never arrived is a crossing that never arrived, and the numbers carry on afterwards as if nothing had gone missing, so the paper and the log agree with each other and both are short a corner.

With marks in the log the notebook can drop the times. A line is just the crossing's name and takes the next mark in order, or `#7 Plaza Fabini` names mark 7 outright when one was pressed by mistake and skipped. Timed and untimed lines can be mixed.

By default (`--button auto`) Enodia listens to every input device that has media keys: a headset's play button, and the laptop's own keyboard, which does no harm from inside a closed backpack. `--button /dev/input/eventN` names one device and takes any key on it. `--button list` shows what the kernel sees. `--button off` disables it. Presses closer together than a second count as one, and with `--resume` the marks carry on across a restart of the same outing. [Setting up the machine](docs/setup.md) covers the permissions and how to find out whether your headset's button reaches the kernel at all.

## Back home: reconciliation

```bash
uv run enodia --reconcile networks.jsonl libreta.txt               # report on the terminal
uv run enodia --reconcile networks.jsonl libreta.txt --scans       # also the position of every scan
uv run enodia --reconcile networks.jsonl libreta.txt --pace clock  # interpolate on time instead
uv run enodia --reconcile networks.jsonl libreta.txt --csv redes.csv
uv run enodia --reconcile networks.jsonl libreta.txt --geojson mapa.geojson
```

Every scan is placed between the two crossings it fell between. By default Enodia works out the pace from the scans themselves rather than from the clock: between two scans it measures how much the set of networks in view turned over, which is near zero while you stand still and climbs as access points enter and leave behind you. A stop therefore stays a stop instead of being smeared over half a block, and you no longer have to write the place down twice. Where the scans say nothing about pace it falls back to the clock, and `--pace clock` asks for that everywhere.

With coordinates on both crossings a position is interpolated to a latitude and longitude. Without them it is expressed as a fraction of the way between two named crossings, which is enough to draw on a paper map.

A notebook is one walk's, so a reconciliation is one walk's too. The usual file holds exactly one and there is nothing to choose. `--log walk.jsonl` reused every week is the other case, and there Enodia reads the last walk in the file and says which one that was, with `--outing TOKEN` to name an older one. Everything that reads a log works this way.

### Where you were, and where the access point is

Every network gets the position of the scan where its signal was strongest: that is where *you* were. It also gets an estimate of where the *access point* stands, worked out from every place it was heard, which is a different question and a better answer. It is a weighted centroid under the log-distance model, so a sighting counts as `10 ** (RSSI / 10n)` and the unknown transmit power cancels out. With coordinates on the crossings it is a latitude and longitude. Without them it still runs, in the one dimension a notebook always has: how far along a stretch between two named crossings you were.

Most of what a walk hears is not on the street it walked, and the estimate still puts a point on the map for it. So each estimate is compared with the plain middle of the same sightings, with the signal ignored. Walk past an access point and its signal peaks sharply, pulling the estimate well clear of that middle. Hear one from a block away and every sighting weighs about the same, the estimate settles on the middle of your own route, and it says nothing whatever about where the thing is. The second kind is marked **barely pinned down**, in the report, in the CSV and in the GeoJSON.

[The methodology notes](docs/methodology.md) give the maths, the two biases worth knowing before you draw any of it on a map, and why sightings from two different stretches are never averaged together.

## Finding yourself again

Reconciling a walk turns a log into a map. The map can be turned back into a compass. Every scan a reconciliation placed is already a fingerprint, the networks in view with their strengths tied to a place, so a map is nothing but those scans kept and searched.

```bash
uv run enodia --map-add <log> libreta.txt     # add an outing to the map
uv run enodia --locate                        # scan now: where am I?
uv run enodia --locate <log>                  # or locate a log's last scan
uv run enodia --check-map                     # and is any of this worth anything?
```

This is scene analysis, not trilateration. It never asks where an access point is, only whether this pattern has been heard before and where, which sidesteps the weakest part of the estimates above and needs no coordinates at all. A notebook of bare crossing names gives answers of the same shape the rest of Enodia speaks in.

Four rules do most of the work, and each of them is a way of not answering. **Not knowing is an answer**: below a floor it says "not on the map" rather than guessing confidently in a city it has never been to. **Two streets are never averaged**: when two stretches match about as well, both are reported, because the midpoint of two streets is inside the block between them, where you certainly were not. **A stretch is one stretch whichever way you walked it**: the two crossing names are sorted before anything is stored, and compared with case, accents and spacing folded away. And **a crossing name means one place**: when the fingerprints behind an answer are scattered across a county, the stretch is named and the coordinates are withheld, because two towns can have the same pair of street names. [The design notes](docs/design.md) give each of those its history.

What is matched on is which networks are in view, as the same Jaccard measure the pace estimate uses. `--match signal` also weighs how strong each one came in, which is more precise and less portable, since one radio reads several dB apart from another and your own body shadows differently walking one way than the other. Which of the two wins is a question about your streets, not about the method, so `--check-map` answers it with your own data.

The map lives in `$XDG_DATA_HOME/enodia/map/map.jsonl`, and `--map FILE` puts it anywhere you like. Adding the same outing twice is refused rather than done, since a doubled outing pulls every answer towards itself. An outing goes on whole or not at all, written through a temporary file whose name nothing can guess, with the map held while it is read, added to and put back, and a map Enodia creates is readable only by you. That last pair matters more in a shared directory than it looks: a predictable temporary is a name somebody else can leave a link under, and then the next outing pours the map through the link into whatever it points at.

Be clear about what this file is. It is a geolocation database of your neighbours' routers, names included, keyed to the street corners they sit near, small enough to mail and easy to grep. `--export-public` exists for when you want to share a walk anyway: see *Sharing a walk* below. That is exactly what makes it work and exactly why it is worth keeping to yourself. `.gitignore` excludes `*.jsonl` already. Keeping the SSID is deliberate, because a map you can read by eye is a map you can check.

## Does any of it work?

Three commands, and all three answer with the walk you already did rather than with an assertion. That is the part worth keeping whichever way the numbers come out.

```bash
uv run enodia --reconcile <log> libreta.txt --check-pace    # reading the pace, against the clock
uv run enodia --reconcile <log> libreta.txt --check-passes  # one block walked twice: do the passes agree?
uv run enodia --check-map                                   # hold out a whole walk and locate it from the rest
```

**`--check-pace`** holds out each crossing in turn, reconciles without it, and measures how far each method puts the scan nearest that crossing's time from where the crossing actually was. The notebook is the only ground truth there is, so it is also the test. If the clock wins on your route the report says so, and `--pace clock` is one flag away.

**`--check-passes`** finds any stretch the notebook shows walked more than once and compares what each pass made of it. Walk a block, turn round at the corner and walk it back, and you have left a small controlled experiment inside an ordinary outing: the same access points, the same street, the pace and the direction the only things that changed.

**`--check-map`** holds out one whole walk at a time and locates every scan of it from the rest of the map. Holding out one scan at a time would be worthless: its neighbour was taken five seconds and six metres later and sees almost exactly the same networks, so the map would be scoring itself on a copy of the question. The report also says how many of its answers were backed only by the outing the scan came from, because a map built from a single outing will find that outing again beautifully and prove nothing.

[The methodology notes](docs/methodology.md) show what each of the three prints, on a synthetic route built to have a known answer, and say what the numbers mean.

## Putting the notebook on the map

Coordinates are what turn a fraction of a block into a place, and writing thirty crossings out by hand is where a neighbourhood walk stops being fun. `--geocode` looks them up on OpenStreetMap through Overpass and writes a second notebook beside the first.

```bash
uv run enodia --geocode libreta.txt --area Montevideo
uv run enodia --geocode libreta.txt --area "s,w,n,e"                      # or a bounding box
uv run enodia --geocode libreta.txt --area Montevideo --marks paseo.jsonl # times from the button
uv run enodia --geocode libreta.txt --area Montevideo --proxy socks5://127.0.0.1:9050
uv run enodia --geocode libreta.txt --area Montevideo --streets calles.jsonl
```

One request for the whole notebook, an exact-name match rather than a regular expression, and the intersections worked out here rather than there. It never touches the original notebook, it refuses to overwrite a second one you may have finished by hand, and it reports every crossing it could not place with the reason. **Your own walk checks the lookup**: the notebook has the times, so the coordinates imply a speed for every stretch, and nobody covers eight hundred metres in ninety seconds. A stretch that says they did means one of its two corners is in the wrong place, and since there is no telling which, neither is written.

`--proxy` sends that one request through an HTTP or SOCKS5 proxy, which is what Tor is (`9050` for the daemon, `9150` for the Browser). It fails closed: if the proxy cannot be reached, nothing is sent and nothing falls back to a direct connection. The proxy resolves the hostname, never this machine, and the environment's proxy variables are deliberately never read, so the destination of the request is in the command you typed.

`--streets FILE` keeps the street geometry that same request came back with. Given it, `--reconcile` and `--map-add` place each scan along the street as OpenStreetMap draws it instead of on the straight line between two corners. `--svg plano.svg` then draws the whole walk, with no tiles fetched, so nothing is asked of OpenStreetMap when the picture is opened.

Coordinates in your own notebook for your own use are not a problem under the ODbL. Publishing a database derived from OpenStreetMap carries share-alike obligations, which is worth knowing before the map file goes anywhere. [The methodology notes](docs/methodology.md) cover what the lookup refuses and why, and what the drawing does and does not claim.

## Sharing a walk

```bash
uv run enodia --export-public paseo.jsonl libreta.txt --out samples/
```

A publishable copy of one outing. Both files together, because the log is where your
neighbours' routers are and the notebook is where the street corners are, and a substituted log
beside a real notebook still says where you were walking and when.

An export is exactly one walk. `--log walk.jsonl` reused every week appends to the same pages,
so a file is not an outing, and a file holding more than one is refused with their names rather
than resolved to the newest: everywhere else in Enodia guessing the last walk is sensible, and
here it would mean publishing the walks nobody asked to publish. `--outing TOKEN` names it.

What it does: every hardware address is replaced by a stable pseudonym, `ap-1c8a74f992ae`, or
by a locally administered MAC with `--mac-shaped` for tools that insist on the shape. Network names
are removed, or pseudonymized with `--ssid pseudonym`, or kept with `--ssid keep`. Crossings are
renamed street by street, so a street that turns up at two corners is still one street. The
clock is moved so that the first moment of the two files becomes the epoch and everything keeps
its distance from it. The coordinates are laid out from an artificial origin in metres, so the
distances between the crossings come out identical. The association records are left out
entirely, because the network you were connected to is yours and not a neighbour's, the interface
name is substituted too, and a failure reason is kept only when Enodia wrote it in its own words. And the
exported outing still reconciles, which is the point: it is an example of a walk, and if Enodia
could not read it back it would be an example of nothing.

The pseudonyms are an HMAC keyed by a secret made once in `$XDG_CONFIG_HOME/enodia`, so two
exports months apart give the same router the same name and an experiment can be published in
parts. `--key-file` names another. Publishing the digest is safe precisely because it is keyed:
without the key nobody can work out the digest of a guessed network name, which is the whole
objection to a bare hash of an SSID.

**It is pseudonymised and not anonymous**, and the command says so every time it runs. A
pseudonym is stable, which is what makes the file worth having and what the word anonymous would
deny. A radio fingerprint locates itself: the set of access points at a corner is that corner's
identity, which is exactly how `--locate` works, so anybody who walks the same streets with
their own scanner can join their real addresses onto this. And the shape of the walk survives
the move, which is what reproducing the numbers needs and what makes the route searchable
against a map. Read the export before publishing it. That is the only check that counts.

The two fields that carry free text out have domains rather than being copied. An `event`
outside Enodia's own vocabulary is not a record of a walk, so the record is left out, and a
`security` label the backends are not known to write is withheld. Both are counted and both are
in the report: a type stops a field nobody named from leaving, and only a domain stops a named
field carrying whatever a hand-edited line put in it.

Before it writes, it searches what it is about to write for every address, network name,
interface and street name the two source files carry, all the walks in the file and not only
the one going out, the notebook's comments included, and names anything it finds. It reports
and does not refuse, because a substring search cannot tell a leak from a coincidence: a
network called `date` matches every directive of the exported notebook. What it is for is the
field somebody adds to the log next year and forgets to substitute.

`--out` names a directory and the directory is the export: it is built beside where it goes and
moved into place whole, so `--out` must not already exist, not even as an empty directory, and
two of these aimed at the same place cannot both think they succeeded. It is written privately
and opened up only once it is complete. The
key is created the same way, once, so that two exports starting together cannot end up with one
walk pseudonymised under a key that no longer exists.

The map, the CSV, the GeoJSON, the SVG and the streets file are not exported. They are derived
from these two, so whoever receives a walk can regenerate them.

## Log format

The log is [JSON Lines](https://jsonlines.org/): one JSON object per line, appended as the walk goes. Eight kinds of record are written: `connected` and `disconnected` when an interface's association changes, `scan` with every access point seen on each cycle, `new` with the ones seen for the first time, `scan_failed` when the daemon could not scan (with the reason), `suspended` on waking from a suspend (with the seconds asleep), `mark` on each press of the headset button (with its number), and `button_lost` when the kernel says its input queue overran and a press may have gone missing. Only `scan` records count as observations.

```
{"time": "2026-09-05T00:14:03-03:00", "event": "scan", "outing": "3f9a2b10", "interface": "wlan0", "networks": [{"ssid": "Home", "bssid": "aa:bb:cc:dd:ee:ff", "security": "psk", "frequency": 5180, "signal_dbm": -47, "signal_percent": 100, "connected": true}, {"ssid": "Cafe libre", "bssid": null, "security": "open", "frequency": null, "signal_dbm": null, "signal_percent": 38, "connected": false}]}
```

One line per record is what the format is for. The log is written from a laptop that may lose power mid-line, and a line that was never finished costs only itself. And the most hostile thing in the file is the SSIDs, which whoever owns the network chooses and which may hold commas, quotes or newlines. JSON escapes all of it, and accented and non-Latin names stay readable because nothing is escaped into `\u` sequences.

Frequencies are in MHz, as the backend reports them, not channel numbers: a channel is derived and ambiguous across bands, and which band an access point is on is what says how far a given signal strength puts it. Anything a backend cannot report is `null`, not a placeholder to parse back, with one exception: a network that hides its name is written `"ssid": ""`, because that is how the reader will hand it back and the file says what it is going to mean rather than something that has to be turned into it. Every record carries the `outing` it belongs to, a token for one run of the loop, and a scan record also carries the `cycle`, the pass of the loop it came from. Both exist because the clock has one second of resolution and two of anything can share a second. [The design notes](docs/design.md) tell that story, along with what is checked when a log is read back and why.

Because every record is a line of JSON, the log works with ordinary tools:

```bash
jq -r 'select(.event=="scan") | .networks[] | select(.security=="open") | .ssid' 2026-09-14T17-45-03.jsonl | sort -u
wc -l 2026-09-14T17-45-03.jsonl          # how many records the outing produced
```

## From Python

```python
from enodia import BackgroundVoice, ESpeak, NetworkLog, VoiceController, WifiMonitor

with WifiMonitor(
    voice=BackgroundVoice(VoiceController(ESpeak())), log=NetworkLog("walk.jsonl")
) as monitor:
    monitor.scan_networks()  # one cycle. Returns the networks seen for the first time
    monitor.scan_networks_loop()  # until interrupted, or until stop() from another thread
```

| Module | Entry points |
|---|---|
| `enodia.monitor` | `WifiMonitor`, with `scan_networks()`, `scan_networks_loop()`, `auto_scan()`, `use_button()`, `mark()`, `resume_from_log()`, and `close()` (also a context manager) |
| `enodia.netlog` | `NetworkLog(path)` writes. `read_log(path)` parses, skipping any line that is not a whole JSON object. `outings(records)` and `records_for_outing(records, outing)` pick one walk out of a file. `find_open_networks(path)` |
| `enodia.voice` | `VoiceController` speaks now. `BackgroundVoice(controller)` queues, dropping utterances marked `optional` when it falls behind. `ESpeak`, `PicoTTS` |
| `enodia.button` | `ButtonMarker(devices, on_press)`, `find_button_devices()`, `list_input_devices()`, `event_age(at)` |
| `enodia.reconcile` | `reconcile(log, notebook, by_movement=True, outing=None)`, `check_pace(...)`, `check_passes(...)`, `read_notebook(path, day, tz, marks)`, `network_turnover(a, b)`, `place_by_movement(scans, waypoints)`, `signal_weight(dbm, exponent)` |
| `enodia.fingerprint` | `add_to_map(map, log, notebook)`, `read_map(path)`, `locate_scan(fingerprints, networks)`, `check_map(fingerprints)`, `scan_now(interface)`, `canonical(a, b, fraction)` |
| `enodia.geocode` | `geocode_notebook(notebook, area)`, `read_crossings(path)`, `junction_of(a, b, places)`, `overpass_query(streets, area)`, `parse_proxy(url)` |
| `enodia.draw` | `svg_map(result, streets)`, `Frame.around(places)` |
| `enodia.streets` | `read_streets(path)`, `write_streets(path, streets)`, `StreetMap.between(here, there)`, `point_along(line, fraction)` |
| `enodia.preflight` | `run_preflight(...)`, `format_preflight(checks, color)` |
| `enodia.system` | `session_log_path(directory)`, `data_dir()`, `map_path()`, `battery()`, `lid_switch_setting()` and `scan_mac_setting()`, each answering with what it read and with the files it could not |

## Development

The `dev` dependency group (pytest, ruff, ty) is installed by `uv sync` unless you pass `--no-dev`. `ruff check` and `ruff format --check` lint and format, `ty check` checks the types, and `pytest` runs the suite with coverage. CI (`.github/workflows/tests.yml`) runs all four on Python 3.10 to 3.14. No test touches this machine's radio or input devices: `tests/conftest.py` refuses any ifpeek call or device listing a test has not stood in for.

## Further reading

- [Every flag](docs/cli.md), grouped by when you reach for it.
- [Setting up the machine](docs/setup.md): the lid, and permission to read the headset button.
- [Why Enodia is deliberately conservative](docs/design.md): what the program refuses to do, and the mistake that taught it to refuse. It is the longest document here and the one worth reading if you want to know why any of this is shaped the way it is.

## License

MIT.
