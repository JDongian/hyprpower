# NixOS module: the system half of hyprpower.
#
# Everything here needs root or a system bus: routing ACPI events, polling the
# battery, and re-asserting the logind drop-in and charge thresholds at boot.
# The session half (policy file, executable, hypridle unit) is
# nix/home-manager.nix.
{ config, lib, pkgs, ... }:

let
  cfg = config.services.hyprpower;
  home = config.users.users.${cfg.user}.home;
  # These run as root, where HOME is /root, so the profile must be named
  # explicitly or they would look under /root/.config and find nothing.
  run = args: "HYPRPOWER_POLICY=${cfg.policyFile} ${cfg.exe} ${args}\n";
in
{
  options.services.hyprpower = {
    enable = lib.mkEnableOption "the hyprpower system power policy";

    user = lib.mkOption {
      type = lib.types.str;
      example = "alice";
      description = ''
        The user whose session this manages. Everything else is derived from
        it: these units run as root, where HOME is /root, so they must be
        told whose profile to read and whose hyprpower to run. One input, so
        the two cannot drift apart.
      '';
    };

    policyFile = lib.mkOption {
      type = lib.types.str;
      default = "${home}/.config/hyprpower/profile.toml";
      defaultText = "<user home>/.config/hyprpower/profile.toml";
      description = "Override only if the profile is not at the XDG default.";
    };

    exe = lib.mkOption {
      type = lib.types.str;
      default = "${home}/.local/bin/hyprpower";
      defaultText = "<user home>/.local/bin/hyprpower";
      description = ''
        Absolute, never a bare name: these units run with a minimal PATH,
        where a bare name exits 127 and the failure is silent. Override to
        use a packaged build instead of the home-manager wrapper.
      '';
    };

    pollInterval = lib.mkOption {
      type = lib.types.str;
      default = "1min";
      description = "How often to check the battery for the low-battery ladder.";
    };
  };

  config = lib.mkIf cfg.enable {
    # logind must not act on the power key. It has no power-source variant for
    # keys, so the decision lives in policy.toml [button.power] and acpid
    # routes the press to us. If both act, the button fires twice.
    services.logind.settings.Login.HandlePowerKey = "ignore";

    services.acpid = {
      enable = true;
      powerEventCommands = run "event power";
    };

    systemd.services.hibernate-on-low-battery = {
      description = "Low-battery actions from policy.toml [battery.low]";
      after = [ "multi-user.target" ];
      wantedBy = [ "multi-user.target" ];
      serviceConfig = {
        Type = "oneshot";
        ExecStart = pkgs.writeShellScript "hyprpower-low-battery"
          (run "event charge");
      };
    };

    systemd.timers.hibernate-on-low-battery = {
      description = "Check battery percentage for the low-battery actions";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnUnitActiveSec = cfg.pollInterval;
        Unit = "hibernate-on-low-battery.service";
      };
    };

    # Re-apply the system half at boot: the logind drop-in, the sleep drop-in
    # and the charge thresholds. Ordered after TLP deliberately -- TLP writes
    # charge thresholds when it starts, and policy must have the last word.
    systemd.services.hyprpower-apply = {
      description = "Apply hyprpower system policy";
      after = [ "tlp.service" "systemd-logind.service" ];
      wants = [ "tlp.service" ];
      wantedBy = [ "multi-user.target" ];
      # Skip cleanly rather than fail when there is no profile yet: this
      # runs at multi-user.target, but a seeded profile only appears at
      # user login, so the first boot of a fresh install would otherwise
      # leave a failed unit behind.
      unitConfig.ConditionPathExists = cfg.policyFile;
      serviceConfig = {
        Type = "oneshot";
        ExecStart = pkgs.writeShellScript "hyprpower-apply-system"
          (run "apply --system");
      };
    };
  };
}
