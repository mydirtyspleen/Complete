import os
import json
import logging
from pathlib import Path
from datetime import datetime

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

LOGGER = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

USERS_FILE = DATA_DIR / "users.json"
PAYOUTS_FILE = DATA_DIR / "payouts.json"
SETTINGS_FILE = DATA_DIR / "settings.json"

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

DEFAULT_SETTINGS = {
    "referral_payouts": {
        "5": 2.0,
        "10": 4.0,
        "20": 6.0,
    },
    "min_payout": 5.0,
}

def load_json(path, default):
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return default

def save_json(path, data):
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def ensure_files():
    users = load_json(USERS_FILE, {})
    payouts = load_json(PAYOUTS_FILE, [])
    settings = load_json(SETTINGS_FILE, DEFAULT_SETTINGS)

    if settings is None:
        settings = DEFAULT_SETTINGS
    save_json(SETTINGS_FILE, settings)
    save_json(USERS_FILE, users)
    save_json(PAYOUTS_FILE, payouts)
    return users, payouts, settings

USERS, PAYOUTS, SETTINGS = ensure_files()

def get_user(update: Update):
    uid = str(update.effective_user.id)
    if uid not in USERS:
        USERS[uid] = {
            "id": uid,
            "username": update.effective_user.username,
            "first_name": update.effective_user.first_name,
            "referrer_id": None,
            "referrals_total": 0,
            "referrals_by_tier": {"5":0,"10":0,"20":0},
            "earnings_total": 0.0,
            "earnings_pending": 0.0,
            "earnings_paid": 0.0,
            "tables": {"5":0,"10":0,"20":0},
            "credited_tiers": [],
        }
        save_json(USERS_FILE, USERS)
    return USERS[uid]

def handle_referral(user, code):
    target = code.split("_")[1]
    if target == user["id"]:
        return
    if user.get("referrer_id"):
        return
    if target not in USERS:
        return

    user["referrer_id"] = target
    save_json(USERS_FILE, USERS)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update)
    if context.args:
        arg = context.args[0]
        if arg.startswith("ref_"):
            handle_referral(user, arg)

    await update.message.reply_text(
        "🔥 AFFILIATE BEAST\n\n"
        "Earn cash when players join through your link and play tables.\n\n"
        "Commands:\n"
        "/myref – your referral link\n"
        "/mystats – your earnings\n"
        "/leaderboard – top promoters\n"
        "/table5 /table10 /table20 – log tables"
    )

async def myref(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update)
    botname = context.bot.username
    link = f"https://t.me/{botname}?start=ref_{user['id']}"

    await update.message.reply_text(
        f"🔗 Your referral link:\n{link}"
    )

async def mystats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update)
    await update.message.reply_text(
        f"📊 Stats:\n"
        f"Referrals: {user['referrals_total']}\n"
        f"Earnings total: ${user['earnings_total']:.2f}\n"
        f"Pending: ${user['earnings_pending']:.2f}\n"
        f"Paid: ${user['earnings_paid']:.2f}\n"
    )

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    arr = sorted(USERS.values(), key=lambda u: u["earnings_total"], reverse=True)
    lines = ["🏆 TOP PROMOTERS\n"]
    for i,u in enumerate(arr[:10], start=1):
        name = u["username"] or u["first_name"] or u["id"]
        lines.append(f"{i}. {name} – ${u['earnings_total']:.2f}")
    await update.message.reply_text("\n".join(lines))

def credit(u, tier):
    ref = u.get("referrer_id")
    if not ref:
        return None
    if tier in u["credited_tiers"]:
        return None

    promoter = USERS[ref]
    amount = SETTINGS["referral_payouts"][tier]

    promoter["referrals_total"] += 1
    promoter["referrals_by_tier"][tier] += 1
    promoter["earnings_total"] += amount
    promoter["earnings_pending"] += amount

    u["credited_tiers"].append(tier)
    save_json(USERS_FILE, USERS)

    return promoter, amount

async def log_table(update, tier):
    user = get_user(update)
    user["tables"][tier] += 1
    save_json(USERS_FILE, USERS)

    r = credit(user, tier)
    msg = f"Logged your ${tier} table."
    if r:
        p,a = r
        name = p["username"] or p["first_name"] or p["id"]
        msg += f"\nPromoter {name} earned ${a:.2f}."

    await update.message.reply_text(msg)

async def table5(update, context): await log_table(update,"5")
async def table10(update, context): await log_table(update,"10")
async def table20(update, context): await log_table(update,"20")

def is_admin(update): return update.effective_user.id == ADMIN_ID

async def pending(update, context):
    if not is_admin(update):
        return await update.message.reply_text("Not authorized.")

    minp = SETTINGS["min_payout"]
    lines = [f"💰 Pending payouts ≥ ${minp}"]
    for u in USERS.values():
        if u["earnings_pending"] >= minp:
            name = u["username"] or u["first_name"] or u["id"]
            lines.append(f"{name}: ${u['earnings_pending']:.2f}")
    await update.message.reply_text("\n".join(lines))

async def markpaid(update, context):
    if not is_admin(update):
        return await update.message.reply_text("Not authorized.")
    if len(context.args) < 2:
        return await update.message.reply_text("Usage: /markpaid <userId> <amount>")

    uid, amt = context.args
    amt = float(amt)
    u = USERS.get(uid)
    if not u:
        return await update.message.reply_text("User not found.")

    if amt > u["earnings_pending"]:
        return await update.message.reply_text("Exceeds pending amount.")

    u["earnings_pending"] -= amt
    u["earnings_paid"] += amt
    save_json(USERS_FILE, USERS)

    PAYOUTS.append({"user":uid,"amount":amt,"time":datetime.utcnow().isoformat()})
    save_json(PAYOUTS_FILE, PAYOUTS)

    await update.message.reply_text(f"Paid ${amt:.2f} to {u['id']}")

async def run():
    if not BOT_TOKEN:
        raise Exception("BOT_TOKEN not set")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myref", myref))
    app.add_handler(CommandHandler("mystats", mystats))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("table5", table5))
    app.add_handler(CommandHandler("table10", table10))
    app.add_handler(CommandHandler("table20", table20))
    app.add_handler(CommandHandler("pending", pending))
    app.add_handler(CommandHandler("markpaid", markpaid))

    await app.run_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.run(run())
