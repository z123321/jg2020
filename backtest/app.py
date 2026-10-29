from flask import Flask
from flask_cors import CORS
from config import Config

from auth import auth_bp
from strategies import strategies_bp
from templates import templates_bp
from backtest import backtest_bp
from payment import payment_bp

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    CORS(app)

    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(strategies_bp, url_prefix="/api/strategies")
    app.register_blueprint(templates_bp, url_prefix="/api/templates")
    app.register_blueprint(backtest_bp, url_prefix="/api/backtest")
    app.register_blueprint(payment_bp, url_prefix="/api/payment")

    return app

if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, port=5000)