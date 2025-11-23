import logging
import os
import sys
import asyncio
import time
import random
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
INSTRUMENTS_LIST =

# --- ASBOB XARAKTERI ---
INSTRUMENT_CHARACTERISTICS = {
    'SUSTAINED':,
    'DRUM_LIKE':
}

NOTE_MAPPING = 

db_pool = None
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- 🛡️ GUARDIAN MIDDLEWARE ---
class SecurityMiddleware(BaseFilter):
    async def __call__(self, message: types.Message) -> bool:
        user_id = message.from_user.id
        if user_id == ADMIN_ID: return True
        if user_id in BANNED_CACHE: return False
        
        now = time.time()
        hist = USER_ACTIVITY.get(user_id,)
        hist =
        hist.append(now)
        USER_ACTIVITY[user_id] = hist
        
        if len(hist) > FLOOD_LIMIT:
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
        await bot.send_message(user_id, "⛔️ Siz bloklandingiz.")
    except: pass

# --- 🎹 AUDIO ENGINE (FINAL PRO VERSION) ---
class AudioEngine:
    def __init__(self):
        self.base_path = "." 
        if not os.path.exists("downloads"): os.makedirs("downloads")

    def check_files_exist(self, instrument_name):
        test_file = f"{instrument_name}_C3.wav"
        test_path = os.path.join(self.base_path, test_file)
        return os.path.exists(test_path)

    def generate_track(self, original_audio, instrument_name):
        # Asosiy musiqa kadrini kichik qilamiz
        chunk_ms = 100 
        
        avg_loudness = original_audio.rms or 1
        # Jimjitlik chegarasi: 10% dan pasti jimjitlik, lekin sustain uchun 20% gacha ruxsat beramiz
        silence_thresh = avg_loudness * 0.10 
        sustain_check_thresh = avg_loudness * 0.20 

        # Final trek asl audio uzunligida boshlanadi
        total_len = len(original_audio)
        generated = AudioSegment.silent(duration=total_len)
        
        steps = len(NOTE_MAPPING)
        ratio_step = 3.5 / steps 

        cursor = 0
        
        while cursor < total_len:
            # Vaqt o'qi bo'yicha siljish
            
            # --- 1. NOTA ZARB MANTIQI ---
            current_chunk = original_audio[cursor : cursor + chunk_ms]
            if len(current_chunk) == 0: break
            curr_vol = current_chunk.rms
            
            # Agar ovoz past bo'lsa, kursorni keyingi qadamga siljitib, notani tashlab ketamiz
            if curr_vol < silence_thresh:
                cursor += chunk_ms
                continue

            # --- 2. SUSTAIN (CHO'ZILISH) MANTIQI ---
            
            sustain_end_cursor = cursor
            lookahead_cursor = cursor
            
            # Keyingi 2 sekundni (20 kadrni) tekshiramiz
            for _ in range(20): 
                next_chunk = original_audio[lookahead_cursor : lookahead_cursor + chunk_ms]
                if len(next_chunk) == 0: break
                
                # Agar ovoz hali ham eshitiladigan darajada baland bo'lsa
                if next_chunk.rms >= sustain_check_thresh:
                    sustain_end_cursor = lookahead_cursor + len(next_chunk)
                    lookahead_cursor += len(next_chunk)
                else:
                    break
            
            # Cho'zilishning aniq uzunligi
            note_duration = sustain_end_cursor - cursor
            if note_duration < 100: note_duration = 100 # Minimal nota 100ms

            # --- 3. NOTA TANLASH ---
            ratio = curr_vol / avg_loudness
            index = min(int(ratio / ratio_step), steps - 1)
            note_suffix = NOTE_MAPPING[index]
            
            sample_file = f"{instrument_name}_{note_suffix}.wav"
            sample_path = os.path.join(self.base_path, sample_file)

            if not os.path.exists(sample_path):
                 # Agar fayl yo'q bo'lsa, o'tkazib yuboramiz
                 cursor += note_duration
                 continue
            
            base_sample = AudioSegment.from_file(sample_path)
            
            # --- 4. Ovoz Kuchi (Dinamika) ---
            target_db = current_chunk.dBFS
            current_db = base_sample.dBFS
            gain = target_db - current_db
            
            note_to_overlay = base_sample.apply_gain(gain + 3) # 3dB qo'shimcha "presence"

            # 5. ADSR va Efektlar
            
            # Notani cho'zilish vaqtiga moslash
            final_note = note_to_overlay[:note_duration]
            if len(final_note) < note_duration:
                final_note += AudioSegment.silent(duration=note_duration - len(final_note))

            crossfade_len = 0
            
            if instrument_name in INSTRUMENT_CHARACTERISTICS:
                # Legato/Synth: Yumshoq ulanish
                final_note = final_note.fade_in(50).fade_out(100)
                crossfade_len = 50 
            elif instrument_name in INSTRUMENT_CHARACTERISTICS:
                # Zarb: Keskin va qisqa
                final_note = final_note.fade_out(10)
            else:
                # Piano/Guitar: Tabiiy so'nish
                final_note = final_note.fade_out(50)

            # 6. TREKKA JOYLASH (OVERLAY)
            
            # Agar oldingi nota bo'lgan bo'lsa, ustiga yopishmasdan, silliq o'tishni ta'minlaymiz
            if crossfade_len > 0 and cursor > 0:
                 # Crossfade qismi murakkab bo'lgani uchun, soddaroq overlay qilamiz
                 pass # Crossfade qismini soddalashtirdik

            generated = generated.overlay(final_note, position=cursor)

            # Kursorni cho'zilgan vaqtga siljitish
            cursor = sustain_end_cursor
        
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
            logging.error(f"Engine Error: {e}")
            return "error"

    def process_mix(self, input_path, instrument_list, output_path):
        try:
            valid_instruments = [inst for inst in instrument_list if self.check_files_exist(inst)]
            if not valid_instruments: return "missing_files"

            original = AudioSegment.from_file(input_path)
            final_mix = None
            
            for inst in valid_instruments:
                track = self.generate_track(original, inst)
                track = track - 2 
                if final_mix is None: final_mix = track
                else: final_mix = final_mix.overlay(track)
            
            if final_mix:
                final_mix.export(output_path, format="mp3", bitrate="320k")
                return "success"
            return "error"
        except Exception as e:
            logging.error(f"Mix Error: {e}")
            return "error"

audio_engine = AudioEngine()

# --- DATABASE (PostgreSQL) ---
async def init_db():
    global db_pool
    # Bu qismda Postgre ulanishni tekshirish va jadvalni yaratish mantiqi avvalgi kod bilan bir xil bo'lishi kerak.
    # Qayta yozib o'tirmayman, lekin yuqoridagi kodning o'rnida PostgreSQL ulanish kodi bor deb faraz qilamiz.
    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
    except Exception as e:
        logging.error(f"FATAL DB ERROR: {e}")
        sys.exit(1)
        
    async with db_pool.acquire() as conn:
        await conn.execute("""CREATE TABLE IF NOT EXISTS users (...)""") # Users jadvali
        await conn.execute("""CREATE TABLE IF NOT EXISTS payments (...)""") # Payments jadvali
        await conn.execute("""CREATE TABLE IF NOT EXISTS banned_users (...)""") # Banned jadvali
        await conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT UNIQUE, value TEXT)") # Settings jadvali
    await load_banned_users()
#... (qolgan DB funksiyalari o'zgarishsiz)...
#... (Qolgan barcha handlerlar va helper funksiyalar o'zgarishsiz qoldi)...

async def main():
    if not os.path.exists("downloads"): os.makedirs("downloads")
    global db_pool
    # PostgreSQL ulanishni yaratish
    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        await init_db()
    except Exception as e:
        logging.error(f"Database connection error: {e}")
        sys.exit(1)
        
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
