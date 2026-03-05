param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$targets = @(
    "app\__pycache__",
    "tests\__pycache__",
    "debug\__pycache__"
)

Write-Output "Cache cleanup targets:"
foreach ($target in $targets) {
    Write-Output " - $target"
}

foreach ($target in $targets) {
    if (-not (Test-Path $target)) {
        Write-Output "[skip] missing: $target"
        continue
    }
    if ($DryRun) {
        Write-Output "[dry-run] would remove: $target"
        continue
    }
    Remove-Item -Recurse -Force $target
    Write-Output "[ok] removed: $target"
}

Write-Output "Done."
