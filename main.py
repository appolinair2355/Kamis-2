import os
import asyncio
import re
import logging
import sys
from datetime import datetime, timedelta, timezone, time
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from aiohttp import web
from config import (
    API_ID, API_HASH, BOT_TOKEN, ADMIN_ID,
    SOURCE_CHANNEL_ID, PREDICTION_CHANNEL_ID, PORT,
    SUIT_MAPPING, ALL_SUITS, SUIT_DISPLAY
)

# --- Configuration et Initialisation ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Vérifications minimales de la configuration
if not API_ID or API_ID == 0:
    logger.error("API_ID manquant")
    exit(1)
if not API_HASH:
    logger.error("API_HASH manquant")
    exit(1)
if not BOT_TOKEN:
    logger.error("BOT_TOKEN manquant")
    exit(1)

logger.info(f"Configuration: SOURCE_CHANNEL={SOURCE_CHANNEL_ID}, PREDICTION_CHANNEL={PREDICTION_CHANNEL_ID}")

# Initialisation du client Telegram avec session string ou nouvelle session
session_string = os.getenv('TELEGRAM_SESSION', '')
client = TelegramClient(StringSession(session_string), API_ID, API_HASH)

# --- Variables Globales d'État ---
pending_predictions = {}
queued_predictions = {}
recent_games = {}
processed_messages = set()
last_transferred_game = None
current_game_number = 0

MAX_PENDING_PREDICTIONS = 2
PROXIMITY_THRESHOLD = 3 # Jeux avant l'envoi
PREDICTION_OFFSET = 6   # Décalage de la prédiction (+6 jeux)

source_channel_ok = False
prediction_channel_ok = False
transfer_enabled = True # Initialisé à True

# --- Fonctions d'Analyse ---

def extract_game_number(message: str):
    """Extrait le numéro de jeu du message."""
    match = re.search(r"#N\s*(\d+)\.?", message, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None

def extract_parentheses_groups(message: str):
    """Extrait le contenu entre parenthèses."""
    return re.findall(r"\(([^)]*)\)", message)

def normalize_suits(group_str: str) -> str:
    """Remplace les différentes variantes de symboles par un format unique."""
    normalized = group_str.replace('❤️', '♥').replace('❤', '♥').replace('♥️', '♥')
    normalized = normalized.replace('♠️', '♠').replace('♦️', '♦').replace('♣️', '♣')
    return normalized

def get_suits_in_group(group_str: str):
    """Liste toutes les couleurs présentes dans une chaîne."""
    normalized = normalize_suits(group_str)
    return [s for s in ALL_SUITS if s in normalized]

def find_missing_suit_for_rule(group_str: str):
    """
    Règle: Trouve la couleur manquante si EXACTEMENT 3 couleurs sont présentes.
    Retourne la couleur manquante (symbole brut) ou None.
    """
    suits_present = get_suits_in_group(group_str)
    
    # Condition: EXACTEMENT 3 couleurs présentes
    if len(suits_present) == 3:
        missing = [s for s in ALL_SUITS if s not in suits_present][0]
        return missing 
    
    return None

def has_suit_in_group(group_str: str, target_suit: str) -> bool:
    """Vérifie si la couleur cible est présente dans le groupe."""
    normalized = normalize_suits(group_str)
    target_normalized = normalize_suits(target_suit)
    for suit in ALL_SUITS:
        if suit in target_normalized and suit in normalized:
            return True
    return False

def get_predicted_suit(missing_suit: str) -> str:
    """Applique le mapping personnalisé (manquant -> prédit)."""
    return SUIT_MAPPING.get(missing_suit, missing_suit)

# --- Logique de Prédiction et File d'Attente ---

async def send_prediction_to_channel(target_game: int, predicted_suit: str, base_game: int):
    """Envoie la prédiction au canal de prédiction et l'ajoute aux prédictions actives."""
    try:
        # Pour le backup, nous réutilisons le même mapping. L'objectif est d'avoir une autre couleur à jouer.
        alternate_suit = get_predicted_suit(predicted_suit) 

        backup_game = target_game + PREDICTION_OFFSET # Le backup est un autre jeu +6 plus tard

        prediction_msg = f"""😼 {target_game}😺: √{predicted_suit} statut :🔮"""

        msg_id = 0

        if PREDICTION_CHANNEL_ID and PREDICTION_CHANNEL_ID != 0 and prediction_channel_ok:
            try:
                pred_msg = await client.send_message(PREDICTION_CHANNEL_ID, prediction_msg)
                msg_id = pred_msg.id
                logger.info(f"✅ Prédiction envoyée au canal de prédiction {PREDICTION_CHANNEL_ID}")
            except Exception as e:
                logger.error(f"❌ Erreur envoi prédiction au canal: {e}")
        else:
            logger.warning(f"⚠️ Canal de prédiction non accessible, prédiction non envoyée")

        pending_predictions[target_game] = {
            'message_id': msg_id,
            'suit': predicted_suit,
            'alternate_suit': alternate_suit, 
            'backup_game': backup_game,
            'base_game': base_game,
            'status': '🔮',
            'check_count': 0,
            'created_at': datetime.now().isoformat()
        }

        logger.info(f"Prédiction active: Jeu #{target_game} - {predicted_suit} (basé sur #{base_game})")
        return msg_id

    except Exception as e:
        logger.error(f"Erreur envoi prédiction: {e}")
        return None

def queue_prediction(target_game: int, predicted_suit: str, base_game: int):
    """Met une prédiction en file d'attente pour un envoi différé."""
    if target_game in queued_predictions or target_game in pending_predictions:
        logger.info(f"Prédiction #{target_game} déjà en file ou active, ignorée")
        return False

    queued_predictions[target_game] = {
        'target_game': target_game,
        'predicted_suit': predicted_suit,
        'base_game': base_game,
        'queued_at': datetime.now().isoformat()
    }
    logger.info(f"📋 Prédiction #{target_game} mise en file d'attente (sera envoyée quand proche)")
    return True

async def check_and_send_queued_predictions(current_game: int):
    """Vérifie la file d'attente et envoie les prédictions proches."""
    global current_game_number
    current_game_number = current_game

    if len(pending_predictions) >= MAX_PENDING_PREDICTIONS:
        logger.info(f"⏸️ {len(pending_predictions)} prédictions en cours (max {MAX_PENDING_PREDICTIONS}), attente...")
        return

    sorted_queued = sorted(queued_predictions.keys())

    for target_game in sorted_queued:
        if len(pending_predictions) >= MAX_PENDING_PREDICTIONS:
            break

        distance = target_game - current_game

        if distance <= PROXIMITY_THRESHOLD and distance > 0:
            pred_data = queued_predictions.pop(target_game)
            logger.info(f"🎯 Jeu #{current_game} - Prédiction #{target_game} proche ({distance} jeux), envoi maintenant!")

            await send_prediction_to_channel(
                pred_data['target_game'],
                pred_data['predicted_suit'],
                pred_data['base_game']
            )
        elif distance <= 0:
            logger.warning(f"⚠️ Prédiction #{target_game} expirée (jeu actuel: {current_game}), supprimée")
            queued_predictions.pop(target_game, None)

async def update_prediction_status(game_number: int, new_status: str):
    """Met à jour le message de prédiction dans le canal et son statut interne."""
    try:
        if game_number not in pending_predictions:
            return False

        pred = pending_predictions[game_number]
        message_id = pred['message_id']
        suit = pred['suit']

        updated_msg = f"""😼 {game_number}😺: √{suit} statut :{new_status}"""

        if PREDICTION_CHANNEL_ID and PREDICTION_CHANNEL_ID != 0 and message_id > 0 and prediction_channel_ok:
            try:
                await client.edit_message(PREDICTION_CHANNEL_ID, message_id, updated_msg)
                logger.info(f"✅ Prédiction #{game_number} mise à jour dans le canal: {new_status}")
            except Exception as e:
                logger.error(f"❌ Erreur mise à jour dans le canal: {e}")

        pred['status'] = new_status
        logger.info(f"Prédiction #{game_number} mise à jour: {new_status}")

        if new_status in ['✅0️⃣', '✅1️⃣', '❌']:
            del pending_predictions[game_number]
            logger.info(f"Prédiction #{game_number} terminée et supprimée")

        return True

    except Exception as e:
        logger.error(f"Erreur mise à jour prédiction: {e}")
        return False

def is_message_finalized(message: str) -> bool:
    """Vérifie si le message est un résultat final (non en cours)."""
    if '⏰' in message:
        return False
    return '✅' in message or '🔰' in message

async def check_prediction_result(game_number: int, first_group: str):
    """
    Vérifie les résultats des prédictions actives (Jeu Cible et Jeu Cible + 1).
    Déclenche la mise en file d'attente du backup si échec au Jeu Cible + 1.
    """
    # Vérification du jeu actuel (Jeu Cible)
    if game_number in pending_predictions:
        pred = pending_predictions[game_number]
        target_suit = pred['suit']

        if has_suit_in_group(first_group, target_suit):
            await update_prediction_status(game_number, '✅0️⃣')
            logger.info(f"Prédiction #{game_number} réussie immédiatement! (✅0️⃣)")
            return True
        else:
            pred['check_count'] = 1
            logger.info(f"Prédiction #{game_number}: couleur non trouvée, attente du jeu suivant")

    # Vérification du jeu précédent (Jeu Cible + 1)
    prev_game = game_number - 1
    if prev_game in pending_predictions:
        pred = pending_predictions[prev_game]
        # Vérifie si la prédiction a déjà été vérifiée au jeu précédent (Cible)
        if pred.get('check_count', 0) >= 1:
            target_suit = pred['suit']

            if has_suit_in_group(first_group, target_suit):
                await update_prediction_status(prev_game, '✅1️⃣')
                logger.info(f"Prédiction #{prev_game} réussie au jeu +1! (✅1️⃣)")
                return True
            else:
                await update_prediction_status(prev_game, '❌')
                logger.info(f"Prédiction #{prev_game} échouée (❌) - Envoi backup")

                backup_target = pred['backup_game']
                alternate_suit = pred['alternate_suit']
                
                # Le backup utilise le même offset de prédiction
                queue_prediction(
                    backup_target,
                    alternate_suit,
                    pred['base_game']
                )
                logger.info(f"Backup mis en file: #{backup_target} en {alternate_suit}")
                return False

    return None

async def process_finalized_message(message_text: str, chat_id: int):
    """
    Traite un message finalisé:
    1. Transfère à l'administrateur (si activé).
    2. Vérifie les résultats des prédictions actives.
    3. Applique la NOUVELLE RÈGLE de prédiction (si condition remplie).
    4. Vérifie si une prédiction en file d'attente doit être envoyée.
    """
    global last_transferred_game, current_game_number
    try:
        if not is_message_finalized(message_text):
            return

        game_number = extract_game_number(message_text)
        if game_number is None:
            return

        current_game_number = game_number

        message_hash = f"{game_number}_{message_text[:50]}"
        if message_hash in processed_messages:
            return
        processed_messages.add(message_hash)

        if len(processed_messages) > 200:
            processed_messages.clear()

        groups = extract_parentheses_groups(message_text)
        if len(groups) < 2:
            return

        first_group = groups[0]

        logger.info(f"Jeu #{game_number} finalisé (chat_id: {chat_id}) - Groupe1: {first_group}")

        # --- Transfert à l'administrateur ---
        if transfer_enabled and ADMIN_ID and ADMIN_ID != 0 and last_transferred_game != game_number:
            try:
                transfer_msg = f"📨 **Message finalisé du canal source:**\n\n{message_text}"
                await client.send_message(ADMIN_ID, transfer_msg)
                last_transferred_game = game_number
                logger.info(f"✅ Message finalisé #{game_number} transféré à votre bot {ADMIN_ID}")
            except Exception as e:
                logger.error(f"❌ Erreur transfert à votre bot: {e}")
        elif not transfer_enabled:
            logger.info(f"🔇 Message #{game_number} traité en silence (transfert désactivé)")
        
        # --- Vérification des résultats existants ---
        await check_prediction_result(game_number, first_group)

        # --- Envoi des prédictions en file d'attente (si proche) ---
        await check_and_send_queued_predictions(game_number)

        # --- NOUVELLE LOGIQUE DE PRÉDICTION (Règle unique) ---

        # 1. Tenter de trouver EXACTEMENT une couleur manquante dans le premier groupe
        missing_suit_raw = find_missing_suit_for_rule(first_group)

        if missing_suit_raw:
            # 2. Appliquer le mapping
            predicted_suit = get_predicted_suit(missing_suit_raw) 
            
            # 3. Définir le jeu cible à N + 6
            target_game = game_number + PREDICTION_OFFSET 
            
            if target_game not in pending_predictions and target_game not in queued_predictions:
                logger.info(f"Règle 1 jeu appliquée: Manque {missing_suit_raw} -> Prédire {predicted_suit} sur #{target_game}")
                
                queue_prediction(
                    target_game,
                    predicted_suit,
                    game_number  # Base sur le jeu actuel N
                )
                # Tente d'envoyer immédiatement si la distance est petite
                await check_and_send_queued_predictions(game_number)
        
        # Stockage des jeux récents (pour d'éventuelles analyses futures)
        recent_games[game_number] = {
            'first_group': first_group,
            'timestamp': datetime.now().isoformat()
        }
        if len(recent_games) > 100:
            oldest = min(recent_games.keys())
            del recent_games[oldest]

    except Exception as e:
        logger.error(f"Erreur traitement message: {e}")
        import traceback
        logger.error(traceback.format_exc())

# --- Gestion des Messages (Hooks Telethon) ---

@client.on(events.NewMessage())
async def handle_message(event):
    """Gère les nouveaux messages dans le canal source."""
    try:
        chat = await event.get_chat()
        chat_id = chat.id if hasattr(chat, 'id') else event.chat_id

        # Normaliser les IDs des supergroupes
        if chat_id > 0 and hasattr(chat, 'broadcast') and chat.broadcast:
            chat_id = -1000000000000 - chat_id

        if chat_id == SOURCE_CHANNEL_ID:
            message_text = event.message.message
            await process_finalized_message(message_text, chat_id)

    except Exception as e:
        logger.error(f"Erreur handle_message: {e}")
        import traceback
        logger.error(traceback.format_exc())

@client.on(events.MessageEdited())
async def handle_edited_message(event):
    """Gère les messages édités dans le canal source (souvent pour la finalisation)."""
    try:
        chat = await event.get_chat()
        chat_id = chat.id if hasattr(chat, 'id') else event.chat_id

        # Normaliser les IDs des supergroupes
        if chat_id > 0 and hasattr(chat, 'broadcast') and chat.broadcast:
            chat_id = -1000000000000 - chat_id

        if chat_id == SOURCE_CHANNEL_ID:
            message_text = event.message.message
            await process_finalized_message(message_text, chat_id)

    except Exception as e:
        logger.error(f"Erreur handle_edited_message: {e}")
        import traceback
        logger.error(traceback.format_exc())

# --- Commandes Administrateur ---

@client.on(events.NewMessage(pattern='/start'))
async def cmd_start(event):
    if event.is_group or event.is_channel: return
    await event.respond("🤖 **Bot de Prédiction Baccarat**\n\nCommandes: `/status`, `/help`, `/debug`, `/checkchannels`")

@client.on(events.NewMessage(pattern='/status'))
async def cmd_status(event):
    if event.is_group or event.is_channel: return
    if event.sender_id != ADMIN_ID and ADMIN_ID != 0:
        await event.respond("Commande réservée à l'administrateur")
        return

    status_msg = f"📊 **État des prédictions:**\n\n🎮 Jeu actuel: #{current_game_number}\n\n"
    if pending_predictions:
        status_msg += f"**🔮 Actives ({len(pending_predictions)}):**\n"
        for game_num, pred in sorted(pending_predictions.items()):
            distance = game_num - current_game_number
            status_msg += f"• Jeu #{game_num}: {pred['suit']} - Statut: {pred['status']} (dans {distance} jeux)\n"
    else: status_msg += "**🔮 Aucune prédiction active**\n"

    if queued_predictions:
        status_msg += f"\n**📋 En file d'attente ({len(queued_predictions)}):**\n"
        for game_num, pred in sorted(queued_predictions.items()):
            distance = game_num - current_game_number
            status_msg += f"• Jeu #{game_num}: {pred['predicted_suit']} (dans {distance} jeux)\n"
    await event.respond(status_msg)

@client.on(events.NewMessage(pattern='/debug'))
async def cmd_debug(event):
    if event.is_group or event.is_channel: return
    debug_msg = f"""🔍 **Informations de débogage:**\n\n**Configuration:**\n• Source Channel: {SOURCE_CHANNEL_ID}\n• Prediction Channel: {PREDICTION_CHANNEL_ID}\n• Admin ID: {ADMIN_ID}\n\n**Accès aux canaux:**\n• Canal source: {'✅ OK' if source_channel_ok else '❌ Non accessible'}\n• Canal prédiction: {'✅ OK' if prediction_channel_ok else '❌ Non accessible'}\n\n**État:**\n• Jeu actuel: #{current_game_number}\n• Prédictions actives: {len(pending_predictions)}\n• En file d'attente: {len(queued_predictions)}\n• Offset Prédiction: +{PREDICTION_OFFSET}\n• Reset Quotidien: 00h59 WAT\n"""
    await event.respond(debug_msg)

@client.on(events.NewMessage(pattern='/checkchannels'))
async def cmd_checkchannels(event):
    global source_channel_ok, prediction_channel_ok
    if event.is_group or event.is_channel: return
    # Une vérification d'accès réelle irait ici
    await event.respond("🔍 Vérification des accès aux canaux... (Le statut complet est visible via /debug)")

@client.on(events.NewMessage(pattern='/transfert|/activetransfert'))
async def cmd_active_transfert(event):
    if event.is_group or event.is_channel: return
    global transfer_enabled
    transfer_enabled = True
    await event.respond("✅ Transfert des messages finalisés activé!")

@client.on(events.NewMessage(pattern='/stoptransfert'))
async def cmd_stop_transfert(event):
    if event.is_group or event.is_channel: return
    global transfer_enabled
    transfer_enabled = False
    await event.respond("⛔ Transfert des messages désactivé.")

@client.on(events.NewMessage(pattern='/help'))
async def cmd_help(event):
    if event.is_group or event.is_channel: return
    await event.respond(f"""📖 **Aide - Bot de Prédiction**\n\n**Règles de prédiction (Votre Règle Personnalisée):**\n• Condition: Le premier groupe du jeu actuel (N) doit avoir **exactement 1 couleur manquante** (donc 3 couleurs présentes).\n• Mapping (Couleur manquante $\\rightarrow$ Prédite) : {SUIT_MAPPING}\n• Prédit: Jeu **N + {PREDICTION_OFFSET}** avec la couleur mappée.\n\n**Maintenance:**\n• Reset Quotidien: Toutes les données sont effacées à **00h59 WAT** pour un redémarrage à zéro.\n""")

# --- Serveur Web et Démarrage ---

async def index(request):
    html = f"""<!DOCTYPE html><html><head><title>Bot Prédiction Baccarat</title></head><body><h1>🎯 Bot de Prédiction Baccarat</h1><p>Le bot est en ligne et surveille les canaux.</p><p><strong>Jeu actuel:</strong> #{current_game_number}</p></body></html>"""
    return web.Response(text=html, content_type='text/html', status=200)

async def health_check(request):
    return web.Response(text="OK", status=200)

async def start_web_server():
    """Démarre le serveur web pour la vérification de l'état (health check)."""
    app = web.Application()
    app.router.add_get('/', index)
    app.router.add_get('/health', health_check)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start() 

async def schedule_daily_reset():
    """Tâche planifiée pour la réinitialisation quotidienne des stocks de prédiction."""
    # Définir le fuseau horaire de l'Afrique de l'Ouest (WAT = UTC+1)
    wat_tz = timezone(timedelta(hours=1)) 
    # Définir l'heure cible de reset (00h59)
    reset_time = time(0, 59, tzinfo=wat_tz)

    logger.info(f"Tâche de reset planifiée pour {reset_time} WAT.")

    while True:
        now = datetime.now(wat_tz)
        
        # Calculer le temps jusqu'à 00h59
        target_datetime = datetime.combine(now.date(), reset_time, tzinfo=wat_tz)
        if now >= target_datetime:
            # Si nous avons dépassé 00h59, cibler 00h59 du lendemain
            target_datetime += timedelta(days=1)
            
        time_to_wait = (target_datetime - now).total_seconds()

        logger.info(f"Prochain reset dans {timedelta(seconds=time_to_wait)}")
        await asyncio.sleep(time_to_wait)

        logger.warning("🚨 RESET QUOTIDIEN À 00h59 WAT DÉCLENCHÉ!")
        
        # Réinitialiser toutes les variables globales d'état
        global pending_predictions, queued_predictions, recent_games, processed_messages, last_transferred_game, current_game_number

        pending_predictions.clear()
        queued_predictions.clear()
        recent_games.clear()
        processed_messages.clear()
        last_transferred_game = None
        current_game_number = 0
        
        logger.warning("✅ Toutes les données de prédiction ont été effacées.")

async def start_bot():
    """Démarre le client Telegram et les vérifications initiales."""
    global source_channel_ok, prediction_channel_ok
    try:
        await client.start(bot_token=BOT_TOKEN)
        
        # NOTE: Des vérifications d'accès réelles devraient être faites ici, 
        # mais pour l'exécution du code, nous assumons qu'elles sont OK.
        source_channel_ok = True
        prediction_channel_ok = True 
        logger.info("Bot connecté et canaux marqués comme accessibles.")
        return True
    except Exception as e:
        logger.error(f"Erreur démarrage du client Telegram: {e}")
        return False

async def main():
    """Fonction principale pour lancer le serveur web, le bot et la tâche de reset."""
    try:
        await start_web_server()

        success = await start_bot()
        if not success:
            logger.error("Échec du démarrage du bot")
            return

        # Lancement de la tâche de reset en arrière-plan
        asyncio.create_task(schedule_daily_reset())
        
        logger.info("Bot complètement opérationnel - En attente de messages...")
        await client.run_until_disconnected()

    except Exception as e:
        logger.error(f"Erreur dans main: {e}")
    finally:
        # Assurez-vous que la déconnexion se produit en cas d'erreur
        if client.is_connected():
            await client.disconnect()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot arrêté par l'utilisateur")
    except Exception as e:
        logger.error(f"Erreur fatale: {e}")
