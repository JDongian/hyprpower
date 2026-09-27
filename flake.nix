{
  description = "hyprpower - one view of every power setting on a Linux laptop";

  inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      hyprpower = pkgs.python3Packages.buildPythonApplication {
        pname = "hyprpower";
        version = "0.1.0";
        src = ./.;
        pyproject = true;
        build-system = [ pkgs.python3Packages.setuptools ];
        dependencies = with pkgs.python3Packages; [ textual tomlkit ];
        doCheck = false;
      };
    in {
      # hyprpower reads the running system, so it needs no build-time
      # knowledge of the host and is installable anywhere.
      packages.${system} = { inherit hyprpower; default = hyprpower; };

      # Verification loop: `nix develop` then `python3 -m hyprpower`.
      # No nixos-rebuild involved.
      devShells.${system}.default = pkgs.mkShell {
        packages = [ (pkgs.python3.withPackages (ps: [ ps.textual ps.tomlkit ])) ];
      };
    };
}
