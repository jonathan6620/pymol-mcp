<#
.SYNOPSIS
    One-shot setup for PyMOL-MCP on Windows.

.DESCRIPTION
    Installs uv, syncs this repo's Python dependencies, finds or installs
    PyMOL from conda-forge, installs the socket plugin and its auto-start
    block, installs the pymol-mcp skill, and registers the MCP server with
    whichever of the Claude Code and Codex CLIs is installed.

    Every step is safe to re-run.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File shell\install-windows.ps1

.EXAMPLE
    .\shell\install-windows.ps1 -Yes -Pymol "C:\path\to\pymol.exe"
#>

[CmdletBinding()]
param(
    # Answer yes to every prompt (for CI or an unattended run).
    [switch]$Yes,
    # Use this PyMOL executable instead of searching for one.
    [string]$Pymol,
    # Do not look for or install PyMOL.
    [switch]$SkipPymol,
    # Do not register the server with Claude Code or Codex.
    [switch]$SkipClients,
    # Overwrite an existing, unmanaged ~/.pymolrc.py.
    [switch]$ForcePymolrc
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$CondaEnvName = 'pymol-env'
$MiniforgeUrl = 'https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Windows-x86_64.exe'

# Set by Find-PyMOL / New-PyMOLEnv. An empty $script:PymolBin with a non-empty
# $script:CondaBin means "run it through conda run" -- see Invoke-PyMOL.
$script:PymolBin = ''
$script:CondaBin = ''
$script:Notes = @()

# --- output -----------------------------------------------------------------

function Write-Step { param([string]$Message) Write-Host "`n==> $Message" -ForegroundColor White }
function Write-Info { param([string]$Message) Write-Host "    $Message" }
function Write-Ok   { param([string]$Message) Write-Host "    $Message" -ForegroundColor Green }
function Write-Warn { param([string]$Message) Write-Host "    $Message" -ForegroundColor Yellow }
function Stop-WithError {
    param([string]$Message)
    Write-Host "`nerror: $Message" -ForegroundColor Red
    exit 1
}
function Add-Note { param([string]$Message) $script:Notes += $Message }

function Test-Command {
    param([string]$Name)
    $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

# Native commands set $LASTEXITCODE instead of throwing, so $ErrorActionPreference
# does not catch a failed installer or a failed `uv sync` on its own.
function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments, [string]$What)
    # Out-Host, not bare output: a native command's stdout otherwise joins the
    # calling function's pipeline and comes back as part of its return value.
    & $Exe @Arguments | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Stop-WithError "$What failed (exit code $LASTEXITCODE)."
    }
}

# Yes on -Yes, yes on a bare Enter, and yes when there is no console to ask:
# every prompt here guards an install this script exists to perform.
function Confirm-Action {
    param([string]$Question)
    if ($Yes -or [Console]::IsInputRedirected) { return $true }
    $reply = Read-Host "    $Question [Y/n]"
    return ($reply -notmatch '^\s*n(o)?\s*$')
}

# --- repository -------------------------------------------------------------

# The repo root is the parent of shell\, resolved from this file rather than
# the working directory, so the script works from anywhere.
$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $RepoRoot 'pyproject.toml'))) {
    Stop-WithError "$RepoRoot does not look like the pymol-mcp repo (no pyproject.toml)."
}
Set-Location $RepoRoot

# --- uv ---------------------------------------------------------------------

function Install-Uv {
    Write-Step 'Checking for uv'

    # The installer adds this directory to the user PATH in the registry, which
    # an already-running process cannot see. Look in it directly first, or a
    # second run reinstalls uv every time.
    $uvHome = Join-Path $env:USERPROFILE '.local\bin'
    if (-not (Test-Command 'uv') -and (Test-Path (Join-Path $uvHome 'uv.exe'))) {
        $env:PATH = "$uvHome;$env:PATH"
    }

    if (Test-Command 'uv') {
        Write-Ok "uv at $((Get-Command uv).Source)"
        return
    }

    Write-Info 'Installing uv from https://astral.sh/uv.'
    # The installer edits the user PATH in the registry, which this process
    # cannot see, so add the directory to the in-process PATH as well.
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    if (Test-Path $uvHome) { $env:PATH = "$uvHome;$env:PATH" }

    if (-not (Test-Command 'uv')) {
        Stop-WithError 'uv install finished but "uv" is still not on PATH. Open a new terminal and rerun.'
    }
    Add-Note "uv was installed to $uvHome. Open a new terminal for other tools to see it."
    Write-Ok 'uv installed.'
}

function Sync-PythonDeps {
    Write-Step "Installing this repo's Python dependencies"
    Write-Info 'uv sync  (creates .venv from uv.lock)'
    Invoke-Checked 'uv' @('sync') 'uv sync'
    Write-Ok "Dependencies installed into $RepoRoot\.venv"
}

# --- PyMOL ------------------------------------------------------------------

function Find-PyMOL {
    if ($Pymol) {
        if (-not (Test-Path $Pymol)) { Stop-WithError "-Pymol $Pymol does not exist." }
        return (Resolve-Path $Pymol).Path
    }
    if (Test-Command 'pymol') { return (Get-Command pymol).Source }

    # conda puts the launcher in Scripts\ on Windows, but which of these names
    # exists varies by build, and Schrodinger's installer uses neither.
    $patterns = @(
        "$env:USERPROFILE\miniforge3\envs\*\Scripts\pymol.exe"
        "$env:USERPROFILE\miniforge3\envs\*\pymol.exe"
        "$env:USERPROFILE\miniconda3\envs\*\Scripts\pymol.exe"
        "$env:USERPROFILE\anaconda3\envs\*\Scripts\pymol.exe"
        "$env:USERPROFILE\mambaforge\envs\*\Scripts\pymol.exe"
        "C:\ProgramData\miniforge3\envs\*\Scripts\pymol.exe"
        "C:\ProgramData\miniconda3\envs\*\Scripts\pymol.exe"
        "C:\ProgramData\anaconda3\envs\*\Scripts\pymol.exe"
        "$env:ProgramFiles\Schrodinger\PyMOL*\PyMOLWin.exe"
        "${env:ProgramFiles(x86)}\Schrodinger\PyMOL*\PyMOLWin.exe"
        "$env:LOCALAPPDATA\Schrodinger\PyMOL*\PyMOLWin.exe"
    )
    foreach ($pattern in $patterns) {
        # Prefer the pymol-env this script creates over any other env that
        # happens to have PyMOL in it.
        $hit = Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue |
               Sort-Object { $_.FullName -notlike "*\$CondaEnvName\*" } |
               Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    return ''
}

function Find-Conda {
    foreach ($name in @('conda', 'mamba', 'micromamba')) {
        if (Test-Command $name) { return (Get-Command $name).Source }
    }
    $candidates = @(
        "$env:USERPROFILE\miniforge3\Scripts\conda.exe"
        "$env:USERPROFILE\miniconda3\Scripts\conda.exe"
        "$env:USERPROFILE\anaconda3\Scripts\conda.exe"
        "$env:USERPROFILE\mambaforge\Scripts\conda.exe"
        "C:\ProgramData\miniforge3\Scripts\conda.exe"
        "C:\ProgramData\miniconda3\Scripts\conda.exe"
        "C:\ProgramData\anaconda3\Scripts\conda.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    return ''
}

function Install-Miniforge {
    $installer = Join-Path $env:TEMP 'Miniforge3-Windows-x86_64.exe'
    Write-Info 'Downloading Miniforge.'
    Invoke-WebRequest -Uri $MiniforgeUrl -OutFile $installer -UseBasicParsing

    $target = Join-Path $env:USERPROFILE 'miniforge3'
    Write-Info "Installing to $target (silent, this user only)."
    # /D is the NSIS install directory switch: it must come last, and it must
    # not be quoted, so a USERPROFILE containing a space would break here.
    $installerArgs = @('/S', '/InstallationType=JustMe', '/RegisterPython=0', "/D=$target")
    Start-Process -FilePath $installer -ArgumentList $installerArgs -Wait -NoNewWindow
    Remove-Item $installer -ErrorAction SilentlyContinue

    $conda = Join-Path $target 'Scripts\conda.exe'
    if (-not (Test-Path $conda)) {
        Stop-WithError "Miniforge install finished but $conda is missing."
    }
    Add-Note "Miniforge was installed to $target but your shell was not modified. Run '$conda init powershell' if you want the 'conda' command in new terminals."
    return $conda
}

function New-PyMOLEnv {
    Write-Step 'Installing PyMOL with conda'

    $script:CondaBin = Find-Conda
    if ($script:CondaBin) {
        Write-Ok "Found conda at $script:CondaBin"
    } else {
        Write-Warn 'No conda, mamba, or micromamba found.'
        Write-Info 'PyMOL is not on PyPI, so this installs it from conda-forge.'
        if (-not (Confirm-Action "Download and install Miniforge to $env:USERPROFILE\miniforge3?")) {
            Add-Note "PyMOL was not installed. Install it yourself and rerun, or pass the path: .\shell\install-windows.ps1 -Pymol C:\path\to\pymol.exe"
            return $false
        }
        $script:CondaBin = Install-Miniforge
    }

    $envList = & $script:CondaBin env list 2>$null
    if ($envList -match "(?m)^$CondaEnvName\s") {
        Write-Ok "conda env '$CondaEnvName' already exists."
    } else {
        Write-Info 'conda env create -f environment.yml   (this pulls ~1 GB, give it a few minutes)'
        Invoke-Checked $script:CondaBin @('env', 'create', '-f', 'environment.yml') 'conda env create'
        Write-Ok "Created conda env '$CondaEnvName'."
    }

    # Prefer a real path over `conda run`, which buffers PyMOL's output and
    # adds a second of startup to every call.
    $script:PymolBin = Find-PyMOL
    return $true
}

function Initialize-PyMOL {
    if ($SkipPymol) {
        Write-Step 'Skipping PyMOL (-SkipPymol)'
        return $true
    }

    Write-Step 'Looking for PyMOL'
    $found = Find-PyMOL
    if ($found) {
        $script:PymolBin = $found
        Write-Ok "Found $found"
        return $true
    }

    Write-Info 'Not on PATH, and not in the usual conda or Schrodinger locations.'
    return (New-PyMOLEnv)
}

# Runs PyMOL whether or not we know its path: `conda run` covers the case where
# the env exists but the launcher is not where the search patterns expect.
# Leaves the exit code in $LASTEXITCODE for the caller to check.
function Invoke-PyMOL {
    param([string[]]$Arguments)
    if ($script:PymolBin) {
        & $script:PymolBin @Arguments | Out-Host
    } else {
        $condaArgs = @('run', '-n', $CondaEnvName, 'pymol') + $Arguments
        & $script:CondaBin @condaArgs | Out-Host
    }
}

# --- plugin, pymolrc, skill -------------------------------------------------

function Install-Plugin {
    Write-Step 'Installing the PyMOL socket plugin'
    if (-not ($script:PymolBin -or $script:CondaBin)) {
        Write-Warn 'No PyMOL to install into -- skipped.'
        Add-Note 'Once PyMOL is installed, run:  pymol -cq scripts\install_plugin.py'
        return
    }
    Invoke-PyMOL @('-cq', 'scripts\install_plugin.py')
    if ($LASTEXITCODE -eq 0) {
        Write-Ok 'Plugin installed.'
    } else {
        Write-Warn 'PyMOL exited non-zero; the plugin may not be installed.'
        Add-Note 'Retry the plugin step with:  pymol -cq scripts\install_plugin.py'
    }
}

function Install-Pymolrc {
    Write-Step 'Configuring PyMOL to start the listener at launch'
    $pymolrcArgs = @('run', 'python', 'scripts\install_pymolrc.py')
    if ($ForcePymolrc) { $pymolrcArgs += '--force' }
    Invoke-Checked 'uv' $pymolrcArgs 'install_pymolrc.py'
}

function Install-Skill {
    Write-Step 'Installing the pymol-mcp skill'
    Invoke-Checked 'uv' @('run', 'python', 'scripts\install_skill.py') 'install_skill.py'
}

# --- MCP clients ------------------------------------------------------------

function Register-Clients {
    if ($SkipClients) {
        Write-Step 'Skipping MCP client registration (-SkipClients)'
        return
    }

    Write-Step 'Registering the MCP server with your clients'
    $found = $false

    # `mcp get` exits non-zero when the name is unknown, in both CLIs, which is
    # steadier than parsing the human-readable `mcp list` table.
    if (Test-Command 'claude') {
        $found = $true
        & claude mcp get pymol 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "Claude Code: 'pymol' is already registered."
        } else {
            & claude mcp add pymol -s user -- uv --directory $RepoRoot run pymol-mcp
            if ($LASTEXITCODE -eq 0) {
                Write-Ok "Claude Code: registered 'pymol' (user scope)."
            } else {
                Write-Warn "Claude Code: 'claude mcp add' failed (already registered?)."
                Add-Note 'Check with:  claude mcp list'
            }
        }
    }

    if (Test-Command 'codex') {
        $found = $true
        & codex mcp get pymol 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "Codex: 'pymol' is already registered."
        } else {
            & codex mcp add pymol -- uv --directory $RepoRoot run pymol-mcp
            if ($LASTEXITCODE -eq 0) {
                Write-Ok "Codex: registered 'pymol'."
            } else {
                Write-Warn "Codex: 'codex mcp add' failed (already registered?)."
                Add-Note 'Check with:  codex mcp list'
            }
        }
    }

    if (-not $found) {
        Write-Warn "Neither the 'claude' nor the 'codex' CLI is on PATH."
        Add-Note @"
No MCP client was configured. For Claude Code:
      claude mcp add pymol -s user -- uv --directory "$RepoRoot" run pymol-mcp
  For Codex:
      codex mcp add pymol -- uv --directory "$RepoRoot" run pymol-mcp
  For Claude Desktop, see the README (Step 3, Option A). Use forward slashes in
  the JSON config.
"@
    }
}

# --- run --------------------------------------------------------------------

Write-Host "PyMOL-MCP setup -- Windows ($env:PROCESSOR_ARCHITECTURE)" -ForegroundColor White
Write-Host $RepoRoot -ForegroundColor DarkGray

if ($PSVersionTable.PSVersion.Major -lt 5) {
    Stop-WithError "PowerShell 5.1 or newer is required (found $($PSVersionTable.PSVersion))."
}

Install-Uv
Sync-PythonDeps
$null = Initialize-PyMOL
Install-Plugin
Install-Pymolrc
Install-Skill
Register-Clients

Write-Step 'Done'
if ($script:Notes.Count -gt 0) {
    Write-Host 'Still to do:' -ForegroundColor White
    foreach ($n in $script:Notes) { Write-Host "  $n" }
    Write-Host ''
}
Write-Host @'
Next:
  1. Restart PyMOL. It should print
     "MCP socket plugin auto-started on port 9876" (or the next free port).
  2. Start a new Claude Code, Codex, or Claude Desktop session so it picks up
     the server.
  3. Ask it to "load PDB 1UBQ and show it as cartoon".
'@
Write-Host @'

Launch PyMOL from its own terminal or desktop shortcut: it writes to the
terminal it was started from, which garbles a terminal client's display.
'@ -ForegroundColor DarkGray
