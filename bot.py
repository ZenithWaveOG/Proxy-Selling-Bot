import os
import logging
import random
import string
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from supabase import create_client, Client

# ==================== CONFIG ====================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ADMIN_IDS = [8778422236]  # Replace with your Telegram user ID

# Initialize Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Setup logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ==================== DATABASE SETUP ====================
# Run these SQL commands in Supabase SQL editor:

"""
-- Users table
CREATE TABLE users (
    user_id BIGINT PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    joined_at TIMESTAMP DEFAULT NOW()
);

-- Coupons table
CREATE TABLE coupons (
    id SERIAL PRIMARY KEY,
    code TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('500','1000','2000','4000')),
    is_used BOOLEAN DEFAULT FALSE,
    used_by BIGINT REFERENCES users(user_id),
    used_at TIMESTAMP
);

-- Orders table
CREATE TABLE orders (
    id SERIAL PRIMARY KEY,
    order_id TEXT UNIQUE NOT NULL,
    user_id BIGINT REFERENCES users(user_id),
    coupon_type TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    total_price INTEGER NOT NULL,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending','completed','declined')),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Prices table (per coupon type and quantity)
CREATE TABLE prices (
    coupon_type TEXT PRIMARY KEY,
    price_1 INTEGER NOT NULL,
    price_5 INTEGER NOT NULL,
    price_10 INTEGER NOT NULL,
    price_20 INTEGER NOT NULL
);
-- Insert default prices
INSERT INTO prices (coupon_type, price_1, price_5, price_10, price_20) VALUES
('500', 10, 45, 80, 150),
('1000', 20, 90, 160, 300),
('2000', 35, 160, 300, 550),
('4000', 60, 280, 520, 1000);

-- Settings table (for QR image)
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
INSERT INTO settings (key, value) VALUES ('qr_image', ''); -- store file_id or URL

-- Admin table (optional)
CREATE TABLE admins (
    user_id BIGINT PRIMARY KEY
);
INSERT INTO admins (user_id) VALUES (123456789);
"""

# ==================== CONSTANTS ====================
COUPON_TYPES = ['500', '1000', '2000', '4000']
QUANTITY_OPTIONS = [1, 5, 10, 20]

# Conversation states for custom quantity
SELECTING_COUPON_TYPE, SELECTING_QUANTITY, CUSTOM_QUANTITY = range(3)

# ==================== HELPER FUNCTIONS ====================
def get_main_menu():
    keyboard = [
        [KeyboardButton("🛒 Buy Vouchers")],
        [KeyboardButton("📦 My Orders")],
        [KeyboardButton("📜 Disclaimer")],
        [KeyboardButton("🆘 Support"), KeyboardButton("📢 Our Channels")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_agree_decline_keyboard():
    keyboard = [
        [InlineKeyboardButton("✅ Agree", callback_data="agree_terms")],
        [InlineKeyboardButton("❌ Decline", callback_data="decline_terms")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_coupon_type_keyboard():
    keyboard = []
    for ct in COUPON_TYPES:
        # Fetch price from db later, but here just label
        keyboard.append([InlineKeyboardButton(f"{ct} Off", callback_data=f"ctype_{ct}")])
    return InlineKeyboardMarkup(keyboard)

def get_quantity_keyboard(coupon_type):
    # Get prices from db to display on buttons
    prices = supabase.table('prices').select('*').eq('coupon_type', coupon_type).execute()
    if prices.data:
        p = prices.data[0]
        keyboard = [
            [InlineKeyboardButton(f"1 Qty - ₹{p['price_1']}", callback_data=f"qty_1")],
            [InlineKeyboardButton(f"5 Qty - ₹{p['price_5']}", callback_data=f"qty_5")],
            [InlineKeyboardButton(f"10 Qty - ₹{p['price_10']}", callback_data=f"qty_10")],
            [InlineKeyboardButton(f"20 Qty - ₹{p['price_20']}", callback_data=f"qty_20")],
            [InlineKeyboardButton("Custom Qty", callback_data="qty_custom")]
        ]
    else:
        # fallback
        keyboard = [[InlineKeyboardButton("Error loading prices", callback_data="error")]]
    return InlineKeyboardMarkup(keyboard)

def generate_order_id():
    return 'ORD' + ''.join(random.choices(string.digits, k=14))

def get_admin_panel_keyboard():
    keyboard = [
        [InlineKeyboardButton("➕ Add Coupon", callback_data="admin_add")],
        [InlineKeyboardButton("➖ Remove Coupon", callback_data="admin_remove")],
        [InlineKeyboardButton("📊 Stock", callback_data="admin_stock")],
        [InlineKeyboardButton("🎁 Get Free Code", callback_data="admin_free")],
        [InlineKeyboardButton("💰 Change Prices", callback_data="admin_prices")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("🕒 Last 10 Purchases", callback_data="admin_last10")],
        [InlineKeyboardButton("🖼 Update QR", callback_data="admin_qr")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_coupon_type_admin_keyboard(action):
    # action: 'add', 'remove', 'free', 'prices'
    keyboard = []
    for ct in COUPON_TYPES:
        keyboard.append([InlineKeyboardButton(f"{ct} Off", callback_data=f"admin_{action}_{ct}")])
    return InlineKeyboardMarkup(keyboard)

# ==================== HANDLERS ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # Save user to db if not exists
    supabase.table('users').upsert({
        'user_id': user.id,
        'username': user.username,
        'first_name': user.first_name
    }).execute()
    
    # Show stock and welcome
    stock_msg = "✏️ PROXY CODE SHOP\n━━━━━━━━━━━━━━\n📊 Current Stock\n\n"
    for ct in COUPON_TYPES:
        count = supabase.table('coupons').select('*', count='exact').eq('type', ct).eq('is_used', False).execute()
        stock = count.count if hasattr(count, 'count') else 0
        price = supabase.table('prices').select('price_1').eq('coupon_type', ct).execute()
        price_val = price.data[0]['price_1'] if price.data else 'N/A'
        stock_msg += f"▫️ {ct} Off: {stock} left (₹{price_val})\n"
    
    await update.message.reply_text(stock_msg, reply_markup=get_main_menu())

async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "🛒 Buy Vouchers":
        terms = (
            "1. Once coupon is delivered, no returns or refunds will be accepted.\n"
            "2. All coupons are fresh and valid.\n"
            "3. All sales are final. No refunds, no replacements.\n"
            "4. If coupon shows redeemed, try after some time (10-15 min).\n"
            "5. If there is a genuine issue and you recorded full screen from payment to applying, you can contact support."
        )
        await update.message.reply_text(terms, reply_markup=get_agree_decline_keyboard())
    elif text == "📦 My Orders":
        orders = supabase.table('orders').select('*').eq('user_id', update.effective_user.id).order('created_at', desc=True).limit(10).execute()
        if not orders.data:
            await update.message.reply_text("You have no orders yet.")
        else:
            msg = "Your last orders:\n"
            for o in orders.data:
                msg += f"Order {o['order_id']}: {o['coupon_type']} x{o['quantity']} - {o['status']}\n"
            await update.message.reply_text(msg)
    elif text == "📜 Disclaimer":
        disclaimer = (
            "1. 🕒 IF CODE SHOW REDEEMED: Wait For 12–13 min Because All Codes Are Checked Before We Add.\n"
            "2. 📦 ELIGIBILITY: Valid only for SHEINVERSE: https://www.sheinindia.in/c/sverse-5939-37961\n"
            "3. ⚡️ DELIVERY: codes are delivered immediately after payment confirmation.\n"
            "4. 🚫 NO REFUNDS: All sales final. No refunds/replacements for any codes.\n"
            "5. ❌ SUPPORT: For issues, a full screen-record from purchase to application is required."
        )
        await update.message.reply_text(disclaimer)
    elif text == "🆘 Support":
        await update.message.reply_text("🆘 Support Contact:\n━━━━━━━━━━━━━━\n@ProxySupportChat_bot")
    elif text == "📢 Our Channels":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("@PROXY_LOOTERS", url="https://t.me/PROXY_LOOTERS")]
        ])
        await update.message.reply_text("📢 Join our official channels for updates and deals:", reply_markup=keyboard)
    else:
        await update.message.reply_text("Use the menu buttons.")

async def terms_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "agree_terms":
        await query.edit_message_text("🛒 Select a coupon type:", reply_markup=get_coupon_type_keyboard())
    else:
        await query.edit_message_text("Thanks for using the bot. Goodbye!")

async def coupon_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctype = query.data.split('_')[1]
    context.user_data['coupon_type'] = ctype
    
    # Get stock
    count = supabase.table('coupons').select('*', count='exact').eq('type', ctype).eq('is_used', False).execute()
    stock = count.count if hasattr(count, 'count') else 0
    await query.edit_message_text(
        f"🏷️ {ctype} Off\n📦 Available stock: {stock}\n\n📋 Available Packages (per-code):",
        reply_markup=get_quantity_keyboard(ctype)
    )

async def quantity_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "qty_custom":
        await query.edit_message_text("Please enter the quantity (number):")
        return CUSTOM_QUANTITY
    else:
        qty = int(data.split('_')[1])
        await process_quantity(update, context, qty)
    return ConversationHandler.END

async def custom_quantity_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        qty = int(update.message.text)
        if qty <= 0:
            raise ValueError
        await process_quantity(update, context, qty)
    except:
        await update.message.reply_text("Invalid number. Please use the menu again.")
    return ConversationHandler.END

async def process_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE, qty):
    ctype = context.user_data['coupon_type']
    # Determine price tier
    prices = supabase.table('prices').select('*').eq('coupon_type', ctype).execute()
    if not prices.data:
        await (update.message or update.callback_query.message).reply_text("Price error.")
        return
    p = prices.data[0]
    if qty <= 1:
        price_per = p['price_1']
    elif qty <= 5:
        price_per = p['price_5']
    elif qty <= 10:
        price_per = p['price_10']
    else:
        price_per = p['price_20']
    total = price_per * qty
    
    # Generate order id
    order_id = generate_order_id()
    context.user_data['order_id'] = order_id
    context.user_data['qty'] = qty
    context.user_data['price_per'] = price_per
    context.user_data['total'] = total
    
    # Save order as pending
    supabase.table('orders').insert({
        'order_id': order_id,
        'user_id': update.effective_user.id,
        'coupon_type': ctype,
        'quantity': qty,
        'total_price': total,
        'status': 'pending'
    }).execute()
    
    # Get QR image from settings
    qr_setting = supabase.table('settings').select('value').eq('key', 'qr_image').execute()
    qr_file_id = qr_setting.data[0]['value'] if qr_setting.data and qr_setting.data[0]['value'] else None
    
    invoice_text = (
        f"🧾 INVOICE\n━━━━━━━━━━━━━━\n"
        f"🆔 {order_id}\n"
        f"📦 {ctype} Off (x{qty})\n"
        f"💰 Pay Exactly: ₹{total}\n"
        f"⚠️ CRITICAL: You MUST pay exact amount. Do not ignore the paise (decimals), or the bot will NOT find your payment!\n\n"
        f"⏳ QR valid for 10 minutes."
    )
    
    if qr_file_id:
        await (update.message or update.callback_query.message).reply_photo(photo=qr_file_id, caption=invoice_text)
    else:
        await (update.message or update.callback_query.message).reply_text(invoice_text + "\n\n(QR not set by admin yet)")
    
    verify_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Verify Payment", callback_data=f"verify_{order_id}")]])
    await (update.message or update.callback_query.message).reply_text("After payment, click Verify.", reply_markup=verify_keyboard)

async def verify_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    order_id = query.data.split('_')[1]
    
    # Get order details
    order = supabase.table('orders').select('*').eq('order_id', order_id).execute()
    if not order.data:
        await query.edit_message_text("Order not found.")
        return
    o = order.data[0]
    
    # Forward to all admins
    admin_list = ADMIN_IDS  # or fetch from admins table
    user_mention = f"@{update.effective_user.username}" if update.effective_user.username else f"{update.effective_user.first_name}"
    admin_msg = (
        f"Payment verification requested:\n"
        f"User: {user_mention} (ID: {update.effective_user.id})\n"
        f"Order: {o['order_id']}\n"
        f"Type: {o['coupon_type']} x{o['quantity']}\n"
        f"Total: ₹{o['total_price']}\n\n"
        f"Accept or Decline?"
    )
    accept_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Accept", callback_data=f"accept_{o['order_id']}"),
         InlineKeyboardButton("❌ Decline", callback_data=f"decline_{o['order_id']}")]
    ])
    for admin_id in admin_list:
        try:
            await context.bot.send_message(admin_id, admin_msg, reply_markup=accept_keyboard)
        except:
            pass
    
    await query.edit_message_text("Verification request sent to admin. Please wait for approval.")

async def admin_accept_decline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split('_')
    action = data[0]
    order_id = data[1]
    
    order = supabase.table('orders').select('*').eq('order_id', order_id).execute()
    if not order.data:
        await query.edit_message_text("Order not found.")
        return
    o = order.data[0]
    
    if action == "accept":
        # Fetch unused coupons of the type
        coupons = supabase.table('coupons').select('*').eq('type', o['coupon_type']).eq('is_used', False).limit(o['quantity']).execute()
        if len(coupons.data) < o['quantity']:
            await query.edit_message_text("Insufficient stock!")
            return
        
        codes = [c['code'] for c in coupons.data]
        # Mark coupons as used
        for c in coupons.data:
            supabase.table('coupons').update({'is_used': True, 'used_by': o['user_id'], 'used_at': datetime.utcnow().isoformat()}).eq('id', c['id']).execute()
        
        # Update order status
        supabase.table('orders').update({'status': 'completed'}).eq('order_id', order_id).execute()
        
        # Send codes to user
        codes_text = "\n".join(codes)
        await context.bot.send_message(o['user_id'], f"✅ Payment accepted! Here are your codes:\n{codes_text}\n\nThanks for purchasing!")
        
        await query.edit_message_text(f"Order {order_id} completed. Codes sent.")
    else:
        supabase.table('orders').update({'status': 'declined'}).eq('order_id', order_id).execute()
        await context.bot.send_message(o['user_id'], "❌ Your payment has been declined by admin. If there is any issue, contact support.")
        await query.edit_message_text(f"Order {order_id} declined.")

# ==================== ADMIN HANDLERS ====================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Unauthorized.")
        return
    await update.message.reply_text("Admin Panel", reply_markup=get_admin_panel_keyboard())

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id not in ADMIN_IDS:
        await query.edit_message_text("Unauthorized.")
        return
    
    data = query.data
    if data == "admin_add":
        await query.edit_message_text("Select coupon type to add:", reply_markup=get_coupon_type_admin_keyboard('add'))
    elif data == "admin_remove":
        await query.edit_message_text("Select coupon type to remove:", reply_markup=get_coupon_type_admin_keyboard('remove'))
    elif data == "admin_stock":
        msg = "Current Stock:\n"
        for ct in COUPON_TYPES:
            count = supabase.table('coupons').select('*', count='exact').eq('type', ct).eq('is_used', False).execute()
            stock = count.count if hasattr(count, 'count') else 0
            msg += f"{ct} Off: {stock}\n"
        await query.edit_message_text(msg)
    elif data == "admin_free":
        await query.edit_message_text("Select coupon type to get free codes:", reply_markup=get_coupon_type_admin_keyboard('free'))
    elif data == "admin_prices":
        await query.edit_message_text("Select coupon type to change prices:", reply_markup=get_coupon_type_admin_keyboard('prices'))
    elif data == "admin_broadcast":
        context.user_data['broadcast'] = True
        await query.edit_message_text("Send the message you want to broadcast to all users:")
        return
    elif data == "admin_last10":
        orders = supabase.table('orders').select('*').order('created_at', desc=True).limit(10).execute()
        if not orders.data:
            await query.edit_message_text("No orders yet.")
        else:
            msg = "Last 10 purchases:\n"
            for o in orders.data:
                user = supabase.table('users').select('username').eq('user_id', o['user_id']).execute()
                username = user.data[0]['username'] if user.data else 'Unknown'
                msg += f"{o['order_id']}: {username} - {o['coupon_type']} x{o['quantity']} - {o['status']} - {o['created_at'][:19]}\n"
            await query.edit_message_text(msg)
    elif data == "admin_qr":
        context.user_data['awaiting_qr'] = True
        await query.edit_message_text("Send the new QR code image.")
        return
    
    # Handle sub-actions
    elif data.startswith('admin_add_'):
        ctype = data.split('_')[2]
        context.user_data['admin_action'] = ('add', ctype)
        await query.edit_message_text(f"Send the coupon codes for {ctype} Off (one per line):")
    elif data.startswith('admin_remove_'):
        ctype = data.split('_')[2]
        context.user_data['admin_action'] = ('remove', ctype)
        await query.edit_message_text(f"How many codes to remove from {ctype} Off? (send a number)")
    elif data.startswith('admin_free_'):
        ctype = data.split('_')[2]
        context.user_data['admin_action'] = ('free', ctype)
        await query.edit_message_text(f"How many free codes from {ctype} Off? (send a number)")
    elif data.startswith('admin_prices_'):
        ctype = data.split('_')[2]
        # Show quantity selection for price change
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("1 Qty", callback_data=f"price_qty_{ctype}_1")],
            [InlineKeyboardButton("5 Qty", callback_data=f"price_qty_{ctype}_5")],
            [InlineKeyboardButton("10 Qty", callback_data=f"price_qty_{ctype}_10")],
            [InlineKeyboardButton("20 Qty", callback_data=f"price_qty_{ctype}_20")]
        ])
        await query.edit_message_text(f"Select quantity for {ctype} Off price change:", reply_markup=keyboard)
    elif data.startswith('price_qty_'):
        parts = data.split('_')
        ctype = parts[2]
        qty = parts[3]
        context.user_data['admin_action'] = ('price', ctype, qty)
        await query.edit_message_text(f"Enter new price for {ctype} Off, {qty} Qty:")

async def admin_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    text = update.message.text
    if context.user_data.get('broadcast'):
        # Broadcast to all users
        users = supabase.table('users').select('user_id').execute()
        success = 0
        for u in users.data:
            try:
                await context.bot.send_message(u['user_id'], text)
                success += 1
            except:
                pass
        await update.message.reply_text(f"Broadcast sent to {success}/{len(users.data)} users.")
        context.user_data['broadcast'] = False
        return
    
    if context.user_data.get('awaiting_qr'):
        # They sent a photo? handle photo or file_id
        if update.message.photo:
            file_id = update.message.photo[-1].file_id
            supabase.table('settings').upsert({'key': 'qr_image', 'value': file_id}).execute()
            await update.message.reply_text("QR code updated.")
            context.user_data['awaiting_qr'] = False
        else:
            await update.message.reply_text("Please send an image.")
        return
    
    if 'admin_action' in context.user_data:
        action = context.user_data['admin_action']
        if action[0] == 'add':
            ctype = action[1]
            codes = text.strip().split('\n')
            for code in codes:
                code = code.strip()
                if code:
                    supabase.table('coupons').insert({'code': code, 'type': ctype}).execute()
            await update.message.reply_text(f"Coupons added successfully to {ctype} Off.")
            context.user_data.pop('admin_action')
        elif action[0] == 'remove':
            ctype = action[1]
            try:
                num = int(text)
                # Fetch and delete oldest 'num' unused coupons of that type
                coupons = supabase.table('coupons').select('id').eq('type', ctype).eq('is_used', False).order('id').limit(num).execute()
                ids = [c['id'] for c in coupons.data]
                if ids:
                    supabase.table('coupons').delete().in_('id', ids).execute()
                await update.message.reply_text(f"Removed {len(ids)} coupons from {ctype} Off.")
            except:
                await update.message.reply_text("Invalid number.")
            context.user_data.pop('admin_action')
        elif action[0] == 'free':
            ctype = action[1]
            try:
                num = int(text)
                coupons = supabase.table('coupons').select('code').eq('type', ctype).eq('is_used', False).limit(num).execute()
                if len(coupons.data) < num:
                    await update.message.reply_text(f"Only {len(coupons.data)} available.")
                codes = [c['code'] for c in coupons.data]
                # Mark them as used? According to spec: "in db that codes should be set to false and true can use. false cant send by the bot"
                # Actually they want to retrieve free codes and mark them as used? The spec says "set to false and true can use" confusing.
                # We'll assume free codes are given and marked as used so they are not sold again.
                for c in coupons.data:
                    supabase.table('coupons').update({'is_used': True, 'used_by': update.effective_user.id, 'used_at': datetime.utcnow().isoformat()}).eq('code', c['code']).execute()
                await update.message.reply_text(f"Here are your free codes:\n" + "\n".join(codes))
            except:
                await update.message.reply_text("Invalid number.")
            context.user_data.pop('admin_action')
        elif action[0] == 'price':
            ctype = action[1]
            qty = action[2]
            try:
                new_price = int(text)
                # Update prices table
                col = f"price_{qty}"
                supabase.table('prices').update({col: new_price}).eq('coupon_type', ctype).execute()
                await update.message.reply_text(f"Price updated for {ctype} Off, {qty} Qty: ₹{new_price}")
            except:
                await update.message.reply_text("Invalid number.")
            context.user_data.pop('admin_action')

# ==================== WEBHOOK SETUP ====================
app = Flask(__name__)

# Initialize Telegram Application
telegram_app = Application.builder().token(TELEGRAM_TOKEN).build()

# Register handlers
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_handler))
telegram_app.add_handler(CallbackQueryHandler(terms_callback, pattern="^(agree|decline)_terms$"))
telegram_app.add_handler(CallbackQueryHandler(coupon_type_callback, pattern="^ctype_"))
telegram_app.add_handler(CallbackQueryHandler(quantity_callback, pattern="^qty_"))
telegram_app.add_handler(CallbackQueryHandler(verify_payment, pattern="^verify_"))
telegram_app.add_handler(CallbackQueryHandler(admin_accept_decline, pattern="^(accept|decline)_"))
telegram_app.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))
telegram_app.add_handler(CommandHandler("admin", admin_panel))

# Conversation handler for custom quantity
conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(quantity_callback, pattern="^qty_custom$")],
    states={
        CUSTOM_QUANTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, custom_quantity_input)]
    },
    fallbacks=[]
)
telegram_app.add_handler(conv_handler)

# Admin message handler (for broadcast, add coupons etc)
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_message_handler))

# Webhook endpoint
@app.route('/webhook', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(force=True), telegram_app.bot)
    telegram_app.process_update(update)
    return 'ok', 200

@app.route('/set_webhook', methods=['GET'])
def set_webhook():
    url = request.url_root.rstrip('/') + '/webhook'
    telegram_app.bot.set_webhook(url=url)
    return f'Webhook set to {url}', 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
