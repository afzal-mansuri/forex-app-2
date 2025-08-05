import MetaTrader5 as mt5
import pandas as pd
import time
from datetime import datetime, date

# === Config ===
symbol = "EURUSD"
sl_pips = 5
tp_pips = 10
sma_period = 50
rsi_period = 14
risk_percent = 1.0
magic_number = 123456
timeframe = mt5.TIMEFRAME_M5
bars = 100

loop_interval_minutes = 5
daily_max_loss = 50.0  # in account currency
daily_loss_reset_hour = 0  # 0 = reset at midnight

# === Track Losses ===
daily_realized_loss = 0.0
last_reset_date = date.today()

# === Initialize MT5 ===
if not mt5.initialize():
    print("initialize() failed:", mt5.last_error())
    quit()

# === Ensure Symbol is Selected ===
if not mt5.symbol_select(symbol, True):
    print(f"Failed to select symbol {symbol}")
    mt5.shutdown()
    quit()

print(f"🔁 Starting live trading loop for {symbol}...")

# === Trading Loop ===
while True:
    now = datetime.now()

    # Reset daily loss at specified hour
    if date.today() != last_reset_date and now.hour >= daily_loss_reset_hour:
        daily_realized_loss = 0.0
        last_reset_date = date.today()
        print(f"[{now}] 🔁 Daily loss reset.")

    # Stop trading if max loss reached
    if daily_realized_loss >= daily_max_loss:
        print(f"[{now}] 🛑 Max daily loss of ${daily_max_loss} reached. Skipping trade.")
        time.sleep(loop_interval_minutes * 60)
        continue

    # === Account Info ===
    account = mt5.account_info()
    if account is None:
        print("Failed to get account info")
        break
    balance = account.balance

    # === Get Market Data ===
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
    if rates is None:
        print("Failed to get market data")
        break

    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df['close'] = df['close']
    df['sma'] = df['close'].rolling(sma_period).mean()

    # RSI calculation
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(rsi_period).mean()
    loss = -delta.where(delta < 0, 0).rolling(rsi_period).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))

    latest = df.iloc[-1]
    close_price = latest['close']
    sma = latest['sma']
    rsi = latest['rsi']

    # === Get Symbol Info ===
    symbol_info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if not symbol_info or not tick:
        print("Failed to get symbol info or tick")
        break

    point = symbol_info.point
    tick_size = symbol_info.trade_tick_size
    tick_value = symbol_info.trade_tick_value
    pip_value_per_lot = tick_value / tick_size

    # === Calculate Lot Size Based on Risk ===
    risk_money = balance * (risk_percent / 100)
    lot = round(risk_money / (sl_pips * pip_value_per_lot), 2)
    if lot < 0.01:
        lot = 0.01

    # === Entry Conditions ===
    def should_buy():
        return close_price > sma and rsi < 30

    def should_sell():
        return close_price < sma and rsi > 70

    # === Trade Execution ===
    def place_trade(order_type, price, sl, tp):
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot,
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 5,
            "magic": magic_number,
            "comment": "Risk-Managed Algo Trade",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            print(f"[{datetime.now()}] ✅ Trade placed: Ticket #{result.order}")
        else:
            print(f"[{datetime.now()}] ❌ Trade failed: Code {result.retcode}")
        return result

    # === Check and Place Order ===
    if should_buy():
        price = tick.ask
        sl = price - sl_pips * 10 * point
        tp = price + tp_pips * 10 * point
        result = place_trade(mt5.ORDER_TYPE_BUY, price, sl, tp)

    elif should_sell():
        price = tick.bid
        sl = price + sl_pips * 10 * point
        tp = price - tp_pips * 10 * point
        result = place_trade(mt5.ORDER_TYPE_SELL, price, sl, tp)

    else:
        print(f"[{datetime.now()}] ⚠️ No trade conditions met.")

    # === Check for Closed Orders in Last X Minutes and Update Daily Loss ===
    from_time = datetime.combine(date.today(), datetime.min.time())
    closed_orders = mt5.history_deals_get(from_time, now)

    daily_realized_loss = 0.0
    if closed_orders:
        for deal in closed_orders:
            if deal.magic == magic_number and deal.type == mt5.DEAL_TYPE_SELL or deal.type == mt5.DEAL_TYPE_BUY:
                daily_realized_loss += -deal.profit if deal.profit < 0 else 0.0

    print(f"[{now}] 📉 Current daily loss: ${round(daily_realized_loss, 2)}")

    # === Sleep Until Next Loop ===
    time.sleep(loop_interval_minutes * 60)

# === Shutdown ===
mt5.shutdown()
