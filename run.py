"""AI_Review 启动脚本：python run.py"""
from app.main import app

if __name__ == "__main__":
    # 本地个人工具：仅监听本机
    app.run(host="127.0.0.1", port=8000, debug=False, threaded=True)
