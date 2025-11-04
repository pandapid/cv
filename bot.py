# -*- coding: utf-8 -*-
import os
import tempfile
from pathlib import Path
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

from converter import table_to_vcf, vcf_to_table

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN tidak ditemukan. Isi .env atau set environment variable.")

# simpan state sederhana per-user
USER_LAST_FILE = {}

SUPPORTED_IN = {".csv", ".txt", ".tsv", ".xlsx", ".vcf"}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        """Halo! Kirimkan berkas kontak (CSV/TXT/TSV/XLSX/VCF).
Setelah diunggah, pilih format tujuan yang diinginkan."""
    )


def options_for_extension(ext: str):
    ext = ext.lower()
    if ext == ".vcf":
        return [
            [InlineKeyboardButton("Ke CSV", callback_data="to_csv"),
             InlineKeyboardButton("Ke XLSX", callback_data="to_xlsx")],
            [InlineKeyboardButton("Ke TSV", callback_data="to_tsv")],
        ]
    elif ext in {".csv", ".txt", ".tsv", ".xlsx"}:
        return [[InlineKeyboardButton("Ke VCF", callback_data="to_vcf")]]
    return []

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    fname = doc.file_name or "file"
    ext = Path(fname).suffix.lower()
    if ext not in SUPPORTED_IN:
        await update.message.reply_text("Ekstensi tidak didukung. Gunakan CSV/TXT/TSV/XLSX/VCF.")
        return
    tmpdir = Path(tempfile.mkdtemp(prefix="convbot_"))
    file_path = tmpdir / fname
    file = await doc.get_file()
    await file.download_to_drive(str(file_path))

    USER_LAST_FILE[update.effective_user.id] = str(file_path)

    kb = options_for_extension(ext)
    if not kb:
        await update.message.reply_text("Tidak ada opsi konversi untuk berkas ini.")
        return
    await update.message.reply_text(
        f"Berkas diterima: {fname}
Pilih format tujuan:",
        reply_markup=InlineKeyboardMarkup(kb),
    )

async def on_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    in_path = USER_LAST_FILE.get(user_id)
    if not in_path or not os.path.exists(in_path):
        await query.edit_message_text("Tidak menemukan berkas terakhir. Kirim ulang file.")
        return
    in_ext = Path(in_path).suffix.lower()

    out_dir = Path(in_path).parent
    stem = Path(in_path).stem

    try:
        if query.data == "to_vcf" and in_ext in {".csv", ".txt", ".tsv", ".xlsx"}:
            out_path = out_dir / f"{stem}.vcf"
            delimiter = "	" if in_ext == ".tsv" else None
            table_to_vcf(in_path, str(out_path), delimiter=delimiter)
            await query.edit_message_text("Konversi selesai: mengirim VCF…")
            await context.bot.send_document(chat_id=update.effective_chat.id, document=open(out_path, "rb"), filename=out_path.name)
        elif query.data in {"to_csv", "to_xlsx", "to_tsv"} and in_ext == ".vcf":
            if query.data == "to_csv":
                out_path = out_dir / f"{stem}.csv"
            elif query.data == "to_xlsx":
                out_path = out_dir / f"{stem}.xlsx"
            else:
                out_path = out_dir / f"{stem}.tsv"
            # konversi ke CSV default, lalu jika perlu ubah ekstensi delimiter
            tmp_csv = out_dir / f"{stem}__tmp.csv"
            vcf_to_table(in_path, str(tmp_csv))
            if out_path.suffix == ".xlsx":
                # baca csv lalu tulis xlsx via openpyxl
                import csv
                from openpyxl import Workbook
                rows = []
                with open(tmp_csv, "r", encoding="utf-8", errors="ignore") as f:
                    reader = csv.reader(f)
                    rows = list(reader)
                wb = Workbook(); ws = wb.active
                for r in rows: ws.append(r)
                wb.save(out_path); wb.close()
            elif out_path.suffix == ".tsv":
                # ganti delimiter
                import csv
                with open(tmp_csv, "r", encoding="utf-8", errors="ignore") as src, open(out_path, "w", encoding="utf-8", newline="") as dst:
                    reader = csv.reader(src)
                    writer = csv.writer(dst, delimiter="	")
                    for r in reader: writer.writerow(r)
            else:
                os.replace(tmp_csv, out_path)
            if tmp_csv.exists():
                try: os.remove(tmp_csv)
                except Exception: pass
            await query.edit_message_text("Konversi selesai: mengirim berkas…")
            await context.bot.send_document(chat_id=update.effective_chat.id, document=open(out_path, "rb"), filename=out_path.name)
        else:
            await query.edit_message_text("Pilihan tidak valid untuk jenis berkas tersebut.")
    except Exception as e:
        await query.edit_message_text(f"Gagal konversi: {e}")


def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CallbackQueryHandler(on_choice))
    app.run_polling()

if __name__ == "__main__":
    main()


