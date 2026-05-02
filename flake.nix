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

  outputs = { self, nixpkgs, flake-utils, uv2nix, pyproject-nix, pyproject-build-systems, ops-utils }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];

      forSystems = f: builtins.foldl' (a: s: a // { ${s} = f s; }) { } systems;

      buildFor = system:
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

          inherit ((pkgs.callPackage pyproject-nix.build.util { })) mkApplication;

          unwrapped = mkApplication {
            venv = pythonSet.mkVirtualEnv "icx-env" workspace.deps.default;
            package = pythonSet.icx-monitor;
          };
        in
        unwrapped.overrideAttrs (old: {
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

      dockerFor = system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          pkg = self.packages.${system}.default;
        in
        pkgs.dockerTools.buildImage {
          name = "icx-monitor";
          tag = "latest";
          created = "now";

          config = {
            EntryPoint = [ "${pkg}/bin/icx-server" ];
            Env = [
              "ICX_MONITOR_ROOT=/data"
            ];
            ExposedPorts = { "8080/tcp" = { }; };
            Volumes = { "/data" = { }; };
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
            pathsToLink = [ "/bin" "/share" ];
          };
        };

    in
    {
      packages = forSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          ops = ops-utils.lib.mkUtils { inherit pkgs; };
        in
        {
          default = buildFor system;
          docker = dockerFor system;
          inherit (ops) build-image push-multi-arch;
        });

      apps = forSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          ops = ops-utils.lib.mkUtils { inherit pkgs; };
          opsApps = ops-utils.lib.mkApps { inherit pkgs; } ops;
        in
        {
          inherit (opsApps) build-image push-multi-arch push-insecure;

          build-docker = {
            type = "app";
            program = toString (pkgs.writeShellScript "build-docker" ''
              set -e
              echo "Building ICX monitor container for ${system}..."
              nix build ".#packages.${system}.docker" -o result-docker
              echo "Loading into Docker..."
              docker load < result-docker
              rm result-docker
              echo "✅ Container for ${system} ready!"
            '');
          };
        });

      devShells = forSystems (system: {
        default =
          let
            pkgs = nixpkgs.legacyPackages.${system};
            workspace = uv2nix.lib.workspace.loadWorkspace {
              workspaceRoot = ./.;
            };
            pythonSet = (pkgs.callPackage pyproject-nix.build.packages {
              python = pkgs.python3;
            }).overrideScope (
              pkgs.lib.composeManyExtensions [
                pyproject-build-systems.overlays.wheel
                (workspace.mkPyprojectOverlay { sourcePreference = "wheel"; })
              ]
            );
            venv = pythonSet.mkVirtualEnv "icx-env" workspace.deps.default;
          in
          pkgs.mkShell {
            packages = [ venv pkgs.uv pkgs.net-snmp pkgs.nixfmt-rfc-style ];
            env = { UV_NO_SYNC = "1"; UV_PYTHON_DOWNLOADS = "never"; };
            shellHook = ''
              unset PYTHONPATH
              export ICX_MONITOR_ROOT="$PWD"
              echo "icx-monitor dev shell"
              echo "  commands: icx-{server,ingest,grab,parse,live}"
              echo "  snmpwalk/snmpget available"
            '';
          };
      });

      formatter = forSystems (system: nixpkgs.legacyPackages.${system}.nixfmt-rfc-style);

      # NixOS module - add to your flake imports:
      #   imports = [ icx-monitor.nixosModules.default ];
      #   services.icx-monitor.enable = true;
      nixosModules.default = import ./module.nix;

      # Overlay for nixpkgs:
      #   nixpkgs.overlays = [ icx-monitor.overlays.default ];
      overlays.default = final: prev: {
        icx-monitor = self.packages.${final.system}.default;
      };
    };
}
