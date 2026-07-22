@echo off
setlocal
set "PYTHON_EXE=C:\Users\bruno\AppData\Roaming\uv\python\cpython-3.14.6-windows-x86_64-none\python.exe"

if exist "%PYTHON_EXE%" (
  "%PYTHON_EXE%" "%~dp0run_demo.py" %*
) else (
  echo Python interpreter not found at "%PYTHON_EXE%".
  exit /b 1
)
