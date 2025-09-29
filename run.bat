@echo off
echo Запуск Telegram-бота...
REM Проверим наличие виртуального окружения
IF NOT EXIST venv (
    echo Создаю виртуальное окружение...
    python -m venv venv
)

REM Активируем окружение
call venv\Scripts\activate

REM Устанавливаем зависимости
pip install -r requirements.txt

REM Запускаем бота
python PythonBots\bot.py

pause