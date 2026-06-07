import logging
import os
from flask import Flask
from dotenv import load_dotenv

# 从项目根目录加载 .env
_env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
load_dotenv(_env_path)


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)

    # 配置根日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Windows 下自动定位 tesseract（Flask debug reloader 不继承 PATH）
    if os.name == "nt":
        try:
            import pytesseract
            for p in [
                r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            ]:
                if os.path.exists(p):
                    pytesseract.pytesseract.tesseract_cmd = p
                    break
        except ImportError:
            pass

    app.config.from_mapping(
        SECRET_KEY=os.urandom(24).hex(),
        SESSION_DIR=os.path.join(app.root_path, "..", "sessions"),
        UPLOAD_DIR=os.path.join(app.root_path, "..", "uploads"),
        DEEPSEEK_API_KEY=os.environ.get("DEEPSEEK_API_KEY", ""),
        DEEPSEEK_BASE_URL="https://api.deepseek.com/v1",
        VISION_API_KEY=os.environ.get("VISION_API_KEY", ""),
        VISION_BASE_URL=os.environ.get("VISION_BASE_URL", "https://api.siliconflow.cn/v1"),
        VISION_MODEL=os.environ.get("VISION_MODEL", "deepseek-ai/deepseek-vl2"),
        MAX_CONTENT_LENGTH=500 * 1024 * 1024,
    )

    if test_config:
        app.config.update(test_config)

    os.makedirs(app.config["SESSION_DIR"], exist_ok=True)
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)

    from .routes.web import web_bp
    from .routes.api import api_bp

    app.register_blueprint(web_bp)
    app.register_blueprint(api_bp, url_prefix="/api")

    return app
