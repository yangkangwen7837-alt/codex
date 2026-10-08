@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   BigFish 网站启动中：http://localhost:8510
echo   这个窗口保持打开 = 网站运行中
echo   关闭这个窗口 = 网站停止（日常自动运行由计划任务负责，一般不需要用它）
echo ============================================
echo.
if exist "C:\Python314\python.exe" (
  "C:\Python314\python.exe" scripts\start_site.py --port 8510
) else (
  python scripts\start_site.py --port 8510
)
echo.
echo 网站已退出。按任意键关闭窗口。
pause >nul
