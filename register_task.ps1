$action = New-ScheduledTaskAction `
    -Execute 'C:\Users\smart\AppData\Local\Programs\Python\Python312\pythonw.exe' `
    -Argument 'scheduler.py' `
    -WorkingDirectory 'C:\dev\job-tracker'

$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit 0

Register-ScheduledTask `
    -TaskName 'JobTrackerScheduler' `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -RunLevel Highest `
    -Force | Out-Null

Start-ScheduledTask -TaskName 'JobTrackerScheduler'
Write-Output 'Task registered and started.'
