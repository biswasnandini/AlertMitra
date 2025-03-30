import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import aiohttp
from bs4 import BeautifulSoup
from telegram import Update, InputMediaPhoto, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
import random
import json

# Configuration
TELEGRAM_TOKEN = "7904429925:AAHeB9Al9fA3GlULAEDTJHlt1sk9novDuok"
WEATHER_API_KEY = "dcfc51ae455db6baebbebb302faead74"

# Enhanced news sources with better disaster data
REALTIME_NEWS_SOURCES = {
    "GDACS": {
        "url": "https://www.gdacs.org/",
        "rss": "https://www.gdacs.org/xml/rss.xml",
        "type": "disaster",
        "categories": ["earthquake", "flood", "cyclone", "volcano", "drought"]
    },
    "ReliefWeb": {
        "url": "https://reliefweb.int/",
        "rss": "https://reliefweb.int/updates/rss.xml",
        "type": "disaster",
        "categories": ["all"]
    },
    "NOAA Weather Alerts": {
        "url": "https://www.weather.gov/",
        "rss": "https://alerts.weather.gov/cap/us.php?x=0",
        "type": "weather"
    },
    "USGS Earthquakes": {
        "url": "https://earthquake.usgs.gov/",
        "rss": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_week.atom",
        "api": "https://earthquake.usgs.gov/fdsnws/event/1/query",
        "type": "disaster",
        "categories": ["earthquake"]
    },
    "EM-DAT Public": {
        "url": "https://public.emdat.be/",
        "type": "disaster",
        "historical": "https://public.emdat.be/data"
    },
    "NASA EONET": {
        "url": "https://eonet.gsfc.nasa.gov/",
        "api": "https://eonet.gsfc.nasa.gov/api/v3/events",
        "type": "disaster",
        "categories": ["wildfires", "severeStorms", "volcanoes"]
    },
    "DisasterAWARE": {
        "url": "https://disasteraware.pdc.org/",
        "api": "https://api.pdc.org/hazards/active",
        "type": "disaster"
    }
}

# Enhanced image sources with more reliable options
IMAGE_SOURCES = {
    "Flickr": {
        "url": "https://www.flickr.com/search/?text={query}",
        "selector": "div.photo-list-photo-view img"
    },
    "Pixabay": {
        "url": "https://pixabay.com/images/search/{query}/",
        "selector": "img[data-lazy-src]",
        "api": "https://pixabay.com/api/",
        "key": "49587805-d161db9460cb1dd6e61d1b9b0"
    },
    "Wikimedia Commons": {
        "url": "https://commons.wikimedia.org/w/index.php?search={query}",
        "selector": "div.mw-search-result-heading a.image img"
    },
    "NASA Image Library": {
        "url": "https://images.nasa.gov/search-results?q={query}",
        "api": "https://images-api.nasa.gov/search",
        "selector": "img"
    }
}

# Emergency helpline numbers by country (expanded)
HELPLINE_NUMBERS = {
    "india": {
        "police": "100",
        "ambulance": "102",
        "disaster": "108",
        "women_helpline": "1091",
        "child_helpline": "1098",
        "national_disaster": "1078"
    },
    "usa": {
        "emergency": "911",
        "disaster": "1-800-621-FEMA",
        "coast_guard": "1-855-889-8855",
        "poison_control": "1-800-222-1222"
    },
    "japan": {
        "emergency": "110 (Police), 119 (Ambulance/Fire)",
        "earthquake": "#7119 (Disaster Info)"
    },
    "default": {
        "emergency": "112 (International)",
        "disaster": "Check local authorities"
    }
}

# Precaution data
PRECAUTIONS = {
    "earthquake": [
        "Drop to the ground and take cover under sturdy furniture",
        "Stay away from windows and heavy objects that could fall",
        "If outdoors, move to an open area away from buildings",
        "Prepare an emergency kit with food, water, and first aid supplies",
        "Learn how to shut off gas valves in your home"
    ],
    "flood": [
        "Move to higher ground immediately",
        "Avoid walking through moving water (6 inches can knock you down)",
        "Do not drive through flooded areas",
        "Disconnect electrical appliances",
        "Be aware of downed power lines"
    ],
    "cyclone": [
        "Board up windows with storm shutters",
        "Secure outdoor objects that could become projectiles",
        "Identify a safe room in your home (windowless interior room)",
        "Store drinking water in clean containers",
        "Monitor weather reports regularly"
    ],
    "wildfire": [
        "Create defensible space around your home",
        "Wet down your roof and shrubs within 30 feet of home",
        "Prepare a respirator mask (N95 or P100)",
        "Know multiple evacuation routes",
        "Keep gutters and roofs clear of leaves/debris"
    ],
    "tsunami": [
        "Move to high ground at least 100 feet above sea level",
        "Go inland as far as possible",
        "Never stay to watch incoming waves",
        "Listen to official evacuation orders",
        "Learn natural warning signs (earthquake + ocean recession)"
    ]
}

# User subscriptions
user_subscriptions = {}

# Cache configuration
CACHE_DURATION = timedelta(minutes=10)
news_cache = {"last_updated": None, "data": []}
historical_cache = {}
weather_cache = {}

# Set up logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def _extract_location(description: str, fallback: str = None) -> str:
    """Extract location information from description"""
    if not description:
        return fallback if fallback else "Various locations"
    
    patterns = [
        r"near ([\w\s]+)",
        r"in ([\w\s]+)",
        r"close to ([\w\s]+)",
        r"at ([\w\s]+)",
        r"of ([\w\s]+)"
    ]
    
    for pattern in patterns:
        match = re.search(pattern, description, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    
    return fallback if fallback else "Various locations"

def _truncate_description(description: str, max_length: int = 300) -> str:
    """Truncate description to reasonable length"""
    if not description:
        return "No description available"
    if len(description) <= max_length:
        return description
    return description[:max_length] + "..."

def _get_severity_from_title(title: str) -> str:
    """Determine severity from disaster title"""
    if not title:
        return "🔵 Unknown"
    
    title_lower = title.lower()
    
    if any(w in title_lower for w in ["major", "catastrophic", "extreme"]):
        return "🌋 Extreme"
    if any(w in title_lower for w in ["strong", "severe", "deadly"]):
        return "🔥 High"
    if any(w in title_lower for w in ["moderate", "medium"]):
        return "⚠ Moderate"
    if any(w in title_lower for w in ["minor", "small"]):
        return "🔵 Low"
    return "🔵 Unknown"

def _get_severity(value) -> str:
    """Convert various severity indicators to standardized levels"""
    if isinstance(value, (int, float)):
        if value >= 7.5: return "🌋 Extreme"
        if value >= 6.0: return "🔥 High"
        if value >= 4.5: return "⚠ Moderate"
        return "🔵 Low"
    elif isinstance(value, str):
        value = value.lower()
        if 'extreme' in value: return "🌋 Extreme"
        if 'high' in value: return "🔥 High"
        if 'moderate' in value: return "⚠ Moderate"
        return "🔵 Low"
    return "🔵 Unknown"

async def welcome_user(update: Update) -> None:
    """Send personalized welcome message with enhanced features"""
    user = update.effective_user
    welcome_msg = (
        f"🛡 Welcome to AlertMitra, {user.first_name}!\n\n"
        "I'm your advanced disaster awareness assistant with:\n"
        "• 🌪 Live disaster alerts worldwide\n"
        "• ⚡ Emergency updates\n"
        "• 🌤 Hyperlocal weather reports\n"
        "• 📸 Verified images from public sources\n"
        "• 🚨 Proactive disaster alerts\n"
        "• 🆘 Local emergency contacts\n"
        "• 🛡 Disaster preparedness guides\n\n"
        "Try these commands:\n"
        "/recent - Latest disaster alerts\n"
        "/weather [location] - Real-time weather\n"
        "/precautions - Get safety measures\n"
        "/disasters [country] - Recent disasters\n"
        "/history [country] - Past disasters\n"
        "/subscribe [country] - Get alerts\n"
        "/helpline [country] - Emergency contacts\n"
        "/alert - Check imminent threats"
    )
    
    try:
        sample_images = [
            "https://upload.wikimedia.org/wikipedia/commons/thumb/6/6c/Cyclone_Amphan_2020-05-18_1205Z.jpg/1200px-Cyclone_Amphan_2020-05-18_1205Z.jpg",
            "https://upload.wikimedia.org/wikipedia/commons/thumb/5/5e/2011_Japan_earthquake.jpg/1200px-2011_Japan_earthquake.jpg"
        ]
        media_group = [
            InputMediaPhoto(sample_images[0]),
            InputMediaPhoto(sample_images[1], caption=welcome_msg, parse_mode="Markdown")
        ]
        await update.message.reply_media_group(media_group)
    except Exception as e:
        logger.error(f"Error sending welcome images: {e}")
        await update.message.reply_text(welcome_msg, parse_mode="Markdown")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command"""
    await welcome_user(update)

async def precaution_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show list of disaster types for precaution selection"""
    keyboard = [
        [InlineKeyboardButton("🌋 Earthquake", callback_data='precaution_earthquake')],
        [InlineKeyboardButton("🌊 Flood", callback_data='precaution_flood')],
        [InlineKeyboardButton("🌀 Cyclone", callback_data='precaution_cyclone')],
        [InlineKeyboardButton("🔥 Wildfire", callback_data='precaution_wildfire')],
        [InlineKeyboardButton("🌊 Tsunami", callback_data='precaution_tsunami')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "⚠️ Select Disaster Type for Safety Precautions:",
        reply_markup=reply_markup
    )

async def precaution_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline button presses for precautions"""
    query = update.callback_query
    await query.answer()
    
    disaster_type = query.data.split('_')[1]
    precautions = PRECAUTIONS.get(disaster_type, [])
    
    if precautions:
        response = f"🛡️ {disaster_type.title()} Safety Precautions:\n\n" + "\n".join(
            [f"• {i+1}. {item}" for i, item in enumerate(precautions)]
        )
        response += "\n\n🚨 General Emergency Contacts:\n"
        response += "\n".join([f"• {k.title()}: {v}" for k, v in HELPLINE_NUMBERS['default'].items()])
    else:
        response = "⚠️ Precautions not available for this disaster type"
    
    await query.edit_message_text(
        text=response,
        parse_mode="Markdown"
    )

# All existing functions remain unchanged below this line
# [Keep all existing functions exactly as provided by the user]
# [Only add the new precaution handlers to main()]

async def fetch_nasa_eonet_events(days: int = 7) -> List[Dict]:
    # Existing implementation
    pass

async def fetch_usgs_earthquakes(start_date: str, end_date: str, min_magnitude: float = 5.0) -> List[Dict]:
    # Existing implementation
    pass

async def fetch_disasteraware_alerts() -> List[Dict]:
    # Existing implementation
    pass

async def fetch_historical_disasters(location: str = None, years: int = 10) -> List[Dict]:
    # Existing implementation
    pass

async def fetch_rss_feed(url: str) -> Optional[List[Dict]]:
    # Existing implementation
    pass

async def fetch_pixabay_images(query: str) -> List[str]:
    # Existing implementation
    pass

async def fetch_nasa_images(query: str) -> List[str]:
    # Existing implementation
    pass

async def get_free_images(query: str) -> List[str]:
    # Existing implementation
    pass

async def fetch_all_realtime_news() -> List[Dict]:
    # Existing implementation
    pass

async def get_recent_disasters(location: str = None) -> Tuple[List[Dict], Optional[str]]:
    # Existing implementation
    pass

async def send_recent_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Existing implementation
    pass

async def disasters_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Existing implementation
    pass

async def history_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Existing implementation
    pass

async def weather_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Existing implementation
    pass

async def get_weather(location: str) -> Tuple[Optional[Dict], Optional[str]]:
    # Existing implementation
    pass

async def subscribe_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Existing implementation
    pass

async def unsubscribe_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Existing implementation
    pass

async def alert_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Existing implementation
    pass

async def helpline_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Existing implementation
    pass

async def check_for_alerts(context: ContextTypes.DEFAULT_TYPE):
    # Existing implementation
    pass

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Existing implementation
    pass

def main() -> None:
    """Run the bot with enhanced features"""
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Original command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("recent", send_recent_alerts))
    application.add_handler(CommandHandler("weather", weather_handler))
    application.add_handler(CommandHandler("disasters", disasters_handler))
    application.add_handler(CommandHandler("history", history_handler))
    application.add_handler(CommandHandler("subscribe", subscribe_handler))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe_handler))
    application.add_handler(CommandHandler("alert", alert_handler))
    application.add_handler(CommandHandler("helpline", helpline_handler))
    
    # New precaution handlers
    application.add_handler(CommandHandler("precautions", precaution_handler))
    application.add_handler(CallbackQueryHandler(precaution_callback, pattern='^precaution_'))

    # Message handler
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))
    
    # Set up periodic alert checking
    job_queue = application.job_queue
    if job_queue:
        job_queue.run_repeating(check_for_alerts, interval=300.0, first=10.0)
    
    logger.info("AlertMitra is now active with enhanced monitoring...")
    application.run_polling()

if __name__ == "__main__":
    main()