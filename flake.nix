{
  description = "hyprpower - one declarative power policy for a Hyprland laptop";

  inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
    in {
      # hyprpower reads the running system, so it needs no build-time
      # knowledge of the host and is installable anywhere.
      packages.${system} = rec {
        hyprpower = pkgs.callPackage ./nix/package.nix { };
        default = hyprpower;
      };

      # The two halves of the integration. hyprpower is only a policy engine;
      # these are what wire it to the machine, and they are versioned with the
      # code deliberately -- a checkout without them is half a system.
      nixosModules.default = import ./nix/nixos.nix;
      homeManagerModules.default = import ./nix/home-manager.nix;

      # Verification loop: `nix develop` then `python3 -m hyprpower`.
      # No nixos-rebuild involved.
      devShells.${system}.default = pkgs.mkShell {
        packages = [ (pkgs.python3.withPackages (ps: [ ps.textual ps.tomlkit ])) ];
      };
    };
}
