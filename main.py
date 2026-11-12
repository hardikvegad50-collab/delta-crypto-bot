import os
import threading
import time
import requests
import ccxt
import pandas as pd
import numpy as np
from flask import Flask

# ==========================================================
# ૧. Render Web Server (Port Binding)
# ==========================================================
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Delta Crypto Bot is Running 24/7 on Render!"

# ==========================================================
# ૨. કન્ફિગરેશન (Environment Variables)
# ==========================================================
DELTA_API_KEY = os.environ.get('4onK6mBkqFkuNuKW709cf38NF1F9pN')
DELTA_API_SECRET = os.environ.get('w34ITWvEeCnR4YbLI2qDQRyCW9CdBPYQPDi3nhtllyHPVnvcIDMSbMY1etT1')

TELEGRAM_BOT_TOKEN = os.environ.get('9165884670:AAEgM8NOp-eANeO1o9zoMVtv5y3Vzy1pnZM')
TELEGRAM_CHAT_ID = os.environ.get('886022031')

PAIRS = [
    {'delta_sym': 'BTC/USDT', 'data_sym': 'BTC/USD'},
    {'delta_sym': 'ETH/USDT', 'data_sym': 'ETH/USD'}
]

TIMEFRAME = '5m'
LOOKBACK_CANDLES = 30
ORDER_SIZE = 1
MIN_RANGE_USD = 170.0  # લઘુત્તમ સ્વિંગ રેન્જ ડૉલરમાં (આશરે ₹૧૫,૦૦૦)
TRAILING_GAP = 0.005   # 0.5% ટ્રેઇલિંગ સ્ટોપલોસ
HEARTBEAT_INTERVAL = 3600  # દર ૧ કલાકે રિપોર્ટ (સેકન્ડ)

# ==========================================================
# ૩. એક્સચેન્જ કનેક્શન
# ==========================================================
data_exchange = ccxt.bitstamp({'enableRateLimit': True})

trade_exchange = ccxt.delta({
    'apiKey': DELTA_API_KEY,
    'secret': DELTA_API_SECRET,
    'enableRateLimit': True,
})
trade_exchange.set_sandbox_mode(True)  # ડેમો મોડ

try:
    trade_exchange.load_markets()
except Exception as e:
    print(f"માર્કેટ લોડ નોટિસ: {e}")

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': msg, 'parse_mode': 'Markdown'}
    try:
        requests.post(url, data=payload, timeout=4)
    except Exception as e:
        print(f"ટેલિગ્રામ એરર: {e}")

trades = {
    p['delta_sym']: {
        'in_trade': False,
        'direction': None,
        'entry_price': 0.0,
        'stop_loss': 0.0,
        'best_price': 0.0
    } for p in PAIRS
}

def get_live_candles(symbol):
    try:
        bars = data_exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=60)
        if not bars or len(bars) < LOOKBACK_CANDLES:
            return None
        return pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    except Exception:
        return None

def get_swing_points(df, lookback=30):
    recent = df.iloc[-lookback:-1]
    swing_high = recent['high'].max()
    swing_low = recent['low'].min()
    high_idx = recent['high'].idxmax()
    low_idx = recent['low'].idxmin()
    trend = 'DOWN' if high_idx < low_idx else 'UP'
    return swing_high, swing_low, trend

def send_heartbeat():
    """દર ૧ કલાકે ડૉલરમાં સ્ટેટસ રિપોર્ટ મોકલશે"""
    lines = [
        "💓 *બોટ સ્ટેટસ રિપોર્ટ (Heartbeat)*",
        "• સર્વર: `Render Active (24/7)`",
        "• લાઈવ માર્કેટ સ્ટેટસ:"
    ]
    for p in PAIRS:
        df = get_live_candles(p['data_sym'])
        delta_sym = p['delta_sym']
        st = trades[delta_sym]
        
        if df is not None and not df.empty:
            cur_usd = df.iloc[-1]['close']
            trade_status = f"{st['direction']} ચાલુ" if st['in_trade'] else "નવા સેટઅપની શોધમાં"
            lines.append(f"  - `{delta_sym}`: `${cur_usd:,.2f}` ({trade_status})")
    
    send_telegram("\n".join(lines))

# ==========================================================
# ૪. ટ્રેડિંગ અને ઓર્ડર લોજિક (ડૉલર ફોર્મેટમાં એલર્ટ્સ)
# ==========================================================
def process_pair(pair):
    global trades
    delta_symbol = pair['delta_sym']
    data_symbol = pair['data_sym']
    state = trades[delta_symbol]
    
    df = get_live_candles(data_symbol)
    if df is None or df.empty or len(df) < LOOKBACK_CANDLES:
        return

    last_candle = df.iloc[-2]
    current_price = df.iloc[-1]['close']
    
    swing_high, swing_low, trend = get_swing_points(df, lookback=LOOKBACK_CANDLES)
    fibo_range = swing_high - swing_low

    # લઘુત્તમ સ્વિંગ રેન્જ ફિલ્ટર ($170)
    if fibo_range < MIN_RANGE_USD:
        return

    # A. ટ્રેઇલિંગ સ્ટોપલોસ હેન્ડલિંગ
    if state['in_trade']:
        side = state['direction']
        if side == 'SHORT':
            if current_price < state['best_price']:
                state['best_price'] = current_price
                new_sl = current_price * (1 + TRAILING_GAP)
                if new_sl < state['stop_loss']:
                    state['stop_loss'] = new_sl
                    send_telegram(f"📉 *શોર્ટ ટ્રેઇલિંગ SL અપડેટ*\n• કોઈન: `{delta_symbol}`\n• ભાવ: `${current_price:,.2f}`\n• નવો SL: `${new_sl:,.2f}`")
            
            if current_price >= state['stop_loss']:
                try:
                    trade_exchange.create_order(delta_symbol, 'market', 'buy', ORDER_SIZE)
                    state['in_trade'] = False
                    send_telegram(f"🛑 *શોર્ટ સ્ટોપલોસ હિટ (Exit)*\n• કોઈન: `{delta_symbol}`\n• એક્ઝિટ ભાવ: `${current_price:,.2f}`")
                except Exception as ex:
                    send_telegram(f"⚠️ *ડેલ્ટા એક્ઝિટ ઓર્ડર ફેલ!*\n• વિગત: `{ex}`")

        elif side == 'LONG':
            if current_price > state['best_price']:
                state['best_price'] = current_price
                new_sl = current_price * (1 - TRAILING_GAP)
                if new_sl > state['stop_loss']:
                    state['stop_loss'] = new_sl
                    send_telegram(f"📈 *લોંગ ટ્રેઇલિંગ SL અપડેટ*\n• કોઈન: `{delta_symbol}`\n• ભાવ: `${current_price:,.2f}`\n• નવો SL: `${new_sl:,.2f}`")
            
            if current_price <= state['stop_loss']:
                try:
                    trade_exchange.create_order(delta_symbol, 'market', 'sell', ORDER_SIZE)
                    state['in_trade'] = False
                    send_telegram(f"🛑 *લોંગ સ્ટોપલોસ હિટ (Exit)*\n• કોઈન: `{delta_symbol}`\n• એક્ઝિટ ભાવ: `${current_price:,.2f}`")
                except Exception as ex:
                    send_telegram(f"⚠️ *ડેલ્ટા એક્ઝિટ ઓર્ડર ફેલ!*\n• વિગત: `{ex}`")
        return

    # B. નવી એન્ટ્રી શરતો
    if trend == 'DOWN':
        fibo_0_5 = swing_low + (0.50 * fibo_range)
        fibo_sl = swing_low + (0.630 * fibo_range)
        is_red = last_candle['close'] < last_candle['open']
        
        if (current_price >= fibo_0_5 and current_price < fibo_sl) and is_red:
            try:
                trade_exchange.create_order(delta_symbol, 'market', 'sell', ORDER_SIZE)
                state['in_trade'] = True
                state['direction'] = 'SHORT'
                state['entry_price'] = current_price
                state['stop_loss'] = fibo_sl
                state['best_price'] = current_price
                send_telegram(f"🔴 *૫-મિનિટ શોર્ટ (SELL) ઓર્ડર થયો!*\n• કોઈન: `{delta_symbol}`\n• એન્ટ્રી: `${current_price:,.2f}`\n• SL: `${fibo_sl:,.2f}`")
            except Exception as ex:
                print(f"Delta Order Error: {ex}")
                send_telegram(f"⚠️ *ડેલ્ટા ઓર્ડર રિજેક્ટ થયો!*\n• કોઈન: `{delta_symbol}`\n• ભૂલ: `{ex}`")

    elif trend == 'UP':
        fibo_0_5 = swing_high - (0.50 * fibo_range)
        fibo_sl = swing_high - (0.630 * fibo_range)
        is_green = last_candle['close'] > last_candle['open']
        
        if (current_price <= fibo_0_5 and current_price > fibo_sl) and is_green:
            try:
                trade_exchange.create_order(delta_symbol, 'market', 'buy', ORDER_SIZE)
                state['in_trade'] = True
                state['direction'] = 'LONG'
                state['entry_price'] = current_price
                state['stop_loss'] = fibo_sl
                state['best_price'] = current_price
                send_telegram(f"🟢 *૫-મિનિટ લોંગ (BUY) ઓર્ડર થયો!*\n• કોઈન: `{delta_symbol}`\n• એન્ટ્રી: `${current_price:,.2f}`\n• SL: `${fibo_sl:,.2f}`")
            except Exception as ex:
                print(f"Delta Order Error: {ex}")
                send_telegram(f"⚠️ *ડેલ્ટા ઓર્ડર રિજેક્ટ થયો!*\n• કોઈન: `{delta_symbol}`\n• ભૂલ: `{ex}`")

def bot_worker():
    send_telegram("🚀 *Render ક્લાઉડ પર બોટ લાઈવ થયો (ડૉલર ફોર્મેટ)!*")
    last_heartbeat = time.time()
    
    while True:
        if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
            try:
                send_heartbeat()
            except Exception:
                pass
            last_heartbeat = time.time()
            
        for p in PAIRS:
            try:
                process_pair(p)
                time.sleep(1)
            except Exception:
                time.sleep(1)
        time.sleep(10)

threading.Thread(target=bot_worker, daemon=True).start()

# ==========================================================
# ૫. મુખ્ય વેબ સર્વર
# ==========================================================
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
