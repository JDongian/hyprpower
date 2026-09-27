# Design

## The one idea

Every power behaviour on this laptop is the same shape:

> **on TRIGGER, if on AC or on battery, do ACTION**

| | trigger | condition | action | who runs it today |
|---|---|---|---|---|
| | idle 150s | — | dim | hypridle |
| | lid close | on battery | suspend-then-hibernate | logind |
| | power button | on AC | suspend | acpid |
| | charge ≤ 4% | on battery | hibernate | systemd timer |

Four daemons, four config languages, one concept — and the policy is
scattered across all four. `config/policy.toml` holds the table; the daemons
become dispatchers that own no decisions.

## Division of labour

**Nix installs and routes. hyprpower decides.**

| | Nix | hyprpower |
|---|---|---|
| packages, service enables | ✓ | |
| swap, resume device, fprintd sleep hooks, udev | ✓ (hardware, not policy) | |
| TLP settings | ✓ (TLP owns hardware power) | reads only |
| event routing (one-line acpid hook, one timer) | ✓ | |
| every timeout, threshold and action | | ✓ |

The test for whether something belongs in Nix: *would changing it alter what
the machine does when you walk away?* If yes, it is policy and lives in
`policy.toml`.

## Dispatch

| policy section | mechanism | why that one |
|---|---|---|
| `[idle.*]` | generated `hypridle.conf`, one listener per rung per source, split with `condition_cmd` | hypridle is the only thing watching idle; `condition_retry` re-checks while idle, so unplugging mid-idle is handled natively |
| `[lid]` | logind drop-in | logind splits the lid natively and integrates with inhibitors |
| `[button.power]` | acpid → `hyprpower event power` | logind has no power-source variant for keys; Windows has one, logind does not |
| `[charge]` | systemd timer → `hyprpower event charge` | must keep running with no session |

Generated artefacts, never hand-edited:

```
~/.local/state/hyprpower/hypridle.conf
~/.config/systemd/user/hypridle.service.d/hyprpower.conf   # points hypridle at it
/etc/systemd/logind.conf.d/50-hyprpower.conf               # lid + HandlePowerKey=ignore
```

`~/.config/systemd/user/` is a real writable directory (home-manager only
drops unit symlinks into it), so the hypridle override needs no Nix change at
all. That removes what was previously the riskiest step in the plan.

## Code

```
hyprpower/
  probe.py    read the live system            (done)
  config.py   load policy + presets, parse durations
  view.py     merge policy + probe + presets -> display model
  apply.py    generate the three artefacts, reload the daemons
  event.py    handle power / lid / charge events per policy
  tui.py      four tabs                       (done)
  cli.py      tui | show | apply | event <kind> | capture
```

`view.py` gains a real job it could not have before: **declared vs live.**
Until now there was nothing to compare the system against, so drift was
meaningless and was cut. With `policy.toml` there is a declared value for
every row, so the display can show policy, live, and whether they agree.

## Behaviour changes when policy.toml is first applied

| | today | after |
|---|---|---|
| dim | raw `10` = 0.04% at 2m30s (a blank screen, not a dim) | a real 10% dim at 1m45s battery / 4m AC |
| backlight off | never (the "dim" was doing it) | 2m battery / 5m AC |
| lock | 5m both | 2m battery / 5m AC |
| idle suspend | never | 10m battery, still never on AC |
| lid, charge thresholds | — | unchanged |

## Cost of this design

Reproducibility moves. `nixos-rebuild` on a fresh machine reproduces
plumbing, not behaviour; behaviour comes from `policy.toml` plus
`hyprpower apply`. That is only equivalent to today if the config is
git-tracked and apply is idempotent — both are required, not optional.

`/etc/systemd/logind.conf.d/50-hyprpower.conf` is imperative state outside
the store. Nix will neither create nor remove it. Nix must therefore not also
set logind lid/power-key values, or the two will fight silently.
