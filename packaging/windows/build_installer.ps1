# Construit dist\DocFlex\ puis dist\DocFlex-<version>-windows-setup.exe.
# Prérequis : Python 3.12 (python.org) et Inno Setup 6 (iscc dans le PATH ou chemin par défaut).
# Usage, depuis la racine du projet :  powershell -ExecutionPolicy Bypass -File packaging\windows\build_installer.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\..")

if (-not (Test-Path ".venv")) { python -m venv .venv }
$py = ".venv\Scripts\python.exe"
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -r requirements-dev.txt

$version = & $py -c "from docflex import __version__; print(__version__)"
& $py -m PyInstaller docflex.spec --noconfirm --log-level WARN
if ($LASTEXITCODE -ne 0) { throw "PyInstaller a échoué" }

# Vérifie l'exécutable empaqueté avant de construire l'installeur.
$check = Join-Path $env:TEMP "docflex-selftest"
Remove-Item -Recurse -Force $check -ErrorAction SilentlyContinue
$proc = Start-Process "dist\DocFlex\DocFlex.exe" -ArgumentList "--selftest", $check -Wait -PassThru
Get-Content (Join-Path $check "selftest.txt")
if ($proc.ExitCode -ne 0) { throw "Autotest en échec : installeur non créé." }

$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (-not $iscc) { $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
& $iscc "/DAppVersion=$version" "packaging\windows\docflex.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup a échoué" }
Write-Host "Créé : dist\DocFlex-$version-windows-setup.exe"
