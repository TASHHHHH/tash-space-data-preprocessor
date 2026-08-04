param(
    [switch]$SkipInstaller,
    [switch]$NoClean
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DistPath = Join-Path $ProjectRoot "dist"
$BuildPath = Join-Path $ProjectRoot "build"
$ReleasePath = Join-Path $ProjectRoot "release"
$SpecPath = Join-Path $PSScriptRoot "TSDP.spec"
$Version = "3.2.0"

New-Item -ItemType Directory -Path $ReleasePath -Force | Out-Null
if (-not $NoClean) {
    if (Test-Path -LiteralPath $DistPath) {
        Remove-Item -LiteralPath $DistPath -Recurse -Force
    }
    if (Test-Path -LiteralPath $BuildPath) {
        Remove-Item -LiteralPath $BuildPath -Recurse -Force
    }
}

python -m PyInstaller --noconfirm --clean --distpath $DistPath --workpath $BuildPath $SpecPath
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE."
}

$Executable = Join-Path $DistPath "TSDP\TSDP.exe"
if (-not (Test-Path -LiteralPath $Executable)) {
    throw "Packaged executable was not created: $Executable"
}

$Aria2Source = Join-Path $ProjectRoot "vendor\aria2c.exe"
if (Test-Path -LiteralPath $Aria2Source) {
    Copy-Item -LiteralPath $Aria2Source -Destination (Join-Path $DistPath "TSDP\aria2c.exe") -Force
}
$Aria2License = Join-Path $ProjectRoot "vendor\aria2-COPYING.txt"
if (Test-Path -LiteralPath $Aria2License) {
    $LicenseFolder = Join-Path $DistPath "TSDP\licenses"
    New-Item -ItemType Directory -Path $LicenseFolder -Force | Out-Null
    Copy-Item -LiteralPath $Aria2License -Destination (Join-Path $LicenseFolder "aria2-COPYING.txt") -Force
}
$PortableZip = Join-Path $ReleasePath "TSDP-$Version-portable.zip"
if (Test-Path -LiteralPath $PortableZip) {
    Remove-Item -LiteralPath $PortableZip -Force
}
Compress-Archive -LiteralPath (Join-Path $DistPath "TSDP") -DestinationPath $PortableZip -CompressionLevel Optimal

if (-not $SkipInstaller) {
    $ProgramFilesX86 = [Environment]::GetFolderPath("ProgramFilesX86")
    $ProgramFiles64 = [Environment]::GetFolderPath("ProgramFiles")
    $Candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
        (Join-Path $ProgramFilesX86 "Inno Setup 6\ISCC.exe"),
        (Join-Path $ProgramFiles64 "Inno Setup 6\ISCC.exe")
    )
    $Compiler = $Candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
    if (-not $Compiler) {
        throw "Inno Setup 6 was not found. Install it, then rerun this script."
    }
    & $Compiler (Join-Path $PSScriptRoot "installer.iss")
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup failed with exit code $LASTEXITCODE."
    }
}

$Files = Get-ChildItem -LiteralPath $ReleasePath -File | Where-Object {
    $_.Extension -in ".zip", ".exe"
}
$HashLines = foreach ($File in $Files) {
    $Hash = (Get-FileHash -LiteralPath $File.FullName -Algorithm SHA256).Hash
    "$Hash  $($File.Name)"
}
$HashLines | Set-Content -LiteralPath (Join-Path $ReleasePath "SHA256SUMS.txt") -Encoding ascii

Write-Host ""
Write-Host "Release ready:"
Get-ChildItem -LiteralPath $ReleasePath -File | Select-Object Name, Length, LastWriteTime
