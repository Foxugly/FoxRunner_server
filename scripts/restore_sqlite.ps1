param(
    [Parameter(Mandatory = $true)]
    [string]$Backup,
    [string]$Database = ".runtime/users.db"
)

Stop-Process -Name uvicorn,celery -ErrorAction SilentlyContinue
Copy-Item $Backup $Database -Force
Write-Output "Restored $Backup to $Database"
