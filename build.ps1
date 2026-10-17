# Builds Zhuzi for one or both Windows architectures.
#
#   .\build.ps1                 # both arm64 and x64
#   .\build.ps1 -Arch arm64     # just one
#
# Each build lands in dist\Zhuzi-<arch>\ (a portable folder: the `portable`
# marker keeps settings and models beside the exe) and is zipped to
# dist\Zhuzi-<arch>-portable.zip. The installer is made from the same folder,
# minus the marker, by installer\zhuzi.iss (see the GitHub workflow).
#
# Environments (first time): .venv-arm64 from .python\arm64, .venv-x64 from an
# x64 Python 3.12, each with `pip install -r requirements.txt`.
param([ValidateSet("arm64", "x64", "both")] [string] $Arch = "both")

$here = $PSScriptRoot
$archs = if ($Arch -eq "both") { @("arm64", "x64") } else { @($Arch) }

foreach ($a in $archs) {
    $py = Join-Path $here ".venv-$a\Scripts\python.exe"
    if (-not (Test-Path $py)) { throw "missing $py" }
    $work = Join-Path $here "build\$a"
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $work "pyi-config"  # keep PyInstaller's cache in the project

    & $py (Join-Path $here "assets\make_icon.py")
    if ($LASTEXITCODE) { throw "icon generation failed" }

    $assets = Join-Path $here "assets"
    & $py -m PyInstaller --noconfirm --clean --windowed `
        --name "Zhuzi" `
        --icon (Join-Path $assets "icon.ico") `
        --add-data "$(Join-Path $assets 'icon.ico');assets" `
        --add-data "$(Join-Path $assets 'icon.png');assets" `
        --add-data "$(Join-Path $assets 'silero_vad_v6.onnx');assets" `
        --paths (Join-Path $here "src") `
        --collect-all sherpa_onnx `
        --exclude-module tkinter `
        --exclude-module PIL `
        --distpath (Join-Path $work "dist") `
        --workpath (Join-Path $work "work") `
        --specpath $work `
        (Join-Path $here "src\app.py")
    if ($LASTEXITCODE) { throw "PyInstaller failed for $a" }

    # Drop what a widgets app never loads: the software OpenGL renderer, Qt's
    # network plugins, and every Qt translation but Simplified Chinese.
    $qt = Join-Path $work "dist\Zhuzi\_internal\PySide6"
    Remove-Item (Join-Path $qt "opengl32sw.dll") -ErrorAction SilentlyContinue
    foreach ($plugin in "networkinformation", "tls") { Remove-Item (Join-Path $qt "plugins\$plugin") -Recurse -ErrorAction SilentlyContinue }
    Get-ChildItem (Join-Path $qt "translations") -File | Where-Object { $_.Name -notmatch '_zh_CN\.qm$' } | Remove-Item

    # No version in the names: README links to releases/latest/download/<name>, which must stay stable.
    $out = Join-Path $here "dist\Zhuzi-$a"
    # Someone may be using this portable folder: keep its data (keys, models) across builds.
    $kept = Join-Path $work "kept-data"
    if (Test-Path $kept) { Remove-Item $kept -Recurse -Force }
    if (Test-Path (Join-Path $out "data")) { Move-Item (Join-Path $out "data") $kept }
    if (Test-Path $out) { Remove-Item $out -Recurse -Force }
    New-Item -ItemType Directory -Force (Split-Path $out) | Out-Null
    Move-Item (Join-Path $work "dist\Zhuzi") $out
    Copy-Item (Join-Path $here "LICENSE"), (Join-Path $here "THIRD_PARTY_NOTICES.md") $out
    Set-Content -Path (Join-Path $out "portable") -Value "Settings, models and transcripts stay next to this folder. Delete this file to use %LOCALAPPDATA%\Zhuzi instead." -Encoding utf8

    $zip = "$out-portable.zip"
    if (Test-Path $zip) { Remove-Item $zip -Force }
    Compress-Archive -Path $out -DestinationPath $zip -CompressionLevel Optimal  # before data returns: never ship keys
    if (Test-Path $kept) { Move-Item $kept (Join-Path $out "data") }
    $mb = [math]::Round(((Get-ChildItem $out -Recurse -File | Measure-Object Length -Sum).Sum / 1MB))
    $zmb = [math]::Round((Get-Item $zip).Length / 1MB)
    Write-Host "Built $out ($mb MB) and $zip ($zmb MB)"
}
