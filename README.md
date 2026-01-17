# Scraper Vinted

Aplicación para monitorear productos en Vinted basada en términos de búsqueda específicos.

## Características
- Scraper automático usando Playwright.
- Interfaz web con Streamlit.
- Base de datos SQLite para persistencia.
- Programador de tareas integrado.

## Instalación Local
1. Instalar dependencias: `pip install -r requirements.txt`
2. Instalar navegadores de Playwright: `playwright install chromium`
3. Ejecutar: `streamlit run app.py`

## Despliegue en Easypanel
Este proyecto incluye un `Dockerfile` optimizado para funcionar en Easypanel. Asegúrate de montar un volumen en `/app/data` para persistir la base de datos.
