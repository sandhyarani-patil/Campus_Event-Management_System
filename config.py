import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'your-secret-key-here')
    
    # MySQL configuration
    MYSQL_HOST = os.getenv('DB_HOST', 'localhost')
    MYSQL_USER = os.getenv('DB_USER', 'root')
    MYSQL_PASSWORD = os.getenv('DB_PASSWORD', '')
    MYSQL_DB = os.getenv('DB_NAME', 'campus_events')
    MYSQL_CURSORCLASS = 'DictCursor'

DB_CONFIG = {
    'host': Config.MYSQL_HOST,
    'user': Config.MYSQL_USER,
    'password': Config.MYSQL_PASSWORD,
    'database': Config.MYSQL_DB
}