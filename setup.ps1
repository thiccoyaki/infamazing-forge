# infamazing-forge setup for Windows. Run from the repo folder:
#   powershell -ExecutionPolicy Bypass -File setup.ps1
$ErrorActionPreference = "Stop"
function Need($cmd, $hint) {
  if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { Write-Host "Missing: $cmd  ->  $hint" -ForegroundColor Yellow; return $false }
  return $true
}
$ok = $true
$ok = (Need "python" "winget install Python.Python.3.12") -and $ok
$ok = (Need "ffmpeg" "winget install Gyan.FFmpeg") -and $ok
$ok = (Need "claude" "npm i -g @anthropic-ai/claude-code   then run 'claude' once to log in") -and $ok
if (-not $ok) { Write-Host "`nInstall the missing tools, open a NEW terminal, and run setup.ps1 again." -ForegroundColor Yellow; exit 1 }
if (-not (Test-Path .venv)) { python -m venv .venv }
& .\.venv\Scripts\python -m pip install --upgrade pip -q
& .\.venv\Scripts\python -m pip install -r requirements.txt -q
if (-not (Test-Path .env)) { Copy-Item .env.example .env; Write-Host "Created .env - keys are copied from story-forge's .env on first start, or add ELEVENLABS_API_KEY." -ForegroundColor Yellow }
Write-Host "`nDone. Double-click start.bat to open the app." -ForegroundColor Green
