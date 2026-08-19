@echo off
setlocal

set "PYTHON=%~dp0wenv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo The project virtual environment was not found:
    echo %PYTHON%
    echo.
    echo Create it or select the correct interpreter, then try again.
    pause
    exit /b 1
)

"%PYTHON%" -m organizer
if errorlevel 1 pause

endlocal
