{
  description = "papermint - multi-project Mintlify documentation repo";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
        python = pkgs.python313;
        pythonPkgs = python.pkgs;
      in
      {
        devShells.default = pkgs.mkShell {
          packages = [
            # Node.js for Mintlify
            pkgs.nodejs_22

            # Python with search API dependencies
            (python.withPackages (ps: [
              ps.fastapi
              ps.uvicorn
              ps.httpx
              ps.numpy
            ]))
          ];

          shellHook = ''
            echo "papermint dev shell"
            echo "  mintlify: cd <project> && npx mint dev"
            echo "  search:   cd search && PROJECT=<project> python server.py"
          '';
        };
      });
}
