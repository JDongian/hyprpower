# hyprpower

Power management for Hyprland laptops on NixOS.

![hyprpower](docs/screenshot.png)

## Why

The Hypr ecosystem has an idle daemon, a lock screen, a bar and a wallpaper
daemon. It has no power manager. GNOME and KDE ship one; here the pieces
exist and nothing joins them:

| | owns | splits by AC / battery |
|---|---|---|
| hypridle | idle timeouts | **no** ([issue #67](https://github.com/hyprwm/hypridle/issues/67), open) |
| logind | lid, power button | only the lid |
| acpid | ACPI events | no |
| TLP | hardware tunables | every setting |

There is nowhere to write down what the laptop should do, and nothing to
check that it does it. Several behaviours have no owner: acting on a battery
level, escalating a suspend to hibernate, and treating charge thresholds as
policy instead of a TLP setting.

hyprpower is that layer. One file, compiled to the four places the pieces
read:

```
profile.toml ──▶ hypridle.conf          one listener per rung per power source
             ──▶ logind drop-in         lid, power button
             ──▶ systemd sleep drop-in  suspend → hibernate delay
             ──▶ battery sysfs          charge thresholds
```

`hyprpower verify` checks all four against the profile. Each one drifts on
its own: hypridle reads its config once at startup, logind caches its own
until reloaded, and TLP rewrites the charge thresholds when it restarts.

## The profile

Every entry is the same primitive: **on a trigger, check a condition, take an
action.** The trigger is idle time, a charge level, the lid or the power
button. The condition is almost always which power source you are on. The
action comes from a closed set. Four daemons can be driven from one file
because every setting is written in that one shape.

hyprpower holds no values of its own. Every key must be present, and a
missing one is an error instead of a silent default, so the file is the whole
story. Defaults are a file you copy, not behaviour hidden in code.

Sleep is composed, not picked from a list of modes. Set a suspend and a
hibernate on the same power source and the gap between them becomes the
delay, so the second number is one you can read instead of a
`HibernateDelaySec` you cannot.

The shipped default explains each setting where it sits:
[`hyprpower/default.toml`](hyprpower/default.toml).

## The TUI

Four daemons' worth of settings on one screen, beside what the machine is
currently doing. What hyprpower owns is editable there; what TLP and logind
own is shown read-only, because a screen that left them out would be a
half-truth about the same laptop.

Seeing the values is not the same as seeing what they add up to, so it also
names settings that cannot work:

```
* Scheduled after the machine is already asleep, so it never runs.
      (Hibernate after = never / 20m)
* This suspend never reaches disk, so the battery can run flat.
      (Suspend after = never / 10m)
```

Both come from checks over the compiled listeners, so any rung scheduled
after the machine sleeps is caught, not only the pairs someone thought of.

## What it does not do

hyprpower owns policy, not tuning. The CPU governor, disk and USB runtime
power and radio power saving belong to TLP; hyprpower shows them and changes
none of them. It does not switch `power-profiles-daemon` profiles, drive the
keyboard backlight or an external display's brightness, or set thermal
limits.

## Install

```nix
inputs.hyprpower.url = "github:JDongian/hyprpower";
```

```nix
# NixOS: ACPI routing, the battery timer, re-applying at boot
imports = [ inputs.hyprpower.nixosModules.default ];
services.hyprpower = { enable = true; user = "you"; };
```

```nix
# home-manager: the hypridle unit and the executable
imports = [ inputs.hyprpower.homeManagerModules.default ];
programs.hyprpower.enable = true;
```

Run `hyprpower`, press `a`.

The first run copies [`hyprpower/default.toml`](hyprpower/default.toml) to
`~/.config/hyprpower/profile.toml`. Point that path at a file you keep under
version control and the TUI writes through the symlink, so a rebuilt machine
comes back with your policy:

```nix
programs.hyprpower.profileSource = "/etc/nixos/hyprpower/profile.toml";
```

## What it touches

Only `apply --system` needs root. It writes three things:

```
/etc/systemd/logind.conf.d/50-hyprpower.conf   lid, HandlePowerKey=ignore
/etc/systemd/sleep.conf.d/50-hyprpower.conf    HibernateDelaySec
<battery>/charge_control_{start,end}_threshold
```

Everything else stays in your home. Actions are a closed set, so a profile
cannot carry a command. The exception is `[lock] unit`, which hyprpower copies
into the generated config as a `systemctl` argument.

MIT.
