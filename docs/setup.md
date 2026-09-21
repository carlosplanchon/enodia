# Setting up the machine

Four things about the laptop have to be settled before an outing, and `enodia --preflight`
tells you whether they are. Everything here is shown for Arch Linux with systemd. The radio and
the voice usually work already; the lid and the button usually do not.

## The lid

Closing the lid suspends the laptop on any default systemd setup, and Enodia with it.

The quickest answer needs no configuration at all, because systemd has an inhibitor lock for
exactly this. It lifts the lid handling for one command and puts it back when that command ends:

```bash
systemd-inhibit --what=handle-lid-switch --why="Enodia is walking" uv run enodia --say-status
```

**The preflight will still say `FAIL` on its `lid` line while you do this, and the walk is safe
anyway.** It reads the configuration, which has not changed, and not the inhibitor locks held
right now. Worth knowing before it sends you looking for a problem that is not there.

The permanent answer is a drop-in for logind, which survives package updates where editing
`/etc/systemd/logind.conf` might not:

```bash
sudo mkdir -p /etc/systemd/logind.conf.d
sudo tee /etc/systemd/logind.conf.d/lid.conf <<'EOF'
[Login]
HandleLidSwitch=ignore
EOF
sudo systemctl kill -s HUP systemd-logind      # logind rereads its configuration, no restart
```

With `ignore`, closing the lid does nothing, on battery or plugged in, and not only for Enodia:
the laptop stays on inside any bag. That is the trade, and it is why the inhibitor is worth
knowing about first.

Either way, a desktop environment such as GNOME or KDE may take the lid over from logind and decide on its own. Then its power settings are the ones that count, and the preflight cannot see them. The test that settles it is the same in every case: with Enodia running, close the lid for a minute and open it. If it does not say "The laptop slept for one minute", the lid is done.

## The Wi-Fi daemon

Enodia never touches the radio itself. It asks whichever daemon is running, iwd,
NetworkManager or wpa_supplicant, over D-Bus, and that daemon decides whether to answer. So
what has to be settled here is not a driver but a permission.

The preflight's `scan` line is the test, and it asks **every** interface the walk will use
rather than the first, since a second card that cannot scan is a warning about that card and
not a reason to stay home:

```bash
uv run enodia --preflight
```

It says `FAIL` with the daemon's own error when the answer was refused. The usual cause is that
the user is not on the daemon's D-Bus policy, and the usual fix is a group: `wheel` or
`network`, depending on which one the distribution's policy files name.

```bash
sudo usermod -aG network "$USER"     # or wheel, whichever the policy names
```

The `-a` matters: without it, `-G` replaces your supplementary groups instead of adding one.
Group membership is read when a session starts: log out and back in.

A `scan` line saying the daemon scanned and found no networks is not a permission problem. That
is a quiet street, and it is reported as a warning rather than a failure for that reason.

Two neighbouring lines. `--no-fresh` reads the daemon's cached view instead of asking for a
scan, which is faster and can be days out of date, so it is not what an outing wants. And
`scan mac` reports whether the daemon randomises the address it scans with, which is about what
your laptop broadcasts rather than about what it hears.

## The voice

Two engines are supported and neither is required: without one, Enodia prints what it would
have said and the walk is otherwise unaffected.

- **espeak-ng** is the default, and the classic `espeak` binary is accepted in its place.
- **SVOX Pico** is the fallback: `libttspico-utils` on Debian and Ubuntu, which gives
  `pico2wave`, or `pico-tts` from the AUR on Arch. Pico writes a WAV rather than playing one,
  so it also needs a player, and the first of `paplay`, `pw-play` and `aplay` found is used.

```bash
uv run enodia --voice espeak       # force one engine
uv run enodia --voice pico
uv run enodia --voice none         # print instead of speaking
```

The preflight's `voice` line **actually says something** rather than looking for a binary,
because a `pico2wave` with no working player is found easily and cannot be heard. `WARN` means
no engine was found at all and Enodia will print. `FAIL` means one was found and could not
speak, and the reason is the message.

If it says `FAIL`, the engine is the place to start, outside Enodia:

```bash
espeak-ng "preflight"                                          # does the engine itself speak?
pico2wave -w /tmp/t.wav "preflight" && paplay /tmp/t.wav       # engine and player separately
```

## Reading the headset button

Reading `/dev/input` is what the button needs, and the devices there belong to `root` and the `input` group, mode `crw-rw----`. The simple fix is to join the group:

```bash
sudo usermod -aG input "$USER"
```

The `-a` matters here too. Without it, `-G` replaces your supplementary groups and you lose `wheel`. Group membership is read when a session starts, so log out and back in, or reboot. Opening a new terminal is not enough. To try it right away in one shell, `newgrp input` starts a shell that already has the group.

If the preflight still fails afterwards, three commands say why:

```bash
id                          # does this session have the group? look for "input"
getent group input          # are you in the group at all? your user should be listed
ls -l /dev/input/event3     # is the device readable by the group? "root input", "crw-rw----"
```

If `getent` lists you and `id` does not, the session predates the `usermod`: log out and back in. If `getent` does not list you, the `usermod` did not run for this user. If the device is not owned by the `input` group, the group cannot help, and a udev rule can.

Being in `input` means being able to read every input device, the keyboard included, from any process that runs as you. It is the usual arrangement on Arch and what `evtest` needs anyway, but it is a real widening. The narrower alternative is a udev rule for the headset's device alone. `udevadm info /dev/input/eventN` shows its `ATTRS{name}` and, for USB or Bluetooth, its `ID_VENDOR_ID` and `ID_MODEL_ID`. With that:

```bash
sudo tee /etc/udev/rules.d/70-enodia-button.rules <<'EOF'
SUBSYSTEM=="input", KERNEL=="event*", ATTRS{name}=="<the device's exact name>", TAG+="uaccess"
EOF
sudo udevadm control --reload                    # new rules, for new events
sudo udevadm trigger --subsystem-match=input     # and for the devices already plugged in
```

`uaccess` has logind grant an ACL on that one device to the user of the active session, for as long as the session lasts, with no group involved. `getfacl /dev/input/eventN` shows the entry. `uv run enodia --button list` names the devices without needing any of this, since it reads `/sys`, and is how to find out which one is the headset before writing the rule.

### Which device is the headset

The devices are read straight from `/dev/input`, with no library in between: the kernel's `struct input_event` is a stable interface, and `/sys/class/input` says which devices have media keys. Whether a 3.5 mm headset's button reaches the kernel at all depends on the sound codec (many laptop jacks only detect the plug), while USB and Bluetooth headsets report their buttons reliably. `evtest /dev/input/eventN` shows what a device actually sends, and is the way to find out before an outing rather than after.

See also the [README](../README.md) for what the button does during a walk, and
[the CLI reference](cli.md) for `--button`.
