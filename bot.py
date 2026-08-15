#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sqlite3, logging, threading, json, time, math, random, asyncio
from datetime import datetime, timedelta
from collections import deque, defaultdict
from typing import Dict, List, Tuple, Optional, Any
import numpy as np
import pandas as pd
import ccxt
from flask import Flask, jsonify, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from io import BytesIO
import seaborn as sns
import warnings
warnings.filterwarnings('ignore')

BOT_TOKEN = os.environ.get("BOT_TOKEN", "ВАШ_ТОКЕН_СЮДА")

EXCHANGES = {
    'binance': ccxt.binance({'enableRateLimit': True, 'options': {'defaultType': 'spot'}}),
    'bybit': ccxt.bybit({'enableRateLimit': True, 'options': {'defaultType': 'spot'}}),
    'okx': ccxt.okx({'enableRateLimit': True, 'options': {'defaultType': 'spot'}}),
    'kucoin': ccxt.kucoin({'enableRateLimit': True, 'options': {'defaultType': 'spot'}}),
    'gate': ccxt.gate({'enableRateLimit': True, 'options': {'defaultType': 'spot'}})
}

EXCHANGE_NAMES = {'binance': '🟡 Binance','bybit': '🔵 Bybit','okx': '🟢 OKX','kucoin': '🟣 KuCoin','gate': '🟠 Gate.io'}
SYMBOLS = ['BTC/USDT','ETH/USDT','SOL/USDT','BNB/USDT','XRP/USDT','ADA/USDT','DOT/USDT','LINK/USDT','MATIC/USDT','AVAX/USDT']
TIMEFRAMES = ['1m','5m','15m','30m','1h','2h','4h','6h','12h','1d']
DEFAULT_SYMBOL = 'BTC/USDT'
DEFAULT_TIMEFRAME = '15m'
DEFAULT_EXCHANGE = 'binance'

def init_db():
    conn = sqlite3.connect('crypto_bot.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS user_settings (user_id INTEGER PRIMARY KEY, exchange TEXT, symbol TEXT, timeframe TEXT, risk_pct REAL, sl_pct REAL, tp_pct REAL, auto BOOLEAN, alert_price REAL, notify_interval INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS signals_history (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, exchange TEXT, symbol TEXT, timeframe TEXT, signal TEXT, score REAL, price REAL, timestamp TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS trade_stats (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, symbol TEXT, direction TEXT, entry REAL, exit REAL, pnl REAL, pnl_percent REAL, timestamp TEXT)''')
    conn.commit()
    conn.close()
init_db()

class Indicators:
    @staticmethod
    def sma(data, period): return pd.Series(data).rolling(period).mean().values
    @staticmethod
    def ema(data, period): return pd.Series(data).ewm(span=period, adjust=False).mean().values
    @staticmethod
    def rsi(data, period=14):
        delta = pd.Series(data).diff()
        gain = (delta.where(delta > 0, 0)).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
        rs = gain / loss
        return (100 - (100 / (1 + rs))).values
    @staticmethod
    def macd(data, fast=12, slow=26, signal=9):
        ema_fast = pd.Series(data).ewm(span=fast, adjust=False).mean()
        ema_slow = pd.Series(data).ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        return macd_line.values, signal_line.values, (macd_line - signal_line).values
    @staticmethod
    def bollinger_bands(data, period=20, std_dev=2):
        sma = pd.Series(data).rolling(period).mean()
        std = pd.Series(data).rolling(period).std()
        return (sma + std_dev * std).values, sma.values, (sma - std_dev * std).values
    @staticmethod
    def atr(high, low, close, period=14):
        tr = np.maximum(high - low, np.maximum(np.abs(high - pd.Series(close).shift(1)), np.abs(low - pd.Series(close).shift(1))))
        return pd.Series(tr).rolling(period).mean().values
    @staticmethod
    def stochastic(high, low, close, k_period=14, d_period=3):
        low_min = pd.Series(low).rolling(k_period).min()
        high_max = pd.Series(high).rolling(k_period).max()
        k = 100 * (close - low_min) / (high_max - low_min)
        return k.values, pd.Series(k).rolling(d_period).mean().values
    @staticmethod
    def obv(close, volume):
        obv = np.zeros_like(close)
        for i in range(1, len(close)):
            if close[i] > close[i-1]: obv[i] = obv[i-1] + volume[i]
            elif close[i] < close[i-1]: obv[i] = obv[i-1] - volume[i]
            else: obv[i] = obv[i-1]
        return obv
    @staticmethod
    def cci(high, low, close, period=20):
        tp = (high + low + close) / 3
        sma = pd.Series(tp).rolling(period).mean()
        mad = pd.Series(tp).rolling(period).apply(lambda x: np.mean(np.abs(x - np.mean(x))))
        return ((tp - sma) / (0.015 * mad)).values
    @staticmethod
    def adx(high, low, close, period=14):
        tr = Indicators.atr(high, low, close, period)
        up_move = high - pd.Series(high).shift(1)
        down_move = pd.Series(low).shift(1) - low
        plus_dm = np.maximum(up_move, 0)
        minus_dm = np.maximum(down_move, 0)
        plus_di = 100 * pd.Series(plus_dm).rolling(period).sum() / pd.Series(tr).rolling(period).sum()
        minus_di = 100 * pd.Series(minus_dm).rolling(period).sum() / pd.Series(tr).rolling(period).sum()
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
        return pd.Series(dx).rolling(period).mean().values
    @staticmethod
    def supertrend(high, low, close, period=10, multiplier=3):
        atr_val = Indicators.atr(high, low, close, period)
        hl2 = (high + low) / 2
        upper = hl2 + multiplier * atr_val
        lower = hl2 - multiplier * atr_val
        trend = np.ones(len(close))
        for i in range(1, len(close)):
            if close[i] > upper[i-1]: trend[i] = 1
            elif close[i] < lower[i-1]: trend[i] = -1
            else: trend[i] = trend[i-1]
        return upper, lower, trend
    @staticmethod
    def pivot_points(high, low, close):
        pivot = (high[-1] + low[-1] + close[-1]) / 3
        return {'pivot': pivot, 'r1': 2*pivot - low[-1], 'r2': pivot + (high[-1] - low[-1]), 'r3': high[-1] + 2*(pivot - low[-1]), 's1': 2*pivot - high[-1], 's2': pivot - (high[-1] - low[-1]), 's3': low[-1] - 2*(high[-1] - pivot)}
    @staticmethod
    def fibonacci_levels(high, low, close):
        diff = high[-1] - low[-1]
        return {'0%': low[-1], '23.6%': low[-1] + 0.236*diff, '38.2%': low[-1] + 0.382*diff, '50%': low[-1] + 0.5*diff, '61.8%': low[-1] + 0.618*diff, '78.6%': low[-1] + 0.786*diff, '100%': high[-1]}

class MarketAnalyzer:
    def __init__(self, df):
        self.df = df
        self.close = df['close'].values
        self.high = df['high'].values
        self.low = df['low'].values
        self.volume = df['volume'].values
    def analyze(self):
        result = {}
        for p in [5,10,20,50,100,200]:
            result[f'SMA_{p}'] = Indicators.sma(self.close, p)[-1] if len(self.close) >= p else 0
        for p in [9,12,26,50,200]:
            result[f'EMA_{p}'] = Indicators.ema(self.close, p)[-1] if len(self.close) >= p else 0
        result['RSI_14'] = Indicators.rsi(self.close, 14)[-1] if len(self.close) >= 14 else 50
        macd_line, signal_line, hist = Indicators.macd(self.close)
        result['MACD'] = macd_line[-1] if len(macd_line) > 0 else 0
        result['MACD_Hist'] = hist[-1] if len(hist) > 0 else 0
        upper, middle, lower = Indicators.bollinger_bands(self.close)
        result['BB_Upper'] = upper[-1] if len(upper) > 0 else 0
        result['BB_Lower'] = lower[-1] if len(lower) > 0 else 0
        result['ATR_14'] = Indicators.atr(self.high, self.low, self.close, 14)[-1] if len(self.close) >= 14 else 0
        k, d = Indicators.stochastic(self.high, self.low, self.close)
        result['Stoch_K'] = k[-1] if len(k) > 0 else 50
        result['Stoch_D'] = d[-1] if len(d) > 0 else 50
        result['ADX_14'] = Indicators.adx(self.high, self.low, self.close, 14)[-1] if len(self.close) >= 14 else 0
        upper_st, lower_st, trend = Indicators.supertrend(self.high, self.low, self.close)
        result['SuperTrend'] = trend[-1] if len(trend) > 0 else 1
        result['Pivot'] = Indicators.pivot_points(self.high, self.low, self.close)
        result['Fibonacci'] = Indicators.fibonacci_levels(self.high, self.low, self.close)
        return result

class SignalGenerator:
    def __init__(self, df, exchange_name='binance'):
        self.df = df
        self.exchange_name = exchange_name
        self.analyzer = MarketAnalyzer(df)
        self.indicators = self.analyzer.analyze()
        self.price = df['close'].iloc[-1]
    def generate(self):
        signals = []
        score = 0.0
        rsi = self.indicators.get('RSI_14', 50)
        if rsi < 30: signals.append(('BUY', 'RSI перепродан', 0.15)); score += 0.15
        elif rsi > 70: signals.append(('SELL', 'RSI перекуплен', -0.15)); score -= 0.15
        macd_hist = self.indicators.get('MACD_Hist', 0)
        if macd_hist > 0: signals.append(('BUY', 'MACD восходящий', 0.20)); score += 0.20
        elif macd_hist < 0: signals.append(('SELL', 'MACD нисходящий', -0.20)); score -= 0.20
        bb_lower = self.indicators.get('BB_Lower', self.price * 0.97)
        bb_upper = self.indicators.get('BB_Upper', self.price * 1.03)
        if self.price < bb_lower: signals.append(('BUY', 'Цена ниже BB', 0.20)); score += 0.20
        elif self.price > bb_upper: signals.append(('SELL', 'Цена выше BB', -0.20)); score -= 0.20
        st = self.indicators.get('SuperTrend', 0)
        if st == 1: signals.append(('BUY', 'SuperTrend бычий', 0.15)); score += 0.15
        elif st == -1: signals.append(('SELL', 'SuperTrend медвежий', -0.15)); score -= 0.15
        score = np.clip(score, -1, 1)
        if score > 0.3: final_signal = 'LONG 🟢'
        elif score < -0.3: final_signal = 'SHORT 🔴'
        else: final_signal = 'NEUTRAL ⚪'
        return {'signal': final_signal, 'score': score, 'price': self.price, 'signals': signals, 'indicators': self.indicators}

async def fetch_klines(exchange_name, symbol='BTC/USDT', timeframe='15m', limit=150):
    try:
        exchange = EXCHANGES.get(exchange_name)
        if not exchange: return pd.DataFrame()
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except Exception as e:
        logging.error(f"Ошибка {exchange_name}: {e}")
        return pd.DataFrame()

def get_main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Прогноз", callback_data="forecast")],
        [InlineKeyboardButton("🌍 Сравнение бирж", callback_data="compare")],
        [InlineKeyboardButton("🔄 Сменить биржу", callback_data="change_exchange")],
        [InlineKeyboardButton("📈 График", callback_data="chart")],
        [InlineKeyboardButton("📋 История", callback_data="history")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="settings")]
    ])

def get_exchange_keyboard():
    buttons = []
    for key, name in EXCHANGE_NAMES.items():
        buttons.append([InlineKeyboardButton(name, callback_data=f"exch_{key}")])
    return InlineKeyboardMarkup(buttons)

def get_symbol_keyboard():
    buttons = []
    for sym in SYMBOLS[:10]:
        buttons.append([InlineKeyboardButton(sym, callback_data=f"sym_{sym}")])
    return InlineKeyboardMarkup(buttons)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect('crypto_bot.db')
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO user_settings (user_id, exchange, symbol, timeframe, risk_pct, sl_pct, tp_pct, auto, alert_price, notify_interval) VALUES (?,?,?,?,?,?,?,?,?,?)",
              (user_id, DEFAULT_EXCHANGE, DEFAULT_SYMBOL, DEFAULT_TIMEFRAME, 2.0, 2.0, 4.0, False, 0, 60))
    conn.commit()
    conn.close()
    await update.message.reply_text(
        "🤖 *DADADOVICH CRYPTO BOT v4.0*\n🔹 5 бирж без API\n🔹 30+ индикаторов\n🔹 TradingView стиль\n\nВыберите действие:",
        reply_markup=get_main_keyboard(), parse_mode='Markdown')

async def forecast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    conn = sqlite3.connect('crypto_bot.db')
    c = conn.cursor()
    c.execute("SELECT exchange, symbol, timeframe FROM user_settings WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        await query.edit_message_text("❌ Настройки не найдены. Используйте /start")
        return
    exchange_name, symbol, tf = row
    df = await fetch_klines(exchange_name, symbol, tf, 150)
    if df.empty:
        await query.edit_message_text(f"❌ Ошибка получения данных")
        return
    generator = SignalGenerator(df, exchange_name)
    result = generator.generate()
    msg = f"🧠 *ПРОГНОЗ {symbol}*\n🏦 {EXCHANGE_NAMES.get(exchange_name, exchange_name)}\n⏱️ {tf}\n\n🔹 *Сигнал:* {result['signal']}\n🔸 *Score:* {result['score']:.3f}\n💰 *Цена:* ${result['price']:.2f}\n\n📈 *Сигналы:*\n"
    for s in result['signals'][:5]:
        msg += f"• {s[1]}\n"
    await query.edit_message_text(msg, parse_mode='Markdown')

async def compare_exchanges(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    msg = "🌍 *СРАВНЕНИЕ БИРЖ*\n\n"
    for name in ['binance', 'bybit', 'okx', 'kucoin', 'gate']:
        df = await fetch_klines(name, 'BTC/USDT', '15m', 100)
        if not df.empty:
            generator = SignalGenerator(df, name)
            result = generator.generate()
            msg += f"{EXCHANGE_NAMES.get(name, name)}: {result['signal']} | Score: {result['score']:.2f}\n"
    await query.edit_message_text(msg, parse_mode='Markdown')

async def change_exchange(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔄 *Выберите биржу:*", reply_markup=get_exchange_keyboard(), parse_mode='Markdown')

async def set_exchange(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    exchange = query.data.replace("exch_", "")
    conn = sqlite3.connect('crypto_bot.db')
    c = conn.cursor()
    c.execute("UPDATE user_settings SET exchange = ? WHERE user_id = ?", (exchange, user_id))
    conn.commit()
    conn.close()
    await query.edit_message_text(f"✅ Установлена биржа: {EXCHANGE_NAMES.get(exchange, exchange)}", parse_mode='Markdown')

async def chart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    conn = sqlite3.connect('crypto_bot.db')
    c = conn.cursor()
    c.execute("SELECT exchange, symbol, timeframe FROM user_settings WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        await query.edit_message_text("❌ Настройки не найдены")
        return
    exchange_name, symbol, tf = row
    df = await fetch_klines(exchange_name, symbol, tf, 100)
    if df.empty:
        await query.edit_message_text("❌ Ошибка данных")
        return
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(df['timestamp'], df['close'], color='blue', linewidth=2, label='Цена')
    ax.set_title(f'{symbol} - {tf}', fontsize=14)
    ax.set_xlabel('Время')
    ax.set_ylabel('Цена (USDT)')
    ax.grid(True, alpha=0.3)
    ax.legend()
    plt.xticks(rotation=45)
    buf = BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format='png')
    buf.seek(0)
    await query.edit_message_media(InputMediaPhoto(buf, caption=f"📈 *График {symbol}*"), parse_mode='Markdown')
    plt.close()

async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    conn = sqlite3.connect('crypto_bot.db')
    c = conn.cursor()
    c.execute("SELECT symbol, signal, score, price, timestamp FROM signals_history WHERE user_id=? ORDER BY id DESC LIMIT 10", (user_id,))
    rows = c.fetchall()
    conn.close()
    if not rows:
        await query.edit_message_text("📋 *История сигналов пуста*", parse_mode='Markdown')
        return
    msg = "📋 *ПОСЛЕДНИЕ СИГНАЛЫ*\n\n"
    for row in rows:
        symbol, signal, score, price, ts = row
        msg += f"• {symbol}: {signal} | Score: {score:.2f} | ${price:.2f}\n"
    await query.edit_message_text(msg, parse_mode='Markdown')

async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    conn = sqlite3.connect('crypto_bot.db')
    c = conn.cursor()
    c.execute("SELECT exchange, symbol, timeframe, risk_pct, sl_pct, tp_pct FROM user_settings WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        await query.edit_message_text("❌ Настройки не найдены")
        return
    exchange, symbol, tf, risk, sl, tp = row
    msg = f"⚙️ *НАСТРОЙКИ*\n\n🏦 Биржа: {EXCHANGE_NAMES.get(exchange, exchange)}\n📊 Пара: {symbol}\n⏱️ Таймфрейм: {tf}\n📉 Риск: {risk}%\n🛑 SL: {sl}%\n🎯 TP: {tp}%"
    await query.edit_message_text(msg, parse_mode='Markdown')

app = Flask(__name__)
@app.route('/')
def home(): return "🤖 DADADOVICH Crypto Bot is running!", 200
@app.route('/health')
def health(): return jsonify({"status": "healthy", "exchanges": list(EXCHANGES.keys())})

def run_bot():
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(forecast, pattern="forecast"))
    application.add_handler(CallbackQueryHandler(compare_exchanges, pattern="compare"))
    application.add_handler(CallbackQueryHandler(change_exchange, pattern="change_exchange"))
    application.add_handler(CallbackQueryHandler(set_exchange, pattern="exch_"))
    application.add_handler(CallbackQueryHandler(chart, pattern="chart"))
    application.add_handler(CallbackQueryHandler(history, pattern="history"))
    application.add_handler(CallbackQueryHandler(settings, pattern="settings"))
    application.run_polling()

if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
