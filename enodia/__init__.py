"""Enodia: wardriving on foot, without GPS.

A talking Wi-Fi scanner in a backpack, a paper notebook of street crossings, and a
reconciliation that places every access point along the walk.
"""

__version__ = "0.1.0"

from enodia.button import ButtonMarker, InputDevice, find_button_devices, list_input_devices
from enodia.draw import Frame, svg_map
from enodia.fingerprint import (
    Fingerprint,
    Location,
    Place,
    add_to_map,
    check_map,
    locate_scan,
    read_map,
    write_map,
)
from enodia.geocode import (
    Crossing,
    GeocodeError,
    Junction,
    Proxy,
    geocode_notebook,
    junction_of,
    overpass_query,
    parse_proxy,
    read_crossings,
)
from enodia.monitor import WifiMonitor
from enodia.netlog import NetworkLog, find_open_networks, read_log
from enodia.preflight import Check, format_preflight, run_preflight
from enodia.reconcile import (
    RepeatedStretch,
    Stretch,
    Waypoint,
    check_pace,
    check_passes,
    confusable_crossings,
    network_turnover,
    place_by_movement,
    read_notebook,
    reconcile,
    repeated_stretches,
    route_stretches,
)
from enodia.streets import (
    Street,
    StreetMap,
    distance_metres,
    line_length_m,
    point_along,
    read_streets,
    write_streets,
)
from enodia.system import battery, data_dir, lid_switch_setting, session_log_path
from enodia.voice import (
    BackgroundVoice,
    ESpeak,
    PicoTTS,
    VoiceController,
    VoiceError,
    VoiceUnavailable,
)

__all__ = [
    "BackgroundVoice",
    "ButtonMarker",
    "Check",
    "Crossing",
    "ESpeak",
    "Fingerprint",
    "Frame",
    "GeocodeError",
    "InputDevice",
    "Junction",
    "Location",
    "NetworkLog",
    "PicoTTS",
    "Place",
    "Proxy",
    "RepeatedStretch",
    "Street",
    "StreetMap",
    "Stretch",
    "VoiceController",
    "VoiceError",
    "VoiceUnavailable",
    "Waypoint",
    "WifiMonitor",
    "add_to_map",
    "battery",
    "check_map",
    "check_pace",
    "check_passes",
    "confusable_crossings",
    "data_dir",
    "distance_metres",
    "find_button_devices",
    "find_open_networks",
    "format_preflight",
    "geocode_notebook",
    "junction_of",
    "lid_switch_setting",
    "line_length_m",
    "list_input_devices",
    "locate_scan",
    "network_turnover",
    "overpass_query",
    "parse_proxy",
    "place_by_movement",
    "point_along",
    "read_crossings",
    "read_log",
    "read_map",
    "read_notebook",
    "read_streets",
    "reconcile",
    "repeated_stretches",
    "route_stretches",
    "run_preflight",
    "session_log_path",
    "svg_map",
    "write_map",
    "write_streets",
]
