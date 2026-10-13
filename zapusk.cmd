@echo off
rem Запуск бота двойным кликом. Файл должен лежать в папке проекта (рядом с README.md).
chcp 65001 >nul
rem Переходим в папку, где лежит этот файл, откуда бы его ни запустили
cd /d "%~dp0"
echo Папка проекта: %cd%
echo.

rem Блокнот часто сохраняет .env как .env.txt. Исправляем сами.
if not exist ".env" if exist ".env.txt" ren ".env.txt" ".env"
if not exist ".env" goto no_env

rem Ищем Python: сначала команду py, потом python
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY (where python >nul 2>nul && set "PY=python")
if not defined PY goto no_python
%PY% --version

echo Проверяю библиотеки...
%PY% -m pip install -q -r requirements.txt
if errorlevel 1 goto pip_failed

echo.
echo Запускаю бота. Когда появится "Start polling", напишите боту /start в Telegram.
echo Остановить бота: Ctrl+C или закрыть это окно.
echo.
%PY% -m bot.main
echo.
echo Бот остановлен.
pause
exit /b 0

:no_env
echo ОШИБКА: в этой папке нет файла .env
echo Сделайте копию .env.example, назовите её .env и впишите BOT_TOKEN и SOUS_CHEF_ID.
pause
exit /b 1

:no_python
echo ОШИБКА: Python не найден.
echo Установите Python 3.10-3.14 с python.org и отметьте галочку "Add Python to PATH".
pause
exit /b 1

:pip_failed
echo ОШИБКА: не удалось установить библиотеки. Проверьте интернет и пришлите скриншот этого окна.
pause
exit /b 1
