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

# ================== FLASK ДЛЯ KEEP-ALIVE ==================
app = Flask('')

# Отключаем логи Flask
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
    # Используем порт, который даёт Render (или 10000 по умолчанию)
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    server = Thread(target=run)
    server.daemon = True
    server.start()
    print(f"🌐 Веб-сервер для keep-alive запущен на порту {os.environ.get('PORT', 10000)}")

# ================== КОНФИГУРАЦИЯ ==================
# Токен берется из переменной окружения (безопасно!)
TOKEN = os.getenv("TOKEN")
GUILD_ID = 1176162885811060756
LOG_CHANNEL_ID = 1455165169075490963
TICKET_CATEGORY_ID = 1486980315825049640
BUYER_ROLE = "Покупатель"
REGULAR_ROLE = "Постоянный покупатель"
MIN_ACCOUNT_AGE_DAYS = 3

# Проверка наличия токена
if not TOKEN:
    print("❌ ОШИБКА: Токен не найден! Установите переменную окружения TOKEN")
    exit(1)

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

invites_cache = {}

# ================== МИГРАЦИЯ БАЗЫ ==================
async def migrate_db():
    async with aiosqlite.connect("db.sqlite3") as db:
        # Добавляем колонку join_date в таблицу joins если её нет
        cursor = await db.execute("PRAGMA table_info(joins)")
        columns = [column[1] for column in await cursor.fetchall()]
        
        if "join_date" not in columns:
            try:
                await db.execute("ALTER TABLE joins ADD COLUMN join_date TEXT")
                print("✅ Добавлена колонка join_date в таблицу joins")
            except:
                pass
        
        # Добавляем другие колонки если нужно
        cursor = await db.execute("PRAGMA table_info(users)")
        columns = [column[1] for column in await cursor.fetchall()]
        
        if "total_invites" not in columns:
            try:
                await db.execute("ALTER TABLE users ADD COLUMN total_invites INTEGER DEFAULT 0")
                print("✅ Добавлена колонка total_invites")
            except:
                pass
        
        await db.commit()

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
            date TEXT
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

        await db.commit()
    
    # Выполняем миграцию для существующей базы
    await migrate_db()

# ================== СТАРТ ==================
@bot.event
async def on_ready():
    await init_db()
    
    # Синхронизация команд
    try:
        guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f"✅ Синхронизировано {len(synced)} команд: {', '.join([cmd.name for cmd in synced])}")
    except Exception as e:
        print(f"❌ Ошибка синхронизации: {e}")
    
    # Загрузка истории приглашений при старте
    for guild in bot.guilds:
        try:
            invites = await guild.invites()
            
            invites_cache[guild.id] = {}
            for invite in invites:
                invites_cache[guild.id][invite.code] = {
                    'uses': invite.uses,
                    'inviter': invite.inviter.id if invite.inviter else None
                }
                
                async with aiosqlite.connect("db.sqlite3") as db:
                    await db.execute("""
                    INSERT OR IGNORE INTO invite_history (user_id, inviter_id, invite_code, date)
                    VALUES (?, ?, ?, datetime('now'))
                    """, (invite.inviter.id if invite.inviter else 0, 
                          invite.inviter.id if invite.inviter else 0, 
                          invite.code))
                    await db.commit()
                    
        except Exception as e:
            print(f"❌ Ошибка загрузки инвайтов для гильдии {guild.id}: {e}")

    print(f"✅ Бот запущен: {bot.user}")
    print(f"🎮 Команды доступны на сервере с ID: {GUILD_ID}")
    
    await bot.change_presence(activity=discord.Game(name="/info | /shop"))

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
        value="Мы занимаемся помощью участникам, у которых нет возможности выполнять задания Discord (например: нет ПК, слабое устройство и т.д.), но которые хотят получать:\n🦋",
        inline=False
    )
    
    embed.add_field(
        name="⭐ Что мы даем?",
        value="• Украшения профиля\n• Orbs (валюта Discord)\n• Награды за выполнение заданий\n\nНаша команда выполнит задания за вас быстро и безопасно.",
        inline=False
    )
    
    embed.add_field(
        name="🔍 Что мы делаем?",
        value="Мы выполняем за вас Discord-задания, за которые вы получаете: ⚠️\n• Orbs ✨\n• Украшения профиля ✨\n• Эксклюзивные награды ✨\n\nВсё что требуется от вас - оплата в виде приглашений на сервер.",
        inline=False
    )
    
    embed.add_field(
        name="💰 Прайс-лист",
        value=(
            "🟠 Выполнить 1 задание (700 orbs 💎) - **3 инвайта**\n"
            "🔵 Выполнить задание с украшением - **3 инвайта**\n"
            "🩷 Выполнить 2 задания (1400 orbs 💎) - **5 инвайтов**\n"
            "🟡 Выполнить все доступные задания на аккаунте - **10 инвайтов**\n"
            "🎁 Nitro Full на 3 дня - **5 инвайтов**\n"
            "🟠 Выполнить 1 задание (200 orbs 💎) - **2 инвайта**"
        ),
        inline=False
    )
    
    embed.add_field(
        name="⚠️ Гарантии",
        value=(
            "• Безопасность аккаунта\n"
            "• Быстрое выполнение\n"
            "• Поддержка 24/7\n"
            "• Честная работа без обмана"
        ),
        inline=False
    )
    
    embed.add_field(
        name="📞 Как заказать?",
        value="Чтобы заказать выполнение, используйте команду `/shop` или нажмите на кнопку ниже!",
        inline=False
    )
    
    embed.set_footer(text="Бот для помощи с заданиями Discord | Ваши инвайты = ваши награды")
    
    view = ShopButtonView()
    await interaction.response.send_message(embed=embed, view=view)

class ShopButtonView(View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="🛒 Открыть магазин", style=discord.ButtonStyle.green, emoji="🛒")
    async def open_shop(self, interaction: discord.Interaction, button: Button):
        await shop_command(interaction)

async def shop_command(interaction):
    embed = discord.Embed(
        title="🛒 Магазин услуг",
        description="Выберите услугу для заказа",
        color=discord.Color.purple()
    )
    
    embed.add_field(
        name="🟠 1 задание (700 Orbs)",
        value="**Цена:** 3 инвайта\nВыполнение одного задания Discord за 💎 700 Orbs",
        inline=False
    )
    
    embed.add_field(
        name="🔵 Задание с украшением",
        value="**Цена:** 3 инвайта\nВыполнение задания с получением украшения профиля",
        inline=False
    )
    
    embed.add_field(
        name="🩷 2 задания (1400 Orbs)",
        value="**Цена:** 5 инвайтов\nВыполнение двух заданий Discord за 💎 1400 Orbs",
        inline=False
    )
    
    embed.add_field(
        name="🟡 Все задания",
        value="**Цена:** 10 инвайтов\nВыполнение всех доступных заданий на аккаунте",
        inline=False
    )
    
    embed.add_field(
        name="🎁 Nitro Full (3 дня)",
        value="**Цена:** 5 инвайтов\nDiscord Nitro на 3 дня",
        inline=False
    )
    
    embed.add_field(
        name="🟠 1 задание (200 Orbs)",
        value="**Цена:** 2 инвайта\nВыполнение одного задания Discord за 💎 200 Orbs",
        inline=False
    )
    
    embed.set_footer(text="💰 Оплата производится вашими приглашениями | /invites - проверить баланс")
    
    view = Shop()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# ================== КОМАНДА /GIVEINVITES ==================
@bot.tree.command(name="giveinvites", description="Выдать инвайты пользователю (только для админов)")
async def giveinvites(interaction: discord.Interaction, user: discord.Member, amount: int):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ У вас нет прав на использование этой команды!", ephemeral=True)
        return
    
    if amount <= 0:
        await interaction.response.send_message("❌ Количество инвайтов должно быть положительным числом!", ephemeral=True)
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
    
    embed = discord.Embed(
        title="✅ Инвайты выданы!",
        description=f"Пользователю {user.mention} выдано **{amount}** инвайтов!",
        color=discord.Color.green()
    )
    embed.set_footer(text=f"Выдал: {interaction.user.name}")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)
    
    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        await log_channel.send(f"📊 **{interaction.user.name}** выдал {amount} инвайтов пользователю {user.mention}")

# ================== КОМАНДА /TAKEINVITES ==================
@bot.tree.command(name="takeinvites", description="Забрать инвайты у пользователя (только для админов)")
async def takeinvites(interaction: discord.Interaction, user: discord.Member, amount: int):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ У вас нет прав на использование этой команды!", ephemeral=True)
        return
    
    if amount <= 0:
        await interaction.response.send_message("❌ Количество инвайтов должно быть положительным числом!", ephemeral=True)
        return
    
    async with aiosqlite.connect("db.sqlite3") as db:
        cursor = await db.execute(
            "SELECT invited, left, spent FROM users WHERE user_id=?",
            (user.id,)
        )
        data = await cursor.fetchone()
        
        if data:
            invited, left, spent = data
            current_valid = invited - left - spent
            
            if current_valid < amount:
                await interaction.response.send_message(
                    f"❌ Нельзя забрать {amount} инвайтов! У пользователя {user.mention} всего {current_valid} доступных инвайтов!",
                    ephemeral=True
                )
                return
            
            await db.execute("""
            UPDATE users SET spent = spent + ? WHERE user_id=?
            """, (amount, user.id))
            
            await db.commit()
            
            embed = discord.Embed(
                title="📤 Инвайты забраны!",
                description=f"У пользователя {user.mention} забрано **{amount}** инвайтов!",
                color=discord.Color.orange()
            )
            embed.add_field(name="Осталось инвайтов", value=f"**{current_valid - amount}**", inline=False)
            embed.set_footer(text=f"Забрал: {interaction.user.name}")
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
            log_channel = bot.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                await log_channel.send(f"📊 **{interaction.user.name}** забрал {amount} инвайтов у пользователя {user.mention}")
        else:
            await interaction.response.send_message(f"❌ У пользователя {user.mention} нет инвайтов!", ephemeral=True)

# ================== КОМАНДА /SYNC ==================
@bot.tree.command(name="sync", description="Синхронизировать команды (только для админов)")
async def sync_commands(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ У вас нет прав на использование этой команды", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        await interaction.followup.send(f"✅ Синхронизировано {len(synced)} команд: {', '.join([cmd.name for cmd in synced])}")
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
        old_uses = old.get(invite.code, {}).get('uses', 0) if isinstance(old.get(invite.code), dict) else old.get(invite.code, 0)
        if invite.uses > old_uses:
            inviter = invite.inviter
            used_invite = invite.code
            break

    # Обновляем кэш
    new_cache = {}
    for invite in invites:
        new_cache[invite.code] = {
            'uses': invite.uses,
            'inviter': invite.inviter.id if invite.inviter else None
        }
    invites_cache[guild.id] = new_cache

    channel = bot.get_channel(LOG_CHANNEL_ID)
    if not channel:
        print(f"❌ Канал с ID {LOG_CHANNEL_ID} не найден!")
        return

    if is_fake(member):
        await channel.send(f"⚠️ {member.mention} подозрительный аккаунт (возраст менее {MIN_ACCOUNT_AGE_DAYS} дней) - не засчитан")
        return

    if inviter:
        async with aiosqlite.connect("db.sqlite3") as db:
            # Обновляем статистику пригласившего
            await db.execute("""
            INSERT INTO users (user_id, invited, total_invites)
            VALUES (?, 1, 1)
            ON CONFLICT(user_id) DO UPDATE SET 
                invited = invited + 1,
                total_invites = total_invites + 1
            """, (inviter.id,))

            # Записываем факт приглашения
            await db.execute(
                "INSERT INTO joins (user_id, inviter_id, join_date) VALUES (?, ?, datetime('now'))",
                (member.id, inviter.id)
            )
            
            # Записываем историю инвайта
            await db.execute(
                "INSERT INTO invite_history (user_id, inviter_id, invite_code, date) VALUES (?, ?, ?, datetime('now'))",
                (member.id, inviter.id, used_invite)
            )

            await db.commit()
        
        await channel.send(
            f"👤 {member.mention} зашел на сервер\n"
            f"📨 Пригласил: {inviter.mention}\n"
            f"📊 Всего приглашений у {inviter.name}: {await get_invites_count(inviter.id)}"
        )
    else:
        await channel.send(
            f"👤 {member.mention} зашел на сервер\n"
            f"📨 Пригласил: Неизвестно"
        )

# ================== ВЫХОД ==================
@bot.event
async def on_member_remove(member):
    async with aiosqlite.connect("db.sqlite3") as db:
        cursor = await db.execute(
            "SELECT inviter_id FROM joins WHERE user_id=? ORDER BY join_date DESC LIMIT 1",
            (member.id,)
        )
        data = await cursor.fetchone()

        if data:
            inviter_id = data[0]

            await db.execute("""
            UPDATE users SET left = left + 1 WHERE user_id=?
            """, (inviter_id,))

            await db.commit()
            
            channel = bot.get_channel(LOG_CHANNEL_ID)
            if channel:
                try:
                    inviter = await bot.fetch_user(inviter_id)
                    await channel.send(
                        f"👋 {member.mention} покинул сервер\n"
                        f"📊 У пригласившего ({inviter.name}) засчитан выход"
                    )
                except:
                    await channel.send(
                        f"👋 {member.mention} покинул сервер\n"
                        f"📊 У пригласившего (ID: {inviter_id}) засчитан выход"
                    )

# ================== ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ==================
async def get_invites_count(user_id):
    async with aiosqlite.connect("db.sqlite3") as db:
        cursor = await db.execute(
            "SELECT invited, left, spent FROM users WHERE user_id=?",
            (user_id,)
        )
        data = await cursor.fetchone()
    
    if data:
        invited, left, spent = data
        return invited - left - spent
    return 0

# ================== /INVITES ==================
@bot.tree.command(name="invites", description="Показать статистику ваших приглашений")
async def invites(interaction: discord.Interaction):
    async with aiosqlite.connect("db.sqlite3") as db:
        cursor = await db.execute(
            "SELECT invited, left, spent, total_invites FROM users WHERE user_id=?",
            (interaction.user.id,)
        )
        data = await cursor.fetchone()

    if data:
        invited, left, spent, total_invites = data
        valid = invited - left - spent
    else:
        invited = left = spent = total_invites = 0
        valid = 0

    async with aiosqlite.connect("db.sqlite3") as db:
        cursor = await db.execute(
            "SELECT item, date FROM purchases WHERE user_id=? ORDER BY date DESC LIMIT 10",
            (interaction.user.id,)
        )
        purchases = await cursor.fetchall()

    embed = discord.Embed(
        title=f"📊 Статистика {interaction.user.name}",
        color=discord.Color.blue()
    )
    
    embed.add_field(name="✅ Доступно", value=f"**{valid}**", inline=True)
    embed.add_field(name="📥 Пригласил", value=f"{invited}", inline=True)
    embed.add_field(name="📤 Вышли", value=f"{left}", inline=True)
    embed.add_field(name="💸 Потрачено", value=f"{spent}", inline=True)
    embed.add_field(name="📈 Всего инвайтов за всё время", value=f"{total_invites}", inline=True)
    
    if purchases:
        history = "\n".join([f"• {item} ({date[:10]})" for item, date in purchases])
        embed.add_field(name="🛒 Последние покупки", value=history, inline=False)
    else:
        embed.add_field(name="🛒 Покупки", value="Нет покупок", inline=False)
    
    embed.set_footer(text=f"ID: {interaction.user.id}")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ================== ТОП ==================
@bot.tree.command(name="top", description="Топ 10 пользователей по приглашениям")
async def top(interaction: discord.Interaction):
    async with aiosqlite.connect("db.sqlite3") as db:
        cursor = await db.execute("""
        SELECT user_id, invited - left - spent as total
        FROM users
        WHERE invited - left - spent > 0
        ORDER BY total DESC
        LIMIT 10
        """)
        data = await cursor.fetchall()

    if not data:
        await interaction.response.send_message("📊 Пока нет пользователей с приглашениями!", ephemeral=True)
        return

    embed = discord.Embed(
        title="🏆 ТОП 10 ИНВАЙТЕРОВ",
        color=discord.Color.gold()
    )
    
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
            cursor = await db.execute(
                "SELECT invited, left, spent FROM users WHERE user_id=?",
                (user_id,)
            )
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
            await interaction.response.send_message("❌ Категория для тикетов не найдена! Обратитесь к администратору.", ephemeral=True)
            return

        channel = await guild.create_text_channel(
            f"заказ-{interaction.user.name}",
            category=category
        )

        await channel.set_permissions(interaction.user, read_messages=True, send_messages=True)
        await channel.set_permissions(guild.default_role, read_messages=False)

        embed = discord.Embed(
            title="🛒 Новый заказ",
            description=f"**Товар:** {item_name}\n{item_description}\n**Цена:** {cost} инвайтов",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"Заказчик: {interaction.user.name}")

        await channel.send(
            content=f"{interaction.user.mention}",
            embed=embed,
            view=TicketView()
        )

        async with aiosqlite.connect("db.sqlite3") as db:
            await db.execute("""
            UPDATE users SET spent = spent + ? WHERE user_id=?
            """, (cost, interaction.user.id))

            await db.execute(
                "INSERT INTO purchases VALUES (?, ?, datetime('now'))",
                (interaction.user.id, f"{item_name} ({cost} инвайтов)")
            )

            await db.commit()

        buyer = discord.utils.get(guild.roles, name=BUYER_ROLE)
        regular = discord.utils.get(guild.roles, name=REGULAR_ROLE)

        if buyer:
            await interaction.user.add_roles(buyer)
        
        async with aiosqlite.connect("db.sqlite3") as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM purchases WHERE user_id=?",
                (interaction.user.id,)
            )
            count = (await cursor.fetchone())[0]

        if count >= 2 and regular:
            await interaction.user.add_roles(regular)

        await interaction.response.send_message(
            f"✅ Заказ создан! Перейдите в канал {channel.mention}",
            ephemeral=True
        )

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

# ================== /SHOP ==================
@bot.tree.command(name="shop", description="Открыть магазин")
async def shop(interaction: discord.Interaction):
    await shop_command(interaction)

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
        await db.commit()
    
    await interaction.response.send_message(f"✅ Статистика пользователя {user.mention} сброшена!", ephemeral=True)

# ================== ЗАПУСК ==================
if __name__ == "__main__":
    try:
        # Запускаем keep-alive сервер
        keep_alive()
        
        # Запускаем бота
        bot.run(TOKEN)
    except discord.LoginFailure:
        print("❌ Ошибка: Неверный токен бота!")
    except Exception as e:
        print(f"❌ Ошибка запуска: {e}")
