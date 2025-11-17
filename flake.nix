{
  description = "Automated NS train departure notifications via Telegram";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in
    {
      packages = forAllSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        {
          default = pkgs.stdenv.mkDerivation {
            pname = "ns-commute";
            version = "0.1.0";

            src = ./.;

            buildInputs = [
              (pkgs.python3.withPackages (ps: with ps; [
                requests
                python-crontab
              ]))
            ];

            installPhase = ''
              mkdir -p $out/bin

              # Create wrapper scripts with proper Python environment
              cat > $out/bin/ns-commute-check <<'EOF'
              #!${pkgs.python3.withPackages (ps: with ps; [ requests python-crontab ])}/bin/python3
              EOF
              cat src/check_trips.py | tail -n +2 >> $out/bin/ns-commute-check

              cat > $out/bin/ns-commute-setup <<'EOF'
              #!${pkgs.python3.withPackages (ps: with ps; [ requests python-crontab ])}/bin/python3
              EOF
              cat src/setup_cron.py | tail -n +2 >> $out/bin/ns-commute-setup

              chmod +x $out/bin/ns-commute-check
              chmod +x $out/bin/ns-commute-setup
            '';

            meta = with pkgs.lib; {
              description = "Automated NS train departure notifications via Telegram";
              license = pkgs.lib.licenses.mit;
              maintainers = [ ];
            };
          };
        });

      nixosModules.default = { config, pkgs, lib, ... }: {
        imports = [ ./module.nix ];
        config = lib.mkIf config.services.ns-commute.enable {
          services.ns-commute.package = lib.mkDefault self.packages.${pkgs.system}.default;
        };
      };

      devShells = forAllSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        {
          default = pkgs.mkShell {
            buildInputs = with pkgs; [
              python3
              python3.pkgs.requests
              python3.pkgs.python-crontab
              python3.pkgs.black
            ];

            shellHook = ''
              echo "ns-commute development shell"
              echo "Use 'devenv shell' for the full devenv experience"
            '';
          };
        });
    };
}
