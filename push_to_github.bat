@echo off
title Push Intermarket Curves Intelligence to GitHub
echo =======================================================================
echo Pushing Intermarket Curves Repository to GitHub:
echo URL: https://github.com/ayan-choradia/inter-market.git
echo Branch: main
echo =======================================================================
echo.

"%LOCALAPPDATA%\Programs\MinGit\cmd\git.exe" push -u origin main

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ===================================================================
    echo [SUCCESS] Successfully pushed all files to GitHub!
    echo URL: https://github.com/ayan-choradia/inter-market
    echo ===================================================================
) else (
    echo.
    echo ===================================================================
    echo [AUTH REQUIRED] If prompted for credentials:
    echo   Username: ayan-choradia
    echo   Password: Use your GitHub Personal Access Token (PAT)
    echo             (Generate at: https://github.com/settings/tokens)
    echo ===================================================================
)

echo.
pause
