"""Nothing in the test suite may read this machine's network.

Every ifpeek function is replaced by one that raises, so a call that no test
deliberately stood in for fails loudly instead of quietly returning whatever
this laptop's Wi-Fi happens to be doing. Without this, adding a new ifpeek call
to the monitor leaves the suite green while it silently reads the real radio,
and the tests start depending on the machine they run on.
"""

import importlib
import os
import socket

import ifpeek
import pytest

from enodia import assistant, button, cli, geocode

# Every callable ifpeek exposes, minus its types (AccessPoint and friends, which
# the tests build stand-in data with).
SYSTEM_CALLS = tuple(
    sorted(
        name
        for name in dir(ifpeek)
        if not name.startswith("_")
        and callable(getattr(ifpeek, name))
        and not isinstance(getattr(ifpeek, name), type)
    )
)


class RealRadioCall(BaseException):
    """A test reached the real network, or the real input devices.

    Deliberately not an `Exception`: the monitor catches those on purpose, so
    that a Wi-Fi daemon falling over mid-outing does not kill the scan loop.
    A guard that those clauses can swallow is no guard.
    """


def _refuse(name):
    def refuse(*args, **kwargs):
        raise RealRadioCall(
            f"ifpeek.{name} was called for real. Stand it in from the test's "
            f"fake instead: reading this machine's network makes the test "
            f"depend on the machine it runs on."
        )

    return refuse


@pytest.fixture(autouse=True)
def no_radio(monkeypatch):
    """Refuse every ifpeek call a test has not deliberately stood in for."""
    for name in SYSTEM_CALLS:
        monkeypatch.setattr(ifpeek, name, _refuse(name))


_real_open_socket = geocode.open_socket


def _only_fake_sockets(
    host,
    port,
    proxy=None,
    timeout=geocode.READ_TIMEOUT_S,
    connect=socket.create_connection,
    import_socks=importlib.import_module,
):
    """open_socket, refusing the real network; a stood-in `connect` passes through.

    Refuses on the default still being the real one rather than on being called
    at all, the way `_only_fake_devices` does, so that every line of `open_socket`
    (the SOCKS branch, `rdns=True`, the missing-PySocks branch) is still reachable
    from a test without anything leaving this machine.
    """
    if connect is socket.create_connection:
        raise RealRadioCall(
            "a real socket was opened. Stand in the `connect` argument of "
            "enodia.geocode.open_socket, or the `fetch` argument of geocode_notebook: "
            "no test may go out to the network, and a lookup that quietly did would "
            "depend on OpenStreetMap being up and on whoever is watching the wire."
        )
    return _real_open_socket(host, port, proxy, timeout, connect, import_socks)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Refuse to open a real socket unless a test stands the connection in."""
    monkeypatch.setattr(geocode, "open_socket", _only_fake_sockets)


_real_list_input_devices = button.list_input_devices


def _only_fake_devices(sysfs=button.SYSFS_INPUT, devices=button.DEV_INPUT):
    """list_input_devices, refusing the real /sys and /dev; a fake tree passes through."""
    if sysfs == button.SYSFS_INPUT or devices == button.DEV_INPUT:
        raise RealRadioCall(
            "the real input devices were listed. Stand in enodia.cli.find_button_devices or "
            "list_input_devices, or pass --button off: tests must not depend on what is "
            "plugged into the machine they run on."
        )
    return _real_list_input_devices(sysfs, devices)


@pytest.fixture(autouse=True)
def no_input_devices(monkeypatch):
    """Refuse to list the real /sys/class/input unless a test stands it in."""
    monkeypatch.setattr(button, "list_input_devices", _only_fake_devices)
    monkeypatch.setattr(cli, "list_input_devices", _only_fake_devices)


def _only_scripted_answers(prompt=""):
    """`input`, refusing the real keyboard.

    The assistant is the first thing here that asks questions, and a test that
    reached this would not fail: it would sit waiting for a key that is never
    coming, in a suite nobody is watching. Every test hands `run_assistant` its
    own `ask`.
    """
    raise RealRadioCall(
        "a test asked the real keyboard a question. Pass `ask=` to "
        "enodia.assistant.run_assistant: a suite that waits for a keystroke hangs "
        "instead of failing."
    )


@pytest.fixture(autouse=True)
def no_keyboard(monkeypatch):
    """Refuse to read the real keyboard unless a test scripts the answers."""
    monkeypatch.setattr(assistant, "input", _only_scripted_answers, raising=False)


# Several tests make a file unreadable to check that Enodia says so instead of
# reporting an empty one. Root is not stopped by a mode of 000, so those tests
# would fail for a reason that has nothing to do with the code. Skipped rather
# than quietly passing: a check that does not check anything is worse than one
# that is not there.
as_root = pytest.mark.skipif(
    os.geteuid() == 0, reason="root reads a file whose mode is 000, so there is nothing to test"
)


@pytest.fixture(autouse=True)
def data_dir_in_tmp(monkeypatch, tmp_path):
    """Logs written by default go to a temporary XDG_DATA_HOME, never to $HOME."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))


@pytest.fixture(autouse=True)
def config_dir_in_tmp(monkeypatch, tmp_path):
    """And so does the export key, which is a secret this suite must not leave behind.

    Its sibling above covers the logs. Without this one, the first test that
    exported anything would make a real key in the real home of whoever ran the
    suite, once, quietly, and it would still be there years later.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
