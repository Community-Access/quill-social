@echo off
rem Run QUILL Social from source on Windows.
rem Usage: run-quill-social.bat
setlocal
set QUILLSOCIAL_DATA=%~dp0data
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0launcher.py" %*
) else (
    python "%~dp0launcher.py" %*
)
endlocal
