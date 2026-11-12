# Inregistreaza rularea automata a monitorului (Windows Scheduled Task): la fiecare N ore, 24/7 non-stop.
#   powershell -ExecutionPolicy Bypass -File register_task.ps1                 # la fiecare 1 ora, non-stop 24/7
#   powershell -ExecutionPolicy Bypass -File register_task.ps1 -EveryHours 3   # la fiecare 3 ore
#   powershell -ExecutionPolicy Bypass -File register_task.ps1 -Remove         # sterge task-ul
# Iesirea fiecarei rulari se adauga in out\task.log. O rulare noua nu porneste daca alta e activa (lock + IgnoreNew).
param([string]$Time = "00:00", [double]$EveryHours = 1.0, [switch]$Continuous = $true, [switch]$Remove)

$name = "AdsMonitor"
if ($Remove) { Unregister-ScheduledTask -TaskName $name -Confirm:$false; "Sters: $name"; exit }

$root = $PSScriptRoot
New-Item -ItemType Directory -Force -Path (Join-Path $root "out") | Out-Null
$cmd = "set PYTHONIOENCODING=utf-8&& `"$root\.venv\Scripts\python.exe`" run.py --target prod >> out\task.log 2>&1"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c $cmd" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME   # si imediat dupa logare (dupa restart/pana de curent)
$interval = New-TimeSpan -Minutes ([int]($EveryHours * 60))
$duration = if ($Continuous) { New-TimeSpan -Days 1 } else { New-TimeSpan -Hours 12 }
$rep = (New-ScheduledTaskTrigger -Once -At $Time -RepetitionInterval $interval -RepetitionDuration $duration).Repetition
$trigger.Repetition = $rep
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3) -MultipleInstances IgnoreNew `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $name -Action $action -Trigger @($trigger, $logon) -Settings $settings `
    -Description "FB Ads Library -> AliExpress 1-10 lei -> WhatsApp Ponturi Ecom (Non-stop 24/7)" -Force | Out-Null
"Inregistrat: $name, rulare la fiecare $EveryHours ore, NON-STOP 24/7. Log: out\task.log"

