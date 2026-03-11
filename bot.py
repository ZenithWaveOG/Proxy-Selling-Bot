import os
import logging
import asyncio
import random
import string
from datetime import datetime
import traceback

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    CallbackQueryHandler, ConversationHandler, ContextTypes
)
from supabase import create_client, Client

# ------------------- CONFIG -------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ADMIN_IDS = [int(x.strip()) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]
BOT_TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", 10000))
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
if not RENDER_EXTERNAL_URL:
    raise ValueError("RENDER_EXTERNAL_URL environment variable not set. Set it to your app's public URL.")

WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}"
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "".join(random.choices(string.ascii_letters + string.digits, k=32)))

# ------------------- LOGGING -------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ------------------- SUPABASE CLIENT -------------------
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ------------------- CONSTANTS -------------------
# States for conversation handlers
(TERMS_STATE, SELECT_COUPON_TYPE, SELECT_QUANTITY, CONFIRM_PAYMENT) = range(4)
(ADMIN_ADD_COUPON_TYPE, ADMIN_ADD_COUPON_DATA, ADMIN_REMOVE_COUPON_TYPE, ADMIN_REMOVE_COUPON_QTY,
 ADMIN_GET_FREE_TYPE, ADMIN_GET_FREE_QTY, ADMIN_CHANGE_PRICE_TYPE, ADMIN_CHANGE_PRICE_QTY,
 ADMIN_CHANGE_PRICE_VALUE, ADMIN_BROADCAST_MSG) = range(4, 14)

# ------------------- HELPER FUNCTIONS -------------------
def generate_order_id():
    return "ORD" + ''.join(random.choices(string.digits, k=14))

async def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

async def get_stock(coupon_type: str = None):
    try:
        query = supabase.table("coupons").select("type").eq("is_available", True)
        if coupon_type:
            query = query.eq("type", coupon_type)
        result = query.execute()
        counts = {}
        for row in result.data:
            t = row['type']
            counts[t] = counts.get(t, 0) + 1
        return counts
    except Exception as e:
        logger.error(f"Error in get_stock: {e}")
        return {}

async def get_price(coupon_type: str, quantity: int):
    try:
        if quantity < 5:
            cat = '1'
        elif quantity < 10:
            cat = '5'
        elif quantity < 20:
            cat = '10'
        else:
            cat = '20'
        resp = supabase.table("prices").select("price").eq("coupon_type", coupon_type).eq("qty_category", cat).execute()
        if resp.data:
            return resp.data[0]['price']
        else:
            logger.warning(f"No price found for {coupon_type} category {cat}")
            return 0
    except Exception as e:
        logger.error(f"Error in get_price: {e}")
        return 0

async def record_user(update: Update):
    try:
        user = update.effective_user
        resp = supabase.table("users").select("user_id").eq("user_id", user.id).execute()
        if not resp.data:
            supabase.table("users").insert({
                "user_id": user.id,
                "username": user.username,
                "first_name": user.first_name,
                "joined_date": datetime.utcnow().isoformat()
            }).execute()
    except Exception as e:
        logger.error(f"Error recording user: {e}")

# ------------------- USER FACING HANDLERS -------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await record_user(update)
        keyboard = [
            [KeyboardButton("🛒 Buy Vouchers")],
            [KeyboardButton("📦 My Orders"), KeyboardButton("📜 Disclaimer")],
            [KeyboardButton("🆘 Support"), KeyboardButton("📢 Our Channels")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        stock = await get_stock()
        stock_msg = "✏️ PROXY CODE SHOP\n━━━━━━━━━━━━━━\n📊 Current Stock\n\n"
        for ctype in ["4000 Off", "2000 Off", "1000 Off", "500 Off"]:
            price = await get_price(ctype, 1)
            stock_msg += f"▫️ {ctype}: {stock.get(ctype, 0)} left (₹{price}/code)\n"
        await update.message.reply_text(f"Welcome to the Coupon Shopping Bot!\n\n{stock_msg}", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error in start: {e}")
        await update.message.reply_text("An error occurred. Please try again later.")

async def buy_vouchers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        terms = (
            "1. Once coupon is delivered, no returns or refunds will be accepted.\n"
            "2. All coupons are fresh and valid.\n"
            "3. All sales are final. No refunds, no replacements.\n"
            "4. If coupon shows redeemed, try after 10-15 min.\n"
            "5. If there is a genuine issue and you recorded full payment to applying, contact support."
        )
        keyboard = [
            [InlineKeyboardButton("✅ Agree", callback_data="terms_agree")],
            [InlineKeyboardButton("❌ Decline", callback_data="terms_decline")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(terms, reply_markup=reply_markup)
        return TERMS_STATE
    except Exception as e:
        logger.error(f"Error in buy_vouchers: {e}")
        await update.message.reply_text("An error occurred. Please try again later.")
        return ConversationHandler.END

async def terms_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        if query.data == "terms_decline":
            await query.edit_message_text("Thanks for using the bot. Goodbye!")
            return ConversationHandler.END
        else:
            types = ["500 Off", "1000 Off", "2000 Off", "4000 Off"]
            keyboard = []
            for ctype in types:
                price = await get_price(ctype, 1)
                keyboard.append([InlineKeyboardButton(f"{ctype} - ₹{price}", callback_data=f"ctype_{ctype}")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("🛒 Select a coupon type:", reply_markup=reply_markup)
            return SELECT_COUPON_TYPE
    except Exception as e:
        logger.error(f"Error in terms_callback: {e}")
        await query.edit_message_text("An error occurred. Please try again.")
        return ConversationHandler.END

async def select_coupon_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        ctype = query.data.replace("ctype_", "")
        context.user_data['ctype'] = ctype
        stock = await get_stock(ctype)
        avail = stock.get(ctype, 0)
        p1 = await get_price(ctype, 1)
        p5 = await get_price(ctype, 5)
        p10 = await get_price(ctype, 10)
        p20 = await get_price(ctype, 20)
        msg = (
            f"🏷️ {ctype}\n"
            f"📦 Available stock: {avail}\n\n"
            f"📋 Available Packages (per-code):\n"
            f"• 1 Code → ₹{p1}/code\n"
            f"• 5 Codes → ₹{p5}/code\n"
            f"• 10 Codes → ₹{p10}/code\n"
            f"• 20+ Codes → ₹{p20}/code\n\n"
            f"👇 Select quantity:"
        )
        keyboard = [
            [InlineKeyboardButton("1 Qty", callback_data="qty_1"),
             InlineKeyboardButton("5 Qty", callback_data="qty_5")],
            [InlineKeyboardButton("10 Qty", callback_data="qty_10"),
             InlineKeyboardButton("20 Qty", callback_data="qty_20")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(msg, reply_markup=reply_markup)
        return SELECT_QUANTITY
    except Exception as e:
        logger.error(f"Error in select_coupon_type: {e}")
        await query.edit_message_text("An error occurred. Please try again.")
        return ConversationHandler.END

async def select_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        data = query.data
        if data.startswith("qty_"):
            qty_str = data.replace("qty_", "")
            qty = int(qty_str)
            context.user_data['qty'] = qty
            await show_invoice(query, context)
            return CONFIRM_PAYMENT
        else:
            await query.edit_message_text("Error, please start over.")
            return ConversationHandler.END
    except Exception as e:
        logger.error(f"Error in select_quantity: {e}")
        await query.edit_message_text("An error occurred. Please try again later.")
        return ConversationHandler.END

async def show_invoice(update_or_query, context: ContextTypes.DEFAULT_TYPE):
    try:
        ctype = context.user_data['ctype']
        qty = context.user_data['qty']
        price_per = await get_price(ctype, qty)
        total = price_per * qty
        order_id = generate_order_id()
        context.user_data['order_id'] = order_id
        context.user_data['total'] = total

        # Insert order into Supabase
        try:
            supabase.table("orders").insert({
                "order_id": order_id,
                "user_id": update_or_query.from_user.id,
                "coupon_type": ctype,
                "quantity": qty,
                "amount_paid": total,
                "status": "pending",
                "payment_time": datetime.utcnow().isoformat()
            }).execute()
        except Exception as e:
            logger.error(f"Failed to insert order {order_id}: {e}")
            error_msg = "Database error. Please try again later."
            if isinstance(update_or_query, Update):
                await update_or_query.reply_text(error_msg)
            else:
                await update_or_query.edit_message_text(error_msg)
            return

        # Get QR code
        try:
            qr_resp = supabase.table("settings").select("value").eq("key", "qr_file_id").execute()
            qr_file_id = qr_resp.data[0]['value'] if qr_resp.data else None
        except Exception as e:
            logger.error(f"Failed to fetch QR code: {e}")
            qr_file_id = None

        invoice_msg = (
            f"🧾 INVOICE\n━━━━━━━━━━━━━━\n"
            f"🆔 {order_id}\n"
            f"📦 {ctype} (x{qty})\n"
            f"💰 Pay Exactly: ₹{total:.2f}\n"
            f"⚠️ CRITICAL: You MUST pay exact amount. Do not ignore the paise, or the bot will NOT find your payment!\n\n"
            f"⏳ QR valid for 10 minutes."
        )
        keyboard = [[InlineKeyboardButton("✅ Verify Payment", callback_data="verify_payment")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if isinstance(update_or_query, Update):
            if qr_file_id:
                await update_or_query.reply_photo(photo=qr_file_id, caption=invoice_msg, reply_markup=reply_markup)
            else:
                await update_or_query.reply_text(invoice_msg + "\n\n(QR not set by admin)", reply_markup=reply_markup)
        else:
            await update_or_query.edit_message_text(invoice_msg)
            if qr_file_id:
                await update_or_query.message.reply_photo(photo=qr_file_id, reply_markup=reply_markup)
            else:
                await update_or_query.message.reply_text("(QR not set by admin)", reply_markup=reply_markup)

    except Exception as e:
        logger.error(f"Unexpected error in show_invoice: {e}\n{traceback.format_exc()}")
        error_msg = "An unexpected error occurred. Please try again later."
        if isinstance(update_or_query, Update):
            await update_or_query.reply_text(error_msg)
        else:
            await update_or_query.edit_message_text(error_msg)

async def verify_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        order_id = context.user_data.get('order_id')
        if not order_id:
            await query.edit_message_text("Order not found. Please start over.")
            return ConversationHandler.END
        order = supabase.table("orders").select("*").eq("order_id", order_id).execute().data[0]
        user = supabase.table("users").select("username,first_name").eq("user_id", order['user_id']).execute().data[0]
        msg = (
            f"New payment verification:\n"
            f"Order ID: {order_id}\n"
            f"User: @{user.get('username')} ({user['first_name']})\n"
            f"Type: {order['coupon_type']}\n"
            f"Quantity: {order['quantity']}\n"
            f"Amount: ₹{order['amount_paid']}\n"
            f"Time: {order['payment_time']}"
        )
        keyboard = [
            [InlineKeyboardButton("✅ Accept", callback_data=f"accept_{order_id}"),
             InlineKeyboardButton("❌ Decline", callback_data=f"decline_{order_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        for admin in ADMIN_IDS:
            try:
                await context.bot.send_message(admin, msg, reply_markup=reply_markup)
            except:
                pass
        await query.edit_message_text("Your payment is being verified. Please wait for admin approval.")
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Error in verify_payment: {e}")
        await query.edit_message_text("An error occurred. Please try again.")
        return ConversationHandler.END

async def admin_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        data = query.data
        if data.startswith("accept_"):
            order_id = data.replace("accept_", "")
            order = supabase.table("orders").select("*").eq("order_id", order_id).execute().data[0]
            if not order:
                await query.edit_message_text("Order not found.")
                return
            supabase.table("orders").update({"status": "approved", "approval_time": datetime.utcnow().isoformat()}).eq("order_id", order_id).execute()
            coupons_resp = supabase.table("coupons").select("code").eq("type", order['coupon_type']).eq("is_available", True).limit(order['quantity']).execute()
            codes = [row['code'] for row in coupons_resp.data]
            if len(codes) < order['quantity']:
                supabase.table("orders").update({"status": "declined"}).eq("order_id", order_id).execute()
                await query.edit_message_text(f"Insufficient stock for order {order_id}. Declined.")
                await context.bot.send_message(order['user_id'], "Your order was declined due to insufficient stock. Contact support.")
                return
            for code in codes:
                supabase.table("coupons").update({"is_available": False, "purchased_by": order['user_id'], "purchase_time": datetime.utcnow().isoformat()}).eq("code", code).execute()
            codes_msg = "Thanks for purchasing!\n\nYour codes:\n" + "\n".join(codes)
            await context.bot.send_message(order['user_id'], codes_msg)
            await query.edit_message_text(f"Order {order_id} approved and codes sent.")
        elif data.startswith("decline_"):
            order_id = data.replace("decline_", "")
            supabase.table("orders").update({"status": "declined"}).eq("order_id", order_id).execute()
            order = supabase.table("orders").select("user_id").eq("order_id", order_id).execute().data[0]
            await context.bot.send_message(order['user_id'], "Your payment has been declined by admin. If there is any issue, contact support.")
            await query.edit_message_text(f"Order {order_id} declined.")
    except Exception as e:
        logger.error(f"Error in admin_payment_callback: {e}")
        await query.edit_message_text("An error occurred while processing.")

async def my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        orders = supabase.table("orders").select("*").eq("user_id", user_id).order("payment_time", desc=True).execute().data
        if not orders:
            await update.message.reply_text("You have no orders yet.")
            return
        msg = "Your orders:\n"
        for o in orders[:10]:
            msg += f"\nID: {o['order_id']}\nType: {o['coupon_type']} x{o['quantity']}\nAmount: ₹{o['amount_paid']}\nStatus: {o['status']}\nTime: {o['payment_time']}\n---"
        await update.message.reply_text(msg)
    except Exception as e:
        logger.error(f"Error in my_orders: {e}")
        await update.message.reply_text("An error occurred. Please try again later.")

async def disclaimer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "1. 🕒 IF CODE SHOW REDEEMED: Wait For 12–13 min Because All Codes Are Checked Before We Add.\n"
        "2. 📦 ELIGIBILITY: Valid only for SHEINVERSE: https://www.sheinindia.in/c/sverse-5939-37961\n"
        "3. ⚡️ DELIVERY: codes are delivered immediately after payment confirmation.\n"
        "4. 🚫 NO REFUNDS: All sales final. No refunds/replacements for any codes.\n"
        "5. ❌ SUPPORT: For issues, a full screen-record from purchase to application is required."
    )
    await update.message.reply_text(text)

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🆘 Support Contact:\n━━━━━━━━━━━━━━\n@ProxySupportChat_bot")

async def our_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("📢 @PROXY_LOOTERS", url="https://t.me/PROXY_LOOTERS")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("📢 Join our official channels for updates and deals:", reply_markup=reply_markup)

# ------------------- ADMIN HANDLERS -------------------
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("Unauthorized.")
        return
    keyboard = [
        [InlineKeyboardButton("➕ Add Coupon", callback_data="admin_add")],
        [InlineKeyboardButton("➖ Remove Coupon", callback_data="admin_remove")],
        [InlineKeyboardButton("📊 Stock", callback_data="admin_stock")],
        [InlineKeyboardButton("🎁 Get A Free Code", callback_data="admin_free")],
        [InlineKeyboardButton("💰 Change Prices", callback_data="admin_price")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("📋 Last 10 Purchases", callback_data="admin_last10")],
        [InlineKeyboardButton("🖼 Update QR", callback_data="admin_update_qr")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Admin Panel", reply_markup=reply_markup)

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await is_admin(query.from_user.id):
        await query.edit_message_text("Unauthorized.")
        return
    data = query.data
    try:
        if data == "admin_stock":
            stock = await get_stock()
            msg = "Current Stock:\n"
            for ctype in ["500 Off", "1000 Off", "2000 Off", "4000 Off"]:
                msg += f"{ctype}: {stock.get(ctype, 0)}\n"
            await query.edit_message_text(msg)
        elif data == "admin_last10":
            orders = supabase.table("orders").select("order_id, user_id, coupon_type, quantity, amount_paid, status, payment_time").eq("status", "approved").order("payment_time", desc=True).limit(10).execute().data
            if not orders:
                await query.edit_message_text("No purchases yet.")
                return
            msg = "Last 10 Purchases:\n"
            for o in orders:
                msg += f"\nOrder: {o['order_id']}\nUser: {o['user_id']}\nType: {o['coupon_type']} x{o['quantity']}\nAmt: ₹{o['amount_paid']}\nTime: {o['payment_time']}\n---"
            await query.edit_message_text(msg)
        elif data == "admin_update_qr":
            await query.edit_message_text("Please send me the new QR code image.")
            return
        elif data.startswith("admin_add"):
            keyboard = [
                [InlineKeyboardButton("500 Off", callback_data="addtype_500 Off")],
                [InlineKeyboardButton("1000 Off", callback_data="addtype_1000 Off")],
                [InlineKeyboardButton("2000 Off", callback_data="addtype_2000 Off")],
                [InlineKeyboardButton("4000 Off", callback_data="addtype_4000 Off")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("Select coupon type to add:", reply_markup=reply_markup)
            return ADMIN_ADD_COUPON_TYPE
        elif data.startswith("admin_remove"):
            keyboard = [
                [InlineKeyboardButton("500 Off", callback_data="removetype_500 Off")],
                [InlineKeyboardButton("1000 Off", callback_data="removetype_1000 Off")],
                [InlineKeyboardButton("2000 Off", callback_data="removetype_2000 Off")],
                [InlineKeyboardButton("4000 Off", callback_data="removetype_4000 Off")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("Select coupon type to remove:", reply_markup=reply_markup)
            return ADMIN_REMOVE_COUPON_TYPE
        elif data.startswith("admin_free"):
            keyboard = [
                [InlineKeyboardButton("500 Off", callback_data="freetype_500 Off")],
                [InlineKeyboardButton("1000 Off", callback_data="freetype_1000 Off")],
                [InlineKeyboardButton("2000 Off", callback_data="freetype_2000 Off")],
                [InlineKeyboardButton("4000 Off", callback_data="freetype_4000 Off")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("Select coupon type for free code:", reply_markup=reply_markup)
            return ADMIN_GET_FREE_TYPE
        elif data.startswith("admin_price"):
            keyboard = [
                [InlineKeyboardButton("500 Off", callback_data="pricetype_500 Off")],
                [InlineKeyboardButton("1000 Off", callback_data="pricetype_1000 Off")],
                [InlineKeyboardButton("2000 Off", callback_data="pricetype_2000 Off")],
                [InlineKeyboardButton("4000 Off", callback_data="pricetype_4000 Off")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("Select coupon type to change price:", reply_markup=reply_markup)
            return ADMIN_CHANGE_PRICE_TYPE
        elif data == "admin_broadcast":
            await query.edit_message_text("Please enter the message to broadcast to all users:")
            return ADMIN_BROADCAST_MSG
    except Exception as e:
        logger.error(f"Error in admin_callback: {e}")
        await query.edit_message_text("An error occurred.")

# ... (admin_add_type, admin_add_data, etc. remain the same as before, ensure they are included)
# I'll include them for completeness

async def admin_add_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        ctype = query.data.replace("addtype_", "")
        context.user_data['admin_add_type'] = ctype
        await query.edit_message_text(f"Send me the coupon codes for {ctype}, one per line:")
        return ADMIN_ADD_COUPON_DATA
    except Exception as e:
        logger.error(f"Error in admin_add_type: {e}")
        await query.edit_message_text("An error occurred.")
        return ConversationHandler.END

async def admin_add_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        codes = update.message.text.strip().split('\n')
        ctype = context.user_data['admin_add_type']
        inserted = 0
        for code in codes:
            code = code.strip()
            if code:
                try:
                    supabase.table("coupons").insert({"code": code, "type": ctype, "is_available": True}).execute()
                    inserted += 1
                except:
                    pass
        await update.message.reply_text(f"Coupons successfully added: {inserted} new codes.")
    except Exception as e:
        logger.error(f"Error in admin_add_data: {e}")
        await update.message.reply_text("An error occurred.")
    return ConversationHandler.END

async def admin_remove_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        ctype = query.data.replace("removetype_", "")
        context.user_data['admin_remove_type'] = ctype
        await query.edit_message_text("How many codes to remove? (Enter number):")
        return ADMIN_REMOVE_COUPON_QTY
    except Exception as e:
        logger.error(f"Error in admin_remove_type: {e}")
        await query.edit_message_text("An error occurred.")
        return ConversationHandler.END

async def admin_remove_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        qty = int(update.message.text)
        ctype = context.user_data['admin_remove_type']
        codes = supabase.table("coupons").select("code").eq("type", ctype).eq("is_available", True).limit(qty).execute().data
        for c in codes:
            supabase.table("coupons").delete().eq("code", c['code']).execute()
        await update.message.reply_text(f"Removed {len(codes)} coupons.")
    except Exception as e:
        logger.error(f"Error in admin_remove_qty: {e}")
        await update.message.reply_text("Invalid number or error.")
    return ConversationHandler.END

async def admin_free_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        ctype = query.data.replace("freetype_", "")
        context.user_data['admin_free_type'] = ctype
        await query.edit_message_text("How many free codes do you want?")
        return ADMIN_GET_FREE_QTY
    except Exception as e:
        logger.error(f"Error in admin_free_type: {e}")
        await query.edit_message_text("An error occurred.")
        return ConversationHandler.END

async def admin_free_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        qty = int(update.message.text)
        ctype = context.user_data['admin_free_type']
        codes = supabase.table("coupons").select("code").eq("type", ctype).eq("is_available", True).limit(qty).execute().data
        code_list = [c['code'] for c in codes]
        for c in codes:
            supabase.table("coupons").update({"is_available": False, "purchased_by": update.effective_user.id, "purchase_time": datetime.utcnow().isoformat()}).eq("code", c['code']).execute()
        await update.message.reply_text("Your free codes:\n" + "\n".join(code_list))
    except Exception as e:
        logger.error(f"Error in admin_free_qty: {e}")
        await update.message.reply_text("Error.")
    return ConversationHandler.END

async def admin_price_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        ctype = query.data.replace("pricetype_", "")
        context.user_data['admin_price_type'] = ctype
        keyboard = [
            [InlineKeyboardButton("1 Qty", callback_data="priceqty_1"),
             InlineKeyboardButton("5 Qty", callback_data="priceqty_5")],
            [InlineKeyboardButton("10 Qty", callback_data="priceqty_10"),
             InlineKeyboardButton("20 Qty", callback_data="priceqty_20")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Select quantity category:", reply_markup=reply_markup)
        return ADMIN_CHANGE_PRICE_QTY
    except Exception as e:
        logger.error(f"Error in admin_price_type: {e}")
        await query.edit_message_text("An error occurred.")
        return ConversationHandler.END

async def admin_price_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        qty_cat = query.data.replace("priceqty_", "")
        context.user_data['admin_price_qty'] = qty_cat
        await query.edit_message_text("Enter new price (in rupees):")
        return ADMIN_CHANGE_PRICE_VALUE
    except Exception as e:
        logger.error(f"Error in admin_price_qty: {e}")
        await query.edit_message_text("An error occurred.")
        return ConversationHandler.END

async def admin_price_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = float(update.message.text)
        ctype = context.user_data['admin_price_type']
        qty_cat = context.user_data['admin_price_qty']
        supabase.table("prices").update({"price": price}).eq("coupon_type", ctype).eq("qty_category", qty_cat).execute()
        await update.message.reply_text("Price updated.")
    except Exception as e:
        logger.error(f"Error in admin_price_value: {e}")
        await update.message.reply_text("Invalid price.")
    return ConversationHandler.END

async def admin_broadcast_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        msg = update.message.text
        users = supabase.table("users").select("user_id").execute().data
        success = 0
        for u in users:
            try:
                await context.bot.send_message(u['user_id'], msg)
                success += 1
                await asyncio.sleep(0.05)
            except:
                pass
        await update.message.reply_text(f"Broadcast sent to {success}/{len(users)} users.")
    except Exception as e:
        logger.error(f"Error in admin_broadcast_msg: {e}")
        await update.message.reply_text("Error sending broadcast.")
    return ConversationHandler.END

async def admin_update_qr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.message.photo:
            file_id = update.message.photo[-1].file_id
            supabase.table("settings").upsert({"key": "qr_file_id", "value": file_id}).execute()
            await update.message.reply_text("QR code updated.")
        else:
            await update.message.reply_text("Please send an image.")
    except Exception as e:
        logger.error(f"Error in admin_update_qr: {e}")
        await update.message.reply_text("Error updating QR.")
    return ConversationHandler.END

# ------------------- MAIN -------------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # User conversation for buying
    buy_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🛒 Buy Vouchers$"), buy_vouchers)],
        states={
            TERMS_STATE: [CallbackQueryHandler(terms_callback, pattern="^(terms_agree|terms_decline)$")],
            SELECT_COUPON_TYPE: [CallbackQueryHandler(select_coupon_type, pattern="^ctype_")],
            SELECT_QUANTITY: [CallbackQueryHandler(select_quantity, pattern="^qty_")],
            CONFIRM_PAYMENT: [CallbackQueryHandler(verify_payment, pattern="^verify_payment$")]
        },
        fallbacks=[CommandHandler("start", start)],
        per_message=True
    )
    app.add_handler(buy_conv)

    # Admin conversation
    admin_conv = ConversationHandler(
        entry_points=[CommandHandler("admin", admin_panel)],
        states={
            ADMIN_ADD_COUPON_TYPE: [CallbackQueryHandler(admin_add_type, pattern="^addtype_")],
            ADMIN_ADD_COUPON_DATA: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_data)],
            ADMIN_REMOVE_COUPON_TYPE: [CallbackQueryHandler(admin_remove_type, pattern="^removetype_")],
            ADMIN_REMOVE_COUPON_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_remove_qty)],
            ADMIN_GET_FREE_TYPE: [CallbackQueryHandler(admin_free_type, pattern="^freetype_")],
            ADMIN_GET_FREE_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_free_qty)],
            ADMIN_CHANGE_PRICE_TYPE: [CallbackQueryHandler(admin_price_type, pattern="^pricetype_")],
            ADMIN_CHANGE_PRICE_QTY: [CallbackQueryHandler(admin_price_qty, pattern="^priceqty_")],
            ADMIN_CHANGE_PRICE_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_price_value)],
            ADMIN_BROADCAST_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_msg)],
        },
        fallbacks=[CommandHandler("start", start)],
        per_message=True
    )
    app.add_handler(admin_conv)

    # QR update conversation
    qr_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_callback, pattern="^admin_update_qr$")],
        states={
            0: [MessageHandler(filters.PHOTO, admin_update_qr)]
        },
        fallbacks=[CommandHandler("start", start)],
        per_message=True
    )
    app.add_handler(qr_conv)

    # Other admin callbacks (stock, last10) – handled directly
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^(admin_stock|admin_last10)$"))

    # Payment verification callbacks
    app.add_handler(CallbackQueryHandler(admin_payment_callback, pattern="^(accept_|decline_)"))

    # Other user handlers
    app.add_handler(MessageHandler(filters.Regex("^📦 My Orders$"), my_orders))
    app.add_handler(MessageHandler(filters.Regex("^📜 Disclaimer$"), disclaimer))
    app.add_handler(MessageHandler(filters.Regex("^🆘 Support$"), support))
    app.add_handler(MessageHandler(filters.Regex("^📢 Our Channels$"), our_channels))
    app.add_handler(CommandHandler("start", start))

    # Set up webhook
    logger.info(f"Starting webhook on port {PORT} with URL {WEBHOOK_URL}")
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=WEBHOOK_PATH,
        webhook_url=WEBHOOK_URL,
        secret_token=WEBHOOK_SECRET,
    )

if __name__ == "__main__":
    main()
