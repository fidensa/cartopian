param([string]$Adapter, [string]$PromptPath)
$ProjectDir = $env:CARTOPIAN_PROJECT_ROOT
if (-not $ProjectDir -and $PromptPath) {
    $PromptDir = Split-Path -Parent ([IO.Path]::GetFullPath($PromptPath))
    if ((Split-Path -Leaf $PromptDir) -eq 'prompts') { $ProjectDir = Split-Path -Parent $PromptDir }
}
if (-not $ProjectDir) { $ProjectDir = $env:CARTOPIAN_LAUNCH_CWD }
$HasConfig = $ProjectDir -and (Test-Path -LiteralPath (Join-Path $ProjectDir 'cartopian.toml') -PathType Leaf)
if ($env:CARTOPIAN_ROLE -or $env:CARTOPIAN_PROJECT_ROOT -or $HasConfig) {
    if (-not $ProjectDir -or -not [IO.Path]::IsPathRooted($ProjectDir)) {
        throw "cartopian-$Adapter`: project work access requires an absolute project binding"
    }
    $Python = $env:CARTOPIAN_PYTHON
    if (-not $Python -or -not [IO.Path]::IsPathRooted($Python) -or -not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw "cartopian-$Adapter`: project work access requires dispatch-bound CARTOPIAN_PYTHON; launch through cartopian dispatch"
    }
    $Helper = Join-Path $PSScriptRoot '..\..\cli\work_access.py'
    if (-not (Test-Path -LiteralPath $Helper -PathType Leaf)) { throw 'Missing required work-access helper; reinstall Cartopian' }
    & $Python -I -S $Helper --wrapper $Adapter --project-dir $ProjectDir --platform win32
    if ($LASTEXITCODE -ne 0) { throw "cartopian-$Adapter`: work-access preflight refused launch" }
}
