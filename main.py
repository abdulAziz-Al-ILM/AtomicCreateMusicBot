import logging
import os
import sys
import asyncio
import time
import math
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart, CommandObject, BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import LabeledPrice, PreCheckoutQuery, ContentType, FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
import asyncpg
from pydub import AudioSegment

# --- SOZLAMALAR ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "SIZNING_BOT_TOKEN")
PAYMENT_TOKEN = os.getenv("PAYMENT_TOKEN", "CLICK_TOKEN") 
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:pass@host/dbname")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))
STICKER_ID = "CAACAgIAAxkBAAIB2WkiBBE0NrYUX7Hlg5uWGQwuTgABcwACZYsAAngPEUk26XnQ7yiUBTYE"
DOWNLOAD_DIR = "converts"

# --- XAVFSIZLIK ---
THROTTLE_CACHE = {} 
THROTTLE_LIMIT = 15 
FLOOD_LIMIT = 7 
FLOOD_WINDOW = 2 
BANNED_CACHE = set() 
USER_ACTIVITY = {} 

# --- LIMITLAR ---
LIMITS = {
    "free": {"duration": 20, "daily": 8, "instruments": 8},
    "plus": {"duration": 120, "daily": 24, "instruments": 12},
    "pro": {"duration": 600, "daily": 50, "instruments": 24}
}

# --- NARXLAR ---
BASE_PRICE_PLUS = 24000 * 100
BASE_PRICE_PRO = 50000 * 100
PRICE_STUDIO = 30000 * 100

# --- ASBOBLAR ---
INSTRUMENTS_LIST = [
    "Piano", "Guitar", "Drum", "Flute", "Bass", "Trumpet", "Violin", "Saxophone",
    "Cello", "Harp", "Clarinet", "Oboe",
    "Synth", "808", "PhonkBass", "PhonkCowbell", "ElectricGuitar", "Koto", "Sitar", "Banjo",
    "Accordion", "Choir", "Strings", "Pad"
]

# 24 Xromatik Nota
NOTE_MAPPING = [
    'C3', 'C#3', 'D3', 'D#3', 'E3', 'F3', 'F#3', 'G3', 'G#3', 'A3', 'A#3', 'B3', 
    'C4', 'C#4', 'D4', 'D#4', 'E4', 'F4', 'F#4', 'G4', 'G#4', 'A4', 'A#4', 'B4'
]

db_pool = None
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- 🛡️ GUARDIAN (XAVFSIZLIK) ---
class SecurityMiddleware(BaseFilter):
    async def __call__(self, message: types.Message) -> bool:
        user_id = message.from_user.id
        if user_id == ADMIN_ID: 
            return True
        if user_id in BANNED_CACHE: 
            return False
        
        now = time.time()
        user_history = USER_ACTIVITY.get(user_id, [])
        user_history = [t for t in user_history if now - t < FLOOD_WINDOW]
        user_history.append(now)
        USER_ACTIVITY[user_id] = user_history
        
        if len(user_history) > FLOOD_LIMIT:
            await block_user_attack(user_id, message.from_user.first_name)
            return False
        return True

async def block_user_attack(user_id, name):
    if user_id in BANNED_CACHE: 
        return
    BANNED_CACHE.add(user_id)
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO banned_users (telegram_id, reason) VALUES ($1, $2) ON CONFLICT DO NOTHING", 
            user_id, "Flood Attack"
        )
    try:
        await bot.send_message(ADMIN_ID, f"🛡 ALERT: {name} ({user_id}) bloklandi (Flood).")
    except: 
        pass

# --- 🧠 HUMANIZER LOGIC ---
def phase(seed: float, t: float) -> float:
    """Deterministik tebranish funksiyasi (Randomsiz)."""
    seed_int = int(seed) % 7
    freq = 0.0015 + seed_int * 0.0007
    phase_val = int(seed) % 10
    return math.sin(2 * math.pi * (freq * t + phase_val * 0.11))

def micro_variation(seed: float, t: float, scale: float) -> float:
    """Vaqt va Kuch uchun mikro-o'zgarishlarni hisoblash."""
    p1 = phase(seed, t)
    seed_int = int(seed) % 5
    p2 = math.sin(2 * math.pi * (0.0009 + seed_int * 0.0003) * t + seed * 0.07)
    return (0.6 * p1 + 0.4 * p2) * scale

# --- 🎹 AUDIO ENGINE ---
class AudioEngine:
    def __init__(self):
        self.base_path = "." 
        if not os.path.exists("downloads"): 
            os.makedirs("downloads")

        self.TIMING_MS_STRENGTH = 15.0
        self.VELOCITY_STRENGTH = 3.0
        self.notes_cache = {}

    def get_available_notes(self, instrument_name):
        """Instrumnet uchun mavjud bo'lgan notalarni topish."""
        if instrument_name in self.notes_cache:
            return self.notes_cache[instrument_name]
        
        available = []
        for note in NOTE_MAPPING:
            sample_file = f"{instrument_name}_{note}.wav"
            sample_path = os.path.join(self.base_path, sample_file)
            if os.path.exists(sample_path):
                available.append(note)
        
        self.notes_cache[instrument_name] = available
        return available

    def find_closest_note(self, target_note, available_notes):
        """Eng yaqin notani topish."""
        if not available_notes:
            return None
        
        target_idx = NOTE_MAPPING.index(target_note)
        closest = min(available_notes, key=lambda n: abs(NOTE_MAPPING.index(n) - target_idx))
        return closest

    def check_files_exist(self, instrument_name):
        """Dinamik: kamita 1 ta nota bo'lsa, true qaytaring."""
        available = self.get_available_notes(instrument_name)
        return len(available) > 0

    def generate_track(self, original_audio, instrument_name):
        """Dinamik nota tanloviga o'tkazildi."""
        available_notes = self.get_available_notes(instrument_name)
        if not available_notes:
            logging.warning(f"No notes found for {instrument_name}")
            return AudioSegment.silent(duration=len(original_audio))
        
        is_fast = instrument_name in ["PhonkCowbell", "808", "PhonkBass", "Drum", "ElectricGuitar"]
        beat_duration = 200 if is_fast else 250

        avg_loudness = max(original_audio.rms or 1, 0.1)
        silence_thresh = avg_loudness * 0.15 

        total_duration_ms = len(original_audio)
        generated = AudioSegment.silent(duration=total_duration_ms + 1000)
        
        chunks = [original_audio[i:i+beat_duration] for i in range(0, total_duration_ms, beat_duration)]
        
        steps = len(NOTE_MAPPING)
        ratio_step = 3.5 / steps 

        for i, chunk in enumerate(chunks):
            current_time_ms = i * beat_duration
            curr_vol = chunk.rms
            
            if curr_vol < silence_thresh:
                continue

            seed = float(i) + float(int(curr_vol) % 997)
            
            timing_offset = micro_variation(seed, current_time_ms, self.TIMING_MS_STRENGTH)
            actual_pos = int(current_time_ms + timing_offset)
            if actual_pos < 0: 
                actual_pos = 0

            vel_change = micro_variation(seed + 3.1, current_time_ms, self.VELOCITY_STRENGTH)

            ratio = curr_vol / avg_loudness
            index = int(ratio / ratio_step)
            index = min(max(index, 0), steps - 1)
            
            target_note = NOTE_MAPPING[index]
            actual_note = self.find_closest_note(target_note, available_notes)
            
            if not actual_note:
                continue
            
            sample_file = f"{instrument_name}_{actual_note}.wav"
            sample_path = os.path.join(self.base_path, sample_file)

            if not os.path.exists(sample_path): 
                continue
            
            base_sample = AudioSegment.from_file(sample_path)
            
            target_db = chunk.dBFS
            current_db = base_sample.dBFS
            gain = (target_db - current_db) + 2 + vel_change
            
            note = base_sample.apply_gain(gain)
            
            note_len = beat_duration + 80
            note = note[:note_len].fade_out(80)

            generated = generated.overlay(note, position=actual_pos)

        generated = generated[:total_duration_ms]
        return generated

    def process(self, input_path, instrument_name, output_path):
        try:
            if not self.check_files_exist(instrument_name):
                return "missing_files"

            original = AudioSegment.from_file(input_path)
            track = self.generate_track(original, instrument_name)
            
            track = track.normalize()
            track.export(output_path, format="mp3", bitrate="192k")
            return "success"
        except Exception as e:
            logging.error(f"Audio Engine Error: {e}", exc_info=True)
            return "error"

    def process_mix(self, input_path, instrument_list, output_path):
        """Studio Mix - 6 tadan ko'p asbob."""
        try:
            valid_instruments = [inst for inst in instrument_list if self.check_files_exist(inst)]
            if not valid_instruments: 
                return "missing_files"

            original = AudioSegment.from_file(input_path)
            final_mix = None
            
            for inst in valid_instruments:
                track = self.generate_track(original, inst)
                track = track - 3 
                if final_mix is None: 
                    final_mix = track
                else: 
                    final_mix = final_mix.overlay(track)
            
            if final_mix:
                final_mix.export(output_path, format="mp3", bitrate="320k")
                return "success"
            return "error"
        except Exception as e:
            logging.error(f"Mix Engine Error: {e}", exc_info=True)
            return "error"

audio_engine = AudioEngine()

# --- DATABASE ---
async def init_db():
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE,
                username TEXT,
                status TEXT DEFAULT 'free',
                sub_end_date TIMESTAMP,
                daily_usage INTEGER DEFAULT 0,
                last_usage_date DATE,
                referrer_id BIGINT,
                bonus_limit INTEGER DEFAULT 0
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT,
                amount INTEGER,
                date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS banned_users (
                telegram_id BIGINT PRIMARY KEY,
                reason TEXT
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT UNIQUE, 
                value TEXT
            )
        """)
    await load_banned_users()

async def load_banned_users():
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT telegram_id FROM banned_users")
        for row in rows: 
            BANNED_CACHE.add(row['telegram_id'])

async def get_discount():
    async with db_pool.acquire() as conn:
        val = await conn.fetchval("SELECT value FROM settings WHERE key='discount'")
        return int(val) if val else 0

async def set_discount_db(percent):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO settings (key, value) VALUES ('discount', $1) ON CONFLICT (key) DO UPDATE SET value = $1", 
            str(percent)
        )

async def get_user(telegram_id):
    async with db_pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE telegram_id = $1", telegram_id)

async def register_user(telegram_id, username, referrer_id=None):
    today = datetime.now().date()
    async with db_pool.acquire() as conn:
        try:
            await conn.execute(
                "INSERT INTO users (telegram_id, username, last_usage_date) VALUES ($1, $2, $3) ON CONFLICT (telegram_id) DO NOTHING",
                telegram_id, username, today
            )
            if referrer_id and referrer_id != telegram_id:
                user = await conn.fetchrow("SELECT referrer_id FROM users WHERE telegram_id = $1", telegram_id)
                if user and user['referrer_id'] is None:
                    ref_exists = await conn.fetchval("SELECT 1 FROM users WHERE telegram_id = $1", referrer_id)
                    if ref_exists:
                        await conn.execute(
                            "UPDATE users SET referrer_id = $1 WHERE telegram_id = $2", 
                            referrer_id, telegram_id
                        )
                        await give_referral_bonus(referrer_id)
        except Exception as e: 
            logging.error(f"Reg Error: {e}")

async def check_user_limits(telegram_id):
    today = datetime.now().date()
    user = await get_user(telegram_id)
    if not user: 
        return None
    
    status = user['status']
    sub_end = user['sub_end_date']
    usage = user['daily_usage']
    last_date = user['last_usage_date']
    bonus = user['bonus_limit']

    if last_date != today:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET daily_usage = 0, bonus_limit = 0, last_usage_date = $1 WHERE telegram_id = $2", 
                today, telegram_id
            )
        usage = 0
        bonus = 0

    if status in ['plus', 'pro'] and sub_end and datetime.now() > sub_end:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET status = 'free', sub_end_date = NULL WHERE telegram_id = $1", 
                telegram_id
            )
        status = 'free'
    
    return {'status': status, 'usage': usage, 'sub_end': sub_end, 'bonus': bonus}

async def update_daily_usage(telegram_id):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET daily_usage = daily_usage + 1 WHERE telegram_id = $1", 
            telegram_id
        )

async def get_total_revenue():
    async with db_pool.acquire() as conn:
        total = await conn.fetchval("SELECT SUM(amount) FROM payments")
        return (total or 0) / 100

async def give_referral_bonus(user_id):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET bonus_limit = bonus_limit + 2 WHERE telegram_id = $1", 
            user_id
        )
    try: 
        await bot.send_message(user_id, "🎁 Sizga yangi referal uchun +2 limit berildi!")
    except: 
        pass

# --- BOT KEYBOARDS ---
dp.message.filter(SecurityMiddleware())

def main_kb():
    kb = ReplyKeyboardBuilder()
    kb.button(text="🎹 Musiqa yasash")
    kb.button(text="🎛 Professional Studio")
    kb.button(text="🌟 Plus Obuna")
    kb.button(text="🚀 Pro Obuna")
    kb.button(text="📊 Statistika")
    kb.button(text="📢 Reklama")
    kb.button(text="ℹ️ Yordam")
    kb.adjust(2, 2, 2)
    return kb.as_markup(resize_keyboard=True)

def instr_kb(status):
    kb = InlineKeyboardBuilder()
    limit = LIMITS[status]['instruments']
    available = INSTRUMENTS_LIST[:limit]
    for inst in available:
        kb.button(text=f"{inst}", callback_data=f"i_{inst}")
    if status != 'pro':
        kb.button(text="🔒 Pro Asboblar...", callback_data="locked_info")
    kb.adjust(3)
    return kb.as_markup()

def studio_kb():
    kb = InlineKeyboardBuilder()
    for inst in INSTRUMENTS_LIST[:6]:
        kb.button(text=f"✓ {inst}", callback_data=f"s_select_{inst}")
    kb.button(text="✅ Ishga tushir", callback_data="s_process")
    kb.button(text="🔙 Orqaga", callback_data="s_back")
    kb.adjust(2)
    return kb.as_markup()

def admin_kb():
    kb = ReplyKeyboardBuilder()
    kb.button(text="📈 Admin Stats")
    kb.button(text="🏷 Chegirma o'rnatish")
    kb.button(text="✉️ Xabar yuborish")
    kb.button(text="🔙 Chiqish")
    kb.adjust(2, 2)
    return kb.as_markup(resize_keyboard=True)

class AudioState(StatesGroup):
    wait_audio = State()
    wait_instr = State()

class StudioState(StatesGroup):
    selecting = State()
    waiting_audio = State()

class AdminState(StatesGroup):
    waiting_discount = State()
    waiting_broadcast = State()

# --- HANDLERS ---
@dp.message(CommandStart())
async def start(message: types.Message, command: CommandObject):
    ref = int(command.args) if command.args and command.args.isdigit() and int(command.args) != message.from_user.id else None
    await register_user(message.from_user.id, message.from_user.username, ref)
    await message.answer(
        f"Assalamu alaykum, {message.from_user.first_name}! 👋\n\n"
        "**ATOMIC Music Composer** taqdim etadi.\n"
        "Ovozingizni professional musiqa asbobida chalib beramiz (24 Xromatik Nota).\n\n"
        "Boshlash uchun pastdagi tugmani bosing 👇", 
        reply_markup=main_kb()
    )

@dp.message(F.text == "📊 Statistika")
async def stats(message: types.Message):
    user = await check_user_limits(message.from_user.id)
    if not user:
        await register_user(message.from_user.id, message.from_user.username)
        user = await check_user_limits(message.from_user.id)

    disc = await get_discount()
    disc_txt = f"\n🔥 **{disc}% CHEGIRMA ketmoqda!**" if disc > 0 else ""
    
    obuna_status = user['sub_end'] if user['sub_end'] else "Yo'q"
    limit_total = LIMITS[user['status']]['daily'] + user['bonus']
    
    link = f"https://t.me/{(await bot.get_me()).username}?start={message.from_user.id}"
    
    text = (
        f"👤 **Profil:**\n"
        f"🏷 Status: **{user['status'].upper()}**\n"
        f"🔋 Limit: {user['usage']}/{limit_total}\n"
        f"⏳ Obuna: {obuna_status}\n"
        f"{disc_txt}\n\n"
        f"🔗 Referal: `{link}`"
    )
    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text.in_({"🌟 Plus Obuna", "🚀 Pro Obuna"}))
async def subscribe(message: types.Message):
    is_plus = "Plus" in message.text
    price = BASE_PRICE_PLUS if is_plus else BASE_PRICE_PRO
    title = "Plus" if is_plus else "Pro"
    payload = "sub_plus" if is_plus else "sub_pro"
    disc = await get_discount()
    final = int(price * (1 - disc / 100))
    desc = f"🎉 {disc}% Chegirma bilan!" if disc > 0 else "Bot imkoniyatlarini oshiring"
    await bot.send_invoice(
        message.chat.id, f"{title} Obuna", desc, payload, PAYMENT_TOKEN, "UZS", 
        [LabeledPrice(label="Obuna", amount=final)], start_parameter="sub"
    )

@dp.pre_checkout_query()
async def process_pre_checkout(query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(query.id, ok=True)

@dp.message(F.successful_payment)
async def payment_success(message: types.Message):
    payload = message.successful_payment.invoice_payload
    amount = message.successful_payment.total_amount / 100
    
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO payments (telegram_id, amount) VALUES ($1, $2)", 
            message.from_user.id, int(amount * 100)
        )
        
        if payload == "sub_plus":
            days = 30
            sub_end = datetime.now() + timedelta(days=days)
            await conn.execute(
                "UPDATE users SET status = 'plus', sub_end_date = $1 WHERE telegram_id = $2", 
                sub_end, message.from_user.id
            )
        elif payload == "sub_pro":
            days = 30
            sub_end = datetime.now() + timedelta(days=days)
            await conn.execute(
                "UPDATE users SET status = 'pro', sub_end_date = $1 WHERE telegram_id = $2", 
                sub_end, message.from_user.id
            )
    
    await message.answer("✅ To'lov qabul qilindi! Obunangiz faollashtirildi 🎉")

# --- ADMIN PANEL ---
@dp.message(Command("admin"), F.from_user.id == ADMIN_ID)
async def admin_panel(message: types.Message):
    await message.answer("🔑 **Admin Panelga xush kelibsiz!**", reply_markup=admin_kb())

@dp.message(F.text == "🔙 Chiqish", F.from_user.id == ADMIN_ID)
async def admin_exit(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Asosiy menyu:", reply_markup=main_kb())

@dp.message(F.text == "📈 Admin Stats", F.from_user.id == ADMIN_ID)
async def admin_stats(message: types.Message):
    async with db_pool.acquire() as conn:
        cnt = await conn.fetchval("SELECT COUNT(*) FROM users")
    disc = await get_discount()
    revenue = await get_total_revenue()
    await message.answer(
        f"📊 **Statistika:**\n\n"
        f"👥 Jami foydalanuvchilar: **{cnt}**\n"
        f"💰 Jami Daromad: **{revenue:,.0f} UZS**\n"
        f"🏷 Joriy chegirma: **{disc}%**"
    )

@dp.message(F.text == "🏷 Chegirma o'rnatish", F.from_user.id == ADMIN_ID)
async def admin_disc_ask(message: types.Message, state: FSMContext):
    await message.answer(
        "Chegirma foizini kiriting (0 - 100):", 
        reply_markup=ReplyKeyboardBuilder().button(text="🔙 Chiqish").as_markup(resize_keyboard=True)
    )
    await state.set_state(AdminState.waiting_discount)

@dp.message(AdminState.waiting_discount, F.from_user.id == ADMIN_ID)
async def admin_disc_set(message: types.Message, state: FSMContext):
    if message.text == "🔙 Chiqish":
        await state.clear()
        return await message.answer("Admin panel:", reply_markup=admin_kb())
    
    if message.text.isdigit():
        perc = int(message.text)
        if 0 <= perc <= 100:
            await set_discount_db(perc)
            await message.answer(f"✅ Chegirma {perc}% etib belgilandi!", reply_markup=admin_kb())
            await state.clear()
        else:
            await message.answer("0 dan 100 gacha raqam kiriting.")
    else:
        await message.answer("Faqat raqam kiriting.")

@dp.message(F.text == "✉️ Xabar yuborish", F.from_user.id == ADMIN_ID)
async def admin_cast_ask(message: types.Message, state: FSMContext):
    await message.answer(
        "Foydalanuvchilarga yuboriladigan xabarni kiriting (Matn, Rasm yoki Forward):", 
        reply_markup=ReplyKeyboardBuilder().button(text="🔙 Chiqish").as_markup(resize_keyboard=True)
    )
    await state.set_state(AdminState.waiting_broadcast)

@dp.message(AdminState.waiting_broadcast, F.from_user.id == ADMIN_ID)
async def admin_cast_send(message: types.Message, state: FSMContext):
    if message.text == "🔙 Chiqish":
        await state.clear()
        return await message.answer("Admin panel:", reply_markup=admin_kb())

    await message.answer("⏳ Xabar yuborilmoqda...")
    
    async with db_pool.acquire() as conn:
        users = await conn.fetch("SELECT telegram_id FROM users")
    
    success, failed = 0, 0
    for user in users:
        try:
            if message.text:
                await bot.send_message(user['telegram_id'], message.text)
            elif message.photo:
                await bot.send_photo(user['telegram_id'], message.photo[-1].file_id, caption=message.caption)
            success += 1
        except:
            failed += 1

    await message.answer(
        f"✅ Yuborildi: {success}\n❌ Xato: {failed}", 
        reply_markup=admin_kb()
    )
    await state.clear()

# --- MUSIQA YASASH ---
@dp.message(F.text == "🎹 Musiqa yasash")
async def music_create(message: types.Message, state: FSMContext):
    user = await check_user_limits(message.from_user.id)
    if not user:
        await register_user(message.from_user.id, message.from_user.username)
        user = await check_user_limits(message.from_user.id)

    limit_total = LIMITS[user['status']]['daily'] + user['bonus']
    if user['usage'] >= limit_total:
        await message.answer("❌ Sizning kunlik limitingiz tugadi!")
        return

    await message.answer("🎵 Audio faylni yuboring (MP3, WAV, OGG):")
    await state.set_state(AudioState.wait_audio)

@dp.message(AudioState.wait_audio, F.content_type == ContentType.AUDIO)
async def audio_received(message: types.Message, state: FSMContext):
    user = await check_user_limits(message.from_user.id)
    limit_total = LIMITS[user['status']]['daily'] + user['bonus']
    
    if user['usage'] >= limit_total:
        await message.answer("❌ Limit tugadi!")
        await state.clear()
        return

    file_info = await bot.get_file(message.audio.file_id)
    file_path = f"downloads/{message.from_user.id}_{int(time.time())}.mp3"
    os.makedirs("downloads", exist_ok=True)
    await bot.download_file(file_info.file_path, file_path)
    
    await state.update_data(audio_path=file_path)
    await message.answer("🎺 Asbobni tanlang:", reply_markup=instr_kb(user['status']))
    await state.set_state(AudioState.wait_instr)

@dp.callback_query(AudioState.wait_instr)
async def instr_selected(query: types.CallbackQuery, state: FSMContext):
    if query.data.startswith("i_"):
        instrument = query.data[2:]
        data = await state.get_data()
        audio_path = data['audio_path']
        
        await query.answer("⏳ Musiqa yaratilmoqda...")
        
        output_path = audio_path.replace(".mp3", "_result.mp3")
        result = audio_engine.process(audio_path, instrument, output_path)
        
        if result == "success":
            await query.message.answer_audio(FSInputFile(output_path))
            await update_daily_usage(query.from_user.id)
        elif result == "missing_files":
            await query.message.answer("❌ Asbob fayllar topilmadi!")
        else:
            await query.message.answer("❌ Xato yuz berdi!")
        
        await state.clear()

# --- PROFESSIONAL STUDIO ---
@dp.message(F.text == "🎛 Professional Studio")
async def studio_start(message: types.Message, state: FSMContext):
    user = await check_user_limits(message.from_user.id)
    if not user:
        await register_user(message.from_user.id, message.from_user.username)
        user = await check_user_limits(message.from_user.id)

    disc = await get_discount()
    price = int(PRICE_STUDIO * (1 - disc / 100))
    
    await message.answer(
        f"🎛 **Professional Studio Mix**\n\n"
        f"6 tadan ortiq asbob bilan musiqa yasang!\n"
        f"💰 Narx: {price // 100} UZS (har bir mix uchun)\n\n"
        f"Asboblar tanlang:", 
        reply_markup=studio_kb()
    )
    await state.set_state(StudioState.selecting)

@dp.callback_query(StudioState.selecting, F.data.startswith("s_select_"))
async def studio_select_instr(query: types.CallbackQuery, state: FSMContext):
    instrument = query.data[9:]
    data = await state.get_data()
    selected = data.get("selected_instr", [])
    
    if instrument in selected:
        selected.remove(instrument)
    else:
        selected.append(instrument)
    
    await state.update_data(selected_instr=selected)
    await query.answer(f"{instrument} {'tanlandi' if instrument in selected else 'tanlovi bekor qilindi'}")

@dp.callback_query(StudioState.selecting, F.data == "s_process")
async def studio_process(query: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = data.get("selected_instr", [])
    
    if len(selected) < 2:
        await query.answer("Kamita 2 ta asbob tanlang!")
        return
    
    user = await check_user_limits(query.from_user.id)
    disc = await get_discount()
    price = int(PRICE_STUDIO * (1 - disc / 100))
    
    await query.message.answer(
        f"💳 To'lash kerak: {price // 100} UZS\n\n"
        f"Audio faylni yuboring:"
    )
    await state.set_state(StudioState.waiting_audio)

@dp.message(StudioState.waiting_audio, F.content_type == ContentType.AUDIO)
async def studio_audio_received(message: types.Message, state: FSMContext):
    data = await state.get_data()
    selected = data.get("selected_instr", [])
    
    file_info = await bot.get_file(message.audio.file_id)
    file_path = f"downloads/{message.from_user.id}_{int(time.time())}.mp3"
    os.makedirs("downloads", exist_ok=True)
    await bot.download_file(file_info.file_path, file_path)
    
    await message.answer("⏳ Studio Mix yaratilmoqda...")
    
    output_path = file_path.replace(".mp3", "_studio_mix.mp3")
    result = audio_engine.process_mix(file_path, selected, output_path)
    
    if result == "success":
        await message.answer_audio(FSInputFile(output_path), caption="✨ Studio Mix tayyor!")
    else:
        await message.answer("❌ Xato yuz berdi!")
    
    await state.clear()

@dp.callback_query(F.data == "s_back")
async def studio_back(query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await query.message.answer("Asosiy menyu:", reply_markup=main_kb())

# --- MAIN ---
async def main():
    global db_pool
    
    logging.basicConfig(level=logging.INFO)
    
    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=5, max_size=20)
        await init_db()
        logging.info("✅ Database ulandi")
    except Exception as e:
        logging.error(f"❌ Database xatosi: {e}")
        sys.exit(1)
    
    try:
        logging.info("🤖 Bot ishga tushmoqda...")
        await dp.start_polling(bot)
    finally:
        await db_pool.close()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
