{ config, lib, pkgs, ... }:

with lib;

let
  cfg = config.services.icx-monitor;
  pkg = cfg.package;
in {
  options.services.icx-monitor = {
    enable = mkEnableOption "ICX switch monitor service";

    package = mkOption {
      type = types.package;
      default = pkg;
      defaultText = literalExpression "pkgs.icx-monitor";
      description = "The icx-monitor package to use.";
    };

    switch = {
      host = mkOption {
        type = types.str;
        example = "192.168.1.1";
        description = "Switch IP address or hostname.";
      };
      username = mkOption {
        type = types.str;
        example = "admin";
        description = "SSH username for switch access.";
      };
      sshKeyFile = mkOption {
        type = types.nullOr types.path;
        default = null;
        description = "Path to SSH private key for switch authentication. If null, password auth is used.";
      };
      sshPasswordFile = mkOption {
        type = types.nullOr types.path;
        default = null;
        description = "Path to file containing SSH password (alternative to key-based auth).";
      };
    };

    snmpCommunityFile = mkOption {
      type = types.nullOr types.path;
      default = null;
      description = "Path to file containing SNMP read community string for live polling.";
    };

    port = mkOption {
      type = types.port;
      default = 8080;
      description = "HTTP port for the web UI.";
    };

    dataDir = mkOption {
      type = types.path;
      default = "/var/lib/icx-monitor";
      description = "Runtime data directory (switch data, live metrics, config).";
    };

    openFirewall = mkOption {
      type = types.bool;
      default = false;
      description = "Open the configured port in the firewall.";
    };
  };

  config = mkIf cfg.enable {
    environment.systemPackages = [ pkg ];

    networking.firewall.allowedTCPPorts = mkIf cfg.openFirewall [ cfg.port ];

    systemd.services.icx-monitor = {
      description = "ICX Switch Monitor";
      after = [ "network.target" ];
      wantedBy = [ "multi-user.target" ];

      preStart = ''
        mkdir -p ${cfg.dataDir}/data
        cp -r ${pkg}/share/icx-monitor/static ${cfg.dataDir}/static
        chmod -R u+w ${cfg.dataDir}
      '';

      serviceConfig = {
        ExecStart = "${lib.getExe pkg}";
        Restart = "always";
        RestartSec = "5";
        DynamicUser = true;
        StateDirectory = "icx-monitor";
        WorkingDirectory = cfg.dataDir;
        Environment = [
          "ICX_MONITOR_ROOT=${cfg.dataDir}"
          "ICX_SWITCH_HOST=${cfg.switch.host}"
          "ICX_SWITCH_USER=${cfg.switch.username}"
          "ICX_SERVER_PORT=${toString cfg.port}"
        ]
        ++ optional (cfg.switch.sshKeyFile != null) "ICX_SSH_KEY=${cfg.switch.sshKeyFile}"
        ++ optional (cfg.snmpCommunityFile != null) "ICX_MONITOR_ROOT=${cfg.dataDir}";

        # Mount secrets
        LoadCredential = optional (cfg.switch.sshKeyFile != null) "ssh-key:${cfg.switch.sshKeyFile}"
          ++ optional (cfg.switch.sshPasswordFile != null) "ssh-password:${cfg.switch.sshPasswordFile}"
          ++ optional (cfg.snmpCommunityFile != null) "snmp-community:${cfg.snmpCommunityFile}";

        # Hardening
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        PrivateDevices = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectControlGroups = true;
        MemoryDenyWriteExecute = true;
        RestrictAddressFamilies = [ "AF_INET" "AF_INET6" "AF_UNIX" ];
        RestrictNamespaces = true;
        LockPersonality = true;
      };
    };

    systemd.tmpfiles.rules = [
      "d ${cfg.dataDir} 0755 icx-monitor icx-monitor -"
      "d ${cfg.dataDir}/data 0755 icx-monitor icx-monitor -"
    ];
  };
}
