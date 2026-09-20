# Setting up the machine

Two things about the laptop itself have to be settled once, and `enodia --preflight` tells
you whether they are. Both are shown for Arch Linux with systemd, and both take a minute.

## The lid

Closing the lid suspends the laptop on any default systemd setup, and Enodia with it. The permanent fix is a drop-in for logind, which survives package updates where editing `/etc/systemd/logind.conf` might not:

```bash
sudo mkdir -p /etc/systemd/logind.conf.d
sudo tee /etc/systemd/logind.conf.d/lid.conf <<'EOF'
[Login]
HandleLidSwitch=ignore
EOF
sudo systemctl kill -s HUP systemd-logind      # logind rereads its configuration, no restart
```

With `ignore`, closing the lid does nothing, on battery or plugged in, and not only for Enodia: the laptop stays on inside any bag. If you would rather keep the default and lift it only while Enodia runs, systemd has an inhibitor lock for exactly that, and it needs no configuration at all:

```bash
systemd-inhibit --what=handle-lid-switch --why="Enodia is walking" uv run enodia --say-status
```

While that command runs, logind ignores the lid. When it ends, the lid suspends again. The preflight reads the configuration and not the inhibitors, so with this approach its `lid` line stays `FAIL` even though the walk is safe.

Either way, a desktop environment such as GNOME or KDE may take the lid over from logind and decide on its own. Then its power settings are the ones that count, and the preflight cannot see them. The test that settles it is the same in every case: with Enodia running, close the lid for a minute and open it. If it does not say "The laptop slept for one minute", the lid is done.

## Reading the headset button

Reading `/dev/input` is what the button needs, and the devices there belong to `root` and the `input` group, mode `crw-rw----`. The simple fix is to join the group:

```bash
sudo usermod -aG input "$USER"
```

The `-a` matters. Without it, `-G` replaces your supplementary groups and you lose `wheel`. Group membership is read when a session starts, so log out and back in, or reboot. Opening a new terminal is not enough. To try it right away in one shell, `newgrp input` starts a shell that already has the group.

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

## Which device is the headset

The devices are read straight from `/dev/input`, with no library in between: the kernel's `struct input_event` is a stable interface, and `/sys/class/input` says which devices have media keys. That needs permission to read `/dev/input`, usually the `input` group, and no root. Whether a 3.5 mm headset's button reaches the kernel at all depends on the sound codec (many laptop jacks only detect the plug), while USB and Bluetooth headsets report their buttons reliably. `evtest /dev/input/eventN` shows what a device actually sends, and is the way to find out before an outing rather than after.

See also the [README](../README.md) for what the button does during a walk, and
[the CLI reference](cli.md) for `--button`.
