import os

# Fichier de configuration pour le Bot de Prédiction Baccarat

# --- Paramètres de Connexion Telegram (Chargement depuis les variables d'environnement) ---
# Les valeurs statiques ci-dessous servent de 'placeholders' ou de valeurs par défaut
# si les variables d'environnement ne sont pas définies.
API_ID = int(os.getenv('API_ID', 1234567))
API_HASH = os.getenv('API_HASH', 'VOTRE_API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN', 'VOTRE_BOT_TOKEN')

# Votre ID utilisateur Telegram (pour recevoir les transferts et utiliser les commandes admin)
ADMIN_ID = int(os.getenv('ADMIN_ID', 1190237801))

# --- Canaux ---
# L'ID du canal source d'où proviennent les messages (ID de supergroupe doit être négatif)
SOURCE_CHANNEL_ID = int(os.getenv('SOURCE_CHANNEL_ID', -1002682552255))
# L'ID du canal de destination où les prédictions sont envoyées (ID de supergroupe doit être négatif)
PREDICTION_CHANNEL_ID = int(os.getenv('PREDICTION_CHANNEL_ID', -1003450873158))

# --- Paramètres du Serveur Web (pour le health check) ---
PORT = int(os.getenv('PORT', 10000))

# --- Logique de Prédiction Baccarat ---

# Toutes les couleurs utilisées (symboles normalisés)
ALL_SUITS = ['♠', '♣', '♥', '♦']

# MAPPING CRITIQUE : Manquante (KEY) -> Prédite (VALUE)
# Ce mapping utilise UNIQUEMENT les symboles normalisés, car le code principal
# gère la conversion des emojis ('❤️', '♠️', etc.) vers ces symboles.
SUIT_MAPPING = {
    '♠': '♣',  # Pique manque -> Prédit Trèfle (Votre logique: ♠️ prédit ♣️)
    '♣': '♠',  # Trèfle manque -> Prédit Pique (Votre logique: ♣️ prédit ♠️)
    '♥': '♦',  # Cœur manque -> Prédit Carreau (Votre logique: ❤️ prédit ♦️)
    '♦': '♥',  # Carreau manque -> Prédit Cœur (Votre logique: ♦️ prédit ❤️)
}

# Affichage des couleurs dans les messages (pour info)
# Mappe le symbole normalisé vers la version emoji pour l'affichage Telegram.
SUIT_DISPLAY = {
    '♠': '♠️',
    '♣': '♣️',
    '♥': '❤️',
    '♦': '♦️',
}
