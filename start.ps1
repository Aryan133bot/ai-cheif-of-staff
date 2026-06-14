# ═════════════════════════════════════════════════════════════════════════════
# AI Chief of Staff — Startup Control Script
# ═════════════════════════════════════════════════════════════════════════════

Clear-Host
Write-Host "=====================================================================" -ForegroundColor Cyan
Write-Host "            AI Chief of Staff — Control Center" -ForegroundColor Cyan
Write-Host "=====================================================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Ensure Database Schema is Up-to-Date
Write-Host "Step 1: Initialising shared SQLite database and migrating schema..." -ForegroundColor Gray
& "venv/Scripts/python.exe" -c "import sys; sys.path.insert(0, 'dashboard'); import db; db.init_db()"
Write-Host "[OK] Database ready at: 'email processor/phase1_tasks.db'" -ForegroundColor Green
Write-Host ""

# Step 2: Open Dashboard in Browser
Write-Host "Step 2: Opening dashboard interface..." -ForegroundColor Gray
Start-Process "http://127.0.0.1:8000"
Write-Host "[OK] Opened http://127.0.0.1:8000 in default browser" -ForegroundColor Green
Write-Host ""

# Step 3: Run the Server
Write-Host "Step 3: Starting Dashboard API Server..." -ForegroundColor Gray
Write-Host "=====================================================================" -ForegroundColor DarkGray
Write-Host "   SERVER RUNNING ON: http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "   Keep this terminal window open." -ForegroundColor Yellow
Write-Host "   Press Ctrl+C inside this terminal to stop the server." -ForegroundColor Red
Write-Host "=====================================================================" -ForegroundColor DarkGray
Write-Host ""

& "venv/Scripts/python.exe" "dashboard/server.py" --port 8000
