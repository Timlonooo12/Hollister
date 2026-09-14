# Installation interactive sous Windows (PowerShell) :
#   powershell -ExecutionPolicy Bypass -File setup.ps1
#   powershell -ExecutionPolicy Bypass -File setup.ps1 -Token "123:AA..." -NoStart
param(
    [string]$Token = "",
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Say  ($m) { Write-Host $m -ForegroundColor Cyan }
function Ok   ($m) { Write-Host "OK  $m" -ForegroundColor Green }
function Warn ($m) { Write-Host "!   $m" -ForegroundColor Yellow }
function Die  ($m) { Write-Host "X   $m" -ForegroundColor Red; exit 1 }

# --- 1. Python -------------------------------------------------------------
$py = $null
foreach ($candidate in @("py -3.12", "py -3.11", "python")) {
    $exe, $arg = $candidate.Split(" ", 2)
    if (Get-Command $exe -ErrorAction SilentlyContinue) {
        $check = & $exe $arg -c "import sys; print(sys.version_info >= (3, 11))" 2>$null
        if ($check -eq "True") { $py = $candidate; break }
    }
}
if (-not $py) { Die "Python 3.11+ est requis — installe-le depuis https://www.python.org/downloads/ (coche « Add python.exe to PATH »)." }
Ok "Python trouve."

# --- 2. Environnement virtuel ---------------------------------------------
Say "Installation des dependances..."
$exe, $arg = $py.Split(" ", 2)
if (-not (Test-Path ".venv")) { & $exe $arg -m venv .venv }
$vpy = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $vpy)) { $vpy = $exe }
& $vpy -m pip install --quiet --upgrade pip
& $vpy -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) { Die "Installation des dependances echouee." }
Ok "Dependances installees."

# --- 3. Token --------------------------------------------------------------
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }

if (-not $Token) {
    $current = (Select-String -Path ".env" -Pattern "^STOCKWATCH_BOT_TOKEN=(.*)$").Matches.Groups[1].Value.Trim()
    $example = (Select-String -Path ".env.example" -Pattern "^STOCKWATCH_BOT_TOKEN=(.*)$").Matches.Groups[1].Value.Trim()
    if ($current -and $current -ne $example) {
        Ok "Token deja configure."
    } else {
        Write-Host ""
        Say "Token Telegram"
        Write-Host "  1. Ouvre https://t.me/BotFather  ->  /newbot  ->  choisis un nom"
        Write-Host "  2. BotFather te repond un token du style 123456789:AAE-xxxxxxxxxxxx"
        Write-Host ""
        $Token = Read-Host "Colle ton token ici puis Entree"
        if (-not $Token) { Die "Aucun token saisi. Relance : powershell -ExecutionPolicy Bypass -File setup.ps1" }
    }
}

if ($Token) {
    $Token = $Token.Trim()
    if ($Token -notmatch ":") { Die "Ce token ne ressemble pas a un token Telegram (il doit contenir « : »)." }
    $content = Get-Content ".env" -Raw
    $line = "STOCKWATCH_BOT_TOKEN=$Token"
    if ($content -match "(?m)^STOCKWATCH_BOT_TOKEN=.*$") {
        $content = [regex]::Replace($content, "(?m)^STOCKWATCH_BOT_TOKEN=.*$", $line)
    } else {
        $content = $content.TrimEnd() + "`r`n" + $line
    }
    Set-Content ".env" $content -NoNewline
    Ok "Token enregistre dans .env."
}

# --- 4. Verification -------------------------------------------------------
Write-Host ""
Say "Verification de la page surveillee..."
& $vpy -m stockwatch diagnose
if ($LASTEXITCODE -ne 0) {
    Warn "Le stock n'a pas pu etre lu (voir ci-dessus). Le bot demarrera quand meme."
}

# --- 5. Demarrage ----------------------------------------------------------
Write-Host ""
if ($NoStart) { Say "Pret. Pour demarrer :  $vpy -m stockwatch"; exit 0 }
Say "Demarrage du bot — envoie /start a ton bot sur Telegram. (Ctrl+C pour arreter)"
& $vpy -m stockwatch
