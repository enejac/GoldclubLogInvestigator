Get-PnpDevice | Where-Object { $_.InstanceId -like '*VID_0483*' } | Format-Table FriendlyName,Status,InstanceId -AutoSize
Get-PnpDevice -Class Ports -ErrorAction SilentlyContinue | Where-Object { $_.FriendlyName -match 'COM' } | Format-Table FriendlyName,Status -AutoSize
