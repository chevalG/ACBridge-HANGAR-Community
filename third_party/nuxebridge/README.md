# NuxeBridge

NuxeBridge is the small ACBridge adapter between HANGAR's request JSON and
Nuke's public `UnpackOperation` API. It performs targeted, read-only extraction
and refuses an output directory that overlaps the selected game directory.

License: GPL-3.0-only.

Build with the .NET 10 SDK:

```powershell
dotnet build .\NuxeBridge.csproj -c Release
```
