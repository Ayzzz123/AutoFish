# 闲鱼自动化系统 - Flask 主应用
# 提供 REST API 和 Web 管理界面

import asyncio
import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from flask import Flask, jsonify, request, send_file, send_from_directory, render_template_string
from flask_cors import CORS

from config import FLASK_HOST, FLASK_PORT, FLASK_DEBUG, MONITOR_INTERVAL_SECONDS, AUTO_DELIVERY_ENABLED, PRODUCTS_DIR
from models import (
    add_product, update_product, get_product, get_all_products, delete_product,
    add_order, update_order, get_order, get_orders, get_pending_delivery_orders,
    add_log, get_logs, get_setting, set_setting, init_db,
    mark_order_delivery_sent, recover_stale_deliveries
)
from goofish_bot import (
    check_login_status, open_login_and_wait, publish_product_quick, unpublish_product,
    fetch_sold_orders, send_im_message, auto_deliver_order,
    close_browser, test_connection, save_storage_state, download_reference_images
)
from baidu_pan import package_product_to_baidu, package_and_get_link
from github_scraper import (
    search_github_repos, bulk_search_github, create_product_from_repo,
    auto_generate_products_from_github, DEFAULT_SEARCH_QUERIES
)

# 获取项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path='')
CORS(app, resources={r'/api/*': {'origins': [
    'http://127.0.0.1:5000',
    'http://localhost:5000',
    'https://www.goofish.com',
]}})

# 监控线程
_monitor_thread = None
_monitor_running = False
_login_state = {'in_progress': False, 'logged_in': False, 'error': ''}
_login_state_lock = threading.Lock()
# 登录状态缓存，避免频繁启动浏览器
_login_status_cache = {'logged_in': False, 'checked_at': 0}
_login_cache_ttl = 300  # 5分钟缓存


# ==================== 主页 ====================

@app.route('/')
def index():
    """管理后台主页"""
    return send_from_directory(FRONTEND_DIR, 'index.html')


@app.route('/<path:path>')
def static_files(path):
    """静态文件服务"""
    if os.path.exists(os.path.join(FRONTEND_DIR, path)):
        return send_from_directory(FRONTEND_DIR, path)
    return jsonify({'error': 'Not found'}), 404


# ==================== 商品 API ====================

@app.route('/api/products', methods=['GET'])
def api_get_products():
    """获取所有商品"""
    status = request.args.get('status')
    products = get_all_products(status=status)
    result = []
    for p in products:
        result.append({
            'id': p['id'],
            'title': p['title'],
            'description': p['description'],
            'price': p['price'],
            'original_price': p['original_price'],
            'category': p['category'],
            'images': json.loads(p['images']) if p['images'] else [],
            'delivery_content': p['delivery_content'],
            'goofish_item_id': p['goofish_item_id'],
            'goofish_url': p['goofish_url'],
            'status': p['status'],
            'created_at': p['created_at'],
        })
    return jsonify({'success': True, 'data': result, 'count': len(result)})


@app.route('/api/products', methods=['POST'])
def api_add_product():
    """添加商品"""
    data = request.json
    if not data:
        return jsonify({'success': False, 'error': '缺少数据'}), 400

    title = data.get('title', '').strip()
    description = data.get('description', '').strip()
    price = float(data.get('price', 0))
    delivery_content = data.get('delivery_content', '').strip()

    if not title or not delivery_content:
        return jsonify({'success': False, 'error': '标题和发货内容不能为空'}), 400
    if price <= 0:
        return jsonify({'success': False, 'error': '价格必须大于0'}), 400

    product_id = add_product(
        title=title,
        description=description,
        price=price,
        original_price=float(data.get('original_price', price * 10)),
        delivery_content=delivery_content,
        category=data.get('category', ''),
        images=data.get('images', [])
    )
    add_log('添加商品', f'商品 "{title}" ID={product_id}', 'success')
    return jsonify({'success': True, 'data': {'id': product_id}})


@app.route('/api/products/<int:product_id>', methods=['GET'])
def api_get_product(product_id):
    """获取单个商品"""
    p = get_product(product_id)
    if not p:
        return jsonify({'success': False, 'error': '商品不存在'}), 404
    return jsonify({'success': True, 'data': {
        'id': p['id'], 'title': p['title'], 'description': p['description'],
        'price': p['price'], 'original_price': p['original_price'],
        'category': p['category'], 'images': json.loads(p['images']) if p['images'] else [],
        'delivery_content': p['delivery_content'],
        'goofish_item_id': p['goofish_item_id'], 'goofish_url': p['goofish_url'],
        'status': p['status'], 'created_at': p['created_at'],
    }})


@app.route('/api/products/<int:product_id>/image', methods=['GET'])
def api_get_product_image(product_id):
    """Serve the first product image from the managed products directory."""
    product = get_product(product_id)
    if not product:
        return jsonify({'success': False, 'error': '商品不存在'}), 404

    images = json.loads(product['images']) if product['images'] else []
    if not images:
        return jsonify({'success': False, 'error': '商品没有图片'}), 404

    image_path = Path(images[0]).resolve()
    products_root = Path(PRODUCTS_DIR).resolve()
    if not image_path.is_relative_to(products_root) or not image_path.is_file():
        return jsonify({'success': False, 'error': '商品图片不存在'}), 404
    return send_file(image_path)


@app.route('/api/products/<int:product_id>/image', methods=['POST'])
def api_upload_product_image(product_id):
    """Save one product cover image and replace the product image list."""
    if not get_product(product_id):
        return jsonify({'success': False, 'error': '商品不存在'}), 404
    if request.content_length and request.content_length > 10 * 1024 * 1024:
        return jsonify({'success': False, 'error': '图片不能超过 10MB'}), 413

    image = request.files.get('image')
    extension = Path(image.filename or '').suffix.lower() if image else ''
    if not image or extension not in {'.png', '.jpg', '.jpeg', '.webp'}:
        return jsonify({'success': False, 'error': '请选择 PNG、JPG 或 WEBP 图片'}), 400

    product_dir = Path(PRODUCTS_DIR) / f'product_{product_id}'
    product_dir.mkdir(parents=True, exist_ok=True)
    image_path = (product_dir / f'cover_{uuid4().hex}{extension}').resolve()
    image.save(image_path)
    update_product(product_id, images=[str(image_path)])
    add_log('更新商品图片', f'商品 ID={product_id} 已更新图片', 'success')
    return jsonify({
        'success': True,
        'data': {'image_url': f'/api/products/{product_id}/image'},
    })


@app.route('/api/products/<int:product_id>', methods=['PUT'])
def api_update_product(product_id):
    """更新商品"""
    data = request.json
    if not data:
        return jsonify({'success': False, 'error': '缺少数据'}), 400

    allowed_fields = ['title', 'description', 'price', 'original_price',
                      'delivery_content', 'category', 'images', 'status',
                      'goofish_item_id', 'goofish_url']
    updates = {k: v for k, v in data.items() if k in allowed_fields}
    if updates:
        update_product(product_id, **updates)
        add_log('更新商品', f'商品 ID={product_id} 已更新', 'success')
    return jsonify({'success': True})


@app.route('/api/products/<int:product_id>', methods=['DELETE'])
def api_delete_product(product_id):
    """删除商品"""
    delete_product(product_id)
    add_log('删除商品', f'商品 ID={product_id} 已删除')
    return jsonify({'success': True})


# ==================== 订单 API ====================

@app.route('/api/orders', methods=['GET'])
def api_get_orders():
    """获取所有订单"""
    status = request.args.get('status')
    orders = get_orders(status=status)
    result = []
    for o in orders:
        result.append({
            'id': o['id'], 'product_id': o['product_id'],
            'goofish_order_id': o['goofish_order_id'],
            'buyer_name': o['buyer_name'], 'buyer_user_id': o['buyer_user_id'],
            'amount': o['amount'], 'status': o['status'],
            'delivery_sent': bool(o['delivery_sent']),
            'delivery_content': o['delivery_content'],
            'sent_at': o['sent_at'], 'detected_at': o['detected_at'],
            'remark': o['remark'],
            'delivery_status': o['delivery_status'],
            'delivery_attempts': o['delivery_attempts'],
            'delivery_error': o['delivery_error'],
            'delivery_started_at': o['delivery_started_at'],
            'last_delivery_attempt_at': o['last_delivery_attempt_at'],
        })
    return jsonify({'success': True, 'data': result, 'count': len(result)})


@app.route('/api/orders/<int:order_id>/deliver', methods=['POST'])
def api_deliver_order(order_id):
    """手动触发发货"""
    order = get_order(order_id)
    if not order:
        return jsonify({'success': False, 'error': '订单不存在'}), 404

    # 检查订单是否可发货
    if order['status'] != 'paid' or order['delivery_sent'] or order['delivery_status'] in ('sending', 'review', 'sent'):
        return jsonify({'success': False, 'error': '订单当前状态不可发货'}), 409

    result = asyncio.run(auto_deliver_order(order_id))
    delivery_status = result.get('status', 'conflict')
    error = result.get('error', '')

    if delivery_status == 'sent':
        return jsonify({'success': True, 'data': {'delivery_status': 'sent'}, 'message': '发货成功'})
    elif delivery_status == 'failed':
        return jsonify({'success': False, 'error': error, 'data': {'delivery_status': 'failed'}}), 422
    elif delivery_status == 'review':
        return jsonify({'success': False, 'error': f'需要人工检查: {error}' if error else '需要人工检查', 'data': {'delivery_status': 'review'}}), 409
    else:
        return jsonify({'success': False, 'error': error, 'data': {'delivery_status': delivery_status}}), 409


# ==================== GitHub采集 API ====================

@app.route('/api/github/search', methods=['POST'])
def api_github_search():
    """搜索GitHub仓库"""
    data = request.json
    query = data.get('query', '').strip()
    if not query:
        return jsonify({'success': False, 'error': '请输入搜索关键词'}), 400

    sort = data.get('sort', 'stars')
    max_results = min(int(data.get('max_results', 10)), 30)

    add_log('GitHub搜索', f'搜索关键词: {query}')
    repos = asyncio.run(search_github_repos(query, sort, max_results))
    return jsonify({'success': True, 'data': repos, 'count': len(repos)})


@app.route('/api/github/bulk-search', methods=['POST'])
def api_github_bulk_search():
    """批量搜索GitHub"""
    data = request.json
    queries = data.get('queries', DEFAULT_SEARCH_QUERIES[:5])
    repos = asyncio.run(bulk_search_github(queries))
    return jsonify({'success': True, 'data': repos, 'count': len(repos)})


@app.route('/api/github/create-products', methods=['POST'])
def api_github_create_products():
    """从GitHub搜索结果批量创建商品"""
    data = request.json
    repos = data.get('repos', [])
    price = float(data.get('price', 9.90))

    if not repos:
        return jsonify({'success': False, 'error': '没有仓库数据'}), 400

    product_ids = auto_generate_products_from_github(repos, price)
    return jsonify({'success': True, 'data': {'product_ids': product_ids}, 'message': f'已创建 {len(product_ids)} 个商品'})


# ==================== 闲鱼操作 API ====================

@app.route('/api/goofish/status', methods=['GET'])
def api_goofish_status():
    """获取闲鱼连接状态（5分钟内缓存，避免频繁启动浏览器）"""
    global _login_status_cache
    now = time.time()
    if now - _login_status_cache['checked_at'] < _login_cache_ttl:
        return jsonify({
            'success': True,
            'connected': _login_status_cache['logged_in'],
            'logged_in': _login_status_cache['logged_in'],
            'title': 'Goofish (cached)',
        })

    async def inspect_status():
        result = await test_connection()
        logged_in = await check_login_status()
        await close_browser()
        return result, logged_in

    result, logged_in = asyncio.run(inspect_status())
    _login_status_cache = {'logged_in': logged_in, 'checked_at': now}
    return jsonify({
        'success': True,
        'connected': result.get('success', False),
        'logged_in': logged_in,
        'title': result.get('title', ''),
    })


@app.route('/api/goofish/login', methods=['POST'])
def api_goofish_login():
    """打开闲鱼登录页并在后台等待登录完成。"""
    timeout = request.json.get('timeout', 180) if request.json else 180
    with _login_state_lock:
        if _login_state['in_progress']:
            return jsonify({'success': True, 'message': '闲鱼登录页已打开'})
        _login_state.update({'in_progress': True, 'logged_in': False, 'error': ''})

    def do_login():
        result = asyncio.run(open_login_and_wait(timeout))
        with _login_state_lock:
            _login_state.update({
                'in_progress': False,
                'logged_in': result.get('success', False),
                'error': result.get('error', ''),
            })

    threading.Thread(target=do_login, daemon=True).start()
    return jsonify({'success': True, 'message': '请在打开的闲鱼页面完成登录'}), 202


@app.route('/api/goofish/login-status', methods=['GET'])
def api_goofish_login_status():
    """读取后台登录任务状态，不操作浏览器。"""
    with _login_state_lock:
        state = dict(_login_state)
    return jsonify({'success': True, **state})


@app.route('/api/goofish/unpublish', methods=['POST'])
def api_goofish_unpublish():
    """在闲鱼下架商品，成功后再同步本地状态。"""
    data = request.json or {}
    product_id = data.get('product_id')
    product = get_product(product_id)
    if not product:
        return jsonify({'success': False, 'error': '商品不存在'}), 404
    if not product['goofish_item_id']:
        return jsonify({'success': False, 'error': '商品尚未关联闲鱼商品 ID'}), 400

    result = asyncio.run(unpublish_product(product['goofish_item_id']))
    if not result.get('success'):
        return jsonify(result), 409

    update_product(product_id, status='removed')
    add_log('商品下架', f'商品 {product_id} 已从闲鱼下架', 'success')
    return jsonify(result)


@app.route('/api/goofish/publish', methods=['POST'])
def api_goofish_publish():
    """发布商品到闲鱼"""
    data = request.json
    product_id = data.get('product_id')

    product = get_product(product_id)
    if not product:
        return jsonify({'success': False, 'error': '商品不存在'}), 404

    images = json.loads(product['images']) if product['images'] else []
    product_data = {
        'title': product['title'],
        'description': product['description'],
        'price': product['price'],
        'original_price': product['original_price'],
        'image_path': images[0] if images else '',
        'category': product['category'],
        'free_shipping': True,
    }

    result = asyncio.run(publish_product_quick(product_data))

    if result.get('success'):
        if result.get('item_id'):
            update_product(product_id, goofish_item_id=result['item_id'], goofish_url=result.get('url', ''), status='listed')

    return jsonify(result)


@app.route('/api/goofish/orders', methods=['GET'])
def api_goofish_sold_orders():
    """获取已卖出订单"""
    orders = asyncio.run(fetch_sold_orders())
    return jsonify({'success': True, 'data': orders, 'count': len(orders)})


@app.route('/api/goofish/download-images', methods=['POST'])
def api_download_images():
    """从闲鱼搜索下载同类商品图片"""
    data = request.json
    keyword = data.get('keyword', '').strip()
    count = min(int(data.get('count', 5)), 10)
    if not keyword:
        return jsonify({'success': False, 'error': '请输入搜索关键词'}), 400

    save_dir = os.path.join(PRODUCTS_DIR, 'ref_images')
    images = asyncio.run(download_reference_images(keyword, save_dir, count))
    return jsonify({'success': True, 'data': images, 'count': len(images)})


@app.route('/api/baidu/package', methods=['POST'])
def api_baidu_package():
    """一键打包商品到百度网盘：创建文件夹 → 上传文件 → 生成分享链接"""
    data = request.json
    product_id = data.get('product_id')

    if not product_id:
        return jsonify({'success': False, 'error': '缺少product_id'}), 400

    product = get_product(product_id)
    if not product:
        return jsonify({'success': False, 'error': '商品不存在'}), 404

    # 收集要上传的文件
    files = []
    import json as _json
    images = _json.loads(product['images']) if product['images'] else []

    # 本地打包目录
    pkg_dir = os.path.join(PRODUCTS_DIR, 'packages', str(product_id))
    os.makedirs(pkg_dir, exist_ok=True)

    # 生成资料说明文件
    desc_file = os.path.join(pkg_dir, '资料说明.txt')
    with open(desc_file, 'w', encoding='utf-8') as f:
        f.write(f"=== {product['title']} ===\n\n")
        f.write(product['description'])
        f.write(f"\n\n闲鱼商品ID：{product['goofish_item_id']}\n")
        f.write(f"售价：{product['price']}元\n")
    files.append(desc_file)

    # 添加封面图
    for img in images:
        if os.path.exists(img):
            files.append(img)

    # 执行打包
    product_data = {
        'title': product['title'],
        'files': files,
        'extraction_code': data.get('extraction_code', 'math'),
    }

    result = asyncio.run(package_product_to_baidu(product_data))

    if result['success']:
        # 生成发货内容并更新商品
        delivery = package_and_get_link_sync(product_data, result)
        update_product(product_id, delivery_content=delivery)
        add_log('百度网盘打包', f'商品 "{product["title"]}" 已打包，链接: {result["link"]}', 'success')

        return jsonify({
            'success': True,
            'data': {
                'link': result['link'],
                'code': result['code'],
                'delivery_content': delivery,
            },
            'message': '百度网盘打包完成'
        })
    else:
        return jsonify({'success': False, 'error': '打包失败'}), 500


def package_and_get_link_sync(product_data, result):
    """同步版本的发货内容生成"""
    title = product_data.get('title', '学习资料')
    return f"""感谢购买！

资料名称：【{title}】

百度网盘：{result['link']}
提取码：{result['code']}

确认收货后凭截图可领取额外福利资料一份！
有任何问题随时联系~"""


@app.route('/api/goofish/send-message', methods=['POST'])
def api_send_message():
    """发送IM消息"""
    data = request.json
    item_id = data.get('item_id', '')
    peer_user_id = data.get('peer_user_id', '')
    message = data.get('message', '')

    if not all([item_id, peer_user_id, message]):
        return jsonify({'success': False, 'error': '缺少必要参数'}), 400

    success = asyncio.run(send_im_message(item_id, peer_user_id, message))
    return jsonify({'success': success})


# ==================== 监控 API ====================

def record_new_orders(incoming):
    """Record new orders and synchronize status changes for existing orders."""
    import sqlite3 as _sqlite3
    new_count = 0
    products = get_all_products()
    product_map = {p['goofish_item_id']: p for p in products if p['goofish_item_id']}
    existing_orders = {
        order['goofish_order_id']: order
        for order in get_orders()
        if order['goofish_order_id']
    }

    for order in incoming:
        order_id = order.get('orderId', '')
        if not order_id:
            continue

        status = order.get('status', 'paid')
        existing_order = existing_orders.get(order_id)
        if existing_order:
            updates = {}
            if status != 'unknown' and status != existing_order['status']:
                updates['status'] = status
            if status in {'shipped', 'completed'} and not existing_order['delivery_sent']:
                mark_order_delivery_sent(existing_order['id'], order_status=status)
                add_log('订单状态同步', f'订单{order_id} 状态更新为 {status}', 'success')
                continue
            if updates:
                update_order(existing_order['id'], **updates)
                add_log('订单状态同步', f'订单{order_id} 状态更新为 {status}', 'success')
            continue

        item_id = order.get('itemId', '')
        product = product_map.get(item_id)
        product_id = product['id'] if product else None

        try:
            new_order_id = add_order(
                product_id=product_id,
                goofish_order_id=order_id,
                buyer_name=order.get('buyerName', ''),
                buyer_user_id=order.get('buyerId', ''),
                item_id=item_id,
                amount=order.get('price', 0),
                status=status,
                raw_data=order,
            )
        except _sqlite3.IntegrityError:
            add_log('订单同步', f'重复订单已忽略: {order_id}', 'warning')
            continue

        if status in {'shipped', 'completed'}:
            mark_order_delivery_sent(new_order_id, order_status=status)
        new_count += 1
        add_log(
            '新订单',
            f'订单{order_id} 买家:{order.get("buyerName", "未知")} ¥{order.get("price", 0)}',
        )

    return new_count


@app.route('/api/monitor/start', methods=['POST'])
def api_monitor_start():
    """启动订单监控"""
    global _monitor_thread, _monitor_running
    if _monitor_running:
        return jsonify({'success': False, 'message': '监控已在运行中'})

    recovered = recover_stale_deliveries()
    if recovered:
        add_log('监控启动', f'已恢复 {recovered} 个中断的发送任务', 'info')

    _monitor_running = True
    _monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
    _monitor_thread.start()
    add_log('监控启动', f'订单监控已启动，间隔 {MONITOR_INTERVAL_SECONDS} 秒')
    return jsonify({'success': True, 'message': '订单监控已启动'})


@app.route('/api/monitor/stop', methods=['POST'])
def api_monitor_stop():
    """停止订单监控"""
    global _monitor_running
    _monitor_running = False
    add_log('监控停止', '订单监控已停止')
    return jsonify({'success': True, 'message': '订单监控已停止'})


@app.route('/api/monitor/status', methods=['GET'])
def api_monitor_status():
    """获取监控状态"""
    global _monitor_running
    return jsonify({
        'success': True,
        'running': _monitor_running,
        'interval': MONITOR_INTERVAL_SECONDS,
        'auto_delivery': AUTO_DELIVERY_ENABLED,
    })


@app.route('/api/monitor/report-orders', methods=['POST'])
def api_report_orders():
    """
    外部（MCP Playwright）上报检测到的订单
    POST body: { orders: [{orderId, itemId, title, price, buyerName, buyerId, status}] }
    """
    data = request.json
    incoming = data.get('orders', [])
    if not incoming:
        return jsonify({'success': False, 'error': '无订单数据'})

    new_count = record_new_orders(incoming)
    delivered_count = 0

    # 处理待发货订单
    if AUTO_DELIVERY_ENABLED:
        pending = get_pending_delivery_orders()
        delivered_count = len(pending)

    return jsonify({
        'success': True,
        'new_orders': new_count,
        'pending_delivery': delivered_count,
        'message': f'新增 {new_count} 个订单，{delivered_count} 个待发货'
    })


@app.route('/api/monitor/pending-deliveries', methods=['GET'])
def api_pending_deliveries():
    """
    获取所有待发货订单（供MCP Playwright批量处理）
    返回每个待发货订单的完整信息包括发货内容
    """
    pending = get_pending_delivery_orders()
    result = []
    for order in pending:
        product = get_product(order['product_id']) if order['product_id'] else None
        result.append({
            'order_id': order['id'],
            'goofish_order_id': order['goofish_order_id'],
            'buyer_user_id': order['buyer_user_id'],
            'buyer_name': order['buyer_name'],
            'item_id': order['item_id'],
            'amount': order['amount'],
            'delivery_content': product['delivery_content'] if product else '',
            'product_title': product['title'] if product else '',
        })
    return jsonify({'success': True, 'data': result, 'count': len(result)})


@app.route('/api/monitor/mark-delivered', methods=['POST'])
def api_mark_delivered():
    """
    标记订单为已发货（MCP Playwright发送IM成功后调用）
    POST body: { order_id: int }
    """
    data = request.json
    order_id = data.get('order_id')
    if not order_id:
        return jsonify({'success': False, 'error': '缺少order_id'})

    order = get_order(order_id)
    if not order:
        return jsonify({'success': False, 'error': '订单不存在'}), 404

    product = get_product(order['product_id'])
    delivery_content = product['delivery_content'] if product else ''

    mark_order_delivery_sent(order_id, delivery_content)
    add_log('发货完成', f'订单#{order_id} 已发货', 'success')
    return jsonify({'success': True, 'message': '已标记为已发货'})


@app.route('/api/monitor/check-im', methods=['POST'])
def api_check_im():
    """
    检查IM中的付款消息并自动发货
    返回检测到的付款会话
    """
    from goofish_bot import get_browser
    from monitor_mcp import check_im_for_payment

    # 构建商品映射：商品关键词 → 发货内容
    products = get_all_products(status='listed')
    product_map = {}
    for p in products:
        # 用商品标题中的关键词匹配
        keywords = p['title'][:10]  # 前10个字符作为匹配关键词
        product_map[keywords] = p['delivery_content']

    async def check_current_page():
        _, _, page = await get_browser()
        return await check_im_for_payment(page)

    result = asyncio.run(check_current_page())

    return jsonify({
        'success': True,
        'conversations': result,
        'product_map_keys': list(product_map.keys()),
        'message': f'检测到 {len(result)} 个付款会话'
    })


# ==================== 日志 API ====================

@app.route('/api/logs', methods=['GET'])
def api_get_logs():
    """获取操作日志"""
    limit = request.args.get('limit', 50, type=int)
    logs = get_logs(limit)
    result = [{
        'id': l['id'], 'action': l['action'], 'detail': l['detail'],
        'level': l['level'], 'created_at': l['created_at']
    } for l in logs]
    return jsonify({'success': True, 'data': result})


# ==================== 系统 API ====================

@app.route('/api/system/info', methods=['GET'])
def api_system_info():
    """获取系统信息"""
    products_count = len(get_all_products())
    orders_count = len(get_orders())
    pending_count = len(get_pending_delivery_orders())
    return jsonify({
        'success': True,
        'data': {
            'products_count': products_count,
            'orders_count': orders_count,
            'pending_delivery': pending_count,
            'monitor_running': _monitor_running,
            'auto_delivery': AUTO_DELIVERY_ENABLED,
        }
    })


# ==================== 监控循环 ====================

def monitor_loop():
    """订单监控后台循环。"""
    global _monitor_running
    add_log('监控循环', '订单监控线程已启动')

    while _monitor_running:
        try:
            orders = asyncio.run(fetch_sold_orders())
            new_count = record_new_orders(orders)
            if new_count:
                add_log('订单同步', f'本轮新增 {new_count} 个订单', 'success')

            pending = get_pending_delivery_orders()
            if pending:
                add_log('待发货提醒', f'当前有 {len(pending)} 个订单等待发货，请通过MCP执行发货')

        except Exception as e:
            add_log('监控异常', str(e), 'error')

        for _ in range(MONITOR_INTERVAL_SECONDS):
            if not _monitor_running:
                break
            time.sleep(1)

    add_log('监控循环', '订单监控线程已退出')


# ==================== 启动 ====================

def main():
    """启动Flask服务"""
    print("=" * 50)
    print("  闲鱼自动上架 + 自动发货系统")
    print("  Goofish Auto Seller v1.0")
    print("=" * 50)
    print(f"\n  管理后台: http://{FLASK_HOST}:{FLASK_PORT}/")
    print(f"  API 地址: http://{FLASK_HOST}:{FLASK_PORT}/api/")
    print()

    recovered = recover_stale_deliveries()
    if recovered:
        add_log('系统启动', f'已恢复 {recovered} 个中断的发送任务', 'info')

    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG)


if __name__ == '__main__':
    main()
