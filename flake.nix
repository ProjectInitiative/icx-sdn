{
  description = "ICX switch monitor - SSH scraping, SNMP live polling, web UI";

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
    ops-utils.url = "github:projectinitiative/ops-utils";
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
      uv2nix,
      pyproject-nix,
      pyproject-build-systems,
      ops-utils,
    }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forSystems = f: builtins.foldl' (a: s: a // { ${s} = f s; }) { } systems;

      mkPkg =
        system:
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

          pythonSet = pythonBase.overrideScope (
            lib.composeManyExtensions [
              pyproject-build-systems.overlays.wheel
              (workspace.mkPyprojectOverlay { sourcePreference = "wheel"; })
            ]
          );

          venv = pythonSet.mkVirtualEnv "icx-env" workspace.deps.default;

          inherit ((pkgs.callPackage pyproject-nix.build.util { })) mkApplication;

          app = mkApplication {
            inherit venv;
            package = pythonSet.icx-monitor;
          };

          pkg = app.overrideAttrs (old: {
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
              description = "ICX switch monitor with web UI";
              license = lib.licenses.mit;
              platforms = lib.platforms.linux;
              mainProgram = "icx-server";
            };
          });

          docker = pkgs.dockerTools.buildImage {
            name = "icx-monitor";
            tag = "latest";
            created = "now";

            config = {
              EntryPoint = [ "${pkg}/bin/icx-server" ];
              Env = [ "ICX_MONITOR_ROOT=/data" ];
              ExposedPorts = {
                "8080/tcp" = { };
              };
              Volumes = {
                "/data" = { };
              };
              WorkingDir = "/data";
              User = "1000";
            };

            copyToRoot = pkgs.buildEnv {
              name = "image-root";
              paths = [
                pkg
                pkgs.net-snmp
                pkgs.bash
                pkgs.coreutils
              ];
              pathsToLink = [
                "/bin"
                "/share"
              ];
            };
          };

          ops = ops-utils.lib.mkUtils { inherit pkgs; };
          opsApps = ops-utils.lib.mkApps { inherit pkgs; } ops;

        in
        {
          inherit
            pkgs
            lib
            venv
            pkg
            docker
            ops
            opsApps
            ;
        };

    in
    {
      packages = forSystems (
        system:
        let
          m = mkPkg system;
          agentCheck = m.pkgs.writeShellScriptBin "agent-check" ''
            set -euo pipefail
            echo "=== Agent Pre-Submission Check ==="
            echo "1. Checking working tree..."
            if [ -n "$(git status --porcelain)" ]; then
              echo "ERROR: Working tree is dirty. Commit all changes first."
              exit 1
            fi
            echo "2. Checking formatting..."
            nixfmt --check flake.nix module.nix
            echo "3. Running hermeticsandbox build..."
            nix build
            echo "=== All checks passed ==="
          '';
        in
        {
          default = m.pkg;
          docker = m.docker;
          agent-check = agentCheck;
          inherit (m.ops) build-image push-multi-arch;
        }
      );

      apps = forSystems (
        system:
        let
          m = mkPkg system;
        in
        {
          inherit (m.opsApps) build-image push-multi-arch push-insecure;

          build-docker = {
            type = "app";
            program = toString (
              m.pkgs.writeShellScript "build-docker" ''
                set -e
                echo "Building ICX monitor container for ${system}..."
                nix build ".#packages.${system}.docker" -o result-docker
                echo "Loading into Docker..."
                docker load < result-docker
                rm result-docker
                echo "✅ Container for ${system} ready!"
              ''
            );
          };
        }
      );

      devShells = forSystems (
        system:
        let
          m = mkPkg system;
        in
        {
          default = m.pkgs.mkShell {
            packages = [
              m.pkg
              m.pkgs.uv
              m.pkgs.net-snmp
              m.pkgs.nixfmt
              m.pkgs.ruff
              m.pkgs.docker
              m.pkgs.docker-client
            ];
            env = {
              UV_NO_SYNC = "1";
              UV_PYTHON_DOWNLOADS = "never";
            };
            shellHook = ''
              unset PYTHONPATH
              export ICX_MONITOR_ROOT="$PWD"
              export PATH="${m.venv}/bin:$PATH"
              echo "icx-monitor dev shell"
              echo "  commands: icx-{server,ingest,grab,parse,live}"
              echo "  container: nix build .#docker, nix run .#build-docker"
              echo "  publish:   nix run .#push-multi-arch -- docker icx-monitor ghcr.io/owner"
              echo "  snmpwalk/snmpget available"
            '';
          };
        }
      );

      checks = forSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          m = mkPkg system;
        in
        {
          formatting =
            pkgs.runCommand "check-formatting"
              {
                nativeBuildInputs = with pkgs; [
                  nixfmt
                  ruff
                ];
                src = ./.;
              }
              ''
                cp -r $src/. .
                chmod -R +w .
                export XDG_CACHE_HOME=$TMPDIR
                nixfmt --check flake.nix module.nix
                RUFF_CACHE_DIR="$TMPDIR" ruff format --check src/
                touch $out
              '';
        }
      );

      formatter = forSystems (system: nixpkgs.legacyPackages.${system}.nixfmt);

      nixosModules.default = import ./module.nix;

      overlays.default = final: prev: {
        icx-monitor = self.packages.${final.system}.default;
      };
    };
}
