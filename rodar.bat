@echo off
setlocal
cd /d "%~dp0"

echo ==========================================
echo  Saneamento de Pessoas - Redsis
echo ==========================================
echo.
echo  1 - Interface grafica
echo  2 - Linha de comando
echo  3 - Ponte Firebird + index.html no navegador
echo  4 - Rodar os testes
echo  5 - Gerar os executaveis (.exe)
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
if "%OPCAO%"=="3" python -m pip install --quiet firebird-driver
if "%OPCAO%"=="5" python -m pip install --quiet pyinstaller pillow

echo.
if "%OPCAO%"=="2" goto CLI
if "%OPCAO%"=="3" goto PONTE
if "%OPCAO%"=="4" goto TESTES
if "%OPCAO%"=="5" goto BUILD

python gui.py
goto FIM

:CLI
python CNPJ.py
goto FIM

:PONTE
python ponte.py
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
