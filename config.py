import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Telegram Bot Token
    BOT_TOKEN = os.getenv("BOT_TOKEN", "8239379393:AAEkq-3sH-teAJLRbBjQRo_Jh-4qjIfVNWk")
    
    # MongoDB Configuration
    MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://Test:Test@cluster0.pcpx5.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
    DATABASE_NAME = os.getenv("DATABASE_NAME", "telegram_bot_db")
    
    # Bot Settings
    ADMIN_IDS = [int(id.strip()) for id in os.getenv("ADMIN_IDS", "6872968794").split(",") if id.strip()]
