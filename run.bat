@echo off
echo ==========================================
echo    Vinted Scraper - Local Runner
echo ==========================================

:: Comprobar si Python existe
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] No se detecta Python.
    echo 1. Instala Python desde https://www.python.org/downloads/
    echo 2. IMPORTANTE: Marca la casilla "Add Python to PATH" al instalar.
    echo.
    pause
    exit /b
)

if not exist venv (
    echo [1/4] Creando entorno virtual (venv)...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [ERROR] Fallo al crear venv.
        pause
        exit /b
    )
)

echo [2/4] Activando venv...
call venv\Scripts\activate

echo [3/4] Instalando dependencias...
pip install -r requirements.txt
echo Verificando Playwright...
playwright install chromium

echo [4/4] Iniciando App...
echo SI SE ABRE EL NAVEGADOR: No lo cierres, dejalo ejecutando.
streamlit run app.py

pause
