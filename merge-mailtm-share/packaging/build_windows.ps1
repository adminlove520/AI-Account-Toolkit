param(
    [string]$DistName = "merge-mailtm",
    [string]$VenvDir = ".venv-build-windows",
    [string]$PythonExe = "py",
    [string]$PythonVersion = "-3.13"
)

$ErrorActionPreference = "Stop"
$RootDir = Split-Path -Parent $PSScriptRoot
Set-Location $RootDir

$DistDir = Join-Path $RootDir "dist"
$BuildDir = Join-Path $RootDir "build\pyinstaller-windows"
$SpecDir = Join-Path $RootDir "build\pyinstaller-windows-spec"

& $PythonExe $PythonVersion -m venv $VenvDir
$VenvPython = Join-Path $RootDir "$VenvDir\Scripts\python.exe"

& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r requirements.txt "pyinstaller>=6.0"

if (Test-Path (Join-Path $DistDir $DistName)) {
    Remove-Item -Recurse -Force (Join-Path $DistDir $DistName)
}

& $VenvPython -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --console `
    --distpath $DistDir `
    --workpath $BuildDir `
    --specpath $SpecDir `
    --name $DistName `
    --hidden-import chatgpt_register_old `
    --collect-all curl_cffi `
    auto_pool_maintainer_mailtm.py

New-Item -ItemType Directory -Force -Path (Join-Path $DistDir "logs") | Out-Null
Copy-Item (Join-Path $RootDir "packaging\\run_windows.cmd") (Join-Path $DistDir ($DistName + ".cmd")) -Force

$ConfigPath = Join-Path $RootDir "config.json"
if (Test-Path $ConfigPath) {
    Copy-Item $ConfigPath (Join-Path $DistDir "config.json") -Force
}

Write-Host "Windows 打包完成: $(Join-Path $DistDir ($DistName + '.exe'))"
