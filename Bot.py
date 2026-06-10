import yfinance as yf
import pandas as pd
import numpy as np
import requests
from datetime import datetime

# ================= CONFIGURATION =================
# ⚠️ PUT YOUR *NEW* REVOKED TOKEN HERE AFTER REGENERATING IT
TELEGRAM_TOKEN = 'YOUR_NEW_BOT_TOKEN_HERE'
CHAT_ID = '8251806684' # Your Chat ID is fine to keep

WATCHLIST = [
    {'symbol': 'EURUSD=X', 'name': 'EUR/USD'},
    {'symbol': 'BTC-USD', 'name': 'Bitcoin'},
    {'symbol': 'GC=F', 'name': 'Gold'}
]

TIMEFRAME = '1h'
HTF_TIMEFRAME = '1d'
EMA_FAST, EMA_SLOW = 20, 50
ADX_PERIOD, ADX_THRESHOLD = 14, 25
VOLUME_PERIOD = 20
SL_MULTIPLIER, TP_MULTIPLIER = 1.5, 3.0
# =================================================

def get_data(symbol, timeframe, days='60d'):
    try:
        data = yf.download(symbol, period=days, interval=timeframe, progress=False)
        if data.empty: 
            return None
        return data
    except Exception as e:
        print(f"Error fetching {symbol}: {e}")
        return None

def calculate_indicators(df):
    # 1. EMAs
    df['EMA_Fast'] = df['Close'].ewm(span=EMA_FAST, adjust=False).mean()
    df['EMA_Slow'] = df['Close'].ewm(span=EMA_SLOW, adjust=False).mean()
    
    # 2. MACD
    exp1 = df['Close'].ewm(span=12, adjust=False).mean()
    exp2 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = exp1 - exp2
    df['Signal_Line'] = df['MACD'].ewm(span=9, adjust=False).mean()
    
    # 3. ATR (for Stop Loss / Take Profit)
    high_low = df['High'] - df['Low']
    high_close = np.abs(df['High'] - df['Close'].shift(1))
    low_close = np.abs(df['Low'] - df['Close'].shift(1))
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['ATR'] = true_range.ewm(alpha=1/14, adjust=False).mean()
    
    # 4. ADX (Chop Filter)
    up_move = df['High'] - df['High'].shift(1)
    down_move = df['Low'].shift(1) - df['Low']
    pos_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
    neg_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
    
    pos_dm_smooth = pd.Series(pos_dm).ewm(alpha=1/ADX_PERIOD, adjust=False).mean()
    neg_dm_smooth = pd.Series(neg_dm).ewm(alpha=1/ADX_PERIOD, adjust=False).mean()
    tr_smooth = pd.Series(true_range).ewm(alpha=1/ADX_PERIOD, adjust=False).mean()
    
    pos_di = 100 * (pos_dm_smooth / tr_smooth)
    neg_di = 100 * (neg_dm_smooth / tr_smooth)
    dx = 100 * np.abs(pos_di - neg_di) / (pos_di + neg_di)
    df['ADX'] = pd.Series(dx).ewm(alpha=1/ADX_PERIOD, adjust=False).mean()
    
    # 5. Volume SMA
    df['Vol_SMA'] = df['Volume'].rolling(window=VOLUME_PERIOD).mean()
    return df

def generate_signal(asset_info, df_1h, df_1d):
    latest = df_1h.iloc[-2]
    prev = df_1h.iloc[-3]
    current_price = latest['Close']
    atr = latest['ATR']
    adx = latest['ADX']
    volume_ratio = latest['Volume'] / latest['Vol_SMA'] if latest['Vol_SMA'] > 0 else 0
    
    # FILTER 1 & 2: ADX and Volume
    if adx < ADX_THRESHOLD or volume_ratio < 0.8:
        return "HOLD", current_price, 0, 0, "Filtered (Low ADX/Volume)"

    # FILTER 3: Higher Timeframe (Daily) Alignment
    df_1d['EMA_50_Daily'] = df_1d['Close'].ewm(span=50, adjust=False).mean()
    latest_1d = df_1d.iloc[-2]
    daily_bullish = latest_1d['Close'] > latest_1d['EMA_50_Daily']
    daily_bearish = latest_1d['Close'] < latest_1d['EMA_50_Daily']

    # BUY LOGIC
    if prev['EMA_Fast'] <= prev['EMA_Slow'] and latest['EMA_Fast'] > latest['EMA_Slow']:
        if latest['MACD'] > latest['Signal_Line'] and daily_bullish:
            return "BUY / LONG 🟢", current_price, current_price - (atr * SL_MULTIPLIER), current_price + (atr * TP_MULTIPLIER), f"HTF Bullish + Vol {volume_ratio:.1f}x + ADX {adx:.1f}"
            
    # SELL LOGIC
    elif prev['EMA_Fast'] >= prev['EMA_Slow'] and latest['EMA_Fast'] < latest['EMA_Slow']:
        if latest['MACD'] < latest['Signal_Line'] and daily_bearish:
            return "SELL / SHORT 🔴", current_price, current_price + (atr * SL_MULTIPLIER), current_price - (atr * TP_MULTIPLIER), f"HTF Bearish + Vol {volume_ratio:.1f}x + ADX {adx:.1f}"
            
    return "HOLD", current_price, 0, 0, "No Setup"

def send_alert(asset_name, symbol, signal, price, sl, tp, reason):
    if signal == "HOLD": 
        return
    fmt = ".2f" if price > 100 else ".5f"
    message = (
        f"👑 *ELITE SIGNAL* 👑\n\n"
        f"📊 *Asset:* {asset_name}\n"
        f"🎯 *Action:* **{signal}**\n"
        f"💰 *Entry:* {price:{fmt}}\n"
        f"🛑 *Stop Loss:* {sl:{fmt}}\n"
        f"✅ *Take Profit:* {tp:{fmt}}\n\n"
        f"📉 *Risk/Reward:* 1 : 2 (ATR Based)\n"
        f"📝 *Confluence:* {reason}\n"
        f"⏰ *Time:* {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        f"⚠️ *High Probability Setup. Execute with strict risk management.*"
    )
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    response = requests.post(url, json={"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"})
    
    if response.status_code == 200:
        print(f"✅ Alert sent for {asset_name}")
    else:
        print(f"❌ Telegram Error for {asset_name}: {response.text}")

# --- MAIN EXECUTION (Runs ONCE per hour via PythonAnywhere Scheduler) ---
print(f"🚀 Scanning markets at {datetime.now().strftime('%Y-%m-%d %H:%M')}...")

for asset in WATCHLIST:
    df_1h = get_data(asset['symbol'], TIMEFRAME, days='60d')
    df_1d = get_data(asset['symbol'], HTF_TIMEFRAME, days='1y')
    
    if df_1h is not None and df_1d is not None and len(df_1h) > 50:
        df_1h = calculate_indicators(df_1h)
        signal, price, sl, tp, reason = generate_signal(asset, df_1h, df_1d)
        
        if signal != "HOLD":
            send_alert(asset['name'], asset['symbol'], signal, price, sl, tp, reason)
        else:
            print(f"  -> {asset['name']}: {reason}")
            
print("✅ Scan complete. Exiting.")
