# User-local, versioned program cache. Preferences and resource writes stay at source.
function Get-MiraCachedRuntime($manifestPath) {
    $m = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $base = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) ('MIRA\startup\' + $m.project_key)
    [void][IO.Directory]::CreateDirectory($base)
    $runtime = Join-Path $base ('runtime-' + $m.runtime_key)
    $app = Join-Path $base ('app-' + $m.app_key)
    $runtimeReady = Join-Path $runtime '.complete'
    $appReady = Join-Path $app '.complete'
    if (!(Test-Path -LiteralPath $runtimeReady) -or !(Test-Path -LiteralPath $appReady)) {
        [Console]::WriteLine('[launcher] preparing local Windows startup cache (first launch or updated code)')
        $lock = $null
        $deadline = [DateTime]::UtcNow.AddMinutes(3)
        while ($null -eq $lock) {
            try { $lock = [IO.File]::Open((Join-Path $base 'update.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }
            catch [IO.IOException] {
                if ([DateTime]::UtcNow -gt $deadline) { throw 'Timed out waiting for startup cache' }
                Start-Sleep -Milliseconds 100
            }
        }
        try {
            if (!(Test-Path -LiteralPath $runtimeReady)) {
                $stage = $runtime + '.preparing-' + [Guid]::NewGuid().ToString('N')
                [void][IO.Directory]::CreateDirectory($stage)
                & robocopy.exe $m.runtime_source $stage /E /MT:8 /R:1 /W:1 /NFL /NDL /NJH /NJS /NP /XD __pycache__ wheels .pytest_cache /XF .env .env.* pip.ini pip.conf | Out-Null
                if ($LASTEXITCODE -ge 8) { throw "Runtime copy failed ($LASTEXITCODE)" }
                [IO.File]::WriteAllText((Join-Path $stage '.complete'), $m.runtime_key)
                if (Test-Path -LiteralPath $runtime) {
                    Move-Item -LiteralPath $runtime -Destination ($runtime + '.previous-' + [Guid]::NewGuid().ToString('N'))
                }
                Move-Item -LiteralPath $stage -Destination $runtime
            }
            if (!(Test-Path -LiteralPath $appReady)) {
                $stage = $app + '.preparing-' + [Guid]::NewGuid().ToString('N')
                [IO.Compression.ZipFile]::ExtractToDirectory($m.bundle, $stage)
                [IO.File]::WriteAllText((Join-Path $stage '.complete'), $m.app_key)
                if (Test-Path -LiteralPath $app) {
                    Move-Item -LiteralPath $app -Destination ($app + '.previous-' + [Guid]::NewGuid().ToString('N'))
                }
                Move-Item -LiteralPath $stage -Destination $app
            }
        } finally { $lock.Dispose() }
    }
    $result = @{
        Python = (Join-Path $runtime $m.python_relative)
        Bootstrap = (Join-Path $app 'scripts\native_bootstrap.py')
        Source = $m.source
        Mirror = $app
    }
    $temp = $m.descriptor + '.preparing-' + [Guid]::NewGuid().ToString('N')
    [IO.File]::WriteAllText($temp, ($result | ConvertTo-Json -Compress), (New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temp -Destination $m.descriptor -Force
    return $result
}
