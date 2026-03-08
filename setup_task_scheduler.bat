@echo off
:: Job Tracker — Windows Task Scheduler setup
:: Run once as Administrator to register the scheduled task.
:: Re-run anytime to update the task.

set TASK_NAME=JobTrackerScheduler
set PYTHON=C:\Users\smart\AppData\Local\Programs\Python\Python312\python.exe
set SCRIPT=C:\dev\job-tracker\scheduler.py
set WORKDIR=C:\dev\job-tracker
set LOGFILE=C:\dev\job-tracker\scheduler.log

echo Registering task: %TASK_NAME%

:: Delete existing task if present (clean update)
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

:: Create task: run at logon, hidden window, log to file
:: Must wrap in cmd /c so redirection operators work inside Task Scheduler
schtasks /create ^
  /tn "%TASK_NAME%" ^
  /tr "cmd /c \"cd /d %WORKDIR% && \"%PYTHON%\" \"%SCRIPT%\" >> \"%LOGFILE%\" 2>&1\"" ^
  /sc ONLOGON ^
  /rl HIGHEST ^
  /f

if %ERRORLEVEL% EQU 0 (
    echo.
    echo Task registered successfully.
    echo.
    echo Starting it now...
    schtasks /run /tn "%TASK_NAME%"
    echo.
    echo Done. Scheduler is running in the background.
    echo Log file: %LOGFILE%
    echo.
    echo Useful commands:
    echo   Check status : schtasks /query /tn "%TASK_NAME%" /fo LIST
    echo   Stop task    : schtasks /end /tn "%TASK_NAME%"
    echo   Start task   : schtasks /run /tn "%TASK_NAME%"
    echo   Remove task  : schtasks /delete /tn "%TASK_NAME%" /f
) else (
    echo.
    echo ERROR: Failed to register task. Try running as Administrator.
)

pause
