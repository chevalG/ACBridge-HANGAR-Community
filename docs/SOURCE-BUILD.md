# Source and rebuild information

This repository is the clean public source tree for ACBridge HANGAR. It does not
contain the private research repository's history, extracted game data, caches,
user profiles, captures or generated GLBs.

## Main source

- `hangar/`: HANGAR application and local service.
- `engine-src/`: reviewed ACBridge Toolkit overlay.
- `web/`: offline viewer frontend.
- `tools/`: package, installer and verification scripts.
- `third_party/nuxebridge/`: the GPL adapter used for targeted Nuxe extraction.

The portable archive includes:

- `licenses/acbridge_toolkit-0.5.1a5.tar.gz`, the frozen engine source.
- Nuxe 1.2.1 source at upstream tag
  `16d94fcd379f45721047a0c2b7b743e84b4fa08b`.
- Coremats source at `a315f5bf7569945cbbd39fb902718de765dba4c4`.
- Bex source at `bb1ade180a72465f89103880f4f5bef500e64236`.
- BinderKeys data at `b95a32c509e35e5b18306c1d37a7778ea4034f38`.

## Toolchain

The public executable is built with:

- Python 3.14.3 (Windows embeddable/full CPython runtime inputs).
- NumPy 2.5.4, SciPy 1.18.1 and Pillow 13.0.0.
- Node.js 24.14.0.
- .NET 10 SDK for the included NuxeBridge adapter and .NET 10 Desktop Runtime
  on the user machine.
- the reviewed `acbridge_toolkit-0.5.1a5` wheel and H02/H03 overlay.

NuxeBridge build:

```powershell
dotnet build .\third_party\nuxebridge\NuxeBridge.csproj -c Release
```

Nuxe 1.2.1 build, from its corresponding source:

```powershell
dotnet restore .\Nuxe.slnx
dotnet build .\Nuxe.slnx -c Release --no-restore
```

Place the resulting Nuxe/runtime dependencies, AC6 `BinderKeys` tree and
`GameConfigs/ArmoredCore6_PC.jsonc` in `release-inputs/nuxe`, then combine the
NuxeBridge build output there. This directory is a build input and is excluded
from Git.

The HANGAR package is assembled with:

```powershell
runtime\python.exe -B tools\build_h24_release.py --revision 6
```

The packaging environment must provide the runtime inputs and the audited
game-tool component directory described by the package verifier.
