# Creates a Desktop shortcut: "Jarvis AI Assistant"
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$batPath = Join-Path $projectRoot "launch_jarvis.bat"
$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "Jarvis AI Assistant.lnk"

if (-not (Test-Path $batPath)) {
    throw "Missing launcher: $batPath"
}

$icon = Join-Path $projectRoot "assets\jarvis.ico"
if (-not (Test-Path $icon)) {
    # A generic Windows icon is invisible on a crowded desktop, which is the whole point here.
    $icon = "$env:SystemRoot\System32\shell32.dll,13"
}

$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $batPath
$shortcut.WorkingDirectory = $projectRoot
$shortcut.WindowStyle = 1
$shortcut.Description = "Jarvis local AI computer agent"
$shortcut.IconLocation = $icon
$shortcut.Save()

# Also pin-friendly Start Menu shortcut
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$startShortcutPath = Join-Path $startMenu "Jarvis AI Assistant.lnk"
$startShortcut = $wsh.CreateShortcut($startShortcutPath)
$startShortcut.TargetPath = $batPath
$startShortcut.WorkingDirectory = $projectRoot
$startShortcut.WindowStyle = 1
$startShortcut.Description = "Jarvis local AI computer agent"
$startShortcut.IconLocation = $icon
$startShortcut.Save()

Write-Output "Desktop shortcut: $shortcutPath"
Write-Output "Start Menu shortcut: $startShortcutPath"

