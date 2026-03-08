@echo off
set TASK_NAME=JobTrackerScheduler

echo Stopping and removing task: %TASK_NAME%
schtasks /end /tn "%TASK_NAME%" >nul 2>&1
schtasks /delete /tn "%TASK_NAME%" /f

if %ERRORLEVEL% EQU 0 (
    echo Task removed successfully.
) else (
    echo Task not found or already removed.
)
pause
