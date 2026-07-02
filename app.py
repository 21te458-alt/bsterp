# -*- coding: utf-8 -*-
import os
import sys
import requests
import base64
import json
import concurrent.futures
import time
from datetime import datetime, timedelta
from flask import Flask, send_file, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

# ===== 加载环境变量 =====
load_dotenv()

app = Flask(__name__)
CORS(app)
app.config['JSON_AS_ASCII'] = False

# ===== 从环境变量读取敏感信息 =====
SERVICE_SECRET = os.environ.get('SERVICE_SECRET', 'SP406647_AaHdEvRVKrO74RDh')
LICENSE_KEY = os.environ.get('LICENSE_KEY', 'SL406647_UUcHdvDI3ZNP0Br3')
SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-me')
app.config['SECRET_KEY'] = SECRET_KEY

# ===== 数据文件路径（兼容 Render 部署） =====
DATA_DIR = os.environ.get('DATA_DIR', os.path.dirname(os.path.abspath(__file__)))
STOCK_RECORDS_FILE = os.path.join(DATA_DIR, 'stock_records.json')

os.makedirs(os.path.dirname(STOCK_RECORDS_FILE) or '.', exist_ok=True)

print(f"[启动] 数据文件路径: {STOCK_RECORDS_FILE}")
print(f"[启动] 环境: {'生产' if os.environ.get('RENDER') else '开发'}")

def get_auth_header():
    auth_string = f"{SERVICE_SECRET}:{LICENSE_KEY}"
    encoded = base64.b64encode(auth_string.encode()).decode()
    return f"ESA {encoded}"

# 复用TCP连接
session = requests.Session()
session.headers.update({'Authorization': get_auth_header()})

# 商品列表缓存
_products_cache = {'data': None, 'time': 0}
_inventory_cache = {'data': None, 'loading': False, 'progress': 0, 'total': 0, 'last_update': None}
PRODUCTS_CACHE_TTL = 300

# ========== アニメ・グッズのgenreId ==========
ANIME_GENRES = [
    '112938', '101914', '567549', '201632', '112203', '101841',
    '403809', '111163', '551732', '566157', '101197', '101917',
    '301748', '403672', '300927', '303097', '210207', '567731',
    '112334', '408789', '207288', '112928', '553697', '400872',
    '406770', '216058', '406864', '111377', '553789', '216132',
    '403755', '207314', '303656'
]

# ========== カテゴリ分類関数 ==========
def determine_category(title, genre_id, item_type, purchasable_period):
    """根据商品信息判断分类（8分类）"""
    
    # 1. 食品・お菓子・天然生活
    food_keywords = ['【天然生活】', '天然生活', '食品', 'お菓子', 'スイーツ', 'ケーキ', 'ラーメン', 'カレー', 'うどん', 'そば', 'パスタ', 'スープ', 'お茶', '紅茶', 'コーヒー', 'ジュース', 'ゼリー', 'プリン', 'アイス', 'クッキー', 'チョコ', 'キャンディ', 'ナッツ', 'ドライフルーツ', 'せんべい', 'かりんとう', 'ようかん', 'まんじゅう', 'どら焼き', 'たい焼き']
    for kw in food_keywords:
        if kw in title:
            return '食品・お菓子・天然生活'
    
    # 2. 健康食品
    health_keywords = ['健康食品', 'サプリ', 'サプリメント', '青汁', '酵素', 'NMN', '乳酸菌', 'プロテイン', 'ビタミン', 'カルシウム', '鉄分', 'マルチビタミン', 'フォルスコリ', 'プラセンタ', 'コラーゲン', 'HMB', 'プロテオグリカン', 'じゃばら']
    for kw in health_keywords:
        if kw in title:
            return '健康食品'
    
    # 3. 美容・コスメ・ボディケア
    beauty_keywords = ['美容', 'コスメ', 'ボディケア', '化粧品', 'クリーム', 'ジェル', '日焼け止め', 'マッサージ', 'ローション', '美容液', '洗顔', 'シャンプー', 'リンス', 'コンディショナー', 'ボディソープ']
    for kw in beauty_keywords:
        if kw in title:
            return '美容・コスメ・ボディケア'
    
    # 4. 日用品雑貨
    daily_keywords = ['日用品', '雑貨', '手袋', 'マフラー', 'ストール', 'タオル', 'バッグ', 'ポーチ', 'マスク', 'ハンドジェル']
    for kw in daily_keywords:
        if kw in title:
            return '日用品雑貨'
    
    # 5. 医薬品
    medicine_keywords = ['医薬品', '薬', '第1類', '第2類', '第3類', 'ロキソプロフェン', '風邪薬', '胃薬', '鎮痛剤', '解熱剤']
    for kw in medicine_keywords:
        if kw in title:
            return '医薬品'
    
    # 6. 水・ソフトドリンク
    drink_keywords = ['水', 'ドリンク', '飲料', 'ソフトドリンク', 'ミネラルウォーター', '炭酸水', 'エナジードリンク', 'スポーツドリンク']
    for kw in drink_keywords:
        if kw in title:
            return '水・ソフトドリンク'
    
    # 7. アニメ・グッズ（根据purchasablePeriod区分预售/在库）
    if genre_id in ANIME_GENRES:
        if purchasable_period and purchasable_period.get('start'):
            return 'アニメ・グッズ（预售）'
        else:
            return 'アニメ・グッズ（在库）'
    
    # 8. その他
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
    """加载库存记录（同时支持入库和出库）"""
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
    """保存库存记录"""
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
        backup_file = STOCK_RECORDS_FILE + '.backup'
        try:
            with open(backup_file, 'w', encoding='utf-8') as f:
                json.dump(records, f, ensure_ascii=False, indent=2)
            print(f"数据已保存到备份文件: {backup_file}")
        except Exception as e2:
            print(f"备份也失败: {e2}")
        return False

def get_current_stock(manage_number):
    """获取当前库存"""
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
    """直接更新库存（change为正数入库，负数出库）"""
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

# ========== 获取单个商品库存（带重试） ==========
def get_single_stock(manage_number):
    """获取单个商品库存（带重试）"""
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

# ========== 商品API ==========
@app.route('/api/products', methods=['GET'])
def get_products():
    force_refresh = request.args.get('refresh', 'false').lower() == 'true'
    return jsonify(get_cached_products(force_refresh))

# ========== 🆕 获取单个商品库存（前端按需调用） ==========
@app.route('/api/inventory/single/<manage_number>', methods=['GET'])
def get_single_inventory(manage_number):
    """获取单个商品库存（供前端分批加载）"""
    try:
        stock_data = get_single_stock(manage_number)
        return jsonify({
            'manageNumber': stock_data['manageNumber'],
            'stock': stock_data['stock']
        })
    except Exception as e:
        return jsonify({'manageNumber': manage_number, 'stock': 0, 'error': str(e)}), 500

# ========== 🆕 批量获取库存（前端分批请求，避免超时） ==========
@app.route('/api/inventory/batch', methods=['POST'])
def get_inventory_batch():
    """批量获取库存（前端传入商品列表，每批最多100件）"""
    try:
        data = request.json
        manage_numbers = data.get('manageNumbers', [])
        
        if not manage_numbers:
            return jsonify([])
        
        # 限制单次请求数量，避免超时
        if len(manage_numbers) > 100:
            return jsonify({'error': 'Too many items, max 100 per request'}), 400
        
        results = []
        # 使用小线程池，避免限流
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

# ========== 🆕 异步加载库存（后台执行，轮询进度） ==========
@app.route('/api/inventory/start-load', methods=['POST'])
def start_inventory_load():
    """启动后台库存加载任务"""
    global _inventory_cache
    
    if _inventory_cache['loading']:
        return jsonify({'status': 'loading', 'progress': _inventory_cache['progress'], 'total': _inventory_cache['total']})
    
    products = get_cached_products()
    _inventory_cache['loading'] = True
    _inventory_cache['progress'] = 0
    _inventory_cache['total'] = len(products)
    _inventory_cache['data'] = None
    _inventory_cache['last_update'] = None
    
    # 在后台线程中执行
    import threading
    thread = threading.Thread(target=_load_inventory_background)
    thread.daemon = True
    thread.start()
    
    return jsonify({'status': 'started', 'total': len(products)})

def _load_inventory_background():
    """后台加载库存数据"""
    global _inventory_cache
    
    try:
        products = get_cached_products()
        total = len(products)
        all_data = []
        batch_size = 50  # 每批50件
        
        product_info = {p['manageNumber']: p for p in products}
        
        for i in range(0, total, batch_size):
            batch = products[i:i + batch_size]
            batch_numbers = [p['manageNumber'] for p in batch]
            
            # 并发获取这批库存
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
            
            # 更新进度
            _inventory_cache['progress'] = min(i + batch_size, total)
            print(f"库存加载进度: {_inventory_cache['progress']}/{total}")
            
            # 每批之间休息1秒，避免限流
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
    """获取库存加载状态"""
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
    """获取已加载的库存数据"""
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

# ========== 原有库存API（保留兼容，但增加超时保护） ==========
@app.route('/api/inventory/all', methods=['GET'])
def get_all_inventory():
    """获取所有商品库存（直接方式，可能超时，建议使用异步方式）"""
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

# ========== 更新库存 ==========
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

# ========== 入库API ==========
@app.route('/api/instock', methods=['POST'])
def add_instock():
    """手动添加入库记录"""
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
    """从商品API同步入库记录（新商品上架时自动入库）"""
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

# ========== 出库API ==========
@app.route('/api/outstock', methods=['POST'])
def add_outstock():
    """手动添加出库记录"""
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
    """从订单API同步出库记录（仅记录，不扣减库存）"""
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

# ========== 获取库存记录API（支持分页） ==========
@app.route('/api/stock-records', methods=['GET'])
def get_stock_records_api():
    """获取库存记录（支持分页和日期过滤）"""
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

# ========== 受注API ==========
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

# ========== 强制刷新商品缓存 ==========
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

# ========== 每日数据总结API（修改版：排除取消订单） ==========
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
        order_details = []
        cancelled_count = 0  # 🆕 统计取消订单数
        
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
                
                # 分批获取全部订单详情
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
                        # 🔧 检查订单状态，跳过已取消的订单 (600)
                        order_progress = order.get('orderProgress', 0)
                        order_number = order.get('orderNumber', '')
                        
                        if order_progress == 600:
                            cancelled_count += 1
                            print(f"⏭️ 跳过取消订单: {order_number}")
                            continue
                        
                        total_orders += 1
                        total_amount = order.get('requestPrice', 0) or order.get('totalPrice', 0) or 0
                        total_sales += total_amount
                        
                        orderer_info = order.get('ordererModel', order.get('OrdererModel', order.get('ordererInfo', {}))) or {}
                        last_name = orderer_info.get('ordererLastName', '') or ''
                        first_name = orderer_info.get('ordererFirstName', '') or ''
                        customer_name = (last_name + first_name).strip() or orderer_info.get('ordererLastNameKana', '') or '---'
                        
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
                            'totalAmount': total_amount,
                            'status': order_progress,
                            'category': order_category,
                            'items': item_list
                        })
            
            print(f"[每日总结] 有效订单数: {total_orders}, 取消订单数: {cancelled_count}, 销售额: ¥{total_sales}, 耗时: {time.time()-_t0:.1f}秒")
                        
        except Exception as e:
            print(f"订单API调用异常: {e}")
        
        estimated_profit = int(total_sales * 0.3)
        profit_rate = (estimated_profit / total_sales * 100) if total_sales > 0 else 0
        
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
                    'profit': estimated_profit,
                    'profit_rate': profit_rate,
                    'cancelled_count': cancelled_count  # 🆕 返回取消订单数
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
    """调试用：查看订单API原始返回结构"""
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

# ========== 健康检查端点 ==========
@app.route('/health')
def health_check():
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'data_file_exists': os.path.exists(STOCK_RECORDS_FILE),
        'cache_age': int(time.time() - _products_cache['time']) if _products_cache['data'] else None
    })

# ========== 静态页面路由 ==========
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

# ========== 启动 ==========
if __name__ == '__main__':
    print("=" * 60)
    print("乐天店铺管理系统启动")
    print(f"数据文件: {STOCK_RECORDS_FILE}")
    print("商品管理: http://127.0.0.1:5000/products")
    print("在庫管理: http://127.0.0.1:5000/inventory")
    print("贩卖信息: http://127.0.0.1:5000/orders")
    print("利益管理: http://127.0.0.1:5000/benefit")
    print("每日总结: http://127.0.0.1:5000/daily-summary")
    print("健康检查: http://127.0.0.1:5000/health")
    print("=" * 60)
    
    port = int(os.environ.get('PORT', 5000))
    debug_mode = os.environ.get('FLASK_ENV', 'production') != 'production'
    app.run(host='0.0.0.0', port=port, debug=debug_mode)
