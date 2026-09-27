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

```toml
[idle.dim]
ac      = "2m"
battery = "1m45s"      # the power source is always the leaf key

[idle.suspend]
ac      = false        # false means never
battery = "10m"

[idle.hibernate]
ac      = false
battery = "20m"        # with the suspend above: RAM at 10m, disk at 20m

[lid]
docked  = "ignore"     # docked wins over the two below
ac      = "suspend"
battery = "suspend"

[button.power]
ac      = "suspend"
battery = "hibernate"

[battery.charge]
start = 70             # begin charging below this
stop  = 80

[battery.low]          # charge level, not time; on battery by definition
dim           = 15
backlight_off = 6
hibernate     = 5
```

Actions are `ignore`, `lock`, `suspend`, `hibernate` and `shutdown`.
Durations are `false` or a string: `"90s"`, `"2m30s"`, `"10m"`.

Set `[idle.suspend]` and `[idle.hibernate]` together and they compose: RAM at
the first time, disk at the second. Nothing in userspace runs while the
machine is asleep, so systemd performs the escalation on an RTC alarm.

## The TUI

`e` edits a value back into the profile, comments intact. `a` applies,
escalating once for the system half. The flag list names settings that
cannot work:

```
* Scheduled after the machine is already asleep, so it never runs.
      (Hibernate after = never / 20m)
* This suspend never reaches disk, so the battery can run flat.
      (Suspend after = never / 10m)
```

Both come from checks over the compiled listeners, so any rung scheduled
after the machine sleeps is caught, not only the pairs someone thought of.

## What it does not do

hyprpower owns policy, not tuning. It does not set the CPU governor, disk or
USB runtime power, or radio power saving; TLP owns those and hyprpower only
reads them. It does not manage keyboard backlight, external display
brightness, power profiles, or thermal limits.

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
