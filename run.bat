@echo off
echo ==========================================
echo    Vinted Scraper - Local Runner
echo ==========================================

if not exist venv (
    echo [1/4] Creando entorno virtual (venv)...
    python -m venv venv
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
