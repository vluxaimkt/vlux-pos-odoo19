@echo off
rem Local Windows helper: lets Odoo browser tests run on Microsoft Edge.
rem   set VLUX_PYTHON=C:\Odoo\venv\Scripts\python.exe   (optional, defaults to "python")
rem   set ODOO_BROWSER_BIN=%CD%\tools\dev\edge_devtools_wrapper.cmd
if "%VLUX_PYTHON%"=="" set "VLUX_PYTHON=python"
"%VLUX_PYTHON%" "%~dp0edge_devtools_wrapper.py" %*
