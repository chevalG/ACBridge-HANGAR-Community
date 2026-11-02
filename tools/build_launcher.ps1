param(
    [string]$Output = ""
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $root 'launcher'
$build = Join-Path $launcher 'build'
$webview = Join-Path $build 'webview2\unpacked\build\native'
$vcvars = 'D:\VS1\VC\Auxiliary\Build\vcvars64.bat'
$rc = 'C:\Program Files (x86)\Windows Kits\10\bin\10.0.22621.0\x64\rc.exe'
$mt = 'C:\Program Files (x86)\Windows Kits\10\bin\10.0.22621.0\x64\mt.exe'

foreach ($path in @($vcvars, $rc, $mt, (Join-Path $webview 'include\WebView2.h'),
                   (Join-Path $webview 'x64\WebView2LoaderStatic.lib'),
                   (Join-Path $root 'assets\HANGAR.ico'))) {
    if (-not (Test-Path $path)) {
        throw "Missing build dependency: $path"
    }
}
if (-not $Output) {
    $Output = Join-Path $root 'release\launcher\HANGAR.exe'
}

New-Item -ItemType Directory -Force (Split-Path -Parent $Output) | Out-Null
$obj = Join-Path $build 'HangarLauncher.obj'
$res = Join-Path $build 'HangarLauncher.res'
Copy-Item (Join-Path $root 'assets\HANGAR.ico') (Join-Path $launcher 'HANGAR.ico') -Force

$commands = @(
    ('call "' + $vcvars + '" >nul'),
    ('cd /d "' + $launcher + '"'),
    ('cl /nologo /utf-8 /O2 /EHsc /W4 /WX /std:c++17 /DUNICODE /D_UNICODE /I"' +
        (Join-Path $webview 'include') + '" /c HangarLauncher.cpp /Fo"' + $obj + '"'),
    ('"' + $rc + '" /nologo /fo "' + $res + '" HangarLauncher.rc'),
    ('link /nologo /SUBSYSTEM:WINDOWS /OUT:"' + $Output + '" "' + $obj + '" "' + $res +
        '" "' + (Join-Path $webview 'x64\WebView2LoaderStatic.lib') +
        '" user32.lib gdi32.lib shell32.lib ole32.lib oleaut32.lib advapi32.lib shlwapi.lib')
)
$cmd = $commands -join " && "
cmd.exe /d /s /c $cmd
if ($LASTEXITCODE -ne 0) {
    throw "Launcher build failed with exit code $LASTEXITCODE"
}
& $mt -nologo -manifest (Join-Path $launcher 'HangarLauncher.manifest') -outputresource:"$Output;1"
if ($LASTEXITCODE -ne 0) {
    throw "Manifest embedding failed with exit code $LASTEXITCODE"
}
Write-Host ("Built " + $Output + " (" + (Get-Item $Output).Length + " bytes)")
