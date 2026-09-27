# AUR packaging

The Nix flake is the tested install path. This PKGBUILD has never been built:
it was written on NixOS, where `makepkg` does not exist.

Before submitting:

1. `makepkg -si` on Arch, and fix whatever breaks.
2. Re-run `updpkgsums` if you retag.
3. Check `hyprpower` starts and `hyprpower verify` reports sensibly.

Note what the package does **not** install: the hypridle unit, the acpid
hook, the battery timer and the boot-time apply. Those are the integration,
and on Arch they have no equivalent of `nix/nixos.nix` yet — a packaged
install gives you the TUI and `verify`, not a working ladder. Read
`nix/nixos.nix` and `nix/home-manager.nix` for what still needs wiring.
