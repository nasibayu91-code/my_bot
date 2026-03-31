"""
🎰 VIP VAULT CASINO BOT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Игры: Мины, Дартс, Баскетбол, Футбол, Башня, Орёл/Решка, Кости
Оплата: Telegram Stars (⭐) + USD (ручное подтверждение)
Шанс выигрыша: 30%
"""

import logging
import sqlite3
import random
import os
import json
from datetime import datetime
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    LabeledPrice
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, PreCheckoutQueryHandler,
    filters, ContextTypes
)

# ═══════════════════════════════════════
#              КОНФИГ
# ═══════════════════════════════════════
BOT_TOKEN       = "8603411643:AAEFOuhoBvnmh90h4MK9PsqQnot5uQGkmTY"       # Токен от @BotFather
ADMIN_ID        = 7564112818                   # Твой Telegram ID (число)
WIN_RATE        = 0.30                        # 30% шанс выигрыша
REG_BONUS       = 50                          # Бонус при регистрации (токены)
STARS_TO_TOKENS = 2                           # 1 Star = 2 токена
USD_TO_TOKENS   = 200                         # 1 USD = 200 токенов
USD_WALLET      = "UQBufQWcrufQW_S0ZqhxYEIO6g1R1YoaE3l5aNd9rM3zqTAa"  # Адрес кошелька для USD
MIN_BET         = 1
MAX_BET         = 10000
MIN_WITHDRAW    = 50
DB_FILE         = "casino.db"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════
#              БАЗА ДАННЫХ
# ═══════════════════════════════════════
def get_conn():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id     INTEGER PRIMARY KEY,
            username    TEXT,
            first_name  TEXT,
            balance     REAL    DEFAULT 0,
            total_dep   REAL    DEFAULT 0,
            total_won   REAL    DEFAULT 0,
            total_lost  REAL    DEFAULT 0,
            games_count INTEGER DEFAULT 0,
            ref_by      INTEGER DEFAULT NULL,
            joined_at   TEXT
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            type        TEXT,
            amount      REAL,
            status      TEXT,
            note        TEXT,
            created_at  TEXT
        );
        CREATE TABLE IF NOT EXISTS promo_codes (
            code        TEXT PRIMARY KEY,
            amount      REAL,
            uses_left   INTEGER,
            used_count  INTEGER DEFAULT 0,
            created_by  INTEGER,
            created_at  TEXT
        );
        CREATE TABLE IF NOT EXISTS used_promos (
            user_id     INTEGER,
            code        TEXT,
            PRIMARY KEY (user_id, code)
        );
        CREATE TABLE IF NOT EXISTS active_games (
            user_id     INTEGER PRIMARY KEY,
            game_type   TEXT,
            bet         REAL,
            state       TEXT,
            started_at  TEXT
        );
        """)

def get_user(user_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()

def ensure_user(user_id: int, username: str, first_name: str, ref_by: int = None):
    with get_conn() as conn:
        existing = conn.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,)).fetchone()
        if existing:
            return False
        conn.execute(
            "INSERT INTO users (user_id,username,first_name,balance,ref_by,joined_at) VALUES (?,?,?,?,?,?)",
            (user_id, username or "", first_name or "", REG_BONUS, ref_by, datetime.now().isoformat())
        )
        log_tx(user_id, "bonus", REG_BONUS, "confirmed", "Регистрационный бонус", conn)
        return True

def get_balance(user_id: int) -> float:
    with get_conn() as conn:
        row = conn.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()
        return row["balance"] if row else 0

def update_balance(user_id: int, delta: float, conn=None):
    def _do(c):
        c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (delta, user_id))
    if conn:
        _do(conn)
    else:
        with get_conn() as c:
            _do(c)

def log_tx(user_id, tx_type, amount, status, note="", conn=None):
    def _do(c):
        c.execute(
            "INSERT INTO transactions (user_id,type,amount,status,note,created_at) VALUES (?,?,?,?,?,?)",
            (user_id, tx_type, amount, status, note, datetime.now().isoformat())
        )
    if conn:
        _do(conn)
    else:
        with get_conn() as c:
            _do(c)

def get_pending_withdrawals():
    with get_conn() as conn:
        return conn.execute(
            "SELECT t.*, u.username, u.first_name FROM transactions t "
            "JOIN users u ON t.user_id=u.user_id "
            "WHERE t.type='withdraw' AND t.status='pending' ORDER BY t.created_at"
        ).fetchall()

def get_stats():
    with get_conn() as conn:
        users  = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
        deps   = conn.execute("SELECT COALESCE(SUM(amount),0) as s FROM transactions WHERE type LIKE 'deposit%' AND status='confirmed'").fetchone()["s"]
        wins   = conn.execute("SELECT COALESCE(SUM(amount),0) as s FROM transactions WHERE type='game_win'").fetchone()["s"]
        losses = conn.execute("SELECT COALESCE(SUM(amount),0) as s FROM transactions WHERE type='game_loss'").fetchone()["s"]
        return users, deps, wins, losses

# ═══════════════════════════════════════
#              ИГРОВАЯ ЛОГИКА
# ═══════════════════════════════════════
def roll_win() -> bool:
    return random.random() < WIN_RATE

MINES_GRID  = 9
MINES_COUNT = 3
TOWER_LEVELS      = 5
TOWER_MULTIPLIERS = [1.5, 2.0, 3.0, 5.0, 10.0]

def mines_new_state(bet: float) -> dict:
    mines = set(random.sample(range(MINES_GRID), MINES_COUNT))
    return {"bet": bet, "mines": list(mines), "revealed": [],
            "multiplier": 1.0, "safe_clicked": 0, "finished": False}

def mines_keyboard(state: dict) -> InlineKeyboardMarkup:
    btns, row = [], []
    for i in range(MINES_GRID):
        if i in state["revealed"]:
            txt = "💣" if i in state["mines"] else f"💎"
            btn = InlineKeyboardButton(txt, callback_data="mines_done")
        else:
            btn = InlineKeyboardButton("⬜", callback_data=f"mines_cell_{i}")
        row.append(btn)
        if len(row) == 3:
            btns.append(row)
            row = []
    if state["safe_clicked"] > 0 and not state["finished"]:
        prize = state['bet'] * state['multiplier']
        btns.append([InlineKeyboardButton(
            f"💰 Забрать x{state['multiplier']:.1f} = {prize:.0f} 🪙",
            callback_data="mines_cashout"
        )])
    return InlineKeyboardMarkup(btns)

def tower_keyboard(state: dict) -> InlineKeyboardMarkup:
    level = state["level"]
    next_mult = TOWER_MULTIPLIERS[level] if level < TOWER_LEVELS else TOWER_MULTIPLIERS[-1]
    cur_mult  = TOWER_MULTIPLIERS[level-1] if level > 0 else 1.0
    btns = [
        [InlineKeyboardButton(f"⬆️ Уровень {level+1}/{TOWER_LEVELS} (риск → x{next_mult})", callback_data="tower_climb")],
        [InlineKeyboardButton(f"💰 Забрать x{cur_mult} = {state['bet'] * cur_mult:.0f} 🪙", callback_data="tower_cashout")]
    ]
    return InlineKeyboardMarkup(btns)

# ═══════════════════════════════════════
#              КЛАВИАТУРЫ
# ═══════════════════════════════════════
MAIN_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("🎮 Игры",      callback_data="menu_games"),
     InlineKeyboardButton("💰 Баланс",    callback_data="menu_balance")],
    [InlineKeyboardButton("➕ Пополнить", callback_data="menu_deposit"),
     InlineKeyboardButton("➖ Вывод",      callback_data="menu_withdraw")],
    [InlineKeyboardButton("🎁 Промокод",  callback_data="menu_promo"),
     InlineKeyboardButton("📊 Статистика",callback_data="menu_stats")],
    [InlineKeyboardButton("❓ Помощь",    callback_data="menu_help")]
])

GAMES_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("💣 Мины",       callback_data="game_mines"),
     InlineKeyboardButton("🎯 Дартс",      callback_data="game_darts")],
    [InlineKeyboardButton("🏀 Баскетбол",  callback_data="game_basketball"),
     InlineKeyboardButton("⚽ Футбол",      callback_data="game_football")],
    [InlineKeyboardButton("🗼 Башня",       callback_data="game_tower"),
     InlineKeyboardButton("🪙 Орёл/Решка", callback_data="game_coinflip")],
    [InlineKeyboardButton("🎲 Кости",      callback_data="game_dice")],
    [InlineKeyboardButton("◀️ Назад",      callback_data="menu_main")]
])

DEPOSIT_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("⭐ Telegram Stars", callback_data="dep_stars")],
    [InlineKeyboardButton("💵 USD (USDT/TRX)", callback_data="dep_usd")],
    [InlineKeyboardButton("◀️ Назад",          callback_data="menu_main")]
])

def back_kb(cb="menu_main"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data=cb)]])

def bet_kb(game: str) -> InlineKeyboardMarkup:
    bets = [10, 25, 50, 100, 250, 500]
    rows, row = [], []
    for b in bets:
        row.append(InlineKeyboardButton(f"{b} 🪙", callback_data=f"bet_{game}_{b}"))
        if len(row) == 3:
            rows.append(row); row = []
    if row: rows.append(row)
    rows.append([InlineKeyboardButton("✏️ Своя ставка", callback_data=f"bet_{game}_custom")])
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="menu_games")])
    return InlineKeyboardMarkup(rows)

# ═══════════════════════════════════════
#              КОМАНДЫ
# ═══════════════════════════════════════
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = ctx.args
    ref_by = None
    if args:
        try:
            ref_by = int(args[0])
            if ref_by == user.id: ref_by = None
        except: pass

    is_new = ensure_user(user.id, user.username, user.first_name, ref_by)

    if is_new:
        text = (
            f"🎰 *Добро пожаловать в VIP Vault Casino!*\n\n"
            f"Привет, {user.first_name}! 🎉\n\n"
            f"🎁 Тебе начислено *{REG_BONUS} 🪙* как приветственный бонус!\n\n"
            f"Выбери действие:"
        )
    else:
        bal = get_balance(user.id)
        text = (
            f"🎰 *VIP Vault Casino*\n\n"
            f"С возвращением, {user.first_name}!\n"
            f"💰 Баланс: *{bal:.0f} 🪙*\n\n"
            f"Выбери действие:"
        )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=MAIN_KB)


async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    users, deps, wins, losses = get_stats()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Заявки на вывод",    callback_data="admin_withdrawals")],
        [InlineKeyboardButton("📊 Подтв. USD депозит", callback_data="admin_confirm_usd_info")],
    ])
    await update.message.reply_text(
        f"👑 *ADMIN PANEL*\n\n"
        f"👥 Пользователей: `{users}`\n"
        f"💰 Всего депозитов: `{deps:.0f}`\n"
        f"🏆 Всего выиграно: `{wins:.0f}`\n"
        f"💸 Всего проиграно: `{losses:.0f}`\n"
        f"📈 Профит казино: `{losses - wins:.0f}`\n\n"
        f"*Команды:*\n"
        f"`/give user_id сумма` — начислить токены\n"
        f"`/newpromo КОД СУММА USES` — создать промокод\n"
        f"`/confirm_dep user_id токены` — подтвердить USD",
        parse_mode="Markdown", reply_markup=kb
    )


async def cmd_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username, user.first_name)
    bal = get_balance(user.id)
    await update.message.reply_text(
        f"💰 Твой баланс: *{bal:.0f} 🪙*",
        parse_mode="Markdown", reply_markup=back_kb()
    )


async def cmd_give(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        uid    = int(ctx.args[0])
        amount = float(ctx.args[1])
    except:
        await update.message.reply_text("❌ Формат: /give user_id amount")
        return
    if not get_user(uid):
        await update.message.reply_text("❌ Пользователь не найден.")
        return
    update_balance(uid, amount)
    log_tx(uid, "bonus", amount, "confirmed", "Бонус от администратора")
    await update.message.reply_text(f"✅ Начислено {amount:.0f} 🪙 пользователю {uid}.")
    try:
        await ctx.bot.send_message(uid, f"🎁 Тебе начислен бонус: *{amount:.0f} 🪙*!\nОт администратора казино.", parse_mode="Markdown")
    except: pass


async def cmd_promo_create(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        code   = ctx.args[0].upper()
        amount = float(ctx.args[1])
        uses   = int(ctx.args[2])
    except:
        await update.message.reply_text("❌ Формат: /newpromo КОД СУММА КОЛИЧЕСТВО")
        return
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO promo_codes (code,amount,uses_left,created_by,created_at) VALUES (?,?,?,?,?)",
            (code, amount, uses, ADMIN_ID, datetime.now().isoformat())
        )
    await update.message.reply_text(
        f"✅ Промокод создан!\n`{code}` — `{amount:.0f} 🪙` — {uses} использований",
        parse_mode="Markdown"
    )


async def cmd_confirm_dep(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        uid    = int(ctx.args[0])
        tokens = float(ctx.args[1])
    except:
        await update.message.reply_text("❌ Формат: /confirm_dep user_id tokens")
        return
    if not get_user(uid):
        await update.message.reply_text("❌ Пользователь не найден.")
        return
    update_balance(uid, tokens)
    with get_conn() as conn:
        conn.execute("UPDATE users SET total_dep=total_dep+? WHERE user_id=?", (tokens, uid))
    log_tx(uid, "deposit_usd", tokens, "confirmed", "Подтверждено администратором")
    await update.message.reply_text(f"✅ Начислено {tokens:.0f} 🪙 пользователю {uid}.")
    try:
        await ctx.bot.send_message(
            uid,
            f"✅ *Депозит подтверждён!*\n💰 +`{tokens:.0f} 🪙` зачислено на баланс.",
            parse_mode="Markdown"
        )
    except: pass

# ═══════════════════════════════════════
#              CALLBACK
# ═══════════════════════════════════════
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    data = q.data
    user = q.from_user
    ensure_user(user.id, user.username, user.first_name)

    if data == "menu_main":
        bal = get_balance(user.id)
        await q.edit_message_text(
            f"🎰 *VIP Vault Casino*\n\n💰 Баланс: *{bal:.0f} 🪙*\n\nВыбери действие:",
            parse_mode="Markdown", reply_markup=MAIN_KB
        )

    elif data == "menu_games":
        await q.edit_message_text("🎮 *Выбери игру:*", parse_mode="Markdown", reply_markup=GAMES_KB)

    elif data == "menu_balance":
        bal = get_balance(user.id)
        row = get_user(user.id)
        await q.edit_message_text(
            f"💰 *Твой баланс*\n\n"
            f"🪙 Текущий: `{bal:.0f}`\n"
            f"📥 Всего пополнено: `{row['total_dep']:.0f}`\n"
            f"🏆 Всего выиграно: `{row['total_won']:.0f}`\n"
            f"💸 Всего проиграно: `{row['total_lost']:.0f}`\n"
            f"🎮 Сыграно игр: `{row['games_count']}`",
            parse_mode="Markdown", reply_markup=back_kb()
        )

    elif data == "menu_deposit":
        await q.edit_message_text(
            "➕ *Пополнение баланса*\n\nВыбери способ оплаты:",
            parse_mode="Markdown", reply_markup=DEPOSIT_KB
        )

    elif data == "menu_withdraw":
        bal = get_balance(user.id)
        ctx.user_data["action"] = "withdraw"
        await q.edit_message_text(
            f"➖ *Вывод средств*\n\n"
            f"💰 Баланс: `{bal:.0f} 🪙`\n"
            f"📌 Минимум: `{MIN_WITHDRAW} 🪙`\n\n"
            f"Введи в формате:\n`сумма кошелёк`\n\n"
            f"Пример: `500 TRXwallet123`",
            parse_mode="Markdown", reply_markup=back_kb()
        )

    elif data == "menu_promo":
        ctx.user_data["action"] = "promo"
        await q.edit_message_text("🎁 *Промокод*\n\nВведи промокод:", parse_mode="Markdown", reply_markup=back_kb())

    elif data == "menu_stats":
        row = get_user(user.id)
        bal = get_balance(user.id)
        winrate = 0
        if row["games_count"] > 0:
            with get_conn() as conn:
                wins_c = conn.execute(
                    "SELECT COUNT(*) as c FROM transactions WHERE user_id=? AND type='game_win'",
                    (user.id,)
                ).fetchone()["c"]
            winrate = wins_c / row["games_count"] * 100
        await q.edit_message_text(
            f"📊 *Твоя статистика*\n\n"
            f"💰 Баланс: `{bal:.0f} 🪙`\n"
            f"🎮 Игр сыграно: `{row['games_count']}`\n"
            f"📈 Процент побед: `{winrate:.1f}%`\n"
            f"🏆 Выиграно: `{row['total_won']:.0f} 🪙`\n"
            f"💸 Проиграно: `{row['total_lost']:.0f} 🪙`",
            parse_mode="Markdown", reply_markup=back_kb()
        )

    elif data == "menu_help":
        link = f"https://t.me/{ctx.bot.username}?start={user.id}"
        await q.edit_message_text(
            f"❓ *Помощь*\n\n"
            f"🎮 *Игры и множители:*\n"
            f"• 💣 Мины — x2.8 | • 🎯 Дартс — x2.5\n"
            f"• 🏀 Баскетбол — x2.2 | • ⚽ Футбол — x2.0\n"
            f"• 🗼 Башня — до x10 | • 🪙 Орёл/Решка — x1.9\n"
            f"• 🎲 Кости — x4.5\n\n"
            f"💰 *Пополнение:*\n"
            f"• ⭐ Stars: 1 Star = {STARS_TO_TOKENS} 🪙\n"
            f"• 💵 USD: 1$ = {USD_TO_TOKENS} 🪙\n\n"
            f"📞 Поддержка: @admin\n\n"
            f"🔗 Твоя реферальная ссылка:\n`{link}`",
            parse_mode="Markdown", reply_markup=back_kb()
        )

    # ─── ДЕПОЗИТ Stars ───
    elif data == "dep_stars":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"⭐ 50 Stars → {50*STARS_TO_TOKENS} 🪙",   callback_data="stars_50")],
            [InlineKeyboardButton(f"⭐ 100 Stars → {100*STARS_TO_TOKENS} 🪙", callback_data="stars_100")],
            [InlineKeyboardButton(f"⭐ 250 Stars → {250*STARS_TO_TOKENS} 🪙", callback_data="stars_250")],
            [InlineKeyboardButton(f"⭐ 500 Stars → {500*STARS_TO_TOKENS} 🪙", callback_data="stars_500")],
            [InlineKeyboardButton("◀️ Назад", callback_data="menu_deposit")]
        ])
        await q.edit_message_text(
            "⭐ *Пополнение через Telegram Stars*\n\nВыбери сумму:",
            parse_mode="Markdown", reply_markup=kb
        )

    elif data.startswith("stars_"):
        stars  = int(data.split("_")[1])
        tokens = stars * STARS_TO_TOKENS
        await ctx.bot.send_invoice(
            chat_id=user.id,
            title=f"💰 Пополнение {tokens} 🪙",
            description=f"Купить {tokens} игровых токенов за {stars} Stars",
            payload=f"deposit_stars_{stars}_{user.id}",
            currency="XTR",
            prices=[LabeledPrice(f"{tokens} токенов", stars)]
        )
        await q.edit_message_text(
            "💳 Счёт отправлен в чат. Оплати его!",
            reply_markup=back_kb("menu_deposit")
        )

    elif data == "dep_usd":
        ctx.user_data["action"] = "deposit_usd"
        await q.edit_message_text(
            f"💵 *Пополнение через USD*\n\n"
            f"Отправь USDT/TRX на адрес:\n"
            f"`{USD_WALLET}`\n\n"
            f"Курс: `1 USD = {USD_TO_TOKENS} 🪙`\n\n"
            f"После оплаты напиши:\n"
            f"`txid сумма_в_usd`\n\n"
            f"Пример: `abc123def 10`\n\n"
            f"⏳ Подтверждение: до 30 минут",
            parse_mode="Markdown", reply_markup=back_kb("menu_deposit")
        )

    elif data == "admin_confirm_usd_info":
        if user.id != ADMIN_ID: return
        await q.edit_message_text(
            "💵 Для подтверждения USD депозита:\n`/confirm_dep user_id токены`",
            parse_mode="Markdown", reply_markup=back_kb()
        )

    # ─── ВЫБОР ИГРЫ (выбор ставки) ───
    elif data.startswith("game_"):
        game = data.split("_")[1]
        names = {
            "mines": "💣 Мины", "darts": "🎯 Дартс",
            "basketball": "🏀 Баскетбол", "football": "⚽ Футбол",
            "tower": "🗼 Башня", "coinflip": "🪙 Орёл/Решка", "dice": "🎲 Кости"
        }
        mults = {
            "mines": "x2.8", "darts": "x2.5", "basketball": "x2.2",
            "football": "x2.0", "tower": "до x10", "coinflip": "x1.9", "dice": "x4.5"
        }
        bal = get_balance(user.id)
        await q.edit_message_text(
            f"{names.get(game,'Игра')} | Множитель: *{mults.get(game,'x2')}*\n\n"
            f"💰 Баланс: `{bal:.0f} 🪙`\nВыбери ставку:",
            parse_mode="Markdown", reply_markup=bet_kb(game)
        )

    # ─── СТАВКА ВЫБРАНА ───
    elif data.startswith("bet_"):
        parts = data.split("_")
        game  = parts[1]
        val   = parts[2]
        if val == "custom":
            ctx.user_data["action"] = "custom_bet"
            ctx.user_data["game"]   = game
            await q.edit_message_text(
                f"✏️ Введи ставку (от {MIN_BET} до {MAX_BET} 🪙):",
                reply_markup=back_kb(f"game_{game}")
            )
            return
        await _start_game(q, ctx, user, game, float(val))

    # ─── МИНЫ ───
    elif data.startswith("mines_cell_"):
        cell_idx = int(data.split("_")[2])
        ag = _get_active_game(user.id, "mines")
        if not ag:
            await q.edit_message_text("❌ Игра не найдена. Начни заново.", reply_markup=back_kb("game_mines"))
            return
        state = json.loads(ag["state"])
        if state["finished"] or cell_idx in state["revealed"]: return

        state["revealed"].append(cell_idx)
        if cell_idx in state["mines"]:
            state["finished"] = True
            _save_active_game(user.id, "mines", state)
            await _finish_game(q, ctx, user, "mines", state["bet"], False, state)
        else:
            state["safe_clicked"] += 1
            state["multiplier"] = round(1.0 + state["safe_clicked"] * 0.6, 1)
            _save_active_game(user.id, "mines", state)
            prize = state['bet'] * state['multiplier']
            await q.edit_message_text(
                f"💣 *Мины*\n\n✅ Безопасно!\nМножитель: *x{state['multiplier']}*\n"
                f"Выигрыш: *{prize:.0f} 🪙*\n\nПродолжай или забери!",
                parse_mode="Markdown", reply_markup=mines_keyboard(state)
            )

    elif data == "mines_cashout":
        ag = _get_active_game(user.id, "mines")
        if not ag: return
        state = json.loads(ag["state"])
        if state["finished"]: return
        state["finished"] = True
        _save_active_game(user.id, "mines", state)
        await _finish_game(q, ctx, user, "mines", state["bet"], True, state)

    elif data == "mines_done":
        pass

    # ─── БАШНЯ ───
    elif data == "tower_climb":
        ag = _get_active_game(user.id, "tower")
        if not ag: return
        state = json.loads(ag["state"])
        if state["finished"]: return
        if roll_win():
            state["level"] += 1
            if state["level"] >= TOWER_LEVELS:
                state["finished"] = True
                _save_active_game(user.id, "tower", state)
                await _finish_game(q, ctx, user, "tower", state["bet"], True, state)
            else:
                cur_mult  = TOWER_MULTIPLIERS[state["level"] - 1]
                _save_active_game(user.id, "tower", state)
                await q.edit_message_text(
                    f"🗼 *Башня* — уровень {state['level']}/{TOWER_LEVELS}\n\n"
                    f"✅ Пройден! Текущий множитель: *x{cur_mult}*\n\n"
                    f"Продолжать или забрать?",
                    parse_mode="Markdown", reply_markup=tower_keyboard(state)
                )
        else:
            state["finished"] = True
            _save_active_game(user.id, "tower", state)
            await _finish_game(q, ctx, user, "tower", state["bet"], False, state)

    elif data == "tower_cashout":
        ag = _get_active_game(user.id, "tower")
        if not ag: return
        state = json.loads(ag["state"])
        if state["finished"]: return
        state["finished"] = True
        _save_active_game(user.id, "tower", state)
        await _finish_game(q, ctx, user, "tower", state["bet"], True, state)

    # ─── ОРЁЛ/РЕШКА ───
    elif data in ("cf_heads", "cf_tails"):
        ag = _get_active_game(user.id, "coinflip")
        if not ag: return
        state  = json.loads(ag["state"])
        choice = "heads" if data == "cf_heads" else "tails"
        won    = roll_win()
        if won:
            result = choice
        else:
            result = "tails" if choice == "heads" else "heads"
        emoji  = "🦅 Орёл" if result == "heads" else "🪙 Решка"
        state["finished"] = True
        _save_active_game(user.id, "coinflip", state)
        await _finish_game(q, ctx, user, "coinflip", state["bet"], won,
                           {"result_text": emoji, **state})

    # ─── КОСТИ ───
    elif data.startswith("dice_pick_"):
        face   = int(data.split("_")[2])
        ag     = _get_active_game(user.id, "dice")
        if not ag: return
        state  = json.loads(ag["state"])
        won    = roll_win()
        if won:
            result_face = face
        else:
            result_face = random.choice([x for x in range(1, 7) if x != face])
        state["finished"] = True
        _save_active_game(user.id, "dice", state)
        await _finish_game(q, ctx, user, "dice", state["bet"], won,
                           {"dice_face": result_face, "picked": face, **state})

    # ─── ADMIN — ЗАЯВКИ НА ВЫВОД ───
    elif data == "admin_withdrawals":
        if user.id != ADMIN_ID: return
        rows = get_pending_withdrawals()
        if not rows:
            await q.edit_message_text("✅ Нет заявок на вывод.", reply_markup=back_kb())
            return
        await q.edit_message_text(f"💸 Найдено заявок: {len(rows)}. Отправляю...", reply_markup=back_kb())
        for row in rows:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Подтвердить", callback_data=f"admin_wd_ok_{row['id']}"),
                InlineKeyboardButton("❌ Отклонить",   callback_data=f"admin_wd_no_{row['id']}")
            ]])
            await ctx.bot.send_message(
                ADMIN_ID,
                f"💸 *Заявка #{row['id']}*\n"
                f"👤 @{row['username'] or row['first_name']} (ID: `{row['user_id']}`)\n"
                f"💰 Сумма: `{row['amount']:.0f} 🪙`\n"
                f"💳 Реквизиты: `{row['note']}`\n"
                f"📅 Дата: {row['created_at'][:10]}",
                parse_mode="Markdown", reply_markup=kb
            )

    elif data.startswith("admin_wd_ok_"):
        if user.id != ADMIN_ID: return
        tx_id = int(data.split("_")[3])
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM transactions WHERE id=?", (tx_id,)).fetchone()
            if row and row["status"] == "pending":
                conn.execute("UPDATE transactions SET status='confirmed' WHERE id=?", (tx_id,))
        await q.edit_message_text(f"✅ Вывод #{tx_id} подтверждён.")
        try:
            await ctx.bot.send_message(
                row["user_id"],
                f"✅ Вывод *{row['amount']:.0f} 🪙* подтверждён!\nСредства отправлены.",
                parse_mode="Markdown"
            )
        except: pass

    elif data.startswith("admin_wd_no_"):
        if user.id != ADMIN_ID: return
        tx_id = int(data.split("_")[3])
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM transactions WHERE id=?", (tx_id,)).fetchone()
            if row and row["status"] == "pending":
                conn.execute("UPDATE transactions SET status='rejected' WHERE id=?", (tx_id,))
                update_balance(row["user_id"], row["amount"], conn)
        await q.edit_message_text(f"❌ Вывод #{tx_id} отклонён, средства возвращены.")
        try:
            await ctx.bot.send_message(
                row["user_id"],
                f"❌ Вывод *{row['amount']:.0f} 🪙* отклонён.\nСредства возвращены на баланс.",
                parse_mode="Markdown"
            )
        except: pass


# ═══════════════════════════════════════
#         ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ═══════════════════════════════════════
def _get_active_game(user_id: int, game_type: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM active_games WHERE user_id=? AND game_type=?",
            (user_id, game_type)
        ).fetchone()

def _save_active_game(user_id: int, game_type: str, state: dict):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO active_games (user_id,game_type,bet,state,started_at) VALUES (?,?,?,?,?)",
            (user_id, game_type, state.get("bet", 0), json.dumps(state), datetime.now().isoformat())
        )

def _del_active_game(user_id: int, game_type: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM active_games WHERE user_id=? AND game_type=?", (user_id, game_type))

async def _start_game(q, ctx, user, game: str, bet: float):
    if bet < MIN_BET or bet > MAX_BET:
        await q.edit_message_text(
            f"❌ Ставка: от {MIN_BET} до {MAX_BET} 🪙",
            reply_markup=back_kb(f"game_{game}")
        )
        return
    bal = get_balance(user.id)
    if bal < bet:
        await q.edit_message_text(
            f"❌ Недостаточно средств!\n💰 Баланс: `{bal:.0f} 🪙`\nСтавка: `{bet:.0f} 🪙`",
            parse_mode="Markdown", reply_markup=back_kb("menu_deposit")
        )
        return

    update_balance(user.id, -bet)

    if game == "mines":
        state = mines_new_state(bet)
        _save_active_game(user.id, "mines", state)
        await q.edit_message_text(
            f"💣 *Мины*\n\nСтавка: `{bet:.0f} 🪙`\n"
            f"Поле 3×3 — {MINES_COUNT} бомбы спрятаны.\nНажимай клетки, ищи алмазы!",
            parse_mode="Markdown", reply_markup=mines_keyboard(state)
        )

    elif game == "tower":
        state = {"bet": bet, "level": 0, "finished": False}
        _save_active_game(user.id, "tower", state)
        await q.edit_message_text(
            f"🗼 *Башня*\n\nСтавка: `{bet:.0f} 🪙`\n"
            f"Поднимайся! На вершине множитель *x{TOWER_MULTIPLIERS[-1]}*",
            parse_mode="Markdown", reply_markup=tower_keyboard(state)
        )

    elif game == "coinflip":
        state = {"bet": bet, "finished": False}
        _save_active_game(user.id, "coinflip", state)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🦅 Орёл", callback_data="cf_heads"),
            InlineKeyboardButton("🪙 Решка", callback_data="cf_tails")
        ]])
        await q.edit_message_text(
            f"🪙 *Орёл или Решка?*\n\nСтавка: `{bet:.0f} 🪙` | Выигрыш: x1.9\n\nВыбирай:",
            parse_mode="Markdown", reply_markup=kb
        )

    elif game == "dice":
        state = {"bet": bet, "finished": False}
        _save_active_game(user.id, "dice", state)
        faces = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣"]
        btns  = [
            [InlineKeyboardButton(faces[i], callback_data=f"dice_pick_{i+1}") for i in range(3)],
            [InlineKeyboardButton(faces[i], callback_data=f"dice_pick_{i+1}") for i in range(3, 6)]
        ]
        await q.edit_message_text(
            f"🎲 *Кости*\n\nСтавка: `{bet:.0f} 🪙` | Выигрыш: x4.5\n\nВыбери число:",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns)
        )

    else:
        gd = {
            "darts":      ("🎯", "Дартс",    "В яблочко!", "Мимо цели",   2.5),
            "basketball": ("🏀", "Баскетбол","Попал!",     "Мимо кольца", 2.2),
            "football":   ("⚽", "Футбол",   "ГОЛ!",       "Мимо ворот",  2.0),
        }
        emoji, name, win_txt, lose_txt, mult = gd[game]
        won   = roll_win()
        state = {"bet": bet, "finished": True}
        _save_active_game(user.id, game, state)
        await _finish_game(q, ctx, user, game, bet, won,
                           {"emoji": emoji, "name": name,
                            "win_txt": win_txt, "lose_txt": lose_txt, "mult": mult})


async def _finish_game(q, ctx, user, game: str, bet: float, won: bool, extra: dict = None):
    _del_active_game(user.id, game)
    extra = extra or {}

    if game == "mines":
        mult  = extra.get("multiplier", 1.0)
        prize = bet * mult if won else 0
        result = f"💎 Забрал выигрыш! x{mult}" if won else "💥 ВЗРЫВ! Попал на бомбу"
    elif game == "tower":
        level = extra.get("level", 0)
        mult  = TOWER_MULTIPLIERS[level-1] if level > 0 else 1.0
        prize = bet * mult if won else 0
        result = f"🏆 Уровень {level}! x{mult}" if won else "💥 Упал с башни!"
    elif game == "coinflip":
        mult  = 1.9
        prize = bet * mult if won else 0
        result = extra.get("result_text", "")
    elif game == "dice":
        mult  = 4.5
        prize = bet * mult if won else 0
        result = f"🎲 Выпало: {extra.get('dice_face')} | Выбрал: {extra.get('picked')}"
    else:
        mult  = extra.get("mult", 2.0)
        prize = bet * mult if won else 0
        em    = extra.get("emoji", "🎮")
        result = f"{em} {extra.get('win_txt','Победа!')}" if won else f"{em} {extra.get('lose_txt','Поражение')}"

    with get_conn() as conn:
        if won:
            update_balance(user.id, prize, conn)
            conn.execute(
                "UPDATE users SET total_won=total_won+?, games_count=games_count+1 WHERE user_id=?",
                (prize, user.id)
            )
            log_tx(user.id, "game_win", prize, "confirmed", f"{game} +{prize:.0f}", conn)
        else:
            conn.execute(
                "UPDATE users SET total_lost=total_lost+?, games_count=games_count+1 WHERE user_id=?",
                (bet, user.id)
            )
            log_tx(user.id, "game_loss", bet, "confirmed", f"{game} -{bet:.0f}", conn)

    bal = get_balance(user.id)
    if won:
        header = f"🏆 *ПОБЕДА!*\n\n{result}\n\n💰 +`{prize:.0f} 🪙` (x{mult})"
    else:
        header = f"💸 *ПОРАЖЕНИЕ*\n\n{result}\n\n❌ Ставка `{bet:.0f} 🪙` проиграна"

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Ещё раз",  callback_data=f"game_{game}"),
        InlineKeyboardButton("🏠 Меню",     callback_data="menu_main")
    ]])
    await q.edit_message_text(
        f"{header}\n\n💰 Баланс: `{bal:.0f} 🪙`",
        parse_mode="Markdown", reply_markup=kb
    )


# ═══════════════════════════════════════
#         ТЕКСТОВЫЕ СООБЩЕНИЯ
# ═══════════════════════════════════════
async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user   = update.effective_user
    ensure_user(user.id, user.username, user.first_name)
    text   = update.message.text.strip()
    action = ctx.user_data.get("action")

    if action == "withdraw":
        parts = text.split()
        if len(parts) < 2:
            await update.message.reply_text("❌ Формат: `сумма кошелёк`", parse_mode="Markdown")
            return
        try:
            amount = float(parts[0]); wallet = parts[1]
        except:
            await update.message.reply_text("❌ Неверный формат.")
            return
        if amount < MIN_WITHDRAW:
            await update.message.reply_text(f"❌ Минимум: {MIN_WITHDRAW} 🪙")
            return
        bal = get_balance(user.id)
        if bal < amount:
            await update.message.reply_text(f"❌ Недостаточно средств. Баланс: {bal:.0f} 🪙")
            return
        update_balance(user.id, -amount)
        log_tx(user.id, "withdraw", amount, "pending", wallet)
        ctx.user_data.pop("action", None)
        try:
            await ctx.bot.send_message(
                ADMIN_ID,
                f"💸 *ВЫВОД* | @{user.username or user.first_name} (ID: `{user.id}`)\n"
                f"💰 `{amount:.0f} 🪙` → `{wallet}`\n"
                f"Используй /admin для управления.",
                parse_mode="Markdown"
            )
        except: pass
        await update.message.reply_text(
            f"✅ Заявка на вывод *{amount:.0f} 🪙* создана!\n⏳ Ожидай до 24 часов.",
            parse_mode="Markdown", reply_markup=back_kb()
        )

    elif action == "promo":
        code = text.upper()
        with get_conn() as conn:
            promo = conn.execute("SELECT * FROM promo_codes WHERE code=?", (code,)).fetchone()
            if not promo:
                await update.message.reply_text("❌ Промокод не найден.", reply_markup=back_kb())
                return
            if promo["uses_left"] <= 0:
                await update.message.reply_text("❌ Промокод исчерпан.", reply_markup=back_kb())
                return
            if conn.execute("SELECT 1 FROM used_promos WHERE user_id=? AND code=?", (user.id, code)).fetchone():
                await update.message.reply_text("❌ Ты уже использовал этот промокод.", reply_markup=back_kb())
                return
            amount = promo["amount"]
            update_balance(user.id, amount, conn)
            conn.execute("UPDATE promo_codes SET uses_left=uses_left-1, used_count=used_count+1 WHERE code=?", (code,))
            conn.execute("INSERT INTO used_promos VALUES (?,?)", (user.id, code))
            log_tx(user.id, "bonus", amount, "confirmed", f"Промокод {code}", conn)
        ctx.user_data.pop("action", None)
        bal = get_balance(user.id)
        await update.message.reply_text(
            f"🎁 Промокод активирован!\n💰 +*{amount:.0f} 🪙*\nБаланс: `{bal:.0f} 🪙`",
            parse_mode="Markdown", reply_markup=MAIN_KB
        )

    elif action == "deposit_usd":
        parts = text.split()
        if len(parts) < 2:
            await update.message.reply_text("❌ Формат: `txid сумма_в_usd`", parse_mode="Markdown")
            return
        try:
            txid = parts[0]; amount = float(parts[1])
        except:
            await update.message.reply_text("❌ Неверный формат.")
            return
        tokens = amount * USD_TO_TOKENS
        log_tx(user.id, "deposit_usd", tokens, "pending", f"txid:{txid} usd:{amount}")
        ctx.user_data.pop("action", None)
        try:
            await ctx.bot.send_message(
                ADMIN_ID,
                f"💵 *USD ДЕПОЗИТ*\n"
                f"👤 @{user.username or user.first_name} (ID: `{user.id}`)\n"
                f"💰 ${amount} → {tokens:.0f} 🪙\n"
                f"🔗 TxID: `{txid}`\n\n"
                f"Подтвердить:\n`/confirm_dep {user.id} {tokens:.0f}`",
                parse_mode="Markdown"
            )
        except: pass
        await update.message.reply_text(
            f"✅ Запрос отправлен!\n⏳ Ожидай `{tokens:.0f} 🪙` — до 30 минут.",
            parse_mode="Markdown", reply_markup=back_kb()
        )

    elif action == "custom_bet":
        game = ctx.user_data.get("game")
        try:
            bet = float(text.replace(",", "."))
        except:
            await update.message.reply_text("❌ Введи число.")
            return
        ctx.user_data.pop("action", None)
        ctx.user_data.pop("game", None)

        class FakeQ:
            from_user = user
            async def edit_message_text(self, *a, **kw):
                await update.message.reply_text(*a, **kw)
            async def answer(self): pass

        await _start_game(FakeQ(), ctx, user, game, bet)

    else:
        bal = get_balance(user.id)
        await update.message.reply_text(
            f"🎰 *VIP Vault Casino*\n💰 Баланс: `{bal:.0f} 🪙`",
            parse_mode="Markdown", reply_markup=MAIN_KB
        )


# ═══════════════════════════════════════
#         STARS ПЛАТЁЖ
# ═══════════════════════════════════════
async def precheckout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.pre_checkout_query
    if q.invoice_payload.startswith("deposit_stars_"):
        await q.answer(ok=True)
    else:
        await q.answer(ok=False, error_message="Неизвестный платёж")

async def successful_payment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    payment = update.message.successful_payment
    payload = payment.invoice_payload

    if payload.startswith("deposit_stars_"):
        stars  = int(payload.split("_")[2])
        tokens = stars * STARS_TO_TOKENS
        update_balance(user.id, tokens)
        with get_conn() as conn:
            conn.execute("UPDATE users SET total_dep=total_dep+? WHERE user_id=?", (tokens, user.id))
        log_tx(user.id, "deposit_stars", tokens, "confirmed", f"Stars:{stars}")
        bal = get_balance(user.id)
        await update.message.reply_text(
            f"✅ *Пополнение успешно!*\n\n"
            f"⭐ Stars: `{stars}`\n🪙 Получено: `{tokens}`\n💰 Баланс: `{bal:.0f} 🪙`",
            parse_mode="Markdown", reply_markup=MAIN_KB
        )
        try:
            await ctx.bot.send_message(
                ADMIN_ID,
                f"⭐ *Stars депозит!*\n"
                f"👤 @{user.username or user.first_name} (ID: `{user.id}`)\n"
                f"💰 {stars}⭐ → {tokens} 🪙",
                parse_mode="Markdown"
            )
        except: pass


# ═══════════════════════════════════════
#              ЗАПУСК
# ═══════════════════════════════════════
def main():
    init_db()
    logger.info("✅ База данных готова")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("admin",       cmd_admin))
    app.add_handler(CommandHandler("balance",     cmd_balance))
    app.add_handler(CommandHandler("give",        cmd_give))
    app.add_handler(CommandHandler("newpromo",    cmd_promo_create))
    app.add_handler(CommandHandler("confirm_dep", cmd_confirm_dep))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    logger.info("🚀 Бот запущен! Polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
