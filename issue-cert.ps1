# 给当前这台电脑上的 Quickback 签发一张本地证书，供 iPhone 信任后用麦克风。
# 用法：powershell -ExecutionPolicy Bypass -File .\issue-cert.ps1
# 根证书会重复使用。地址变了就重新签发服务器证书，不用再在手机上装一次根证书。
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

function Find-OpenSsl {
    $cmd = Get-Command openssl -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $git = "C:\Program Files\Git\usr\bin\openssl.exe"
    if (Test-Path -LiteralPath $git) { return $git }
    throw "找不到 openssl。安装 Git for Windows 后再运行。"
}

function Get-QuickbackBind {
    $lines = & .\.venv\Scripts\python.exe -c "from app.config import settings; print(settings.host); print(settings.port)"
    return @{
        Host = ([string]$lines[0]).Trim()
        Port = ([string]$lines[1]).Trim()
    }
}

function Get-LanAddresses {
    param([string]$BindHost)
    $ips = New-Object System.Collections.Generic.List[string]
    if ($BindHost -and $BindHost -ne "0.0.0.0" -and $BindHost -ne "::" -and $BindHost -notlike "127.*") {
        $ips.Add($BindHost)
    } else {
        $skip = "vEthernet|WSL|Hyper-V|VirtualBox|VMware|Loopback|Bluetooth|Npcap|Tailscale"
        Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object {
                $_.IPAddress -notlike "127.*" -and
                $_.IPAddress -notlike "169.254.*" -and
                $_.AddressState -eq "Preferred" -and
                $_.InterfaceAlias -notmatch $skip
            } |
            ForEach-Object { $ips.Add($_.IPAddress) }
    }
    return @($ips | Select-Object -Unique)
}

function Write-Utf8NoBom {
    param([string]$Path, [string]$Text)
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($Path, $Text.Replace("`r`n", "`n"), $utf8)
}

$openssl = Find-OpenSsl
if (-not (Test-Path .venv)) {
    throw "还没有 .venv。先运行一次 .\run.ps1 。"
}

$bind = Get-QuickbackBind
$names = @(Get-LanAddresses -BindHost $bind.Host)
$dns = @("localhost", $env:COMPUTERNAME) | Select-Object -Unique
$dir = Join-Path $PSScriptRoot "certs"
New-Item -ItemType Directory -Force -Path $dir | Out-Null

$caKey = Join-Path $dir "ca.key"
$caCrt = Join-Path $dir "ca.crt"
$caCer = Join-Path $dir "quickback-ca.cer"
$serverKey = Join-Path $dir "server.key"
$serverCrt = Join-Path $dir "server.crt"
$serverCsr = Join-Path $dir "server.csr"
$cnf = Join-Path $dir "server.cnf"

if (-not (Test-Path -LiteralPath $caKey) -or -not (Test-Path -LiteralPath $caCrt)) {
    & $openssl genrsa -out $caKey 2048
    & $openssl req -x509 -new -nodes -key $caKey -sha256 -days 825 -out $caCrt -subj "/CN=Quickback Local CA"
}
& $openssl x509 -outform der -in $caCrt -out $caCer

$alt = @()
$i = 1
foreach ($name in $dns) {
    $alt += "DNS.$i = $name"
    $i++
}
$i = 1
$alt += "IP.$i = 127.0.0.1"
$i++
foreach ($ip in $names) {
    $alt += "IP.$i = $ip"
    $i++
}
$cnfText = @"
[req]
distinguished_name = dn
prompt = no

[dn]
CN = quickback

[v3_req]
subjectAltName = @alt_names
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth

[alt_names]
$($alt -join "`n")
"@
Write-Utf8NoBom -Path $cnf -Text $cnfText

& $openssl genrsa -out $serverKey 2048
& $openssl req -new -key $serverKey -out $serverCsr -config $cnf
& $openssl x509 -req -in $serverCsr -CA $caCrt -CAkey $caKey -CAcreateserial -out $serverCrt -days 825 -sha256 -extfile $cnf -extensions v3_req
Remove-Item -LiteralPath $serverCsr -Force

Write-Host ""
Write-Host "已签好。私钥留在本机的 certs 目录，不要传到手机。"
Write-Host "传给 iPhone 的只有这个文件：certs\quickback-ca.cer"
if ($names.Count -eq 0) {
    Write-Host "没有发现局域网地址。证书只包含本机 localhost。"
} else {
    Write-Host ("手机打开：" + (($names | ForEach-Object { "https://${_}:$($bind.Port)" }) -join "  或  "))
}
Write-Host "iPhone：安装描述文件后，到 设置 → 通用 → 关于本机 → 证书信任设置，打开 Quickback Local CA 的完全信任。"
Write-Host "重新运行 .\run.ps1 就会自动改用这张证书。"
