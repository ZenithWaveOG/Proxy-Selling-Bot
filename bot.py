import os
import logging
import random
import string
from datetime import datetime
from decimal import Decimal

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
import supabase

# ------------------- CONFIGURATION -------------------
BOT_TOKEN = "YOUR_BOT_TOKEN"
ADMIN_IDS = [123456789, 987654321]  # Replace with your Telegram user IDs
SUPABASE_URL = "YOUR_SUPABASE_URL"
SUPABASE_KEY = "YOUR_SUPABASE_ANON_KEY"

# Initialize Supabase client
supabase_client = supabase.create_client(SUPABASE_URL, SUPABASE_KEY)

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
BUY_SELECT_TYPE, BUY_TERMS, BUY_SELECT_QUANTITY, BUY_CUSTOM_QTY, BUY_VERIFY = range(5)
ADMIN_ADD_COUPON_TYPE, ADMIN_ADD_COUPON_DATA = range(10, 12)
ADMIN_REMOVE_COUPON_TYPE, ADMIN_REMOVE_COUPON_QTY = range(12, 14)
ADMIN_GET_FREE_TYPE, ADMIN_GET_FREE_QTY = range(14, 16)
ADMIN_BROADCAST_MSG = 16
ADMIN_CHANGE_PRICE_TYPE, ADMIN_CHANGE_PRICE_QTY, ADMIN_CHANGE_PRICE_VALUE = range(17, 20)
ADMIN_UPDATE_QR = 20

# Helper function to check if user is admin
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# Generate random invoice ID
def generate_invoice_id():
    return "ORD" + ''.join(random.choices(string.digits, k=12))

# Get price per code for a given coupon type and quantity
def get_price(coupon_type: str, quantity: int) -> Decimal:
    # Determine bracket
    if quantity < 5:
        bracket = 1
    elif quantity < 10:
        bracket = 5
    elif quantity < 20:
        bracket = 10
    else:
        bracket = 20
    # Fetch from DB
    response = supabase_client.table("price_settings") \
        .select("price_per_code") \
        .eq("coupon_type", coupon_type) \
        .eq("qty_bracket", bracket) \
        .execute()
    if response.data:
        return Decimal(str(response.data[0]["price_per_code"]))
    return Decimal('0')

# ------------------- START COMMAND -------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # Save/update user in DB
    supabase_client.table("users").upsert({
        "user_id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "last_interaction": datetime.utcnow().isoformat()
    }).execute()

    welcome_text = (
        "🎁 Welcome to the Coupon Shopping Bot!\n\n"
        "Use the buttons below to navigate."
    )
    keyboard = [
        [InlineKeyboardButton("🛍 Buy Vouchers", callback_data="menu_buy")],
        [InlineKeyboardButton("📦 My Orders", callback_data="menu_orders")],
        [InlineKeyboardButton("📜 Disclaimer", callback_data="menu_disclaimer")],
        [InlineKeyboardButton("🆘 Support", callback_data="menu_support")],
        [InlineKeyboardButton("📢 Our Channels", callback_data="menu_channels")],
    ]
    if is_admin(user.id):
        keyboard.append([InlineKeyboardButton("🔧 Admin Panel", callback_data="admin_panel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(welcome_text, reply_markup=reply_markup)

# ------------------- MENU HANDLERS (Callback Queries) -------------------
async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "menu_buy":
        # Show coupon types with stock
        await show_coupon_stock(update, context)
    elif data == "menu_orders":
        await show_user_orders(update, context)
    elif data == "menu_disclaimer":
        disclaimer = (
            "📜 *DISCLAIMER*\n\n"
            "1. 🕒 IF CODE SHOW REDEEMED: Wait for 12–13 min Because All Codes Are Checked Before We Add.\n"
            "2. 📦 ELIGIBILITY: Valid only for SHEINVERSE: https://www.sheinindia.in/c/sverse-5939-37961\n"
            "3. ⚡️ DELIVERY: codes are delivered immediately after payment confirmation.\n"
            "4. 🚫 NO REFUNDS: All sales final. No refunds/replacements for any codes.\n"
            "5. ❌ SUPPORT: For issues, a full screen-record from purchase to application is required."
        )
        await query.edit_message_text(disclaimer, parse_mode="Markdown")
    elif data == "menu_support":
        support_text = (
            "🆘 *Support Contact:*\n"
            "━━━━━━━━━━━━━━\n"
            "@ProxySupportChat_bot"
        )
        await query.edit_message_text(support_text, parse_mode="Markdown")
    elif data == "menu_channels":
        keyboard = [
            [InlineKeyboardButton("📢 @PROXY_LOOTERS", url="https://t.me/PROXY_LOOTERS")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "📢 Join our official channels for updates and deals:",
            reply_markup=reply_markup
        )
    elif data == "back_to_main":
        await start(update, context)  # Re-send main menu (but edit message)
        # Alternatively, we could just delete and send new, but editing is fine
    elif data.startswith("buy_type_"):
        # User selected a coupon type from stock view
        coupon_type = data.replace("buy_type_", "")
        context.user_data["buy_type"] = coupon_type
        # Show terms and conditions
        await show_terms(update, context)
    elif data == "terms_accept":
        await show_quantity_selection(update, context)
    elif data == "terms_decline":
        await query.edit_message_text("Thanks for using the bot. Goodbye!")
    elif data.startswith("qty_"):
        # quantity selection from inline buttons (1,5,10,20)
        qty = int(data.split("_")[1])
        await process_quantity(update, context, qty)
    elif data == "qty_custom":
        await query.edit_message_text("Please enter the quantity you want (number):")
        return BUY_CUSTOM_QTY
    elif data.startswith("verify_"):
        # User clicked verify payment
        await verify_payment(update, context)
    elif data.startswith("admin_accept_"):
        # Admin accepts payment
        order_id = int(data.split("_")[2])
        await approve_order(update, context, order_id)
    elif data.startswith("admin_decline_"):
        order_id = int(data.split("_")[2])
        await decline_order(update, context, order_id)
    elif data == "admin_panel":
        await show_admin_panel(update, context)
    elif data.startswith("admin_"):
        await handle_admin_submenus(update, context, data)

    return ConversationHandler.END  # Most are not stateful, but we'll manage

async def show_coupon_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    # Get stock counts from DB
    stock = {}
    for ctype in ['500', '1000', '2000', '4000']:
        resp = supabase_client.table("coupons") \
            .select("id", count="exact") \
            .eq("type", ctype) \
            .eq("used", False) \
            .execute()
        stock[ctype] = resp.count

    # Get prices from DB (display price per code for 1 qty as example)
    # We'll show price for 1 code
    price1 = {}
    for ctype in ['500', '1000', '2000', '4000']:
        p = supabase_client.table("price_settings") \
            .select("price_per_code") \
            .eq("coupon_type", ctype) \
            .eq("qty_bracket", 1) \
            .execute()
        price1[ctype] = p.data[0]["price_per_code"] if p.data else 0

    text = (
        "✏️ *PROXY CODE SHOP*\n"
        "━━━━━━━━━━━━━━\n"
        "📊 *Current Stock*\n\n"
        f"▫️ 4000 Off: {stock['4000']} left (₹{price1['4000']}/code)\n"
        f"▫️ 2000 Off: {stock['2000']} left (₹{price1['2000']}/code)\n"
        f"▫️ 1000 Off: {stock['1000']} left (₹{price1['1000']}/code)\n"
        f"▫️ 500 Off: {stock['500']} left (₹{price1['500']}/code)\n"
    )
    keyboard = [
        [InlineKeyboardButton("500 Off", callback_data="buy_type_500")],
        [InlineKeyboardButton("1000 Off", callback_data="buy_type_1000")],
        [InlineKeyboardButton("2000 Off", callback_data="buy_type_2000")],
        [InlineKeyboardButton("4000 Off", callback_data="buy_type_4000")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)

async def show_terms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    terms_text = (
        "Please read and accept the terms:\n\n"
        "1. Once coupon is delivered, no returns or refunds will be accepted.\n"
        "2. All coupons are fresh and valid.\n"
        "3. All sales are final. No refunds, no replacements.\n"
        "4. If coupon shows redeemed, try after some time (10-15 min).\n"
        "5. If there is a genuine issue and you recorded full like payment to applying, then you can contact in support."
    )
    keyboard = [
        [InlineKeyboardButton("✅ I agree", callback_data="terms_accept"),
         InlineKeyboardButton("❌ Decline", callback_data="terms_decline")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(terms_text, reply_markup=reply_markup)

async def show_quantity_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    coupon_type = context.user_data["buy_type"]
    # Get available stock
    resp = supabase_client.table("coupons") \
        .select("id", count="exact") \
        .eq("type", coupon_type) \
        .eq("used", False) \
        .execute()
    stock = resp.count

    # Get prices for each bracket
    prices = {}
    for bracket in [1,5,10,20]:
        p = supabase_client.table("price_settings") \
            .select("price_per_code") \
            .eq("coupon_type", coupon_type) \
            .eq("qty_bracket", bracket) \
            .execute()
        prices[bracket] = p.data[0]["price_per_code"] if p.data else 0

    text = (
        f"🏷️ *{coupon_type} Off*\n"
        f"📦 Available stock: {stock}\n\n"
        "📋 *Available Packages (per-code):*\n"
        f"• 1 Code → ₹{prices[1]}/code\n"
        f"• 5 Codes → ₹{prices[5]}/code\n"
        f"• 10 Codes → ₹{prices[10]}/code\n"
        f"• 20+ Codes → ₹{prices[20]}/code\n\n"
        "👇 Select quantity:"
    )
    keyboard = [
        [InlineKeyboardButton("1 Qty", callback_data="qty_1"),
         InlineKeyboardButton("5 Qty", callback_data="qty_5")],
        [InlineKeyboardButton("10 Qty", callback_data="qty_10"),
         InlineKeyboardButton("20 Qty", callback_data="qty_20")],
        [InlineKeyboardButton("Custom Qty", callback_data="qty_custom")],
        [InlineKeyboardButton("🔙 Back", callback_data="menu_buy")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)

async def process_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE, qty: int):
    context.user_data["buy_quantity"] = qty
    await generate_invoice(update, context)

async def custom_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # This is a message handler, will be called after user types number
    try:
        qty = int(update.message.text)
        if qty <= 0:
            raise ValueError
        context.user_data["buy_quantity"] = qty
        await generate_invoice(update, context, is_message=True)
    except:
        await update.message.reply_text("Invalid number. Please enter a positive integer.")
        return BUY_CUSTOM_QTY
    return ConversationHandler.END

async def generate_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE, is_message=False):
    if is_message:
        user_id = update.message.from_user.id
        message = update.message
    else:
        query = update.callback_query
        user_id = query.from_user.id
        await query.answer()

    coupon_type = context.user_data["buy_type"]
    quantity = context.user_data["buy_quantity"]

    # Calculate price
    price_per_code = get_price(coupon_type, quantity)
    total = price_per_code * quantity

    # Create invoice ID
    invoice_id = generate_invoice_id()

    # Save order as pending in DB
    supabase_client.table("orders").insert({
        "user_id": user_id,
        "coupon_type": coupon_type,
        "quantity": quantity,
        "amount_paid": float(total),
        "status": "pending",
        "invoice_id": invoice_id
    }).execute()

    # Get QR code from settings
    qr_resp = supabase_client.table("admin_settings").select("value").eq("key", "qr_code").execute()
    qr_data = qr_resp.data[0]["value"] if qr_resp.data else None

    # If QR is a file_id, send photo; else if URL, send as text
    invoice_text = (
        f"🧾 *INVOICE*\n"
        f"━━━━━━━━━━━━━━\n"
        f"🆔 {invoice_id}\n"
        f"📦 {coupon_type} Off (x{quantity})\n"
        f"💰 Pay Exactly: ₹{total:.2f}\n"
        f"⚠️ CRITICAL: You MUST pay exact amount. Do not ignore the paise (decimals), or the bot will NOT find your payment!\n\n"
        f"⏳ QR valid for 10 minutes."
    )
    keyboard = [[InlineKeyboardButton("✅ Verify Payment", callback_data="verify_" + invoice_id)]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if is_message:
        if qr_data and qr_data.startswith("file_id:"):
            file_id = qr_data.replace("file_id:", "")
            await update.message.reply_photo(photo=file_id, caption=invoice_text, parse_mode="Markdown", reply_markup=reply_markup)
        else:
            await update.message.reply_text(invoice_text + "\n\nQR: " + (qr_data or "Not set"), parse_mode="Markdown", reply_markup=reply_markup)
    else:
        if qr_data and qr_data.startswith("file_id:"):
            file_id = qr_data.replace("file_id:", "")
            await query.edit_message_media(media=InputMediaPhoto(media=file_id, caption=invoice_text, parse_mode="Markdown"), reply_markup=reply_markup)
        else:
            await query.edit_message_text(invoice_text + "\n\nQR: " + (qr_data or "Not set"), parse_mode="Markdown", reply_markup=reply_markup)

    # Store invoice_id in context for later verification
    context.user_data["current_invoice"] = invoice_id

async def verify_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    invoice_id = context.user_data.get("current_invoice")
    if not invoice_id:
        invoice_id = query.data.split("_")[1]  # fallback

    # Fetch order details
    order_resp = supabase_client.table("orders").select("*").eq("invoice_id", invoice_id).execute()
    if not order_resp.data:
        await query.edit_message_text("Order not found.")
        return
    order = order_resp.data[0]

    # Notify admins
    admin_text = (
        f"💰 *Payment verification requested*\n"
        f"User: {query.from_user.full_name} (@{query.from_user.username})\n"
        f"Invoice: {invoice_id}\n"
        f"Type: {order['coupon_type']} Off\n"
        f"Quantity: {order['quantity']}\n"
        f"Amount: ₹{order['amount_paid']}\n"
        f"Please accept or decline."
    )
    keyboard = [
        [InlineKeyboardButton("✅ Accept", callback_data=f"admin_accept_{order['id']}"),
         InlineKeyboardButton("❌ Decline", callback_data=f"admin_decline_{order['id']}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(admin_id, admin_text, parse_mode="Markdown", reply_markup=reply_markup)
        except:
            pass

    await query.edit_message_text("⏳ Your payment verification request has been sent to admin. Please wait for approval.")

async def approve_order(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int):
    query = update.callback_query
    await query.answer()
    # Update order status
    supabase_client.table("orders").update({"status": "approved"}).eq("id", order_id).execute()
    # Fetch order details
    order_resp = supabase_client.table("orders").select("*").eq("id", order_id).execute()
    order = order_resp.data[0]
    # Fetch unused coupons of the required type
    coupons_resp = supabase_client.table("coupons") \
        .select("code") \
        .eq("type", order["coupon_type"]) \
        .eq("used", False) \
        .limit(order["quantity"]) \
        .execute()
    codes = [c["code"] for c in coupons_resp.data]
    if len(codes) < order["quantity"]:
        # Not enough stock – maybe handle error
        await query.edit_message_text("Error: Not enough stock to fulfill order.")
        return
    # Mark coupons as used
    for code in codes:
        supabase_client.table("coupons").update({"used": True}).eq("code", code).execute()
    # Send codes to user
    codes_text = "\n".join(codes)
    user_msg = f"✅ Payment approved! Here are your codes:\n\n{codes_text}\n\nThanks for purchasing!"
    await context.bot.send_message(order["user_id"], user_msg)
    await query.edit_message_text("Order approved and codes sent to user.")

async def decline_order(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int):
    query = update.callback_query
    await query.answer()
    supabase_client.table("orders").update({"status": "declined"}).eq("id", order_id).execute()
    order_resp = supabase_client.table("orders").select("*").eq("id", order_id).execute()
    order = order_resp.data[0]
    await context.bot.send_message(order["user_id"], "❌ Your payment has been declined by admin. If you think this is a mistake, please contact support.")
    await query.edit_message_text("Order declined.")

async def show_user_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    orders_resp = supabase_client.table("orders") \
        .select("*") \
        .eq("user_id", user_id) \
        .order("order_date", desc=True) \
        .limit(10) \
        .execute()
    if not orders_resp.data:
        await query.edit_message_text("You have no orders yet.")
        return
    text = "📦 *Your Recent Orders*\n"
    for o in orders_resp.data:
        text += f"\n• {o['invoice_id']} – {o['coupon_type']} Off x{o['quantity']} – ₹{o['amount_paid']} – {o['status']}"
    await query.edit_message_text(text, parse_mode="Markdown")

# ------------------- ADMIN PANEL -------------------
async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("Access denied.")
        return
    keyboard = [
        [InlineKeyboardButton("➕ Add Coupon", callback_data="admin_add_coupon")],
        [InlineKeyboardButton("➖ Remove Coupon", callback_data="admin_remove_coupon")],
        [InlineKeyboardButton("📊 Stock", callback_data="admin_stock")],
        [InlineKeyboardButton("🎁 Get Free Code", callback_data="admin_get_free")],
        [InlineKeyboardButton("💱 Change Prices", callback_data="admin_change_prices")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("🕒 Last 10 Purchases", callback_data="admin_last10")],
        [InlineKeyboardButton("🔄 Update QR", callback_data="admin_update_qr")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("🔧 *Admin Panel*", parse_mode="Markdown", reply_markup=reply_markup)

async def handle_admin_submenus(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("Access denied.")
        return

    if data == "admin_add_coupon":
        keyboard = [
            [InlineKeyboardButton("500 Off", callback_data="admin_add_500")],
            [InlineKeyboardButton("1000 Off", callback_data="admin_add_1000")],
            [InlineKeyboardButton("2000 Off", callback_data="admin_add_2000")],
            [InlineKeyboardButton("4000 Off", callback_data="admin_add_4000")],
            [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Select coupon type to add:", reply_markup=reply_markup)
        return ADMIN_ADD_COUPON_TYPE
    elif data.startswith("admin_add_"):
        ctype = data.replace("admin_add_", "")
        context.user_data["admin_coupon_type"] = ctype
        await query.edit_message_text(f"Send me the codes for {ctype} Off (one per line):")
        return ADMIN_ADD_COUPON_DATA

    elif data == "admin_remove_coupon":
        keyboard = [
            [InlineKeyboardButton("500 Off", callback_data="admin_remove_500")],
            [InlineKeyboardButton("1000 Off", callback_data="admin_remove_1000")],
            [InlineKeyboardButton("2000 Off", callback_data="admin_remove_2000")],
            [InlineKeyboardButton("4000 Off", callback_data="admin_remove_4000")],
            [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Select coupon type to remove from:", reply_markup=reply_markup)
        return ADMIN_REMOVE_COUPON_TYPE
    elif data.startswith("admin_remove_"):
        ctype = data.replace("admin_remove_", "")
        context.user_data["admin_coupon_type"] = ctype
        await query.edit_message_text("How many codes do you want to remove? (enter number):")
        return ADMIN_REMOVE_COUPON_QTY

    elif data == "admin_stock":
        stock = {}
        for ctype in ['500', '1000', '2000', '4000']:
            resp = supabase_client.table("coupons") \
                .select("id", count="exact") \
                .eq("type", ctype) \
                .eq("used", False) \
                .execute()
            stock[ctype] = resp.count
        text = (
            "📊 *Current Stock*\n"
            f"500 Off: {stock['500']}\n"
            f"1000 Off: {stock['1000']}\n"
            f"2000 Off: {stock['2000']}\n"
            f"4000 Off: {stock['4000']}"
        )
        await query.edit_message_text(text, parse_mode="Markdown")
        return

    elif data == "admin_get_free":
        keyboard = [
            [InlineKeyboardButton("500 Off", callback_data="admin_free_500")],
            [InlineKeyboardButton("1000 Off", callback_data="admin_free_1000")],
            [InlineKeyboardButton("2000 Off", callback_data="admin_free_2000")],
            [InlineKeyboardButton("4000 Off", callback_data="admin_free_4000")],
            [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Select coupon type to get free codes:", reply_markup=reply_markup)
        return ADMIN_GET_FREE_TYPE
    elif data.startswith("admin_free_"):
        ctype = data.replace("admin_free_", "")
        context.user_data["admin_coupon_type"] = ctype
        await query.edit_message_text("How many codes do you want? (enter number):")
        return ADMIN_GET_FREE_QTY

    elif data == "admin_change_prices":
        keyboard = [
            [InlineKeyboardButton("500 Off", callback_data="admin_price_500")],
            [InlineKeyboardButton("1000 Off", callback_data="admin_price_1000")],
            [InlineKeyboardButton("2000 Off", callback_data="admin_price_2000")],
            [InlineKeyboardButton("4000 Off", callback_data="admin_price_4000")],
            [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Select coupon type to change price:", reply_markup=reply_markup)
        return ADMIN_CHANGE_PRICE_TYPE
    elif data.startswith("admin_price_"):
        ctype = data.replace("admin_price_", "")
        context.user_data["admin_coupon_type"] = ctype
        keyboard = [
            [InlineKeyboardButton("1 Qty", callback_data="admin_pricebracket_1")],
            [InlineKeyboardButton("5 Qty", callback_data="admin_pricebracket_5")],
            [InlineKeyboardButton("10 Qty", callback_data="admin_pricebracket_10")],
            [InlineKeyboardButton("20 Qty", callback_data="admin_pricebracket_20")],
            [InlineKeyboardButton("🔙 Back", callback_data="admin_change_prices")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Select quantity bracket:", reply_markup=reply_markup)
        return ADMIN_CHANGE_PRICE_QTY
    elif data.startswith("admin_pricebracket_"):
        bracket = int(data.replace("admin_pricebracket_", ""))
        context.user_data["admin_price_bracket"] = bracket
        await query.edit_message_text(f"Enter new price per code for {context.user_data['admin_coupon_type']} Off, bracket {bracket} qty (in ₹):")
        return ADMIN_CHANGE_PRICE_VALUE

    elif data == "admin_broadcast":
        await query.edit_message_text("Send the message you want to broadcast to all users:")
        return ADMIN_BROADCAST_MSG

    elif data == "admin_last10":
        orders_resp = supabase_client.table("orders") \
            .select("*, users(username, first_name)") \
            .order("order_date", desc=True) \
            .limit(10) \
            .execute()
        if not orders_resp.data:
            await query.edit_message_text("No purchases yet.")
            return
        text = "🕒 *Last 10 Purchases*\n"
        for o in orders_resp.data:
            user = o.get("users", {})
            name = user.get("first_name") or user.get("username") or "Unknown"
            text += f"\n• {o['invoice_id']} – {name} – {o['coupon_type']} x{o['quantity']} – ₹{o['amount_paid']} – {o['status']}"
        await query.edit_message_text(text, parse_mode="Markdown")

    elif data == "admin_update_qr":
        await query.edit_message_text("Send me the new QR code image:")
        return ADMIN_UPDATE_QR

# Admin message handlers for adding coupons, removing, etc.
async def admin_add_coupon_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.message.from_user.id):
        return ConversationHandler.END
    ctype = context.user_data.get("admin_coupon_type")
    codes_text = update.message.text.strip().splitlines()
    codes = [c.strip() for c in codes_text if c.strip()]
    # Insert into DB
    for code in codes:
        supabase_client.table("coupons").insert({
            "type": ctype,
            "code": code,
            "used": False
        }).execute()
    await update.message.reply_text(f"✅ {len(codes)} coupons added successfully.")
    return ConversationHandler.END

async def admin_remove_coupon_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.message.from_user.id):
        return ConversationHandler.END
    try:
        qty = int(update.message.text)
    except:
        await update.message.reply_text("Invalid number.")
        return ConversationHandler.END
    ctype = context.user_data.get("admin_coupon_type")
    # Fetch qty unused coupons of that type
    resp = supabase_client.table("coupons") \
        .select("id") \
        .eq("type", ctype) \
        .eq("used", False) \
        .limit(qty) \
        .execute()
    ids = [r["id"] for r in resp.data]
    if not ids:
        await update.message.reply_text("No unused coupons of that type.")
        return ConversationHandler.END
    # Delete them
    for id_ in ids:
        supabase_client.table("coupons").delete().eq("id", id_).execute()
    await update.message.reply_text(f"✅ Removed {len(ids)} coupons.")
    return ConversationHandler.END

async def admin_get_free_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.message.from_user.id):
        return ConversationHandler.END
    try:
        qty = int(update.message.text)
    except:
        await update.message.reply_text("Invalid number.")
        return ConversationHandler.END
    ctype = context.user_data.get("admin_coupon_type")
    # Fetch qty unused coupons
    resp = supabase_client.table("coupons") \
        .select("code") \
        .eq("type", ctype) \
        .eq("used", False) \
        .limit(qty) \
        .execute()
    codes = [r["code"] for r in resp.data]
    if not codes:
        await update.message.reply_text("No unused coupons available.")
        return ConversationHandler.END
    # Mark them as used? The spec says "set to false and true can use. false cant send by the bot". Actually ambiguous. But we'll treat getting free as admin taking codes, so they should be marked used to prevent being sold. I'll mark as used.
    for code in codes:
        supabase_client.table("coupons").update({"used": True}).eq("code", code).execute()
    codes_text = "\n".join(codes)
    await update.message.reply_text(f"Here are your free codes:\n\n{codes_text}")
    return ConversationHandler.END

async def admin_change_price_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.message.from_user.id):
        return ConversationHandler.END
    try:
        price = float(update.message.text)
    except:
        await update.message.reply_text("Invalid price.")
        return ConversationHandler.END
    ctype = context.user_data.get("admin_coupon_type")
    bracket = context.user_data.get("admin_price_bracket")
    # Update DB
    supabase_client.table("price_settings") \
        .update({"price_per_code": price}) \
        .eq("coupon_type", ctype) \
        .eq("qty_bracket", bracket) \
        .execute()
    await update.message.reply_text(f"✅ Price updated for {ctype} Off, bracket {bracket} qty to ₹{price}.")
    return ConversationHandler.END

async def admin_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.message.from_user.id):
        return ConversationHandler.END
    msg = update.message.text
    # Fetch all users
    users_resp = supabase_client.table("users").select("user_id").execute()
    count = 0
    for u in users_resp.data:
        try:
            await context.bot.send_message(u["user_id"], msg)
            count += 1
        except:
            pass
    await update.message.reply_text(f"✅ Broadcast sent to {count} users.")
    return ConversationHandler.END

async def admin_update_qr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.message.from_user.id):
        return ConversationHandler.END
    # Get the photo file_id
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        # Store as "file_id:..." prefix
        supabase_client.table("admin_settings").upsert({
            "key": "qr_code",
            "value": f"file_id:{file_id}"
        }).execute()
        await update.message.reply_text("✅ QR code updated.")
    else:
        await update.message.reply_text("Please send an image.")
    return ConversationHandler.END

# ------------------- MAIN FUNCTION -------------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Conversation handlers for buying flow (custom quantity)
    buy_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_callback, pattern="^qty_custom$")],
        states={
            BUY_CUSTOM_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, custom_quantity)],
        },
        fallbacks=[],
    )
    app.add_handler(buy_conv)

    # Admin conversation handlers
    admin_add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_admin_submenus, pattern="^admin_add_")],
        states={
            ADMIN_ADD_COUPON_DATA: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_coupon_data)],
        },
        fallbacks=[],
    )
    app.add_handler(admin_add_conv)

    admin_remove_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_admin_submenus, pattern="^admin_remove_")],
        states={
            ADMIN_REMOVE_COUPON_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_remove_coupon_qty)],
        },
        fallbacks=[],
    )
    app.add_handler(admin_remove_conv)

    admin_free_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_admin_submenus, pattern="^admin_free_")],
        states={
            ADMIN_GET_FREE_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_get_free_qty)],
        },
        fallbacks=[],
    )
    app.add_handler(admin_free_conv)

    admin_price_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_admin_submenus, pattern="^admin_pricebracket_")],
        states={
            ADMIN_CHANGE_PRICE_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_change_price_value)],
        },
        fallbacks=[],
    )
    app.add_handler(admin_price_conv)

    admin_broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_admin_submenus, pattern="^admin_broadcast$")],
        states={
            ADMIN_BROADCAST_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_message)],
        },
        fallbacks=[],
    )
    app.add_handler(admin_broadcast_conv)

    admin_qr_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_admin_submenus, pattern="^admin_update_qr$")],
        states={
            ADMIN_UPDATE_QR: [MessageHandler(filters.PHOTO, admin_update_qr)],
        },
        fallbacks=[],
    )
    app.add_handler(admin_qr_conv)

    # General callback query handler (for menu and non-stateful callbacks)
    app.add_handler(CallbackQueryHandler(menu_callback, pattern="^(?!admin_|qty_custom).*$"))

    # Start command
    app.add_handler(CommandHandler("start", start))

    # Set webhook (for Render)
    # In Render, you'll set the webhook URL via environment variable or manually.
    # For local testing, use polling. We'll include webhook setup code.
    # If running on Render, use:
    # PORT = int(os.environ.get('PORT', 8443))
    # app.run_webhook(listen="0.0.0.0", port=PORT, url_path=BOT_TOKEN, webhook_url=f"https://your-app.onrender.com/{BOT_TOKEN}")
    # For simplicity here, we'll use polling. You can adjust.

    # Start polling (for local) – replace with webhook when deploying
    app.run_polling()

if __name__ == "__main__":
    main()
