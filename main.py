# -*- coding: utf-8 -*-
import os
import json
import sys
import time
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from mootdx.quotes import Quotes
import warnings
# ==========================================
# 🔧 类型转换工具（用于JSON序列化）
# ==========================================
def convert_numpy(obj):
    """将numpy类型转换为Python原生类型，以便JSON序列化"""
    if isinstance(obj, dict):
        return {k: convert_numpy(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_numpy(v) for v in obj]
    elif isinstance(obj, tuple):
        return tuple(convert_numpy(v) for v in obj)
    elif isinstance(obj, (np.integer, np.floating)):
        return obj.item()
    elif isinstance(obj, np.bool_):
        return bool(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    else:
        return obj
warnings.filterwarnings('ignore')

# ==========================================
# ⚙️ 全局配置
# ==========================================
# 左侧策略参数（与原 main.py 一致）
P1 = 8.0
P2 = 9.0
BIAS_THRESH = 6.0

# 右侧策略参数
TAKE_PROFIT_PCT = 12.0   # 止盈百分比
STOP_LOSS_PCT = 4.0      # 止损百分比

# 文件路径
EXCEL_LIST = 'stock_list.xlsx'                # 股票池
LEFT_HISTORY_FILE = 'left_history.json'       # 左侧历史推荐
RIGHT_HISTORY_FILE = 'right_history.json'     # 右侧历史推荐
DAILY_CANDIDATES_FILE = 'daily_candidates.json' # 当日候选
HTML_OUTPUT = 'index.html'                     # 生成的看板

# ==========================================
# 📦 JSON 读写工具
# ==========================================
def load_history(file_path):
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_history(data, file_path):
    data = convert_numpy(data)  # 转换numpy类型
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# ==========================================
# 📊 股票分析函数（返回左右侧信号及指标）
# ==========================================
def analyze_stock(stock_info, client):
    symbol = stock_info['code']
    try:
        # 获取最近100根K线（日线）
        df = client.bars(symbol=symbol, frequency=9, offset=100)
        if df is None or len(df) < 60:
            return None

        df.rename(columns={'datetime':'日期','open':'开盘','close':'收盘','high':'最高','low':'最低','vol':'成交量'}, inplace=True)
        for col in ['开盘', '收盘', '最高', '最低', '成交量']:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        # ---------- 通用指标 ----------
        df['MA20'] = df['收盘'].rolling(20).mean()
        df['MA60'] = df['收盘'].rolling(60).mean()
        df['MA5'] = df['收盘'].rolling(5).mean()
        df['MA10'] = df['收盘'].rolling(10).mean()
        df['VOL_MA5'] = df['成交量'].rolling(5).mean()

        # ---------- 左侧策略指标 ----------
        df['VAR1'] = (df['收盘'] + df['最高'] + df['开盘'] + df['最低']) / 4
        df['MID'] = df['VAR1'].ewm(span=32, adjust=False).mean()
        df['UPPER'] = df['MID'] * (1 + P1 / 100.0)
        df['LOWER'] = df['MID'] * (1 - P2 / 100.0)
        df['BIAS_VAL'] = (df['收盘'] - df['MA20']) / df['MA20'] * 100

        # ---------- 右侧策略指标 ----------
        df['MA20_Slope'] = (df['MA20'] / df['MA20'].shift(1) - 1) * 100
        df['MA20_Angle'] = np.degrees(np.arctan(df['MA20_Slope']))
        exp12 = df['收盘'].ewm(span=12, adjust=False).mean()
        exp26 = df['收盘'].ewm(span=26, adjust=False).mean()
        df['DIF'] = exp12 - exp26
        df['DEA'] = df['DIF'].ewm(span=9, adjust=False).mean()

        # 确保右侧指标列存在（防止因全 NaN 导致列未创建）
        for col in ['MA20_Angle', 'DIF', 'DEA']:
            if col not in df.columns:
                df[col] = np.nan

        # 最新一根K线
        curr = df.iloc[-1]
        prev = df.iloc[-2]

        # 左侧买入条件
        bias_ok = curr['BIAS_VAL'] < -BIAS_THRESH
        b_cond1 = (curr['最低'] <= curr['LOWER']) and bias_ok
        b_cond2 = (curr['收盘'] > curr['开盘']) and ((curr['收盘'] - curr['最低']) > (curr['最高'] - curr['收盘']))
        left_buy_signal = b_cond1 and b_cond2

        # 涨跌停判断（简单按板块区分）
        prev_close = prev['收盘']
        if symbol.startswith('688') or symbol.startswith('30'):
            limit_pct = 0.20
        else:
            limit_pct = 0.10
        limit_up_price = round(prev_close * (1 + limit_pct), 2)
        limit_down_price = round(prev_close * (1 - limit_pct), 2)
        # 用容差判断是否涨跌停
        is_limit_up = curr['收盘'] >= (limit_up_price - 0.015)
        is_limit_down = curr['收盘'] <= (limit_down_price + 0.015)

        # 右侧买入条件（全部基于最新K线）
        # 使用 .get() 安全获取，避免 KeyError，同时处理 NaN
        ma20_angle = curr['MA20_Angle'] if pd.notna(curr['MA20_Angle']) else 0.0
        dif = curr['DIF'] if pd.notna(curr['DIF']) else 0.0
        dea = curr['DEA'] if pd.notna(curr['DEA']) else 0.0

        cond_angle = ma20_angle > 25
        cond_trend = (curr['收盘'] > curr['MA10']) and (curr['MA5'] > curr['MA20']) and (curr['MA20'] > curr['MA60']) and (curr['MA60'] > prev['MA60'])
        cond_power = (curr['收盘'] / prev['收盘'] > 1.03) and (curr['收盘'] > curr['开盘'])
        cond_vol = curr['成交量'] > curr['VOL_MA5']
        cond_macd = (dif > 0) and (dif > dea)
        cond_not_limit = not (is_limit_up or is_limit_down)

        right_buy_signal = cond_angle and cond_trend and cond_power and cond_vol and cond_macd and cond_not_limit

        # 返回包含所有需要字段的字典（确保数值类型）
        return {
            'code': symbol,
            'name': stock_info['name'],
            'price': float(curr['收盘']) if pd.notna(curr['收盘']) else 0.0,
            'low': float(curr['最低']) if pd.notna(curr['最低']) else 0.0,
            'bias_val': float(curr['BIAS_VAL']) if pd.notna(curr['BIAS_VAL']) else 0.0,
            'left_buy_signal': left_buy_signal,
            'right_buy_signal': right_buy_signal,
            'is_limit_up': bool(is_limit_up),
            'is_limit_down': bool(is_limit_down),
            'ma20_angle': ma20_angle,
            'dif': dif,
            'dea': dea
        }
    except Exception as e:
        print(f"分析 {symbol} 出错: {e}")
        return None
# ==========================================
# 📈 更新历史记录（最新价、止盈止损）
# ==========================================
def update_history(strategy, history_file, market_data):
    history = load_history(history_file)
    today = datetime.now().strftime('%Y-%m-%d')
    updated = False
    for rec in history:
        # 如果已止盈或止损，不再更新
        if rec.get('take_profit_date') or rec.get('stop_loss_date'):
            continue
        code = rec['code']
        if code in market_data:
            current_price = market_data[code]['price']
            rec['latest_price'] = current_price
            rec['latest_update'] = today
            # 计算涨跌幅
            pct_change = (current_price / rec['price'] - 1) * 100
            if pct_change >= TAKE_PROFIT_PCT:
                rec['take_profit_date'] = today
            elif pct_change <= -STOP_LOSS_PCT:
                rec['stop_loss_date'] = today
            updated = True
    if updated:
        save_history(history, history_file)
    return history

# ==========================================
# 🎯 选出今日候选（按策略排序，取前5）
# ==========================================
def select_today_candidates(market_data, strategy):
    candidates = []
    for code, data in market_data.items():
        if data is None:
            continue
        if strategy == 'left' and data.get('left_buy_signal'):
            candidates.append(data)
        elif strategy == 'right' and data.get('right_buy_signal'):
            candidates.append(data)
    if strategy == 'left':
        # 左侧按乖离率升序（越负越好）
        candidates.sort(key=lambda x: x['bias_val'])
    else:
        # 右侧按MA20角度降序（角度越大越好）
        candidates.sort(key=lambda x: x['ma20_angle'], reverse=True)
    return candidates[:5]

# ==========================================
# 🖥️ 生成 HTML 看板
# ==========================================
def generate_dashboard(today, now_time, market_data):
    # 加载今日候选
    if os.path.exists(DAILY_CANDIDATES_FILE):
        with open(DAILY_CANDIDATES_FILE, 'r', encoding='utf-8') as f:
            daily = json.load(f)
    else:
        daily = {'date': today, 'left': [], 'right': []}
    left_today = daily.get('left', [])
    right_today = daily.get('right', [])

    # 加载历史
    left_history = load_history(LEFT_HISTORY_FILE)
    right_history = load_history(RIGHT_HISTORY_FILE)

    # 按推荐日期倒序排列
    left_history.sort(key=lambda x: x['date'], reverse=True)
    right_history.sort(key=lambda x: x['date'], reverse=True)

    # 限制显示数量（例如各显示10条）
    left_display = left_history[:10]
    right_display = right_history[:10]

    # 构建HTML
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI策略选股看板</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body {{ background-color: #f8f9fa; font-family: 'Microsoft YaHei'; padding: 20px; }}
        .table th {{ background-color: #e9ecef; }}
        .positive {{ color: #dc3545; font-weight: bold; }}
        .negative {{ color: #198754; font-weight: bold; }}
        .badge-tp {{ background-color: #28a745; color: white; padding: 3px 8px; border-radius: 10px; }}
        .badge-sl {{ background-color: #dc3545; color: white; padding: 3px 8px; border-radius: 10px; }}
        .card {{ margin-bottom: 20px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }}
        .card-header {{ font-weight: bold; }}
    </style>
</head>
<body>
    <h2 class="mb-4">📈 AI 策略选股看板 <small class="text-muted" style="font-size:1rem;">更新时间: {now_time}</small></h2>
    
    <div class="row">
        <!-- 左侧策略今日候选 -->
        <div class="col-md-6">
            <div class="card">
                <div class="card-header bg-primary text-white">左侧抄底策略 - 今日候选 (5只)</div>
                <div class="card-body">
                    <table class="table table-bordered table-hover">
                        <thead><tr><th>代码</th><th>名称</th><th>最新价</th><th>乖离率%</th></tr></thead>
                        <tbody>
    """
    for s in left_today:
        html += f"<tr><td>{s['code']}</td><td>{s['name']}</td><td>{s['price']:.2f}</td><td>{s['bias_val']:.2f}</td></tr>"
    html += """
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
        <!-- 右侧策略今日候选 -->
        <div class="col-md-6">
            <div class="card">
                <div class="card-header bg-success text-white">右侧追涨策略 - 今日候选 (5只)</div>
                <div class="card-body">
                    <table class="table table-bordered table-hover">
                        <thead><tr><th>代码</th><th>名称</th><th>最新价</th><th>MA20角度</th></tr></thead>
                        <tbody>
    """
    for s in right_today:
        html += f"<tr><td>{s['code']}</td><td>{s['name']}</td><td>{s['price']:.2f}</td><td>{s['ma20_angle']:.2f}</td></tr>"
    html += """
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>

    <div class="row">
        <!-- 左侧历史表现 -->
        <div class="col-md-6">
            <div class="card">
                <div class="card-header bg-secondary text-white">历史左侧股票表现</div>
                <div class="card-body">
                    <table class="table table-bordered table-hover">
                        <thead><tr><th>推荐日期</th><th>代码</th><th>名称</th><th>推荐价</th><th>最新价</th><th>涨跌幅%</th><th>止盈日期</th><th>止损日期</th></tr></thead>
                        <tbody>
    """
    for rec in left_display:
        pct = (rec['latest_price'] / rec['price'] - 1) * 100
        color_class = 'positive' if pct > 0 else 'negative' if pct < 0 else ''
        tp = rec.get('take_profit_date') or '-'
        sl = rec.get('stop_loss_date') or '-'
        html += f"<tr><td>{rec['date']}</td><td>{rec['code']}</td><td>{rec['name']}</td><td>{rec['price']:.2f}</td><td>{rec['latest_price']:.2f}</td><td class='{color_class}'>{pct:.2f}%</td><td>{tp}</td><td>{sl}</td></tr>"
    html += """
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
        <!-- 右侧历史表现 -->
        <div class="col-md-6">
            <div class="card">
                <div class="card-header bg-secondary text-white">历史右侧股票表现</div>
                <div class="card-body">
                    <table class="table table-bordered table-hover">
                        <thead><tr><th>推荐日期</th><th>代码</th><th>名称</th><th>推荐价</th><th>最新价</th><th>涨跌幅%</th><th>止盈日期</th><th>止损日期</th></tr></thead>
                        <tbody>
    """
    for rec in right_display:
        pct = (rec['latest_price'] / rec['price'] - 1) * 100
        color_class = 'positive' if pct > 0 else 'negative' if pct < 0 else ''
        tp = rec.get('take_profit_date') or '-'
        sl = rec.get('stop_loss_date') or '-'
        html += f"<tr><td>{rec['date']}</td><td>{rec['code']}</td><td>{rec['name']}</td><td>{rec['price']:.2f}</td><td>{rec['latest_price']:.2f}</td><td class='{color_class}'>{pct:.2f}%</td><td>{tp}</td><td>{sl}</td></tr>"
    html += """
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>
</body>
</html>
    """
    with open(HTML_OUTPUT, 'w', encoding='utf-8') as f:
        f.write(html)
    print("HTML看板已生成")

# ==========================================
# 🚀 主程序入口
# ==========================================
if __name__ == '__main__':
    # 解析运行模式
    mode = 'candidates'  # 默认
    if len(sys.argv) > 1:
        mode = sys.argv[1]  # 传入 'candidates' 或 'history'

    # 获取北京时间
    utc_now = datetime.now(timezone.utc)
    beijing_now = utc_now + timedelta(hours=8)
    today = beijing_now.strftime('%Y-%m-%d')
    now_time = beijing_now.strftime('%Y-%m-%d %H:%M:%S')
    print(f"当前北京时间: {now_time}，运行模式: {mode}")

    # 加载股票池
    if not os.path.exists(EXCEL_LIST):
        print(f"错误: 找不到股票池文件 {EXCEL_LIST}")
        sys.exit(1)
    meta_df = pd.read_excel(EXCEL_LIST, usecols=[0, 1])
    meta_df.columns = ['code', 'name']
    meta_df.dropna(subset=['code'], inplace=True)
    meta_df['code'] = meta_df['code'].astype(str).str.replace(r'\.0$', '', regex=True).str.zfill(6)
    stock_list = meta_df.to_dict('records')
    print(f"股票池共 {len(stock_list)} 只股票")

    # 连接行情
    client = Quotes.factory(market='std', multithread=True, heartbeat=True)
    market_data = {}

    # 并行获取行情数据
    print("正在获取行情数据...")
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(analyze_stock, stock, client): stock['code'] for stock in stock_list}
        for future in as_completed(futures):
            res = future.result()
            if res:
                market_data[res['code']] = res
    print(f"成功获取 {len(market_data)} 只股票数据")

    # 根据模式执行不同任务
    if mode == 'candidates':
        # 选出今日左右侧候选
        left_candidates = select_today_candidates(market_data, 'left')
        right_candidates = select_today_candidates(market_data, 'right')
        print(f"左侧候选数量: {len(left_candidates)}，右侧候选数量: {len(right_candidates)}")

        # 转换numpy类型为Python原生类型（确保JSON可序列化）
        left_candidates = convert_numpy(left_candidates)
        right_candidates = convert_numpy(right_candidates)

        # 保存今日候选
        daily = {
            'date': today,
            'left': left_candidates,
            'right': right_candidates
        }
        with open(DAILY_CANDIDATES_FILE, 'w', encoding='utf-8') as f:
            json.dump(daily, f, ensure_ascii=False, indent=4)

        # 将今日候选追加到历史记录（去重：同一天同一股票不重复）
        left_history = load_history(LEFT_HISTORY_FILE)
        right_history = load_history(RIGHT_HISTORY_FILE)

        existing_left = {(rec['code'], rec['date']) for rec in left_history}
        for cand in left_candidates:
            key = (cand['code'], today)
            if key not in existing_left:
                left_history.append({
                    'code': cand['code'],
                    'name': cand['name'],
                    'date': today,
                    'price': cand['price'],
                    'latest_price': cand['price'],
                    'latest_update': today,
                    'take_profit_date': None,
                    'stop_loss_date': None,
                    'strategy': 'left'
                })
        save_history(left_history, LEFT_HISTORY_FILE)

        existing_right = {(rec['code'], rec['date']) for rec in right_history}
        for cand in right_candidates:
            key = (cand['code'], today)
            if key not in existing_right:
                right_history.append({
                    'code': cand['code'],
                    'name': cand['name'],
                    'date': today,
                    'price': cand['price'],
                    'latest_price': cand['price'],
                    'latest_update': today,
                    'take_profit_date': None,
                    'stop_loss_date': None,
                    'strategy': 'right'
                })
        save_history(right_history, RIGHT_HISTORY_FILE)

    elif mode == 'history':
        # 更新历史记录（最新价、止盈止损）
        update_history('left', LEFT_HISTORY_FILE, market_data)
        update_history('right', RIGHT_HISTORY_FILE, market_data)
        print("历史记录已更新")

    else:
        print(f"未知模式: {mode}，退出")
        sys.exit(1)

    # 最后生成HTML看板（两种模式都生成）
    generate_dashboard(today, now_time, market_data)
    print("任务完成")
