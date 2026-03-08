# -*- coding: utf-8 -*-
import os
import json
import sys
import time
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
import warnings
warnings.filterwarnings('ignore')

# ==========================================
# ⚙️ 全局配置 (保留)
# ==========================================
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
# 🛑 核心修改：模拟分析，不联网！
# ==========================================
def analyze_stock_mock(stock_info):
    # 随机生成假数据，直接返回
    symbol = stock_info['code']
    return {
        'code': symbol,
        'name': stock_info['name'],
        'price': np.random.uniform(5.0, 50.0),
        'low': np.random.uniform(4.0, 49.0),
        'bias_val': np.random.uniform(-10.0, 10.0),
        # 随机产生信号
        'left_buy_signal': np.random.choice([True, False], p=[0.2, 0.8]),
        'right_buy_signal': np.random.choice([True, False], p=[0.2, 0.8]),
        'is_limit_up': False,
        'is_limit_down': False,
        'ma20_angle': np.random.uniform(10.0, 40.0),
        'dif': 0.5,
        'dea': 0.2
    }

def generate_dashboard(today, now_time, left_today, right_today):
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>模拟跑通测试</title>
</head>
<body>
    <h2>🚀 模拟测试成功！生成时间: {now_time}</h2>
    <p>如果能在 GitHub 看到这个文件，说明 YML 权限配置完美！</p>
    <p>左侧假候选数量: {len(left_today)}</p>
    <p>右侧假候选数量: {len(right_today)}</p>
</body>
</html>
    """
    with open(HTML_OUTPUT, 'w', encoding='utf-8') as f: f.write(html)
    print(f"✅ HTML看板已生成: {HTML_OUTPUT}")

# ==========================================
# 🚀 主程序入口
# ==========================================
if __name__ == '__main__':
    print("=== 开始执行 [光速模拟版] 脚本 ===")
    
    beijing_now = datetime.now(timezone.utc) + timedelta(hours=8)
    today = beijing_now.strftime('%Y-%m-%d')
    now_time = beijing_now.strftime('%Y-%m-%d %H:%M:%S')

    # 制造 10 只假股票，跳过 Excel 读取
    stock_list = [{'code': str(i).zfill(6), 'name': f'假股票{i}'} for i in range(1, 11)]
    
    market_data = {}
    left_candidates = []
    right_candidates = []
    
    print("🤖 正在伪造行情数据...")
    for stock in stock_list:
        res = analyze_stock_mock(stock)
        market_data[res['code']] = res
        if res['left_buy_signal']: left_candidates.append(res)
        if res['right_buy_signal']: right_candidates.append(res)

    print("💾 正在伪造 JSON 文件...")
    daily = {'date': today, 'left': left_candidates, 'right': right_candidates}
    with open(DAILY_CANDIDATES_FILE, 'w', encoding='utf-8') as f:
        json.dump(convert_numpy(daily), f, ensure_ascii=False, indent=4)
        
    save_history(left_candidates, LEFT_HISTORY_FILE)
    save_history(right_candidates, RIGHT_HISTORY_FILE)

    print("🖥️ 正在伪造 HTML...")
    generate_dashboard(today, now_time, left_candidates, right_candidates)

    print("\n🎉 光速模拟版跑完啦！")
