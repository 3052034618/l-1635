@echo off
chcp 65001 > nul
echo ============================================
echo   企业班车智能调度系统 - 后端API
echo ============================================
echo.

if "%1"=="init" (
    echo 正在初始化测试数据...
    python init_data.py
    echo.
    echo 初始化完成！
    goto end
)

if "%1"=="test" (
    echo 正在运行API测试...
    pytest test_api.py -v
    goto end
)

if "%1"=="install" (
    echo 正在安装依赖...
    pip install -r requirements.txt
    echo.
    echo 依赖安装完成！
    goto end
)

echo 启动开发服务器...
echo API文档: http://127.0.0.1:8000/docs
echo.
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

:end
