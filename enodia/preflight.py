"""Ten seconds before leaving: does everything Enodia needs actually work?

The screen is shut inside a backpack for the next four hours, and every check
here is a failure the operator would otherwise find out about on getting home:
a Wi-Fi daemon that refuses to scan, a voice engine that is not installed, a
lid that suspends the laptop, a headset button nobody may read, a battery that
will not last. Each check answers in one line, and the exit status says whether
to go.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import ifpeek

from enodia.button import ButtonMarker
from enodia.system import (
    OFF,
    ON,
    battery,
    data_dir,
    lid_switch_setting,
    scan_mac_setting,
    session_log_path,
)
from enodia.voice import VoiceController

OK, WARN, FAIL = "OK", "WARN", "FAIL"
COLORS = {OK: "\033[32m", WARN: "\033[33m", FAIL: "\033[31m"}
BOLD, RESET = "\033[1m", "\033[0m"
BATTERY_WARN_BELOW = 50
BATTERY_FAIL_BELOW = 20


@dataclass(frozen=True)
class Check:
    """One thing that has to be true before an outing, and whether it is."""

    name: str
    status: str
    detail: str

    @property
    def failed(self) -> bool:
        return self.status == FAIL


def walking_with(interfaces: Sequence[str] | None = None) -> list[str]:
    """The interfaces the outing will use: the ones asked for, or all of them.

    The preflight has to try what the walk will try. Checking the first Wi-Fi
    card the kernel happens to list, when `-i wlan1` says the walk is on the
    other one, reports on a radio nobody is taking anywhere.
    """
    return list(interfaces) if interfaces else ifpeek.get_wifi_interfaces()


def check_interfaces(interfaces: Sequence[str] | None = None) -> Check:
    interfaces = walking_with(interfaces)
    if not interfaces:
        return Check("interfaces", FAIL, "no Wi-Fi interface found")
    states: list[str] = []
    on: list[str] = []
    blocked: list[str] = []
    unreadable: list[str] = []
    for interface in interfaces:
        try:
            block = ifpeek.interface_rfkill(interface)
        # A switch that cannot be read says nothing either way, and saying so is
        # better than a traceback out of a command whose job is to report.
        except Exception as exc:  # noqa: BLE001
            states.append(f"{interface} (switch unreadable: {exc})")
            unreadable.append(interface)
            continue
        states.append(f"{interface} ({block + ' blocked' if block else 'radio on'})")
        (blocked if block else on).append(interface)
    detail = ", ".join(states)
    if not on:
        # Every card answered that it was blocked, which is the one answer worth
        # staying home for. A card whose switch could not be read answered
        # nothing, so counting it as a blocked radio would be putting words in
        # its mouth: the honest verdict is that this does not know, and the scan
        # check right after it asks the radio itself.
        if not unreadable:
            return Check("interfaces", FAIL, detail + ": no radio on")
        return Check("interfaces", WARN, detail + ": no radio known to be on")
    return Check("interfaces", WARN if blocked or unreadable else OK, detail)


def check_scan(interfaces: Sequence[str] | None = None) -> Check:
    """Whether a real scan works, on every radio the walk will use.

    Every one and not the first: the monitor asks them all and carries on with
    whichever answer, so a first card that cannot scan is a warning about that
    card and not a reason to stay home.
    """
    cards = walking_with(interfaces)
    if not cards:
        return Check("scan", FAIL, "nothing to scan with")
    answered, broke = [], []
    for card in cards:
        found = _one_scan(card)
        # A daemon that scanned and found nothing answered: that is a quiet
        # street, or a warning worth one line, and not a radio that cannot scan.
        (broke if found.status == FAIL else answered).append(found)
    if not answered:
        return Check("scan", FAIL, ", ".join(one.detail for one in broke))
    detail = ", ".join(one.detail for one in answered + broke)
    worst = WARN if broke or any(one.status == WARN for one in answered) else OK
    return Check("scan", worst, detail)


def _one_scan(interface: str) -> Check:
    try:
        seen = ifpeek.scan_access_points(interface=interface, fresh=True)
    # Anything at all is a failed preflight, and the reason is the message.
    except Exception as exc:  # noqa: BLE001
        return Check("scan", FAIL, f"{interface}: {exc}")
    if not seen:
        return Check("scan", WARN, f"{interface}: the daemon scanned, but found no networks")
    return Check("scan", OK, f"{interface}: {len(seen)} networks found by a fresh scan")


def check_voice(voice: VoiceController | None) -> Check:
    """Actually say something: finding the binary is not the same as hearing it."""
    if voice is None or voice.selected_voice is None:
        return Check("voice", WARN, "no speech engine: Enodia will print instead of speaking")
    voice.say("Preflight", say_silent=True)
    if not voice.available:
        return Check("voice", FAIL, voice.disabled_reason or "the engine failed")
    return Check("voice", OK, f"{type(voice.selected_voice).__name__} spoke")


def check_lid(conf: Path | None = None, dropins: Sequence[Path] | None = None) -> Check:
    kwargs: dict[str, Any] = {}
    if conf is not None:
        kwargs["conf"] = conf
    if dropins is not None:
        kwargs["dropins"] = tuple(dropins)
    setting, unread = lid_switch_setting(**kwargs)
    caveat = " (a desktop environment may handle the lid itself; check its power settings too)"
    if unread:
        # logind reads that file with its own privileges and this does not, so
        # reporting what the files it could read said is a verdict about a
        # configuration that may not be the machine's.
        return Check(
            "lid",
            WARN,
            "cannot say: "
            + ", ".join(str(path) for path in unread)
            + " is there and cannot be read here, and logind reads it. "
            + (f"The rest of the files say HandleLidSwitch={setting}" if setting else "")
            + caveat,
        )
    if setting is None:
        return Check(
            "lid",
            FAIL,
            "HandleLidSwitch is not set, so closing the lid suspends "
            "the laptop: set HandleLidSwitch=ignore in logind.conf" + caveat,
        )
    if setting in ("ignore", "lock"):
        return Check("lid", OK, f"HandleLidSwitch={setting}" + caveat)
    return Check("lid", FAIL, f"HandleLidSwitch={setting}: closing the lid ends the walk" + caveat)


def check_scan_mac(**where: Any) -> Check:
    """Whether the address a scan goes out under is randomised.

    Asking for a fresh scan makes the card send probe requests, and those carry
    the sender's MAC, so an outing lays a trail under whatever address the card
    is using. This only reads what the Wi-Fi daemon was told, because changing
    it needs root and fights the daemon that owns the interface, and both
    daemons already do it properly when asked.
    """
    found = scan_mac_setting(**where)
    worries = []
    told = sorted(set(found.devices.values()))
    if found.unsure:
        # A file that decides the answer and could not be weighed: no permission
        # to open it, or a `[.config] enable=` predicate about the daemon's
        # version or environment. Either way the effective setting is not
        # established, and the daemon reads that file perfectly well itself.
        worries.append(
            "this could not weigh "
            + ", ".join(f"{path} ({why})" for path, why in found.unsure)
            + ", so the configuration that is actually in force is not established"
        )
    if found.elsewhere:
        # The key parses in any section and only does anything in a device one.
        worries.append(
            "wifi.scan-rand-mac-address is set under "
            + ", ".join(f"[{name or 'top'}]" for name in sorted(found.elsewhere))
            + ", which is not a section NetworkManager reads device properties from, so it "
            "does nothing"
        )
    if found.masks:
        # Confirmed against NetworkManager's manual: the mask fixes some bits of
        # the generated address and leaves the rest randomised. How many bits it
        # leaves is not worked out here, so the word is withheld rather than
        # qualified.
        worries.append(
            "wifi.scan-generate-mac-address-mask is set ("
            + ", ".join(f"[{name}]={value}" for name, value in sorted(found.masks.items()))
            + "), which fixes some bits of the scanning address and randomises only the rest, "
            "so how much of it varies is not established here"
        )
    if len(told) > 1:
        # [device-wlan0] and [device-wlan1] can disagree through match-device.
        # Which one applies to the card you walk with is a question about
        # NetworkManager's device matching, and guessing at it here would be
        # reporting a setting this cannot know is yours.
        worries.append(
            "NetworkManager sets wifi.scan-rand-mac-address more than one way ("
            + ", ".join(
                f"[{name or 'top'}]={value}" for name, value in sorted(found.devices.items())
            )
            + "), so which one your card gets depends on match-device and is not read here"
        )
    elif told and told[0].lower() in OFF:
        worries.append(
            f"NetworkManager has wifi.scan-rand-mac-address={told[0]}, so every scan of the "
            "walk goes out under this card's own address"
        )
    elif told and told[0].lower() not in ON:
        # The setting is a boolean, documented as yes by default. Anything that
        # is neither a yes nor a no is a value NetworkManager is not being asked
        # for, and reading it as a yes because it is not a no is how
        # `wifi.scan-rand-mac-address=banana` came back as a green line.
        worries.append(
            f"NetworkManager has wifi.scan-rand-mac-address={told[0]}, which is neither of the "
            "values it takes, so what the daemon made of it is not established here"
        )
    if found.iwd is not None:
        # Said carefully, and never as an OK. iwd documents AddressRandomization
        # as the address the interface uses, which is a related question and not
        # this one, and what it does for scanning specifically is not something
        # this has a source for. Not established is a warning, not a pass.
        worries.append(
            f"iwd has AddressRandomization={found.iwd}, which is about the address the "
            "interface "
            "uses. What iwd does for scanning specifically is not established here, so check "
            "that yourself"
        )
    if worries:
        caveat = (
            " Only the daemon you actually run is about you."
            if told and found.iwd is not None
            else ""
        )
        return Check("scan mac", WARN, ". ".join(worries) + "." + caveat)
    if told:
        # The one case that earns the word: one section, one value, and a value
        # NetworkManager documents, for the setting that is about scanning.
        return Check(
            "scan mac",
            OK,
            f"NetworkManager randomises it (wifi.scan-rand-mac-address={told[0]})",
        )
    # Neither daemon left a configuration file behind, and scanning works
    # through any of iwd, NetworkManager or wpa_supplicant. Which of the three
    # answered is not known here, so neither is what address it used. Claiming
    # NetworkManager's default would be answering for a daemon that may not be
    # the one running, which is the failure this whole check exists to catch.
    return Check(
        "scan mac",
        WARN,
        "unknown: no NetworkManager or iwd configuration found, and scanning also works "
        "through wpa_supplicant. Which daemon answered, and what address it scanned under, "
        "is not established here",
    )


def check_button(button: ButtonMarker | None, choice: str) -> Check:
    if choice == "off":
        return Check("button", OK, "off")
    if button is None:
        return Check(
            "button",
            WARN,
            "no input device with media keys: crossings go in the notebook by the spoken time",
        )
    try:
        opened = button.open()
    except PermissionError as exc:
        return Check("button", FAIL, str(exc))
    finally:
        button.close()
    if not opened:
        return Check("button", FAIL, "none of the devices could be opened")
    return Check("button", OK, ", ".join(str(path) for path in opened))


def check_battery(root: Path | None = None) -> Check:
    reading = battery() if root is None else battery(root)
    if reading is None:
        return Check("battery", WARN, "no battery reported by the kernel")
    percent, status = reading
    detail = f"{percent}%, {status.lower()}"
    if percent < BATTERY_FAIL_BELOW:
        return Check("battery", FAIL, detail + ": not enough for an outing")
    if percent < BATTERY_WARN_BELOW:
        return Check("battery", WARN, detail)
    return Check("battery", OK, detail)


def check_log(log: Path | None, log_dir: Path | None = None, resume: bool = False) -> Check:
    if log is not None:
        # Opened for real rather than taken on trust. A path into a directory
        # that is not there passes every look at the string and fails on the
        # first scan, which is two hours later with the screen shut in a bag.
        try:
            with log.open("a", encoding="utf-8"):
                pass
        except OSError as exc:
            return Check("log", FAIL, f"{log} cannot be written to: {exc}")
        return Check("log", OK, str(log))
    directory = log_dir if log_dir is not None else data_dir()
    try:
        path, continuing = session_log_path(directory, resume=resume)
    except OSError as exc:
        return Check("log", FAIL, f"cannot use {directory}: {exc}")
    return Check("log", OK, ("continuing " if continuing else "new outing: ") + str(path))


def run_preflight(
    voice: VoiceController | None,
    button: ButtonMarker | None,
    button_choice: str,
    log: Path | None,
    lid_conf: Path | None = None,
    lid_dropins: Sequence[Path] | None = None,
    battery_root: Path | None = None,
    log_dir: Path | None = None,
    resume: bool = False,
    interfaces: Sequence[str] | None = None,
) -> list[Check]:
    """Every check, in the order they matter."""
    return [
        check_interfaces(interfaces),
        check_scan(interfaces),
        check_voice(voice),
        check_lid(lid_conf, lid_dropins),
        check_scan_mac(),
        check_button(button, button_choice),
        check_battery(battery_root),
        check_log(log, log_dir, resume),
    ]


def format_preflight(checks: Sequence[Check], color: bool = False) -> str:
    """One line per check, and the verdict. With `color`, green, yellow and red."""

    def paint(status: str, text: str) -> str:
        return f"{COLORS[status]}{text}{RESET}" if color else text

    lines = [f"{paint(c.status, f'{c.status:<5}')} {c.name:<11} {c.detail}" for c in checks]
    failed = [check.name for check in checks if check.failed]
    verdict = f"Do not go: {', '.join(failed)}." if failed else "Ready to go."
    if color:
        verdict = f"{BOLD}{paint(FAIL if failed else OK, verdict)}"
    lines += ["", verdict]
    return "\n".join(lines)
