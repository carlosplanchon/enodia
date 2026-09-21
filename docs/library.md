# Using Enodia from Python

Everything the command line does is a function call, and the pieces are importable on their own. Nothing here goes online or touches the radio except where it says so.

## The pieces

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
| `enodia.fingerprint` | `add_to_map(map, log, notebook)`, `read_map(path)`, `locate_scan(fingerprints, networks)`, `locate_sequence(fingerprints, scans)`, `check_map(fingerprints)`, `scan_now(interface)`, `canonical(a, b, fraction)` |
| `enodia.geocode` | `geocode_notebook(notebook, area)`, `read_crossings(path)`, `junction_of(a, b, places)`, `overpass_query(streets, area)`, `parse_proxy(url)` |
| `enodia.draw` | `svg_map(result, streets)`, `Frame.around(places)` |
| `enodia.streets` | `read_streets(path)`, `write_streets(path, streets)`, `StreetMap.between(here, there)`, `point_along(line, fraction)` |
| `enodia.preflight` | `run_preflight(...)`, `format_preflight(checks, color)` |
| `enodia.system` | `session_log_path(directory)`, `data_dir()`, `map_path()`, `battery()`, `lid_switch_setting()` and `scan_mac_setting()`, each answering with what it read and with the files it could not |

See [the CLI reference](cli.md) for the same operations as commands, and [the formats](formats.md) for what the files these read and write look like.
