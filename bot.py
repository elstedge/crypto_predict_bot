#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║  DADADOVICH MEGA CRYPTO BOT v4.0                               ║
║  50+ функций | 30+ индикаторов | 5 бирж БЕЗ API               ║
║  TradingView стиль | Аналитика | Авто-сигналы                  ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os
import asyncio
import sqlite3
import logging
import threading
import json
import time
import math
import random
from datetime import datetime, timedelta
from collections import deque, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional, Any
import numpy as np
import pandas as pd
import ccxt
from flask import Flask, jsonify, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle
from io import BytesIO
import seaborn as sns
import warnings
warnings.filterwarnings('ignore')

# ==================== КОНФИГ ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "ВАШ_ТОКЕН_СЮДА")

# ==================== БИРЖИ БЕЗ API ====================
# Используем публичные эндпоинты, API ключи НЕ НУЖНЫ
EXCHANGES = {
    'binance': ccxt.binance({
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'}
    }),
    'bybit': ccxt.bybit({
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'}
    }),
    'okx': ccxt.okx({
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'}
    }),
    'kucoin': ccxt.kucoin({
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'}
    }),
    'gate': ccxt.gate({
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'}
    })
}

EXCHANGE_NAMES = {
    'binance': '🟡 Binance',
    'bybit': '🔵 Bybit',
    'okx': '🟢 OKX',
    'kucoin': '🟣 KuCoin',
    'gate': '🟠 Gate.io'
}

# ==================== ПАРЫ И ТАЙМФРЕЙМЫ ====================
SYMBOLS = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT',
    'ADA/USDT', 'DOT/USDT', 'LINK/USDT', 'MATIC/USDT', 'AVAX/USDT',
    'ATOM/USDT', 'UNI/USDT', 'LTC/USDT', 'BCH/USDT', 'NEAR/USDT',
    'APT/USDT', 'ARB/USDT', 'OP/USDT', 'INJ/USDT', 'SEI/USDT'
]

TIMEFRAMES = ['1m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '12h', '1d', '1w']

DEFAULT_SYMBOL = 'BTC/USDT'
DEFAULT_TIMEFRAME = '15m'
DEFAULT_EXCHANGE = 'binance'

# ==================== БАЗА ДАННЫХ ====================
def init_db():
    conn = sqlite3.connect('crypto_bot.db')
    c = conn.cursor()
    
    # Настройки пользователей
    c.execute('''CREATE TABLE IF NOT EXISTS user_settings
                 (user_id INTEGER PRIMARY KEY, exchange TEXT, symbol TEXT, 
                  timeframe TEXT, risk_pct REAL, sl_pct REAL, tp_pct REAL, 
                  auto BOOLEAN, alert_price REAL, notify_interval INTEGER)''')
    
    # История сигналов
    c.execute('''CREATE TABLE IF NOT EXISTS signals_history
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                  exchange TEXT, symbol TEXT, timeframe TEXT, signal TEXT,
                  score REAL, price REAL, timestamp TEXT,
                  indicators TEXT, levels TEXT)''')
    
    # Торговая статистика
    c.execute('''CREATE TABLE IF NOT EXISTS trade_stats
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                  symbol TEXT, direction TEXT, entry REAL, exit REAL,
                  pnl REAL, pnl_percent REAL, timestamp TEXT)''')
    
    # Индикаторная база (кэш)
    c.execute('''CREATE TABLE IF NOT EXISTS indicator_cache
                 (symbol TEXT, timeframe TEXT, timestamp TEXT,
                  indicators TEXT, PRIMARY KEY (symbol, timeframe))''')
    
    conn.commit()
    conn.close()
init_db()

# ==================== 30+ ИНДИКАТОРОВ ====================
class Indicators:
    """Класс со всеми индикаторами в стиле TradingView"""
    
    @staticmethod
    def sma(data, period):
        """Simple Moving Average"""
        return pd.Series(data).rolling(period).mean().values
    
    @staticmethod
    def ema(data, period):
        """Exponential Moving Average"""
        return pd.Series(data).ewm(span=period, adjust=False).mean().values
    
    @staticmethod
    def wma(data, period):
        """Weighted Moving Average"""
        return pd.Series(data).rolling(period).apply(
            lambda x: np.sum(x * np.arange(1, len(x)+1)) / np.sum(np.arange(1, len(x)+1))
        ).values
    
    @staticmethod
    def hma(data, period):
        """Hull Moving Average"""
        wma_half = pd.Series(data).rolling(period//2).apply(
            lambda x: np.sum(x * np.arange(1, len(x)+1)) / np.sum(np.arange(1, len(x)+1))
        ).values
        wma_full = pd.Series(data).rolling(period).apply(
            lambda x: np.sum(x * np.arange(1, len(x)+1)) / np.sum(np.arange(1, len(x)+1))
        ).values
        hma = 2 * wma_half - wma_full
        return pd.Series(hma).rolling(int(np.sqrt(period))).mean().values
    
    @staticmethod
    def rsi(data, period=14):
        """Relative Strength Index"""
        delta = pd.Series(data).diff()
        gain = (delta.where(delta > 0, 0)).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
        rs = gain / loss
        return (100 - (100 / (1 + rs))).values
    
    @staticmethod
    def macd(data, fast=12, slow=26, signal=9):
        """MACD"""
        ema_fast = pd.Series(data).ewm(span=fast, adjust=False).mean()
        ema_slow = pd.Series(data).ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line
        return macd_line.values, signal_line.values, histogram.values
    
    @staticmethod
    def bollinger_bands(data, period=20, std_dev=2):
        """Bollinger Bands"""
        sma = pd.Series(data).rolling(period).mean()
        std = pd.Series(data).rolling(period).std()
        upper = sma + std_dev * std
        lower = sma - std_dev * std
        return upper.values, sma.values, lower.values
    
    @staticmethod
    def stochastic(high, low, close, k_period=14, d_period=3):
        """Stochastic Oscillator"""
        low_min = pd.Series(low).rolling(k_period).min()
        high_max = pd.Series(high).rolling(k_period).max()
        k = 100 * (close - low_min) / (high_max - low_min)
        d = pd.Series(k).rolling(d_period).mean()
        return k.values, d.values
    
    @staticmethod
    def atr(high, low, close, period=14):
        """Average True Range"""
        tr1 = high - low
        tr2 = np.abs(high - pd.Series(close).shift(1))
        tr3 = np.abs(low - pd.Series(close).shift(1))
        tr = np.maximum(tr1, np.maximum(tr2, tr3))
        return pd.Series(tr).rolling(period).mean().values
    
    @staticmethod
    def obv(close, volume):
        """On-Balance Volume"""
        obv = np.zeros_like(close)
        for i in range(1, len(close)):
            if close[i] > close[i-1]:
                obv[i] = obv[i-1] + volume[i]
            elif close[i] < close[i-1]:
                obv[i] = obv[i-1] - volume[i]
            else:
                obv[i] = obv[i-1]
        return obv
    
    @staticmethod
    def cci(high, low, close, period=20):
        """Commodity Channel Index"""
        tp = (high + low + close) / 3
        sma = pd.Series(tp).rolling(period).mean()
        mad = pd.Series(tp).rolling(period).apply(lambda x: np.mean(np.abs(x - np.mean(x))))
        return ((tp - sma) / (0.015 * mad)).values
    
    @staticmethod
    def williams_r(high, low, close, period=14):
        """Williams %R"""
        high_max = pd.Series(high).rolling(period).max()
        low_min = pd.Series(low).rolling(period).min()
        return (-100 * (high_max - close) / (high_max - low_min)).values
    
    @staticmethod
    def mfi(high, low, close, volume, period=14):
        """Money Flow Index"""
        tp = (high + low + close) / 3
        money_flow = tp * volume
        positive = []
        negative = []
        for i in range(1, len(tp)):
            if tp[i] > tp[i-1]:
                positive.append(money_flow[i])
                negative.append(0)
            else:
                positive.append(0)
                negative.append(money_flow[i])
        pos_sum = pd.Series(positive).rolling(period).sum()
        neg_sum = pd.Series(negative).rolling(period).sum()
        return (100 - (100 / (1 + pos_sum / neg_sum))).values
    
    @staticmethod
    def adx(high, low, close, period=14):
        """Average Directional Index"""
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
    def ichimoku(high, low, close):
        """Ichimoku Cloud (упрощённый)"""
        tenkan = (pd.Series(high).rolling(9).max() + pd.Series(low).rolling(9).min()) / 2
        kijun = (pd.Series(high).rolling(26).max() + pd.Series(low).rolling(26).min()) / 2
        senkou_a = (tenkan + kijun) / 2
        senkou_b = (pd.Series(high).rolling(52).max() + pd.Series(low).rolling(52).min()) / 2
        return tenkan.values, kijun.values, senkou_a.values, senkou_b.values
    
    @staticmethod
    def vortex(high, low, close, period=14):
        """Vortex Indicator"""
        vm_plus = np.abs(high - pd.Series(low).shift(1))
        vm_minus = np.abs(low - pd.Series(high).shift(1))
        tr = np.maximum(high - low, np.maximum(np.abs(high - pd.Series(close).shift(1)), 
                                                np.abs(low - pd.Series(close).shift(1))))
        vp = pd.Series(vm_plus).rolling(period).sum() / pd.Series(tr).rolling(period).sum()
        vn = pd.Series(vm_minus).rolling(period).sum() / pd.Series(tr).rolling(period).sum()
        return vp.values, vn.values
    
    @staticmethod
    def supertrend(high, low, close, period=10, multiplier=3):
        """SuperTrend"""
        atr_val = Indicators.atr(high, low, close, period)
        hl2 = (high + low) / 2
        upper = hl2 + multiplier * atr_val
        lower = hl2 - multiplier * atr_val
        trend = np.ones(len(close))
        for i in range(1, len(close)):
            if close[i] > upper[i-1]:
                trend[i] = 1
            elif close[i] < lower[i-1]:
                trend[i] = -1
            else:
                trend[i] = trend[i-1]
        return upper, lower, trend
    
    @staticmethod
    def keltner_channel(high, low, close, period=20, multiplier=1.5):
        """Keltner Channel"""
        ema = pd.Series(close).ewm(span=period, adjust=False).mean()
        atr_val = Indicators.atr(high, low, close, period)
        upper = ema + multiplier * atr_val
        lower = ema - multiplier * atr_val
        return upper.values, ema.values, lower.values
    
    @staticmethod
    def donchian_channel(high, low, period=20):
        """Donchian Channel"""
        upper = pd.Series(high).rolling(period).max()
        lower = pd.Series(low).rolling(period).min()
        middle = (upper + lower) / 2
        return upper.values, middle.values, lower.values
    
    @staticmethod
    def vwap(df):
        """Volume Weighted Average Price"""
        cum_vol = df['volume'].cumsum()
        cum_price_vol = (df['close'] * df['volume']).cumsum()
        return (cum_price_vol / cum_vol).values
    
    @staticmethod
    def pivot_points(high, low, close):
        """Pivot Points (классические)"""
        pivot = (high[-1] + low[-1] + close[-1]) / 3
        r1 = 2 * pivot - low[-1]
        r2 = pivot + (high[-1] - low[-1])
        r3 = high[-1] + 2 * (pivot - low[-1])
        s1 = 2 * pivot - high[-1]
        s2 = pivot - (high[-1] - low[-1])
        s3 = low[-1] - 2 * (high[-1] - pivot)
        return {'pivot': pivot, 'r1': r1, 'r2': r2, 'r3': r3, 's1': s1, 's2': s2, 's3': s3}
    
    @staticmethod
    def fibonacci_levels(high, low, close):
        """Fibonacci Retracement"""
        diff = high[-1] - low[-1]
        levels = {
            '0%': low[-1],
            '23.6%': low[-1] + 0.236 * diff,
            '38.2%': low[-1] + 0.382 * diff,
            '50%': low[-1] + 0.5 * diff,
            '61.8%': low[-1] + 0.618 * diff,
            '78.6%': low[-1] + 0.786 * diff,
            '100%': high[-1]
        }
        return levels

# ==================== АНАЛИЗАТОР ====================
class MarketAnalyzer:
    """Анализ рынка с 30+ индикаторами"""
    
    def __init__(self, df):
        self.df = df
        self.close = df['close'].values
        self.high = df['high'].values
        self.low = df['low'].values
        self.volume = df['volume'].values
        self.indicators = Indicators()
    
    def analyze(self):
        """Полный анализ с 30+ индикаторами"""
        result = {}
        
        # 1. SMA
        for p in [5, 10, 20, 50, 100, 200]:
            result[f'SMA_{p}'] = self.indicators.sma(self.close, p)[-1] if len(self.close) >= p else 0
        
        # 2. EMA
        for p in [9, 12, 26, 50, 200]:
            result[f'EMA_{p}'] = self.indicators.ema(self.close, p)[-1] if len(self.close) >= p else 0
        
        # 3. RSI
        result['RSI_7'] = self.indicators.rsi(self.close, 7)[-1] if len(self.close) >= 7 else 50
        result['RSI_14'] = self.indicators.rsi(self.close, 14)[-1] if len(self.close) >= 14 else 50
        result['RSI_21'] = self.indicators.rsi(self.close, 21)[-1] if len(self.close) >= 21 else 50
        
        # 4. MACD
        macd_line, signal_line, hist = self.indicators.macd(self.close)
        result['MACD'] = macd_line[-1] if len(macd_line) > 0 else 0
        result['MACD_Signal'] = signal_line[-1] if len(signal_line) > 0 else 0
        result['MACD_Hist'] = hist[-1] if len(hist) > 0 else 0
        
        # 5. Bollinger Bands
        upper, middle, lower = self.indicators.bollinger_bands(self.close)
        result['BB_Upper'] = upper[-1] if len(upper) > 0 else 0
        result['BB_Middle'] = middle[-1] if len(middle) > 0 else 0
        result['BB_Lower'] = lower[-1] if len(lower) > 0 else 0
        result['BB_%B'] = (result['BB_Upper'] - result['BB_Lower']) / (result['BB_Upper'] + result['BB_Lower'])
        
        # 6. Stochastic
        k, d = self.indicators.stochastic(self.high, self.low, self.close)
        result['Stoch_K'] = k[-1] if len(k) > 0 else 50
        result['Stoch_D'] = d[-1] if len(d) > 0 else 50
        
        # 7. ATR
        result['ATR_14'] = self.indicators.atr(self.high, self.low, self.close, 14)[-1] if len(self.close) >= 14 else 0
        
        # 8. OBV
        obv = self.indicators.obv(self.close, self.volume)
        result['OBV'] = obv[-1] if len(obv) > 0 else 0
        
        # 9. CCI
        result['CCI_20'] = self.indicators.cci(self.high, self.low, self.close, 20)[-1] if len(self.close) >= 20 else 0
        
        # 10. Williams %R
        result['Williams_%R'] = self.indicators.williams_r(self.high, self.low, self.close, 14)[-1] if len(self.close) >= 14 else -50
        
        # 11. MFI
        result['MFI_14'] = self.indicators.mfi(self.high, self.low, self.close, self.volume, 14)[-1] if len(self.close) >= 14 else 50
        
        # 12. ADX
        result['ADX_14'] = self.indicators.adx(self.high, self.low, self.close, 14)[-1] if len(self.close) >= 14 else 0
        
        # 13. Ichimoku
        tenkan, kijun, senkou_a, senkou_b = self.indicators.ichimoku(self.high, self.low, self.close)
        result['Ichimoku_Tenkan'] = tenkan[-1] if len(tenkan) > 0 else 0
        result['Ichimoku_Kijun'] = kijun[-1] if len(kijun) > 0 else 0
        result['Ichimoku_Senkou_A'] = senkou_a[-1] if len(senkou_a) > 0 else 0
        result['Ichimoku_Senkou_B'] = senkou_b[-1] if len(senkou_b) > 0 else 0
        
        # 14. Vortex
        vp, vn = self.indicators.vortex(self.high, self.low, self.close, 14)
        result['Vortex_P'] = vp[-1] if len(vp) > 0 else 0
        result['Vortex_N'] = vn[-1] if len(vn) > 0 else 0
        
        # 15. SuperTrend
        upper_st, lower_st, trend = self.indicators.supertrend(self.high, self.low, self.close)
        result['SuperTrend'] = trend[-1] if len(trend) > 0 else 1
        result['SuperTrend_Upper'] = upper_st[-1] if len(upper_st) > 0 else 0
        result['SuperTrend_Lower'] = lower_st[-1] if len(lower_st) > 0 else 0
        
        # 16. Keltner Channel
        kc_upper, kc_middle, kc_lower = self.indicators.keltner_channel(self.high, self.low, self.close)
        result['KC_Upper'] = kc_upper[-1] if len(kc_upper) > 0 else 0
        result['KC_Middle'] = kc_middle[-1] if len(kc_middle) > 0 else 0
        result['KC_Lower'] = kc_lower[-1] if len(kc_lower) > 0 else 0
        
        # 17. Donchian Channel
        dc_upper, dc_middle, dc_lower = self.indicators.donchian_channel(self.high, self.low)
        result['DC_Upper'] = dc_upper[-1] if len(dc_upper) > 0 else 0
        result['DC_Middle'] = dc_middle[-1] if len(dc_middle) > 0 else 0
        result['DC_Lower'] = dc_lower[-1] if len(dc_lower) > 0 else 0
        
        # 18. VWAP
        result['VWAP'] = self.indicators.vwap(self.df)[-1] if len(self.df) > 0 else 0
        
        # 19. Pivot Points
        result['Pivot'] = self.indicators.pivot_points(self.high, self.low, self.close)
        
        # 20. Fibonacci
        result['Fibonacci'] = self.indicators.fibonacci_levels(self.high, self.low, self.close)
        
        # 21. Тренды (дополнительные)
        result['Trend_Strength'] = self._trend_strength()
        result['Volatility'] = np.std(self.close[-20:]) / np.mean(self.close[-20:]) if len(self.close) >= 20 else 0
        
        return result
    
    def _trend_strength(self):
        """Оценка силы тренда"""
        if len(self.close) < 50:
            return 0
        sma_10 = np.mean(self.close[-10:])
        sma_50 = np.mean(self.close[-50:])
        diff = (sma_10 - sma_50) / sma_50 * 100
        return np.clip(diff, -100, 100)

# ==================== ГЕНЕРАТОР СИГНАЛОВ ====================
class SignalGenerator:
    """Генерация торговых сигналов на основе анализа"""
    
    def __init__(self, df, exchange_name='binance'):
        self.df = df
        self.exchange_name = exchange_name
        self.analyzer = MarketAnalyzer(df)
        self.indicators = self.analyzer.analyze()
        self.price = df['close'].iloc[-1]
    
    def generate(self):
        """Генерация полного сигнала"""
        signals = []
        score = 0.0
        confidence = 0.0
        
        # 1. RSI сигналы
        rsi = self.indicators.get('RSI_14', 50)
        if rsi < 30:
            signals.append(('BUY', 'RSI перепродан', 0.15))
            score += 0.15
        elif rsi > 70:
            signals.append(('SELL', 'RSI перекуплен', -0.15))
            score -= 0.15
        elif rsi < 20:
            signals.append(('BUY', 'RSI сильно перепродан', 0.25))
            score += 0.25
        elif rsi > 80:
            signals.append(('SELL', 'RSI сильно перекуплен', -0.25))
            score -= 0.25
        
        # 2. MACD сигналы
        macd_hist = self.indicators.get('MACD_Hist', 0)
        macd_line = self.indicators.get('MACD', 0)
        macd_signal = self.indicators.get('MACD_Signal', 0)
        
        if macd_hist > 0 and macd_line > macd_signal:
            signals.append(('BUY', 'MACD восходящий', 0.20))
            score += 0.20
        elif macd_hist < 0 and macd_line < macd_signal:
            signals.append(('SELL', 'MACD нисходящий', -0.20))
            score -= 0.20
        
        # 3. SMA кроссоверы
        sma_10 = self.indicators.get('SMA_10', 0)
        sma_50 = self.indicators.get('SMA_50', 0)
        sma_200 = self.indicators.get('SMA_200', 0)
        
        if sma_10 > sma_50 and sma_10 > sma_200:
            signals.append(('BUY', 'SMA 10 > 50 > 200', 0.15))
            score += 0.15
        elif sma_10 < sma_50 and sma_10 < sma_200:
            signals.append(('SELL', 'SMA 10 < 50 < 200', -0.15))
            score -= 0.15
        
        # 4. Bollinger Bands
        bb_lower = self.indicators.get('BB_Lower', 0)
        bb_upper = self.indicators.get('BB_Upper', 0)
        bb_middle = self.indicators.get('BB_Middle', 0)
        
        if self.price < bb_lower:
            signals.append(('BUY', 'Цена ниже BB (перепродано)', 0.20))
            score += 0.20
        elif self.price > bb_upper:
            signals.append(('SELL', 'Цена выше BB (перекуплено)', -0.20))
            score -= 0.20
        
        # 5. Stochastic
        stoch_k = self.indicators.get('Stoch_K', 50)
        stoch_d = self.indicators.get('Stoch_D', 50)
        
        if stoch_k < 20 and stoch_k > stoch_d:
            signals.append(('BUY', 'Stoch восходящий из перепроданности', 0.15))
            score += 0.15
        elif stoch_k > 80 and stoch_k < stoch_d:
            signals.append(('SELL', 'Stoch нисходящий из перекупленности', -0.15))
            score -= 0.15
        
        # 6. Ichimoku
        price_above_cloud = self.price > self.indicators.get('Ichimoku_Senkou_A', 0) and \
                           self.price > self.indicators.get('Ichimoku_Senkou_B', 0)
        price_below_cloud = self.price < self.indicators.get('Ichimoku_Senkou_A', 0) and \
                           self.price < self.indicators.get('Ichimoku_Senkou_B', 0)
        
        if price_above_cloud:
            signals.append(('BUY', 'Цена выше облака Ишимоку', 0.15))
            score += 0.15
        elif price_below_cloud:
            signals.append(('SELL', 'Цена ниже облака Ишимоку', -0.15))
            score -= 0.15
        
        # 7. ADX (сила тренда)
        adx = self.indicators.get('ADX_14', 0)
        if adx > 25:
            signals.append(('BUY' if score > 0 else 'SELL', 
                           f'Сильный тренд (ADX: {adx:.1f})', 0.10 * (1 if score > 0 else -1)))
            score += 0.10 * (1 if score > 0 else -1)
        
        # 8. Vortex
        vp = self.indicators.get('Vortex_P', 0)
        vn = self.indicators.get('Vortex_N', 0)
        if vp > vn and vp > 1:
            signals.append(('BUY', 'Vortex бычий', 0.10))
            score += 0.10
        elif vn > vp and vn > 1:
            signals.append(('SELL', 'Vortex медвежий', -0.10))
            score -= 0.10
        
        # 9. SuperTrend
        st = self.indicators.get('SuperTrend', 0)
        if st == 1:
            signals.append(('BUY', 'SuperTrend бычий', 0.15))
            score += 0.15
        elif st == -1:
            signals.append(('SELL', 'SuperTrend медвежий', -0.15))
            score -= 0.15
        
        # 10. Объём
        obv = self.indicators.get('OBV', 0)
        if len(self.df) > 20:
            obv_trend = obv - np.mean(self.indicators.get('OBV', 0))
            if obv_trend > 0:
                signals.append(('BUY', 'OBV растёт (подтверждение)', 0.10))
                score += 0.10
            else:
                signals.append(('SELL', 'OBV падает (подтверждение)', -0.10))
                score -= 0.10
        
        # Нормализация
        score = np.clip(score, -1, 1)
        confidence = abs(score)
        
        # Определение финального сигнала
        if score > 0.3:
            final_signal = 'LONG'
        elif score < -0.3:
            final_signal = 'SHORT'
        else:
            final_signal = 'NEUTRAL'
        
        # Уровни для входа
        levels = self._calculate_levels()
        
        return {
            'signal': final_signal,
            'score': score,
            'confidence': confidence,
            'signals': signals,
            'price': self.price,
            'levels': levels,
            'indicators': self.indicators,
            'exchange': self.exchange_name
        }
    
    def _calculate_levels(self):
        """Расчёт уровней входа/выхода"""
        price = self.price
        
        # Stop Loss и Take Profit
        atr = self.indicators.get('ATR_14', price * 0.02)
        
        if price > 0:
            sl_pct = 2.0
            tp_pct = 4.0
            sl = price * (1 - sl_pct/100)
            tp = price * (1 + tp_pct/100)
            sl_short = price * (1 + sl_pct/100)
            tp_short = price * (1 - tp_pct/100)
        else:
            sl = price * 0.98
            tp = price * 1.04
            sl_short = price * 1.02
            tp_short = price * 0.96
        
        # Поддержка и сопротивление
        support = self.indicators.get('BB_Lower', price * 0.97)
        resistance = self.indicators.get('BB_Upper', price * 1.03)
        
        return {
            'entry_long': price,
            'entry_short': price,
            'sl_long': round(sl, 2),
            'tp_long': round(tp, 2),
            'sl_short': round(sl_short, 2),
            'tp_short': round(tp_short, 2),
            'support': round(support, 2),
            'resistance': round(resistance, 2),
            'atr': round(atr, 2)
        }

# ==================== ПОЛУЧЕНИЕ ДАННЫХ С БИРЖ ====================
async def fetch_klines(exchange_name, symbol='BTC/USDT', timeframe='15m', limit=300):
    """Получение данных с биржи (без API)"""
    try:
        exchange = EXCHANGES.get(exchange_name)
        if not exchange:
            return pd.DataFrame()
        
        # Лимит для разных таймфреймов
        if timeframe in ['1m', '5m']:
            limit = min(limit, 500)
        elif timeframe in ['1h', '2h']:
            limit = min(limit, 300)
        else:
            limit = min(limit, 200)
        
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df
