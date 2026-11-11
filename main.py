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
# ૨. કન્ફિગરેશન (Render Environment Variables માંથી ઓટો-રીડ)
# ==========================================================
DELTA_API_KEY = os.environ.get('DELTA_API_KEY')
DELTA_API_SECRET = os.environ.get('DELTA_API_SECRET')

TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

USD_TO_INR = 88.0
PAIRS = [
    {'delta_sym': 'BTC/USDT', 'data_sym': 'BTC/USD'},
    {'delta_sym': 'ETH/USDT', 'data_sym': 'ETH/USD'}
]

TIMEFRAME = '5m'
LOOKBACK_CANDLES = 30
ORDER_SIZE = 1
MIN_RANGE_INR = 15000
TRAILING_GAP = 0.005

# ==========================================================
# ૩. એક્સચેન્જ કનેક્શન
# ==========================================================
data_exchange = ccxt.bitstamp({'enableRateLimit': True})

trade_exchange = ccxt.delta({
    'apiKey': DELTA_API_KEY,
    'secret': DELTA_API_SECRET,
    'enableRateLimit': True,
})
trade_exchange.set_sandbox_mode(True)  # ટેસ્ટનેટ મોડ

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

# ==========================================================
# ૪. ટ્રેડિંગ અને ઓર્ડર લોજિક (એરર ટ્રેકિંગ સાથે)
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
    current_price_inr = current_price * USD_TO_INR
    
    swing_high, swing_low, trend = get_swing_points(df, lookback=LOOKBACK_CANDLES)
    fibo_range = swing_high - swing_low
    fibo_range_inr = fibo_range * USD_TO_INR

    if fibo_range_inr < MIN_RANGE_INR:
        return

    # ટ્રેઇલિંગ સ્ટોપલોસ હેન્ડલિંગ
    if state['in_trade']:
        side = state['direction']
        if side == 'SHORT':
            if current_price < state['best_price']:
                state['best_price'] = current_price
                new_sl = current_price * (1 + TRAILING_GAP)
                if new_sl < state['stop_loss']:
                    state['stop_loss'] = new_sl
                    send_telegram(f"📉 *શોર્ટ ટ્રેઇલિંગ SL અપડેટ*\n• કોઈન: `{delta_symbol}`\n• ભાવ: `₹{current_price_inr:,.0f}`\n• નવો SL: `₹{new_sl * USD_TO_INR:,.0f}`")
            
            if current_price >= state['stop_loss']:
                try:
                    trade_exchange.create_order(delta_symbol, 'market', 'buy', ORDER_SIZE)
                    state['in_trade'] = False
                    send_telegram(f"🛑 *શોર્ટ સ્ટોપલોસ હિટ (Exit)*\n• કોઈન: `{delta_symbol}`\n• એક્ઝિટ ભાવ: `₹{current_price_inr:,.0f}`")
                except Exception as ex:
                    send_telegram(f"⚠️ *ડેલ્ટા એક્ઝિટ ઓર્ડર ફેલ!*\n• વિગત: `{ex}`")

        elif side == 'LONG':
            if current_price > state['best_price']:
                state['best_price'] = current_price
                new_sl = current_price * (1 - TRAILING_GAP)
                if new_sl > state['stop_loss']:
                    state['stop_loss'] = new_sl
                    send_telegram(f"📈 *લોંગ ટ્રેઇલિંગ SL અપડેટ*\n• કોઈન: `{delta_symbol}`\n• ભાવ: `₹{current_price_inr:,.0f}`\n• નવો SL: `₹{new_sl * USD_TO_INR:,.0f}`")
            
            if current_price <= state['stop_loss']:
                try:
                    trade_exchange.create_order(delta_symbol, 'market', 'sell', ORDER_SIZE)
                    state['in_trade'] = False
                    send_telegram(f"🛑 *લોંગ સ્ટોપલોસ હિટ (Exit)*\n• કોઈન: `{delta_symbol}`\n• એક્ઝિટ ભાવ: `₹{current_price_inr:,.0f}`")
                except Exception as ex:
                    send_telegram(f"⚠️ *ડેલ્ટા એક્ઝિટ ઓર્ડર ફેલ!*\n• વિગત: `{ex}`")
        return

    # નવી એન્ટ્રી શરતો
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
                send_telegram(f"🔴 *૫-મિનિટ શોર્ટ (SELL) ઓર્ડર થયો!*\n• કોઈન: `{delta_symbol}`\n• એન્ટ્રી: `₹{current_price_inr:,.0f}`\n• SL: `₹{fibo_sl * USD_TO_INR:,.0f}`")
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
                send_telegram(f"🟢 *૫-મિનિટ લોંગ (BUY) ઓર્ડર થયો!*\n• કોઈન: `{delta_symbol}`\n• એન્ટ્રી: `₹{current_price_inr:,.0f}`\n• SL: `₹{fibo_sl * USD_TO_INR:,.0f}`")
            except Exception as ex:
                print(f"Delta Order Error: {ex}")
                send_telegram(f"⚠️ *ડેલ્ટા ઓર્ડર રિજેક્ટ થયો!*\n• કોઈન: `{delta_symbol}`\n• ભૂલ: `{ex}`")

def bot_worker():
    send_telegram("🚀 *Render ક્લાઉડ પર બોટ લાઈવ થયો!*")
    while True:
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
