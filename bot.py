import discord
from discord.ext import commands
from discord.ui import View, Button, Modal, TextInput
import aiosqlite
from datetime import datetime, timedelta, timezone
import asyncio
import os
from flask import Flask, jsonify
from threading import Thread
import logging
import random
from typing import Dict, Optional, List
from discord import app_commands

# ================== FLASK ДЛЯ KEEP-ALIVE ==================
app = Flask('')
logging.getLogger('werkzeug').setLevel(logging.ERROR)

@app.route('/')
def home():
    return "OK", 200

@app.route('/health')
def health():
    return jsonify({"status": "alive", "bot": "running"}), 200

@app.route('/ping')
def ping():
    return "pong", 200

def run():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    server = Thread(target=run)
    server.daemon = True
    server.start()
    print(f"🌐 Веб-сервер для keep-alive запущен на порту {os.environ.get('PORT', 10000)}")

# ================== КОНФИГУРАЦИЯ ==================
TOKEN = os.getenv("TOKEN")
GUILD_ID = 1176162885811060756
LOG_CHANNEL_ID = 1455165169075490963
TICKET_CATEGORY_ID = 1490089159917043833
ORDERS_CHANNEL_ID = 1372910944472006706
BUYER_ROLE = "Покупатель"
REGULAR_ROLE = "Постоянный покупатель"
MIN_ACCOUNT_AGE_DAYS = 3
OWNER_ROLE_ID = 1373760116678987916
TAG_ROLE_ID = 1489575333718921428
TARGET_ROLE_FOR_TAG_ID = 1489575333718921428

# Конфиг для розыгрышей и игр
REVIEW_CHANNEL_ID = 1372671847690272789
GUESS_CHANNEL_ID = 1484247093299118262
WINNER_CHANNEL_ID = 1372910944472006706
ALLOWED_ROLE_ID = 1490014283164160201

if not TOKEN:
    print("❌ ОШИБКА: Токен не найден!")
    exit(1)

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# Кэши и переменные
invites_cache = {}
order_counter = 591
active_giveaways: Dict[str, dict] = {}
active_guess_games: Dict[int, dict] = {}
active_clickers: Dict[str, dict] = {}

# Счётчик отзывов (будет храниться в БД)
review_counter = 402

# ================== БАЗА ДАННЫХ ==================
async def init_db():
    async with aiosqlite.connect("db.sqlite3") as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            invited INTEGER DEFAULT 0,
            left INTEGER DEFAULT 0,
            spent INTEGER DEFAULT 0,
            total_invites INTEGER DEFAULT 0
        )
        """)
        
        await db.execute("""
        CREATE TABLE IF NOT EXISTS purchases (
            user_id INTEGER,
            item TEXT,
            date TEXT,
            order_number INTEGER
        )
        """)
        
        await db.execute("""
        CREATE TABLE IF NOT EXISTS joins (
            user_id INTEGER,
            inviter_id INTEGER,
            join_date TEXT
        )
        """)
        
        await db.execute("""
        CREATE TABLE IF NOT EXISTS invite_history (
            user_id INTEGER,
            inviter_id INTEGER,
            invite_code TEXT,
            date TEXT
        )
        """)
        
        await db.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            order_number INTEGER PRIMARY KEY,
            user_id INTEGER,
            item TEXT,
            status TEXT,
            message_id INTEGER,
            created_at TEXT
        )
        """)
        
        await db.execute("""
        CREATE TABLE IF NOT EXISTS user_stats (
            user_id INTEGER PRIMARY KEY,
            messages INTEGER DEFAULT 0,
            join_date TEXT,
            last_active TEXT
        )
        """)
        
        await db.execute("""
        CREATE TABLE IF NOT EXISTS bot_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """)
        
        await db.execute("""
        CREATE TABLE IF NOT EXISTS giveaway_invites (
            giveaway_key TEXT,
            inviter_id INTEGER,
            invited_user_id INTEGER,
            invite_date TEXT,
            PRIMARY KEY (giveaway_key, invited_user_id)
        )
        """)
        
        await db.execute("""
        INSERT OR IGNORE INTO bot_settings (key, value)
        VALUES ('review_counter', '364')
        """)
        
        await db.commit()

async def get_review_counter():
    async with aiosqlite.connect("db.sqlite3") as db:
        cursor = await db.execute("SELECT value FROM bot_settings WHERE key = 'review_counter'")
        result = await cursor.fetchone()
        if result:
            return int(result[0])
        return 364

async def increment_review_counter():
    async with aiosqlite.connect("db.sqlite3") as db:
        await db.execute("""
        UPDATE bot_settings SET value = CAST(value AS INTEGER) + 1
        WHERE key = 'review_counter'
        """)
        await db.commit()

async def get_next_order_number():
    global order_counter
    async with aiosqlite.connect("db.sqlite3") as db:
        cursor = await db.execute("SELECT MAX(order_number) FROM orders")
        data = await cursor.fetchone()
        if data and data[0]:
            order_counter = data[0] + 1
        else:
            order_counter = 564
    return order_counter

async def migrate_db():
    async with aiosqlite.connect("db.sqlite3") as db:
        cursor = await db.execute("PRAGMA table_info(joins)")
        columns = [column[1] for column in await cursor.fetchall()]
        if "join_date" not in columns:
            try:
                await db.execute("ALTER TABLE joins ADD COLUMN join_date TEXT")
            except:
                pass
        
        cursor = await db.execute("PRAGMA table_info(users)")
        columns = [column[1] for column in await cursor.fetchall()]
        if "total_invites" not in columns:
            try:
                await db.execute("ALTER TABLE users ADD COLUMN total_invites INTEGER DEFAULT 0")
            except:
                pass
        
        cursor = await db.execute("PRAGMA table_info(user_stats)")
        columns = [column[1] for column in await cursor.fetchall()]
        if "messages" not in columns:
            try:
                await db.execute("ALTER TABLE user_stats ADD COLUMN messages INTEGER DEFAULT 0")
            except:
                pass
        if "join_date" not in columns:
            try:
                await db.execute("ALTER TABLE user_stats ADD COLUMN join_date TEXT")
            except:
                pass
        if "last_active" not in columns:
            try:
                await db.execute("ALTER TABLE user_stats ADD COLUMN last_active TEXT")
            except:
                pass
        
        await db.commit()

# ================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==================
def has_permission(interaction: discord.Interaction) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True
    role = discord.utils.get(interaction.user.roles, id=ALLOWED_ROLE_ID)
    return role is not None

def is_fake(member):
    age = datetime.now(timezone.utc) - member.created_at
    return age < timedelta(days=MIN_ACCOUNT_AGE_DAYS)

async def get_invites_count(user_id):
    async with aiosqlite.connect("db.sqlite3") as db:
        cursor = await db.execute("SELECT invited, left, spent FROM users WHERE user_id=?", (user_id,))
        data = await cursor.fetchone()
    if data:
        return data[0] - data[1] - data[2]
    return 0

async def get_giveaway_invites_count(giveaway_key: str, user_id: int):
    async with aiosqlite.connect("db.sqlite3") as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM giveaway_invites WHERE giveaway_key = ? AND inviter_id = ?",
            (giveaway_key, user_id)
        )
        result = await cursor.fetchone()
        return result[0] if result else 0

async def add_giveaway_invite(giveaway_key: str, inviter_id: int, invited_user_id: int):
    async with aiosqlite.connect("db.sqlite3") as db:
        await db.execute("""
        INSERT OR IGNORE INTO giveaway_invites (giveaway_key, inviter_id, invited_user_id, invite_date)
        VALUES (?, ?, ?, datetime('now'))
        """, (giveaway_key, inviter_id, invited_user_id))
        await db.commit()

# ================== ПАГИНАЦИЯ ДЛЯ УЧАСТНИКОВ ==================
class MembersPaginator(View):
    def __init__(self, participants: list, giveaway_key: str, page: int = 0, items_per_page: int = 10):
        super().__init__(timeout=60)
        self.participants = participants
        self.giveaway_key = giveaway_key
        self.page = page
        self.items_per_page = items_per_page
        self.total_pages = (len(participants) + items_per_page - 1) // items_per_page if participants else 1
    
    def get_page_content(self, interaction: discord.Interaction):
        giveaway = active_giveaways.get(self.giveaway_key, {})
        start = self.page * self.items_per_page
        end = start + self.items_per_page
        page_participants = self.participants[start:end]
        total_participants = len(self.participants)
        
        text = f"**📋 Участники розыгрыша** (всего: {total_participants})\n\n"
        
        for uid in page_participants:
            user = interaction.guild.get_member(uid)
            if not user:
                continue
            
            base_chance = (giveaway.get("winners_count", 1) / total_participants) * 100 if total_participants > 0 else 0
            invite_bonus = giveaway.get("invite_bonus", {}).get(uid, 0)
            final_chance = min(base_chance + (invite_bonus * 10), 100)
            
            text += f"• {user.mention} — **{final_chance:.1f}%**"
            if invite_bonus > 0:
                text += f" (+{invite_bonus * 10}% за {invite_bonus} приглашённых)"
            text += "\n"
        
        text += f"\n📄 Страница {self.page + 1} из {self.total_pages}"
        return text
    
    @discord.ui.button(label="◀️ Назад", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: Button):
        if self.page > 0:
            self.page -= 1
            await interaction.response.edit_message(content=self.get_page_content(interaction), view=self)
        else:
            await interaction.response.send_message("Это первая страница!", ephemeral=True)
    
    @discord.ui.button(label="Вперед ▶️", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: Button):
        if self.page < self.total_pages - 1:
            self.page += 1
            await interaction.response.edit_message(content=self.get_page_content(interaction), view=self)
        else:
            await interaction.response.send_message("Это последняя страница!", ephemeral=True)

# ================== КЛИКЕР-РОЗЫГРЫШ ==================
class ClickerModal(Modal, title="🎮 Создание кликер-розыгрыша (скрытый клик)"):
    prize = TextInput(label="🎁 ПРИЗ", placeholder="Что выигрывает победитель?", required=True, max_length=200)
    target_clicks = TextInput(label="🎯 Целевое количество кликов", placeholder="Например: 1000", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        if not has_permission(interaction):
            await interaction.response.send_message("❌ У вас нет прав!", ephemeral=True)
            return
        
        try:
            target = int(self.target_clicks.value)
            if target < 10:
                await interaction.response.send_message("❌ Целевое количество кликов должно быть не менее 10!", ephemeral=True)
                return
        except ValueError:
            await interaction.response.send_message("❌ Введите корректное число!", ephemeral=True)
            return
        
        winning_click = random.randint(1, target)
        clicker_id = f"{interaction.channel.id}_{datetime.now().timestamp()}"
        
        clicker_data = {
            "type": "hidden",
            "prize": self.prize.value,
            "target_clicks": target,
            "winning_click": winning_click,
            "current_clicks": 0,
            "participants_clicks": {},
            "winner": None,
            "creator_id": interaction.user.id,
            "creator_name": interaction.user.display_name,
            "channel_id": interaction.channel_id,
            "active": True
        }
        
        active_clickers[clicker_id] = clicker_data
        
        embed = discord.Embed(
            title="🎮 КЛИКЕР-РОЗЫГРЫШ!",
            description=f"**Приз:** {self.prize.value}\n\n"
                       f"**Цель:** {target} кликов\n"
                       f"**Секрет:** Победный клик спрятан! 🔥\n\n"
                       f"Кликни и, возможно, именно ТЫ станешь победителем!\n\n"
                       f"**Текущий прогресс:** 0/{target} кликов\n"
                       f"**Участников:** 0",
            color=discord.Color.gold()
        )
        embed.set_footer(text=f"Создал: {interaction.user.display_name} | Каждый может кликать сколько угодно! Победный клик НЕ ВИДЕН")
        
        view = ClickerView(clicker_id)
        await interaction.response.send_message(embed=embed, view=view)


class ClickerTopModal(Modal, title="🎮 Создание кликер-конкурса (на время)"):
    prize = TextInput(label="🎁 ПРИЗ", placeholder="Что выигрывает победитель?", required=True, max_length=200)
    duration = TextInput(label="⏰ Длительность (в минутах)", placeholder="Например: 5, 10, 30", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        if not has_permission(interaction):
            await interaction.response.send_message("❌ У вас нет прав!", ephemeral=True)
            return
        
        try:
            duration_minutes = int(self.duration.value)
            if duration_minutes < 1 or duration_minutes > 60:
                await interaction.response.send_message("❌ Длительность должна быть от 1 до 60 минут!", ephemeral=True)
                return
        except ValueError:
            await interaction.response.send_message("❌ Введите корректное число минут!", ephemeral=True)
            return
        
        end_time = datetime.now() + timedelta(minutes=duration_minutes)
        clicker_id = f"top_{interaction.channel.id}_{datetime.now().timestamp()}"
        
        clicker_data = {
            "type": "top",
            "prize": self.prize.value,
            "duration_minutes": duration_minutes,
            "end_time": end_time,
            "current_clicks": 0,
            "participants_clicks": {},
            "winner": None,
            "creator_id": interaction.user.id,
            "creator_name": interaction.user.display_name,
            "channel_id": interaction.channel_id,
            "active": True
        }
        
        active_clickers[clicker_id] = clicker_data
        
        embed = discord.Embed(
            title="🎮 КЛИКЕР-КОНКУРС!",
            description=f"**Приз:** {self.prize.value}\n\n"
                       f"**Время:** {duration_minutes} минут\n"
                       f"**Правило:** Кто больше всех кликнет за отведённое время - тот победит!\n\n"
                       f"**Текущий прогресс:** 0 кликов\n"
                       f"**Участников:** 0",
            color=discord.Color.purple()
        )
        embed.set_footer(text=f"Создал: {interaction.user.display_name} | Конкурс закончится через {duration_minutes} минут")
        embed.timestamp = end_time
        
        view = ClickerView(clicker_id)
        await interaction.response.send_message(embed=embed, view=view)
        
        asyncio.create_task(end_top_clicker(clicker_id, end_time))


async def end_top_clicker(clicker_id: str, end_time: datetime):
    wait_seconds = (end_time - datetime.now()).total_seconds()
    if wait_seconds > 0:
        await asyncio.sleep(wait_seconds)
    
    clicker = active_clickers.get(clicker_id)
    if not clicker or not clicker.get("active", False):
        return
    
    clicker["active"] = False
    
    if clicker["participants_clicks"]:
        winner_id = max(clicker["participants_clicks"].items(), key=lambda x: x[1])[0]
        winner_clicks = clicker["participants_clicks"][winner_id]
        clicker["winner"] = winner_id
        
        top_clickers = sorted(clicker["participants_clicks"].items(), key=lambda x: x[1], reverse=True)[:5]
        top_text = "\n".join([f"• <@{uid}> — {count} кликов" for uid, count in top_clickers])
        
        orders_channel = bot.get_channel(ORDERS_CHANNEL_ID)
        if orders_channel:
            order_embed = discord.Embed(
                title="🎮 КЛИКЕР-КОНКУРС ЗАВЕРШЁН!",
                description=f"**Победитель:** <@{winner_id}>\n"
                           f"**Приз:** {clicker['prize']}\n"
                           f"**Всего кликов:** {clicker['current_clicks']}\n"
                           f"**Участников:** {len(clicker['participants_clicks'])}\n\n"
                           f"**Топ-5 кликеров:**\n{top_text}",
                color=discord.Color.green()
            )
            order_embed.set_footer(text=f"Создал: {clicker['creator_name']}")
            await orders_channel.send(embed=order_embed)
    
    async def remove_clicker():
        await asyncio.sleep(10)
        if clicker_id in active_clickers:
            del active_clickers[clicker_id]
    asyncio.create_task(remove_clicker())


class ClickerView(View):
    def __init__(self, clicker_id: str):
        super().__init__(timeout=None)
        self.clicker_id = clicker_id
    
    @discord.ui.button(label="🔘 КЛИК!", style=discord.ButtonStyle.primary, custom_id="click_button")
    async def click_button(self, interaction: discord.Interaction, button: Button):
        clicker = active_clickers.get(self.clicker_id)
        
        if not clicker or not clicker.get("active", False):
            await interaction.response.send_message("❌ Этот розыгрыш уже завершён!", ephemeral=True)
            return
        
        clicker["current_clicks"] += 1
        
        if interaction.user.id not in clicker["participants_clicks"]:
            clicker["participants_clicks"][interaction.user.id] = 0
        clicker["participants_clicks"][interaction.user.id] += 1
        
        if clicker["type"] == "hidden":
            embed = discord.Embed(
                title="🎮 КЛИКЕР-РОЗЫГРЫШ!",
                description=f"**Приз:** {clicker['prize']}\n\n"
                           f"**Цель:** {clicker['target_clicks']} кликов\n"
                           f"**Секрет:** Победный клик спрятан! 🔥\n\n"
                           f"Кликни и, возможно, именно ТЫ станешь победителем!\n\n"
                           f"**Текущий прогресс:** {clicker['current_clicks']}/{clicker['target_clicks']} кликов\n"
                           f"**Участников:** {len(clicker['participants_clicks'])}\n\n"
                           f"**Ваш личный счёт:** {clicker['participants_clicks'][interaction.user.id]} кликов",
                color=discord.Color.gold()
            )
            embed.set_footer(text=f"Создал: {clicker['creator_name']} | Каждый может кликать! Победный клик НЕ ВИДЕН")
            await interaction.message.edit(embed=embed)
            
            if clicker["current_clicks"] == clicker["winning_click"]:
                clicker["active"] = False
                clicker["winner"] = interaction.user.id
                
                top_clickers = sorted(clicker["participants_clicks"].items(), key=lambda x: x[1], reverse=True)[:5]
                top_text = "\n".join([f"• <@{uid}> — {count} кликов" for uid, count in top_clickers])
                
                winner_embed = discord.Embed(
                    title="🎉 ПОБЕДИТЕЛЬ КЛИКЕР-РОЗЫГРЫША! 🎉",
                    description=f"**Победитель:** {interaction.user.mention}\n"
                               f"**Приз:** {clicker['prize']}\n"
                               f"**Счастливый клик:** {clicker['winning_click']}/{clicker['target_clicks']}\n\n"
                               f"**Топ-5 кликеров:**\n{top_text}\n\n"
                               f"Поздравляем! 🎊",
                    color=discord.Color.green()
                )
                await interaction.message.edit(embed=winner_embed, view=None)
                
                orders_channel = bot.get_channel(ORDERS_CHANNEL_ID)
                if orders_channel:
                    order_embed = discord.Embed(
                        title="🎮 КЛИКЕР-РОЗЫГРЫШ ЗАВЕРШЁН!",
                        description=f"**Победитель:** {interaction.user.mention}\n"
                                   f"**Приз:** {clicker['prize']}\n"
                                   f"**Всего кликов:** {clicker['current_clicks']}\n"
                                   f"**Участников:** {len(clicker['participants_clicks'])}\n\n"
                                   f"**Топ-5 кликеров:**\n{top_text}",
                        color=discord.Color.green()
                    )
                    order_embed.set_footer(text=f"Создал: {clicker['creator_name']}")
                    await orders_channel.send(embed=order_embed)
                
                await interaction.response.send_message(f"🎉 **ПОЗДРАВЛЯЮ!** Вы сделали счастливый клик и выиграли **{clicker['prize']}**! 🎉", ephemeral=True)
                
                async def remove_clicker():
                    await asyncio.sleep(10)
                    if self.clicker_id in active_clickers:
                        del active_clickers[self.clicker_id]
                asyncio.create_task(remove_clicker())
            else:
                await interaction.response.send_message(f"✅ Вы кликнули! Прогресс: {clicker['current_clicks']}/{clicker['target_clicks']}\nВаш личный счёт: {clicker['participants_clicks'][interaction.user.id]} кликов", ephemeral=True)
        
        else:
            remaining = clicker["end_time"] - datetime.now()
            remaining_minutes = int(remaining.total_seconds() // 60)
            remaining_seconds = int(remaining.total_seconds() % 60)
            
            embed = discord.Embed(
                title="🎮 КЛИКЕР-КОНКУРС!",
                description=f"**Приз:** {clicker['prize']}\n\n"
                           f"**Время:** {clicker['duration_minutes']} минут\n"
                           f"**Осталось:** {remaining_minutes}м {remaining_seconds}с\n"
                           f"**Правило:** Кто больше всех кликнет - тот победит!\n\n"
                           f"**Текущий прогресс:** {clicker['current_clicks']} кликов\n"
                           f"**Участников:** {len(clicker['participants_clicks'])}\n\n"
                           f"**Ваш личный счёт:** {clicker['participants_clicks'][interaction.user.id]} кликов",
                color=discord.Color.purple()
            )
            embed.set_footer(text=f"Создал: {clicker['creator_name']} | Конкурс идёт!")
            embed.timestamp = clicker["end_time"]
            await interaction.message.edit(embed=embed)
            
            await interaction.response.send_message(f"✅ Вы кликнули! Всего кликов: {clicker['current_clicks']}\nВаш личный счёт: {clicker['participants_clicks'][interaction.user.id]} кликов", ephemeral=True)


# ================== РОЗЫГРЫШИ ==================
def build_giveaway_message(giveaway: dict, user_id: Optional[int] = None):
    embed = discord.Embed(
        title=f"🎁 {giveaway['prize']}",
        description=giveaway["description"],
        color=discord.Color.gold(),
        timestamp=giveaway["end_time"]
    )
    
    remaining = giveaway["end_time"] - datetime.now()
    if remaining.total_seconds() > 0:
        days = remaining.days
        hours = remaining.seconds // 3600
        minutes = (remaining.seconds % 3600) // 60
        
        if days > 0:
            time_str = f"{days}д {hours}ч"
        elif hours > 0:
            time_str = f"{hours}ч {minutes}м"
        else:
            time_str = f"{minutes}м"
        embed.add_field(name="⏰ Окончание", value=time_str, inline=True)
    else:
        embed.add_field(name="⏰ Окончание", value="Завершён", inline=True)
    
    embed.add_field(name="👤 Создал", value=giveaway["creator_name"], inline=True)
    embed.add_field(name="👥 Участников", value=str(len(giveaway["participants"])), inline=True)
    embed.add_field(name="🏆 Победителей", value=str(giveaway["winners_count"]), inline=True)
    
    embed.set_footer(text="🎁 +10% шанс за каждого приглашённого ДРУГА во время розыгрыша!")
    
    view = GiveawayView(giveaway)
    return embed, view

class GiveawayView(discord.ui.View):
    def __init__(self, giveaway: dict):
        super().__init__(timeout=None)
        self.giveaway = giveaway
    
    @discord.ui.button(label="✅ Участвовать", style=discord.ButtonStyle.success, custom_id="giveaway_join")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        key = f"{interaction.channel.id}_{self.giveaway['message_id']}"
        
        if key not in active_giveaways:
            await interaction.response.send_message("❌ Розыгрыш уже завершён", ephemeral=True)
            return
        
        giveaway = active_giveaways[key]
        
        if interaction.user.id in giveaway["participants"]:
            await interaction.response.send_message("⚠️ Вы уже участвуете!", ephemeral=True)
            return
        
        if datetime.now() > giveaway["end_time"]:
            await interaction.response.send_message("❌ Розыгрыш уже закончился", ephemeral=True)
            return
        
        giveaway["participants"].append(interaction.user.id)
        
        invite_bonus = await get_giveaway_invites_count(key, interaction.user.id)
        giveaway["invite_bonus"][interaction.user.id] = invite_bonus
        
        embed, _ = build_giveaway_message(giveaway, None)
        message = await interaction.channel.fetch_message(giveaway["message_id"])
        await message.edit(embed=embed)
        
        await interaction.response.send_message("✅ Вы участвуете в розыгрыше!", ephemeral=True)
    
    @discord.ui.button(label="📋 Участники", style=discord.ButtonStyle.secondary, custom_id="giveaway_members")
    async def members_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        key = f"{interaction.channel.id}_{self.giveaway['message_id']}"
        
        if key not in active_giveaways:
            await interaction.response.send_message("❌ Розыгрыш не найден", ephemeral=True)
            return
        
        giveaway = active_giveaways[key]
        participants = giveaway["participants"]
        
        if not participants:
            await interaction.response.send_message("📭 Пока нет участников", ephemeral=True)
            return
        
        paginator = MembersPaginator(participants, key)
        await interaction.response.send_message(paginator.get_page_content(interaction), view=paginator, ephemeral=True)
    
    @discord.ui.button(label="🎲 Шанс", style=discord.ButtonStyle.primary, custom_id="giveaway_chance")
    async def chance_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        key = f"{interaction.channel.id}_{self.giveaway['message_id']}"
        
        if key not in active_giveaways:
            await interaction.response.send_message("❌ Розыгрыш не найден", ephemeral=True)
            return
        
        giveaway = active_giveaways[key]
        
        if interaction.user.id not in giveaway["participants"]:
            await interaction.response.send_message("❌ Вы не участвуете", ephemeral=True)
            return
        
        count = len(giveaway["participants"])
        base_chance = (giveaway["winners_count"] / count) * 100 if count > 0 else 0
        invite_bonus = giveaway["invite_bonus"].get(interaction.user.id, 0)
        final_chance = min(base_chance + (invite_bonus * 10), 100)
        
        embed = discord.Embed(
            title="🎲 Ваш шанс на победу",
            description=f"**{final_chance:.2f}%**",
            color=discord.Color.blue()
        )
        embed.add_field(name="📊 Базовый шанс", value=f"{base_chance:.2f}%", inline=True)
        embed.add_field(name="🎁 Бонус за приглашения", value=f"+{invite_bonus * 10}% ({invite_bonus} приглашённых)", inline=True)
        embed.add_field(name="👥 Всего участников", value=str(count), inline=True)
        embed.add_field(name="🏆 Победителей", value=str(giveaway["winners_count"]), inline=True)
        embed.set_footer(text="💡 Приглашайте друзей во время розыгрыша — каждый приглашённый даёт +10% к шансу!")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def end_giveaway(channel_id: int, message_id: int, reroll: bool = False):
    key = f"{channel_id}_{message_id}"
    
    if key not in active_giveaways:
        return False, None
    
    giveaway = active_giveaways[key]
    participants = giveaway["participants"]
    winners_count = giveaway["winners_count"]
    
    winners = []
    if participants:
        weighted_list = []
        for uid in participants:
            count = len(participants)
            base_chance = (winners_count / count)
            invite_bonus = giveaway["invite_bonus"].get(uid, 0)
            weight = base_chance + (invite_bonus * 0.1)
            weighted_list.extend([uid] * max(1, int(weight * 100)))
        
        random.shuffle(weighted_list)
        unique_winners = []
        for uid in weighted_list:
            if uid not in unique_winners:
                unique_winners.append(uid)
            if len(unique_winners) >= winners_count:
                break
        winners = unique_winners
    
    channel = bot.get_channel(channel_id)
    if not channel:
        return False, None
    
    try:
        message = await channel.fetch_message(message_id)
    except:
        return False, None
    
    if winners:
        winner_mentions = [f"<@{uid}>" for uid in winners]
        result_text = f"**🏆 ПОБЕДИТЕЛИ:**\n{', '.join(winner_mentions)}\n\n🎉 ПОЗДРАВЛЯЕМ!"
    else:
        result_text = "😞 В розыгрыше никто не участвовал. Победителей нет."
    
    embed = discord.Embed(
        title=f"🎁 {giveaway['prize']} — ЗАВЕРШЁН",
        description=f"{giveaway['description']}\n\n{result_text}",
        color=discord.Color.green()
    )
    embed.add_field(name="👥 Участников", value=str(len(participants)), inline=True)
    embed.add_field(name="🏆 Победителей", value=str(len(winners)), inline=True)
    embed.timestamp = datetime.now()
    
    await message.edit(embed=embed, view=None)
    
    if winners:
        winner_channel = bot.get_channel(WINNER_CHANNEL_ID)
        if winner_channel:
            await winner_channel.send(
                f"🎉 **РОЗЫГРЫШ ЗАВЕРШЁН!** 🎉\n\n"
                f"**Приз:** {giveaway['prize']}\n"
                f"**Победители:** {', '.join(winner_mentions)}\n\n"
                f"Поздравляем!"
            )
    
    async with aiosqlite.connect("db.sqlite3") as db:
        await db.execute("DELETE FROM giveaway_invites WHERE giveaway_key = ?", (key,))
        await db.commit()
    
    if not reroll:
        del active_giveaways[key]
    
    return winners, giveaway

async def end_giveaway_timer(channel_id: int, message_id: int, end_time: datetime):
    wait_seconds = (end_time - datetime.now()).total_seconds()
    if wait_seconds > 0:
        await asyncio.sleep(wait_seconds)
    
    await end_giveaway(channel_id, message_id, reroll=False)

class GiveawayModal(discord.ui.Modal, title="🎁 Создание розыгрыша"):
    duration = discord.ui.TextInput(
        label="⏰ Время (пример: 30м, 2ч, 1д)",
        placeholder="30м / 2ч / 1д",
        required=True
    )
    winners = discord.ui.TextInput(
        label="🏆 Количество победителей",
        placeholder="от 1 до 10",
        required=True
    )
    prize = discord.ui.TextInput(
        label="🎁 ПРИЗ",
        placeholder="Например: 1000 рублей",
        required=True,
        max_length=200
    )
    description = discord.ui.TextInput(
        label="📝 Описание",
        placeholder="Текст розыгрыша...",
        required=True,
        style=discord.TextStyle.paragraph,
        max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not has_permission(interaction):
            await interaction.response.send_message("❌ У вас нет прав!", ephemeral=True)
            return
            
        duration_str = self.duration.value.lower()
        seconds = 0
        if duration_str.endswith("м"):
            seconds = int(duration_str[:-1]) * 60
        elif duration_str.endswith("ч"):
            seconds = int(duration_str[:-1]) * 3600
        elif duration_str.endswith("д"):
            seconds = int(duration_str[:-1]) * 86400
        else:
            await interaction.response.send_message("❌ Неверный формат времени!", ephemeral=True)
            return

        try:
            winners_count = int(self.winners.value)
            if winners_count < 1 or winners_count > 10:
                raise ValueError
        except:
            await interaction.response.send_message("❌ Количество победителей от 1 до 10", ephemeral=True)
            return

        end_time = datetime.now() + timedelta(seconds=seconds)

        giveaway_data = {
            "prize": self.prize.value,
            "description": self.description.value,
            "end_time": end_time,
            "creator_id": interaction.user.id,
            "creator_name": interaction.user.display_name,
            "winners_count": winners_count,
            "participants": [],
            "channel_id": interaction.channel_id,
            "invite_bonus": {},
            "start_time": datetime.now()
        }

        embed, view = build_giveaway_message(giveaway_data, None)
        message = await interaction.channel.send(embed=embed, view=view)

        giveaway_data["message_id"] = message.id
        active_giveaways[f"{message.channel.id}_{message.id}"] = giveaway_data

        await interaction.response.send_message("✅ Розыгрыш создан!", ephemeral=True)
        asyncio.create_task(end_giveaway_timer(message.channel.id, message.id, end_time))

# ================== ИГРА УГАДАЙ ЧИСЛО ==================
class GuessNumberGame:
    def __init__(self, channel_id: int, target_number: int, prize: str):
        self.channel_id = channel_id
        self.target_number = target_number
        self.prize = prize
        self.active = True
        self.winner = None
        self.start_time = datetime.now()
    
    async def check_guess(self, message: discord.Message):
        if not self.active:
            return False
        
        try:
            guess = int(message.content.strip())
            if guess == self.target_number:
                self.active = False
                self.winner = message.author.id
                
                winner_channel = bot.get_channel(WINNER_CHANNEL_ID)
                if winner_channel:
                    await winner_channel.send(
                        f"🎉 **УГАДАЙ ЧИСЛО — ПОБЕДИТЕЛЬ!** 🎉\n\n"
                        f"**Правильное число:** {self.target_number}\n"
                        f"**Победитель:** {message.author.mention}\n"
                        f"**Приз:** {self.prize}\n\n"
                        f"Поздравляем!"
                    )
                
                await message.channel.send(
                    f"🎉 **{message.author.mention} угадал число {self.target_number}!** 🎉\n"
                    f"Игра завершена! Победитель получит: {self.prize}"
                )
                return True
        except ValueError:
            pass
        return False

# ================== ТИКЕТЫ ==================
class TicketView(View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="❌ Закрыть тикет", style=discord.ButtonStyle.red)
    async def close(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("Тикет будет закрыт через 5 секунд...")
        await asyncio.sleep(5)
        await interaction.channel.delete()

# ================== МАГАЗИН ==================
class Shop(View):
    def __init__(self):
        super().__init__(timeout=None)
    
    async def get_user(self, user_id):
        async with aiosqlite.connect("db.sqlite3") as db:
            cursor = await db.execute("SELECT invited, left, spent FROM users WHERE user_id=?", (user_id,))
            return await cursor.fetchone()
    
    async def process(self, interaction, cost, item_name, item_description):
        data = await self.get_user(interaction.user.id)
        invited, left, spent = data if data else (0,0,0)
        valid = invited - left - spent
        
        if valid < cost:
            await interaction.response.send_message(f"❌ Недостаточно инвайтов! Нужно: {cost}, у вас: {valid}", ephemeral=True)
            return
        
        guild = interaction.guild
        category = discord.utils.get(guild.categories, id=TICKET_CATEGORY_ID)
        if not category:
            await interaction.response.send_message("❌ Категория для тикетов не найдена!", ephemeral=True)
            return
        
        channel = await guild.create_text_channel(f"заказ-{interaction.user.name}", category=category)
        await channel.set_permissions(interaction.user, read_messages=True, send_messages=True)
        await channel.set_permissions(guild.default_role, read_messages=False)
        
        order_number = await get_next_order_number()
        
        embed = discord.Embed(
            title="🛒 Новый заказ",
            description=f"**Товар:** {item_name}\n{item_description}\n**Цена:** {cost} инвайтов\n**Номер заказа:** #{order_number}",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"Заказчик: {interaction.user.name}")
        await channel.send(content=f"{interaction.user.mention}", embed=embed, view=TicketView())
        
        orders_channel = bot.get_channel(ORDERS_CHANNEL_ID)
        order_message = None
        if orders_channel:
            order_embed = discord.Embed(
                title=f"📦 Заказ #{order_number}",
                description=f"**Пользователь:** {interaction.user.mention}\n**Тег:** {interaction.user.name}\n**Заказано:** {item_name}\n**Цена:** {cost} инвайтов",
                color=discord.Color.orange()
            )
            order_embed.add_field(name="Статус", value="⏳ Ожидается", inline=False)
            order_embed.set_footer(text=f"ID: {interaction.user.id}")
            order_message = await orders_channel.send(embed=order_embed)
        
        async with aiosqlite.connect("db.sqlite3") as db:
            await db.execute("UPDATE users SET spent = spent + ? WHERE user_id=?", (cost, interaction.user.id))
            await db.execute("INSERT INTO purchases (user_id, item, date, order_number) VALUES (?, ?, datetime('now'), ?)",
                           (interaction.user.id, f"{item_name} ({cost} инвайтов)", order_number))
            await db.execute("INSERT INTO orders (order_number, user_id, item, status, message_id, created_at) VALUES (?, ?, ?, 'Ожидается', ?, datetime('now'))",
                           (order_number, interaction.user.id, item_name, order_message.id if order_message else 0))
            await db.commit()
        
        buyer = discord.utils.get(guild.roles, name=BUYER_ROLE)
        regular = discord.utils.get(guild.roles, name=REGULAR_ROLE)
        if buyer:
            await interaction.user.add_roles(buyer)
        
        cursor = await db.execute("SELECT COUNT(*) FROM purchases WHERE user_id=?", (interaction.user.id,))
        count = (await cursor.fetchone())[0]
        if count >= 2 and regular:
            await interaction.user.add_roles(regular)
        
        await interaction.response.send_message(f"✅ Заказ #{order_number} создан! Перейдите в канал {channel.mention}", ephemeral=True)
    
    @discord.ui.button(label="🟠 1 задание (700 Orbs) - 3 инвайта", style=discord.ButtonStyle.green)
    async def b1(self, interaction: discord.Interaction, button: Button):
        await self.process(interaction, 3, "1 задание (700 Orbs)", "Выполнение одного задания Discord за 💎 700 Orbs")
    
    @discord.ui.button(label="🔵 Задание с украшением - 3 инвайта", style=discord.ButtonStyle.blurple)
    async def b2(self, interaction: discord.Interaction, button: Button):
        await self.process(interaction, 3, "Задание с украшением", "Выполнение задания с получением украшения профиля")
    
    @discord.ui.button(label="🩷 2 задания (1400 Orbs) - 5 инвайтов", style=discord.ButtonStyle.green)
    async def b3(self, interaction: discord.Interaction, button: Button):
        await self.process(interaction, 5, "2 задания (1400 Orbs)", "Выполнение двух заданий Discord за 💎 1400 Orbs")
    
    @discord.ui.button(label="🟡 Все задания - 10 инвайтов", style=discord.ButtonStyle.blurple)
    async def b4(self, interaction: discord.Interaction, button: Button):
        await self.process(interaction, 10, "Все доступные задания", "Выполнение всех доступных заданий на аккаунте")
    
    @discord.ui.button(label="🎁 Nitro Full (3 дня) - 5 инвайтов", style=discord.ButtonStyle.green)
    async def b5(self, interaction: discord.Interaction, button: Button):
        await self.process(interaction, 5, "Nitro Full на 3 дня", "Получение Discord Nitro на 3 дня")
    
    @discord.ui.button(label="🟠 1 задание (200 Orbs) - 2 инвайта", style=discord.ButtonStyle.blurple)
    async def b6(self, interaction: discord.Interaction, button: Button):
        await self.process(interaction, 2, "1 задание (200 Orbs)", "Выполнение одного задания Discord за 💎 200 Orbs")
    
    @discord.ui.button(label="🎨 Значок HypeSquad - 1 инвайт", style=discord.ButtonStyle.blurple)
    async def b7(self, interaction: discord.Interaction, button: Button):
        await self.process(interaction, 1, "Значок HypeSquad (3 цвета на выбор)", "Выдача значка HypeSquad (Brilliance/Bravery/Balance на выбор)")
    
    @discord.ui.button(label="🍃 Значок зелёного листочка - 2 инвайта", style=discord.ButtonStyle.green)
    async def b8(self, interaction: discord.Interaction, button: Button):
        await self.process(interaction, 2, "Значок зелёного листочка", "Выдача эксклюзивного значка зелёного листочка")

# ================== КОМАНДЫ БОТА ==================
@bot.tree.command(name="help", description="Показать список всех доступных команд")
async def help_command(interaction: discord.Interaction):
    is_admin = interaction.user.guild_permissions.administrator
    
    embed = discord.Embed(
        title="📚 Список команд",
        description="Вот все доступные команды бота:",
        color=discord.Color.blue()
    )
    
    embed.add_field(
        name="📋 Общие команды",
        value=(
            "`/help` - Показать это меню\n"
            "`/info` - Информация о сервисе\n"
            "`/shop` - Открыть магазин\n"
            "`/invites` - Ваша статистика приглашений\n"
            "`/top` - Топ 10 инвайтеров\n"
            "`/server` - Статистика сервера"
        ),
        inline=False
    )
    
    embed.add_field(
        name="🎁 Розыгрыши и игры",
        value=(
            "`/gcreate` - Создать розыгрыш\n"
            "`/gend <message_id>` - Завершить розыгрыш\n"
            "`/greroll <message_id>` - Перевыбрать победителей\n"
            "`/gdelete <message_id>` - Удалить розыгрыш\n"
            "`/gmp <приз>` - Запустить игру 'Угадай число'\n"
            "`/gclick` - Создать кликер-розыгрыш\n"
            "`/gclicktop` - Создать кликер-конкурс"
        ),
        inline=False
    )
    
    if is_admin:
        embed.add_field(
            name="👑 Административные команды",
            value=(
                "`/giveinvites <user> <amount>` - Выдать инвайты\n"
                "`/takeinvites <user> <amount>` - Забрать инвайты\n"
                "`/reset_user <user>` - Сбросить статистику\n"
                "`/sync` - Синхронизировать команды\n"
                "`/successful <order_number>` - Отметить заказ выполненным\n"
                "`/stats <user>` - Полная статистика игрока"
            ),
            inline=False
        )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="gclick", description="🎮 Создать кликер-розыгрыш (скрытый победный клик)")
async def gclick_command(interaction: discord.Interaction):
    if not has_permission(interaction):
        await interaction.response.send_message("❌ У вас нет прав! Требуется роль или права администратора.", ephemeral=True)
        return
    
    modal = ClickerModal()
    await interaction.response.send_modal(modal)


@bot.tree.command(name="gclicktop", description="🏆 Создать кликер-конкурс (кто больше кликнет за время)")
async def gclicktop_command(interaction: discord.Interaction):
    if not has_permission(interaction):
        await interaction.response.send_message("❌ У вас нет прав! Требуется роль или права администратора.", ephemeral=True)
        return
    
    modal = ClickerTopModal()
    await interaction.response.send_modal(modal)


@bot.tree.command(name="info", description="Информация о сервисе помощи с заданиями")
async def info(interaction: discord.Interaction):
    embed = discord.Embed(
        title="👑 Информация о сервисе помощи с заданиями",
        description="Добро пожаловать! 👑",
        color=discord.Color.purple()
    )
    
    embed.add_field(
        name="🤝 О нас",
        value="Мы помогаем участникам выполнять задания Discord!",
        inline=False
    )
    
    embed.add_field(
        name="⭐ Что мы даем?",
        value="• Украшения профиля\n• Orbs (валюта Discord)\n• Награды за выполнение заданий\n• Значки HypeSquad\n• Значок зелёного листочка",
        inline=False
    )
    
    embed.add_field(
        name="💰 Прайс-лист",
        value=(
            "🟠 1 задание (700 Orbs) - **3 инвайта**\n"
            "🔵 Задание с украшением - **3 инвайта**\n"
            "🩷 2 задания (1400 Orbs) - **5 инвайтов**\n"
            "🟡 Все задания - **10 инвайтов**\n"
            "🎁 Nitro Full (3 дня) - **5 инвайтов**\n"
            "🟠 1 задание (200 Orbs) - **2 инвайта**\n"
            "🎨 Значок HypeSquad (3 цвета на выбор) - **1 инвайт**\n"
            "🍃 Новый значок зелёного листочка - **2 инвайта**"
        ),
        inline=False
    )
    
    embed.add_field(
        name="⚠️ Гарантии",
        value="• Безопасность\n• Быстрое выполнение\n• Поддержка 24/7",
        inline=False
    )
    
    embed.add_field(name="📞 Как заказать?", value="Используйте команду `/shop`!", inline=False)
    embed.set_footer(text="Ваши инвайты = ваши награды")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="shop", description="Открыть магазин")
async def shop(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🛒 Магазин услуг",
        description="Выберите услугу для заказа",
        color=discord.Color.purple()
    )
    
    embed.add_field(name="🟠 1 задание (700 Orbs)", value="**Цена:** 3 инвайта", inline=False)
    embed.add_field(name="🔵 Задание с украшением", value="**Цена:** 3 инвайта", inline=False)
    embed.add_field(name="🩷 2 задания (1400 Orbs)", value="**Цена:** 5 инвайтов", inline=False)
    embed.add_field(name="🟡 Все задания", value="**Цена:** 10 инвайтов", inline=False)
    embed.add_field(name="🎁 Nitro Full (3 дня)", value="**Цена:** 5 инвайтов", inline=False)
    embed.add_field(name="🟠 1 задание (200 Orbs)", value="**Цена:** 2 инвайта", inline=False)
    embed.add_field(name="🎨 Значок HypeSquad", value="**Цена:** 1 инвайт (3 цвета на выбор)", inline=False)
    embed.add_field(name="🍃 Значок зелёного листочка", value="**Цена:** 2 инвайта", inline=False)
    
    embed.set_footer(text="💰 Оплата вашими приглашениями | /invites - проверить баланс")
    
    view = Shop()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@bot.tree.command(name="invites", description="Показать статистику ваших приглашений")
async def invites(interaction: discord.Interaction):
    async with aiosqlite.connect("db.sqlite3") as db:
        cursor = await db.execute("SELECT invited, left, spent, total_invites FROM users WHERE user_id=?", (interaction.user.id,))
        data = await cursor.fetchone()
        
        if data:
            invited, left, spent, total_invites = data
            valid = invited - left - spent
        else:
            invited = left = spent = total_invites = 0
            valid = 0
        
        cursor = await db.execute("SELECT item, date FROM purchases WHERE user_id=? ORDER BY date DESC LIMIT 10", (interaction.user.id,))
        purchases = await cursor.fetchall()
    
    embed = discord.Embed(title=f"📊 Статистика {interaction.user.name}", color=discord.Color.blue())
    embed.add_field(name="✅ Доступно", value=f"**{valid}**", inline=True)
    embed.add_field(name="📥 Пригласил", value=f"{invited}", inline=True)
    embed.add_field(name="📤 Вышли", value=f"{left}", inline=True)
    embed.add_field(name="💸 Потрачено", value=f"{spent}", inline=True)
    embed.add_field(name="📈 Всего инвайтов", value=f"{total_invites}", inline=True)
    
    if purchases:
        history = "\n".join([f"• {item} ({date[:10]})" for item, date in purchases])
        embed.add_field(name="🛒 Последние покупки", value=history, inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="top", description="Топ 10 пользователей по приглашениям")
async def top(interaction: discord.Interaction):
    async with aiosqlite.connect("db.sqlite3") as db:
        cursor = await db.execute("""
        SELECT user_id, invited - left - spent as total
        FROM users WHERE invited - left - spent > 0
        ORDER BY total DESC LIMIT 10
        """)
        data = await cursor.fetchall()
    
    if not data:
        await interaction.response.send_message("📊 Пока нет пользователей с приглашениями!", ephemeral=True)
        return
    
    embed = discord.Embed(title="🏆 ТОП 10 ИНВАЙТЕРОВ", color=discord.Color.gold())
    text = ""
    for i, (user_id, total) in enumerate(data, 1):
        try:
            user = await bot.fetch_user(user_id)
            name = user.name
        except:
            name = f"User {user_id}"
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "🔹"
        text += f"{medal} **{i}.** {name} — **{total}** приглашений\n"
    
    embed.description = text
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="server", description="Показать статистику сервера")
async def server(interaction: discord.Interaction):
    guild = interaction.guild
    
    total_members = guild.member_count
    
    tag_role = guild.get_role(TAG_ROLE_ID)
    tag_count = len(tag_role.members) if tag_role else 0
    
    owner_role = guild.get_role(OWNER_ROLE_ID)
    owners = owner_role.members if owner_role else []
    owners_list = "\n".join([f"• {owner.mention}" for owner in owners]) if owners else "Не найдены"
    
    creator = guild.owner.mention if guild.owner else "Неизвестно"
    
    embed = discord.Embed(
        title=f"📊 Статистика сервера {guild.name}",
        color=discord.Color.gold()
    )
    embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
    
    embed.add_field(name="👥 Всего участников", value=f"{total_members}", inline=True)
    embed.add_field(name="🏷️ С тегом", value=f"{tag_count}", inline=True)
    embed.add_field(name="👑 Создатель", value=creator, inline=True)
    
    embed.add_field(name="👔 Владельцы", value=owners_list, inline=False)
    
    embed.add_field(name="📅 Сервер создан", value=guild.created_at.strftime("%d.%m.%Y"), inline=True)
    embed.add_field(name="🔰 Уровень буста", value=guild.premium_tier, inline=True)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="stats", description="Показать полную статистику пользователя (только для админов)")
async def stats(interaction: discord.Interaction, user: discord.Member):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ У вас нет прав!", ephemeral=True)
        return
    
    async with aiosqlite.connect("db.sqlite3") as db:
        cursor = await db.execute("SELECT invited, left, spent, total_invites FROM users WHERE user_id=?", (user.id,))
        user_data = await cursor.fetchone()
        
        if user_data:
            invited, left, spent, total_invites = user_data
            valid = invited - left - spent
        else:
            invited = left = spent = total_invites = 0
            valid = 0
        
        cursor = await db.execute("SELECT messages, join_date, last_active FROM user_stats WHERE user_id=?", (user.id,))
        stats_data = await cursor.fetchone()
        
        if stats_data:
            messages = stats_data[0] if stats_data[0] else 0
            join_date = stats_data[1] if stats_data[1] else "Неизвестно"
            last_active = stats_data[2] if stats_data[2] else "Неизвестно"
        else:
            messages = 0
            join_date = "Неизвестно"
            last_active = "Неизвестно"
        
        cursor = await db.execute("SELECT item, date FROM purchases WHERE user_id=? ORDER BY date DESC LIMIT 5", (user.id,))
        purchases = await cursor.fetchall()
    
    joined_at = user.joined_at
    if joined_at:
        days_on_server = (datetime.now(timezone.utc) - joined_at).days
        joined_str = joined_at.strftime("%d.%m.%Y %H:%M")
    else:
        days_on_server = 0
        joined_str = "Неизвестно"
    
    embed = discord.Embed(
        title=f"📊 Полная статистика {user.name}",
        color=discord.Color.purple()
    )
    embed.set_thumbnail(url=user.avatar.url if user.avatar else None)
    
    embed.add_field(name="📥 Пригласил", value=f"{invited}", inline=True)
    embed.add_field(name="📤 Вышли", value=f"{left}", inline=True)
    embed.add_field(name="✅ Доступно", value=f"**{valid}**", inline=True)
    embed.add_field(name="💸 Потрачено", value=f"{spent}", inline=True)
    embed.add_field(name="📈 Всего инвайтов", value=f"{total_invites}", inline=True)
    
    embed.add_field(name="💬 Сообщений", value=f"{messages}", inline=True)
    embed.add_field(name="📅 На сервере", value=f"{days_on_server} дней", inline=True)
    embed.add_field(name="🕐 Зашёл", value=joined_str, inline=True)
    embed.add_field(name="🕒 Последняя активность", value=last_active[:16] if last_active != "Неизвестно" else last_active, inline=True)
    
    if purchases:
        history = "\n".join([f"• {item} ({date[:10]})" for item, date in purchases])
        embed.add_field(name="🛒 Последние покупки", value=history, inline=False)
    else:
        embed.add_field(name="🛒 Покупки", value="Нет покупок", inline=False)
    
    embed.set_footer(text=f"ID: {user.id}")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="giveinvites", description="Выдать инвайты пользователю (только для админов)")
async def giveinvites(interaction: discord.Interaction, user: discord.Member, amount: int):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ У вас нет прав!", ephemeral=True)
        return
    
    if amount <= 0:
        await interaction.response.send_message("❌ Количество должно быть положительным!", ephemeral=True)
        return
    
    async with aiosqlite.connect("db.sqlite3") as db:
        await db.execute("""
        INSERT INTO users (user_id, invited, total_invites)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET 
            invited = invited + ?,
            total_invites = total_invites + ?
        """, (user.id, amount, amount, amount, amount))
        await db.commit()
    
    embed = discord.Embed(title="✅ Инвайты выданы!", description=f"{user.mention} выдано **{amount}** инвайтов!", color=discord.Color.green())
    await interaction.response.send_message(embed=embed, ephemeral=True)
    
    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        await log_channel.send(f"📊 {interaction.user.name} выдал {amount} инвайтов {user.mention}")

@bot.tree.command(name="takeinvites", description="Забрать инвайты у пользователя (только для админов)")
async def takeinvites(interaction: discord.Interaction, user: discord.Member, amount: int):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ У вас нет прав!", ephemeral=True)
        return
    
    if amount <= 0:
        await interaction.response.send_message("❌ Количество должно быть положительным!", ephemeral=True)
        return
    
    async with aiosqlite.connect("db.sqlite3") as db:
        cursor = await db.execute("SELECT invited, left, spent FROM users WHERE user_id=?", (user.id,))
        data = await cursor.fetchone()
        
        if data:
            invited, left, spent = data
            current_valid = invited - left - spent
            
            if current_valid < amount:
                await interaction.response.send_message(f"❌ У {user.mention} всего {current_valid} инвайтов!", ephemeral=True)
                return
            
            await db.execute("UPDATE users SET spent = spent + ? WHERE user_id=?", (amount, user.id))
            await db.commit()
            
            embed = discord.Embed(title="📤 Инвайты забраны!", description=f"У {user.mention} забрано **{amount}** инвайтов!\nОсталось: {current_valid - amount}", color=discord.Color.orange())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
            log_channel = bot.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                await log_channel.send(f"📊 {interaction.user.name} забрал {amount} инвайтов у {user.mention}")
        else:
            await interaction.response.send_message(f"❌ У {user.mention} нет инвайтов!", ephemeral=True)

@bot.tree.command(name="reset_user", description="Сбросить статистику пользователя (только для админов)")
async def reset_user(interaction: discord.Interaction, user: discord.Member):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ У вас нет прав!", ephemeral=True)
        return
    
    async with aiosqlite.connect("db.sqlite3") as db:
        await db.execute("DELETE FROM users WHERE user_id=?", (user.id,))
        await db.execute("DELETE FROM purchases WHERE user_id=?", (user.id,))
        await db.execute("DELETE FROM joins WHERE user_id=? OR inviter_id=?", (user.id, user.id))
        await db.execute("DELETE FROM invite_history WHERE user_id=? OR inviter_id=?", (user.id, user.id))
        await db.execute("DELETE FROM orders WHERE user_id=?", (user.id,))
        await db.execute("DELETE FROM user_stats WHERE user_id=?", (user.id,))
        await db.commit()
    
    await interaction.response.send_message(f"✅ Статистика пользователя {user.mention} сброшена!", ephemeral=True)

@bot.tree.command(name="successful", description="Отметить заказ как выполненный (только для админов)")
async def successful(interaction: discord.Interaction, order_number: int):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ У вас нет прав!", ephemeral=True)
        return
    
    async with aiosqlite.connect("db.sqlite3") as db:
        cursor = await db.execute(
            "SELECT user_id, item, message_id FROM orders WHERE order_number=? AND status='Ожидается'",
            (order_number,)
        )
        order = await cursor.fetchone()
        
        if not order:
            await interaction.response.send_message(f"❌ Заказ #{order_number} не найден или уже выполнен!", ephemeral=True)
            return
        
        user_id, item, message_id = order
        await db.execute("UPDATE orders SET status='Выполнено' WHERE order_number=?", (order_number,))
        await db.commit()
    
    orders_channel = bot.get_channel(ORDERS_CHANNEL_ID)
    if orders_channel:
        try:
            message = await orders_channel.fetch_message(message_id)
            embed = message.embeds[0] if message.embeds else None
            if embed:
                new_embed = discord.Embed(
                    title=embed.title,
                    description=embed.description,
                    color=discord.Color.green()
                )
                for field in embed.fields:
                    if field.name == "Статус":
                        new_embed.add_field(name="Статус", value="✅ Выполнено", inline=field.inline)
                    else:
                        new_embed.add_field(name=field.name, value=field.value, inline=field.inline)
                new_embed.set_footer(text=embed.footer.text)
                await message.edit(embed=new_embed)
        except:
            pass
    
    embed = discord.Embed(title="✅ Заказ выполнен!", description=f"Заказ #{order_number} отмечен как выполненный", color=discord.Color.green())
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="gcreate", description="🎁 Создать новый розыгрыш")
async def slash_gcreate(interaction: discord.Interaction):
    if not has_permission(interaction):
        await interaction.response.send_message("❌ У вас нет прав! Требуется роль или права администратора.", ephemeral=True)
        return
    
    modal = GiveawayModal()
    await interaction.response.send_modal(modal)

@bot.tree.command(name="gend", description="⚡ Досрочно завершить розыгрыш")
@app_commands.describe(message_id="ID сообщения с розыгрышем")
async def slash_gend(interaction: discord.Interaction, message_id: str):
    if not has_permission(interaction):
        await interaction.response.send_message("❌ У вас нет прав!", ephemeral=True)
        return
    
    try:
        msg_id = int(message_id)
    except:
        await interaction.response.send_message("❌ Неверный ID", ephemeral=True)
        return
    
    key = f"{interaction.channel.id}_{msg_id}"
    
    if key not in active_giveaways:
        await interaction.response.send_message("❌ Розыгрыш не найден", ephemeral=True)
        return
    
    await interaction.response.send_message("⚡ Завершаю...", ephemeral=True)
    winners, _ = await end_giveaway(interaction.channel.id, msg_id, reroll=False)
    
    if winners:
        await interaction.followup.send("✅ Розыгрыш завершён!", ephemeral=True)
    else:
        await interaction.followup.send("✅ Завершён. Победителей нет.", ephemeral=True)

@bot.tree.command(name="gdelete", description="🗑️ Удалить розыгрыш")
@app_commands.describe(message_id="ID сообщения с розыгрышем")
async def slash_gdelete(interaction: discord.Interaction, message_id: str):
    if not has_permission(interaction):
        await interaction.response.send_message("❌ У вас нет прав!", ephemeral=True)
        return
    
    try:
        msg_id = int(message_id)
    except:
        await interaction.response.send_message("❌ Неверный ID", ephemeral=True)
        return
    
    key = f"{interaction.channel.id}_{msg_id}"
    
    if key not in active_giveaways:
        await interaction.response.send_message("❌ Розыгрыш не найден", ephemeral=True)
        return
    
    try:
        channel = bot.get_channel(interaction.channel.id)
        message = await channel.fetch_message(msg_id)
        await message.delete()
    except:
        pass
    
    async with aiosqlite.connect("db.sqlite3") as db:
        await db.execute("DELETE FROM giveaway_invites WHERE giveaway_key = ?", (key,))
        await db.commit()
    
    del active_giveaways[key]
    await interaction.response.send_message("🗑️ Розыгрыш удалён!", ephemeral=True)

@bot.tree.command(name="greroll", description="🔄 Перевыбрать победителей")
@app_commands.describe(message_id="ID сообщения с розыгрышем")
async def slash_greroll(interaction: discord.Interaction, message_id: str):
    if not has_permission(interaction):
        await interaction.response.send_message("❌ У вас нет прав!", ephemeral=True)
        return
    
    try:
        msg_id = int(message_id)
    except:
        await interaction.response.send_message("❌ Неверный ID", ephemeral=True)
        return
    
    key = f"{interaction.channel.id}_{msg_id}"
    
    if key not in active_giveaways:
        await interaction.response.send_message("❌ Розыгрыш не найден", ephemeral=True)
        return
    
    winners, _ = await end_giveaway(interaction.channel.id, msg_id, reroll=True)
    
    if winners:
        await interaction.response.send_message(f"🔄 Новые победители: {', '.join([f'<@{uid}>' for uid in winners])}")
    else:
        await interaction.response.send_message("😞 Нет участников", ephemeral=True)

@bot.tree.command(name="gmp", description="🎲 Запустить игру 'Угадай число'")
@app_commands.describe(prize="Приз для победителя")
async def slash_gmp(interaction: discord.Interaction, prize: str):
    if not has_permission(interaction):
        await interaction.response.send_message("❌ У вас нет прав!", ephemeral=True)
        return
    
    target_number = random.randint(1, 100)
    
    embed = discord.Embed(
        title="🎲 **УГАДАЙ ЧИСЛО** 🎲",
        description=(
            f"**Ваша задача отгадать число от 1 до 100.**\n\n"
            f"**Приз:** {prize}\n\n"
            f"**Ответ отправьте в** <#{GUESS_CHANNEL_ID}>\n"
            f"<@&{ALLOWED_ROLE_ID}>"
        ),
        color=discord.Color.purple()
    )
    
    await interaction.channel.send(embed=embed)
    
    active_guess_games[GUESS_CHANNEL_ID] = GuessNumberGame(
        GUESS_CHANNEL_ID, target_number, prize
    )
    
    await interaction.response.send_message(f"✅ Игра запущена! Загаданное число: {target_number} (логи)", ephemeral=True)

@bot.tree.command(name="sync", description="Синхронизировать команды (только для админов)")
async def sync_commands(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ У вас нет прав!", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        await interaction.followup.send(f"✅ Синхронизировано {len(synced)} команд")
    except Exception as e:
        await interaction.followup.send(f"❌ Ошибка: {e}")

# ================== СОБЫТИЯ ==================
@bot.event
async def on_ready():
    await init_db()
    await migrate_db()
    
    try:
        guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f"✅ Синхронизировано {len(synced)} команд")
    except Exception as e:
        print(f"❌ Ошибка синхронизации: {e}")
    
    for guild in bot.guilds:
        try:
            invites = await guild.invites()
            invites_cache[guild.id] = {}
            for invite in invites:
                invites_cache[guild.id][invite.code] = {
                    'uses': invite.uses,
                    'inviter': invite.inviter.id if invite.inviter else None
                }
            print(f"📊 Загружено {len(invites)} инвайтов")
        except Exception as e:
            print(f"❌ Ошибка загрузки инвайтов: {e}")

    print(f"✅ Бот запущен: {bot.user}")
    await bot.change_presence(activity=discord.Game(name="/help | /shop | /gcreate | /gclick | /gclicktop"))

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    
    # Автоответ на отзывы
    if message.channel.id == REVIEW_CHANNEL_ID:
        review_num = await get_review_counter()
        
        reply_text = (f"Благодарим за отзыв №{review_num}! <:800962blobcatflower:1455572431963164733> SGTeam всегда с вами <:guildtag:1484883258473840640>")
        
        await message.reply(reply_text)
        await increment_review_counter()
        return
    
    # Игра "Угадай число"
    if message.channel.id == GUESS_CHANNEL_ID:
        game = active_guess_games.get(GUESS_CHANNEL_ID)
        if game and game.active:
            await game.check_guess(message)
    
    # Подсчёт сообщений для статистики
    async with aiosqlite.connect("db.sqlite3") as db:
        await db.execute("""
        INSERT INTO user_stats (user_id, messages, last_active)
        VALUES (?, 1, datetime('now'))
        ON CONFLICT(user_id) DO UPDATE SET 
            messages = messages + 1,
            last_active = datetime('now')
        """, (message.author.id,))
        await db.commit()
    
    await bot.process_commands(message)

@bot.event
async def on_member_join(member):
    guild = member.guild
    
    if guild.id not in invites_cache:
        invites_cache[guild.id] = {}
    
    invites = await guild.invites()
    old = invites_cache[guild.id]
    
    inviter = None
    used_invite = None
    
    for invite in invites:
        old_uses = 0
        if invite.code in old:
            if isinstance(old[invite.code], dict):
                old_uses = old[invite.code].get('uses', 0)
            else:
                old_uses = old[invite.code]
        
        if invite.uses > old_uses:
            inviter = invite.inviter
            used_invite = invite.code
            break
    
    new_cache = {}
    for invite in invites:
        new_cache[invite.code] = {'uses': invite.uses, 'inviter': invite.inviter.id if invite.inviter else None}
    invites_cache[guild.id] = new_cache
    
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if not channel:
        return
    
    async with aiosqlite.connect("db.sqlite3") as db:
        await db.execute("""
        INSERT INTO user_stats (user_id, join_date)
        VALUES (?, datetime('now'))
        ON CONFLICT(user_id) DO UPDATE SET join_date = datetime('now')
        """, (member.id,))
        await db.commit()
    
    if is_fake(member):
        await channel.send(f"⚠️ {member.mention} подозрительный аккаунт - не засчитан")
        return
    
    if inviter:
        async with aiosqlite.connect("db.sqlite3") as db:
            cursor = await db.execute("SELECT inviter_id FROM joins WHERE user_id=?", (member.id,))
            if not await cursor.fetchone():
                await db.execute("""
                INSERT INTO users (user_id, invited, total_invites)
                VALUES (?, 1, 1)
                ON CONFLICT(user_id) DO UPDATE SET 
                    invited = invited + 1,
                    total_invites = total_invites + 1
                """, (inviter.id,))
                
                await db.execute("INSERT INTO joins (user_id, inviter_id, join_date) VALUES (?, ?, datetime('now'))", (member.id, inviter.id))
                await db.execute("INSERT INTO invite_history (user_id, inviter_id, invite_code, date) VALUES (?, ?, ?, datetime('now'))", (member.id, inviter.id, used_invite))
                await db.commit()
                
                cursor = await db.execute("SELECT invited, left, spent FROM users WHERE user_id=?", (inviter.id,))
                inv_data = await cursor.fetchone()
                if inv_data:
                    total_valid = inv_data[0] - inv_data[1] - inv_data[2]
                    await channel.send(f"👤 {member.mention} зашел\n📨 Пригласил: {inviter.mention}\n📊 Теперь у {inviter.name} {total_valid} инвайтов")
                
                for key, giveaway in active_giveaways.items():
                    if inviter.id in giveaway["participants"]:
                        await add_giveaway_invite(key, inviter.id, member.id)
                        giveaway["invite_bonus"][inviter.id] = await get_giveaway_invites_count(key, inviter.id)
                        try:
                            channel_obj = bot.get_channel(giveaway["channel_id"])
                            if channel_obj:
                                msg = await channel_obj.fetch_message(giveaway["message_id"])
                                embed, _ = build_giveaway_message(giveaway, None)
                                await msg.edit(embed=embed)
                        except:
                            pass
            else:
                await channel.send(f"👤 {member.mention} зашел\n📨 Пригласил: {inviter.mention}\n⚠️ Но этот пользователь уже заходил ранее - инвайт не засчитан")
    else:
        await channel.send(f"👤 {member.mention} зашел\n📨 Пригласил: Неизвестно")

@bot.event
async def on_member_remove(member):
    async with aiosqlite.connect("db.sqlite3") as db:
        cursor = await db.execute("SELECT inviter_id FROM joins WHERE user_id=? ORDER BY join_date DESC LIMIT 1", (member.id,))
        data = await cursor.fetchone()
        if data:
            inviter_id = data[0]
            await db.execute("UPDATE users SET left = left + 1 WHERE user_id=?", (inviter_id,))
            await db.commit()
            
            channel = bot.get_channel(LOG_CHANNEL_ID)
            if channel:
                try:
                    inviter = await bot.fetch_user(inviter_id)
                    await channel.send(f"👋 {member.mention} покинул сервер\n📊 У {inviter.name} засчитан выход")
                except:
                    await channel.send(f"👋 {member.mention} покинул сервер\n📊 У пригласившего (ID: {inviter_id}) засчитан выход")

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    tag_role = after.guild.get_role(TAG_ROLE_ID)
    target_role = after.guild.get_role(TARGET_ROLE_FOR_TAG_ID)
    
    if not tag_role or not target_role:
        return
    
    had_tag = any(role.id == TAG_ROLE_ID for role in before.roles)
    has_tag = any(role.id == TAG_ROLE_ID for role in after.roles)
    
    if not had_tag and has_tag:
        if target_role not in after.roles:
            await after.add_roles(target_role)
            log_channel = bot.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                await log_channel.send(f"🏷️ Пользователю {after.mention} выдана роль {target_role.mention} за наличие тега!")
    
    elif had_tag and not has_tag:
        if target_role in after.roles:
            await after.remove_roles(target_role)
            log_channel = bot.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                await log_channel.send(f"🏷️ Пользователь {after.mention} потерял роль {target_role.mention} (тег снят)")

# ================== ЗАПУСК ==================
if __name__ == "__main__":
    try:
        keep_alive()
        bot.run(TOKEN)
    except discord.LoginFailure:
        print("❌ Ошибка: Неверный токен бота!")
    except Exception as e:
        print(f"❌ Ошибка запуска: {e}")
