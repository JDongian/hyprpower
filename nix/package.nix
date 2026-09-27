{ lib, python3Packages }:

python3Packages.buildPythonApplication {
  pname = "hyprpower";
  version = "0.1.0";
  src = ../.;
  pyproject = true;
  build-system = [ python3Packages.setuptools ];
  dependencies = with python3Packages; [ textual tomlkit ];
  doCheck = false;

  meta = {
    description = "One declarative power policy for a Hyprland laptop";
    license = lib.licenses.mit;
    mainProgram = "hyprpower";
  };
}
