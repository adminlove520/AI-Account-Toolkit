@echo off
setlocal
set BASE_DIR=%~dp0
pushd "%BASE_DIR%"
"%BASE_DIR%merge-mailtm.exe" %*
set ERR=%ERRORLEVEL%
echo.
echo Process exited with code: %ERR%
if /I not "%MERGE_MAILTM_NO_PAUSE%"=="1" pause
popd
exit /b %ERR%
