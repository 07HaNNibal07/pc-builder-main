@echo off
rem Один тик планировщика сбора (collector\schedule.py) — для Планировщика заданий Windows.
rem Регистрация раз в час, только пока пользователь вошёл в систему (Chrome должен быть видимым):
rem   schtasks /Create /TN "pcb-collect" /SC HOURLY /TR "F:\Claude\pc-builder\scripts\collect_tick.bat"
rem Снять: schtasks /Delete /TN "pcb-collect" /F
rem VPN на Windows закрывает площадки — при нём тики будут записывать отказы.
cd /d "%~dp0.."
.venv-win\Scripts\python.exe -m collector.schedule >> recon\schedule.log 2>&1
