@echo off
rem Builds dist\cms50f-download.exe, a single file that runs without Python.
rem Needs Python for Windows (the "py" launcher) to build it.

py -m pip install --upgrade hidapi pyinstaller || goto :error
py -m PyInstaller --onefile --console --name cms50f-download cms50f_download.py || goto :error

echo.
echo Done: dist\cms50f-download.exe
goto :eof

:error
echo.
echo Build failed.
exit /b 1
