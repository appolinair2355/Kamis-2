"""
Configuration du bot Telegram de prédiction Baccarat
"""
import os

def parse_channel_id(env_var: str, default: str) -> int:
    value = os.getenv(env_var) or default
    channel_id = int(value)
    # Convertit l'ID positif en format ID de canal Telegram négatif si nécessaire
    if channel_id > 0 and len(str(channel_id)) >= 10:
        channel_id = -channel_id
    return channel_id

# ID du canal source (inchangé)
SOURCE_CHANNEL_ID = parse_channel_id('SOURCE_CHANNEL_ID', '-1002682552255')

# NOUVEL ID DU CANAL DE PRÉDICTION (Baccara B)
PREDICTION_CHANNEL_ID = parse_channel_id('PREDICTION_CHANNEL_ID', '-1003450873158')

ADMIN_ID = int(os.getenv('ADMIN_ID') or '0')

API_ID = int(os.getenv('API_ID') or '0')
API_HASH = os.getenv('API_HASH') or ''
BOT_TOKEN = os.getenv('BOT_TOKEN') or ''

PORT = int(os.getenv('PORT') or '5000')  # Port 5000 for Replit

# NOUVEAU MAPPING : Échange des enseignes de même couleur (Noir/Noir et Rouge/Rouge)
# Note : Les variantes multiples (♠️, ♥, etc.) du mapping précédent ont été retirées 
# pour simplifier, car elles sont gérées par SUIT_DISPLAY et ALL_SUITS.
SUIT_MAPPING = {
    '♠': '♣',  # Pique manque -> Prédit Trèfle
    '♣': '♠',  # Trèfle manque -> Prédit Pique
    '♥': '♦',  # Cœur manque -> Prédit Carreau
    '♦': '♥',  # Carreau manque -> Prédit Cœur
}

ALL_SUITS = ['♠', '♥', '♦', '♣']
SUIT_DISPLAY = {
    '♠': '♠️',
    '♥': '❤️',
    '♦': '♦️',
    '♣': '♣️'
}
