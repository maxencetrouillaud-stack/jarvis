# Installation de Jarvis en une commande (PowerShell) :
#   irm https://raw.githubusercontent.com/maxencetrouillaud-stack/jarvis/main/install.ps1 | iex
$ErrorActionPreference = "Stop"
$py = Get-Command py -ErrorAction SilentlyContinue
$args3 = @("-3")
if (-not $py) {
  $py = Get-Command python -ErrorAction SilentlyContinue
  $args3 = @()
  if ($py -and $py.Source -like "*WindowsApps*") { $py = $null }   # raccourci du Microsoft Store, pas un vrai Python
}
if (-not $py) {
  Write-Host "Python n'est pas installe. Installez-le depuis python.org (cochez 'Add python.exe to PATH'), puis relancez cette commande." -ForegroundColor Yellow
  Start-Process "https://www.python.org/downloads/"
  return
}
$tmp = Join-Path $env:TEMP "jarvis-install.py"
Write-Host "Telechargement de l'installateur de Jarvis..."
$t = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/maxencetrouillaud-stack/jarvis/main/install.py?t=$t" -OutFile $tmp
$env:JARVIS_NO_PAUSE = "1"
& $py.Source @args3 $tmp --from-github
Remove-Item $tmp -ErrorAction SilentlyContinue
