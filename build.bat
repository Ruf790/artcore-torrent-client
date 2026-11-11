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
  --collect-submodules windows_toasts --collect-submodules winrt ^
  --exclude-module PySide6.QtQml ^
  --exclude-module PySide6.QtQuick ^
  --exclude-module PySide6.QtQuickWidgets ^
  --exclude-module PySide6.QtQuick3D ^
  --exclude-module PySide6.QtQuickControls2 ^
  --exclude-module PySide6.QtOpenGL ^
  --exclude-module PySide6.QtOpenGLWidgets ^
  --exclude-module PySide6.QtPdf ^
  --exclude-module PySide6.QtPdfWidgets ^
  --exclude-module PySide6.QtDataVisualization ^
  --exclude-module PySide6.QtCharts ^
  --exclude-module PySide6.QtGraphs ^
  --exclude-module PySide6.QtWebEngineCore ^
  --exclude-module PySide6.QtWebEngineWidgets ^
  --exclude-module PySide6.QtWebChannel ^
  --exclude-module PySide6.QtWebSockets ^
  --exclude-module PySide6.QtMultimedia ^
  --exclude-module PySide6.QtMultimediaWidgets ^
  --exclude-module PySide6.QtSql ^
  --exclude-module PySide6.QtTest ^
  --exclude-module PySide6.QtDesigner ^
  --exclude-module PySide6.QtHelp ^
  --exclude-module PySide6.QtBluetooth ^
  --exclude-module PySide6.QtPositioning ^
  --exclude-module PySide6.QtLocation ^
  --exclude-module PySide6.QtSensors ^
  --exclude-module PySide6.QtSerialPort ^
  --exclude-module PySide6.QtSvgWidgets ^
  --exclude-module PySide6.Qt3DCore ^
  --exclude-module PySide6.QtScxml ^
  --exclude-module PySide6.QtStateMachine ^
  --exclude-module PySide6.QtTextToSpeech ^
  --exclude-module PySide6.QtRemoteObjects ^
  --exclude-module PySide6.QtNfc ^
  --exclude-module PySide6.QtSpatialAudio ^
  --exclude-module PySide6.QtHttpServer ^
  run.py || goto :err

python tools\trim_build.py dist\ArtCoreClient || goto :err

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
