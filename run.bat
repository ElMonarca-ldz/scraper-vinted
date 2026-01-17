@echo off
setlocal enabledelayedexpansion

echo ==========================================
echo    Vinted Scraper - Local Runner
echo ==========================================

:: 1. CHECK PYTHON
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] No se detecta Python.
    echo 1. Instala Python desde https://www.python.org/downloads/
    echo 2. IMPORTANTE: Marca la casilla "Add Python to PATH".
    pause
    exit /b
)

:: 2. CREATE VENV IF MISSING
if not exist venv (
    echo [1/4] Creando entorno virtual...
    python -m venv venv
)

:: Check if venv creation worked (folder must exist)
if not exist venv (
    echo [ERROR] Fallo al crear la carpeta venv.
    pause
    exit /b
)

:: 3. ACTIVATE
echo [2/4] Activando venv...
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo [ERROR] No se pudo activar venv.
    pause
    exit /b
)

:: 4. INSTALL DEPS
echo [3/4] Instalando dependencias...
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Fallo al instalar librerias.
    pause
    exit /b
)

echo Verificando Playwright...
playwright install chromium

:: 5. RUN
echo [4/4] Iniciando App...
echo SI SE ABRE EL NAVEGADOR: No lo cierres, dejalo ejecutando.
streamlit run app.py

pause
