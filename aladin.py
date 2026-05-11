import sys
# Windows Terminal Encoding Fix
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

import ccxt
import pandas as pd
import pandas_ta as ta
import time

# ==========================================
# 1. BOT CONFIGURATIONS (Edit Here)
# ==========================================
API_KEY = 'Z6BO7KI3cSaOnUKYasUEII8wRt4yCZGPXHlyl17xJRp1HNpKlk9QjGhrtPwjY0u91kTe8UHmy7QQ0kBIRSsFbw'       
SECRET_KEY = 'qbOvnF3oLWvnSwRVt1c94aYOZ86BK3NnnotXBVCUQxa9ZNWixgjtF6Og37upouZHdBbs6ugHkWKXV0zsUJQ' 

# Multiple Coins ki List
SYMBOLS = ['RESOLV-USDT', 'SOL-USDT', 'ETH-USDT']

TIMEFRAME = '15m'
LEVERAGE = 10

# Margin & Risk Settings
MARGIN_PERCENT = 10.0     # Wallet ka kitna % per trade use karna hai
USE_SL_CAP = True        # True = SL 1% se bada hua toh reject nahi karega, 1% par fix kar dega
MAX_SL_PCT = 1.0         # Max allowed SL % (Cap Limit)

# Strategy Logic Switch
USE_CROSSOVER_LOGIC = False # True = Sirf RSI/SMA Crossover pe trade lega. False = SMA Slope/Color change pe trade lega.

# VWAP Filter Setting
USE_VWAP_FILTER = False   # True = VWAP k upar hi Long lega, aur niche hi Short lega

# Indicator Settings
RSI_LEN = 50
SMA_LEN = 40
SWING_LEN = 5

# 🟢 NAYA FEATURE: BOT MEMORY (Over-triggering fix)
# Ye dictionary yaad rakhegi ki kis coin pe kis time trade liya gaya tha
last_traded_candles = {sym: None for sym in SYMBOLS}

# ==========================================
# 2. EXCHANGE SETUP
# ==========================================
print("[SYSTEM] Connecting to BingX API...")
exchange = ccxt.bingx({
    'apiKey': API_KEY,
    'secret': SECRET_KEY,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'swap',               
        'positionMode': True,                
        'adjustForTimeDifference': True      
    }
})

try:
    exchange.load_markets()
    for sym in SYMBOLS:
        try:
            exchange.set_leverage(LEVERAGE, sym)
            print(f"[SYSTEM] Leverage set to {LEVERAGE}x for {sym}")
        except Exception:
            pass 
    print("[SYSTEM] All leverages checked successfully.")
except Exception as e:
    print(f"[ERROR] Connection failed: {e}")

# ==========================================
# 3. DYNAMIC MARGIN CALCULATION
# ==========================================
def get_compounded_margin(symbol):
    try:
        balance = exchange.fetch_balance()
        available_usdt = float(balance['USDT']['free'])
        calculated_margin = available_usdt * (MARGIN_PERCENT / 100.0)
        ticker = exchange.fetch_ticker(symbol)
        current_price = float(ticker['last'])
        
        raw_qty = (calculated_margin * LEVERAGE) / current_price
        final_qty = float(exchange.amount_to_precision(symbol, raw_qty))
        
        return final_qty
    except Exception as e:
        print(f"[ERROR] Failed to calculate margin for {symbol}: {e}")
        return None

# ==========================================
# 4. HEDGE TRADE EXECUTION
# ==========================================
def execute_hedge_trade(symbol, position_side, entry_price, sl_price):
    try:
        dynamic_qty = get_compounded_margin(symbol)
        if dynamic_qty is None or dynamic_qty <= 0:
            print(f"[ERROR] Invalid Trade Quantity for {symbol}. Skipping.")
            return

        risk = abs(entry_price - sl_price)
        
        if position_side == 'LONG':
            entry_side, exit_side = 'buy', 'sell'
            tp_price = entry_price + (risk * 3)
        else: # SHORT
            entry_side, exit_side = 'sell', 'buy'
            tp_price = entry_price - (risk * 3)
            
        print(f"[EXECUTE] Opening HEDGE {position_side} on {symbol} | QTY: {dynamic_qty} | EP: {entry_price} | SL: {sl_price:.4f} | TP: {tp_price:.4f}")
        
        exchange.create_order(symbol=symbol, type='MARKET', side=entry_side, amount=dynamic_qty, params={'positionSide': position_side})
        exchange.create_order(symbol=symbol, type='STOP_MARKET', side=exit_side, amount=dynamic_qty, params={'stopPrice': sl_price, 'positionSide': position_side})
        exchange.create_order(symbol=symbol, type='TAKE_PROFIT_MARKET', side=exit_side, amount=dynamic_qty, params={'stopPrice': tp_price, 'positionSide': position_side})
        
        print(f"[SUCCESS] {position_side} Trade placed for {symbol} with TP/SL!")
        time.sleep(3) 
        
    except Exception as e:
        print(f"[ERROR] Order execution failed for {symbol}: {e}")

# ==========================================
# 5. STRATEGY & LOGIC EVALUATION
# ==========================================
def fetch_data_and_check_signal(symbol):
    global last_traded_candles # Global memory ko access karne ke liye
    
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=1000)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # 1. RSI & SMA Logic
        df['rsi'] = ta.rsi(df['close'], length=RSI_LEN)
        df['sma'] = ta.sma(df['rsi'], length=SMA_LEN)
        
        df['sma_prev'] = df['sma'].shift(1)
        df['is_green'] = df['sma'] > df['sma_prev']
        df['is_red'] = df['sma'] < df['sma_prev']
        
        df['recent_low'] = df['low'].rolling(window=SWING_LEN).min().shift(1)
        df['recent_high'] = df['high'].rolling(window=SWING_LEN).max().shift(1)
        
        # 2. VWAP Logic
        df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
        df['date'] = df['datetime'].dt.date 
        
        df['typical_price'] = (df['high'] + df['low'] + df['close']) / 3
        df['tp_vol'] = df['typical_price'] * df['volume']
        
        df['cum_tp_vol'] = df.groupby('date')['tp_vol'].cumsum()
        df['cum_vol'] = df.groupby('date')['volume'].cumsum()
        df['vwap'] = df['cum_tp_vol'] / df['cum_vol']
        
        last_closed = df.iloc[-2]
        prev_closed = df.iloc[-3]
        
        current_price = last_closed['close']
        current_vwap = last_closed['vwap']
        current_candle_time = last_closed['timestamp'] # Is candle ki unique ID (Time)
        
        # 3. STRATEGY LOGIC
        if USE_CROSSOVER_LOGIC:
            long_condition = (prev_closed['rsi'] <= prev_closed['sma']) and (last_closed['rsi'] > last_closed['sma'])
            short_condition = (prev_closed['rsi'] >= prev_closed['sma']) and (last_closed['rsi'] < last_closed['sma'])
            logic_name = "CROSSOVER"
        else:
            long_condition = (last_closed['is_green'] and not prev_closed['is_green'] and (last_closed['rsi'] > last_closed['sma']))
            short_condition = (last_closed['is_red'] and not prev_closed['is_red'] and (last_closed['rsi'] < last_closed['sma']))
            logic_name = "SLOPE"

        vwap_long_ok = (not USE_VWAP_FILTER) or (current_price > current_vwap)
        vwap_short_ok = (not USE_VWAP_FILTER) or (current_price < current_vwap)
        
        # --- CHECK LONG ---
        if long_condition:
            # Check if already traded on this specific candle
            if last_traded_candles[symbol] == current_candle_time:
                print(f"[SKIP] {symbol} Already took LONG on this candle. Waiting for new signal...")
                return

            sl_price = last_closed['recent_low']
            risk_pct = ((current_price - sl_price) / current_price) * 100
            
            if not vwap_long_ok:
                print(f"[FILTER] {logic_name} Long Rejected on {symbol}. Price ({current_price}) is BELOW VWAP ({current_vwap:.4f})")
            else:
                if USE_SL_CAP and risk_pct > MAX_SL_PCT:
                    print(f"[ADJUST] {symbol} Long SL was {risk_pct:.2f}%. Capping to {MAX_SL_PCT}%")
                    sl_price = current_price * (1 - (MAX_SL_PCT / 100.0))
                    risk_pct = MAX_SL_PCT

                print(f"[{logic_name} LONG] VALID SIGNAL on {symbol} | Price: {current_price} | Risk: {risk_pct:.2f}%")
                execute_hedge_trade(symbol, 'LONG', current_price, sl_price)
                last_traded_candles[symbol] = current_candle_time # Memory update kar di
                
        # --- CHECK SHORT ---
        elif short_condition:
            # Check if already traded on this specific candle
            if last_traded_candles[symbol] == current_candle_time:
                print(f"[SKIP] {symbol} Already took SHORT on this candle. Waiting for new signal...")
                return

            sl_price = last_closed['recent_high']
            risk_pct = ((sl_price - current_price) / current_price) * 100
            
            if not vwap_short_ok:
                print(f"[FILTER] {logic_name} Short Rejected on {symbol}. Price ({current_price}) is ABOVE VWAP ({current_vwap:.4f})")
            else:
                if USE_SL_CAP and risk_pct > MAX_SL_PCT:
                    print(f"[ADJUST] {symbol} Short SL was {risk_pct:.2f}%. Capping to {MAX_SL_PCT}%")
                    sl_price = current_price * (1 + (MAX_SL_PCT / 100.0))
                    risk_pct = MAX_SL_PCT

                print(f"[{logic_name} SHORT] VALID SIGNAL on {symbol} | Price: {current_price} | Risk: {risk_pct:.2f}%")
                execute_hedge_trade(symbol, 'SHORT', current_price, sl_price)
                last_traded_candles[symbol] = current_candle_time # Memory update kar di
                
        else:
            print(f"[SCAN] {symbol} | Price: {current_price} | VWAP: {current_vwap:.4f} | Waiting...")
            
    except Exception as e:
        print(f"[ERROR] Fetching data for {symbol}: {e}")

# ==========================================
# 6. MAIN BOT LOOP (Multi-Coin Scanner)
# ==========================================
print(f"\n[SYSTEM] Multi-Coin Bot started. Crossover Logic: {USE_CROSSOVER_LOGIC}")
while True:
    print("\n--- Starting New Scan Cycle ---")
    for symbol in SYMBOLS:
        fetch_data_and_check_signal(symbol)
        time.sleep(1) 
        
    time.sleep(30)