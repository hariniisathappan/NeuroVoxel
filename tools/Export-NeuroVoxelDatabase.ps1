$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

$databaseFolder = Join-Path $projectRoot 'database'
New-Item -ItemType Directory -Path $databaseFolder -Force | Out-Null
$outputFile = Join-Path $databaseFolder 'neurovoxel_v3.sqlite3'

$pythonBackup = "import sqlite3; src=sqlite3.connect('/var/lib/neurovoxel/neurovoxel_v3.sqlite3'); dst=sqlite3.connect('/tmp/neurovoxel_v3.sqlite3'); src.backup(dst); dst.close(); src.close()"
& docker compose exec -T neurovoxel python -c $pythonBackup
if ($LASTEXITCODE -ne 0) {
    throw 'Could not back up the live database. Check that Docker Desktop is running and NeuroVoxel is started.'
}

& docker compose cp 'neurovoxel:/tmp/neurovoxel_v3.sqlite3' $outputFile
if ($LASTEXITCODE -ne 0) {
    throw 'The SQLite backup was created in Docker but could not be copied into the project database folder.'
}

Write-Host "Database snapshot saved to: $outputFile"
Write-Host 'Open this file in DB Browser for SQLite. Run this script again to refresh the snapshot.'
