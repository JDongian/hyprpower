# hyprpower

One declarative power policy for a Hyprland laptop on NixOS.

![hyprpower](docs/screenshot.png)

## Why

The Hypr ecosystem has an idle daemon, a lock screen, a bar and a wallpaper
daemon. It has no power manager. GNOME and KDE ship one; here the pieces
exist but nothing joins them, and each covers a different part of the job:

| | owns | splits by AC / battery |
|---|---|---|
| hypridle | idle timeouts | **no** ([issue #67](https://github.com/hyprwm/hypridle/issues/67), open) |
| logind | lid, power button | only the lid |
| acpid | ACPI events | no |
| TLP | hardware tunables | every setting |

So there is nowhere to write down what the laptop should do, and nothing to
check that it does it. Some behaviours have no home at all: acting on a
battery level, escalating a suspend to hibernate, treating charge thresholds
as policy rather than as a TLP setting.

hyprpower is that layer. One file, compiled to the four places the pieces
actually read:

```
profile.toml ──▶ hypridle.conf          one listener per rung per power source
             ──▶ logind drop-in         lid, power button
             ──▶ systemd sleep drop-in  suspend → hibernate delay
             ──▶ battery sysfs          charge thresholds
```

`hyprpower verify` checks all four against the profile. Each can drift:
hypridle reads its config once at startup, logind caches its own until
reloaded, and TLP rewrites the charge thresholds when it restarts.

## The profile

```toml
[idle.dim]              [lid]
ac      = "2m"          docked  = "ignore"
battery = "1m45s"       ac      = "suspend"
                        battery = "suspend"
[idle.suspend]
ac      = false         [battery.charge]
battery = "10m"         start = 70
                        stop  = 80
[idle.hibernate]
ac      = false         [button.power]
battery = "20m"         ac      = "suspend"
                        battery = "hibernate"
```

The power source is always the leaf key. Actions are `ignore`, `lock`,
`suspend`, `hibernate`, `shutdown`; durations are `never`, `90s`, `2m30s`.

Set both `[idle.suspend]` and `[idle.hibernate]` and they compose: sleep to
RAM at the first time, to disk at the second. Nothing in userspace runs while
asleep, so systemd does that escalation on an RTC alarm.

## The TUI

`e` edits a value back into the profile, comments intact. `a` applies,
escalating once for the system half. It also reports settings that will not
do what they look like:

```
* Scheduled after the machine is already asleep, so it never runs.
      (Hibernate after = never / 20m)
* This suspend never reaches disk, so the battery can run flat.
      (Suspend after = never / 10m)
```

These are checks over the compiled listeners rather than named cases, so
any rung scheduled after the machine sleeps is reported.

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

Your profile is seeded to `~/.config/hyprpower/profile.toml` from
[`hyprpower/default.toml`](hyprpower/default.toml). Point it at a file under
version control and the TUI writes through the symlink, so a rebuilt machine
keeps your policy:

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

Everything else stays in your home. Actions are a closed set, so the profile
cannot contain a command. The exception is `[lock] unit`, which is copied
into the generated config as a `systemctl` argument.

MIT.
