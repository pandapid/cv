#!/usr/bin/env python3 """ telegram_vcf_bot.py

Telegram bot to convert contact files between TXT/CSV/Excel/VCF, split and merge VCFs, and create VCFs from plain text.

Designed to run in Termux (so it can be hosted via Redfinger Android emulator).

Dependencies: pip install python-telegram-bot==13.15 pandas openpyxl vobject python-magic

Usage:

Set environment variable TELEGRAM_BOT_TOKEN or put token in bot_token variable below.

Run: python telegram_vcf_bot.py


Features:

Accepts documents: .csv, .txt, .xlsx/.xls, .vcf

Auto-convert between formats (CSV/XLSX <-> VCF and VCF -> CSV/XLSX)

/split: split a multi-contact VCF into single-contact VCF files and return a ZIP

/merge: start a merge session, send multiple .vcf files with caption "merge", then send /finish_merge to get merged VCF

/makevcf: send plain text in simple key:value or CSV form to create a VCF


This is a single-file example intended for small/medium contact files. """

import os import tempfile import logging import zipfile import re import shutil from functools import wraps from pathlib import Path from io import BytesIO

import pandas as pd import vobject

from telegram import Update, Bot, ReplyKeyboardMarkup from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext, ConversationHandler

-------- CONFIG --------

BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')  # or paste token here

If empty, bot will refuse to start.

Memory storage for merge sessions per chat_id

merge_sessions = {}

Temporary base dir

BASE_TMP = Path(tempfile.gettempdir()) / 'tg_vcf_bot' BASE_TMP.mkdir(parents=True, exist_ok=True)

Logging

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO) logger = logging.getLogger(name)

-------- Helpers (based on prior converter) --------

def normalize_str(x): if pd.isna(x): return '' return str(x).strip()

def read_table_auto(path, sep=None, sheet_name=None): path = str(path) if path.lower().endswith(('.xls', '.xlsx')) or sheet_name is not None: return pd.read_excel(path, sheet_name=sheet_name) else: if sep is None: with open(path, 'r', encoding='utf-8', errors='ignore') as f: sample = f.read(2048) if '\t' in sample: sep = '\t' elif ';' in sample and sample.count(';') > sample.count(','): sep = ';' else: sep = ',' return pd.read_csv(path, sep=sep)

def make_vcard_from_row(row, mapping=None): mapping = mapping or {} v = vobject.vCard()

firstname = normalize_str(row.get(mapping.get('firstname', 'firstname'), row.get('firstname', '')))
lastname = normalize_str(row.get(mapping.get('lastname', 'lastname'), row.get('lastname', '')))
fullname = normalize_str(row.get(mapping.get('fullname', 'fullname'), row.get('fullname', '')))
if not fullname:
    fullname = (firstname + ' ' + lastname).strip()
if fullname:
    v.add('fn').value = fullname
try:
    n = v.add('n')
    n.value = vobject.vcard.Name(family=lastname or None, given=firstname or None)
except Exception:
    pass

# email
email = normalize_str(row.get(mapping.get('email', 'email'), row.get('email', '')))
if email:
    e = v.add('email')
    e.value = email
    e.type_param = 'INTERNET'

# phone(s)
phones = normalize_str(row.get(mapping.get('phone', 'phone'), row.get('phone', '')))
if phones:
    for p in re.split(r"[;/,|]+", phones):
        p = p.strip()
        if not p:
            continue
        t = v.add('tel')
        t.value = p
        t.type_param = 'CELL'

# org/title
org = normalize_str(row.get(mapping.get('org', 'org'), row.get('org', '')))
if org:
    o = v.add('org')
    o.value = [org]
title = normalize_str(row.get(mapping.get('title', 'title'), row.get('title', '')))
if title:
    t = v.add('title')
    t.value = title

# address
street = normalize_str(row.get(mapping.get('street', 'street'), row.get('street', '')))
city = normalize_str(row.get(mapping.get('city', 'city'), row.get('city', '')))
region = normalize_str(row.get(mapping.get('region', 'region'), row.get('region', '')))
postcode = normalize_str(row.get(mapping.get('postcode', 'postcode'), row.get('postcode', '')))
country = normalize_str(row.get(mapping.get('country', 'country'), row.get('country', '')))
if any([street, city, region, postcode, country]):
    a = v.add('adr')
    a.value = vobject.vcard.Address(box='', extended='', street=street or None, city=city or None, region=region or None, code=postcode or None, country=country or None)

return v

def df_to_vcf_file(df, outpath, mapping=None): with open(outpath, 'w', encoding='utf-8') as f: for _, row in df.iterrows(): v = make_vcard_from_row(row, mapping) f.write(v.serialize())

def vcf_to_records(vcf_path): with open(vcf_path, 'r', encoding='utf-8', errors='ignore') as f: data = f.read() comps = list(vobject.readComponents(data)) rows = [] for c in comps: r = {} try: r['fullname'] = c.fn.value if hasattr(c, 'fn') else '' except Exception: r['fullname'] = '' try: n = c.n.value r['firstname'] = getattr(n, 'given', '') or '' r['lastname'] = getattr(n, 'family', '') or '' except Exception: r['firstname'] = r['lastname'] = '' # emails emails = [] try: for e in c.contents.get('email', []): emails.append(normalize_str(getattr(e, 'value', e))) except Exception: pass r['email'] = ';'.join([e for e in emails if e]) # phones phones = [] try: for t in c.contents.get('tel', []): phones.append(normalize_str(getattr(t, 'value', t))) except Exception: pass r['phone'] = ';'.join([p for p in phones if p]) # org try: if hasattr(c, 'org'): orgv = c.org.value if isinstance(orgv, (list, tuple)): r['org'] = ' '.join(orgv) else: r['org'] = str(orgv) else: r['org'] = '' except Exception: r['org'] = '' # title try: r['title'] = c.title.value if hasattr(c, 'title') else '' except Exception: r['title'] = '' # address adr = '' try: if hasattr(c, 'adr'): adr_obj = c.adr.value adr = ', '.join([p for p in [getattr(adr_obj, 'street', ''), getattr(adr_obj, 'city', ''), getattr(adr_obj, 'region', ''), getattr(adr_obj, 'code', ''), getattr(adr_obj, 'country', '')] if p]) except Exception: adr = '' r['address'] = adr

rows.append(r)
return rows

-------- File utilities --------

def ensure_chat_tmp(chat_id): d = BASE_TMP / str(chat_id) d.mkdir(parents=True, exist_ok=True) return d

def clean_chat_tmp(chat_id): d = BASE_TMP / str(chat_id) if d.exists(): shutil.rmtree(d)

-------- Decorators --------

def require_token(func): @wraps(func) def wrapper(update: Update, context: CallbackContext): if not BOT_TOKEN: update.message.reply_text('Bot token not configured. Set TELEGRAM_BOT_TOKEN environment variable.') return return func(update, context) return wrapper

-------- Bot Handlers --------

@require_token def start(update: Update, context: CallbackContext): text = ( "Halo! Saya bot konversi kontak.\n\n" "Kirim file .csv/.txt/.xlsx/.vcf dan saya akan menawarkan opsi konversi.\n" "Perintah penting:\n" "/split - kirim file .vcf setelah /split untuk memecah per kontak (kembali sebagai ZIP)\n" "/merge - mulai sesi merge. Kirim beberapa file .vcf dengan caption 'merge' lalu kirim /finish_merge\n" "/makevcf - buat VCF dari teks (lalu kirim teks berikutnya)\n" ) update.message.reply_text(text)

@require_token def help_cmd(update: Update, context: CallbackContext): start(update, context)

@require_token def split_cmd(update: Update, context: CallbackContext): update.message.reply_text('Silakan kirim file .vcf yang ingin di-split. Saya akan membalas ZIP berisi setiap kontak sebagai file .vcf terpisah.') # set state: next vcf will be treated as split context.user_data['expect_split'] = True

@require_token def merge_cmd(update: Update, context: CallbackContext): chat_id = update.effective_chat.id merge_sessions[chat_id] = [] update.message.reply_text("Merge session dimulai. Silakan kirim file .vcf satu per pesan. Setiap file kirim dengan caption 'merge' akan ditambahkan. Ketik /finish_merge ketika selesai.")

@require_token def finish_merge_cmd(update: Update, context: CallbackContext): chat_id = update.effective_chat.id files = merge_sessions.get(chat_id, []) if not files: update.message.reply_text('Tidak ada file untuk digabung. Mulai sesi dengan /merge dan kirim file .vcf dengan caption "merge".') return # merge out = ensure_chat_tmp(chat_id) / 'merged.vcf' with open(out, 'w', encoding='utf-8') as w: for p in files: with open(p, 'r', encoding='utf-8', errors='ignore') as r: w.write(r.read()) if not r.read().endswith('\n'): w.write('\n') update.message.reply_document(document=open(out, 'rb'), filename='merged.vcf') # cleanup merge_sessions.pop(chat_id, None) clean_chat_tmp(chat_id)

@require_token def makevcf_cmd(update: Update, context: CallbackContext): update.message.reply_text('Kirim teks kontak dalam format sederhana per baris, contoh:\nName: John Doe; Phone: +628123; Email: j@example.com\nAtau CSV-like: John Doe,+628123,j@example.com\nSaya akan membuat vCard untuk setiap baris dalam pesan.') context.user_data['expect_makevcf'] = True

@require_token def document_handler(update: Update, context: CallbackContext): doc = update.message.document if not doc: update.message.reply_text('Tidak ada dokumen terdeteksi.') return chat_id = update.effective_chat.id tmp = ensure_chat_tmp(chat_id) file_name = doc.file_name file_path = tmp / file_name update.message.reply_text(f'Menerima file {file_name} — sedang mengunduh...') # download file doc.get_file().download(custom_path=str(file_path)) update.message.reply_text('File tersimpan. Memproses...')

# handle merge session: if caption contains 'merge' or session active
caption = (update.message.caption or '').lower()
if 'merge' in caption or (chat_id in merge_sessions and caption == ''):
    # only add if vcf
    if file_name.lower().endswith('.vcf'):
        merge_sessions.setdefault(chat_id, []).append(str(file_path))
        update.message.reply_text('VCF ditambahkan ke sesi merge.')
    else:
        update.message.reply_text('Hanya file .vcf diterima untuk merge.')
    return

# handle split expectation
if context.user_data.pop('expect_split', False):
    if not file_name.lower().endswith('.vcf'):
        update.message.reply_text('Untuk split, kirim file .vcf.')
        return
    # split into single vcfs
    try:
        comps = list(vobject.readComponents(file_path.read_text(encoding='utf-8', errors='ignore')))
        files = []
        for i, c in enumerate(comps, start=1):
            out = tmp / f'contact_{i}.vcf'
            with open(out, 'w', encoding='utf-8') as w:
                w.write(c.serialize())
            files.append(out)
        # make zip
        zipbuf = BytesIO()
        with zipfile.ZipFile(zipbuf, 'w') as z:
            for p in files:
                z.write(p, arcname=p.name)
        zipbuf.seek(0)
        update.message.reply_document(document=zipbuf, filename='split_contacts.zip')
    except Exception as e:
        logger.exception('Error splitting vcf')
        update.message.reply_text('Gagal memecah VCF: ' + str(e))
    finally:
        clean_chat_tmp(chat_id)
    return

# otherwise auto-detect and convert to the other formats
try:
    ext = file_name.split('.')[-1].lower()
    if ext in ['csv', 'txt']:
        # convert to vcf
        df = read_table_auto(str(file_path), sep=None)
        out_vcf = tmp / (Path(file_name).stem + '.vcf')
        df_to_vcf_file(df, out_vcf)
        update.message.reply_document(document=open(out_vcf, 'rb'), filename=out_vcf.name)
    elif ext in ['xls', 'xlsx']:
        df = read_table_auto(str(file_path), sheet_name=0)
        out_vcf = tmp / (Path(file_name).stem + '.vcf')
        df_to_vcf_file(df, out_vcf)
        update.message.reply_document(document=open(out_vcf, 'rb'), filename=out_vcf.name)
    elif ext == 'vcf':
        # produce CSV and XLSX
        rows = vcf_to_records(str(file_path))
        df = pd.DataFrame(rows)
        out_csv = tmp / (Path(file_name).stem + '.csv')
        out_xlsx = tmp / (Path(file_name).stem + '.xlsx')
        df.to_csv(out_csv, index=False)
        df.to_excel(out_xlsx, index=False)
        # if small, send both; otherwise send zip
        total_size = out_csv.stat().st_size + out_xlsx.stat().st_size
        if total_size < 15 * 1024 * 1024:
            update.message.reply_document(document=open(out_csv, 'rb'), filename=out_csv.name)
            update.message.reply_document(document=open(out_xlsx, 'rb'), filename=out_xlsx.name)
        else:
            zipbuf = BytesIO()
            with zipfile.ZipFile(zipbuf, 'w') as z:
                z.write(out_csv, arcname=out_csv.name)
                z.write(out_xlsx, arcname=out_xlsx.name)
            zipbuf.seek(0)
            update.message.reply_document(document=zipbuf, filename='vcf_converted.zip')
    else:
        update.message.reply_text('Format file tidak didukung. Gunakan .csv/.txt/.xls/.xlsx/.vcf')
except Exception as e:
    logger.exception('Error converting file')
    update.message.reply_text('Terjadi kesalahan saat memproses file: ' + str(e))
finally:
    # if not in merge session, clean
    if chat_id not in merge_sessions:
        clean_chat_tmp(chat_id)

@require_token def text_message_handler(update: Update, context: CallbackContext): chat_id = update.effective_chat.id text = update.message.text # check makevcf flow if context.user_data.pop('expect_makevcf', False): # parse lines and create vcf lines = [l.strip() for l in text.splitlines() if l.strip()] tmp = ensure_chat_tmp(chat_id) out = tmp / 'made.vcf' vcards = [] for line in lines: # try key:value pairs separated by ; or , or tab if ':' in line: # key:value; key2: val2 parts = re.split(r'[;|,]+', line) row = {} for p in parts: if ':' in p: k, v = p.split(':', 1) row[k.strip().lower()] = v.strip() v = vobject.vCard() fn = row.get('name') or row.get('fullname') or row.get('nama') if fn: v.add('fn').value = fn n = v.add('n') # attempt split name if fn and ' ' in fn: given, family = fn.split(' ', 1) n.value = vobject.vcard.Name(family=family, given=given) else: n.value = vobject.vcard.Name(family='', given=fn or '') if 'phone' in row: t = v.add('tel') t.value = row['phone'] t.type_param = 'CELL' if 'email' in row: e = v.add('email') e.value = row['email'] e.type_param = 'INTERNET' vcards.append(v) else: # try CSV-like: name,phone,email parts = [p.strip() for p in re.split(r'[;,\t]+', line) if p.strip()] if not parts: continue v = vobject.vCard() fn = parts[0] v.add('fn').value = fn n = v.add('n') if ' ' in fn: given, family = fn.split(' ', 1) n.value = vobject.vcard.Name(family=family, given=given) else: n.value = vobject.vcard.Name(family='', given=fn) if len(parts) > 1: t = v.add('tel') t.value = parts[1] t.type_param = 'CELL' if len(parts) > 2: e = v.add('email') e.value = parts[2] e.type_param = 'INTERNET' vcards.append(v) # write vcf with open(out, 'w', encoding='utf-8') as w: for v in vcards: w.write(v.serialize()) update.message.reply_document(document=open(out, 'rb'), filename='created.vcf') clean_chat_tmp(chat_id) return

# generic text: show help
update.message.reply_text('Saya menerima teks. Gunakan /makevcf untuk membuat vcf dari teks, atau kirim file kontak.')

@require_token def error_handler(update: object, context: CallbackContext): logger.error('Update caused error %s', context.error)

def main(): if not BOT_TOKEN: print('ERROR: TELEGRAM_BOT_TOKEN not set. Exiting.') return updater = Updater(token=BOT_TOKEN, use_context=True) dp = updater.dispatcher

dp.add_handler(CommandHandler('start', start))
dp.add_handler(CommandHandler('help', help_cmd))
dp.add_handler(CommandHandler('split', split_cmd))
dp.add_handler(CommandHandler('merge', merge_cmd))
dp.add_handler(CommandHandler('finish_merge', finish_merge_cmd))
dp.add_handler(CommandHandler('makevcf', makevcf_cmd))

dp.add_handler(MessageHandler(Filters.document, document_handler))
dp.add_handler(MessageHandler(Filters.text & (~Filters.command), text_message_handler))
dp.add_error_handler(error_handler)

print('Bot starting...')
updater.start_polling()
updater.idle()

if name == 'main': main()
