import logging
import os
import sys
import asyncio
import time
import math  # <--- Matematik funksiyalar uchun
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

# --- 🛡️ GUARDIAN ---
class SecurityMiddleware(BaseFilter):
    async def __call__(self, message: types.Message) -> bool:
        user_id = message.from_user.id
        if user_id == ADMIN_ID: return True
        if user_id in BANNED_CACHE: return False
        
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
    if user_id in BANNED_CACHE: return
    BANNED_CACHE.add(user_id)
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO banned_users (telegram_id, reason) VALUES ($1, $2) ON CONFLICT DO NOTHING", user_id, "Flood Attack")
    try:
        await bot.send_message(ADMIN_ID, f"🛡 ALERT: {name} ({user_id}) bloklandi (Flood).")
    except: pass

# --- 🧠 HUMANIZER LOGIC (SIZ YUBORGAN KOD ASOSIDA) ---
def phase(seed: float, t: float) -> float:
    """Deterministik tebranish funksiyasi (Randomsiz)."""
    freq = 0.0015 + (seed % 7) * 0.0007
    return math.sin(2 * math.pi * (freq * t + (seed % 10) * 0.11))

def micro_variation(seed: float, t: float, scale: float) -> float:
    """Vaqt va Kuch uchun mikro-o'zgarishlarni hisoblash."""
    p1 = phase(seed, t)
    p2 = math.sin(2 * math.pi * (0.0009 + (seed % 5) * 0.0003) * t + seed * 0.07)
    return (0.6 * p1 + 0.4 * p2) * scale

# --- 🎹 AUDIO ENGINE (HUMANIZED + WAVEFORM OVERLAY) ---
class AudioEngine:
    def __init__(self):
        self.base_path = "." 
        if not os.path.exists("downloads"): os.makedirs("downloads")

        # Konfiguratsiya (Humanize uchun)
        self.TIMING_MS_STRENGTH = 15.0  # ±15ms siljish (Juda tabiiy)
        self.VELOCITY_STRENGTH = 3.0    # ±3dB ovoz o'zgarishi

    def check_files_exist(self, instrument_name):
        test_file = f"{instrument_name}_C3.wav"
        test_path = os.path.join(self.base_path, test_file)
        return os.path.exists(test_path)

    def generate_track(self, original_audio, instrument_name):
        # 1. Asosiy parametrlar
        is_fast = instrument_name in ["PhonkCowbell", "808", "PhonkBass", "Drum", "ElectricGuitar"]
        beat_duration = 200 if is_fast else 250 # BPM

        avg_loudness = original_audio.rms or 1
        silence_thresh = avg_loudness * 0.15 

        # 🔴 MUHIM: Bo'sh polotno asl audio uzunligida yaratiladi
        # append() ishlatilmaydi, overlay() ishlatiladi -> Vaqt buzilmaydi
        total_duration_ms = len(original_audio)
        generated = AudioSegment.silent(duration=total_duration_ms + 1000) # +1s dum qismi uchun
        
        # Audioni bo'laklarga bo'lamiz
        chunks = [original_audio[i:i+beat_duration] for i in range(0, total_duration_ms, beat_duration)]
        
        steps = len(NOTE_MAPPING)
        ratio_step = 3.5 / steps 

        prev_note_suffix = None
        prev_sample = None

        for i, chunk in enumerate(chunks):
            # Hozirgi aniq vaqt (ms)
            current_time_ms = i * beat_duration
            
            curr_vol = chunk.rms
            
            # Agar jimjitlik bo'lsa -> o'tkazib yuboramiz (Overlayda shundoq ham jimjitlik bor)
            if curr_vol < silence_thresh:
                prev_note_suffix = None
                continue

            # --- HUMANIZATION (Tiriklik) ---
            # Seed yaratamiz (Har bir vaqt uchun unikal raqam)
            seed = i * 1.0 + (curr_vol % 997)
            
            # 1. Timing (Vaqtni siljitish)
            # Notani robot kabi to'ppa-to'g'ri 0ms da emas, sal oldinroq yoki keyinroq chalamiz
            timing_offset = micro_variation(seed, current_time_ms, self.TIMING_MS_STRENGTH)
            actual_pos = int(current_time_ms + timing_offset)
            if actual_pos < 0: actual_pos = 0

            # 2. Velocity (Ovoz kuchi)
            # Ovozni ham ozgina o'ynatamiz
            vel_change = micro_variation(seed + 3.1, current_time_ms, self.VELOCITY_STRENGTH)

            # --- NOTA TANLASH ---
            ratio = curr_vol / avg_loudness
            index = min(int(ratio / ratio_step), steps - 1)
            if ratio >= 3.5: index = steps - 1
            note_suffix = NOTE_MAPPING[index]
            
            # Faylni yuklash
            sample_file = f"{instrument_name}_{note_suffix}.wav"
            sample_path = os.path.join(self.base_path, sample_file)

            if not os.path.exists(sample_path): continue
            
            base_sample = AudioSegment.from_file(sample_path)
            
            # --- SUSTAIN VA ADSR ---
            # Agar nota takrorlansa, uni cho'zish o'rniga, yangi "Attack" beramiz, 
            # lekin yumshoqroq (Humanize effekti uchun)
            
            # Ovozni foydalanuvchiga moslash + Humanize
            target_db = chunk.dBFS
            current_db = base_sample.dBFS
            gain = (target_db - current_db) + 2 + vel_change
            
            note = base_sample.apply_gain(gain)
            
            # Uzunlikni beat_duration ga moslash (yoki sal uzunroq qoldirish, rezonans uchun)
            # 80ms release qo'shamiz
            note_len = beat_duration + 80
            note = note[:note_len].fade_out(80)

            # --- OVERLAY (Yopishtirish) ---
            # Eng muhim qism: Biz 'append' qilmaymiz, biz aniq vaqtga (actual_pos) qo'yamiz.
            generated = generated.overlay(note, position=actual_pos)
            
            prev_note_suffix = note_suffix

        # Yakuniy qirqish (Original uzunlikkacha)
        generated = generated[:total_duration_ms]
        return generated

    def process(self, input_path, instrument_name, output_path):
        try:
            # Fayllar bormi yo'qmi tekshiramiz, lekin xato qaytarmaymiz
            # Agar fayl bo'lmasa, jimjitlik qaytadi (lekin jarayon to'xtamaydi)
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
        try:
            valid_instruments = [inst for inst in instrument_list if self.check_files_exist(inst)]
            if not valid_instruments: return "missing_files"

            original = AudioSegment.from_file(input_path)
            final_mix = None
            
            for inst in valid_instruments:
                track = self.generate_track(original, inst)
                track = track - 3 
                if final_mix is None: final_mix = track
                else: final_mix = final_mix.overlay(track)
            
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
        # Users
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
        # Payments
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT,
                amount INTEGER,
                date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Banned Users (YANGI: Saqlanib qoladigan blok)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS banned_users (
                telegram_id BIGINT PRIMARY KEY,
                reason TEXT
            )
        """)
        # Settings
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
        for row in rows: BANNED_CACHE.add(row['telegram_id'])

async def get_discount():
    async with db_pool.acquire() as conn:
        val = await conn.fetchval("SELECT value FROM settings WHERE key='discount'")
        return int(val) if val else 0

async def set_discount_db(percent):
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO settings (key, value) VALUES ('discount', $1) ON CONFLICT (key) DO UPDATE SET value = $1", str(percent))

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
                        await conn.execute("UPDATE users SET referrer_id = $1 WHERE telegram_id = $2", referrer_id, telegram_id)
                        await give_referral_bonus(referrer_id)
        except Exception as e: logging.error(f"Reg Error: {e}")

async def check_user_limits(telegram_id):
    today = datetime.now().date()
    user = await get_user(telegram_id)
    if not user: return None
    
    status = user['status']
    sub_end = user['sub_end_date']
    usage = user['daily_usage']
    last_date = user['last_usage_date']
    bonus = user['bonus_limit']

    if last_date != today:
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE users SET daily_usage = 0, bonus_limit = 0, last_usage_date = $1 WHERE telegram_id = $2", today, telegram_id)
        usage = 0
        bonus = 0

    if status in ['plus', 'pro'] and sub_end and datetime.now() > sub_end:
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE users SET status = 'free', sub_end_date = NULL WHERE telegram_id = $1", telegram_id)
        status = 'free'
    
    return {'status': status, 'usage': usage, 'sub_end': sub_end, 'bonus': bonus}

async def update_daily_usage(telegram_id):
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET daily_usage = daily_usage + 1 WHERE telegram_id = $1", telegram_id)

async def get_total_revenue():
    async with db_pool.acquire() as conn:
        total = await conn.fetchval("SELECT SUM(amount) FROM payments")
        return (total or 0) / 100

async def give_referral_bonus(user_id):
    async with db_pool.acquire() as conn:
        # Har yangi odam uchun 2 ta limit
        await conn.execute("UPDATE users SET bonus_limit = bonus_limit + 2 WHERE telegram_id = $1", user_id)
    try: await bot.send_message(user_id, "🎁 Sizga yangi referal uchun +2 limit berildi!")
    except: pass

# --- BOT HANDLERS ---
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
    paying = State()
    waiting_audio = State()

class AdminState(StatesGroup):
    waiting_discount = State()
    waiting_broadcast = State()

@dp.message(CommandStart())
async def start(message: types.Message, command: CommandObject):
    ref = int(command.args) if command.args and command.args.isdigit() and int(command.args) != message.from_user.id else None
    await register_user(message.from_user.id, message.from_user.username, ref)
    await message.answer(f"Assalamu alaykum, {message.from_user.first_name}! 👋\n\n** ΛTOMIC ** taqdim etadi.\nOvozingizni professional musiqa asbobida chalib beramiz (24 Xromatik Nota).\n\nBoshlash uchun pastdagi tugmani bosing 👇", reply_markup=main_kb())

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
    
    text = (f"👤 **Profil:**\n🏷 Status: **{user['status'].upper()}**\n🔋 Limit: {user['usage']}/{limit_total}\n⏳ Obuna: {obuna_status}\n{disc_txt}\n\n🔗 Referal: `{link}`")
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
    await bot.send_invoice(message.chat.id, f"{title} Obuna", desc, payload, PAYMENT_TOKEN, "UZS", [LabeledPrice(label="Obuna", amount=final)], start_parameter="sub")

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
    await message.answer(f"📊 **Statistika:**\n\n👥 Jami foydalanuvchilar: **{cnt}**\n💰 Jami Daromad: **{revenue:,.0f} UZS**\n🏷 Joriy chegirma: **{disc}%**")

@dp.message(F.text == "🏷 Chegirma o'rnatish", F.from_user.id == ADMIN_ID)
async def admin_disc_ask(message: types.Message, state: FSMContext):
    await message.answer("Chegirma foizini kiriting (0 - 100):", reply_markup=ReplyKeyboardBuilder().button(text="🔙 Chiqish").as_markup(resize_keyboard=True))
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
    await message.answer("Foydalanuvchilarga yuboriladigan xabarni kiriting (Matn, Rasm yoki Forward):", reply_markup=ReplyKeyboardBuilder().button(text="🔙 Chiqish").as_markup(resize_keyboard=True))
    await state.set_state(AdminState.waiting_broadcast)

@dp.message(AdminState.waiting_broadcast, F.from_user.id == ADMIN_ID)
async def admin_cast_send(message: types.Message, state: FSMContext):
    if message.text == "🔙 Chiqish":
        await state.clear()
        return await message.answer("Admin panel:", reply_markup=admin_kb())

    await message.answer("⏳ Xabar yuborilmoqda...")
    count = 0
    async with db_pool.acquire() as conn:
        users = await conn.fetch("SELECT telegram_id FROM users")
        for row in users:
            try:
                await message.copy_to(row['telegram_id'])
                count += 1
                await asyncio.sleep(0.05)
            except: pass
    
    await message.answer(f"✅ Xabar {count} ta foydalanuvchiga yuborildi!", reply_markup=admin_kb())
    await state.clear()

# --- AUDIO PROCESS HANDLERS ---
@dp.message(F.text == "🎹 Musiqa yasash")
async def music_req(message: types.Message, state: FSMContext):
    await message.answer("Assalomu alaykum! \nQani, boshladik! 🎤Ovozli xabar yoki audio yuboring, men uni musiqa asboblarida chalib beraman. \nSiz esa o'zingiz istagan musiqalarni yoza olasiz. \nPlus 🌟  va  Pro 🚀 obunalari bilan yanada keng imkoniyatga ega bo'ling. \\\n Foydalanish qoidalari (ToU) bilan tanishing: https://t.me/Atomic_Online_Services/5", reply_markup=main_kb())
    await state.set_state(AudioState.wait_audio)

# 2. KUTIB OLUVCHI HANDLER (State bo'lmasa javob beradi)
@dp.message(F.content_type.in_([ContentType.AUDIO, ContentType.VOICE]))
async def catch_audio_no_state(message: types.Message, state: FSMContext):
    curr_state = await state.get_state()
    if curr_state != AudioState.wait_audio:
        await message.reply("🛑 **Iltimos, avval pastdagi '🎹 Musiqa yasash' tugmasini bosing.**", reply_markup=main_kb())

@dp.message(AudioState.wait_audio, F.content_type.in_([ContentType.AUDIO, ContentType.VOICE]))
async def get_audio_std(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    now = time.time()
    
    if uid in THROTTLE_CACHE and (now - THROTTLE_CACHE[uid]) < THROTTLE_LIMIT:
        wait = int(THROTTLE_LIMIT - (now - THROTTLE_CACHE[uid]))
        return await message.answer(f"✋ Shoshmang do'stim, yana {wait} soniya kuting.")
    THROTTLE_CACHE[uid] = now

    user = await check_user_limits(uid)
    limit_total = LIMITS[user['status']]['daily'] + user['bonus']
    
    if user['usage'] >= limit_total:
        await message.answer("😔 Bugungi limit tugadi. Ertaga keling yoki obuna bo'ling! \nKimnidir taklif qilsangiz 1 kunlik Plus obunasiga ega bo'lasiz!")
        await state.clear()
        return

    temp_dir = "downloads"
    file_id = message.voice.file_id if message.voice else message.audio.file_id
    
    # Telegram Voice har doim OGG, Audio esa boshqa bo'lishi mumkin
    # Xavfsizlik uchun fayl nomini ID bilan saqlaymiz, lekin kengaytmani tekshiramiz
    path_in = os.path.join(temp_dir, f"{file_id}_in") 
    path_out = os.path.join(temp_dir, f"{file_id}.mp3") 
    
    try:
        await bot.download(file_id, destination=path_in)
        
        try:
            audio = AudioSegment.from_file(path_in)
        except Exception as e:
            os.remove(path_in)
            await state.clear()
            return await message.answer("❌ Audio fayl formati noto'g'ri yoki buzilgan.")

        limit_duration = LIMITS[user['status']]['duration'] * 1000
        if len(audio) > limit_duration:
             os.remove(path_in)
             await state.clear()
             limit_sec = int(limit_duration / 1000)
             return await message.answer(f"⚠️ Limit: Maksimal audio uzunligi {limit_sec} soniya.")

        await state.update_data(path_in=path_in, path_out=path_out)
        await message.answer("Qaysi asbobda chalib beray?", reply_markup=instr_kb(user['status']))
        await state.set_state(AudioState.wait_instr)

    except Exception as e:
        logging.error(f"Xato: {e}")
        await message.answer("❌ Texnik xatolik.")
        try: os.remove(path_in)
        except: pass
        await state.clear()

@dp.callback_query(AudioState.wait_instr, F.data.startswith("i_"))
async def process_std(call: types.CallbackQuery, state: FSMContext):
    inst = call.data.split("_")[1]
    data = await state.get_data()
    path_in = data['path_in']
    path_out = data['path_out']
    
    await call.message.edit_text(f"🎧 **{inst}** sozlanmoqda... (ΛTOMIC • Composer)")
    
    result = await asyncio.to_thread(audio_engine.process, path_in, inst, path_out)
    
    if result == "success":
        await bot.send_audio(call.from_user.id, FSInputFile(path_out), caption=f"Natija: {inst}")
        await update_daily_usage(call.from_user.id) 
        try: os.remove(path_out)
        except: pass
    elif result == "missing_files":
        await call.message.edit_text(f"🛠 **Ushbu asbob ({inst}) fayllari serverga yuklanmoqda.**\nTez orada ishga tushadi!")
    else:
        await call.message.edit_text("❌ Texnik xatolik yuz berdi.")
    
    try: os.remove(path_in)
    except: pass
    await state.clear()

@dp.callback_query(AudioState.wait_instr, F.data == "locked_info")
async def locked_info(call: types.CallbackQuery):
    await call.answer("🔒 Bu asboblarni ochish uchun Plus yoki Pro obunasini oling!", show_alert=True)

# --- STUDIO ---
class StudioState(StatesGroup):
    selecting = State()
    paying = State()
    waiting_audio = State()

@dp.message(F.text == "🎛 Professional Studio")
async def studio_start(message: types.Message, state: FSMContext):
    user = await check_user_limits(message.from_user.id)
    if user[3] not in ['plus', 'pro']:
        await message.answer("🔒 Bu bo'lim faqat **Plus** va **Pro** obunachilari uchun ochiq.\nBizga qo'shiling!")
        return
        
    await state.update_data(sel=[])
    await message.answer("🎛 **Professional Studio** ga xush kelibsiz!\n\nBu yerda 8 tagacha asbob tanlab, to'liq trek yaratishingiz mumkin.\nHar bir trek narxi: 30,000 so'm.\n\nQani, asboblarni tanlang:", reply_markup=studio_kb([]))
    await state.set_state(StudioState.selecting)

def studio_kb(selected):
    kb = InlineKeyboardBuilder()
    for inst in INSTRUMENTS_LIST:
        txt = f"✅ {inst}" if inst in selected else inst
        kb.button(text=txt, callback_data=f"mix_{inst}")
    kb.button(text=f"Yaratish ({len(selected)}/8) ➡️", callback_data="mix_done")
    kb.adjust(3)
    return kb.as_markup()

@dp.callback_query(StudioState.selecting, F.data.startswith("mix_"))
async def studio_sel(call: types.CallbackQuery, state: FSMContext):
    if call.data == "mix_done":
        data = await state.get_data()
        sel = data.get('sel', [])
        if not sel: return await call.answer("Asbob tanlamadingiz!", show_alert=True)
        
        await call.message.delete()
        await call.message.answer(f"🎹 Tanlandi: {', '.join(sel)}\n💰 Narxi: 30,000 so'm")
        await bot.send_invoice(call.message.chat.id, "Professional Mix", "Multitrack Studio xizmati", "pay_studio", PAYMENT_TOKEN, "UZS", [LabeledPrice(label="Xizmat", amount=PRICE_STUDIO)])
        await state.set_state(StudioState.paying)
        return

    inst = call.data.split("_")[1]
    data = await state.get_data()
    sel = data.get('sel', [])
    
    if inst in sel: sel.remove(inst)
    else:
        if len(sel) >= 8: return await call.answer("Maksimal 8 ta asbob!", show_alert=True)
        sel.append(inst)
    
    await state.update_data(sel=sel)
    await call.message.edit_reply_markup(reply_markup=studio_kb(sel))

@dp.message(F.successful_payment, StudioState.paying)
async def studio_paid(message: types.Message, state: FSMContext):
    await message.answer("✅ To'lov qabul qilindi! Audioni yuboring.")
    await state.set_state(StudioState.waiting_audio)

@dp.message(StudioState.waiting_audio, F.content_type.in_([ContentType.AUDIO, ContentType.VOICE]))
async def studio_process(message: types.Message, state: FSMContext):
    file_id = message.voice.file_id if message.voice else message.audio.file_id
    path = f"downloads/mix_{file_id}.ogg"
    out = f"downloads/mix_{file_id}.mp3"
    await bot.download(file_id, destination=path)
    
    try:
        audio = AudioSegment.from_file(path)
        if len(audio) > 200 * 1000:
             await message.answer("⚠️ Limit: 3 daqiqa 20 soniya.")
             os.remove(path); await state.clear(); return
    except: return

    data = await state.get_data()
    sel = data['sel']
    await message.answer("🎛 Mix tayyorlanmoqda...")
    
    result = await asyncio.to_thread(audio_engine.process_mix, path, sel, out)
    
    if result == "success":
        await bot.send_audio(message.chat.id, FSInputFile(out), caption="🎹 **Professional Studio Result**")
        try: os.remove(out)
        except: pass
    elif result == "missing_files":
        await message.answer("🛠 Tanlangan asboblardan birining fayllari serverda topilmadi. Tez orada yuklanadi.")
    else:
        await message.answer("❌ Texnik xatolik.")
        
    try: os.remove(path)
    except: pass
    await state.clear()

@dp.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(q.id, ok=True)

@dp.message(F.successful_payment)
async def sub_paid(message: types.Message):
    if "pay_studio" in message.successful_payment.invoice_payload: return
    status = "plus" if "sub_plus" in message.successful_payment.invoice_payload else "pro"
    end = datetime.now() + timedelta(days=31)
    amount = message.successful_payment.total_amount
    
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET status = $1, sub_end_date = $2 WHERE telegram_id = $3", status, end, message.from_user.id)
        await conn.execute("INSERT INTO payments (telegram_id, amount, date) VALUES ($1, $2, $3)", message.from_user.id, amount, datetime.now())
        
    await message.answer(f"🎉 Tabriklayman! Siz endi **{status.upper()}** a'zosisiz!")

@dp.message(F.text == "📢 Reklama")
async def ads_handler(message: types.Message):
    await message.answer(f"Reklama bo'yicha adminga murojaat qiling: @Al_Abdul_Aziz")

@dp.message(F.text == "ℹ️ Yordam")
async def help_msg(message: types.Message):
    await message.answer("Yordam kerakmi? Botdan foydalanish juda oson 😊 \n1. 'Musiqa yasash'ni bosing \n2. Audio yuboring \n3. Musiqa asbobini tanlang \n biroz kutsangiz musiqangizni olasiz \n\nReferal havola orqali do'stlaringizni chaqirsangiz ko'proq imkoniyat olasiz! 😉 \n\n\nPlus va Pro obunasi bilan yanada keng imkoniyat: \n🆓 Bepul bilan kuniga 8 ta 20 soniyadan ko'p bo'lmagan auidolarni 8 xil musiqa asbobida yarating \n🌟 Plus bilan atigi 24 000 uzs (yigirma to'rt ming o'zbek so'mi) evaziga 24 ta 120 soniyagacha bo'lgan audiolarni 12 xil musiqa asbobida havaskor musiqachilardek yarating \n🚀 Pro bilan atigi 50 000 uzs (ellik ming o'zbek so'mi) evaziga 50 ta 10 daqiqagacha bo'lgan audiolarni 24 xil musiqa asbobida professionallardek yarating \n\nMashhur qo'shiqchi bo'lib ketsangiz bizni eslab qo'ysangiz kifoya 😇")

async def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL) # Poolni yaratish
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
