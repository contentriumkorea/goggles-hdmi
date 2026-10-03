param([switch]$Restore)
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$null = New-Item -ItemType Directory -Path (Join-Path $taskRoot 'qa') -Force
$taskResult = Join-Path $taskRoot 'qa/network-setup-result.json'
try {
    $taskDevice = @(Get-CimInstance Win32_NetworkAdapter | Where-Object {
        $_.PNPDeviceID -like 'USB\VID_2CA3&PID_0020&MI_00\*'
    })
    if ($taskDevice.Count -ne 1) { throw 'Expected exactly one DJI USB network adapter.' }
    $taskIndex = $taskDevice[0].InterfaceIndex
    $taskInterface = Get-NetIPInterface -InterfaceIndex $taskIndex -AddressFamily IPv4
    $taskBackup = Join-Path $taskRoot 'qa/network-settings-before.json'
    if ($Restore) {
        $taskSaved = Get-Content -LiteralPath $taskBackup -Raw | ConvertFrom-Json
        if ($taskSaved.InterfaceGuid -ne $taskDevice[0].GUID) { throw 'Adapter does not match saved backup.' }
        Get-NetIPAddress -InterfaceIndex $taskIndex -AddressFamily IPv4 |
            Where-Object IPAddress -eq '192.168.60.1' |
            Remove-NetIPAddress -Confirm:$false
        Set-NetIPInterface -InterfaceIndex $taskIndex -AddressFamily IPv4 -Dhcp Enabled
    } else {
        $taskAddresses = @(Get-NetIPAddress -InterfaceIndex $taskIndex -AddressFamily IPv4)
        if ($taskAddresses | Where-Object IPAddress -eq '192.168.60.1') {
            @{success=$true; action='already configured'; interfaceIndex=$taskIndex} |
                ConvertTo-Json | Set-Content -LiteralPath $taskResult -Encoding UTF8
            exit 0
        }
        if ($taskInterface.Dhcp -ne 'Enabled' -or
            ($taskAddresses | Where-Object { $_.IPAddress -notlike '169.254.*' })) {
            throw 'Unexpected existing configuration; no changes made.'
        }
        if (Get-NetIPAddress -AddressFamily IPv4 | Where-Object IPAddress -like '192.168.60.*') {
            throw 'Existing 192.168.60 network conflicts; no changes made.'
        }
        @{InterfaceGuid=$taskDevice[0].GUID; InterfaceIndex=$taskIndex;
          Dhcp='Enabled'; Addresses=@($taskAddresses.IPAddress); SavedAt=(Get-Date).ToString('o')} |
            ConvertTo-Json | Set-Content -LiteralPath $taskBackup -Encoding UTF8
        New-NetIPAddress -InterfaceIndex $taskIndex -IPAddress '192.168.60.1' -PrefixLength 24 |
            Out-Null
    }
    @{success=$true; action=$(if ($Restore) {'restored DHCP'} else {'configured DJI adapter'});
      interfaceIndex=$taskIndex} | ConvertTo-Json |
        Set-Content -LiteralPath $taskResult -Encoding UTF8
} catch {
    @{success=$false; error=$_.Exception.Message} | ConvertTo-Json |
        Set-Content -LiteralPath $taskResult -Encoding UTF8
    exit 1
}
