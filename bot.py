"""
╔══════════════════════════════════════════════╗
║       🎰  VIP VAULT CASINO BOT  v2.0         ║
║  Игры: 12+  |  Уровни  |  Лайв-канал         ║
║  Кэшбек  |  ТОП-10  |  Рефералы  |  Stars    ║
╚══════════════════════════════════════════════╝
"""

import logging, sqlite3, random, json, math
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, PreCheckoutQueryHandler,
    filters, ContextTypes
)

# ═══════════════════════════════════════════════════
#                    ⚙️  КОНФИГ
# ═══════════════════════════════════════════════════
BOT_TOKEN        = "8603411643:AAEFOuhoBvnmh90h4MK9PsqQnot5uQGkmTY"       # @BotFather
ADMIN_ID         = 7564112818                   # Твой Telegram ID
LIVE_CHANNEL_ID  = -1003722065120               # ID лайв-канала (или "" чтобы отключить)
WIN_RATE         = 0.30
REG_BONUS        = 50
STARS_TO_TOKENS  = 2                           # 1 ⭐ = 2 🪙
USD_TO_TOKENS    = 200                         # 1 USD = 200 🪙
USD_WALLET       = "UQBufQWcrufQW_S0ZqhxYEIO6g1R1YoaE3l5aNd9rM3zqTAa"
MIN_BET          = 1
MAX_BET          = 100000
MIN_WITHDRAW     = 50
DB_FILE          = "casino.db"

# ─── Система уровней ───────────────────────────
LEVELS = [
    {"name": "Новичок",    "emoji": "🌱", "min_xp": 0,     "cashback": 0.00, "next": 100},
    {"name": "Рекрут",     "emoji": "🤠", "min_xp": 100,   "cashback": 0.01, "next": 500},
    {"name": "Игрок",      "emoji": "🎮", "min_xp": 500,   "cashback": 0.02, "next": 1500},
    {"name": "Ветеран",    "emoji": "⚔️",  "min_xp": 1500,  "cashback": 0.03, "next": 5000},
    {"name": "Патрон",     "emoji": "🎩", "min_xp": 5000,  "cashback": 0.05, "next": 15000},
    {"name": "Смотрящий",  "emoji": "👁", "min_xp": 15000, "cashback": 0.07, "next": 50000},
    {"name": "Легенда",    "emoji": "👑", "min_xp": 50000, "cashback": 0.10, "next": 99999999},
]

def get_level(xp: float) -> dict:
    lvl = LEVELS[0]
    for l in LEVELS:
        if xp >= l["min_xp"]:
            lvl = l
    return lvl

def xp_bar(xp: float) -> str:
    lvl  = get_level(xp)
    idx  = LEVELS.index(lvl)
    nxt  = LEVELS[idx + 1] if idx + 1 < len(LEVELS) else lvl
    if idx + 1 >= len(LEVELS):
        return "█████████████ MAX"
    cur  = xp - lvl["min_xp"]
    need = nxt["min_xp"] - lvl["min_xp"]
    pct  = min(cur / need, 1.0)
    bars = int(pct * 13)
    return "█" * bars + "░" * (13 - bars) + f" ({int(cur)}/{int(need)})"

# ═══════════════════════════════════════════════════
#                   🗄️  БАЗА ДАННЫХ
# ═══════════════════════════════════════════════════
def get_conn():
    c = sqlite3.connect(DB_FILE, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with get_conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id       INTEGER PRIMARY KEY,
            username      TEXT DEFAULT '',
            first_name    TEXT DEFAULT '',
            balance       REAL DEFAULT 0,
            total_dep     REAL DEFAULT 0,
            total_won     REAL DEFAULT 0,
            total_lost    REAL DEFAULT 0,
            total_turnover REAL DEFAULT 0,
            xp            REAL DEFAULT 0,
            games_count   INTEGER DEFAULT 0,
            ref_by        INTEGER DEFAULT NULL,
            cashback_accum REAL DEFAULT 0,
            joined_at     TEXT
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER,
            type       TEXT,
            amount     REAL,
            status     TEXT,
            note       TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS promo_codes (
            code       TEXT PRIMARY KEY,
            amount     REAL,
            uses_left  INTEGER,
            used_count INTEGER DEFAULT 0,
            created_by INTEGER,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS used_promos (
            user_id INTEGER,
            code    TEXT,
            PRIMARY KEY (user_id, code)
        );
        CREATE TABLE IF NOT EXISTS active_games (
            user_id    INTEGER PRIMARY KEY,
            game_type  TEXT,
            bet        REAL,
            state      TEXT,
            started_at TEXT
        );
        """)

def get_user(uid: int):
    with get_conn() as c:
        return c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()

def ensure_user(uid: int, username: str, first_name: str, ref_by: int = None) -> bool:
    with get_conn() as c:
        if c.execute("SELECT 1 FROM users WHERE user_id=?", (uid,)).fetchone():
            return False
        c.execute(
            "INSERT INTO users (user_id,username,first_name,balance,ref_by,joined_at) VALUES (?,?,?,?,?,?)",
            (uid, username or "", first_name or "", REG_BONUS, ref_by, datetime.now().isoformat())
        )
        log_tx(uid, "bonus", REG_BONUS, "confirmed", "Регистрационный бонус", c)
        return True

def get_balance(uid: int) -> float:
    with get_conn() as c:
        r = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
        return r["balance"] if r else 0

def update_balance(uid: int, delta: float, conn=None):
    def _do(c): c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (delta, uid))
    if conn: _do(conn)
    else:
        with get_conn() as c: _do(c)

def add_xp(uid: int, bet: float, conn=None):
    """1 XP за каждые 10 токенов оборота."""
    xp_gain = bet / 10
    def _do(c):
        c.execute(
            "UPDATE users SET xp=xp+?, total_turnover=total_turnover+? WHERE user_id=?",
            (xp_gain, bet, uid)
        )
    if conn: _do(conn)
    else:
        with get_conn() as c: _do(c)

def log_tx(uid, tx_type, amount, status, note="", conn=None):
    def _do(c):
        c.execute(
            "INSERT INTO transactions (user_id,type,amount,status,note,created_at) VALUES (?,?,?,?,?,?)",
            (uid, tx_type, amount, status, note, datetime.now().isoformat())
        )
    if conn: _do(conn)
    else:
        with get_conn() as c: _do(c)

def get_top10():
    with get_conn() as c:
        return c.execute(
            "SELECT first_name, username, total_won, xp, games_count FROM users "
            "ORDER BY total_won DESC LIMIT 10"
        ).fetchall()

def get_stats():
    with get_conn() as c:
        users  = c.execute("SELECT COUNT(*) as n FROM users").fetchone()["n"]
        deps   = c.execute("SELECT COALESCE(SUM(amount),0) as s FROM transactions WHERE type LIKE 'deposit%' AND status='confirmed'").fetchone()["s"]
        wins   = c.execute("SELECT COALESCE(SUM(amount),0) as s FROM transactions WHERE type='game_win'").fetchone()["s"]
        losses = c.execute("SELECT COALESCE(SUM(amount),0) as s FROM transactions WHERE type='game_loss'").fetchone()["s"]
        return users, deps, wins, losses

# ═══════════════════════════════════════════════════
#               🎯  ИГРОВЫЕ УТИЛИТЫ
# ═══════════════════════════════════════════════════
def roll_win() -> bool:
    return random.random() < WIN_RATE

# ─── МИНЫ 5×5 ────────────────────────────────────
def mines_new_state(bet: float, bomb_count: int) -> dict:
    mines = set(random.sample(range(25), bomb_count))
    return {
        "bet": bet, "bomb_count": bomb_count,
        "mines": list(mines), "revealed": [],
        "multiplier": 1.0, "safe_clicked": 0, "finished": False
    }

def mines_mult(safe: int, bombs: int) -> float:
    safe_cells = 25 - bombs
    if safe == 0: return 1.0
    mult = 1.0
    for i in range(safe):
        mult *= (safe_cells - i) / (25 - bombs - i)
    return round(0.97 / mult, 2)  # house edge 3%

def mines_keyboard(state: dict) -> InlineKeyboardMarkup:
    mines_set   = set(state["mines"])
    revealed    = state["revealed"]
    rows = []
    for row_i in range(5):
        row = []
        for col_i in range(5):
            idx = row_i * 5 + col_i
            if idx in revealed:
                txt = "💣" if idx in mines_set else "💎"
                row.append(InlineKeyboardButton(txt, callback_data="mines_done"))
            else:
                row.append(InlineKeyboardButton("⬜", callback_data=f"mc_{idx}"))
        rows.append(row)
    if state["safe_clicked"] > 0 and not state["finished"]:
        m = state["multiplier"]
        rows.append([InlineKeyboardButton(
            f"💰 Забрать x{m} = {state['bet'] * m:.0f} 🪙",
            callback_data="mines_cashout"
        )])
    return InlineKeyboardMarkup(rows)

def mines_board_text(state: dict) -> str:
    """Текстовое представление поля для лайв-канала."""
    mines_set = set(state["mines"])
    revealed  = state["revealed"]
    lines = []
    for r in range(5):
        line = ""
        for c in range(5):
            idx = r * 5 + c
            if idx in revealed:
                line += "💣" if idx in mines_set else "💎"
            elif not state["finished"]:
                line += "⬜"
            else:
                line += "💣" if idx in mines_set else "⬛"
        lines.append(line)
    return "\n".join(lines)

# ─── БАШНЯ ───────────────────────────────────────
TOWER_ROWS = [
    {"mult": 1.5,  "safe": 3, "total": 4},
    {"mult": 2.0,  "safe": 3, "total": 4},
    {"mult": 2.8,  "safe": 2, "total": 4},
    {"mult": 4.0,  "safe": 2, "total": 4},
    {"mult": 6.0,  "safe": 1, "total": 4},
    {"mult": 10.0, "safe": 1, "total": 4},
]

def tower_keyboard(state: dict, row_opts: list) -> InlineKeyboardMarkup:
    level = state["level"]
    row_d = TOWER_ROWS[level]
    btns = [[InlineKeyboardButton(f"{'✅' if i in row_opts else '⬜'}", callback_data=f"twrcell_{i}") for i in range(row_d["total"])]]
    cur_mult = TOWER_ROWS[level - 1]["mult"] if level > 0 else 1.0
    if level > 0:
        btns.append([InlineKeyboardButton(
            f"💰 Забрать x{cur_mult} = {state['bet'] * cur_mult:.0f} 🪙",
            callback_data="tower_cashout"
        )])
    return InlineKeyboardMarkup(btns)

# ─── ПЛИНКО ──────────────────────────────────────
PLINKO_MULTS = [10.0, 3.0, 1.5, 1.0, 0.5, 1.0, 1.5, 3.0, 10.0]

# ─── РУЛЕТКА ─────────────────────────────────────
ROULETTE_NUMS = list(range(37))  # 0-36
RED_NUMS   = {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}
BLACK_NUMS = {2,4,6,8,10,11,13,15,17,20,22,24,26,28,29,31,33,35}

def roulette_color(n: int) -> str:
    if n == 0: return "🟢"
    return "🔴" if n in RED_NUMS else "⚫"

# ─── СЛОТЫ ───────────────────────────────────────
SLOT_SYMBOLS = ["🍒","🍋","🍊","🍇","💎","7️⃣","🃏"]
SLOT_WEIGHTS  = [30, 25, 20, 15, 5, 3, 2]  # шансы

def slot_spin() -> list:
    return random.choices(SLOT_SYMBOLS, weights=SLOT_WEIGHTS, k=3)

def slot_multiplier(reels: list) -> float:
    if reels[0] == reels[1] == reels[2]:
        mults = {"🍒":2,"🍋":3,"🍊":4,"🍇":5,"💎":10,"7️⃣":15,"🃏":20}
        return mults.get(reels[0], 2)
    if reels[0] == reels[1] or reels[1] == reels[2]:
        return 1.3
    return 0

# ─── БЛЭКДЖЕК ────────────────────────────────────
DECK = (["2","3","4","5","6","7","8","9","10","J","Q","K","A"] * 4)
CARD_VALS = {"2":2,"3":3,"4":4,"5":5,"6":6,"7":7,"8":8,"9":9,"10":10,"J":10,"Q":10,"K":10,"A":11}

def card_value(hand: list) -> int:
    v = sum(CARD_VALS[c] for c in hand)
    aces = hand.count("A")
    while v > 21 and aces:
        v -= 10; aces -= 1
    return v

def deal_card(deck: list) -> str:
    return deck.pop(random.randint(0, len(deck)-1))

# ═══════════════════════════════════════════════════
#               📺  ЛАЙВ-КАНАЛ
# ═══════════════════════════════════════════════════
async def live_post(bot, user, game_name: str, bet: float, won: bool,
                    prize: float, mult: float, extra_text: str = ""):
    if not LIVE_CHANNEL_ID:
        return
    row  = get_user(user.id)
    lvl  = get_level(row["xp"] if row else 0)
    name = f"@{user.username}" if user.username else user.first_name
    outcome = "🏆 Победа!" if won else "💸 Проигрыш..."
    text = (
        f"👤 Игрок: *{name}*\n"
        f"↳ Уровень: {lvl['name']} {lvl['emoji']}\n\n"
        f"🎮 Игра: *{game_name}*\n"
        f"{'🏆' if won else '❌'} Исход: *{outcome}*\n\n"
        f"💰 Ставка: `{bet:.0f} 🪙`\n"
        f"{'↳ Выигрыш: ' + str(round(prize)) + ' 🪙 (x' + str(mult) + ')' if won else '↳ Ставка потеряна'}"
    )
    if extra_text:
        text += f"\n\n{extra_text}"
    try:
        await bot.send_message(LIVE_CHANNEL_ID, text, parse_mode="Markdown")
    except:
        pass

# ═══════════════════════════════════════════════════
#               📲  КЛАВИАТУРЫ
# ═══════════════════════════════════════════════════
def main_kb(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎰 Игры",        callback_data="menu_games"),
         InlineKeyboardButton("💰 Баланс",       callback_data="menu_balance")],
        [InlineKeyboardButton("➕ Пополнить",    callback_data="menu_deposit"),
         InlineKeyboardButton("➖ Вывод",         callback_data="menu_withdraw")],
        [InlineKeyboardButton("👥 Пригласить",   callback_data="menu_ref"),
         InlineKeyboardButton("🏆 ТОП-10",       callback_data="menu_top")],
        [InlineKeyboardButton("🎁 Промокод",     callback_data="menu_promo"),
         InlineKeyboardButton("📊 Моя статистика",callback_data="menu_stats")],
        [InlineKeyboardButton("💸 Кэшбек",       callback_data="menu_cashback"),
         InlineKeyboardButton("❓ Помощь",        callback_data="menu_help")],
    ])

GAMES_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("💣 Мины 5×5",     callback_data="game_mines"),
     InlineKeyboardButton("🗼 Башня",         callback_data="game_tower")],
    [InlineKeyboardButton("🎰 Слоты",        callback_data="game_slots"),
     InlineKeyboardButton("🪙 Орёл/Решка",   callback_data="game_coinflip")],
    [InlineKeyboardButton("🎲 Кости",        callback_data="game_dice"),
     InlineKeyboardButton("🎯 Дартс",        callback_data="game_darts")],
    [InlineKeyboardButton("🏀 Баскетбол",    callback_data="game_basketball"),
     InlineKeyboardButton("⚽ Футбол",        callback_data="game_football")],
    [InlineKeyboardButton("🎳 Боулинг",      callback_data="game_bowling"),
     InlineKeyboardButton("🟢 Плинко",       callback_data="game_plinko")],
    [InlineKeyboardButton("🃏 Блэкджек (21)",callback_data="game_blackjack")],
    [InlineKeyboardButton("🎡 Рулетка",      callback_data="game_roulette")],
    [InlineKeyboardButton("✂️ КНБ",          callback_data="game_rps")],
    [InlineKeyboardButton("◀️ Назад",        callback_data="menu_main")],
])

DEPOSIT_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("⭐ Telegram Stars", callback_data="dep_stars")],
    [InlineKeyboardButton("💵 USD (USDT/TRX)", callback_data="dep_usd")],
    [InlineKeyboardButton("◀️ Назад",          callback_data="menu_main")],
])

def back_kb(cb="menu_main"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data=cb)]])

def bet_kb(game: str) -> InlineKeyboardMarkup:
    bets = [10, 25, 50, 100, 250, 500, 1000]
    rows, row = [], []
    for b in bets:
        row.append(InlineKeyboardButton(f"{b}🪙", callback_data=f"bet_{game}_{b}"))
        if len(row) == 4: rows.append(row); row = []
    if row: rows.append(row)
    rows.append([InlineKeyboardButton("✏️ Своя ставка", callback_data=f"bet_{game}_custom")])
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="menu_games")])
    return InlineKeyboardMarkup(rows)

# ═══════════════════════════════════════════════════
#               🤖  КОМАНДЫ
# ═══════════════════════════════════════════════════
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user   = update.effective_user
    args   = ctx.args
    ref_by = None
    if args:
        try:
            ref_by = int(args[0])
            if ref_by == user.id: ref_by = None
        except: pass

    is_new = ensure_user(user.id, user.username, user.first_name, ref_by)

    # Уведомить реферера
    if is_new and ref_by:
        try:
            await ctx.bot.send_message(
                ref_by,
                f"🎉 По твоей ссылке зарегистрировался *{user.first_name}*!\n🎁 Ты получишь бонус когда он пополнит баланс.",
                parse_mode="Markdown"
            )
        except: pass

    row = get_user(user.id)
    lvl = get_level(row["xp"])
    bal = row["balance"]

    if is_new:
        text = (
            f"🎰 *Добро пожаловать в VIP Vault Casino!*\n\n"
            f"Привет, {user.first_name}!\n\n"
            f"🎁 Тебе начислено *{REG_BONUS} 🪙* как приветственный бонус!\n"
            f"🏆 Уровень: *{lvl['name']} {lvl['emoji']}*\n\n"
            f"Выбери действие:"
        )
    else:
        text = (
            f"🎰 *VIP Vault Casino*\n\n"
            f"👤 С возвращением, *{user.first_name}*!\n\n"
            f"🆔 ID: `{user.id}`\n"
            f"💰 Баланс: *{bal:.0f} 🪙*\n"
            f"🏆 Уровень: *{lvl['name']} {lvl['emoji']}*\n"
            f"🎁 Кэшбек: *+{int(lvl['cashback']*100)}%*\n\n"
            f"Выбери действие:"
        )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_kb(user.id))


async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    u, d, w, l = get_stats()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Заявки на вывод", callback_data="admin_withdrawals")],
    ])
    await update.message.reply_text(
        f"👑 *ADMIN PANEL*\n\n"
        f"👥 Пользователей: `{u}`\n"
        f"💰 Депозитов всего: `{d:.0f}`\n"
        f"🏆 Выиграно игроками: `{w:.0f}`\n"
        f"💸 Проиграно: `{l:.0f}`\n"
        f"📈 Профит казино: `{l-w:.0f}`\n\n"
        f"*Команды:*\n"
        f"`/give uid сумма` — начислить токены\n"
        f"`/newpromo КОД СУММА USES` — промокод\n"
        f"`/confirm_dep uid токены` — подтвердить USD\n"
        f"`/broadcast текст` — рассылка всем",
        parse_mode="Markdown", reply_markup=kb
    )

async def cmd_give(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try: uid = int(ctx.args[0]); amount = float(ctx.args[1])
    except:
        await update.message.reply_text("❌ /give user_id amount"); return
    if not get_user(uid):
        await update.message.reply_text("❌ Пользователь не найден."); return
    update_balance(uid, amount)
    log_tx(uid, "bonus", amount, "confirmed", "Бонус от администратора")
    await update.message.reply_text(f"✅ Начислено {amount:.0f} 🪙 → {uid}")
    try:
        await ctx.bot.send_message(uid, f"🎁 Тебе начислен бонус *{amount:.0f} 🪙* от администратора!", parse_mode="Markdown")
    except: pass

async def cmd_newpromo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try: code = ctx.args[0].upper(); amount = float(ctx.args[1]); uses = int(ctx.args[2])
    except:
        await update.message.reply_text("❌ /newpromo КОД СУММА USES"); return
    with get_conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO promo_codes (code,amount,uses_left,created_by,created_at) VALUES (?,?,?,?,?)",
            (code, amount, uses, ADMIN_ID, datetime.now().isoformat())
        )
    await update.message.reply_text(f"✅ Промокод `{code}` | {amount:.0f}🪙 | {uses} исп.", parse_mode="Markdown")

async def cmd_confirm_dep(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try: uid = int(ctx.args[0]); tokens = float(ctx.args[1])
    except:
        await update.message.reply_text("❌ /confirm_dep uid tokens"); return
    if not get_user(uid):
        await update.message.reply_text("❌ Не найден."); return
    update_balance(uid, tokens)
    with get_conn() as c:
        c.execute("UPDATE users SET total_dep=total_dep+? WHERE user_id=?", (tokens, uid))
    log_tx(uid, "deposit_usd", tokens, "confirmed", "Admin confirmed")
    await update.message.reply_text(f"✅ +{tokens:.0f}🪙 → {uid}")
    # Реферал бонус 5% рефереру
    row = get_user(uid)
    if row and row["ref_by"]:
        bonus = tokens * 0.05
        update_balance(row["ref_by"], bonus)
        log_tx(row["ref_by"], "bonus", bonus, "confirmed", f"Реферальный бонус от {uid}")
        try:
            await ctx.bot.send_message(
                row["ref_by"],
                f"🎉 Твой реферал пополнил баланс!\n💰 Ты получил *{bonus:.0f} 🪙* (5%)",
                parse_mode="Markdown"
            )
        except: pass
    try:
        await ctx.bot.send_message(uid, f"✅ Депозит *{tokens:.0f} 🪙* подтверждён!", parse_mode="Markdown")
    except: pass

async def cmd_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    text = " ".join(ctx.args)
    if not text:
        await update.message.reply_text("❌ /broadcast текст"); return
    with get_conn() as c:
        uids = [r["user_id"] for r in c.execute("SELECT user_id FROM users").fetchall()]
    sent = 0
    for uid in uids:
        try:
            await ctx.bot.send_message(uid, f"📢 *Объявление:*\n\n{text}", parse_mode="Markdown")
            sent += 1
        except: pass
    await update.message.reply_text(f"✅ Отправлено {sent}/{len(uids)}")

# ═══════════════════════════════════════════════════
#               🔘  CALLBACK HANDLER
# ═══════════════════════════════════════════════════
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    data = q.data
    user = q.from_user
    ensure_user(user.id, user.username, user.first_name)

    # ─── ГЛАВНОЕ МЕНЮ ──────────────────────────────
    if data == "menu_main":
        row = get_user(user.id)
        lvl = get_level(row["xp"])
        await q.edit_message_text(
            f"🎰 *VIP Vault Casino*\n\n"
            f"🆔 ID: `{user.id}`\n"
            f"💰 Баланс: *{row['balance']:.0f} 🪙*\n"
            f"🎮 Сыграно: `{row['games_count']}`\n"
            f"📊 Оборот: `{row['total_turnover']:.0f} 🪙`\n\n"
            f"🏆 Уровень: *{lvl['name']} {lvl['emoji']}*\n"
            f"▏{xp_bar(row['xp'])}▏\n"
            f"🎁 Кэшбек: *+{int(lvl['cashback']*100)}%*",
            parse_mode="Markdown", reply_markup=main_kb(user.id)
        )

    elif data == "menu_games":
        await q.edit_message_text("🎮 *Выбери игру:*", parse_mode="Markdown", reply_markup=GAMES_KB)

    elif data == "menu_balance":
        row = get_user(user.id)
        lvl = get_level(row["xp"])
        await q.edit_message_text(
            f"💰 *Твой баланс*\n\n"
            f"🪙 Баланс: `{row['balance']:.0f}`\n"
            f"📥 Пополнено: `{row['total_dep']:.0f}`\n"
            f"🏆 Выиграно: `{row['total_won']:.0f}`\n"
            f"💸 Проиграно: `{row['total_lost']:.0f}`\n"
            f"📊 Оборот: `{row['total_turnover']:.0f}`\n"
            f"🎮 Игр: `{row['games_count']}`\n\n"
            f"🏆 *{lvl['name']} {lvl['emoji']}*\n"
            f"▏{xp_bar(row['xp'])}▏\n"
            f"🎁 Кэшбек: *+{int(lvl['cashback']*100)}%*",
            parse_mode="Markdown", reply_markup=back_kb()
        )

    elif data == "menu_top":
        rows = get_top10()
        medals = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
        lines = ["🏆 *ТОП-10 по выигрышам*\n"]
        for i, r in enumerate(rows):
            nm  = f"@{r['username']}" if r["username"] else r["first_name"]
            lvl = get_level(r["xp"])
            lines.append(f"{medals[i]} *{nm}* {lvl['emoji']}\n   💰 {r['total_won']:.0f} 🪙 | 🎮 {r['games_count']} игр")
        await q.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=back_kb())

    elif data == "menu_ref":
        row = get_user(user.id)
        link = f"https://t.me/{ctx.bot.username}?start={user.id}"
        with get_conn() as c:
            refs = c.execute("SELECT COUNT(*) as n FROM users WHERE ref_by=?", (user.id,)).fetchone()["n"]
        await q.edit_message_text(
            f"👥 *Реферальная система*\n\n"
            f"Приглашай друзей — получай *5%* от их депозитов!\n\n"
            f"👤 Приглашено: `{refs}`\n\n"
            f"🔗 Твоя ссылка:\n`{link}`",
            parse_mode="Markdown", reply_markup=back_kb()
        )

    elif data == "menu_cashback":
        row = get_user(user.id)
        lvl = get_level(row["xp"])
        cb  = row["cashback_accum"]
        await q.edit_message_text(
            f"💸 *Кэшбек*\n\n"
            f"Твой уровень: *{lvl['name']} {lvl['emoji']}*\n"
            f"Кэшбек: *{int(lvl['cashback']*100)}% от проигрышей*\n\n"
            f"💰 Накоплено: *{cb:.0f} 🪙*\n\n"
            f"{'Нажми забрать!' if cb >= 1 else 'Играй чтобы накопить кэшбек!'}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"💰 Забрать {cb:.0f} 🪙", callback_data="cashback_claim")] if cb >= 1 else
                [InlineKeyboardButton("❌ Пока нечего забирать", callback_data="menu_cashback")],
                [InlineKeyboardButton("◀️ Назад", callback_data="menu_main")]
            ])
        )

    elif data == "cashback_claim":
        row = get_user(user.id)
        cb  = row["cashback_accum"]
        if cb < 1:
            await q.edit_message_text("❌ Нечего забирать.", reply_markup=back_kb("menu_cashback"))
            return
        with get_conn() as c:
            c.execute("UPDATE users SET cashback_accum=0 WHERE user_id=?", (user.id,))
            update_balance(user.id, cb, c)
            log_tx(user.id, "bonus", cb, "confirmed", "Кэшбек", c)
        await q.edit_message_text(
            f"✅ Кэшбек *{cb:.0f} 🪙* зачислен на баланс!",
            parse_mode="Markdown", reply_markup=back_kb()
        )

    elif data == "menu_promo":
        ctx.user_data["action"] = "promo"
        await q.edit_message_text("🎁 Введи промокод:", reply_markup=back_kb())

    elif data == "menu_stats":
        row = get_user(user.id)
        wc  = 0
        if row["games_count"] > 0:
            with get_conn() as c:
                wc = c.execute("SELECT COUNT(*) as n FROM transactions WHERE user_id=? AND type='game_win'", (user.id,)).fetchone()["n"]
            wr = wc / row["games_count"] * 100
        else:
            wr = 0
        lvl = get_level(row["xp"])
        await q.edit_message_text(
            f"📊 *Твоя статистика*\n\n"
            f"🆔 ID: `{user.id}`\n"
            f"💰 Баланс: `{row['balance']:.0f} 🪙`\n"
            f"🎮 Игр: `{row['games_count']}`\n"
            f"📈 Побед: `{wr:.1f}%`\n"
            f"🏆 Выиграно: `{row['total_won']:.0f} 🪙`\n"
            f"💸 Проиграно: `{row['total_lost']:.0f} 🪙`\n"
            f"📊 Оборот: `{row['total_turnover']:.0f} 🪙`\n\n"
            f"🏆 *{lvl['name']} {lvl['emoji']}*\n"
            f"▏{xp_bar(row['xp'])}▏\n"
            f"🎁 Кэшбек: +{int(lvl['cashback']*100)}%",
            parse_mode="Markdown", reply_markup=back_kb()
        )

    elif data == "menu_help":
        await q.edit_message_text(
            "❓ *Помощь*\n\n"
            "🎮 *Игры:*\n"
            "• 💣 Мины 5×5 — ищи алмазы x до 8.99\n"
            "• 🗼 Башня — поднимайся, риск растёт\n"
            "• 🎰 Слоты — совпади 3 символа\n"
            "• 🎡 Рулетка — цвет/число/чётность\n"
            "• 🃏 Блэкджек — 21, x2\n"
            "• 🟢 Плинко — шарик падает x0.5-x10\n"
            "• 🪙 Орёл/Решка — x1.9\n"
            "• 🎲 Кости — x4.5\n"
            "• ✂️ КНБ — x1.9\n"
            "• 🎯🏀⚽🎳 — мгновенные игры\n\n"
            "💰 *Пополнение:*\n"
            f"• ⭐ Stars: 1 Star = {STARS_TO_TOKENS} 🪙\n"
            f"• 💵 USD: 1$ = {USD_TO_TOKENS} 🪙\n\n"
            "🏆 *Уровни и кэшбек:*\n"
            "• Новичок 🌱 — 0%\n• Рекрут 🤠 — 1%\n"
            "• Игрок 🎮 — 2%\n• Ветеран ⚔️ — 3%\n"
            "• Патрон 🎩 — 5%\n• Смотрящий 👁 — 7%\n"
            "• Легенда 👑 — 10%\n\n"
            "📞 Поддержка: @admin",
            parse_mode="Markdown", reply_markup=back_kb()
        )

    elif data == "menu_deposit":
        await q.edit_message_text(
            "➕ *Пополнение баланса*\n\nВыбери способ:",
            parse_mode="Markdown", reply_markup=DEPOSIT_KB
        )

    elif data == "menu_withdraw":
        ctx.user_data["action"] = "withdraw"
        bal = get_balance(user.id)
        await q.edit_message_text(
            f"➖ *Вывод средств*\n\n💰 Баланс: `{bal:.0f} 🪙`\n"
            f"Минимум: `{MIN_WITHDRAW} 🪙`\n\n"
            f"Введи: `сумма кошелёк`\nПример: `500 TRXwallet`",
            parse_mode="Markdown", reply_markup=back_kb()
        )

    # ─── ДЕПОЗИТ Stars ─────────────────────────────
    elif data == "dep_stars":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"⭐ 50 → {50*STARS_TO_TOKENS}🪙",   callback_data="stars_50")],
            [InlineKeyboardButton(f"⭐ 100 → {100*STARS_TO_TOKENS}🪙", callback_data="stars_100")],
            [InlineKeyboardButton(f"⭐ 250 → {250*STARS_TO_TOKENS}🪙", callback_data="stars_250")],
            [InlineKeyboardButton(f"⭐ 500 → {500*STARS_TO_TOKENS}🪙", callback_data="stars_500")],
            [InlineKeyboardButton(f"⭐ 1000 → {1000*STARS_TO_TOKENS}🪙",callback_data="stars_1000")],
            [InlineKeyboardButton("◀️ Назад", callback_data="menu_deposit")],
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
        await q.edit_message_text("💳 Счёт отправлен! Оплати в чате.", reply_markup=back_kb("menu_deposit"))

    elif data == "dep_usd":
        ctx.user_data["action"] = "deposit_usd"
        await q.edit_message_text(
            f"💵 *Пополнение через USD*\n\n"
            f"Отправь на кошелёк:\n`{USD_WALLET}`\n\n"
            f"Курс: 1 USD = {USD_TO_TOKENS} 🪙\n\n"
            f"Затем напиши: `txid сумма_usd`\nПример: `abc123 10`",
            parse_mode="Markdown", reply_markup=back_kb("menu_deposit")
        )

    # ─── ВЫБОР ИГРЫ → СТАВКА ───────────────────────
    elif data.startswith("game_"):
        game = data[5:]
        descs = {
            "mines":      ("💣 Мины 5×5",     "Выбирай бомбы. Множитель до x8+"),
            "tower":      ("🗼 Башня",          "6 уровней, риск растёт с каждым"),
            "slots":      ("🎰 Слоты",         "3 барабана. Совпади — выиграй!"),
            "coinflip":   ("🪙 Орёл/Решка",    "Классика. x1.9"),
            "dice":       ("🎲 Кости",         "Угадай число 1-6. x4.5"),
            "darts":      ("🎯 Дартс",         "Попади в цель. x2.5"),
            "basketball": ("🏀 Баскетбол",     "Забрось мяч. x2.2"),
            "football":   ("⚽ Футбол",         "Забей гол. x2.0"),
            "bowling":    ("🎳 Боулинг",       "Выбей кегли. x2.3"),
            "plinko":     ("🟢 Плинко",        "Шарик падает вниз. x0.5 до x10"),
            "blackjack":  ("🃏 Блэкджек (21)", "Набери 21. x2.0"),
            "roulette":   ("🎡 Рулетка",       "Красное x1.9 | Зелёное x14 | Число x35"),
            "rps":        ("✂️ КНБ",           "Камень, Ножницы, Бумага. x1.9"),
        }
        name, desc = descs.get(game, (game, ""))
        bal = get_balance(user.id)
        await q.edit_message_text(
            f"{name}\n\n📌 {desc}\n\n💰 Баланс: `{bal:.0f} 🪙`\nВыбери ставку:",
            parse_mode="Markdown", reply_markup=bet_kb(game)
        )

    # ─── СТАВКА ВЫБРАНА ───────────────────────────
    elif data.startswith("bet_"):
        parts = data.split("_")
        game  = parts[1]
        val   = parts[2]
        if val == "custom":
            ctx.user_data["action"] = "custom_bet"
            ctx.user_data["game"]   = game
            await q.edit_message_text(
                f"✏️ Введи ставку ({MIN_BET}-{MAX_BET} 🪙):",
                reply_markup=back_kb(f"game_{game}")
            )
            return
        await _start_game(q, ctx, user, game, float(val))

    # ─── МИНЫ — клик ──────────────────────────────
    elif data.startswith("mc_"):
        idx = int(data[3:])
        ag  = _get_ag(user.id, "mines")
        if not ag: return
        st  = json.loads(ag["state"])
        if st["finished"] or idx in st["revealed"]: return
        st["revealed"].append(idx)
        if idx in st["mines"]:
            # reveal all mines
            for m in st["mines"]:
                if m not in st["revealed"]: st["revealed"].append(m)
            st["finished"] = True
            _save_ag(user.id, "mines", st)
            await _finish_game(q, ctx, user, "mines", st["bet"], False, st)
        else:
            st["safe_clicked"] += 1
            st["multiplier"] = mines_mult(st["safe_clicked"], st["bomb_count"])
            _save_ag(user.id, "mines", st)
            prize = st["bet"] * st["multiplier"]
            await q.edit_message_text(
                f"💣 *Мины* | ✅ Безопасно! (ходов: {st['safe_clicked']})\n"
                f"Множитель: *x{st['multiplier']}*\n"
                f"Выигрыш: *{prize:.0f} 🪙*\n\nПродолжай или забери!",
                parse_mode="Markdown", reply_markup=mines_keyboard(st)
            )

    elif data == "mines_cashout":
        ag = _get_ag(user.id, "mines")
        if not ag: return
        st = json.loads(ag["state"])
        if st["finished"] or st["safe_clicked"] == 0: return
        st["finished"] = True
        _save_ag(user.id, "mines", st)
        await _finish_game(q, ctx, user, "mines", st["bet"], True, st)

    elif data == "mines_done":
        pass

    # ─── МИНЫ — выбор кол-ва бомб ─────────────────
    elif data.startswith("mines_bombs_"):
        bc  = int(data.split("_")[2])
        ctx.user_data["mines_bombs"] = bc
        bet = ctx.user_data.get("pending_bet", 0)
        await _launch_mines(q, ctx, user, bet, bc)

    # ─── БАШНЯ ────────────────────────────────────
    elif data.startswith("twrcell_"):
        cell = int(data[8:])
        ag   = _get_ag(user.id, "tower")
        if not ag: return
        st   = json.loads(ag["state"])
        if st["finished"]: return
        level    = st["level"]
        row_data = TOWER_ROWS[level]
        # Спрятать бомбы для этого ряда
        key = f"row_{level}_mines"
        if key not in st:
            bombs_count = row_data["total"] - row_data["safe"]
            st[key] = random.sample(range(row_data["total"]), bombs_count)
        if cell in st[key]:
            st["finished"] = True
            _save_ag(user.id, "tower", st)
            await _finish_game(q, ctx, user, "tower", st["bet"], False, st)
        else:
            st["level"] += 1
            if st["level"] >= len(TOWER_ROWS):
                st["finished"] = True
                _save_ag(user.id, "tower", st)
                await _finish_game(q, ctx, user, "tower", st["bet"], True, st)
            else:
                new_row = TOWER_ROWS[st["level"]]
                safe_cells = random.sample(range(new_row["total"]),
                                           new_row["total"] - (new_row["total"] - new_row["safe"]))
                _save_ag(user.id, "tower", st)
                cur_mult = TOWER_ROWS[st["level"] - 1]["mult"]
                nxt_mult = TOWER_ROWS[st["level"]]["mult"]
                await q.edit_message_text(
                    f"🗼 *Башня* — уровень {st['level']}/{len(TOWER_ROWS)}\n\n"
                    f"✅ Безопасно! Текущий: *x{cur_mult}*\n"
                    f"Следующий: *x{nxt_mult}*\n\n"
                    f"Выбери клетку или забери:",
                    parse_mode="Markdown",
                    reply_markup=tower_keyboard(st, safe_cells)
                )

    elif data == "tower_cashout":
        ag = _get_ag(user.id, "tower")
        if not ag: return
        st = json.loads(ag["state"])
        if st["finished"] or st["level"] == 0: return
        st["finished"] = True
        _save_ag(user.id, "tower", st)
        await _finish_game(q, ctx, user, "tower", st["bet"], True, st)

    # ─── ОРЁЛ/РЕШКА ───────────────────────────────
    elif data in ("cf_heads", "cf_tails"):
        ag = _get_ag(user.id, "coinflip")
        if not ag: return
        st = json.loads(ag["state"])
        choice = "heads" if data == "cf_heads" else "tails"
        won    = roll_win()
        result = choice if won else ("tails" if choice == "heads" else "heads")
        emoji  = "🦅 Орёл" if result == "heads" else "🪙 Решка"
        st["finished"] = True
        _save_ag(user.id, "coinflip", st)
        await _finish_game(q, ctx, user, "coinflip", st["bet"], won,
                           {"result_text": emoji, **st})

    # ─── КОСТИ ────────────────────────────────────
    elif data.startswith("dice_pick_"):
        face   = int(data.split("_")[2])
        ag     = _get_ag(user.id, "dice")
        if not ag: return
        st     = json.loads(ag["state"])
        won    = roll_win()
        result = face if won else random.choice([x for x in range(1,7) if x != face])
        DICE_EMOJI = {1:"1️⃣",2:"2️⃣",3:"3️⃣",4:"4️⃣",5:"5️⃣",6:"6️⃣"}
        st["finished"] = True
        _save_ag(user.id, "dice", st)
        await _finish_game(q, ctx, user, "dice", st["bet"], won,
                           {"dice_face": result, "picked": face, "dice_emoji": DICE_EMOJI.get(result, str(result)), **st})

    # ─── РУЛЕТКА — выбор ──────────────────────────
    elif data.startswith("rul_"):
        choice = data[4:]   # "red","black","green","even","odd","1-12","13-24","25-36"
        ag = _get_ag(user.id, "roulette")
        if not ag: return
        st = json.loads(ag["state"])
        num = random.randint(0, 36)
        col = roulette_color(num)
        # Определяем выигрыш
        won   = False
        mult  = 1.0
        if choice == "red"   and num in RED_NUMS:         won = True; mult = 1.9
        elif choice == "black" and num in BLACK_NUMS:     won = True; mult = 1.9
        elif choice == "green" and num == 0:              won = True; mult = 14.0
        elif choice == "even" and num > 0 and num % 2==0: won = True; mult = 1.9
        elif choice == "odd"  and num % 2 == 1:          won = True; mult = 1.9
        elif choice == "1-12"  and 1 <= num <= 12:        won = True; mult = 2.9
        elif choice == "13-24" and 13 <= num <= 24:       won = True; mult = 2.9
        elif choice == "25-36" and 25 <= num <= 36:       won = True; mult = 2.9
        elif choice == str(num):                           won = True; mult = 35.0
        # override with win rate
        if not won and roll_win():
            # forced win — but only for low multipliers
            if mult <= 2.0:
                won = True
        st["finished"] = True
        _save_ag(user.id, "roulette", st)
        await _finish_game(q, ctx, user, "roulette", st["bet"], won,
                           {"num": num, "col": col, "mult": mult, "choice": choice, **st})

    # ─── БЛЭКДЖЕК ─────────────────────────────────
    elif data == "bj_hit":
        ag = _get_ag(user.id, "blackjack")
        if not ag: return
        st = json.loads(ag["state"])
        if st["finished"]: return
        new_card = deal_card(st["deck"])
        st["player"].append(new_card)
        pv = card_value(st["player"])
        if pv > 21:
            st["finished"] = True
            _save_ag(user.id, "blackjack", st)
            await _finish_game(q, ctx, user, "blackjack", st["bet"], False, st)
        else:
            _save_ag(user.id, "blackjack", st)
            dv = card_value([st["dealer"][0]])
            await q.edit_message_text(
                f"🃏 *Блэкджек*\n\n"
                f"Ты: *{' '.join(st['player'])}* = `{pv}`\n"
                f"Дилер: `{st['dealer'][0]}` + 🂠\n\n"
                f"Ставка: `{st['bet']:.0f} 🪙`",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🃏 Ещё", callback_data="bj_hit"),
                    InlineKeyboardButton("🛑 Стоп", callback_data="bj_stand")
                ]])
            )

    elif data == "bj_stand":
        ag = _get_ag(user.id, "blackjack")
        if not ag: return
        st = json.loads(ag["state"])
        if st["finished"]: return
        # Дилер добирает до 17
        while card_value(st["dealer"]) < 17:
            st["dealer"].append(deal_card(st["deck"]))
        pv = card_value(st["player"])
        dv = card_value(st["dealer"])
        if dv > 21 or pv > dv:
            won = roll_win()  # still apply win rate
            if not won: won = True  # blackjack stand is more skill-based, let them win if rules say so
        elif pv == dv:
            won = False  # push = loss for simplicity
        else:
            won = False
        st["finished"] = True
        _save_ag(user.id, "blackjack", st)
        await _finish_game(q, ctx, user, "blackjack", st["bet"], won, st)

    # ─── КНБ — выбор ──────────────────────────────
    elif data.startswith("rps_"):
        choice = data[4:]
        ag = _get_ag(user.id, "rps")
        if not ag: return
        st = json.loads(ag["state"])
        options = ["rock", "scissors", "paper"]
        emojis  = {"rock":"🪨","scissors":"✂️","paper":"📄"}
        wins_vs = {"rock":"scissors","scissors":"paper","paper":"rock"}
        bot_pick = random.choice(options)
        if roll_win():
            bot_pick = wins_vs[choice]  # player wins
            won = True
        else:
            bot_pick = wins_vs.get({"rock":"scissors","scissors":"paper","paper":"rock"}.get(choice,"rock"), "rock")
            won = False
        st["finished"] = True
        _save_ag(user.id, "rps", st)
        await _finish_game(q, ctx, user, "rps", st["bet"], won,
                           {"player_pick": emojis[choice], "bot_pick": emojis[bot_pick], **st})

    # ─── ADMIN ────────────────────────────────────
    elif data == "admin_withdrawals":
        if user.id != ADMIN_ID: return
        with get_conn() as c:
            rows = c.execute(
                "SELECT t.*, u.username, u.first_name FROM transactions t "
                "JOIN users u ON t.user_id=u.user_id "
                "WHERE t.type='withdraw' AND t.status='pending'"
            ).fetchall()
        if not rows:
            await q.edit_message_text("✅ Нет заявок.", reply_markup=back_kb()); return
        await q.edit_message_text(f"💸 Заявок: {len(rows)}. Отправляю...", reply_markup=back_kb())
        for r in rows:
            kb2 = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ ОК", callback_data=f"awd_ok_{r['id']}"),
                InlineKeyboardButton("❌ НЕТ",callback_data=f"awd_no_{r['id']}")
            ]])
            await ctx.bot.send_message(
                ADMIN_ID,
                f"💸 *Вывод #{r['id']}*\n"
                f"👤 @{r['username'] or r['first_name']} (`{r['user_id']}`)\n"
                f"💰 `{r['amount']:.0f} 🪙`\n💳 `{r['note']}`",
                parse_mode="Markdown", reply_markup=kb2
            )

    elif data.startswith("awd_ok_"):
        if user.id != ADMIN_ID: return
        tx_id = int(data[7:])
        with get_conn() as c:
            r = c.execute("SELECT * FROM transactions WHERE id=?", (tx_id,)).fetchone()
            if r and r["status"] == "pending":
                c.execute("UPDATE transactions SET status='confirmed' WHERE id=?", (tx_id,))
        await q.edit_message_text(f"✅ #{tx_id} подтверждён.")
        try: await ctx.bot.send_message(r["user_id"], f"✅ Вывод *{r['amount']:.0f} 🪙* одобрен!", parse_mode="Markdown")
        except: pass

    elif data.startswith("awd_no_"):
        if user.id != ADMIN_ID: return
        tx_id = int(data[7:])
        with get_conn() as c:
            r = c.execute("SELECT * FROM transactions WHERE id=?", (tx_id,)).fetchone()
            if r and r["status"] == "pending":
                c.execute("UPDATE transactions SET status='rejected' WHERE id=?", (tx_id,))
                update_balance(r["user_id"], r["amount"], c)
        await q.edit_message_text(f"❌ #{tx_id} отклонён.")
        try: await ctx.bot.send_message(r["user_id"], f"❌ Вывод *{r['amount']:.0f} 🪙* отклонён. Средства возвращены.", parse_mode="Markdown")
        except: pass


# ═══════════════════════════════════════════════════
#              🎮  ЗАПУСК ИГР
# ═══════════════════════════════════════════════════
async def _start_game(q, ctx, user, game: str, bet: float):
    if not (MIN_BET <= bet <= MAX_BET):
        await q.edit_message_text(f"❌ Ставка: {MIN_BET}-{MAX_BET} 🪙", reply_markup=back_kb(f"game_{game}"))
        return
    bal = get_balance(user.id)
    if bal < bet:
        await q.edit_message_text(
            f"❌ Недостаточно!\n💰 Баланс: `{bal:.0f}`\nНужно: `{bet:.0f}`",
            parse_mode="Markdown", reply_markup=back_kb("menu_deposit")
        )
        return

    update_balance(user.id, -bet)

    if game == "mines":
        ctx.user_data["pending_bet"] = bet
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💣 1 бомба",  callback_data="mines_bombs_1"),
             InlineKeyboardButton("💣 3 бомбы",  callback_data="mines_bombs_3")],
            [InlineKeyboardButton("💣 5 бомб",   callback_data="mines_bombs_5"),
             InlineKeyboardButton("💣 10 бомб",  callback_data="mines_bombs_10")],
            [InlineKeyboardButton("◀️ Назад", callback_data="menu_games")]
        ])
        await q.edit_message_text(
            f"💣 *Мины 5×5*\n\nСтавка: `{bet:.0f} 🪙`\nСколько бомб спрятать?",
            parse_mode="Markdown", reply_markup=kb
        )

    elif game == "tower":
        st = {"bet": bet, "level": 0, "finished": False}
        _save_ag(user.id, "tower", st)
        row_opts = random.sample(range(4), 3)
        await q.edit_message_text(
            f"🗼 *Башня* (6 уровней)\n\nСтавка: `{bet:.0f} 🪙`\n"
            f"На вершине: *x{TOWER_ROWS[-1]['mult']}*\n\nВыбери клетку:",
            parse_mode="Markdown", reply_markup=tower_keyboard(st, row_opts)
        )

    elif game == "slots":
        st = {"bet": bet, "finished": True}
        _save_ag(user.id, "slots", st)
        reels = slot_spin()
        mult  = slot_multiplier(reels)
        won   = mult > 0
        if not won and roll_win():  # force win with low multiplier
            reels = [reels[0], reels[0], reels[0]]
            mult  = slot_multiplier(reels)
            won   = True
        elif won and not roll_win():
            reels = slot_spin()
            while slot_multiplier(reels) > 0:
                reels = slot_spin()
            mult = 0; won = False
        await _finish_game(q, ctx, user, "slots", bet, won,
                           {"reels": reels, "mult": mult})

    elif game == "coinflip":
        st = {"bet": bet, "finished": False}
        _save_ag(user.id, "coinflip", st)
        await q.edit_message_text(
            f"🪙 *Орёл или Решка?*\n\nСтавка: `{bet:.0f} 🪙` | x1.9",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🦅 Орёл", callback_data="cf_heads"),
                InlineKeyboardButton("🪙 Решка", callback_data="cf_tails")
            ]])
        )

    elif game == "dice":
        st = {"bet": bet, "finished": False}
        _save_ag(user.id, "dice", st)
        faces = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣"]
        btns  = [
            [InlineKeyboardButton(faces[i], callback_data=f"dice_pick_{i+1}") for i in range(3)],
            [InlineKeyboardButton(faces[i], callback_data=f"dice_pick_{i+1}") for i in range(3, 6)]
        ]
        await q.edit_message_text(
            f"🎲 *Кости*\n\nСтавка: `{bet:.0f} 🪙` | x4.5\n\nВыбери число:",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns)
        )

    elif game == "roulette":
        st = {"bet": bet, "finished": False}
        _save_ag(user.id, "roulette", st)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔴 Красное x1.9", callback_data="rul_red"),
             InlineKeyboardButton("⚫ Чёрное x1.9",  callback_data="rul_black")],
            [InlineKeyboardButton("🟢 Зелёное x14",  callback_data="rul_green")],
            [InlineKeyboardButton("👆 Чётное x1.9",  callback_data="rul_even"),
             InlineKeyboardButton("☝️ Нечётное x1.9",callback_data="rul_odd")],
            [InlineKeyboardButton("1-12 x2.9",       callback_data="rul_1-12"),
             InlineKeyboardButton("13-24 x2.9",      callback_data="rul_13-24"),
             InlineKeyboardButton("25-36 x2.9",      callback_data="rul_25-36")],
            [InlineKeyboardButton("◀️ Назад", callback_data="game_roulette")]
        ])
        await q.edit_message_text(
            f"🎡 *Рулетка*\n\nСтавка: `{bet:.0f} 🪙`\n\nВыбери ставку:",
            parse_mode="Markdown", reply_markup=kb
        )

    elif game == "blackjack":
        deck = list(DECK)
        random.shuffle(deck)
        player = [deal_card(deck), deal_card(deck)]
        dealer = [deal_card(deck), deal_card(deck)]
        pv = card_value(player)
        st = {"bet": bet, "deck": deck, "player": player, "dealer": dealer, "finished": False}
        _save_ag(user.id, "blackjack", st)
        if pv == 21:
            st["finished"] = True
            _save_ag(user.id, "blackjack", st)
            await _finish_game(q, ctx, user, "blackjack", bet, True, st)
            return
        dv = card_value([dealer[0]])
        await q.edit_message_text(
            f"🃏 *Блэкджек*\n\n"
            f"Ты: *{' '.join(player)}* = `{pv}`\n"
            f"Дилер: `{dealer[0]}` + 🂠\n\n"
            f"Ставка: `{bet:.0f} 🪙`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🃏 Ещё", callback_data="bj_hit"),
                InlineKeyboardButton("🛑 Стоп", callback_data="bj_stand")
            ]])
        )

    elif game == "rps":
        st = {"bet": bet, "finished": False}
        _save_ag(user.id, "rps", st)
        await q.edit_message_text(
            f"✂️ *КНБ* (Камень Ножницы Бумага)\n\nСтавка: `{bet:.0f} 🪙` | x1.9\n\nВыбирай:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🪨 Камень",   callback_data="rps_rock"),
                InlineKeyboardButton("✂️ Ножницы",  callback_data="rps_scissors"),
                InlineKeyboardButton("📄 Бумага",   callback_data="rps_paper")
            ]])
        )

    elif game == "plinko":
        # Шарик падает, результат случайный
        slot  = random.choices(range(9), weights=[1,3,7,12,15,12,7,3,1], k=1)[0]
        mult  = PLINKO_MULTS[slot]
        won   = mult > 1.0
        if not won and roll_win():
            slot = random.choice([0, 1, 7, 8])
            mult = PLINKO_MULTS[slot]
            won  = True
        elif won and not roll_win():
            slot = 4; mult = 0.5; won = False
        path = "🟢" + "".join(["↘️" if random.random() > 0.5 else "↙️" for _ in range(7)])
        st = {"bet": bet, "finished": True}
        _save_ag(user.id, "plinko", st)
        await _finish_game(q, ctx, user, "plinko", bet, won,
                           {"plinko_path": path, "plinko_slot": slot, "mult": mult})

    else:
        gd = {
            "darts":      ("🎯","Дартс",     "в яблочко!", "мимо цели",   2.5),
            "basketball": ("🏀","Баскетбол","Попал!",      "мимо кольца", 2.2),
            "football":   ("⚽","Футбол",    "ГОЛ!",        "мимо ворот",  2.0),
            "bowling":    ("🎳","Боулинг",   "СТРАЙК!",     "неудача",     2.3),
        }
        em, nm, wt, lt, mult = gd.get(game, ("🎮","Игра","Победа","Проигрыш",2.0))
        won = roll_win()
        st  = {"bet": bet, "finished": True}
        _save_ag(user.id, game, st)
        await _finish_game(q, ctx, user, game, bet, won,
                           {"emoji": em, "name": nm, "win_txt": wt, "lose_txt": lt, "mult": mult})


async def _launch_mines(q, ctx, user, bet: float, bomb_count: int):
    st = mines_new_state(bet, bomb_count)
    _save_ag(user.id, "mines", st)
    await q.edit_message_text(
        f"💣 *Мины 5×5*\n\n"
        f"Ставка: `{bet:.0f} 🪙` | Бомб: {bomb_count}\n"
        f"Начальный множитель: x1.0\n\n"
        f"Нажимай клетки, ищи алмазы!",
        parse_mode="Markdown", reply_markup=mines_keyboard(st)
    )


async def _finish_game(q, ctx, user, game: str, bet: float, won: bool, extra: dict = None):
    _del_ag(user.id, game)
    extra   = extra or {}
    prize   = 0
    mult    = 1.0
    details = ""

    if game == "mines":
        mult    = extra.get("multiplier", 1.0)
        prize   = bet * mult if won else 0
        details = f"💎 Забрал выигрыш! x{mult}" if won else "💥 ВЗРЫВ! Попал на бомбу"
        if not won:
            details += f"\n\n{mines_board_text(extra)}"

    elif game == "tower":
        level = extra.get("level", 0)
        mult  = TOWER_ROWS[level-1]["mult"] if level > 0 else 1.0
        prize = bet * mult if won else 0
        details = f"🏆 Уровень {level}/{len(TOWER_ROWS)}! x{mult}" if won else "💥 Упал с башни!"

    elif game == "coinflip":
        mult  = 1.9; prize = bet * mult if won else 0
        details = extra.get("result_text","")

    elif game == "dice":
        mult  = 4.5; prize = bet * mult if won else 0
        de    = extra.get("dice_emoji","?")
        details = f"🎲 Выпало: {de} | Выбрал: {extra.get('picked')}"

    elif game == "slots":
        reels = extra.get("reels", ["?","?","?"])
        mult  = extra.get("mult", 0)
        prize = bet * mult if won else 0
        details = f"🎰 {' '.join(reels)}"
        if won: details += f" — x{mult}!"
        else: details += " — не совпало"

    elif game == "roulette":
        mult    = extra.get("mult", 1.9)
        prize   = bet * mult if won else 0
        num     = extra.get("num", 0)
        col     = extra.get("col","?")
        details = f"🎡 Шарик: {col} *{num}*"

    elif game == "blackjack":
        mult    = 2.0; prize = bet * mult if won else 0
        pv = card_value(extra.get("player",[]))
        dv = card_value(extra.get("dealer",[]))
        details = (
            f"Ты: *{' '.join(extra.get('player',[]))}* = `{pv}`\n"
            f"Дилер: *{' '.join(extra.get('dealer',[]))}* = `{dv}`"
        )

    elif game == "plinko":
        mult    = extra.get("mult", 0.5)
        prize   = bet * mult if won else 0
        details = f"{extra.get('plinko_path','')}\n🟢 Слот: {extra.get('plinko_slot',0)+1} → x{mult}"

    elif game == "rps":
        mult    = 1.9; prize = bet * mult if won else 0
        details = (f"Ты: {extra.get('player_pick','?')} vs Бот: {extra.get('bot_pick','?')}")

    else:
        mult    = extra.get("mult", 2.0)
        prize   = bet * mult if won else 0
        em      = extra.get("emoji","🎮")
        details = f"{em} {extra.get('win_txt','Победа!')}" if won else f"{em} {extra.get('lose_txt','Проигрыш')}"

    # Обновляем статистику
    row = get_user(user.id)
    lvl_now = get_level(row["xp"]) if row else LEVELS[0]

    with get_conn() as c:
        if won:
            update_balance(user.id, prize, c)
            c.execute("UPDATE users SET total_won=total_won+?, games_count=games_count+1 WHERE user_id=?",
                      (prize, user.id))
            log_tx(user.id, "game_win", prize, "confirmed", f"{game} +{prize:.0f}", c)
        else:
            # Кэшбек
            cb_rate   = lvl_now["cashback"]
            cb_amount = bet * cb_rate
            c.execute(
                "UPDATE users SET total_lost=total_lost+?, games_count=games_count+1, "
                "cashback_accum=cashback_accum+? WHERE user_id=?",
                (bet, cb_amount, user.id)
            )
            log_tx(user.id, "game_loss", bet, "confirmed", f"{game} -{bet:.0f}", c)
        add_xp(user.id, bet, c)

    # Проверка апгрейда уровня
    row_new = get_user(user.id)
    lvl_new = get_level(row_new["xp"])
    if lvl_new["min_xp"] > lvl_now["min_xp"]:
        try:
            await ctx.bot.send_message(
                user.id,
                f"🎉 *Новый уровень!*\n\n"
                f"Ты достиг уровня: *{lvl_new['name']} {lvl_new['emoji']}*\n"
                f"🎁 Кэшбек увеличен до *{int(lvl_new['cashback']*100)}%*",
                parse_mode="Markdown"
            )
        except: pass

    bal = get_balance(user.id)
    lvl = get_level(row_new["xp"])

    if won:
        header = f"🏆 *ПОБЕДА!*\n\n{details}\n\n💰 +`{prize:.0f} 🪙` (x{mult})"
    else:
        cb_gain = bet * lvl["cashback"]
        header  = f"💸 *ПОРАЖЕНИЕ*\n\n{details}\n\n❌ Ставка `{bet:.0f} 🪙` проиграна"
        if cb_gain > 0:
            header += f"\n💸 Кэшбек: +{cb_gain:.1f} 🪙 накоплено"

    game_names = {
        "mines":"💣 Мины","tower":"🗼 Башня","slots":"🎰 Слоты",
        "coinflip":"🪙 Орёл/Решка","dice":"🎲 Кости","darts":"🎯 Дартс",
        "basketball":"🏀 Баскетбол","football":"⚽ Футбол","bowling":"🎳 Боулинг",
        "plinko":"🟢 Плинко","blackjack":"🃏 Блэкджек","roulette":"🎡 Рулетка","rps":"✂️ КНБ"
    }

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Ещё раз", callback_data=f"game_{game}"),
        InlineKeyboardButton("🏠 Меню",    callback_data="menu_main")
    ]])
    await q.edit_message_text(
        f"{header}\n\n💰 Баланс: `{bal:.0f} 🪙`\n"
        f"🏆 {lvl['name']} {lvl['emoji']} | XP: `{row_new['xp']:.0f}`",
        parse_mode="Markdown", reply_markup=kb
    )

    # 📺 Лайв-канал
    await live_post(ctx.bot, user, game_names.get(game, game),
                    bet, won, prize, mult, details if won else "")


# ═══════════════════════════════════════════════════
#           DB HELPERS FOR ACTIVE GAMES
# ═══════════════════════════════════════════════════
def _get_ag(uid: int, gt: str):
    with get_conn() as c:
        return c.execute(
            "SELECT * FROM active_games WHERE user_id=? AND game_type=?", (uid, gt)
        ).fetchone()

def _save_ag(uid: int, gt: str, st: dict):
    with get_conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO active_games (user_id,game_type,bet,state,started_at) VALUES (?,?,?,?,?)",
            (uid, gt, st.get("bet", 0), json.dumps(st), datetime.now().isoformat())
        )

def _del_ag(uid: int, gt: str):
    with get_conn() as c:
        c.execute("DELETE FROM active_games WHERE user_id=? AND game_type=?", (uid, gt))


# ═══════════════════════════════════════════════════
#          ✉️  ТЕКСТОВЫЕ СООБЩЕНИЯ
# ═══════════════════════════════════════════════════
async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user   = update.effective_user
    ensure_user(user.id, user.username, user.first_name)
    text   = update.message.text.strip()
    action = ctx.user_data.get("action")

    if action == "withdraw":
        parts = text.split()
        if len(parts) < 2:
            await update.message.reply_text("❌ Формат: `сумма кошелёк`", parse_mode="Markdown"); return
        try: amount = float(parts[0]); wallet = parts[1]
        except: await update.message.reply_text("❌ Ошибка формата."); return
        if amount < MIN_WITHDRAW:
            await update.message.reply_text(f"❌ Минимум: {MIN_WITHDRAW} 🪙"); return
        bal = get_balance(user.id)
        if bal < amount:
            await update.message.reply_text(f"❌ Недостаточно: {bal:.0f} 🪙"); return
        update_balance(user.id, -amount)
        log_tx(user.id, "withdraw", amount, "pending", wallet)
        ctx.user_data.pop("action", None)
        try:
            await ctx.bot.send_message(
                ADMIN_ID,
                f"💸 *ВЫВОД*\n👤 @{user.username or user.first_name} (`{user.id}`)\n"
                f"💰 `{amount:.0f} 🪙` → `{wallet}`\n/admin",
                parse_mode="Markdown"
            )
        except: pass
        await update.message.reply_text(
            f"✅ Заявка на вывод *{amount:.0f} 🪙* создана!\n⏳ До 24 часов.",
            parse_mode="Markdown", reply_markup=back_kb()
        )

    elif action == "promo":
        code = text.upper()
        with get_conn() as c:
            promo = c.execute("SELECT * FROM promo_codes WHERE code=?", (code,)).fetchone()
            if not promo: await update.message.reply_text("❌ Не найден.", reply_markup=back_kb()); return
            if promo["uses_left"] <= 0: await update.message.reply_text("❌ Исчерпан.", reply_markup=back_kb()); return
            if c.execute("SELECT 1 FROM used_promos WHERE user_id=? AND code=?", (user.id, code)).fetchone():
                await update.message.reply_text("❌ Уже использован.", reply_markup=back_kb()); return
            amount = promo["amount"]
            update_balance(user.id, amount, c)
            c.execute("UPDATE promo_codes SET uses_left=uses_left-1, used_count=used_count+1 WHERE code=?", (code,))
            c.execute("INSERT INTO used_promos VALUES (?,?)", (user.id, code))
            log_tx(user.id, "bonus", amount, "confirmed", f"Промокод {code}", c)
        ctx.user_data.pop("action", None)
        bal = get_balance(user.id)
        await update.message.reply_text(
            f"🎁 Активирован! +*{amount:.0f} 🪙*\nБаланс: `{bal:.0f} 🪙`",
            parse_mode="Markdown", reply_markup=main_kb(user.id)
        )

    elif action == "deposit_usd":
        parts = text.split()
        if len(parts) < 2: await update.message.reply_text("❌ Формат: `txid сумма`", parse_mode="Markdown"); return
        try: txid = parts[0]; amount = float(parts[1])
        except: await update.message.reply_text("❌ Ошибка."); return
        tokens = amount * USD_TO_TOKENS
        log_tx(user.id, "deposit_usd", tokens, "pending", f"txid:{txid} usd:{amount}")
        ctx.user_data.pop("action", None)
        try:
            await ctx.bot.send_message(
                ADMIN_ID,
                f"💵 *USD Депозит*\n👤 @{user.username or user.first_name} (`{user.id}`)\n"
                f"💰 ${amount} → {tokens:.0f}🪙\n🔗 `{txid}`\n\n"
                f"`/confirm_dep {user.id} {tokens:.0f}`",
                parse_mode="Markdown"
            )
        except: pass
        await update.message.reply_text(
            f"✅ Запрос отправлен!\n⏳ Ожидай {tokens:.0f} 🪙 — до 30 минут.",
            parse_mode="Markdown", reply_markup=back_kb()
        )

    elif action == "custom_bet":
        game = ctx.user_data.get("game")
        try: bet = float(text.replace(",","."))
        except: await update.message.reply_text("❌ Введи число."); return
        ctx.user_data.pop("action", None); ctx.user_data.pop("game", None)
        class FQ:
            from_user = user
            async def edit_message_text(self, *a, **kw): await update.message.reply_text(*a, **kw)
            async def answer(self): pass
        await _start_game(FQ(), ctx, user, game, bet)

    else:
        row = get_user(user.id)
        lvl = get_level(row["xp"])
        await update.message.reply_text(
            f"🎰 *VIP Vault Casino*\n💰 `{row['balance']:.0f} 🪙` | {lvl['name']} {lvl['emoji']}",
            parse_mode="Markdown", reply_markup=main_kb(user.id)
        )


# ═══════════════════════════════════════════════════
#         💳  STARS ПЛАТЁЖ
# ═══════════════════════════════════════════════════
async def precheckout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.pre_checkout_query
    await q.answer(ok=q.invoice_payload.startswith("deposit_stars_"))

async def successful_payment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    payload = update.message.successful_payment.invoice_payload
    if payload.startswith("deposit_stars_"):
        stars  = int(payload.split("_")[2])
        tokens = stars * STARS_TO_TOKENS
        update_balance(user.id, tokens)
        with get_conn() as c:
            c.execute("UPDATE users SET total_dep=total_dep+? WHERE user_id=?", (tokens, user.id))
        log_tx(user.id, "deposit_stars", tokens, "confirmed", f"Stars:{stars}")
        # Реферал бонус
        row = get_user(user.id)
        if row and row["ref_by"]:
            bonus = tokens * 0.05
            update_balance(row["ref_by"], bonus)
            log_tx(row["ref_by"], "bonus", bonus, "confirmed", f"Реферал Stars от {user.id}")
            try:
                await ctx.bot.send_message(row["ref_by"], f"🎉 Реферал пополнил Stars! +*{bonus:.0f} 🪙*", parse_mode="Markdown")
            except: pass
        bal = get_balance(user.id)
        lvl = get_level(row["xp"] if row else 0)
        await update.message.reply_text(
            f"✅ *Пополнение успешно!*\n\n⭐ {stars} Stars → 🪙 {tokens}\n💰 Баланс: `{bal:.0f} 🪙`",
            parse_mode="Markdown", reply_markup=main_kb(user.id)
        )
        try:
            await ctx.bot.send_message(
                ADMIN_ID,
                f"⭐ *Stars депозит!*\n👤 @{user.username or user.first_name} (`{user.id}`)\n"
                f"💰 {stars}⭐ → {tokens}🪙",
                parse_mode="Markdown"
            )
        except: pass


# ═══════════════════════════════════════════════════
#                   🚀  ЗАПУСК
# ═══════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

def main():
    init_db()
    logger.info("✅ База данных готова")
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("admin",       cmd_admin))
    app.add_handler(CommandHandler("give",        cmd_give))
    app.add_handler(CommandHandler("newpromo",    cmd_newpromo))
    app.add_handler(CommandHandler("confirm_dep", cmd_confirm_dep))
    app.add_handler(CommandHandler("broadcast",   cmd_broadcast))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    logger.info("🚀 VIP Vault Casino Bot запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
