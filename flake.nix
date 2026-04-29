{
  description = "Brocade ICX switch monitor";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
    };
    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, flake-utils, uv2nix, pyproject-nix, pyproject-build-systems }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        inherit (pkgs) lib;

        workspace = uv2nix.lib.workspace.loadWorkspace {
          workspaceRoot = ./.;
        };

        python = pkgs.python3;

        pythonBase = pkgs.callPackage pyproject-nix.build.packages {
          inherit python;
        };

        uvOverlay = workspace.mkPyprojectOverlay {
          sourcePreference = "wheel";
        };

        pythonSet = pythonBase.overrideScope (
          lib.composeManyExtensions [
            pyproject-build-systems.overlays.wheel
            uvOverlay
          ]
        );

        venv = pythonSet.mkVirtualEnv "icx-env" workspace.deps.default;

        inherit ((pkgs.callPackage pyproject-nix.build.util { })) mkApplication;

        app = mkApplication {
          venv = pythonSet.mkVirtualEnv "icx-env" workspace.deps.default;
          package = pythonSet.icx-monitor;
        };

      in
      {
        packages.default = app.overrideAttrs (old: {
          nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [ pkgs.makeWrapper ];

          installPhase = (old.installPhase or "") + ''
            mkdir -p $out/share/icx-monitor
            cp -r ${./static} $out/share/icx-monitor/static
            mkdir -p $out/share/icx-monitor/data

            for bin in icx-server icx-live; do
              if [ -f "$out/bin/$bin" ]; then
                wrapProgram "$out/bin/$bin" \
                  --set-default ICX_MONITOR_ROOT "$out/share/icx-monitor" \
                  --prefix PATH : ${lib.makeBinPath [ pkgs.net-snmp ]}
              fi
            done
            for bin in icx-ingest icx-grab icx-parse; do
              if [ -f "$out/bin/$bin" ]; then
                wrapProgram "$out/bin/$bin" \
                  --set-default ICX_MONITOR_ROOT "$out/share/icx-monitor"
              fi
            done
          '';

          meta = {
            description = "Brocade ICX switch monitor with web UI";
            license = lib.licenses.mit;
            platforms = lib.platforms.linux;
            mainProgram = "icx-server";
          };
        });

        devShells.default = pkgs.mkShell {
          packages = [
            venv
            pkgs.uv
            pkgs.net-snmp
            pkgs.nixfmt-rfc-style
          ];

          env = {
            UV_NO_SYNC = "1";
            UV_PYTHON_DOWNLOADS = "never";
          };

          shellHook = ''
            unset PYTHONPATH
            export ICX_MONITOR_ROOT="$PWD"
            echo "icx-monitor dev shell"
            echo "  commands: icx-{server,ingest,grab,parse,live}"
            echo "  snmpwalk/snmpget available"
          '';
        };

        formatter = pkgs.nixfmt-rfc-style;
      }
    );
}
