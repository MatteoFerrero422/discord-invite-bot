import discord
from discord.ext import commands
from discord.ui import View, Button
import aiosqlite
from datetime import datetime, timedelta, timezone
import asyncio
import os
from flask import Flask, jsonify
from threading import Thread
import logging
import random

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
TICKET_CATEGORY_ID = 1486980315825049640
ORDERS_CHANNEL_ID = 1372910944472006706
BUYER_ROLE = "Покупатель"
REGULAR_ROLE = "Постоянный покупатель"
MIN_ACCOUNT_AGE_DAYS = 3
OWNER_ROLE_ID = 1373760116678987916  # ID роли владельца
TAG_ROLE_ID = 1489575333718921428    # ID роли для тега

if not TOKEN:
    print("❌ ОШИБКА: Токен не найден!")
    exit(1)

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

invites_cache = {}
order_counter = 496

# ================== БАЗА ==================
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
        await db.commit()

async def get_next_order_number():
    global order_counter
    async with aiosqlite.connect("db.sqlite3") as db:
        cursor = await db.execute("SELECT MAX(order_number) FROM orders")
        data = await cursor.fetchone()
        if data and data[0]:
            order_counter = data[0] + 1
        else:
            order_counter = 496
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
    await bot.change_presence(activity=discord.Game(name="/help | /shop"))

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    
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
async def on_member_update(before: discord.Member, after: discord.Member):
    # Автоматическая выдача роли за тег
    tag_role = after.guild.get_role(TAG_ROLE_ID)
    if not tag_role:
        return
    
    # Проверяем, есть ли у пользователя тег
    has_tag = any(role.id == TAG_ROLE_ID for role in after.roles)
    
    if has_tag:
        # Если есть тег - выдаём роль
        target_role = after.guild.get_role(1489575333718921428)
        if target_role and target_role not in after.roles:
            await after.add_roles(target_role)
            log_channel = bot.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                await log_channel.send(f"🏷️ Пользователю {after.mention} выдана роль {target_role.mention} за наличие тега!")

# ================== КОМАНДА /HELP ==================
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

# ================== КОМАНДА /STATS ==================
@bot.tree.command(name="stats", description="Показать полную статистику пользователя (только для админов)")
async def stats(interaction: discord.Interaction, user: discord.Member):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ У вас нет прав!", ephemeral=True)
        return
    
    async with aiosqlite.connect("db.sqlite3") as db:
        # Данные из таблицы users (инвайты)
        cursor = await db.execute("SELECT invited, left, spent, total_invites FROM users WHERE user_id=?", (user.id,))
        user_data = await cursor.fetchone()
        
        if user_data:
            invited, left, spent, total_invites = user_data
            valid = invited - left - spent
        else:
            invited = left = spent = total_invites = 0
            valid = 0
        
        # Данные из таблицы user_stats (сообщения)
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
        
        # Покупки
        cursor = await db.execute("SELECT item, date FROM purchases WHERE user_id=? ORDER BY date DESC LIMIT 5", (user.id,))
        purchases = await cursor.fetchall()
    
    # Время на сервере
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
    
    # Инвайты
    embed.add_field(name="📥 Пригласил", value=f"{invited}", inline=True)
    embed.add_field(name="📤 Вышли", value=f"{left}", inline=True)
    embed.add_field(name="✅ Доступно", value=f"**{valid}**", inline=True)
    embed.add_field(name="💸 Потрачено", value=f"{spent}", inline=True)
    embed.add_field(name="📈 Всего инвайтов", value=f"{total_invites}", inline=True)
    
    # Активность
    embed.add_field(name="💬 Сообщений", value=f"{messages}", inline=True)
    embed.add_field(name="📅 На сервере", value=f"{days_on_server} дней", inline=True)
    embed.add_field(name="🕐 Зашёл", value=joined_str, inline=True)
    embed.add_field(name="🕒 Последняя активность", value=last_active[:16] if last_active != "Неизвестно" else last_active, inline=True)
    
    # Покупки
    if purchases:
        history = "\n".join([f"• {item} ({date[:10]})" for item, date in purchases])
        embed.add_field(name="🛒 Последние покупки", value=history, inline=False)
    else:
        embed.add_field(name="🛒 Покупки", value="Нет покупок", inline=False)
    
    embed.set_footer(text=f"ID: {user.id}")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ================== КОМАНДА /SERVER ==================
@bot.tree.command(name="server", description="Показать статистику сервера")
async def server(interaction: discord.Interaction):
    guild = interaction.guild
    
    # Общее количество участников
    total_members = guild.member_count
    
    # Количество людей с тегом (роль TAG_ROLE_ID)
    tag_role = guild.get_role(TAG_ROLE_ID)
    tag_count = len(tag_role.members) if tag_role else 0
    
    # Владельцы (с ролью OWNER_ROLE_ID)
    owner_role = guild.get_role(OWNER_ROLE_ID)
    owners = owner_role.members if owner_role else []
    owners_list = "\n".join([f"• {owner.mention}" for owner in owners]) if owners else "Не найдены"
    
    # Создатель сервера
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

# ================== КОМАНДА /SUCCESSFUL ==================
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

# ================== КОМАНДА /INFO ==================
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

# ================== КОМАНДА /GIVEINVITES ==================
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

# ================== КОМАНДА /TAKEINVITES ==================
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

# ================== КОМАНДА /SYNC ==================
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

# ================== АНТИ-ФЕЙК ==================
def is_fake(member):
    age = datetime.now(timezone.utc) - member.created_at
    return age < timedelta(days=MIN_ACCOUNT_AGE_DAYS)

# ================== ВХОД ==================
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
    
    # Сохраняем дату входа в user_stats
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
            else:
                await channel.send(f"👤 {member.mention} зашел\n📨 Пригласил: {inviter.mention}\n⚠️ Но этот пользователь уже заходил ранее - инвайт не засчитан")
    else:
        await channel.send(f"👤 {member.mention} зашел\n📨 Пригласил: Неизвестно")

# ================== ВЫХОД ==================
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

# ================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==================
async def get_invites_count(user_id):
    async with aiosqlite.connect("db.sqlite3") as db:
        cursor = await db.execute("SELECT invited, left, spent FROM users WHERE user_id=?", (user_id,))
        data = await cursor.fetchone()
    if data:
        return data[0] - data[1] - data[2]
    return 0

# ================== /INVITES ==================
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

# ================== ТОП ==================
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

# ================== ТИКЕТЫ ==================
class TicketView(View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="❌ Закрыть тикет", style=discord.ButtonStyle.red)
    async def close(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("Тикет будет закрыт через 5 секунд...")
        await asyncio.sleep(5)
        await interaction.channel.delete()

# ================== SHOP ==================
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

# ================== /SHOP ==================
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

# ================== /RESET ==================
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

# ================== ЗАПУСК ==================
if __name__ == "__main__":
    try:
        keep_alive()
        bot.run(TOKEN)
    except discord.LoginFailure:
        print("❌ Ошибка: Неверный токен бота!")
    except Exception as e:
        print(f"❌ Ошибка запуска: {e}")
