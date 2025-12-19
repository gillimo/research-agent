@echo off
setlocal
set "ROOT=%~dp0"
python "%ROOT%researcher\cli.py" %*
