param(
    [string]$Database = ".runtime/users.db",
    [string]$BackupDir = ".runtime/backups"
)

New-Item -ItemType Directory -Force $BackupDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$target = Join-Path $BackupDir "users-$stamp.db"
Copy-Item $Database $target -Force
Write-Output $target
