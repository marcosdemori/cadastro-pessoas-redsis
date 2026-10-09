@echo off
setlocal
cd /d "%~dp0"

echo ==========================================
echo  Saneamento de Pessoas - Redsis
echo ==========================================
echo.
echo  1 - Interface grafica
echo  2 - Linha de comando
echo  3 - Rodar os testes
echo  4 - Gerar os executaveis (.exe)
echo.

set /p OPCAO="Opcao [1]: "
if "%OPCAO%"=="" set OPCAO=1

where python >nul 2>&1
if errorlevel 1 (
  echo ERRO: Python nao encontrado no PATH.
  echo Instale em https://www.python.org/downloads/ marcando "Add python.exe to PATH".
  pause
  exit /b 1
)

echo.
echo Instalando dependencias...
python -m pip install --quiet requests
if "%OPCAO%"=="4" python -m pip install --quiet pyinstaller pillow

echo.
if "%OPCAO%"=="2" goto CLI
if "%OPCAO%"=="3" goto TESTES
if "%OPCAO%"=="4" goto BUILD

python gui.py
goto FIM

:CLI
python CNPJ.py
goto FIM

:TESTES
python tests\run.py
goto FIM

:BUILD
python build.py
goto FIM

:FIM
echo.
echo ==========================================
echo  FINALIZADO
echo ==========================================
pause
