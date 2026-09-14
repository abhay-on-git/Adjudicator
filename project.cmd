@echo off
setlocal
set "VENXR_DIR=%~dp0"
python "%VENXR_DIR%project.py" %*
endlocal
