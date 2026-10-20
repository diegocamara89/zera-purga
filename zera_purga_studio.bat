@echo off
rem zera-purga: atalho para o Bambu Studio (Processo > Outros > Scripts de pos-processamento).
rem O Studio chama este arquivo com o caminho do G-code no fim. A pasta nao pode ter espacos no caminho.
rem Para fixar um Python, preencha PYPY ou PY abaixo; em branco, o atalho procura sozinho.
set "PYPY="
set "PY="
set "SCRIPT=%~dp0scripts\pos_studio.py"

if "%PYPY%"=="" for /d %%D in ("%LOCALAPPDATA%\pypy\pypy3*") do if exist "%%D\pypy3.exe" set "PYPY=%%D\pypy3.exe"
if "%PY%"=="" for %%P in (python.exe) do set "PY=%%~$PATH:P"
if "%PY%"=="" for %%P in (py.exe) do set "PY=%%~$PATH:P"

if "%PYPY%"=="" goto normal
if not exist "%PYPY%" goto normal
"%PYPY%" "%SCRIPT%" %*
set "R=%ERRORLEVEL%"
rem codigo 3 = o PyPy pediu para repetir no Python normal (precisa de numpy)
if not "%R%"=="3" exit /b %R%

:normal
if "%PY%"=="" (
  echo ZERA_PURGA: Python nao encontrado. Instale o Python 3 ^(python.org^) marcando "Add python.exe to PATH".
  pause
  exit /b 1
)
"%PY%" "%SCRIPT%" %*
exit /b %ERRORLEVEL%
