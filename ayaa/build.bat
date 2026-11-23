@echo off
setlocal
cd /d "%~dp0"
set "MSB="
for %%P in (
  "%ProgramFiles%\Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\MSBuild.exe"
  "%ProgramFiles%\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\MSBuild.exe"
  "%ProgramFiles%\Microsoft Visual Studio\2022\Professional\MSBuild\Current\Bin\MSBuild.exe"
) do if exist %%P set "MSB=%%~P"
if not defined MSB ( echo MSBuild introuvable : installe VS Build Tools. & exit /b 1 )
"%MSB%" drv\aya.vcxproj /p:Configuration=Release /p:Platform=x64 /m /nologo
if errorlevel 1 ( echo ECHEC COMPILATION & exit /b 1 )
copy /y "drv\x64\Release\aya.sys" "aya.sys" >nul
echo OK : aya.sys genere