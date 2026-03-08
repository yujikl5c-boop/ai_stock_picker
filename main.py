# -*- coding: utf-8 -*-
import os
import json
import sys
from pathlib import Path
import socket

# ==========================================
# 🛑 反扫描伪装
# ==========================================
_mootdx_dir = os.path.join(str(Path.home()), '.mootdx')
_config_file = os.path.join(_mootdx_dir, 'config.json')
if not os.path.exists(_config_file):
    os.makedirs(_mootdx_dir, exist_ok=True)
    with open(_config_file, 'w', encoding='utf-8') as f:
        fake_config = {"HQ": [{"name": "上海双线", "ip": "124.71.187.122", "port": 7709}], "EX": []}
        json.dump(fake_config, f)

socket.setdefaulttimeout(10) # 强制超时防卡死

import time
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from mootdx.quotes import Quotes
import warnings
warnings.filterwarnings('ignore')

# ==========================================
# ⚙️ 全局配置 (加入止盈止损参数)
# ==========================================
P1 = 8.0
P2 = 9.0
BIAS_THRESH = 6.0
TAKE_PROFIT_PCT = 12.0  # 12% 止盈
STOP_LOSS_PCT = 4.0     # 4% 止损
EXPIRE_DAYS = 8         # 8天清盘

EXCEL_LIST = 'stock_list.xlsx'
LEFT_HISTORY_FILE = 'left_history.json'
RIGHT_HISTORY_FILE = 'right_history.json'
DAILY_CANDIDATES_FILE = 'daily_candidates.json'
HTML_OUTPUT = 'index.html'

def convert_numpy(obj):
    if isinstance(obj, dict): return {k: convert_numpy(v) for k, v in obj.items()}
    elif isinstance(obj, list): return [convert_numpy(v) for v in obj]
    elif isinstance(obj, tuple): return tuple(convert_numpy(v) for v in obj)
    elif isinstance(obj, (np.integer, np.floating)): return obj.item()
    elif isinstance(obj, np.bool_): return bool(obj)
    elif isinstance(obj, np.ndarray): return obj.tolist()
    else: return obj

def load_history(file_path):
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f: return json.load(f)
    return []

def save_history(data, file_path):
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(convert_numpy(data), f, ensure_ascii=False, indent=4)

# ==========================================
# 📊 股票核心分析函数
# ==========================================
def analyze_stock(stock_info, client):
    symbol = stock_info['code']
    try:
        time.sleep(0.05) 
        df = client.bars(symbol=symbol, frequency=9, offset=100)
        if df is None or len(df) < 60: return None

        df.rename(columns={'datetime':'日期','open':'开盘','close':'收盘','high':'最高','low':'最低','vol':'成交量'}, inplace=True)
        for col in ['开盘', '收盘', '最高', '最低', '成交量']: df[col] = pd.to_numeric(df[col], errors='coerce')

        df['MA20'], df['MA60'] = df['收盘'].rolling(20).mean(), df['收盘'].rolling(60).mean()
        df['MA5'], df['MA10'] = df['收盘'].rolling(5).mean(), df['收盘'].rolling(10).mean()
        df['VOL_MA5'] = df['成交量'].rolling(5).mean()

        df['VAR1'] = (df['收盘'] + df['最高'] + df['开盘'] + df['最低']) / 4
        df['MID'] = df['VAR1'].ewm(span=32, adjust=False).mean()
        df['UPPER'], df['LOWER'] = df['MID'] * (1 + P1 / 100.0), df['MID'] * (1 - P2 / 100.0)
        df['BIAS_VAL'] = (df['收盘'] - df['MA20']) / df['MA20'] * 100

        df['MA20_Slope'] = (df['MA20'] / df['MA20'].shift(1) - 1) * 100
        df['MA20_Angle'] = np.degrees(np.arctan(df['MA20_Slope']))
        df['DIF'] = df['收盘'].ewm(span=12, adjust=False).mean() - df['收盘'].ewm(span=26, adjust=False).mean()
        df['DEA'] = df['DIF'].ewm(span=9, adjust=False).mean()

        for col in ['MA20_Angle', 'DIF', 'DEA']:
            if col not in df.columns: df[col] = np.nan

        curr, prev = df.iloc[-1], df.iloc[-2] if len(df) > 1 else df.iloc[-1]
        bias_ok = curr['BIAS_VAL'] < -BIAS_THRESH
        left_buy_signal = (curr['最低'] <= curr['LOWER']) and bias_ok and (curr['收盘'] > curr['开盘']) and ((curr['收盘'] - curr['最低']) > (curr['最高'] - curr['收盘']))

        limit_pct = 0.20 if symbol.startswith(('688', '30')) else 0.10
        is_limit_up = curr['收盘'] >= (round(prev['收盘'] * (1 + limit_pct), 2) - 0.015)
        is_limit_down = curr['收盘'] <= (round(prev['收盘'] * (1 - limit_pct), 2) + 0.015)

        ma20_angle, dif, dea = (curr['MA20_Angle'] if pd.notna(curr['MA20_Angle']) else 0.0), (curr['DIF'] if pd.notna(curr['DIF']) else 0.0), (curr['DEA'] if pd.notna(curr['DEA']) else 0.0)
        right_buy_signal = (ma20_angle > 25) and (curr['收盘'] > curr['MA10']) and (curr['MA5'] > curr['MA20']) and (curr['MA20'] > curr['MA60']) and (curr['MA60'] > prev['MA60']) and (curr['收盘'] / prev['收盘'] > 1.03) and (curr['收盘'] > curr['开盘']) and (curr['成交量'] > curr['VOL_MA5']) and (dif > 0) and (dif > dea) and not (is_limit_up or is_limit_down)

        return {
            'code': symbol, 'name': stock_info['name'], 'price': float(curr['收盘']) if pd.notna(curr['收盘']) else 0.0,
            'bias_val': float(curr['BIAS_VAL']) if pd.notna(curr['BIAS_VAL']) else 0.0, 'ma20_angle': ma20_angle,
            'left_buy_signal': left_buy_signal, 'right_buy_signal': right_buy_signal
        }
    except Exception: return None

def select_today_candidates(market_data, strategy):
    cands = [d for d in market_data.values() if d and d.get(f'{strategy}_buy_signal')]
    cands.sort(key=lambda x: x['bias_val'] if strategy == 'left' else x['ma20_angle'], reverse=(strategy != 'left'))
    return cands[:5]

# ==========================================
# 📈 历史池维护 (止盈、止损、清盘)
# ==========================================
def process_history(history, market_data, today_date):
    today_dt = datetime.strptime(today_date, '%Y-%m-%d')
    updated = False
    for rec in history:
        # 如果已经标记了出局，永远跳过不再计算
        if rec.get('take_profit_date') or rec.get('stop_loss_date') or rec.get('expire_date'):
            continue
        
        code = rec['code']
        if code in market_data:
            current_price = market_data[code]['price']
            rec['latest_price'] = current_price
            rec['latest_update'] = today_date
            updated = True
            
            # 🌟 核心：只有【非今日推荐】的股票，才计算止盈止损（第二天开始进入）
            if rec['date'] != today_date:
                pct_change = (current_price / rec['price'] - 1) * 100
                rec_dt = datetime.strptime(rec['date'], '%Y-%m-%d')
                days_held = (today_dt - rec_dt).days
                
                if pct_change >= TAKE_PROFIT_PCT:
                    rec['take_profit_date'] = today_date
                    print(f"💰 [{code}] 触发止盈: +{pct_change:.2f}%", flush=True)
                elif pct_change <= -STOP_LOSS_PCT:
                    rec['stop_loss_date'] = today_date
                    print(f"🩸 [{code}] 触发止损: {pct_change:.2f}%", flush=True)
                elif days_held >= EXPIRE_DAYS:
                    rec['expire_date'] = today_date
                    print(f"⏳ [{code}] 持仓达 {days_held} 天未达标，到期清盘", flush=True)
    return updated

# ==========================================
# 🖥️ 生成 HTML 页面
# ==========================================
def generate_dashboard(today, now_time):
    daily = json.load(open(DAILY_CANDIDATES_FILE, 'r', encoding='utf-8')) if os.path.exists(DAILY_CANDIDATES_FILE) else {'left': [], 'right': []}
    left_history = sorted(load_history(LEFT_HISTORY_FILE), key=lambda x: x['date'], reverse=True)
    right_history = sorted(load_history(RIGHT_HISTORY_FILE), key=lambda x: x['date'], reverse=True)

    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>AI策略看板</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>body {{ background: #f8f9fa; font-family: 'Microsoft YaHei'; padding: 20px; }} .positive{{color:#dc3545;font-weight:bold;}} .negative{{color:#198754;font-weight:bold;}} .badge{{font-size:0.9em;}}</style>
    </head><body><h2>📈 AI 策略选股看板 <small class="text-muted" style="font-size:1rem;">更新时间: {now_time}</small></h2>
    
    <div class="row"><div class="col-md-6"><div class="card mb-4"><div class="card-header bg-primary text-white">今日抄底 (5只)</div><div class="card-body"><table class="table">
    <thead><tr><th>代码</th><th>名称</th><th>最新价</th><th>乖离率%</th></tr></thead><tbody>"""
    for s in daily.get('left', []): html += f"<tr><td>{s['code']}</td><td>{s['name']}</td><td>{s['price']:.2f}</td><td>{s['bias_val']:.2f}</td></tr>"
    html += """</tbody></table></div></div></div><div class="col-md-6"><div class="card mb-4"><div class="card-header bg-success text-white">今日主升浪 (5只)</div><div class="card-body"><table class="table">
    <thead><tr><th>代码</th><th>名称</th><th>最新价</th><th>MA20角度</th></tr></thead><tbody>"""
    for s in daily.get('right', []): html += f"<tr><td>{s['code']}</td><td>{s['name']}</td><td>{s['price']:.2f}</td><td>{s['ma20_angle']:.2f}</td></tr>"
    
    html += """</tbody></table></div></div></div></div><div class="row"><div class="col-md-12"><div class="card mb-4"><div class="card-header bg-secondary text-white">历史抄底推荐 (无限回溯)</div><div class="card-body"><table class="table table-hover">
    <thead><tr><th>推荐日期</th><th>代码</th><th>名称</th><th>推荐价</th><th>最新价</th><th>涨跌%</th><th>状态</th></tr></thead><tbody>"""
    
    for r in left_history: 
        pct = (r['latest_price']/r['price']-1)*100
        status = "-"
        if r.get('take_profit_date'): status = f"<span class='badge bg-success'>{r['take_profit_date'][2:].replace('-', '_')} 止盈</span>"
        elif r.get('stop_loss_date'): status = f"<span class='badge bg-danger'>{r['stop_loss_date'][2:].replace('-', '_')} 止损</span>"
        elif r.get('expire_date'): status = f"<span class='badge bg-warning text-dark'>{r['expire_date'][2:].replace('-', '_')} 清盘</span>"
        color_class = 'positive' if pct > 0 else 'negative' if pct < 0 else ''
        html += f"<tr><td>{r['date']}</td><td>{r['code']}</td><td>{r['name']}</td><td>{r['price']:.2f}</td><td>{r['latest_price']:.2f}</td><td class='{color_class}'>{pct:.2f}%</td><td>{status}</td></tr>"
        
    html += """</tbody></table></div></div></div><div class="col-md-12"><div class="card mb-4"><div class="card-header bg-secondary text-white">历史主升浪推荐 (无限回溯)</div><div class="card-body"><table class="table table-hover">
    <thead><tr><th>推荐日期</th><th>代码</th><th>名称</th><th>推荐价</th><th>最新价</th><th>涨跌%</th><th>状态</th></tr></thead><tbody>"""
    
    for r in right_history: 
        pct = (r['latest_price']/r['price']-1)*100
        status = "-"
        if r.get('take_profit_date'): status = f"<span class='badge bg-success'>{r['take_profit_date'][2:].replace('-', '_')} 止盈</span>"
        elif r.get('stop_loss_date'): status = f"<span class='badge bg-danger'>{r['stop_loss_date'][2:].replace('-', '_')} 止损</span>"
        elif r.get('expire_date'): status = f"<span class='badge bg-warning text-dark'>{r['expire_date'][2:].replace('-', '_')} 清盘</span>"
        color_class = 'positive' if pct > 0 else 'negative' if pct < 0 else ''
        html += f"<tr><td>{r['date']}</td><td>{r['code']}</td><td>{r['name']}</td><td>{r['price']:.2f}</td><td>{r['latest_price']:.2f}</td><td class='{color_class}'>{pct:.2f}%</td><td>{status}</td></tr>"
        
    html += "</tbody></table></div></div></div></div></body></html>"
    with open(HTML_OUTPUT, 'w', encoding='utf-8') as f: f.write(html)

# ==========================================
# 🚀 主程序入口
# ==========================================
if __name__ == '__main__':
    beijing_now = datetime.now(timezone.utc) + timedelta(hours=8)
    today, now_time = beijing_now.strftime('%Y-%m-%d'), beijing_now.strftime('%Y-%m-%d %H:%M:%S')
    
    # 🌟 智能判别运行模式
    mode = 'auto' if len(sys.argv) == 1 else sys.argv[1]
    if mode == 'auto':
        if beijing_now.hour >= 15:
            mode = 'history'  # 15点之后只更新历史
        else:
            mode = 'candidates' # 15点之前选股
            
    meta_df = pd.read_excel(EXCEL_LIST, usecols=[0, 1])
    meta_df.columns, stock_list = ['code', 'name'], []
    meta_df.dropna(subset=['code'], inplace=True)
    meta_df['code'] = meta_df['code'].astype(str).str.replace(r'\.0$', '', regex=True).str.zfill(6)
    stock_list = meta_df.to_dict('records')

    left_history, right_history = load_history(LEFT_HISTORY_FILE), load_history(RIGHT_HISTORY_FILE)

    if mode == 'history':
        # 历史模式下，剔除所有已经止盈止损清盘的股票，只查询还在交易的活水股票！
        active_codes = set()
        for r in left_history + right_history:
            if not (r.get('take_profit_date') or r.get('stop_loss_date') or r.get('expire_date')):
                active_codes.add(r['code'])
        stock_list = [s for s in stock_list if s['code'] in active_codes]
        print(f"\n🕒 当前是下午收盘后，启动【极速历史收尾模式】，只需扫描 {len(stock_list)} 只持仓股票！", flush=True)
    else:
        print(f"\n🕒 当前是下午收盘前，启动【全盘选股模式】，开始扫描全部 {len(stock_list)} 只股票...", flush=True)

    tdx_servers = [
        ('124.71.187.122', 7709), ('115.238.90.165', 7709), 
        ('124.71.187.72', 7709), ('124.70.199.56', 7709), 
        ('115.238.56.198', 7709), ('106.14.95.149', 7709)
    ]
    client = None
    for ip, port in tdx_servers:
        try:
            temp_client = Quotes.factory(market='std', server=(ip, port), multithread=True, heartbeat=True)
            if temp_client.bars(symbol='600000', frequency=9, offset=1) is not None:
                print(f"📡 连通服务器: {ip}:{port}", flush=True)
                client = temp_client
                break
        except: pass
    if client is None: sys.exit(1)

    market_data = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(analyze_stock, s, client): s['code'] for s in stock_list}
        for i, f in enumerate(as_completed(futures), 1):
            if i % 200 == 0: print(f"进度: {i}/{len(stock_list)}...", flush=True)
            try:
                res = f.result(timeout=10)
                if res: market_data[res['code']] = res
            except: pass

    # ============================
    # 写入逻辑分支
    # ============================
    if mode == 'candidates':
        left_cands, right_cands = select_today_candidates(market_data, 'left'), select_today_candidates(market_data, 'right')
        with open(DAILY_CANDIDATES_FILE, 'w', encoding='utf-8') as f:
            json.dump(convert_numpy({'date': today, 'left': left_cands, 'right': right_cands}), f, ensure_ascii=False, indent=4)
        
        # 将新选出的股票塞进历史池
        for c in left_cands:
            if not any(r['code'] == c['code'] and r['date'] == today for r in left_history):
                left_history.append({'code': c['code'], 'name': c['name'], 'date': today, 'price': c['price'], 'latest_price': c['price']})
        for c in right_cands:
            if not any(r['code'] == c['code'] and r['date'] == today for r in right_history):
                right_history.append({'code': c['code'], 'name': c['name'], 'date': today, 'price': c['price'], 'latest_price': c['price']})

    # 不管什么模式，都要更新历史价格和止盈止损！
    changed_l = process_history(left_history, market_data, today)
    changed_r = process_history(right_history, market_data, today)

    if mode == 'candidates' or changed_l: save_history(left_history, LEFT_HISTORY_FILE)
    if mode == 'candidates' or changed_r: save_history(right_history, RIGHT_HISTORY_FILE)
    
    print("🖥️ 正在生成最终 HTML 报告看板...", flush=True)
    generate_dashboard(today, now_time)
    
    print("\n🎉 恭喜！量化选股全部跑通，准备强制关闭后台守护线程...", flush=True)
    os._exit(0)
