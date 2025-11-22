import logging
import os
import sys
import asyncio
import time
import random  # <--- YANGI: Ritm uchun
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import LabeledPrice, PreCheckoutQuery, ContentType, FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
import aiosqlite
from pydub import AudioSegment

# --- SOZLAMALAR (Railway Variables) ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "SIZNING_BOT_TOKEN")
PAYMENT_TOKEN = os.getenv("PAYMENT_TOKEN", "CLICK_TOKEN") 
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))
STICKER_ID = "CAACAgIAAxkBAAIB2WkiBBE0NrYUX7Hlg5uWGQwuTgABcwACZYsAAngPEUk26XnQ7yiUBTYE" # YANGI QO'SHILDI
DB_NAME = "music_bot.db"

# --- XAVFSIZLIK ---
THROTTLE_CACHE = {} 
THROTTLE_LIMIT = 15 

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

# --- ASBOBLAR VA NOTALAR ---
INSTRUMENTS_LIST = [
    # -- FREE --
    "Piano", "Guitar", "Drum", "Flute", "Bass", "Trumpet", "Violin", "Saxophone",
    # -- PLUS --
    "Cello", "Harp", "Clarinet", "Oboe",
    # -- PRO --
    "Synth", "808", "PhonkBass", "PhonkCowbell", "ElectricGuitar", "Koto", "Sitar", "Banjo",
    "Accordion", "Choir", "Strings", "Pad"
]

NOTE_MAPPING = [
    'C3', 'C#3', 'D3', 'D#3', 'E3', 'F3', 'F#3', 'G3', 'G#3', 'A3', 'A#3', 'B3', 
    'C4', 'C#4', 'D4', 'D#4', 'E4', 'F4', 'F#4', 'G4', 'G#4', 'A4', 'A#4', 'B4'
] 

# --- AUDIO ENGINE (UPDATED: PROFESSIONAL RHYTHM) ---
class AudioEngine:
    def __init__(self):
        self.base_path = "." 
        if not os.path.exists("downloads"): os.makedirs("downloads")

    def check_files_exist(self, instrument_name):
        test_file = f"{instrument_name}_C3.wav"
        test_path = os.path.join(self.base_path, test_file)
        return os.path.exists(test_path)

    def generate_track(self, original_audio, instrument_name):
        # 1. Ritmik vaqtlar ro'yxati (Millisekundlarda)
        # Qisqa, O'rta, Uzun notalar
        DURATIONS = [200, 400, 600, 800] 
        
        # Tezkor asboblar uchun qisqaroq vaqtlar
        if instrument_name in ["PhonkCowbell", "808", "Drum", "ElectricGuitar"]:
            DURATIONS = [150, 300, 450]

        avg_loudness = original_audio.rms or 1
        generated = AudioSegment.silent(duration=0)
        
        # 2. Original audioni analiz qilish (Loop)
        cursor = 0
        total_len = len(original_audio)
        
        steps = len(NOTE_MAPPING)
        ratio_step = 3.5 / steps 

        while cursor < total_len:
            # Tasodifiy uzunlikni tanlaymiz (Ritmik o'zgaruvchanlik)
            current_duration = random.choice(DURATIONS)
            
            # Audiodan shu bo'lakni kesib olamiz
            chunk = original_audio[cursor : cursor + current_duration]
            
            # Agar fayl oxiriga yetgan bo'lsa
            if len(chunk) == 0: break
            
            # Bo'lakning o'rtacha ovoz balandligi
            curr_vol = chunk.rms 
            
            # Dinamik pauza (Silence threshold)
            threshold = 0.6
            if instrument_name in ["Drum", "PhonkCowbell"]: threshold = 1.2
            elif instrument_name in ["Bass", "808"]: threshold = 0.9
            
            # Agar ovoz juda past bo'lsa, nota chalmaymiz (Pauza)
            if curr_vol < avg_loudness * threshold:
                generated += AudioSegment.silent(duration=len(chunk))
                cursor += len(chunk)
                continue

            # Nota tanlash (Ovoz balandligiga qarab)
            ratio = curr_vol / avg_loudness
            index = min(int(ratio / ratio_step), steps - 1)
            if ratio >= 3.5: index = steps - 1
            
            note_suffix = NOTE_MAPPING[index]
            sample_file = f"{instrument_name}_{note_suffix}.wav"
            sample_path = os.path.join(self.base_path, sample_file)

            if not os.path.exists(sample_path):
                 generated += AudioSegment.silent(duration=len(chunk))
                 cursor += len(chunk)
                 continue
            
            base_sample = AudioSegment.from_file(sample_path)
            
            # 3. Professional ADSR (Attack, Decay, Sustain, Release)
            # Tanlangan vaqtga (current_duration) moslab notani kesamiz
            
            note = base_sample[:current_duration]
            
            # Agar asl sempl qisqa bo'lsa, jimlik qo'shamiz (Loop qilinmaydi, tabiiyroq)
            if len(note) < current_duration:
                note += AudioSegment.silent(duration=current_duration - len(note))
            
            # Yumshatish (Fade In/Out) - "Chertish" ovozini yo'qotish uchun
            if instrument_name in ["Violin", "Cello", "Flute", "Synth", "Pad", "Strings", "Choir"]:
                # Legato (Silliq o'tish)
                note = note.fade_in(30).fade_out(50)
            elif instrument_name in ["Drum", "PhonkCowbell", "808"]:
                # Zarbli asboblar (Keskin boshlanish, tez so'nish)
                note = note.fade_out(10)
            else:
                # Piano, Guitar (Standart)
                note = note.fade_out(30)

            # 4. Yig'ish
            # Ba'zi asboblar uchun Crossfade (bir-biriga kirishib ketish) qilamiz
            if len(generated) > 50 and instrument_name in ["Violin", "Strings", "Pad"]:
                generated = generated.append(note, crossfade=50)
            else:
                generated += note
            
            cursor += len(chunk) # Keyingi bo'lakka o'tish
        
        return generated

    def process(self, input_path, instrument_name, output_path):
        try:
            if not self.check_files_exist(instrument_name):
                return "missing_files"

            original = AudioSegment.from_file(input_path)
            track = self.generate_track(original, instrument_name)
            
            # Mastering (Ovozni normallashtirish va kuchaytirish)
            track = track.normalize() + 2 
            
            track.export(output_path, format="mp3", bitrate="128k")
            return "success"
        except Exception as e:
            logging.error(f"Xato: {e}")
            return "error"

    def process_mix(self, input_path, instrument_list, output_path):
        try:
            valid_instruments = []
            for inst in instrument_list:
                if self.check_files_exist(inst):
                    valid_instruments.append(inst)
            
            if not valid_instruments: return "missing_files"

            original = AudioSegment.from_file(input_path)
            final_mix = None
            
            for inst in valid_instruments:
                track = self.generate_track(original, inst)
                track = track - 3 # Mixda shovqin bo'lmasligi uchun pasaytiramiz
                if final_mix is None: final_mix = track
                else: final_mix = final_mix.overlay(track)
            
            if final_mix:
                final_mix.export(output_path, format="mp3", bitrate="192k")
                return "success"
            return "error"
        except Exception as e:
            logging.error(f"Mix xato: {e}")
            return "error"

audio_engine = AudioEngine()

# --- DATABASE (O'zgarishsiz) ---
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                telegram_id INTEGER UNIQUE,
                username TEXT,
                status TEXT DEFAULT 'free',
                sub_end_date TEXT,
                daily_usage INTEGER DEFAULT 0,
                last_usage_date TEXT,
                referrer_id INTEGER,
                join_date TEXT,
                bonus_limit INTEGER DEFAULT 0
            )
        """)
        await db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT UNIQUE, value TEXT)")
        await db.commit()

async def get_discount():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT value FROM settings WHERE key='discount'") as cursor:
            row = await cursor.fetchone()
            return int(row[0]) if row else 0

async def set_discount_db(percent):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('discount', ?)", (str(percent),))
        await db.commit()

async def get_user(telegram_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)) as cursor:
            return await cursor.fetchone()

async def register_user(telegram_id, username, referrer_id=None):
    today = datetime.now().date().isoformat()
    async with aiosqlite.connect(DB_NAME) as db:
        try:
            await db.execute("""
                INSERT INTO users (telegram_id, username, referrer_id, join_date, last_usage_date)
                VALUES (?, ?, ?, ?, ?)
            """, (telegram_id, username, referrer_id, datetime.now().isoformat(), today))
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

async def check_user_limits(telegram_id):
    today = datetime.now().date().isoformat()
    user = await get_user(telegram_id)
    if not user: return None
    updated = False
    if user[6] != today:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET daily_usage = 0, bonus_limit = 0, last_usage_date = ? WHERE telegram_id = ?", (today, telegram_id))
            await db.commit()
        updated = True
    if user[3] in ['plus', 'pro'] and user[4]:
        if datetime.now() > datetime.fromisoformat(user[4]):
            async with aiosqlite.connect(DB_NAME) as db:
                await db.execute("UPDATE users SET status = 'free', sub_end_date = NULL WHERE telegram_id = ?", (telegram_id,))
                await db.commit()
            updated = True
    return await get_user(telegram_id) if updated else user

async def give_referral_bonus(user_id, action):
    user = await get_user(user_id)
    if not user or not user[7]: return
    bonus = 0
    if action == 'usage': bonus = 2
    elif action == 'plus': bonus = 8
    elif action == 'pro': bonus = 16
    if bonus > 0:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET bonus_limit = bonus_limit + ? WHERE telegram_id = ?", (bonus, user[7]))
            await db.commit()
        try: await bot.send_message(user[7], f"🎉 Do'stingiz faol! Sizga +{bonus} ta limit qo'shildi.")
        except: pass

# --- BOT SETUP ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- KEYBOARDS ---
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

# 🟢 YANGI: ADMIN KEYBOARD
def admin_kb():
    kb = ReplyKeyboardBuilder()
    kb.button(text="📈 Admin Stats")
    kb.button(text="🏷 Chegirma o'rnatish")
    kb.button(text="✉️ Xabar yuborish")
    kb.button(text="🔙 Chiqish")
    kb.adjust(2, 2)
    return kb.as_markup(resize_keyboard=True)

# --- STATES ---
class AudioState(StatesGroup):
    wait_audio = State()
    wait_instr = State()

class StudioState(StatesGroup):
    selecting = State()
    paying = State()
    waiting_audio = State()

# 🟢 YANGI: ADMIN STATES
class AdminState(StatesGroup):
    waiting_discount = State()
    waiting_broadcast = State()

# --- HANDLERS ---

@dp.message(CommandStart())
async def start(message: types.Message, command: CommandObject):
    ref = int(command.args) if command.args and command.args.isdigit() and int(command.args) != message.from_user.id else None
    await register_user(message.from_user.id, message.from_user.username, ref)
    await message.answer(f"Assalamu alaykum, {message.from_user.first_name}! 👋\n\n** ΛTOMIC ** taqdim etadi.\nOvozingizni professional musiqa asbobida chalib beramiz (24 Xromatik Nota).\n\nBoshlash uchun pastdagi tugmani bosing 👇", reply_markup=main_kb())

@dp.message(F.text == "📊 Statistika")
async def stats(message: types.Message):
    user = await check_user_limits(message.from_user.id)
    disc = await get_discount()
    disc_txt = f"\n🔥 **{disc}% CHEGIRMA ketmoqda!**" if disc > 0 else ""
    
    # Xatoni oldini olish uchun tashqarida hisoblash
    obuna_status = user[4] if user[4] else "Yo'q"
    
    text = (f"👤 **Profil:**\n🏷 Status: **{user[3].upper()}**\n🔋 Limit: {user[5]}/{LIMITS[user[3]]['daily'] + user[9]}\n⏳ Obuna: {obuna_status}\n{disc_txt}\n\n🔗 Referal: `https://t.me/{(await bot.get_me()).username}?start={message.from_user.id}`")
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

# --- ADMIN PANEL (YANGILANGAN) ---
@dp.message(Command("admin"), F.from_user.id == ADMIN_ID)
async def admin_panel(message: types.Message):
    await message.answer("🔑 **Admin Panelga xush kelibsiz!**", reply_markup=admin_kb())

@dp.message(F.text == "🔙 Chiqish", F.from_user.id == ADMIN_ID)
async def admin_exit(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Asosiy menyu:", reply_markup=main_kb())

@dp.message(F.text == "📈 Admin Stats", F.from_user.id == ADMIN_ID)
async def admin_stats(message: types.Message):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor: cnt = (await cursor.fetchone())[0]
    disc = await get_discount()
    await message.answer(f"📊 **Statistika:**\n\n👥 Jami foydalanuvchilar: **{cnt}**\n🏷 Joriy chegirma: **{disc}%**")

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
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT telegram_id FROM users") as cursor:
            async for row in cursor:
                try:
                    await message.copy_to(row[0])
                    count += 1
                    await asyncio.sleep(0.05)
                except: pass
    
    await message.answer(f"✅ Xabar {count} ta foydalanuvchiga yuborildi!", reply_markup=admin_kb())
    await state.clear()

# --- AUDIO PROCESS HANDLERS ---
@dp.message(F.text == "🎹 Musiqa yasash")
async def music_req(message: types.Message, state: FSMContext):
    await message.answer("Assalomu alaykum! \n   Qani, boshladik! Audio yuboring, men uni musiqa asboblarida chalib beraman. \n   Agar telegram orqali hozirni o'zida ovozli xabarni yubormoqchi bo'lsangiz [] @AtomicAudioConvertorBot [] botimiz orqali wav formatga o'girib oling  \n   Siz esa o'zingiz istagan musiqalarni yoza olasiz. \n   Plus 🌟  va  Pro 🚀 obunalari bilan yanada keng imkoniyatga ega bo'ling. \n\n\nFoydalanish qoidalari (ToU) bilan tanishing: https://t.me/Atomic_Online_Services/5", reply_markup=main_kb())
    await state.set_state(AudioState.wait_audio)

@dp.message(AudioState.wait_audio, F.content_type.in_([ContentType.AUDIO, ContentType.VOICE]))
async def get_audio_std(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    now = time.time()
    if uid in THROTTLE_CACHE and (now - THROTTLE_CACHE[uid]) < THROTTLE_LIMIT:
        wait = int(THROTTLE_LIMIT - (now - THROTTLE_CACHE[uid]))
        return await message.answer(f"✋ Shoshmang do'stim, yana {wait} soniya kuting.")
    THROTTLE_CACHE[uid] = now

    user = await check_user_limits(uid)
    if user[5] >= (LIMITS[user[3]]['daily'] + user[9]):
        await message.answer("😔 Bugungi limit tugadi. Ertaga keling yoki obuna bo'ling! \nAytgancha, do'stingizni taklif qilsangiz ham qo'shimcha imkoniyatga ega bo'lasiz \nBepul rejimdan foydalansa sizga +2 ta \n🌟 Plus rejimdan foydalansa sizga +8 ta \n🚀 Pro rejimdan foydalansa +16 ta \n\nReferalingizni olish uchun 'Statistika' tugmasini bosing")
        await state.clear()
        return

    file_id = message.voice.file_id if message.voice else message.audio.file_id
    path = f"downloads/{file_id}.ogg"
    await bot.download(file_id, destination=path)
    await state.update_data(path=path)
    await message.answer("Qaysi asbobda chalib beray?", reply_markup=instr_kb(user[3]))
    await state.set_state(AudioState.wait_instr)

@dp.callback_query(AudioState.wait_instr, F.data.startswith("i_"))
async def process_std(call: types.CallbackQuery, state: FSMContext):
    inst = call.data.split("_")[1]
    data = await state.get_data()
    path = data['path']
    out = path.replace(".ogg", ".mp3")
    
    await call.message.edit_text(f"🎧 **{inst}** sozlanmoqda... (ΛTOMIC • Composer)")
    
    result = await asyncio.to_thread(audio_engine.process, path, inst, out)
    
    if result == "success":
        await bot.send_audio(call.from_user.id, FSInputFile(out), caption=f"🎹 Natija: {inst}")
        await bot.send_document(call.from_user.id, STICKER_ID) 
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET daily_usage = daily_usage + 1 WHERE telegram_id = ?", (call.from_user.id,))
            await db.commit()
        await give_referral_bonus(call.from_user.id, 'usage')
        try: os.remove(out)
        except: pass
    elif result == "missing_files":
        await call.message.edit_text(f"🛠 **Ushbu asbob ({inst}) fayllari serverga yuklanmoqda.**\nTez orada ishga tushadi!")
    else:
        await call.message.edit_text("❌ Texnik xatolik yuz berdi.")
    
    try: os.remove(path)
    except: pass
    await state.clear()

@dp.callback_query(AudioState.wait_instr, F.data == "locked_info")
async def locked_info(call: types.CallbackQuery):
    await call.answer("🔒 Bu asboblarni ochish uchun Plus yoki Pro obunasini oling!", show_alert=True)

# --- STUDIO (O'zgarishsiz) ---
@dp.message(F.text == "🎛 Professional Studio")
async def studio_start(message: types.Message, state: FSMContext):
    user = await check_user_limits(message.from_user.id)
    if user[3] not in ['plus', 'pro']:
        return await message.answer("🔒 Bu bo'lim faqat **Plus** va **Pro** obunachilari uchun.")
    await state.update_data(sel=[])
    await message.answer("🎛 **Professional Studio**\n6 tagacha asbob tanlang va Mix yarating.\nNarxi: 30,000 so'm.", reply_markup=studio_kb(INSTRUMENTS_LIST, []))
    await state.set_state(StudioState.selecting)

def studio_kb(all_inst, selected):
    kb = InlineKeyboardBuilder()
    for inst in all_inst:
        txt = f"✅ {inst}" if inst in selected else inst
        kb.button(text=txt, callback_data=f"mix_{inst}")
    kb.button(text=f"Davom etish ({len(selected)}/6) ➡️", callback_data="mix_done")
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
        await bot.send_invoice(call.message.chat.id, "Professional Mix", "Studio xizmati", "pay_studio", PAYMENT_TOKEN, "UZS", [LabeledPrice(label="Xizmat", amount=PRICE_STUDIO)])
        await state.set_state(StudioState.paying)
        return

    inst = call.data.split("_")[1]
    data = await state.get_data()
    sel = data.get('sel', [])
    if inst in sel: sel.remove(inst)
    else:
        if len(sel) >= 6: return await call.answer("Maksimal 6 ta!", show_alert=True)
        sel.append(inst)
    await state.update_data(sel=sel)
    await call.message.edit_reply_markup(reply_markup=studio_kb(INSTRUMENTS_LIST, sel))

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
        await message.answer("🛠 Asbob fayllari yetishmayapti.")
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
    end = (datetime.now() + timedelta(days=31)).isoformat()
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET status = ?, sub_end_date = ? WHERE telegram_id = ?", (status, end, message.from_user.id))
        await db.commit()
    await give_referral_bonus(message.from_user.id, status)
    await message.answer(f"🎉 Tabriklayman! Siz endi **{status.upper()}** a'zosisiz!")

@dp.message(F.text == "📢 Reklama")
async def ads_handler(message: types.Message):
    await message.answer(f"Reklama bo'yicha adminga murojaat qiling: @Al_Abdul_Aziz")

@dp.message(F.text == "ℹ️ Yordam")
async def help_msg(message: types.Message):
    await message.answer("Yordam kerakmi? Botdan foydalanish juda oson 😊 \n1. 'Musiqa yasash'ni bosing \n2. Audio yuboring \n3. Musiqa asbobini tanlang \n biroz kutsangiz musiqangizni olasiz \n\nReferal havola orqali do'stlaringizni chaqirsangiz ko'proq imkoniyat olasiz! 😉 \n\n\nPlus va Pro obunasi bilan yanada keng imkoniyat: \n🆓 Bepul bilan kuniga 8 ta 20 soniyadan ko'p bo'lmagan auidolarni 8 xil musiqa asbobida yarating \n🌟 Plus bilan atigi 24 000 uzs (yigirma to'rt ming o'zbek so'mi) evaziga 24 ta 120 soniyagacha bo'lgan audiolarni 12 xil musiqa asbobida havaskor musiqachilardek yarating \n🚀 Pro bilan atigi 50 000 uzs (ellik ming o'zbek so'mi) evaziga 50 ta 10 daqiqagacha bo'lgan audiolarni 24 xil musiqa asbobida professionallardek yarating \n\nMashhur qo'shiqchi bo'lib ketsangiz bizni eslab qo'ysangiz kifoya 😇")

async def main():
    await init_db()
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
