{
  description = "thinkpower - one view of every power setting on a Linux laptop";

  inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      thinkpower = pkgs.python3Packages.buildPythonApplication {
        pname = "thinkpower";
        version = "0.1.0";
        src = ./.;
        pyproject = true;
        build-system = [ pkgs.python3Packages.setuptools ];
        dependencies = with pkgs.python3Packages; [ textual tomlkit ];
        doCheck = false;
      };
    in {
      # thinkpower reads the running system, so it needs no build-time
      # knowledge of the host and is installable anywhere.
      packages.${system} = { inherit thinkpower; default = thinkpower; };

      # Verification loop: `nix develop` then `python3 -m thinkpower`.
      # No nixos-rebuild involved.
      devShells.${system}.default = pkgs.mkShell {
        packages = [ (pkgs.python3.withPackages (ps: [ ps.textual ps.tomlkit ])) ];
      };
    };
}
