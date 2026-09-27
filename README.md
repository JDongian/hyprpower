# hyprpower

Every power setting on a Linux laptop in one table, next to what macOS and
Windows would do by default.

```
hyprpower
```

Reads the running system — never a config source, so it works with or without
a NixOS/dotfiles checkout present. Uses the tool that already owns each layer:
`busctl` for logind, `tlp-stat` for TLP, `upower` for the battery. Only
hypridle has no such tool, so its config file is parsed directly.

Anything it cannot read is printed as `unreadable`, never as a plausible
default.

## What it found on the first machine it ran on

- No screen-off step at all: the backlight never turns off on idle.
- No idle-sleep step: the machine stays awake indefinitely with the lid open.
- The dim step runs `brightnessctl set 10` — a *raw* value, 0.04% of a 24242
  scale, not the 10% the config comment claims.
- Battery health reads 100% after 501 cycles because charging stops at 80%,
  so the gauge has likely never recalibrated.
- logind's idle timer is armed at 30m with `IdleAction=ignore`.

None of these are visible from any single file.

## Status

v1 displays. Editing profiles and applying them are not implemented.

## Depends on

`upower` being enabled for battery history and cycle count; falls back to
sysfs (instantaneous values only) without it.
