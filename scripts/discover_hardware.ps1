Write-Host "--- AMD IPU (NPU) Device Check ---"
Get-PnpDevice -FriendlyName "*AMD IPU*" | Select-Object Status, Class, FriendlyName, InstanceId
Write-Host "--- AMD IPU Driver Version ---"
Get-WmiObject Win32_PnPSignedDriver | Where-Object { $_.DeviceName -like "*AMD IPU*" } | Select-Object DeviceName, Manufacturer, DriverVersion, DriverDate
Write-Host "--- Memory Information ---"
Get-CimInstance Win32_PhysicalMemory | Select-Object Capacity, Speed, ConfiguredClockSpeed, Manufacturer
