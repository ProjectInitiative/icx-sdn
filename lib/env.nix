{ pkgs, lib, uv2nix, pyproject-nix, pyproject-build-systems, workspaceRoot, static-src }:

let
  workspace = uv2nix.lib.workspace.loadWorkspace {
    inherit workspaceRoot;
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

  unwrapped = mkApplication {
    inherit venv;
    package = pythonSet.icx-monitor;
  };

  pkg = unwrapped.overrideAttrs (old: {
    nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [ pkgs.makeWrapper ];

    installPhase = (old.installPhase or "") + ''
      mkdir -p $out/share/icx-monitor
      cp -r ${static-src} $out/share/icx-monitor/static
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

in
{
  inherit pythonSet venv;
  inherit pkg;
}
