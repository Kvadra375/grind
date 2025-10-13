@echo off
echo ========================================
echo    Hybrid Price Chart - Запуск
echo ========================================
echo.

echo Проверка папки logs...
if not exist "logs" mkdir logs

echo Запуск приложения...
python "main copy 38.py"

echo.
echo Приложение завершено.
pause
