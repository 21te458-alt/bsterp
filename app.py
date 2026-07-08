# -*- coding: utf-8 -*-
import os
import sys
import requests
import base64
import json
import concurrent.futures
import time
from datetime import datetime, timedelta
from flask import Flask, send_file, request, jsonify, render_template
from flask_cors import CORS
from dotenv import load_dotenv

# ===== Google Sheets 相关导入 =====
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import io
import re

def parse_leading_number(raw, as_int=False):
    """从单元格文本中提取开头的数字，行为与前端 JS 的 parseFloat 保持一致。

    例如 "358*8=2864" -> 358（不会像旧的 re.sub(r'[^\\d.-]','') 那样把
    358、8、2864 三段数字拼接成 35882864）。
    "¥1,932" -> 1932，"5P" -> 5，空值/无法识别 -> 0。
    """
    if raw is None:
        return 0
    s = str(raw).strip()
    if not s:
        return 0
    # 去掉数字之间的千分位逗号，如 "1,932" -> "1932"
    s = re.sub(r'(?<=\d),(?=\d)', '', s)
    m = re.match(r'^[^\d\-]*(-?\d+(?:\.\d+)?)', s)
    if not m:
        return 0
    try:
        val = float(m.group(1))
        return int(val) if as_int else val
    except (TypeError, ValueError):
        return 0

# ===== 加载环境变量 =====
load_dotenv()

app = Flask(__name__)
CORS(app)
app.config['JSON_AS_ASCII'] = False

# ============================================================
# 第一部分：乐天RMS API 配置
# ============================================================

SERVICE_SECRET = os.environ.get('SERVICE_SECRET', 'SP406647_AaHdEvRVKrO74RDh')
LICENSE_KEY = os.environ.get('LICENSE_KEY', 'SL406647_UUcHdvDI3ZNP0Br3')
SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-me')
app.config['SECRET_KEY'] = SECRET_KEY

DATA_DIR = os.environ.get('DATA_DIR', os.path.dirname(os.path.abspath(__file__)))
STOCK_RECORDS_FILE = os.path.join(DATA_DIR, 'stock_records.json')

os.makedirs(os.path.dirname(STOCK_RECORDS_FILE) or '.', exist_ok=True)

print(f"[启动] 数据文件路径: {STOCK_RECORDS_FILE}")
print(f"[启动] 环境: {'生产' if os.environ.get('RENDER') else '开发'}")

def get_auth_header():
    auth_string = f"{SERVICE_SECRET}:{LICENSE_KEY}"
    encoded = base64.b64encode(auth_string.encode()).decode()
    return f"ESA {encoded}"

session = requests.Session()
session.headers.update({'Authorization': get_auth_header()})

_products_cache = {'data': None, 'time': 0}
_inventory_cache = {'data': None, 'loading': False, 'progress': 0, 'total': 0, 'last_update': None}
PRODUCTS_CACHE_TTL = 300

# ============================================================
# 第二部分：商品分类映射表
# ============================================================

# アニメ・グッズのgenreId
ANIME_GENRES = [
    '112938', '101914', '567549', '201632', '112203', '101841',
    '403809', '111163', '551732', '566157', '101197', '101917',
    '301748', '403672', '300927', '303097', '210207', '567731',
    '112334', '408789', '207288', '112928', '553697', '400872',
    '406770', '216058', '406864', '111377', '553789', '216132',
    '403755', '207314', '303656'
]

# ========== ジャンルID → 商品分类 映射表 ==========
CATEGORY_BY_GENRE = {
    # ---- 食品・お菓子・天然生活 ----
    '100001': '食品・お菓子・天然生活',  # 食品
    '100002': '食品・お菓子・天然生活',  # 菓子
    '100003': '食品・お菓子・天然生活',  # パン
    '100004': '食品・お菓子・天然生活',  # 米・雑穀
    '100005': '食品・お菓子・天然生活',  # 麺類
    '100006': '食品・お菓子・天然生活',  # 缶詰・瓶詰
    '100007': '食品・お菓子・天然生活',  # 調味料
    '100008': '食品・お菓子・天然生活',  # 油・脂
    '100010': '食品・お菓子・天然生活',  # 酒類
    '100012': '食品・お菓子・天然生活',  # コーヒー
    '100013': '食品・お菓子・天然生活',  # 紅茶
    '100015': '食品・お菓子・天然生活',  # スープ
    '100016': '食品・お菓子・天然生活',  # カレー
    '100017': '食品・お菓子・天然生活',  # レトルト食品
    '100018': '食品・お菓子・天然生活',  # 冷凍食品
    '100019': '食品・お菓子・天然生活',  # デザート
    '100020': '食品・お菓子・天然生活',  # アイスクリーム
    '100021': '食品・お菓子・天然生活',  # チョコレート
    '100022': '食品・お菓子・天然生活',  # キャンディ
    '100023': '食品・お菓子・天然生活',  # ガム
    '100024': '食品・お菓子・天然生活',  # ナッツ
    '100025': '食品・お菓子・天然生活',  # ドライフルーツ
    '100026': '食品・お菓子・天然生活',  # せんべい
    '100027': '食品・お菓子・天然生活',  # 和菓子
    '100028': '食品・お菓子・天然生活',  # 洋菓子
    '100029': '食品・お菓子・天然生活',  # ジャム
    '100030': '食品・お菓子・天然生活',  # はちみつ
    '100031': '食品・お菓子・天然生活',  # シリアル
    
    # ---- 健康食品 ----
    '100032': '健康食品',  # サプリメント
    '100033': '健康食品',  # プロテイン
    '100034': '健康食品',  # ビタミン
    '100035': '健康食品',  # ミネラル
    '100036': '健康食品',  # 青汁
    '100037': '健康食品',  # 酵素
    '100038': '健康食品',  # 乳酸菌
    '100039': '健康食品',  # コラーゲン
    '100040': '健康食品',  # プラセンタ
    '100041': '健康食品',  # グルコサミン
    '100042': '健康食品',  # 還元型コエンザイムQ10
    '100043': '健康食品',  # DHA・EPA
    '100044': '健康食品',  # カルシウム
    '100045': '健康食品',  # 鉄分
    '100046': '健康食品',  # マルチビタミン
    '100047': '健康食品',  # フォルスコリ
    '100048': '健康食品',  # HMB
    '100049': '健康食品',  # プロテオグリカン
    '100050': '健康食品',  # じゃばら
    '100051': '健康食品',  # にがり
    '100052': '健康食品',  # 黒酢
    
    # ---- 美容・コスメ・ボディケア ----
    '100053': '美容・コスメ・ボディケア',  # スキンケア
    '100054': '美容・コスメ・ボディケア',  # 化粧水
    '100055': '美容・コスメ・ボディケア',  # 乳液
    '100056': '美容・コスメ・ボディケア',  # クリーム
    '100057': '美容・コスメ・ボディケア',  # 美容液
    '100058': '美容・コスメ・ボディケア',  # パック
    '100059': '美容・コスメ・ボディケア',  # 洗顔料
    '100060': '美容・コスメ・ボディケア',  # クレンジング
    '100061': '美容・コスメ・ボディケア',  # メイクアップ
    '100062': '美容・コスメ・ボディケア',  # ファンデーション
    '100063': '美容・コスメ・ボディケア',  # 口紅
    '100064': '美容・コスメ・ボディケア',  # アイメイク
    '100065': '美容・コスメ・ボディケア',  # ネイル
    '100066': '美容・コスメ・ボディケア',  # ヘアケア
    '100067': '美容・コスメ・ボディケア',  # シャンプー
    '100068': '美容・コスメ・ボディケア',  # リンス・コンディショナー
    '100069': '美容・コスメ・ボディケア',  # トリートメント
    '100070': '美容・コスメ・ボディケア',  # ヘアカラー
    '100071': '美容・コスメ・ボディケア',  # ボディケア
    '100072': '美容・コスメ・ボディケア',  # ボディソープ
    '100073': '美容・コスメ・ボディケア',  # ハンドケア
    '100074': '美容・コスメ・ボディケア',  # フットケア
    '100075': '美容・コスメ・ボディケア',  # 日焼け止め
    '100076': '美容・コスメ・ボディケア',  # アロマ
    '100077': '美容・コスメ・ボディケア',  # 香水
    
    # ---- 日用品雑貨 ----
    '100078': '日用品雑貨',  # 掃除用品
    '100079': '日用品雑貨',  # 洗濯用品
    '100080': '日用品雑貨',  # キッチン用品
    '100081': '日用品雑貨',  # 食器
    '100082': '日用品雑貨',  # 調理器具
    '100083': '日用品雑貨',  # 収納用品
    '100084': '日用品雑貨',  # インテリア
    '100085': '日用品雑貨',  # 寝具
    '100086': '日用品雑貨',  # タオル
    '100087': '日用品雑貨',  # バッグ
    '100088': '日用品雑貨',  # ポーチ
    '100089': '日用品雑貨',  # アクセサリー
    '100090': '日用品雑貨',  # 時計
    '100091': '日用品雑貨',  # 傘
    '100092': '日用品雑貨',  # 手袋
    '100093': '日用品雑貨',  # マフラー
    '100094': '日用品雑貨',  # ストール
    '100095': '日用品雑貨',  # 帽子
    '100096': '日用品雑貨',  # 靴下
    '100097': '日用品雑貨',  # 下着
    '100098': '日用品雑貨',  # パジャマ
    '100099': '日用品雑貨',  # マスク
    '100100': '日用品雑貨',  # ハンドジェル
    '100101': '日用品雑貨',  # 除菌グッズ
    
    # ---- 医薬品 ----
    '100102': '医薬品',  # 鎮痛剤
    '100103': '医薬品',  # 解熱剤
    '100104': '医薬品',  # 風邪薬
    '100105': '医薬品',  # 胃薬
    '100106': '医薬品',  # 整腸剤
    '100107': '医薬品',  # アレルギー薬
    '100108': '医薬品',  # 目薬
    '100109': '医薬品',  # 皮膚薬
    '100110': '医薬品',  # ビタミン剤
    '100111': '医薬品',  # 漢方薬
    '100112': '医薬品',  # 第1類医薬品
    '100113': '医薬品',  # 第2類医薬品
    '100114': '医薬品',  # 第3類医薬品
    '100115': '医薬品',  # ロキソプロフェン
    '100116': '医薬品',  # 葛根湯
    '100117': '医薬品',  # 乗り物酔い薬
    
    # ---- 水・ソフトドリンク ----
    '100118': '水・ソフトドリンク',  # ミネラルウォーター
    '100119': '水・ソフトドリンク',  # 炭酸水
    '100120': '水・ソフトドリンク',  # エナジードリンク
    '100121': '水・ソフトドリンク',  # スポーツドリンク
    '100122': '水・ソフトドリンク',  # ジュース
    '100123': '水・ソフトドリンク',  # 野菜ジュース
    '100124': '水・ソフトドリンク',  # コーラ
    '100125': '水・ソフトドリンク',  # お茶飲料
    '100126': '水・ソフトドリンク',  # コーヒー飲料
    '100127': '水・ソフトドリンク',  # 乳酸菌飲料
    
    # ---- その他（デフォルト） ----
    # 上記に含まれないジャンルIDは「その他」に分類
}

# ============================================================
# 第三部分：Google Sheets 配置
# ============================================================

# Google Sheets API 认证
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']

# 从 render_credentials.json 或环境变量加载凭证
try:
    credentials = Credentials.from_service_account_file('render_credentials.json', scopes=SCOPES)
    print("✓ 成功从 render_credentials.json 文件加载凭证")
except Exception as e:
    print(f"✗ 从文件加载凭证失败: {e}")
    credentials_json = os.environ.get('GOOGLE_CREDENTIALS')
    if credentials_json:
        try:
            creds_dict = json.loads(credentials_json)
            credentials = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
            print("✓ 使用备用方案：从环境变量加载凭证成功")
        except Exception as env_e:
            print(f"✗ 从环境变量加载凭证也失败: {env_e}")
            raise
    else:
        raise Exception("无法找到任何有效的凭证来源，应用启动失败")

gc = gspread.authorize(credentials)

# Google Sheets 文件ID
FILE_IDS = {
    'file_a': '1zLLFiiZ8Vu89rW1QQ3JlW9EDKSOSd2u7DAkYA5dd1j4',
    'file_b': '1anuXNRZFwerO7Gxw9kvpsxglkphbQ65JEXKSHCsZrrs'
}

# 工作表名称
ORDER_WORKSHEET_NAME = '销售记录'
STOCK_WORKSHEET_NAME = '动漫记录'
GENERAL_STOCK_NAME = '在庫'

# ============================================================
# 第四部分：Google Sheets 函数
# ============================================================

class SimpleCache:
    """简单的缓存系统"""
    def __init__(self, default_ttl=30):
        self.cache = {}
        self.ttl = default_ttl
    
    def get(self, key):
        if key in self.cache:
            data, timestamp = self.cache[key]
            if datetime.now() - timestamp < timedelta(seconds=self.ttl):
                return data
            else:
                del self.cache[key]
        return None
    
    def set(self, key, value):
        self.cache[key] = (value, datetime.now())
    
    def clear(self):
        self.cache.clear()

# 创建Google Sheets专用缓存
gs_cache = SimpleCache(default_ttl=30)

_sheets_cache = None

def get_all_sheets_info():
    """获取所有文件中的所有工作表"""
    global _sheets_cache
    if _sheets_cache is not None:
        return _sheets_cache
    
    sheets_info = []
    
    try:
        sh_a = gc.open_by_key(FILE_IDS['file_a'])
        for sheet in sh_a.worksheets():
            if sheet.title == '销售记录':
                sheet_type = 'order'
                file_name = '📦 订单表'
            elif sheet.title == '动漫记录':
                sheet_type = 'stock'
                file_name = '🎬 动漫库存表'
            else:
                sheet_type = 'normal'
                file_name = '📄 文件A - ' + sheet.title
            
            sheets_info.append({
                'id': f"{FILE_IDS['file_a']}|{sheet.id}",
                'title': sheet.title,
                'file_name': file_name,
                'file_key': FILE_IDS['file_a'],
                'sheet_id': sheet.id,
                'type': sheet_type
            })
        
        sh_b = gc.open_by_key(FILE_IDS['file_b'])
        for sheet in sh_b.worksheets():
            if sheet.title == '在庫':
                sheet_type = 'stock'
                file_name = '📝 一般库存表'
            elif sheet.title == '楽天-予約商品登録リスト':
                sheet_type = 'reserve'
                file_name = '📋 预订商品表'
            else:
                sheet_type = 'normal'
                file_name = '📄 文件B - ' + sheet.title
            
            sheets_info.append({
                'id': f"{FILE_IDS['file_b']}|{sheet.id}",
                'title': sheet.title,
                'file_name': file_name,
                'file_key': FILE_IDS['file_b'],
                'sheet_id': sheet.id,
                'type': sheet_type
            })
        
        _sheets_cache = sheets_info
        return sheets_info
        
    except Exception as e:
        print(f"获取工作表列表出错: {e}")
        return sheets_info

def get_worksheet(file_key, sheet_id):
    """获取工作表对象"""
    sh = gc.open_by_key(file_key)
    return sh.get_worksheet_by_id(int(sheet_id))

def get_worksheet_by_name(file_key, sheet_name):
    """根据工作表名称获取工作表对象"""
    try:
        sh = gc.open_by_key(file_key)
        return sh.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        # 如果找不到，回退到默认的"在庫"
        print(f"⚠️ 找不到工作表 '{sheet_name}'，回退到 '在庫'")
        return sh.worksheet('在庫')
    except Exception as e:
        print(f"❌ 获取工作表失败: {e}")
        raise

def get_general_stock_worksheet():
    """获取一般库存表"""
    sh = gc.open_by_key(FILE_IDS['file_b'])
    return sh.worksheet('在庫')

def get_stock_worksheet():
    """获取库存工作表（动漫记录）"""
    sh = gc.open_by_key(FILE_IDS['file_a'])
    return sh.worksheet('动漫记录')

def get_order_worksheet():
    """获取订单工作表"""
    sh = gc.open_by_key(FILE_IDS['file_a'])
    return sh.worksheet('销售记录')

# ============================================================
# 第五部分：Google Sheets API 路由
# ============================================================

@app.route('/api/sheets-info')
def get_sheets_info_api():
    """获取所有工作表信息"""
    sheets_info = get_all_sheets_info()
    return jsonify(sheets_info)

@app.route('/api/gs-stock-stats')
def get_gs_stock_stats():
    """获取Google Sheets库存统计数据（支持指定工作表）"""
    sheet_name = request.args.get('sheet_name', '在庫')
    cache_key = f'gs_stock_stats_{sheet_name}'
    cached_data = gs_cache.get(cache_key)
    if cached_data is not None:
        return jsonify(cached_data)
    
    try:
        # 根据 sheet_name 获取对应的工作表
        stock_ws = get_worksheet_by_name(FILE_IDS['file_b'], sheet_name)
        stock_data = stock_ws.get_all_values()
        
        if len(stock_data) < 2:
            result = {
                'success': True,
                'total_items': 0,
                'total_quantity': 0,
                'total_value': 0,
                'total_purchase_value': 0,
                'stock_rate': 0,
                'low_stock_count': 0,
                'out_stock_count': 0
            }
            gs_cache.set(cache_key, result)
            return jsonify(result)
        
        headers = stock_data[0]
        
        # 查找列索引
        jan_idx = None
        name_idx = None
        maker_idx = None
        price_idx = None
        stock_idx = None
        in_qty_idx = None
        
        for i, h in enumerate(headers):
            if h == 'JAN CODE':
                jan_idx = i
            elif h == '商品名':
                name_idx = i
            elif h == 'メーカー':
                maker_idx = i
            elif h == '仕入れ価格（個/税抜）':
                price_idx = i
            elif h == '残り在庫':
                stock_idx = i
            elif h == '入庫数量':
                in_qty_idx = i
        
        total_quantity = 0
        total_value = 0
        total_purchase_value = 0
        total_in_qty = 0
        low_stock_count = 0
        out_stock_count = 0
        
        for row in stock_data[1:]:
            price = 0
            if price_idx is not None and price_idx < len(row) and row[price_idx]:
                price = parse_leading_number(row[price_idx])
            
            stock_qty = 0
            if stock_idx is not None and stock_idx < len(row) and row[stock_idx]:
                stock_qty = parse_leading_number(row[stock_idx], as_int=True)
            
            in_qty = 0
            if in_qty_idx is not None and in_qty_idx < len(row) and row[in_qty_idx]:
                in_qty = parse_leading_number(row[in_qty_idx], as_int=True)
            
            total_quantity += stock_qty
            total_value += price * stock_qty
            
            if in_qty > 0:
                total_in_qty += in_qty
                total_purchase_value += price * in_qty
            
            if stock_qty <= 0:
                out_stock_count += 1
            elif stock_qty <= 5:
                low_stock_count += 1
        
        stock_rate = (total_quantity / total_in_qty * 100) if total_in_qty > 0 else 0
        
        result = {
            'success': True,
            'total_items': len(stock_data) - 1,
            'total_quantity': total_quantity,
            'total_value': total_value,
            'total_purchase_value': total_purchase_value,
            'stock_rate': stock_rate,
            'low_stock_count': low_stock_count,
            'out_stock_count': out_stock_count
        }
        gs_cache.set(cache_key, result)
        return jsonify(result)
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/gs-sheet/<path:file_key>')
def api_get_gs_sheet_data(file_key):
    """获取Google Sheets表格数据（支持指定工作表）"""
    sheet_name = request.args.get('sheet_name', '在庫')
    cache_key = f'gs_sheet_data_{file_key}_{sheet_name}'
    cached_data = gs_cache.get(cache_key)
    if cached_data is not None:
        return jsonify(cached_data)
    
    try:
        # 根据 sheet_name 获取对应的工作表
        worksheet = get_worksheet_by_name(file_key, sheet_name)
        data = worksheet.get_all_values()
        if not data:
            result = {'success': True, 'headers': [], 'data': [], 'total': 0}
            gs_cache.set(cache_key, result)
            return jsonify(result)
        
        original_headers = data[0]
        valid_indices = []
        filtered_headers = []
        for i, h in enumerate(original_headers):
            if h and not str(h).startswith('¥') and 'Unnamed' not in str(h):
                valid_indices.append(i)
                filtered_headers.append(h)
        
        filtered_data = []
        for row in data[1:]:
            filtered_row = [row[i] if i < len(row) else '' for i in valid_indices]
            filtered_data.append(filtered_row)
        
        # 为"在庫"或"预售"表添加"在库金额"列
        if file_key == FILE_IDS['file_b']:
            sheet_title = worksheet.title
            if sheet_title in ['在庫', '预售']:
                price_col_idx = None
                stock_col_idx = None
                for i, h in enumerate(filtered_headers):
                    if h == '仕入れ価格（個/税抜）':
                        price_col_idx = i
                    elif h == '残り在庫':
                        stock_col_idx = i
                
                if price_col_idx is not None and stock_col_idx is not None:
                    sold_col_idx = None
                    for i, h in enumerate(filtered_headers):
                        if h == '售出':
                            sold_col_idx = i
                            break
                    
                    if sold_col_idx is not None:
                        insert_position = sold_col_idx + 1
                    else:
                        insert_position = stock_col_idx + 1
                    
                    filtered_headers.insert(insert_position, '在库金额')
                    
                    for row in filtered_data:
                        price_str = row[price_col_idx] if price_col_idx < len(row) else '0'
                        stock_str = row[stock_col_idx] if stock_col_idx < len(row) else '0'
                        
                        price_val = parse_leading_number(price_str) if price_str else 0
                        stock_qty = parse_leading_number(stock_str, as_int=True) if stock_str else 0
                        
                        amount = price_val * stock_qty
                        row.insert(insert_position, f'¥{amount:,.0f}' if amount > 0 else '¥0')
        
        result = {
            'success': True,
            'headers': filtered_headers,
            'data': filtered_data,
            'total': len(filtered_data)
        }
        gs_cache.set(cache_key, result)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/gs-sheet/<path:file_key>/row', methods=['POST'])
def api_add_gs_row(file_key):
    """添加Google Sheets行（支持指定工作表）"""
    try:
        sheet_name = request.args.get('sheet_name', '在庫')
        worksheet = get_worksheet_by_name(file_key, sheet_name)
        new_row = request.json.get('row_data', [])
        worksheet.append_row(new_row)
        gs_cache.clear()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/gs-sheet/<path:file_key>/cell', methods=['PUT'])
def api_update_gs_cell(file_key):
    """更新Google Sheets单元格（支持指定工作表）"""
    try:
        sheet_name = request.args.get('sheet_name', '在庫')
        worksheet = get_worksheet_by_name(file_key, sheet_name)
        row = request.json.get('row')
        col = request.json.get('col')
        value = request.json.get('value')
        worksheet.update_cell(row, col, value)
        gs_cache.clear()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/gs-sheet/<path:file_key>/row/<int:row_num>', methods=['DELETE'])
def api_delete_gs_row(file_key, row_num):
    """删除Google Sheets行（支持指定工作表）"""
    try:
        sheet_name = request.args.get('sheet_name', '在庫')
        worksheet = get_worksheet_by_name(file_key, sheet_name)
        worksheet.delete_rows(row_num + 1)
        gs_cache.clear()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/gs-sheet/<path:file_key>/export')
def export_gs_to_excel(file_key):
    """导出Google Sheets为Excel（支持指定工作表）"""
    try:
        sheet_name = request.args.get('sheet_name', '在庫')
        worksheet = get_worksheet_by_name(file_key, sheet_name)
        data = worksheet.get_all_values()
        if not data or len(data) < 2:
            return jsonify({'success': False, 'error': '无数据可导出'})
        
        headers = data[0]
        rows = data[1:]
        df = pd.DataFrame(rows, columns=headers)
        
        # 获取工作表信息用于命名
        sheets_info = get_all_sheets_info()
        sheet_key = f"{file_key}|{worksheet.id}"
        sheet_info = next((s for s in sheets_info if s['id'] == sheet_key), None)
        sheet_display_name = sheet_info['title'] if sheet_info else sheet_name
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name=sheet_display_name[:31], index=False)
        
        output.seek(0)
        filename = f"{sheet_display_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ============================================================
# 第六部分：乐天RMS API 路由
# ============================================================

# ========== 🆕 改进后的分类函数 ==========
def determine_category(title, genre_id, item_type, purchasable_period):
    """
    根据商品信息判断分类（8分类）
    优先使用ジャンルID映射，然后使用关键词作为补充
    """
    # 1. 首先检查是否在アニメジャンルリスト中
    if genre_id in ANIME_GENRES:
        # アニメ商品：有预售期间则为预售，否则为在库
        if purchasable_period and purchasable_period.get('start'):
            return 'アニメ・グッズ（预售）'
        else:
            return 'アニメ・グッズ（在库）'
    
    # 2. 使用ジャンルID映射表
    if genre_id in CATEGORY_BY_GENRE:
        return CATEGORY_BY_GENRE[genre_id]
    
    # 3. ジャンルIDがマップにない場合、关键词で補完
    # 食品关键词
    food_keywords = ['【天然生活】', '天然生活', '食品', 'お菓子', 'スイーツ', 'ケーキ', 'ラーメン', 
                     'カレー', 'うどん', 'そば', 'パスタ', 'スープ', 'ゼリー', 'プリン', 'アイス', 
                     'クッキー', 'チョコ', 'キャンディ', 'ナッツ', 'ドライフルーツ', 'せんべい', 
                     'かりんとう', 'ようかん', 'まんじゅう', 'どら焼き', 'たい焼き']
    for kw in food_keywords:
        if kw in title:
            return '食品・お菓子・天然生活'
    
    # 健康食品关键词
    health_keywords = ['健康食品', 'サプリ', 'サプリメント', '青汁', '酵素', 'NMN', '乳酸菌', 
                       'プロテイン', 'ビタミン', 'カルシウム', '鉄分', 'マルチビタミン', 
                       'フォルスコリ', 'プラセンタ', 'コラーゲン', 'HMB', 'プロテオグリカン', 'じゃばら']
    for kw in health_keywords:
        if kw in title:
            return '健康食品'
    
    # 美容关键词
    beauty_keywords = ['美容', 'コスメ', 'ボディケア', '化粧品', 'クリーム', 'ジェル', '日焼け止め', 
                       'マッサージ', 'ローション', '美容液', '洗顔', 'シャンプー', 'リンス', 
                       'コンディショナー', 'ボディソープ']
    for kw in beauty_keywords:
        if kw in title:
            return '美容・コスメ・ボディケア'
    
    # 日用品关键词
    daily_keywords = ['日用品', '雑貨', '手袋', 'マフラー', 'ストール', 'タオル', 'バッグ', 
                      'ポーチ', 'マスク', 'ハンドジェル']
    for kw in daily_keywords:
        if kw in title:
            return '日用品雑貨'
    
    # 医薬品关键词
    medicine_keywords = ['医薬品', '薬', '第1類', '第2類', '第3類', 'ロキソプロフェン', 
                         '風邪薬', '胃薬', '鎮痛剤', '解熱剤']
    for kw in medicine_keywords:
        if kw in title:
            return '医薬品'
    
    # 飲料关键词
    drink_keywords = ['水', 'ドリンク', '飲料', 'ソフトドリンク', 'ミネラルウォーター', 
                      '炭酸水', 'エナジードリンク', 'スポーツドリンク']
    for kw in drink_keywords:
        if kw in title:
            return '水・ソフトドリンク'
    
    # 4. それでも分類できない場合
    return 'その他'

def get_category_info(category):
    """获取分类的显示信息"""
    category_map = {
        'アニメ・グッズ（预售）': {'icon': '🎬', 'color': '#8b5cf6', 'badge_class': 'category-preorder'},
        'アニメ・グッズ（在库）': {'icon': '📦', 'color': '#3b82f6', 'badge_class': 'category-instock'},
        '食品・お菓子・天然生活': {'icon': '🌿', 'color': '#10b981', 'badge_class': 'category-food'},
        '健康食品': {'icon': '🍎', 'color': '#ef4444', 'badge_class': 'category-health'},
        '美容・コスメ・ボディケア': {'icon': '💄', 'color': '#ec4899', 'badge_class': 'category-beauty'},
        '日用品雑貨': {'icon': '🏠', 'color': '#f59e0b', 'badge_class': 'category-daily'},
        '医薬品': {'icon': '💊', 'color': '#8b5cf6', 'badge_class': 'category-medicine'},
        '水・ソフトドリンク': {'icon': '🥤', 'color': '#06b6d4', 'badge_class': 'category-drink'},
        'その他': {'icon': '📦', 'color': '#6b7280', 'badge_class': 'category-other'}
    }
    return category_map.get(category, {'icon': '📦', 'color': '#6b7280', 'badge_class': 'category-other'})

# 楽天受注API端点
ORDER_SEARCH_URL = "https://api.rms.rakuten.co.jp/es/2.0/order/searchOrder/"
ORDER_GET_URL = "https://api.rms.rakuten.co.jp/es/2.0/order/getOrder/"
ORDER_API_HEADERS = {'Content-Type': 'application/json; charset=utf-8'}

# ========== 入库/出库记录管理 ==========
def load_stock_records():
    default = {'出库记录': [], '入库记录': []}
    if os.path.exists(STOCK_RECORDS_FILE):
        try:
            with open(STOCK_RECORDS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return {
                    '出库记录': data.get('出库记录', []),
                    '入库记录': data.get('入库记录', [])
                }
        except (json.JSONDecodeError, IOError) as e:
            print(f"读取数据文件失败: {e}，使用默认数据")
            return default
    save_stock_records(default)
    return default

def save_stock_records(records):
    if '出库记录' not in records:
        records['出库记录'] = []
    if '入库记录' not in records:
        records['入库记录'] = []
    try:
        with open(STOCK_RECORDS_FILE, 'w', encoding='utf-8') as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"保存数据失败: {e}")
        return False

def get_current_stock(manage_number):
    url = f"https://api.rms.rakuten.co.jp/es/2.1/inventories/manage-numbers/{manage_number}/variants/{manage_number}"
    for attempt in range(3):
        try:
            response = session.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                return data.get('quantity', 0)
            return 0
        except requests.exceptions.RequestException:
            if attempt < 2:
                time.sleep(1 * (attempt + 1))
                continue
            return 0
    return 0

def update_inventory_direct(manage_number, change):
    current_stock = get_current_stock(manage_number)
    new_stock = max(0, current_stock + change)
    url = f"https://api.rms.rakuten.co.jp/es/2.1/inventories/manage-numbers/{manage_number}/variants/{manage_number}"
    body = {'quantity': new_stock, 'mode': 'set'}
    for attempt in range(3):
        try:
            response = session.put(url, json=body, timeout=30)
            if response.status_code == 200:
                if _products_cache['data'] is not None:
                    for p in _products_cache['data']:
                        if p['manageNumber'] == manage_number:
                            p['stock'] = new_stock
                            break
                return True, new_stock
            return False, current_stock
        except requests.exceptions.RequestException:
            if attempt < 2:
                time.sleep(1 * (attempt + 1))
                continue
            return False, current_stock
    return False, current_stock

# ========== 商品数据获取 ==========
def fetch_products():
    print("开始获取商品数据...")
    all_products = []
    offset = 0
    hits_per_page = 100
    
    while True:
        url = "https://api.rms.rakuten.co.jp/es/2.0/items/search"
        params = {"offset": offset, "hits": hits_per_page}
        
        try:
            response = session.get(url, params=params, timeout=30)
            if response.status_code == 429:
                print(f"  API限流，等待3秒后重试...")
                time.sleep(3)
                continue
            if response.status_code != 200:
                print(f"请求失败: {response.status_code}")
                break
            data = response.json()
            results = data.get('results', [])
            if not results:
                break
            for result in results:
                item = result.get('item', {})
                manage_number = item.get('manageNumber', '')
                if not manage_number:
                    continue
                price = item.get('price', 0)
                if not price or price == 0:
                    variants = item.get('variants', {})
                    for sku_key, variant_data in variants.items():
                        std_price = variant_data.get('standardPrice', 0)
                        if std_price:
                            price = int(std_price)
                            break
                title = item.get('title', '无商品名')
                genre_id = item.get('genreId', '')
                item_type = item.get('itemType', 'NORMAL')
                purchasable_period = item.get('purchasablePeriod', {})
                # 🆕 使用改进后的分类函数
                category = determine_category(title, genre_id, item_type, purchasable_period)
                all_products.append({
                    'manageNumber': manage_number,
                    'name': title,
                    'price': price,
                    'itemType': item_type,
                    'genreId': genre_id,
                    'category': category,
                    'status': '贩售中',
                    'stock': 0
                })
            print(f"  已获取 {len(all_products)} 件商品...")
            if len(results) < hits_per_page:
                break
            offset += hits_per_page
            time.sleep(0.3)
        except Exception as e:
            print(f"错误: {e}")
            break
    
    print(f"获取完成！共 {len(all_products)} 件商品")
    return all_products

def get_cached_products(force_refresh=False):
    now = time.time()
    if force_refresh or _products_cache['data'] is None or (now - _products_cache['time']) >= PRODUCTS_CACHE_TTL:
        products = fetch_products()
        _products_cache['data'] = products
        _products_cache['time'] = now
        return products
    print(f"使用缓存（{int(now - _products_cache['time'])}秒前）")
    return _products_cache['data']

def get_single_stock(manage_number):
    url = f"https://api.rms.rakuten.co.jp/es/2.1/inventories/manage-numbers/{manage_number}/variants/{manage_number}"
    for attempt in range(3):
        try:
            response = session.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                return {'manageNumber': manage_number, 'stock': data.get('quantity', 0)}
            else:
                if attempt < 2:
                    time.sleep(1 * (attempt + 1))
                    continue
                return {'manageNumber': manage_number, 'stock': 0}
        except requests.exceptions.RequestException:
            if attempt < 2:
                time.sleep(1 * (attempt + 1))
                continue
            return {'manageNumber': manage_number, 'stock': 0}

# ========== 乐天RMS API 路由 ==========

@app.route('/api/products', methods=['GET'])
def get_products():
    force_refresh = request.args.get('refresh', 'false').lower() == 'true'
    return jsonify(get_cached_products(force_refresh))

@app.route('/api/inventory/single/<manage_number>', methods=['GET'])
def get_single_inventory(manage_number):
    try:
        stock_data = get_single_stock(manage_number)
        return jsonify({
            'manageNumber': stock_data['manageNumber'],
            'stock': stock_data['stock']
        })
    except Exception as e:
        return jsonify({'manageNumber': manage_number, 'stock': 0, 'error': str(e)}), 500

@app.route('/api/inventory/batch', methods=['POST'])
def get_inventory_batch():
    try:
        data = request.json
        manage_numbers = data.get('manageNumbers', [])
        if not manage_numbers:
            return jsonify([])
        if len(manage_numbers) > 100:
            return jsonify({'error': 'Too many items, max 100 per request'}), 400
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            futures = {executor.submit(get_single_stock, mn): mn for mn in manage_numbers}
            for future in concurrent.futures.as_completed(futures):
                try:
                    result = future.result(timeout=30)
                    results.append(result)
                except Exception as e:
                    mn = futures[future]
                    results.append({'manageNumber': mn, 'stock': 0, 'error': str(e)})
        return jsonify(results)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/inventory/start-load', methods=['POST'])
def start_inventory_load():
    global _inventory_cache
    if _inventory_cache['loading']:
        return jsonify({'status': 'loading', 'progress': _inventory_cache['progress'], 'total': _inventory_cache['total']})
    products = get_cached_products()
    _inventory_cache['loading'] = True
    _inventory_cache['progress'] = 0
    _inventory_cache['total'] = len(products)
    _inventory_cache['data'] = None
    _inventory_cache['last_update'] = None
    import threading
    thread = threading.Thread(target=_load_inventory_background)
    thread.daemon = True
    thread.start()
    return jsonify({'status': 'started', 'total': len(products)})

def _load_inventory_background():
    global _inventory_cache
    try:
        products = get_cached_products()
        total = len(products)
        all_data = []
        batch_size = 50
        product_info = {p['manageNumber']: p for p in products}
        for i in range(0, total, batch_size):
            batch = products[i:i + batch_size]
            batch_numbers = [p['manageNumber'] for p in batch]
            with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
                futures = [executor.submit(get_single_stock, mn) for mn in batch_numbers]
                for future in concurrent.futures.as_completed(futures):
                    try:
                        stock_data = future.result(timeout=30)
                        info = product_info.get(stock_data['manageNumber'], {})
                        all_data.append({
                            'manageNumber': stock_data['manageNumber'],
                            'name': info.get('name', '无商品名'),
                            'price': info.get('price', 0),
                            'stock': stock_data['stock'],
                            'itemType': info.get('itemType', 'NORMAL'),
                            'genreId': info.get('genreId', ''),
                            'category': info.get('category', 'その他'),
                            'status': info.get('status', '贩售中')
                        })
                    except Exception as e:
                        print(f"获取库存失败: {e}")
            _inventory_cache['progress'] = min(i + batch_size, total)
            print(f"库存加载进度: {_inventory_cache['progress']}/{total}")
            time.sleep(1)
        _inventory_cache['data'] = all_data
        _inventory_cache['last_update'] = datetime.now().isoformat()
        _inventory_cache['loading'] = False
        print(f"库存加载完成！共 {len(all_data)} 件商品")
    except Exception as e:
        print(f"后台加载库存失败: {e}")
        _inventory_cache['loading'] = False

@app.route('/api/inventory/status', methods=['GET'])
def get_inventory_status():
    global _inventory_cache
    return jsonify({
        'loading': _inventory_cache['loading'],
        'progress': _inventory_cache['progress'],
        'total': _inventory_cache['total'],
        'last_update': _inventory_cache['last_update'],
        'has_data': _inventory_cache['data'] is not None
    })

@app.route('/api/inventory/result', methods=['GET'])
def get_inventory_result():
    global _inventory_cache
    if _inventory_cache['loading']:
        return jsonify({'status': 'loading', 'progress': _inventory_cache['progress'], 'total': _inventory_cache['total']})
    if _inventory_cache['data'] is None:
        return jsonify({'status': 'not_started'})
    return jsonify({
        'status': 'done',
        'data': _inventory_cache['data'],
        'total': len(_inventory_cache['data']),
        'last_update': _inventory_cache['last_update']
    })

@app.route('/api/inventory/all', methods=['GET'])
def get_all_inventory():
    print("开始获取库存数据...")
    start_time = time.time()
    products = get_cached_products()
    print(f"共 {len(products)} 件商品，30线程并发获取库存...")
    product_info = {p['manageNumber']: p for p in products}
    all_data = []
    done_count = 0
    max_workers = min(30, len(products) or 1)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(get_single_stock, p['manageNumber']) for p in products]
        for future in concurrent.futures.as_completed(futures):
            try:
                stock_data = future.result(timeout=30)
                info = product_info.get(stock_data['manageNumber'], {})
                all_data.append({
                    'manageNumber': stock_data['manageNumber'],
                    'name': info.get('name', '无商品名'),
                    'price': info.get('price', 0),
                    'stock': stock_data['stock'],
                    'itemType': info.get('itemType', 'NORMAL'),
                    'genreId': info.get('genreId', ''),
                    'category': info.get('category', 'その他'),
                    'status': info.get('status', '贩售中')
                })
                done_count += 1
                if done_count % 200 == 0:
                    print(f"  进度: {done_count}/{len(products)}")
            except Exception as e:
                print(f"获取库存失败: {e}")
                done_count += 1
    elapsed = time.time() - start_time
    print(f"完成！共 {len(all_data)} 件商品，耗时 {elapsed:.1f}秒")
    return jsonify(all_data)

@app.route('/api/inventory/update', methods=['POST'])
def update_inventory():
    data = request.json
    manage_number = data.get('manageNumber')
    stock = data.get('stock')
    if not manage_number:
        return jsonify({'error': 'manageNumber required'}), 400
    url = f"https://api.rms.rakuten.co.jp/es/2.1/inventories/manage-numbers/{manage_number}/variants/{manage_number}"
    body = {'quantity': stock, 'mode': 'set'}
    try:
        response = session.put(url, json=body, timeout=30)
        if response.status_code == 200:
            if _products_cache['data'] is not None:
                for p in _products_cache['data']:
                    if p['manageNumber'] == manage_number:
                        p['stock'] = stock
                        break
            return jsonify({'success': True})
        else:
            return jsonify({'error': f'API错误: {response.status_code}'}), response.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/instock', methods=['POST'])
def add_instock():
    try:
        data = request.json
        manage_number = data.get('manageNumber')
        quantity = data.get('quantity', 0)
        note = data.get('note', '')
        if not manage_number or quantity <= 0:
            return jsonify({'success': False, 'error': '参数错误'}), 400
        success, new_stock = update_inventory_direct(manage_number, quantity)
        if not success:
            return jsonify({'success': False, 'error': '更新库存失败'}), 500
        product = None
        for p in get_cached_products():
            if p['manageNumber'] == manage_number:
                product = p
                break
        records = load_stock_records()
        records['入库记录'].insert(0, {
            'id': int(time.time() * 1000),
            'manageNumber': manage_number,
            'name': product['name'] if product else '',
            'quantity': quantity,
            'note': note,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'operator': 'system'
        })
        save_stock_records(records)
        return jsonify({'success': True, 'message': f'入库{quantity}件成功', 'newStock': new_stock})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/sync-instock-from-products', methods=['POST'])
def sync_instock_from_products():
    try:
        products = get_cached_products()
        records = load_stock_records()
        existing_manage_numbers = set()
        for r in records.get('入库记录', []):
            existing_manage_numbers.add(r.get('manageNumber', ''))
        is_first_run = len(records.get('入库记录', [])) == 0
        new_count = 0
        for p in products:
            manage_number = p.get('manageNumber')
            if manage_number and manage_number not in existing_manage_numbers:
                if is_first_run:
                    stock_result = get_single_stock(manage_number)
                    quantity = stock_result.get('stock', 0)
                    note = f'初始库存同步（当前库存: {quantity}）'
                    if quantity == 0:
                        quantity = 1
                        note = '商品上架（自动入库）'
                else:
                    quantity = 1
                    note = '商品上架（自动入库）'
                records['入库记录'].insert(0, {
                    'id': int(time.time() * 1000) + new_count,
                    'manageNumber': manage_number,
                    'name': p.get('name', ''),
                    'quantity': quantity,
                    'note': note,
                    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'operator': 'system'
                })
                new_count += 1
        if new_count > 0:
            save_stock_records(records)
        return jsonify({'success': True, 'new_instock_count': new_count, 'message': f'新增{new_count}条入库记录'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/outstock', methods=['POST'])
def add_outstock():
    try:
        data = request.json
        manage_number = data.get('manageNumber')
        quantity = data.get('quantity', 0)
        note = data.get('note', '')
        if not manage_number or quantity <= 0:
            return jsonify({'success': False, 'error': '参数错误'}), 400
        current_stock = get_current_stock(manage_number)
        if current_stock < quantity:
            return jsonify({'success': False, 'error': f'库存不足，当前库存: {current_stock}'}), 400
        success, new_stock = update_inventory_direct(manage_number, -quantity)
        if not success:
            return jsonify({'success': False, 'error': '更新库存失败'}), 500
        product = None
        for p in get_cached_products():
            if p['manageNumber'] == manage_number:
                product = p
                break
        records = load_stock_records()
        records['出库记录'].insert(0, {
            'id': int(time.time() * 1000),
            'manageNumber': manage_number,
            'name': product['name'] if product else '',
            'quantity': quantity,
            'note': note,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'operator': 'system'
        })
        save_stock_records(records)
        return jsonify({'success': True, 'message': f'出库{quantity}件成功', 'newStock': new_stock})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/sync-outstock-from-orders', methods=['POST'])
def sync_outstock_from_orders():
    try:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)
        search_data = {
            "dateType": 1,
            "startDatetime": start_date.strftime("%Y-%m-%d") + "T00:00:00+0900",
            "endDatetime": end_date.strftime("%Y-%m-%d") + "T23:59:59+0900",
            "PaginationRequestModel": {
                "requestRecordsAmount": 200,
                "requestPage": 1,
                "SortModelList": []
            }
        }
        search_response = session.post(ORDER_SEARCH_URL, data=json.dumps(search_data), headers=ORDER_API_HEADERS, timeout=30)
        if search_response.status_code != 200:
            return jsonify({'success': False, 'error': f'订单搜索失败: {search_response.status_code}', 'detail': search_response.text}), 500
        search_result = search_response.json()
        order_numbers = search_result.get('orderNumberList', [])
        if not order_numbers:
            return jsonify({'success': True, 'new_outstock_count': 0, 'message': '没有订单数据'})
        records = load_stock_records()
        existing_order_ids = set()
        for r in records.get('出库记录', []):
            if r.get('orderNumber'):
                existing_order_ids.add(r['orderNumber'])
        new_order_numbers = [n for n in order_numbers if n not in existing_order_ids]
        if not new_order_numbers:
            return jsonify({'success': True, 'new_outstock_count': 0, 'message': '没有新订单'})
        new_count = 0
        for i in range(0, len(new_order_numbers), 100):
            batch = new_order_numbers[i:i+100]
            detail_response = session.post(
                ORDER_GET_URL,
                data=json.dumps({"orderNumberList": batch, "version": 10}),
                headers=ORDER_API_HEADERS,
                timeout=30
            )
            if detail_response.status_code != 200:
                print(f"获取订单详情失败: {detail_response.status_code}")
                continue
            detail_data = detail_response.json()
            orders = detail_data.get('OrderModelList', [])
            for order in orders:
                order_number = order.get('orderNumber', '')
                order_datetime = (order.get('orderDatetime') or '')[:19].replace('T', ' ')
                if not order_number:
                    continue
                packages = order.get('PackageModelList', order.get('packageModelList', []))
                for pkg in packages:
                    items = pkg.get('ItemModelList', pkg.get('itemModelList', []))
                    for item in items:
                        manage_number = item.get('manageNumber', '')
                        item_name = item.get('itemName', '')
                        quantity = item.get('units', item.get('quantity', 1))
                        if not manage_number:
                            continue
                        records['出库记录'].insert(0, {
                            'id': int(time.time() * 1000) + new_count,
                            'orderNumber': order_number,
                            'manageNumber': manage_number,
                            'name': item_name,
                            'quantity': quantity,
                            'note': f'订单出库 {order_number}',
                            'timestamp': order_datetime or datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            'operator': 'system'
                        })
                        new_count += 1
        if new_count > 0:
            save_stock_records(records)
        return jsonify({'success': True, 'new_outstock_count': new_count, 'message': f'新增{new_count}条出库记录'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/stock-records', methods=['GET'])
def get_stock_records_api():
    try:
        limit = int(request.args.get('limit', 100))
        offset = int(request.args.get('offset', 0))
        record_type = request.args.get('type', 'all')
        start_date = request.args.get('start_date', '')
        end_date = request.args.get('end_date', '')
        records = load_stock_records()
        all_records = []
        if record_type in ['all', 'out']:
            for r in records.get('出库记录', []):
                r['_type'] = '出库'
                all_records.append(r)
        if record_type in ['all', 'in']:
            for r in records.get('入库记录', []):
                r['_type'] = '入库'
                all_records.append(r)
        all_records.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        if start_date:
            all_records = [r for r in all_records if r.get('timestamp', '') >= start_date]
        if end_date:
            all_records = [r for r in all_records if r.get('timestamp', '') <= end_date + ' 23:59:59']
        total = len(all_records)
        result = all_records[offset:offset + limit]
        return jsonify({
            'success': True,
            'records': result,
            'total': total,
            'limit': limit,
            'offset': offset
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/orders/search', methods=['POST'])
def search_orders():
    data = request.json
    try:
        response = session.post(ORDER_SEARCH_URL, data=json.dumps(data), headers=ORDER_API_HEADERS, timeout=30)
        if response.status_code == 200:
            return jsonify(response.json())
        try:
            error_detail = response.json()
        except Exception:
            error_detail = response.text
        print(f"楽天订单搜索API错误 {response.status_code}: {error_detail}")
        return jsonify({'error': f'API错误: {response.status_code}', 'detail': error_detail}), response.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/orders/get', methods=['POST'])
def get_order():
    data = request.json
    try:
        response = session.post(ORDER_GET_URL, data=json.dumps(data), headers=ORDER_API_HEADERS, timeout=30)
        if response.status_code == 200:
            return jsonify(response.json())
        try:
            error_detail = response.json()
        except Exception:
            error_detail = response.text
        print(f"楽天订单详情API错误 {response.status_code}: {error_detail}")
        return jsonify({'error': f'API错误: {response.status_code}', 'detail': error_detail}), response.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/products/refresh', methods=['POST'])
def refresh_products():
    _products_cache['data'] = None
    _products_cache['time'] = 0
    products = get_cached_products(force_refresh=True)
    return jsonify({'success': True, 'count': len(products)})

# ========== 利益管理API ==========
RAKUTEN_SHIPPING_RATES = {
    '北海道': 900, '北东北': 600, '南东北': 500, '関東': 500,
    '信越': 500, '北陸': 500, '東海': 500, '関西': 600,
    '中国': 700, '四国': 700, '北九州': 900, '南九州': 900, '沖縄': 2460
}

BLACKCAT_RATES = {
    '60': {'北海道': 650, '北东北': 450, '南东北': 400, '關東': 400,
           '信越': 400, '北陸': 400, '中部': 400, '關西': 450,
           '中国': 500, '四国': 500, '九州': 650, '沖縄': 1320},
    '80': {'北海道': 800, '北东北': 510, '南东北': 450, '關東': 450,
           '信越': 450, '北陸': 450, '中部': 450, '關西': 550,
           '中国': 650, '四国': 650, '九州': 800, '沖縄': 1880}
}

@app.route('/api/benefit/calculate', methods=['POST'])
def calculate_benefit():
    try:
        data = request.json
        sales_price = int(data.get('salesPrice', 0))
        cost_price = int(data.get('costPrice', 0))
        category = data.get('category', '')
        delivery_method = data.get('deliveryMethod', 'takkyubin')
        rakuten_region = data.get('rakutenRegion', '関東')
        size = data.get('size', '60')
        blackcat_region = data.get('blackcatRegion', '關東')
        free_shipping_categories = ['食品・お菓子・天然生活', '健康食品', '美容・コスメ・ボディケア', '日用品雑貨', '医薬品', '水・ソフトドリンク']
        if category in free_shipping_categories:
            rakuten_shipping = 0
            actual_shipping = 0
        elif delivery_method == 'nekopos':
            rakuten_shipping = 200
            actual_shipping = 198
        else:
            rakuten_shipping = RAKUTEN_SHIPPING_RATES.get(rakuten_region, 500)
            rate = BLACKCAT_RATES.get(size, {}).get(blackcat_region, 0)
            actual_shipping = int(rate * 1.1) if rate else 0
        subtotal = sales_price + rakuten_shipping
        shipping_profit = rakuten_shipping - actual_shipping
        profit = subtotal - cost_price - actual_shipping
        return jsonify({
            'success': True,
            'rakuten_shipping': rakuten_shipping,
            'actual_shipping': actual_shipping,
            'subtotal': subtotal,
            'shipping_profit': shipping_profit,
            'profit': profit
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ============================================================
# 每日数据总结API
# ============================================================
@app.route('/api/daily-summary')
def daily_summary_api():
    _t0 = time.time()
    start_date_str = request.args.get('start_date', '')
    end_date_str = request.args.get('end_date', '')
    
    try:
        if not start_date_str or not end_date_str:
            today = datetime.now().strftime('%Y-%m-%d')
            start_date_str = today
            end_date_str = today
        
        categories_list = [
            'アニメ・グッズ（预售）', 'アニメ・グッズ（在库）',
            '食品・お菓子・天然生活', '健康食品', '美容・コスメ・ボディケア',
            '日用品雑貨', '医薬品', '水・ソフトドリンク', 'その他'
        ]
        
        products = get_cached_products()
        
        manage_number_to_category = {}
        for p in products:
            mn = p.get('manageNumber')
            if mn:
                manage_number_to_category[mn] = p.get('category', 'その他')
        
        category_count = {}
        category_total_price = {}
        for cat in categories_list:
            category_count[cat] = 0
            category_total_price[cat] = 0
        
        for p in products:
            category = p.get('category', 'その他')
            price = p.get('price', 0)
            if category in category_count:
                category_count[category] += 1
                category_total_price[category] += price
            else:
                category_count['その他'] = category_count.get('その他', 0) + 1
                category_total_price['その他'] = category_total_price.get('その他', 0) + price
        
        total_orders = 0
        total_sales = 0
        total_item_sum = 0
        total_shipping_sum = 0
        order_details = []
        cancelled_count = 0
        
        try:
            search_data = {
                "dateType": 1,
                "startDatetime": start_date_str + "T00:00:00+0900",
                "endDatetime": end_date_str + "T23:59:59+0900",
                "PaginationRequestModel": {
                    "requestRecordsAmount": 200,
                    "requestPage": 1,
                    "SortModelList": []
                }
            }
            
            search_response = session.post(ORDER_SEARCH_URL, data=json.dumps(search_data), headers=ORDER_API_HEADERS, timeout=30)
            
            if search_response.status_code == 200:
                search_result = search_response.json()
                order_numbers = search_result.get('orderNumberList', [])
                
                for i in range(0, len(order_numbers), 100):
                    batch = order_numbers[i:i + 100]
                    detail_response = session.post(
                        ORDER_GET_URL,
                        data=json.dumps({"orderNumberList": batch, "version": 10}),
                        headers=ORDER_API_HEADERS,
                        timeout=30
                    )
                    
                    if detail_response.status_code != 200:
                        continue
                    
                    detail_data = detail_response.json()
                    orders = detail_data.get('OrderModelList', [])
                    
                    for order in orders:
                        order_progress = order.get('orderProgress', 0)
                        order_number = order.get('orderNumber', '')
                        
                        if order_progress == 600:
                            cancelled_count += 1
                            continue
                        
                        total_orders += 1
                        
                        shipping = order.get('postagePrice', 0)
                        if shipping == 0:
                            shipping = order.get('shippingPrice', 0)
                        if shipping == 0:
                            packages = order.get('PackageModelList', []) or order.get('packageModelList', [])
                            for pkg in packages:
                                pkg_shipping = pkg.get('postagePrice', 0) or pkg.get('shippingPrice', 0) or pkg.get('carriage', 0)
                                shipping += pkg_shipping
                        
                        request_price = order.get('requestPrice', 0) or order.get('totalPrice', 0)
                        
                        if request_price > 0:
                            total_amount = request_price
                            item_total = request_price - shipping
                            if item_total < 0:
                                item_total = request_price
                        else:
                            item_total = 0
                            packages = order.get('PackageModelList', order.get('packageModelList', [])) or []
                            for pkg in packages:
                                items = pkg.get('ItemModelList', pkg.get('itemModelList', [])) or []
                                for item in items:
                                    price = item.get('price', 0)
                                    quantity = item.get('units', item.get('quantity', 1))
                                    item_total += price * quantity
                            total_amount = item_total + shipping
                        
                        total_item_sum += item_total
                        total_shipping_sum += shipping
                        total_sales += total_amount
                        
                        orderer_info = order.get('OrdererModel', {}) or order.get('ordererModel', {})
                        family_name = orderer_info.get('familyName', '') or ''
                        first_name = orderer_info.get('firstName', '') or ''
                        customer_name = (family_name + first_name).strip()
                        if not customer_name:
                            customer_name = orderer_info.get('familyNameKana', '') or orderer_info.get('firstNameKana', '') or '---'
                        
                        packages = order.get('PackageModelList', order.get('packageModelList', [])) or []
                        item_list = []
                        for pkg in packages:
                            items = pkg.get('ItemModelList', pkg.get('itemModelList', [])) or []
                            for item in items:
                                manage_number = item.get('manageNumber', '')
                                item_list.append({
                                    'name': item.get('itemName', ''),
                                    'quantity': item.get('units', item.get('quantity', 1)),
                                    'price': item.get('price', 0),
                                    'manageNumber': manage_number
                                })
                        
                        order_category = 'その他'
                        for it in item_list:
                            mn = it.get('manageNumber')
                            if mn and mn in manage_number_to_category:
                                order_category = manage_number_to_category[mn]
                                break
                        
                        order_details.append({
                            'orderNumber': order_number,
                            'orderDate': (order.get('orderDatetime', '')[:10]) if order.get('orderDatetime') else '',
                            'customerName': customer_name,
                            'item_total': item_total,
                            'shipping': shipping,
                            'totalAmount': total_amount,
                            'status': order_progress,
                            'category': order_category,
                            'items': item_list
                        })
            
            print(f"[每日总结] 有效订单数: {total_orders}, 取消订单数: {cancelled_count}, "
                  f"商品金额合计: ¥{total_item_sum}, 运费合计: ¥{total_shipping_sum}, "
                  f"总销售额: ¥{total_sales}, 耗时: {time.time()-_t0:.1f}秒")
                        
        except Exception as e:
            print(f"订单API调用异常: {e}")
        
        estimated_profit = int(total_item_sum * 0.3) if total_item_sum > 0 else 0
        profit_rate = (estimated_profit / total_item_sum * 100) if total_item_sum > 0 else 0
        
        stock_items = []
        total_stock_value = 0
        
        try:
            target_products = products[:100]
            with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor:
                futures = {executor.submit(get_single_stock, p['manageNumber']): p for p in target_products}
                for future in concurrent.futures.as_completed(futures):
                    p = futures[future]
                    try:
                        stock_result = future.result()
                    except Exception:
                        continue
                    stock_qty = stock_result.get('stock', 0)
                    if stock_qty > 0:
                        price = p.get('price', 0)
                        value = price * stock_qty
                        total_stock_value += value
                        stock_items.append({
                            'name': p.get('name', '')[:35],
                            'price': price,
                            'qty': stock_qty,
                            'value': value,
                            'category': p.get('category', 'その他')
                        })
            stock_items.sort(key=lambda x: x['value'], reverse=True)
            print(f"[每日总结] 库存查询完成，耗时: {time.time()-_t0:.1f}秒")
        except Exception as e:
            print(f"库存统计异常: {e}")
        
        category_list = []
        for cat in categories_list:
            cat_info = get_category_info(cat)
            category_list.append({
                'name': cat,
                'count': category_count.get(cat, 0),
                'sales': category_total_price.get(cat, 0),
                'profit': 0,
                'icon': cat_info['icon'],
                'color': cat_info['color']
            })
        
        result = {
            'success': True,
            'summary': {
                'period': {'start_date': start_date_str, 'end_date': end_date_str},
                'total_products': len(products),
                'total': {
                    'count': total_orders,
                    'sales': total_sales,
                    'item_total': total_item_sum,
                    'shipping_total': total_shipping_sum,
                    'profit': estimated_profit,
                    'profit_rate': profit_rate,
                    'cancelled_count': cancelled_count
                },
                'categories': category_list,
                'order_items': order_details,
                'stock_summary': {
                    'total_value': total_stock_value,
                    'top_items': stock_items[:10]
                }
            }
        }
        
        print(f"[每日总结] 全部完成，总耗时: {time.time()-_t0:.1f}秒")
        return jsonify(result)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/debug/orders', methods=['GET'])
def debug_orders():
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        search_data = {
            "dateType": 1,
            "startDatetime": week_ago + "T00:00:00+0900",
            "endDatetime": today + "T23:59:59+0900",
            "PaginationRequestModel": {
                "requestRecordsAmount": 10,
                "requestPage": 1,
                "SortModelList": []
            }
        }
        search_response = session.post(ORDER_SEARCH_URL, data=json.dumps(search_data), headers=ORDER_API_HEADERS, timeout=30)
        search_result = search_response.json()
        order_numbers = search_result.get('orderNumberList', [])
        if not order_numbers:
            return jsonify({'search_result': search_result, 'detail_result': None, 'order_numbers': []})
        detail_response = session.post(
            ORDER_GET_URL,
            data=json.dumps({"orderNumberList": order_numbers[:3], "version": 10}),
            headers=ORDER_API_HEADERS,
            timeout=30
        )
        detail_result = detail_response.json()
        return jsonify({
            'order_numbers': order_numbers[:3],
            'search_status': search_response.status_code,
            'detail_status': detail_response.status_code,
            'detail_result': detail_result
        })
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'trace': traceback.format_exc()})

# ============================================================
# 第七部分：静态页面路由
# ============================================================

@app.route('/')
def index():
    return send_file('products.html')

@app.route('/products')
def products():
    return send_file('products.html')

@app.route('/inventory')
def inventory():
    return send_file('inventory.html')

@app.route('/orders')
def orders():
    return send_file('orders.html')

@app.route('/benefit')
def benefit_page():
    return send_file('benefit.html')

@app.route('/daily-summary')
def daily_summary_page():
    return send_file('daily_summary.html')

@app.route('/gs-inventory')
def gs_inventory_page():
    """Google Sheets 在库管理页面"""
    return send_file('gs_inventory.html')

# ============================================================
# 启动
# ============================================================
if __name__ == '__main__':
    print("=" * 60)
    print("乐天店铺管理系统启动（整合版）")
    print(f"数据文件: {STOCK_RECORDS_FILE}")
    print("商品管理: http://127.0.0.1:5000/products")
    print("在庫管理: http://127.0.0.1:5000/inventory")
    print("贩卖信息: http://127.0.0.1:5000/orders")
    print("利益管理: http://127.0.0.1:5000/benefit")
    print("每日总结: http://127.0.0.1:5000/daily-summary")
    print("Google Sheets在库管理: http://127.0.0.1:5000/gs-inventory")
    print("=" * 60)
    
    port = int(os.environ.get('PORT', 5000))
    debug_mode = os.environ.get('FLASK_ENV', 'production') != 'production'
    app.run(host='0.0.0.0', port=port, debug=debug_mode)
