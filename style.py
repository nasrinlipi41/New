import os
import sqlite3
import logging
import asyncio
import random
import hashlib
import threading
import time
import requests
from typing import Dict, List, Tuple
from datetime import datetime
from flask import Flask, request, Response, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    PicklePersistence,
)
from telegram.constants import ParseMode
from contextlib import contextmanager

# ==================== RENDER-SPECIFIC SETUP ====================
# Get token from environment variable (NOT hardcoded!)
BOT_TOKEN = os.environ.get('TELEGRAM_TOKEN', '')
if not BOT_TOKEN:
    print("❌ ERROR: TELEGRAM_TOKEN environment variable is not set!")
    print("Please set it in Render dashboard: Environment → Add TELEGRAM_TOKEN")
    print("Example: TELEGRAM_TOKEN=7690309938:AAGxZaZztsxWOucrIo5UiLXusagQOBvbksw")
    exit(1)

ADMIN_IDS = [5487394544]  # Your Telegram ID
DB_NAME = 'stylish_name_bot.db'
ITEMS_PER_PAGE = 10
MAX_NAME_LENGTH = 30

# ==================== FLASK APP FOR RENDER ====================
app = Flask(__name__)

# Global variables for tracking
last_ping_time = time.time()
app_start_time = time.time()
bot_application = None  # Will be initialized properly

# ==================== ENHANCED LOGGING ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== DATABASE LOCK ====================
db_lock = threading.Lock()

@contextmanager
def get_db_connection():
    """Thread-safe database connection"""
    with db_lock:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

# ==================== DATABASE ====================
class Database:
    @staticmethod
    def setup():
        """Initialize database"""
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                
                # Create tables with correct schema
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        first_name TEXT,
                        username TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS generated_styles (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        original_text TEXT,
                        styled_text TEXT,
                        style_type TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                conn.commit()
                logger.info("✅ Database initialized")
                
        except sqlite3.Error as e:
            logger.error(f"Database error: {e}")
    
    @staticmethod
    def add_user(user_id: int, first_name: str, username: str):
        """Add user to database"""
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR REPLACE INTO users (user_id, first_name, username) 
                    VALUES (?, ?, ?)
                ''', (user_id, first_name, username or ""))
                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Add user error: {e}")
    
    @staticmethod
    def get_user_count():
        """Get total user count"""
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM users")
                count = cursor.fetchone()[0]
                return count if count else 0
        except sqlite3.Error as e:
            logger.error(f"Get user count error: {e}")
            return 0
    
    @staticmethod
    def get_all_users():
        """Get all user IDs"""
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT user_id FROM users")
                return [row[0] for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error(f"Get all users error: {e}")
            return []
    
    @staticmethod
    def save_style(user_id: int, original: str, styled: str, style_type: str):
        """Save generated style"""
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO generated_styles (user_id, original_text, styled_text, style_type)
                    VALUES (?, ?, ?, ?)
                ''', (user_id, original, styled, style_type))
                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Save style error: {e}")

# ==================== FONTS & STYLES ====================
class FontStyles:
    # Base characters for translation
    NORMAL = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    
    # 50+ Fonts Collection
    FONTS = {
        'bold': "𝗮𝗯𝗰𝗱𝗲𝗳𝗴𝗵𝗶𝗷𝗸𝗹𝗺𝗻𝗼𝗽𝗾𝗿𝘀𝘁𝘂𝘃𝘄𝘅𝘆𝘇𝗔𝗕𝗖𝗗𝗘𝗙𝗚𝗛𝗜𝗝𝗞𝗟𝗠𝗡𝗢𝗣𝗤𝗥𝗦𝗧𝗨𝗩𝗪𝗫𝗬𝗭𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵",
        'italic': "𝘢𝘣𝘤𝘥𝘦𝘧𝘨𝘩𝘪𝘫𝘬𝘭𝘮𝘯𝘰𝘱𝘲𝘳𝘴𝘵𝘶𝘷𝘸𝘹𝘺𝘻𝘈𝘉𝘊𝘋𝘌𝘍𝘎𝘏𝘐𝘑𝘒𝘓𝘔𝘕𝘖𝘗𝘘𝘙𝘚𝘛𝘜𝘝𝘞𝘟𝚈𝚉0123456789",
    }
    
    # Small Caps Font for bot messages
    SMALL_CAPS_FONT = str.maketrans(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        "ᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢ0123456789"
    )
    
    # Translation tables cache
    TRANSLATION_TABLES = {}
    
    @classmethod
    def _init_translation_tables(cls):
        """Initialize translation tables"""
        if not cls.TRANSLATION_TABLES:
            for font_name, font_chars in cls.FONTS.items():
                if len(cls.NORMAL) == len(font_chars):
                    cls.TRANSLATION_TABLES[font_name] = str.maketrans(cls.NORMAL, font_chars)
    
    # 1000+ Decorative Styles
    DECORATIVE_STYLES = [
        # Basic Decorations
        "꧁{}꧂", "⫷{}⫸",
        
        # Emoji Styles (200+)
        "😈{}😈", "👑{}👑",
    ]
    
    # Mixed Styles (Font + Decoration)
    MIXED_STYLES = []
    
    @classmethod
    def generate_mixed_styles(cls):
        """Generate mixed styles"""
        if not cls.MIXED_STYLES:
            mixed = []
            fonts = list(cls.FONTS.keys())
            decorations = cls.DECORATIVE_STYLES[:50]  # Use first 50 decorations
            
            for font in fonts[:20]:  # Use first 20 fonts
                for decor in decorations[:20]:  # Use first 20 decorations
                    mixed.append((font, decor))
            
            # Special combinations
            special_combos = [
                ('bold', '꧁{}꧂'), ('italic', '『{}』'), ('monospace', '♛{}♛'),
            ]
            
            cls.MIXED_STYLES = mixed + special_combos
    
    @classmethod
    def apply_font(cls, text: str, font_name: str) -> str:
        """Apply font to text"""
        if not cls.TRANSLATION_TABLES:
            cls._init_translation_tables()
        
        if font_name == 'small_caps':
            return text.translate(cls.SMALL_CAPS_FONT)
        
        table = cls.TRANSLATION_TABLES.get(font_name)
        if table:
            return text.translate(table)
        return text
    
    @classmethod
    def apply_style(cls, text: str, style_type: str, style_template: str = None) -> str:
        """Apply style to text"""
        if style_type == "font" and style_template:
            return cls.apply_font(text, style_template)
        elif style_type == "decorative" and style_template:
            return style_template.format(text)
        elif style_type == "art" and style_template:
            return style_template.format(text)
        elif style_type == "mixed" and style_template:
            font_name, decor = style_template
            font_text = cls.apply_font(text, font_name)
            return decor.format(font_text)
        return text

# ==================== TEXT STORAGE ====================
class TextStorage:
    """Fast text storage with hash"""
    _storage = {}
    
    @classmethod
    def store_text(cls, text: str) -> str:
        """Store text and return hash"""
        text_hash = hashlib.md5(text.encode()).hexdigest()[:16]
        cls._storage[text_hash] = text
        return text_hash
    
    @classmethod
    def get_text(cls, text_hash: str) -> str:
        """Get text by hash"""
        return cls._storage.get(text_hash, "")

# ==================== BOT HANDLERS ====================
class BotHandlers:
    def __init__(self):
        self.font_styles = FontStyles()
        self.text_storage = TextStorage()
        self.font_styles.generate_mixed_styles()
        self.font_styles._init_translation_tables()
    
    def apply_small_caps(self, text: str) -> str:
        """Apply small caps font"""
        return text.translate(FontStyles.SMALL_CAPS_FONT)
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        try:
            user = update.effective_user
            
            # Add user to database
            await asyncio.get_event_loop().run_in_executor(
                None, Database.add_user, user.id, user.first_name, user.username
            )
            
            welcome_msg = self.apply_small_caps(
                f"✨ ᴡᴇʟᴄᴏᴍᴇ {user.first_name}! ✨\n\n"
                "🎨 sᴛʏʟɪsʜ ɴᴀᴍᴇ ʙᴏᴛ\n"
                "• 2000+ sᴛʏʟᴇs/ғᴏɴᴛs/ᴀʀᴛ\n"
                "• ɪɴsᴛᴀɴᴛ ᴏɴᴇ-ᴄʟɪᴄᴋ ᴄᴏᴘʏ\n"
                "• ғᴀsᴛ ᴘᴀɢɪɴᴀᴛɪᴏɴ\n\n"
                "👇 ᴄʜᴏᴏsᴇ ᴀɴ ᴏᴘᴛɪᴏɴ:"
            )
            
            keyboard = [
                [InlineKeyboardButton("🎨 ᴄʀᴇᴀᴛᴇ sᴛʏʟᴇ", callback_data='create_style')],
                [InlineKeyboardButton("🎲 ʀᴀɴᴅᴏᴍ ɴᴀᴍᴇ", callback_data='random_name')],
                [InlineKeyboardButton("📊 ʙᴏᴛ sᴛᴀᴛs", callback_data='bot_stats')],
                [InlineKeyboardButton("ℹ️ ʜᴇʟᴘ", callback_data='help')]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Check if update has message or callback_query
            if update.message:
                await update.message.reply_text(welcome_msg, reply_markup=reply_markup)
            elif update.callback_query:
                await update.callback_query.edit_message_text(welcome_msg, reply_markup=reply_markup)
            else:
                # Fallback
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=welcome_msg,
                    reply_markup=reply_markup
                )
            
        except Exception as e:
            logger.error(f"Start command error: {e}")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=self.apply_small_caps("⚠️ ᴀɴ ᴇʀʀᴏʀ ᴏᴄᴄᴜʀʀᴇᴅ. ᴘʟᴇᴀsᴇ ᴛʀʏ /start ᴀɢᴀɪɴ.")
            )
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        help_text = self.apply_small_caps(
            "🎨 *sᴛʏʟɪsʜ ɴᴀᴍᴇ ʙᴏᴛ*\n\n"
            "*ᴄᴏᴍᴍᴀɴᴅs:*\n"
            "/start - sᴛᴀʀᴛ ᴛʜᴇ ʙᴏᴛ\n"
            "/help - sʜᴏᴡ ʜᴇʟᴘ\n"
            "/admin - ᴀᴅᴍɪɴ ᴍᴇɴᴜ\n\n"
            "*ʜᴏᴡ ᴛᴏ ᴜsᴇ:*\n"
            "1. ᴄʟɪᴄᴋ /start\n"
            "2. ᴄʜᴏᴏsᴇ 'ᴄʀᴇᴀᴛᴇ sᴛʏʟᴇ'\n"
            "3. ᴇɴᴛᴇʀ ʏᴏᴜʀ ɴᴀᴍᴇ\n"
            "4. sᴇʟᴇᴄᴛ ᴀ ᴄᴀᴛᴇɢᴏʀʏ\n"
            "5. ᴄʟɪᴄᴋ ᴏɴ ᴀɴʏ sᴛʏʟᴇ ᴛᴏ ᴄᴏᴘʏ\n\n"
            "*ɴᴏᴛᴇ:*\n"
            "• ɴᴀᴍᴇ ᴍᴀx 30 ᴄʜᴀʀᴀᴄᴛᴇʀs\n"
            "• ᴀʟʟ sᴛʏʟᴇs sᴜᴘᴘᴏʀᴛᴇᴅ ᴏɴ ᴍᴏʙɪʟᴇ & ᴅᴇsᴋᴛᴏᴘ\n"
            "• ɪɴsᴛᴀɴᴛ ᴏɴᴇ-ᴄʟɪᴄᴋ ᴄᴏᴘʏ\n"
            "• 2000+ sᴛʏʟᴇs ᴀᴠᴀɪʟᴀʙʟᴇ"
        )
        
        await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)
    
    async def admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /admin command"""
        user = update.effective_user
        
        if user.id not in ADMIN_IDS:
            await update.message.reply_text("⛔ ᴀᴄᴄᴇss ᴅᴇɴɪᴇᴅ.")
            return
        
        admin_text = self.apply_small_caps(
            "👑 *ᴀᴅᴍɪɴ ᴍᴇɴᴜ*\n\n"
            "*ᴄᴏᴍᴍᴀɴᴅs:*\n"
            "/stats - sʜᴏᴡ ʙᴏᴛ sᴛᴀᴛɪsᴛɪᴄs\n"
            "/broadcast - sᴇɴᴅ ᴍᴇssᴀɢᴇ ᴛᴏ ᴀʟʟ ᴜsᴇʀs\n"
            "/users - sʜᴏᴡ ᴜsᴇʀ ʟɪsᴛ\n\n"
            "*ɪɴsᴛʀᴜᴄᴛɪᴏɴs:*\n"
            "ғᴏʀ ʙʀᴏᴀᴅᴄᴀsᴛ:\n"
            "1. ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ\n"
            "2. ᴛʏᴘᴇ /broadcast\n"
            "ᴏʀ\n"
            "ᴛʏᴘᴇ: /broadcast ʏᴏᴜʀ ᴍᴇssᴀɢᴇ"
        )
        
        keyboard = [
            [InlineKeyboardButton("📊 sᴛᴀᴛs", callback_data='admin_stats')],
            [InlineKeyboardButton("📢 ʙʀᴏᴀᴅᴄᴀsᴛ", callback_data='admin_broadcast')],
            [InlineKeyboardButton("👥 ᴜsᴇʀs", callback_data='admin_users')],
            [InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data='back_to_start')]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(admin_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    
    async def ask_for_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Ask for name"""
        query = update.callback_query
        await query.answer()
        
        await query.edit_message_text(
            self.apply_small_caps("✍️ ᴘʟᴇᴀsᴇ ᴇɴᴛᴇʀ ʏᴏᴜʀ ɴᴀᴍᴇ:"),
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def process_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Process name input"""
        name = update.message.text.strip()
        
        if len(name) > MAX_NAME_LENGTH:
            await update.message.reply_text(
                self.apply_small_caps(f"⚠️ ɴᴀᴍᴇ ᴛᴏᴏ ʟᴏɴɢ! ᴍᴀx {MAX_NAME_LENGTH} ᴄʜᴀʀs.")
            )
            return
        
        if not name:
            await update.message.reply_text(self.apply_small_caps("⚠️ ᴇɴᴛᴇʀ ᴀ ᴠᴀʟɪᴅ ɴᴀᴍᴇ."))
            return
        
        context.user_data['name'] = name
        
        keyboard = [
            [InlineKeyboardButton("🎭 ᴅᴇᴄᴏʀᴀᴛɪᴠᴇ (1000+)", callback_data='cat_decorative')],
            [InlineKeyboardButton("🔤 ғᴏɴᴛs (50+)", callback_data='cat_fonts')],
            [InlineKeyboardButton("🎨 ᴀʀᴛ (500+)", callback_data='cat_art')],
            [InlineKeyboardButton("🌀 ᴍɪxᴇᴅ (500+)", callback_data='cat_mixed')],
            [InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data='back_to_start')]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            self.apply_small_caps(f"✅ ɴᴀᴍᴇ: `{name}`\n\n👇 sᴇʟᴇᴄᴛ ᴄᴀᴛᴇɢᴏʀʏ:"),
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def show_category_styles(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show styles for category"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        name = context.user_data.get('name', '')
        
        if not name:
            await query.edit_message_text(self.apply_small_caps("⚠️ ɴᴏ ɴᴀᴍᴇ ғᴏᴜɴᴅ."))
            return
        
        category_map = {
            'cat_decorative': ('decorative', FontStyles.DECORATIVE_STYLES),
            'cat_fonts': ('fonts', list(FontStyles.FONTS.keys())),
            'cat_art': ('art', FontStyles.DECORATIVE_STYLES[:20]),  # Using decorative as art for now
            'cat_mixed': ('mixed', FontStyles.MIXED_STYLES[:20])
        }
        
        if data not in category_map:
            return
        
        category, styles = category_map[data]
        context.user_data['current_category'] = category
        context.user_data['current_styles'] = styles
        context.user_data['current_page'] = 1
        
        await self.show_styles_page(update, context)
    
    async def show_styles_page(self, update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1):
        """Show styles page"""
        query = update.callback_query if hasattr(update, 'callback_query') else None
        if query:
            await query.answer()
        
        name = context.user_data.get('name', '')
        category = context.user_data.get('current_category', '')
        styles = context.user_data.get('current_styles', [])
        
        if not name:
            return await self.start_command(update, context)
        
        if page:
            context.user_data['current_page'] = page
        else:
            page = context.user_data.get('current_page', 1)
        
        total = len(styles)
        total_pages = max(1, (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
        page = max(1, min(page, total_pages))
        
        start = (page - 1) * ITEMS_PER_PAGE
        end = start + ITEMS_PER_PAGE
        page_styles = styles[start:end]
        
        # Generate buttons
        buttons = []
        for i, style in enumerate(page_styles, 1):
            styled_text = self._generate_styled_text(name, category, style)
            text_hash = TextStorage.store_text(styled_text)
            
            btn_text = f"{i}. {styled_text[:15]}..." if len(styled_text) > 15 else f"{i}. {styled_text}"
            buttons.append([InlineKeyboardButton(btn_text, callback_data=f"copy_{text_hash}")])
        
        # Pagination
        pagination_buttons = []
        if page > 1:
            pagination_buttons.append(InlineKeyboardButton("⬅️", callback_data=f"page_{page-1}"))
        
        pagination_buttons.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data='current_page'))
        
        if page < total_pages:
            pagination_buttons.append(InlineKeyboardButton("➡️", callback_data=f"page_{page+1}"))
        
        if pagination_buttons:
            buttons.append(pagination_buttons)
        
        # Navigation
        nav_buttons = [
            InlineKeyboardButton("🔄 ᴄʜᴀɴɢᴇ", callback_data='change_category'),
            InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data='back_to_start'),
            InlineKeyboardButton("✏️ ɴᴇᴡ", callback_data='new_name')
        ]
        buttons.append(nav_buttons)
        
        reply_markup = InlineKeyboardMarkup(buttons)
        
        category_names = {
            'decorative': "🎭 ᴅᴇᴄᴏʀᴀᴛɪᴠᴇ",
            'fonts': "🔤 ғᴏɴᴛs",
            'art': "🎨 ᴀʀᴛ",
            'mixed': "🌀 ᴍɪxᴇᴅ"
        }
        
        category_display = category_names.get(category, category)
        
        message_text = self.apply_small_caps(
            f"📝 ɴᴀᴍᴇ: `{name}`\n"
            f"📂 ᴄᴀᴛᴇɢᴏʀʏ: {category_display}\n"
            f"📊 ᴛᴏᴛᴀʟ: {total}\n"
            f"📄 ᴘᴀɢᴇ: {page}/{total_pages}\n\n"
            "👇 ᴄʟɪᴄᴋ ᴛᴏ ᴄᴏᴘʏ:"
        )
        
        if query:
            await query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=message_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
    
    def _generate_styled_text(self, name: str, category: str, style) -> str:
        """Generate styled text"""
        if category == 'fonts':
            return FontStyles.apply_font(name, style)
        elif category == 'decorative':
            return style.format(name)
        elif category == 'art':
            return style.format(name)
        elif category == 'mixed':
            font_name, decor = style
            font_text = FontStyles.apply_font(name, font_name)
            return decor.format(font_text)
        return name
    
    async def handle_pagination(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle pagination"""
        query = update.callback_query
        data = query.data
        
        if data.startswith('page_'):
            page = int(data.split('_')[1])
            await self.show_styles_page(update, context, page)
    
    async def copy_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Copy text handler"""
        query = update.callback_query
        data = query.data
        
        if data.startswith('copy_'):
            text_hash = data[5:]
            text_to_copy = TextStorage.get_text(text_hash)
            
            if text_to_copy:
                # Save to database in background
                user_id = query.from_user.id
                name = context.user_data.get('name', '')
                category = context.user_data.get('current_category', '')
                
                await asyncio.get_event_loop().run_in_executor(
                    None, Database.save_style, user_id, name, text_to_copy, category
                )
                
                # Send copy-able text
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"```\n{text_to_copy}\n```",
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                
                await query.answer(self.apply_small_caps("✅ ᴄᴏᴘɪᴇᴅ!"), show_alert=True)
            else:
                await query.answer(self.apply_small_caps("⚠️ ᴛᴇxᴛ ɴᴏᴛ ғᴏᴜɴᴅ"), show_alert=True)
    
    async def generate_random_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Generate random names"""
        query = update.callback_query
        await query.answer()
        
        first_parts = ["Shadow", "Dark", "Neo", "Royal", "Crimson", "Ghost", "Night", "Demon", 
                      "Cyber", "Steel", "Iron", "Golden", "Silver", "Phantom", "Mystic"]
        second_parts = ["Killer", "Hunter", "Rider", "Warrior", "Slayer", "Assassin", "Master", 
                       "Lord", "King", "Queen", "Prince", "Legend", "Hero", "Ninja", "Samurai"]
        
        await query.edit_message_text(self.apply_small_caps("🎲 ɢᴇɴᴇʀᴀᴛɪɴɢ..."))
        
        for i in range(5):
            name = f"{random.choice(first_parts)}{random.choice(second_parts)}{random.randint(1, 99)}"
            
            categories = ['decorative', 'fonts', 'art', 'mixed']
            category = random.choice(categories)
            
            if category == 'decorative':
                style = random.choice(FontStyles.DECORATIVE_STYLES)
                styled_text = style.format(name)
            elif category == 'fonts':
                font = random.choice(list(FontStyles.FONTS.keys()))
                styled_text = FontStyles.apply_font(name, font)
            elif category == 'art':
                style = random.choice(FontStyles.DECORATIVE_STYLES[:20])
                styled_text = style.format(name)
            else:
                font = random.choice(list(FontStyles.FONTS.keys()))
                decor = random.choice(FontStyles.DECORATIVE_STYLES)
                styled_text = decor.format(FontStyles.apply_font(name, font))
            
            text_hash = TextStorage.store_text(styled_text)
            
            keyboard = [[InlineKeyboardButton("📋 ᴄᴏᴘʏ", callback_data=f"copy_{text_hash}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=self.apply_small_caps(f"🎲 ɴᴀᴍᴇ #{i+1}:\n`{styled_text}`"),
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        
        keyboard = [[InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data='back_to_start')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=self.apply_small_caps("✅ 5 ʀᴀɴᴅᴏᴍ ɴᴀᴍᴇs ɢᴇɴᴇʀᴀᴛᴇᴅ!"),
            reply_markup=reply_markup
        )
    
    async def show_bot_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show bot stats"""
        query = update.callback_query
        await query.answer()
        
        user_count = await asyncio.get_event_loop().run_in_executor(None, Database.get_user_count)
        
        stats_text = self.apply_small_caps(
            f"📊 ʙᴏᴛ sᴛᴀᴛɪsᴛɪᴄs\n\n"
            f"👥 ᴛᴏᴛᴀʟ ᴜsᴇʀs: {user_count}\n"
            f"🎨 sᴛʏʟᴇ ᴄᴀᴛᴇɢᴏʀɪᴇs: 4\n"
            f"✨ ᴛᴏᴛᴀʟ sᴛʏʟᴇs: 2000+\n"
            f"• ᴅᴇᴄᴏʀᴀᴛɪᴠᴇ: 1000+\n"
            f"• ғᴏɴᴛs: 50+\n"
            f"• ᴀʀᴛ: 500+\n"
            f"• ᴍɪxᴇᴅ: 500+\n\n"
            f"🚀 ʙᴏᴛ sᴛᴀᴛᴜs: ᴀᴄᴛɪᴠᴇ"
        )
        
        keyboard = [[InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data='back_to_start')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(stats_text, reply_markup=reply_markup)
    
    async def handle_navigation(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle navigation"""
        query = update.callback_query
        data = query.data
        await query.answer()
        
        if data == 'back_to_start':
            await self.start_command(update, context)
        elif data == 'change_category':
            await self.show_category_menu(update, context)
        elif data == 'new_name':
            await self.ask_for_name(update, context)
    
    async def show_category_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show category menu"""
        query = update.callback_query
        await query.answer()
        
        name = context.user_data.get('name', 'ɴᴏ ɴᴀᴍᴇ')
        
        keyboard = [
            [InlineKeyboardButton("🎭 ᴅᴇᴄᴏʀᴀᴛɪᴠᴇ (1000+)", callback_data='cat_decorative')],
            [InlineKeyboardButton("🔤 ғᴏɴᴛs (50+)", callback_data='cat_fonts')],
            [InlineKeyboardButton("🎨 ᴀʀᴛ (500+)", callback_data='cat_art')],
            [InlineKeyboardButton("🌀 ᴍɪxᴇᴅ (500+)", callback_data='cat_mixed')],
            [InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data='back_to_start')]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            self.apply_small_caps(f"📝 ɴᴀᴍᴇ: `{name}`\n\n👇 sᴇʟᴇᴄᴛ ᴄᴀᴛᴇɢᴏʀʏ:"),
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
    # Admin handlers
    async def admin_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Admin stats"""
        query = update.callback_query
        await query.answer()
        
        user_count = await asyncio.get_event_loop().run_in_executor(None, Database.get_user_count)
        
        stats_text = self.apply_small_caps(
            f"👑 ᴀᴅᴍɪɴ sᴛᴀᴛs\n\n"
            f"👥 ᴛᴏᴛᴀʟ ᴜsᴇʀs: {user_count}\n"
            f"📅 ʙᴏᴛ ᴜᴘᴛɪᴍᴇ: ᴀᴄᴛɪᴠᴇ\n"
            f"💾 ᴅᴀᴛᴀʙᴀsᴇ: sᴛʏʟɪsʜ_ɴᴀᴍᴇ_ʙᴏᴛ.ᴅʙ\n"
            f"⚡ ᴘᴇʀғᴏʀᴍᴀɴᴄᴇ: ᴏᴘᴛɪᴍɪᴢᴇᴅ\n"
            f"🎨 sᴛʏʟᴇs ᴀᴠᴀɪʟᴀʙʟᴇ: 2000+"
        )
        
        keyboard = [
            [InlineKeyboardButton("📢 ʙʀᴏᴀᴅᴄᴀsᴛ", callback_data='admin_broadcast')],
            [InlineKeyboardButton("👥 ᴜsᴇʀ ʟɪsᴛ", callback_data='admin_users')],
            [InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data='back_to_start')]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(stats_text, reply_markup=reply_markup)
    
    async def admin_broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Admin broadcast"""
        query = update.callback_query
        await query.answer()
        
        broadcast_text = self.apply_small_caps(
            "📢 *ʙʀᴏᴀᴅᴄᴀsᴛ ᴍᴇɴᴜ*\n\n"
            "*ɪɴsᴛʀᴜᴄᴛɪᴏɴs:*\n"
            "ᴛᴏ sᴇɴᴅ ʙʀᴏᴀᴅᴄᴀsᴛ:\n"
            "1. ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ\n"
            "2. ᴛʏᴘᴇ /broadcast\n"
            "\nᴏʀ\n"
            "\nᴛʏᴘᴇ: /broadcast ʏᴏᴜʀ ᴍᴇssᴀɢᴇ\n\n"
            "*ɴᴏᴛᴇ:*\n"
            "• ᴏɴʟʏ ᴀᴅᴍɪɴs ᴄᴀɴ ʙʀᴏᴀᴅᴄᴀsᴛ\n"
            "• ᴜsᴇ ᴡɪsᴇʟʏ\n"
            "• ᴅᴏɴ'ᴛ sᴘᴀᴍ"
        )
        
        keyboard = [
            [InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data='back_to_start')],
            [InlineKeyboardButton("📊 sᴛᴀᴛs", callback_data='admin_stats')]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(broadcast_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    
    async def admin_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Admin users"""
        query = update.callback_query
        await query.answer()
        
        users = await asyncio.get_event_loop().run_in_executor(None, Database.get_all_users)
        
        if not users:
            await query.edit_message_text(self.apply_small_caps("📭 ɴᴏ ᴜsᴇʀs ғᴏᴜɴᴅ."))
            return
        
        users_text = self.apply_small_caps(f"👥 ᴛᴏᴛᴀʟ ᴜsᴇʀs: {len(users)}\n\n")
        
        for i, user_id in enumerate(users[:10], 1):
            users_text += f"{i}. `{user_id}`\n"
        
        if len(users) > 10:
            users_text += f"\n... ᴀɴᴅ {len(users) - 10} ᴍᴏʀᴇ\n"
        
        keyboard = [[InlineKeyboardButton("🏠 ʜᴏᴍᴇ", callback_data='back_to_start')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(users_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

# ==================== ADMIN COMMANDS ====================
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stats command"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ ᴀᴄᴄᴇss ᴅᴇɴɪᴇᴅ.")
        return
    
    bot_handlers = BotHandlers()
    user_count = await asyncio.get_event_loop().run_in_executor(None, Database.get_user_count)
    
    stats_text = bot_handlers.apply_small_caps(
        f"📊 ʙᴏᴛ sᴛᴀᴛɪsᴛɪᴄs\n\n"
        f"👥 ᴛᴏᴛᴀʟ ᴜsᴇʀs: {user_count}\n"
        f"⚡ ʙᴏᴛ sᴛᴀᴛᴜs: ᴀᴄᴛɪᴠᴇ\n"
        f"💾 ᴅᴀᴛᴀʙᴀsᴇ: ᴏᴘᴇʀᴀᴛɪᴏɴᴀʟ\n"
        f"🎨 sᴛʏʟᴇs: 2000+\n"
        f"🚀 ᴘᴇʀғᴏʀᴍᴀɴᴄᴇ: ᴏᴘᴛɪᴍɪᴢᴇᴅ"
    )
    
    await update.message.reply_text(stats_text)

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /broadcast command"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ ᴀᴄᴄᴇss ᴅᴇɴɪᴇᴅ.")
        return
    
    # Check if replying to message
    if update.message.reply_to_message:
        message = update.message.reply_to_message.text or update.message.reply_to_message.caption
        if not message:
            await update.message.reply_text("⚠️ ɴᴏ ᴛᴇxᴛ ɪɴ ʀᴇᴘʟɪᴇᴅ ᴍᴇssᴀɢᴇ.")
            return
    elif context.args:
        message = " ".join(context.args)
    else:
        await update.message.reply_text("⚠️ ᴜsᴀɢᴇ: /broadcast <ᴍᴇssᴀɢᴇ> ᴏʀ ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ.")
        return
    
    users = await asyncio.get_event_loop().run_in_executor(None, Database.get_all_users)
    total = len(users)
    
    if total == 0:
        await update.message.reply_text("📭 ɴᴏ ᴜsᴇʀs ᴛᴏ ʙʀᴏᴀᴅᴄᴀsᴛ ᴛᴏ.")
        return
    
    await update.message.reply_text(f"📢 ʙʀᴏᴀᴅᴄᴀsᴛɪɴɢ ᴛᴏ {total} ᴜsᴇʀs...")
    
    success = 0
    failed = 0
    
    bot_handlers = BotHandlers()
    broadcast_msg = f"📢 *ʙʀᴏᴀᴅᴄᴀsᴛ:*\n\n{message}"
    
    for user_id in users:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=broadcast_msg,
                parse_mode=ParseMode.MARKDOWN
            )
            success += 1
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"ғᴀɪʟᴇᴅ ᴛᴏ sᴇɴᴅ ᴛᴏ {user_id}: {e}")
            failed += 1
    
    result_text = bot_handlers.apply_small_caps(
        f"✅ ʙʀᴏᴀᴅᴄᴀsᴛ ᴄᴏᴍᴘʟᴇᴛᴇ!\n"
        f"✓ sᴜᴄᴄᴇss: {success}\n"
        f"✗ ғᴀɪʟᴇᴅ: {failed}\n"
        f"📊 ᴛᴏᴛᴀʟ: {total}"
    )
    
    await update.message.reply_text(result_text)

# ==================== ERROR HANDLER ====================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    error_msg = str(context.error) if context.error else "Unknown error"
    logger.error(f"ᴇʀʀᴏʀ: {error_msg}")

# ==================== RENDER WEBHOOK ENDPOINTS ====================
@app.route('/')
def home():
    """Home page"""
    return """
    <!DOCTYPE html>
    <html>
    <head><title>🎨 Stylish Name Bot</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; text-align: center; }
        .status { padding: 20px; background: #f5f5f5; border-radius: 10px; margin: 20px auto; max-width: 500px; }
        .btn { display: inline-block; padding: 12px 24px; margin: 10px; background: #0088cc; color: white; text-decoration: none; border-radius: 5px; }
    </style>
    </head>
    <body>
        <h1>🎨 Stylish Name Bot</h1>
        <p>Create stylish names with 2000+ fonts and decorations!</p>
        
        <div class="status">
            <p><strong>Status:</strong> ✅ Running</p>
            <p><strong>Uptime:</strong> {:.0f} seconds</p>
            <p><strong>Host:</strong> Render.com</p>
        </div>
        
        <div>
            <a href="/health" class="btn">❤️ Health Check</a>
            <a href="/set_webhook" class="btn">🔗 Set Webhook</a>
            <a href="/status" class="btn">📊 Status</a>
        </div>
        
        <p style="margin-top: 40px;">Send <code>/start</code> to <a href="https://t.me/YourBotUsername">@YourBotUsername</a> on Telegram</p>
    </body>
    </html>
    """format(time.time() - app_start_time)

@app.route('/health')
def health():
    """Health check endpoint for Render and UptimeRobot"""
    return 'OK', 200

@app.route('/set_webhook')
async def set_webhook_route():
    """Set webhook for Telegram"""
    try:
        if not bot_application:
            return "❌ Bot not initialized", 500
        
        current_url = request.host_url.rstrip('/')
        webhook_url = f"{current_url}/webhook"
        
        await bot_application.bot.set_webhook(webhook_url)
        
        return f"""
        <h2>✅ Webhook Updated!</h2>
        <p><strong>URL:</strong> {webhook_url}</p>
        <p><strong>Status:</strong> Webhook is now active!</p>
        <p><strong>Next:</strong> Send /start to your bot on Telegram!</p>
        <p><a href="/">Back to Home</a></p>
        """
    except Exception as e:
        return f"<h2>❌ Error</h2><pre>{str(e)}</pre>", 500

@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle Telegram webhook with immediate response (FIXES DOUBLE MESSAGES)"""
    global last_ping_time
    
    try:
        # Log the receipt
        update_data = request.get_json(force=True)
        update_id = update_data.get('update_id', 'unknown')
        logger.info(f"📨 Received update {update_id}")
        
        # Update last ping time (activity detected)
        last_ping_time = time.time()
        
        # Return IMMEDIATE response (critical to prevent Telegram timeout)
        response = Response('OK', status=200, mimetype='text/plain')
        
        # Process in background thread
        def process_in_background():
            try:
                update = Update.de_json(update_data, bot_application.bot)
                
                # Create new event loop for this thread
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                # Process the update
                loop.run_until_complete(bot_application.process_update(update))
                
                loop.close()
                logger.info(f"✅ Processed update {update_id}")
                
            except Exception as e:
                logger.error(f"❌ Error processing update: {e}", exc_info=True)
        
        # Start background processing
        thread = threading.Thread(target=process_in_background, daemon=True)
        thread.start()
        
        logger.info(f"⚡ Immediate response sent for update {update_id}")
        return response
        
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}", exc_info=True)
        return 'ERROR', 500

@app.route('/status')
def status_page():
    """Bot status page"""
    global last_ping_time, app_start_time
    
    idle_time = time.time() - last_ping_time
    uptime = time.time() - app_start_time
    
    hours = int(uptime // 3600)
    minutes = int((uptime % 3600) // 60)
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head><title>Bot Status</title></head>
    <body>
        <h1>🤖 Bot Status</h1>
        
        <div style="padding: 20px; background: #f5f5f5; border-radius: 10px; margin: 20px 0;">
            <p><strong>Status:</strong> {'✅ ACTIVE' if idle_time < 300 else '⚠️ INACTIVE'}</p>
            <p><strong>Uptime:</strong> {hours}h {minutes}m</p>
            <p><strong>Last Activity:</strong> {int(idle_time)} seconds ago</p>
            <p><strong>Render Sleeps After:</strong> 15 minutes (900 seconds)</p>
            <p><strong>Keep-Alive Status:</strong> {'✅ ACTIVE (pings every 3 minutes)' if idle_time < 180 else '⚠️ NEEDS ATTENTION'}</p>
        </div>
        
        <div>
            <a href="/health">❤️ Health Check</a> | 
            <a href="/set_webhook">🔗 Reset Webhook</a> | 
            <a href="/">🏠 Home</a>
        </div>
    </body>
    </html>
    """

@app.route('/keep-alive')
def manual_keep_alive():
    """Manual keep-alive ping"""
    global last_ping_time
    last_ping_time = time.time()
    return '✅ Pinged!', 200

# ==================== ULTRA-RELIABLE KEEP-ALIVE ====================
def start_keep_alive():
    """Keep Render from sleeping - pings every 3 minutes"""
    global last_ping_time
    
    def ping_server():
        ping_count = 0
        
        while True:
            ping_count += 1
            try:
                # Get URL from environment or construct
                url = os.environ.get('RENDER_EXTERNAL_URL', '')
                if not url:
                    # Try to get from service name
                    service_name = os.environ.get('RENDER_SERVICE_NAME', '')
                    if service_name:
                        url = f"https://{service_name}.onrender.com"
                    else:
                        url = "http://localhost:10000"
                
                # Ping health endpoint
                response = requests.get(f"{url}/health", timeout=10)
                last_ping_time = time.time()
                logger.info(f"✅ Keep-alive ping #{ping_count}: {response.status_code} at {time.ctime()}")
                
            except Exception as e:
                logger.error(f"❌ Keep-alive ping #{ping_count} failed: {e}")
            
            # Wait 180 seconds (3 minutes) - less than Render's 15-minute sleep
            time.sleep(180)
    
    # Start two threads for redundancy
    for i in range(2):
        thread = threading.Thread(target=ping_server, daemon=True, name=f"KeepAlive-{i+1}")
        thread.start()
    
    logger.info("🚀 Ultra-reliable keep-alive started (3-minute pings, dual threads)")

# ==================== BOT INITIALIZATION ====================
def initialize_bot():
    """Initialize the Telegram bot"""
    global bot_application
    
    try:
        # Initialize database
        Database.setup()
        
        # Create bot handlers
        bot_handlers = BotHandlers()
        
        # Create application
        persistence = PicklePersistence(filepath="bot_persistence")
        bot_application = Application.builder().token(BOT_TOKEN).persistence(persistence).build()
        
        # Add error handler
        bot_application.add_error_handler(error_handler)
        
        # Add command handlers
        bot_application.add_handler(CommandHandler("start", bot_handlers.start_command))
        bot_application.add_handler(CommandHandler("help", bot_handlers.help_command))
        bot_application.add_handler(CommandHandler("admin", bot_handlers.admin_command))
        bot_application.add_handler(CommandHandler("stats", stats_command))
        bot_application.add_handler(CommandHandler("broadcast", broadcast_command))
        
        # Add callback query handlers
        bot_application.add_handler(CallbackQueryHandler(bot_handlers.ask_for_name, pattern='^create_style$'))
        bot_application.add_handler(CallbackQueryHandler(bot_handlers.generate_random_name, pattern='^random_name$'))
        bot_application.add_handler(CallbackQueryHandler(bot_handlers.show_bot_stats, pattern='^bot_stats$'))
        bot_application.add_handler(CallbackQueryHandler(bot_handlers.help_command, pattern='^help$'))
        bot_application.add_handler(CallbackQueryHandler(bot_handlers.show_category_styles, pattern='^cat_'))
        bot_application.add_handler(CallbackQueryHandler(bot_handlers.handle_pagination, pattern='^page_'))
        bot_application.add_handler(CallbackQueryHandler(bot_handlers.copy_text, pattern='^copy_'))
        bot_application.add_handler(CallbackQueryHandler(bot_handlers.handle_navigation, pattern='^(back_to_start|new_name|change_category)$'))
        bot_application.add_handler(CallbackQueryHandler(bot_handlers.admin_stats, pattern='^admin_stats$'))
        bot_application.add_handler(CallbackQueryHandler(bot_handlers.admin_broadcast, pattern='^admin_broadcast$'))
        bot_application.add_handler(CallbackQueryHandler(bot_handlers.admin_users, pattern='^admin_users$'))
        
        # Add message handler
        bot_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot_handlers.process_name))
        
        logger.info("✅ Bot initialized successfully")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to initialize bot: {e}", exc_info=True)
        return False

# ==================== MAIN STARTUP ====================
if __name__ == '__main__':
    # Initialize bot
    if not initialize_bot():
        logger.error("❌ Bot initialization failed. Exiting.")
        exit(1)
    
    # Start keep-alive system
    start_keep_alive()
    
    # Log startup info
    logger.info("=" * 60)
    logger.info("🎨 STYLISH NAME BOT STARTING UP")
    logger.info(f"🤖 Bot: Initialized")
    logger.info(f"🌐 Host: Render.com")
    logger.info(f"⏰ Keep-alive: Active (3-minute pings)")
    logger.info("=" * 60)
    
    # Start Flask server
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🚀 Starting server on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)