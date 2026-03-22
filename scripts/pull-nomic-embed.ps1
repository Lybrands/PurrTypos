# 安装完成后拉取 nomic-embed-text（mem0 本地 embedding 用）
# 用法：在 PowerShell 中执行 .\scripts\pull-nomic-embed.ps1
# 或在「以管理员身份」打开的新终端中执行：ollama pull nomic-embed-text

$ollama = $null
foreach ($p in @(
    "C:\Users\$env:USERNAME\AppData\Local\Programs\Ollama\ollama.exe",
    "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe",
    "ollama"
)) {
    if ($p -eq "ollama") {
        $ollama = Get-Command ollama -ErrorAction SilentlyContinue
        if ($ollama) { $ollama = $ollama.Source }
    } elseif (Test-Path $p) {
        $ollama = $p
        break
    }
}

if (-not $ollama) {
    Write-Host "未找到 Ollama。请先安装：https://ollama.com/download 或运行：winget install Ollama.Ollama"
    exit 1
}

Write-Host "使用: $ollama"
Write-Host "正在拉取 nomic-embed-text ..."
& $ollama pull nomic-embed-text
if ($LASTEXITCODE -eq 0) {
    Write-Host "完成。可用 ollama list 查看。"
} else {
    Write-Host "拉取失败，请确认 Ollama 已启动（安装后通常会自动运行）。"
}
