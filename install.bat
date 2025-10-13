@echo off
echo ========================================
echo    Hybrid Price Chart - Установка
echo ========================================
echo.

echo [1/4] Создание папки logs...
if not exist "logs" mkdir logs
echo ✓ Папка logs создана

echo.
echo [2/4] Установка зависимостей...
pip install websocket-client matplotlib numpy requests beautifulsoup4 pyperclip
echo ✓ Зависимости установлены

echo.
echo [3/4] Проверка файлов...
if exist "main copy 38.py" (
    echo ✓ Основной файл найден
) else (
    echo ✗ Файл main copy 38.py не найден!
    pause
    exit
)

if exist "tokens.json" (
    echo ✓ Конфигурация токенов найдена
) else (
    echo ✗ Файл tokens.json не найден!
    pause
    exit
)

echo.
echo [4/4] Запуск приложения...
echo ========================================
echo    Запуск Hybrid Price Chart...
echo ========================================
echo.

python "main copy 38.py"

echo.
echo Приложение завершено.
pause
