# Windows installer for ic705suite: installs, upgrades and uninstalls the
# self-contained executable published with each release, for the current user
# (no administrator rights). Needs no Python. Windows PowerShell 5.1 or newer.
#
#   .\install.ps1                      install the ic705suite.exe next to this script,
#                                      or download the latest release
#   .\install.ps1 -Upgrade             download and install the latest release
#   .\install.ps1 -Uninstall           remove it; settings and data stay
#   .\install.ps1 -Uninstall -Purge    remove it along with its settings and data
#
# Straight from the web:
#   irm https://github.com/manuel-alcocer/ic705suite/releases/latest/download/ic705suite-install.ps1 | iex
#
# The file is plain ASCII on purpose: "irm | iex" and Windows PowerShell 5.1 do not agree on
# the encoding of a script, so the accents of the Spanish messages are written as \uXXXX.

[CmdletBinding()]
param(
    [switch]$Upgrade,
    [switch]$Uninstall,
    [switch]$Purge,
    [switch]$Status,
    [string]$Version = "",
    [string]$InstallDir = "",
    [switch]$Force,
    [switch]$Yes,
    [switch]$Help
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2

$App = "ic705suite"
$Platform = "windows-x86_64"
$ReleasesUrl = if ($env:IC705SUITE_RELEASES_URL) { $env:IC705SUITE_RELEASES_URL } `
               else { "https://github.com/manuel-alcocer/ic705suite/releases" }

# Messages follow IC705SUITE_LANG or the Windows display language, like the suite does.
$Lang = if ($env:IC705SUITE_LANG) { $env:IC705SUITE_LANG } else { (Get-UICulture).Name }
$Spanish = $Lang -like "es*"

# Only the \uXXXX escapes are turned into letters; paths keep their backslashes.
function ConvertFrom-Escapes([string]$Text) {
    return [regex]::Replace($Text, '\\u([0-9a-fA-F]{4})',
        { param($m) [string][char][Convert]::ToInt32($m.Groups[1].Value, 16) })
}

# The English or the Spanish text, with {0}, {1}... filled from the remaining arguments.
function T([string]$English, [string]$Es) {
    $text = if ($Spanish) { ConvertFrom-Escapes $Es } else { $English }
    if ($args.Count) { return $text -f $args }
    return $text
}
function Info([string]$Text) { Write-Host "==> " -ForegroundColor Blue -NoNewline; Write-Host $Text }
function Warn([string]$Text) { Write-Host "==> " -ForegroundColor Yellow -NoNewline; Write-Host $Text }

# Stops the installer. Not "exit": run through "irm | iex" that would close the window.
$script:ExitCode = 0
function Die([string]$Text) {
    Write-Host "==> " -ForegroundColor Red -NoNewline; Write-Host $Text
    $script:ExitCode = 1
    throw [System.OperationCanceledException]::new($Text)
}

function Show-Usage {
    if ($Spanish) {
        ConvertFrom-Escapes @'
Uso: install.ps1 [opciones]

  (sin opciones)      Instala el ic705suite.exe que acompa\u00f1a al script o, si no
                      hay ninguno, descarga la \u00faltima versi\u00f3n. Si ya hay una
                      instalada m\u00e1s antigua, la actualiza.
  -Upgrade            Descarga la \u00faltima versi\u00f3n y actualiza la instalada.
  -Uninstall          Desinstala ic705suite. La configuraci\u00f3n y los datos se quedan.
  -Purge              Desinstala y borra tambi\u00e9n la configuraci\u00f3n y los datos
                      de la suite (el diario de hamrlog no se toca).
  -Status             Muestra la versi\u00f3n instalada.
  -Version X.Y.Z      Descarga esa versi\u00f3n en lugar de la \u00faltima.
  -InstallDir DIR     Instala en DIR (por defecto %LOCALAPPDATA%\Programs\ic705suite).
  -Force              Reinstala la misma versi\u00f3n o permite volver a una anterior.
  -Yes                No pide confirmaci\u00f3n antes de borrar datos.
  -Help               Muestra esta ayuda.
'@ | Write-Host
    } else {
        @'
Usage: install.ps1 [options]

  (no options)        Install the ic705suite.exe shipped with this script or, when
                      there is none, download the latest release. An older
                      installed version is upgraded.
  -Upgrade            Download the latest release and upgrade to it.
  -Uninstall          Uninstall ic705suite. Its settings and data stay.
  -Purge              Uninstall and delete the suite's settings and data as
                      well (hamrlog's logbook is left alone).
  -Status             Show the installed version.
  -Version X.Y.Z      Download that version instead of the latest.
  -InstallDir DIR     Install in DIR (default %LOCALAPPDATA%\Programs\ic705suite).
  -Force              Reinstall the same version or go back to an older one.
  -Yes                Do not ask before deleting data.
  -Help               Show this help.
'@ | Write-Host
    }
}

if ($Purge) { $Uninstall = $true }
$Version = $Version.TrimStart("v")

if (-not $InstallDir) { $InstallDir = Join-Path $env:LOCALAPPDATA "Programs\$App" }
$Exe = Join-Path $InstallDir "$App.exe"
# What this installer put on disk, so uninstalling removes exactly that.
$VersionFile = Join-Path $InstallDir "version.txt"
$Shortcut = Join-Path ([Environment]::GetFolderPath("Programs")) "$App.lnk"

# The same folders as ic705suite.paths, for -Purge. hamrlog's logbook lives in hamrlog's
# own folder, shared with a standalone hamrlog, and is not touched.
$ConfigDir = Join-Path $env:APPDATA $App
$DataDir = Join-Path $env:LOCALAPPDATA $App

# An ic705suite.exe next to the script means the script came inside a release zip.
$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { "" }

function Get-InstalledVersion {
    if (Test-Path -LiteralPath $VersionFile) { return (Get-Content -LiteralPath $VersionFile -Raw).Trim() }
    return ""
}

# "ic705suite 0.2.0" -> "0.2.0"; empty when the executable does not run here.
function Get-VersionOf([string]$Path) {
    try {
        $out = & $Path --version 2>$null
        if ($LASTEXITCODE -eq 0 -and $out -match "^$App (\S+)") { return $Matches[1] }
    } catch { }
    return ""
}

function Test-Older([string]$A, [string]$B) {
    try { return [version]$A -lt [version]$B } catch { return $A -ne $B -and $A -lt $B }
}

function Get-UserPath {
    $key = Get-Item -LiteralPath "HKCU:\Environment"
    return [string]$key.GetValue("Path", "", "DoNotExpandEnvironmentNames")
}

# The user's PATH keeps its kind (REG_EXPAND_SZ) so entries like %USERPROFILE%\bin survive.
function Set-UserPath([string]$Value) {
    Set-ItemProperty -LiteralPath "HKCU:\Environment" -Name "Path" -Value $Value -Type ExpandString
    # Tell Explorer, so terminals opened from now on see the change.
    [Environment]::SetEnvironmentVariable("IC705SUITE_INSTALLER", "1", "User")
    [Environment]::SetEnvironmentVariable("IC705SUITE_INSTALLER", $null, "User")
}

function Add-ToPath {
    $entries = @((Get-UserPath) -split ";" | Where-Object { $_ })
    if ($entries -notcontains $InstallDir) { Set-UserPath (($entries + $InstallDir) -join ";") }
    if (($env:Path -split ";") -notcontains $InstallDir) { $env:Path = "$env:Path;$InstallDir" }
}

function Remove-FromPath {
    $entries = @((Get-UserPath) -split ";" | Where-Object { $_ })
    $kept = @($entries | Where-Object { $_ -ne $InstallDir })
    if ($kept.Count -ne $entries.Count) { Set-UserPath ($kept -join ";") }
}

function New-StartMenuShortcut {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($Shortcut)
    $link.TargetPath = $Exe
    $link.WorkingDirectory = $env:USERPROFILE
    $link.Description = T "Rig control, CW, antenna, logbook and packet for the Icom IC-705" `
        "Control del equipo, CW, antena, diario y packet para el Icom IC-705"
    $link.Save()
}

# Installs the executable at $Source: fresh install, upgrade or reinstall.
function Install-Executable([string]$Source) {
    $new = Get-VersionOf $Source
    if (-not $new) { Die (T "{0} does not run on this system" "{0} no arranca en este sistema" $Source) }
    $current = Get-InstalledVersion

    if ($current) {
        if ($current -eq $new -and -not $Force) {
            Info (T "ic705suite {0} is already installed in {1}" "ic705suite {0} ya est\u00e1 instalado en {1}" $new $Exe)
            return
        }
        if ((Test-Older $new $current) -and -not $Force) {
            Die (T "ic705suite {0} is installed, newer than {1}. Use -Force to go back." `
                   "Est\u00e1 instalado ic705suite {0}, m\u00e1s nuevo que {1}. Usa -Force para volver atr\u00e1s." $current $new)
        }
        if ($current -eq $new) {
            Info (T "Reinstalling ic705suite {0}" "Reinstalando ic705suite {0}" $new)
        } else {
            Info (T "Upgrading ic705suite from {0} to {1}" "Actualizando ic705suite de {0} a {1}" $current $new)
        }
    } else {
        if ((Test-Path -LiteralPath $Exe) -and -not $Force) {
            Die (T "{0} already exists and was not installed by this script. Remove it first or use -Force." `
                   "{0} ya existe y no lo instal\u00f3 este script. Qu\u00edtalo antes o usa -Force." $Exe)
        }
        Info (T "Installing ic705suite {0} in {1}" "Instalando ic705suite {0} en {1}" $new $Exe)
    }

    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    # Copy, then swap. A running ic705suite.exe cannot be overwritten but can be renamed:
    # it keeps running from the old file, which goes the next time.
    $old = "$Exe.old"
    Remove-Item -LiteralPath $old -Force -ErrorAction SilentlyContinue
    Copy-Item -LiteralPath $Source -Destination "$Exe.new" -Force
    if (Test-Path -LiteralPath $Exe) { Move-Item -LiteralPath $Exe -Destination $old -Force }
    Move-Item -LiteralPath "$Exe.new" -Destination $Exe -Force
    Remove-Item -LiteralPath $old -Force -ErrorAction SilentlyContinue
    Set-Content -LiteralPath $VersionFile -Value $new -Encoding Ascii
    Add-ToPath
    New-StartMenuShortcut

    Info (T "Done: ic705suite {0} (open a new terminal to have it in PATH)" `
            "Hecho: ic705suite {0} (abre un terminal nuevo para tenerlo en el PATH)" $new)
    Info (T "Try it without a radio:  ic705suite --sim" "Pru\u00e9balo sin equipo:  ic705suite --sim --lang es")
    Info (T "Then set up your radio: ic705suite --init-config  ({0}\config.toml)" `
            "Luego configura tu equipo: ic705suite --init-config  ({0}\config.toml)" $ConfigDir)
}

# Downloads a release, checks it against SHA256SUMS.txt and installs it.
function Install-Release {
    $base = if ($Version) { "$ReleasesUrl/download/v$Version" } else { "$ReleasesUrl/latest/download" }
    # Windows PowerShell 5.1 does not offer TLS 1.2 by default.
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072
    $ProgressPreference = "SilentlyContinue"   # the progress bar makes 5.1 downloads crawl

    $work = Join-Path ([IO.Path]::GetTempPath()) ("$App-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $work | Out-Null
    try {
        Info (T "Looking for the release in {0}" "Buscando la versi\u00f3n en {0}" $base)
        $sums = Join-Path $work "SHA256SUMS.txt"
        try { Invoke-WebRequest -UseBasicParsing -Uri "$base/SHA256SUMS.txt" -OutFile $sums }
        catch { Die (T "Could not download {0}/SHA256SUMS.txt" "No se pudo descargar {0}/SHA256SUMS.txt" $base) }
        $line = Get-Content -LiteralPath $sums |
            Where-Object { $_ -match "^([0-9a-fA-F]{64})\s+\*?($App-([0-9][0-9A-Za-z.]*)-$Platform\.zip)$" } |
            Select-Object -First 1
        if (-not $line) { Die (T "That release has no Windows package" "Esa versi\u00f3n no tiene paquete para Windows") }
        $null = $line -match "^([0-9a-fA-F]{64})\s+\*?($App-([0-9][0-9A-Za-z.]*)-$Platform\.zip)$"
        $sum, $name, $remote = $Matches[1], $Matches[2], $Matches[3]

        $current = Get-InstalledVersion
        if ($current -eq $remote -and -not $Force) {
            Info (T "ic705suite {0} is already the latest version" "ic705suite {0} ya es la \u00faltima versi\u00f3n" $current)
            return
        }

        Info (T "Downloading {0}" "Descargando {0}" $name)
        $zip = Join-Path $work $name
        try { Invoke-WebRequest -UseBasicParsing -Uri "$base/$name" -OutFile $zip }
        catch { Die (T "Could not download {0}/{1}" "No se pudo descargar {0}/{1}" $base $name) }
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash -ne $sum.ToUpper()) {
            Die (T "{0} does not match its SHA-256 sum; nothing was installed" `
                   "{0} no coincide con su suma SHA-256; no se ha instalado nada" $name)
        }
        Expand-Archive -LiteralPath $zip -DestinationPath $work -Force
        Install-Executable (Join-Path $work "$App-$remote-$Platform\$App.exe")
    } finally {
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Uninstall-Suite {
    $current = Get-InstalledVersion
    if ($current) {
        Info (T "Uninstalling ic705suite {0} from {1}" "Desinstalando ic705suite {0} de {1}" $current $InstallDir)
        Remove-FromPath
        Remove-Item -LiteralPath $Shortcut -Force -ErrorAction SilentlyContinue
        foreach ($file in @($Exe, "$Exe.old", "$Exe.new", $VersionFile)) {
            Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue
        }
        if ((Test-Path -LiteralPath $Exe) -or (Test-Path -LiteralPath "$Exe.old")) {
            Warn (T "{0} is in use; close ic705suite and run this again to remove it" `
                    "{0} est\u00e1 en uso; cierra ic705suite y vuelve a ejecutar esto para quitarlo" $Exe)
        }
        # The folder only if nothing else was left in it.
        if ((Test-Path -LiteralPath $InstallDir) -and -not (Get-ChildItem -LiteralPath $InstallDir -Force)) {
            Remove-Item -LiteralPath $InstallDir -Force
        }
    } elseif ($Purge) {
        Warn (T "ic705suite is not installed in {0}; deleting its data only" `
                "ic705suite no est\u00e1 instalado en {0}; solo se borran sus datos" $InstallDir)
    } else {
        Die (T "ic705suite is not installed in {0} (installed with -InstallDir?)" `
               "ic705suite no est\u00e1 instalado en {0} (\u00bfse instal\u00f3 con -InstallDir?)" $InstallDir)
    }

    if (-not $Purge) {
        Info (T "Done. Your settings stay in {0} and your data in {1}" `
                "Hecho. Tu configuraci\u00f3n sigue en {0} y tus datos en {1}" $ConfigDir $DataDir)
        return
    }
    if (-not $Yes) {
        $answer = Read-Host (T "Delete {0} and {1}, with the CW channels and the antenna history? [y/N]" `
                               "\u00bfBorrar {0} y {1}, con los canales de CW y el historial de antenas? [s/N]" $ConfigDir $DataDir)
        if ($answer -notmatch "^[yYsS]") {
            Info (T "Data kept in {0}" "Datos conservados en {0}" $DataDir)
            return
        }
    }
    Remove-Item -LiteralPath $ConfigDir, $DataDir -Recurse -Force -ErrorAction SilentlyContinue
    Info (T "Data deleted (hamrlog's logbook was left alone)" "Datos borrados (el diario de hamrlog no se ha tocado)")
}

function Invoke-Installer {
    if ($Help) { Show-Usage; return }
    if ($Status) {
        $current = Get-InstalledVersion
        if ($current) { Write-Host "ic705suite $current ($Exe)"; return }
        Write-Host (T "ic705suite is not installed in {0}" "ic705suite no est\u00e1 instalado en {0}" $InstallDir)
        $script:ExitCode = 1
    } elseif ($Uninstall) {
        Uninstall-Suite
    } elseif ($Upgrade) {
        if (-not (Get-InstalledVersion)) {
            Die (T "ic705suite is not installed in {0}; run without -Upgrade to install it" `
                   "ic705suite no est\u00e1 instalado en {0}; ejecuta sin -Upgrade para instalarlo" $InstallDir)
        }
        Install-Release
    } else {
        $bundled = if ($ScriptDir) { Join-Path $ScriptDir "$App.exe" } else { "" }
        if (-not $Version -and $bundled -and (Test-Path -LiteralPath $bundled)) {
            Install-Executable $bundled
        } else {
            Install-Release
        }
    }
}

try {
    Invoke-Installer
} catch [System.OperationCanceledException] {
    # Die has already said why.
}
# A script file reports how it went; "irm | iex" must leave the window open.
if ($PSCommandPath) { exit $script:ExitCode }
