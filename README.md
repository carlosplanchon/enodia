![A walker on a Montevideo street, carrying a laptop in a backpack, pressing a headset button and holding a notebook of street crossings.](https://raw.githubusercontent.com/carlosplanchon/enodia/main/assets/enodia_walk.jpg)

# Enodia

**Know where you are in a city from its Wi-Fi alone. No GPS, no location service: a map you walked yourself.**

[![tests](https://github.com/carlosplanchon/enodia/actions/workflows/tests.yml/badge.svg)](https://github.com/carlosplanchon/enodia/actions/workflows/tests.yml)
[![PyPI version](https://img.shields.io/pypi/v/enodia.svg)](https://pypi.org/project/enodia/)
[![Python versions](https://img.shields.io/pypi/pyversions/enodia.svg)](https://pypi.org/project/enodia/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/carlosplanchon/enodia)

Enodia turns a Linux laptop in a backpack into a Wi-Fi scanner that talks. You walk, and it logs every network in reach and tells you through your headphones what changes. At each corner you press the headset button and write the corner down. Back home, Enodia lines the two up: every scan gets its place along your route, every router an estimated position, and the walk becomes a map. Walk those streets again and it tells you where you are, as you go.

It all runs on your machine. The one command that goes online looks your corners up on OpenStreetMap, and only when you ask it to.

![A real walk in Dolores, Uruguay: 31 blocks and 39 corners, and where each of 652 access points probably stands, drawn over the neighbourhood from OpenStreetMap.](https://raw.githubusercontent.com/carlosplanchon/enodia/main/samples/dolores/dolores-plan.svg)

*A real walk, published in [`samples/dolores/`](samples/dolores/README.md) with every network pseudonymised: the route, the corners marked along it, and where each access point probably stands. A dot is a router the walk pinned down, a ring one it only heard from around there.*

## What it does

- **Records a walk by ear.** A scan every five seconds, every network logged with its signal, and the news in your headphones: "New network found", "Mark 3". Nothing to look at while you walk.
- **Places everything along your route.** It joins the log with the corners you noted, places each scan between two of them, and estimates where each router stands. Out come a plan in SVG, a table in CSV and the walk in GeoJSON.
- **Builds a map from your walks** and tells you where a scan was taken: `between "Rivera y Brito del Pino" and "Rivera y Simón Bolívar", 53% of the way`, or `at the corner of "Rivera y Brito del Pino"`.
- **Follows you live.** `--locate --watch` scans as you walk, says where you are when it changes, and draws you on a page that reloads itself, over the streets, the water and the parks, with no connection.
- **Checks itself.** Each estimate has a command that measures it against the walk: a corner held out and placed again, a block walked twice set against itself, a pass held out and found again from the rest of the map.

## Install

You need Linux, Python 3.10 or newer, and a Wi-Fi interface run by iwd, NetworkManager or wpa_supplicant that you may scan with over D-Bus. For the voice, `espeak-ng` or SVOX Pico. Without either, Enodia prints what it would have said.

```bash
uv tool install enodia
```

`uv tool upgrade enodia` follows new releases. The proxy support of `--geocode` needs the `socks` extra: `uv tool install "enodia[socks]"`. Before the first walk, [set up the machine](docs/setup.md) so that it keeps scanning with the lid closed and hears the headset button.

To work on Enodia itself, run `uv sync` in a clone and `uv run enodia` instead.

## Try it now

The samples live in the repository rather than in the package, so clone it for them. No Wi-Fi card is involved:

```bash
git clone https://github.com/carlosplanchon/enodia.git
cd enodia
enodia --locate samples/synthetic_montevideo/rivera-query.jsonl \
  --map samples/synthetic_montevideo/rivera-map.jsonl --voice none
```

```text
You are between "Rivera y Brito del Pino" and "Rivera y Simón Bolívar", 53% of the way
  around [-34.90311, -56.15836]
  2 walks agree, best similarity 89%, spread 0% of the stretch (1 m)
  from evidence last gathered 2026-09-17 17:02
```

That one is synthetic: real streets of Montevideo, invented radios, made to show the workflow ([how it was made](samples/synthetic_montevideo/README.md)). The walk in Dolores is real. To see how well its map finds its own passes, which takes about two minutes:

```bash
enodia --check-map --map samples/dolores/dolores-map.jsonl
```

## Your first walk

**1. Check the machine, then walk.**

```bash
enodia --preflight --log walk.jsonl
enodia --log walk.jsonl
```

Press the headset button at every corner, the first and the last included. Enodia answers "Mark 1", "Mark 2" and so on. Two or three blocks are enough for a first try. Ctrl+C ends the walk.

**2. Write the corners down** in `notebook.txt`, one line per mark:

```text
#1 Rivera y Avenida Doctor Francisco Soca
#2 Rivera y Brito del Pino
```

Without a button, write the time instead of the mark: `17:45:00 Rivera y Brito del Pino`.

**3. Put the walk on the map.**

```bash
enodia --geocode notebook.txt --area Montevideo --streets streets.jsonl --surroundings
enodia --reconcile walk.jsonl notebook.geo.txt --streets streets.jsonl --svg plan.svg
enodia --map-add walk.jsonl notebook.geo.txt --streets streets.jsonl
```

`--geocode` looks the corners up on OpenStreetMap and writes `notebook.geo.txt` beside the notebook, with the streets and the neighbourhood into `streets.jsonl`. It is optional: without coordinates, every place is a fraction of the way between two corners.

**4. Come back, and find yourself.**

```bash
enodia --locate --watch --streets streets.jsonl --live-map live.html
```

Open `live.html` in a browser once and leave it open. `enodia --assistant` is a menu for the same steps, the lookup aside: the walk, the reconciliation, the map and finding yourself.

## How good is it?

One real walk so far. It is in the repository, so the first three numbers can be run again. The fourth comes from walking the same streets with Enodia the next day.

- **The corners.** Held out one at a time and placed again from the walk alone, they land 9 m from where they were on average, and 4 m with `--pace clock` (`--check-pace`).
- **The routers.** Seven blocks walked twice place the same networks within 16% of a block of each other on average (`--check-passes`).
- **Finding yourself.** A pass held out and located from the rest of the map lands 38 m from where it was on average (`--check-map`). Most of those answers are on the block next door, because most blocks were walked once and holding that pass out left nothing on them. The experimental `--along levels` brings it to 33 m.
- **In the street.** Walking with `--locate --watch`, matching on which networks are in view lost no scan on streets the map knows. Matching on their signal strength too lost a third of them, and it is not the default.

A second walk over the same streets, on another day, is the measurement that settles these. [The methodology](docs/methodology.md) says what each check can and cannot tell you.

## Limits

- The map knows the streets you walked, and it ages as routers move or disappear.
- Positions are estimates, and the report says how far to trust each one. A router the walk only heard from around there is drawn as a ring, never as a dot.
- Enodia has no GPS on purpose: the question is whether a map of radios can place you by itself, and a receiver in the loop would answer it for the radio. GPS belongs as ground truth, written beside a corner in the notebook (`@ -34.9066, -56.2001`).
- Fresh scans send probe requests. `--preflight` checks that the scanning address is randomised, and changes nothing.
- Logs and maps hold your neighbours' network names and addresses, and where they are. [`--export-public`](docs/export.md) makes a copy you can publish, and says what it cannot hide.

## Documentation

- [Machine setup](docs/setup.md): the lid, the Wi-Fi daemon, the voice, the headset button.
- [CLI reference](docs/cli.md): every flag, by the command you reach for it with.
- [File formats](docs/formats.md): the notebook you write, and the log and streets files Enodia writes.
- [Methodology](docs/methodology.md): how scans, routers and fingerprints are placed, and what each check measures.
- [Design notes](docs/design.md): each decision, and the failure behind it.
- [Publishing a walk](docs/export.md): what `--export-public` promises, and what it cannot.
- [From Python](docs/library.md): the same operations as function calls.

## License

[MIT](LICENSE).
