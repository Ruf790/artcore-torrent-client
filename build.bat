@echo off
rem ArtCore Client: build the .exe (PyInstaller) and the installer (Inno Setup)
rem Needs: Python 3.12 or 3.13 64-bit (python.org), Inno Setup 6.3+ (jrsoftware.org)
cd /d "%~dp0"

python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller || goto :err

python tools\make_icon.py || goto :err

python -m PyInstaller --noconfirm --clean --windowed ^
  --name ArtCoreClient ^
  --icon assets\icon.ico ^
  --version-file version_info.txt ^
  --add-data "assets;assets" ^
  --collect-binaries libtorrent ^
  --collect-data qtawesome ^
  run.py || goto :err

echo.
echo EXE: dist\ArtCoreClient\ArtCoreClient.exe

set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
  echo Inno Setup not found - installer skipped. Install it and run build.bat again.
  goto :done
)
"%ISCC%" setup.iss || goto :err
echo Installer: installer\ArtCoreClient-Setup-*.exe

:done
pause
exit /b 0

:err
echo BUILD FAILED
pause
exit /b 1
