# 闲鱼浏览器自动化模块 v2
# 基于实操验证过的 Playwright 交互方式

import asyncio
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright, Page, Browser, BrowserContext

from config import (
    GOOFISH_URL, PUBLISH_URL, BOUGHT_URL, IM_URL,
    BROWSER_HEADLESS, USER_DATA_DIR
)
from models import add_log

_browser: Browser = None
_context: BrowserContext = None
_page: Page = None
_browser_loop = None


async def get_browser():
    global _browser, _context, _page, _browser_loop
    current_loop = asyncio.get_running_loop()
    if _browser_loop is not None and _browser_loop is not current_loop:
        # 新事件循环：关闭旧浏览器避免泄露，再重建
        try:
            await _context.close() if _context else None
        except Exception:
            pass
        try:
            await _browser.close() if _browser else None
        except Exception:
            pass
        _browser = _context = _page = None
        _browser_loop = None

    if _browser is None or not _browser.is_connected():
        p = await async_playwright().start()
        _browser = await p.chromium.launch(
            headless=BROWSER_HEADLESS,
            channel='msedge',  # 使用系统安装的 Edge
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
            ]
        )
        os.makedirs(USER_DATA_DIR, exist_ok=True)
        state_file = os.path.join(USER_DATA_DIR, 'state.json')
        _context = await _browser.new_context(
            viewport={'width': 1280, 'height': 900},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            storage_state=state_file if os.path.exists(state_file) else None,
        )
        _page = await _context.new_page()
        _browser_loop = current_loop
    return _browser, _context, _page


async def save_storage_state():
    if _context:
        state_path = os.path.join(USER_DATA_DIR, 'state.json')
        await _context.storage_state(path=state_path)


async def close_browser():
    global _browser, _context, _page, _browser_loop
    if _context:
        await save_storage_state()
    if _browser:
        await _browser.close()
        _browser = _context = _page = None
        _browser_loop = None


async def _page_is_logged_in(page: Page) -> bool:
    login_entry = page.get_by_text('登录', exact=True).first
    if await login_entry.count() and await login_entry.is_visible():
        return False
    return bool(await page.query_selector('a[href*="personal"]'))


async def check_login_status() -> bool:
    try:
        _, _, page = await get_browser()
        await page.goto(GOOFISH_URL, wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(2)
        return await _page_is_logged_in(page)
    except:
        return False


async def open_login_and_wait(timeout_seconds: int = 180) -> dict:
    """Open the Goofish login UI and wait until the account is authenticated."""
    try:
        _, _, page = await get_browser()
        await page.goto(GOOFISH_URL, wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(2)

        if await _page_is_logged_in(page):
            await save_storage_state()
            return {'success': True, 'message': '闲鱼账号已连接'}

        login_entry = page.get_by_text('登录', exact=True).first
        if await login_entry.count() and await login_entry.is_visible():
            await login_entry.click()

        deadline = time.monotonic() + max(10, timeout_seconds)
        while time.monotonic() < deadline:
            await asyncio.sleep(2)
            if await _page_is_logged_in(page):
                await save_storage_state()
                add_log('闲鱼连接', '闲鱼账号登录成功', 'success')
                return {'success': True, 'message': '闲鱼账号已连接'}

        return {'success': False, 'error': '登录等待超时，请重新连接闲鱼'}
    except Exception as exc:
        add_log('闲鱼连接失败', str(exc), 'error')
        return {'success': False, 'error': f'无法打开闲鱼登录页：{exc}'}
    finally:
        try:
            await close_browser()
        except Exception:
            pass


async def _wait_for_unpublish_success(page: Page, timeout_seconds: float = 5) -> bool:
    deadline = time.monotonic() + timeout_seconds
    success_markers = ('下架成功', '已下架', '重新上架', '上架商品')
    while True:
        for marker in success_markers:
            locator = page.get_by_text(marker, exact=False)
            for index in range(await locator.count()):
                if await locator.nth(index).is_visible():
                    return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(0.2)


async def unpublish_product(item_id: str) -> dict:
    """Unpublish an owned Goofish item and only report success after UI confirmation."""
    if not item_id:
        return {'success': False, 'error': '商品缺少闲鱼商品 ID'}

    try:
        _, _, page = await get_browser()
        await page.goto(f'{GOOFISH_URL}/item?id={item_id}', wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(2)

        if not await _page_is_logged_in(page):
            return {'success': False, 'error': '请先连接并登录闲鱼账号'}

        action = None
        for label in ('下架商品', '下架'):
            locator = page.get_by_text(label, exact=True)
            for index in range(await locator.count()):
                candidate = locator.nth(index)
                if await candidate.is_visible():
                    action = candidate
                    break
            if action:
                break

        if not action:
            return {'success': False, 'error': '闲鱼页面中未找到下架入口，请确认该商品属于当前账号'}

        await action.click()
        await asyncio.sleep(1)
        for label in ('确认下架', '确定', '确认'):
            confirm = page.get_by_text(label, exact=True)
            for index in range(await confirm.count()):
                candidate = confirm.nth(index)
                if await candidate.is_visible():
                    await candidate.click()
                    break
            else:
                continue
            break

        if await _wait_for_unpublish_success(page):
            add_log('商品下架', f'闲鱼商品 {item_id} 已下架', 'success')
            return {'success': True, 'message': '闲鱼商品已下架'}

        return {'success': False, 'error': '闲鱼未返回下架成功确认，本地状态未修改'}
    except Exception as exc:
        add_log('商品下架失败', str(exc), 'error')
        return {'success': False, 'error': f'闲鱼商品下架失败：{exc}'}
    finally:
        try:
            await close_browser()
        except Exception:
            pass


# ============ 自动发货核心 ============

def normalize_order_status(text: str) -> str:
    """Map the order status text shown by Goofish to the local status."""
    if '交易成功' in text:
        return 'completed'
    if '待收货' in text or '已发货' in text:
        return 'shipped'
    if '待发货' in text or '已付款' in text:
        return 'paid'
    if '退款' in text:
        return 'refund'
    if '关闭' in text:
        return 'closed'
    if '待付款' in text:
        return 'pending'
    return 'unknown'


async def fetch_sold_orders() -> list:
    """
    获取"我卖出的"订单列表
    返回: [{'order_id': str, 'item_id': str, 'buyer_name': str, 'buyer_id': str,
            'title': str, 'price': float, 'status': str, 'im_link': str}, ...]
    """
    try:
        _, _, page = await get_browser()
        add_log('获取订单', '正在获取已卖出订单...')

        # 导航到订单页面
        await page.goto(BOUGHT_URL, wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(2)

        # 点击"我卖出的"tab
        sold_tab = None
        all_elements = await page.query_selector_all('*')
        for el in all_elements:
            text = await el.inner_text()
            if text.strip() == '我卖出的':
                sold_tab = el
                break
        if sold_tab:
            await sold_tab.click()
            await asyncio.sleep(2)

        # 提取订单数据
        orders = await page.evaluate("""
            () => {
                const orders = [];
                // 找所有订单链接
                const links = document.querySelectorAll('a[href*="order-detail"], a[href*="orderId"]');
                const seen = new Set();

                links.forEach(link => {
                    const href = link.getAttribute('href') || '';
                    const orderMatch = href.match(/orderId=(\\d+)/);
                    const orderId = orderMatch ? orderMatch[1] : '';
                    if (!orderId || seen.has(orderId)) return;
                    seen.add(orderId);

                    // 向上找订单容器
                    const container = link.closest('[class*="order"], [class*="Order"], li, div[class*="item"]') || link.parentElement;

                    // 提取信息
                    let title = '';
                    let price = 0;
                    let buyerName = '';
                    let buyerId = '';
                    let itemId = '';
                    let imLink = '';
                    let status = 'unknown';

                    if (container) {
                        const text = container.innerText || '';
                        // 提取价格
                        const priceMatch = text.match(/[¥￥]\\s*([\\d.]+)/);
                        if (priceMatch) price = parseFloat(priceMatch[1]);

                        // 提取状态
                        if (text.includes('交易成功')) status = 'completed';
                        else if (text.includes('待发货')) status = 'paid';
                        else if (text.includes('已发货')) status = 'shipped';
                        else if (text.includes('退款')) status = 'refund';
                        else if (text.includes('关闭')) status = 'closed';
                        else if (text.includes('待付款')) status = 'pending';

                        // 商品标题
                        const titleEl = container.querySelector('[class*="title"], [class*="name"], a[href*="item"]');
                        if (titleEl) title = (titleEl.innerText || '').trim().substring(0, 200);

                        // 买家链接
                        const userLink = container.querySelector('a[href*="userId"]');
                        if (userLink) {
                            buyerName = (userLink.innerText || '').trim();
                            const uidMatch = userLink.getAttribute('href').match(/userId=(\\d+)/);
                            if (uidMatch) buyerId = uidMatch[1];
                        }

                        // IM链接和itemId
                        const imA = container.querySelector('a[href*="/im?"]');
                        if (imA) {
                            imLink = imA.getAttribute('href') || '';
                            const itemMatch = imLink.match(/itemId=(\\d+)/);
                            if (itemMatch) itemId = itemMatch[1];
                        }
                    }

                    orders.push({
                        orderId, itemId, title, price, buyerName, buyerId,
                        imLink: imLink.startsWith('/') ? 'https://www.goofish.com' + imLink : imLink,
                        status, statusText: container ? (container.innerText || '') : ''
                    });
                });
                return orders;
            }
        """)

        for order in orders:
            status_text = order.pop('statusText', '')
            if status_text:
                order['status'] = normalize_order_status(status_text)

        add_log('订单获取结果', f'找到 {len(orders)} 个订单', 'success')
        return orders

    except Exception as e:
        add_log('获取订单失败', str(e), 'error')
        return []


async def send_im_message_result(item_id: str, peer_user_id: str, message: str) -> dict:
    """Send an IM message and report whether the send action might have started."""
    send_started = False
    try:
        _, _, page = await get_browser()

        im_url = f"{IM_URL}?itemId={item_id}&peerUserId={peer_user_id}"
        add_log('自动发货', f'打开IM: itemId={item_id}, userId={peer_user_id}')
        await page.goto(im_url, wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(3)

        msg_input = await page.query_selector('[contenteditable="true"]')
        if not msg_input:
            msg_input = await page.query_selector('textarea')
        if not msg_input:
            error = '\u672a\u627e\u5230\u95f2\u9c7c\u6d88\u606f\u8f93\u5165\u6846'
            add_log('发货失败', error, 'error')
            return {'outcome': 'not_sent', 'error': error}

        await msg_input.click()
        await asyncio.sleep(0.3)
        await msg_input.fill('')
        await msg_input.type(message, delay=50)
        await asyncio.sleep(1)

        send_btn = None
        buttons = await page.query_selector_all('button')
        for btn in buttons:
            if (await btn.inner_text()).strip() in ['发送', 'Send']:
                send_btn = btn
                break
        if not send_btn:
            send_btn = await page.query_selector('[class*="send"], [class*="Send"]')

        send_started = True
        if send_btn:
            await send_btn.click()
        else:
            await page.keyboard.press('Enter')

        await asyncio.sleep(1)
        add_log('自动发货成功', f'已发送百度网盘链接给 {peer_user_id}', 'success')
        return {'outcome': 'sent', 'error': ''}
    except Exception as error:
        error_message = str(error)
        add_log('发送消息异常', error_message, 'error')
        return {
            'outcome': 'unknown' if send_started else 'not_sent',
            'error': error_message,
        }


async def send_im_message(item_id: str, peer_user_id: str, message: str) -> bool:
    """Compatibility wrapper for callers that only need a boolean outcome."""
    result = await send_im_message_result(item_id, peer_user_id, message)
    return result['outcome'] == 'sent'


RETRY_DELAYS = (5, 30, 120)


async def auto_deliver_order(order_id) -> dict:
    """Deliver a paid order, retrying only failures known not to have sent."""
    from models import (
        claim_order_for_delivery,
        finish_delivery,
        get_order,
        get_product,
    )

    for attempt in range(len(RETRY_DELAYS) + 1):
        attempt_token = claim_order_for_delivery(order_id)
        if attempt_token is None:
            return {'status': 'conflict', 'error': ''}

        order = get_order(order_id)
        product = get_product(order['product_id']) if order else None
        delivery_content = product['delivery_content'] if product else ''
        buyer_user_id = order['buyer_user_id'] if order else ''
        item_id = order['item_id'] if order else ''

        if not product:
            result = {'outcome': 'not_sent', 'error': 'product not found'}
        elif not delivery_content:
            result = {'outcome': 'not_sent', 'error': 'delivery content is empty'}
        elif not buyer_user_id or not item_id:
            result = {'outcome': 'not_sent', 'error': 'buyer or item information is missing'}
        else:
            result = await send_im_message_result(item_id, buyer_user_id, delivery_content)

        if isinstance(result, dict):
            outcome = result.get('outcome')
            raw_error = result.get('error', '')
            error = raw_error if isinstance(raw_error, str) else str(raw_error)
        else:
            outcome = None
            error = ''

        if outcome == 'sent':
            delivery_status = 'sent'
        elif outcome == 'not_sent':
            delivery_status = 'failed'
        elif outcome == 'unknown':
            delivery_status = 'review'
            error = error or '\u53d1\u9001\u7ed3\u679c\u65e0\u6cd5\u786e\u8ba4'
        else:
            delivery_status = 'review'
            error = '\u53d1\u9001\u7ed3\u679c\u65e0\u6cd5\u786e\u8ba4'

        delivery_value = delivery_content if delivery_status == 'sent' else ''
        if not finish_delivery(
            order_id,
            attempt_token,
            delivery_status,
            error=error,
            delivery_content=delivery_value,
        ):
            current_order = get_order(order_id)
            if current_order:
                return {
                    'status': current_order['delivery_status'],
                    'error': current_order['delivery_error'],
                }
            return {'status': 'conflict', 'error': ''}

        if delivery_status != 'failed':
            return {'status': delivery_status, 'error': error}
        if attempt == len(RETRY_DELAYS):
            return {'status': 'failed', 'error': error}

        await asyncio.sleep(RETRY_DELAYS[attempt])


# ============ 图片下载 ============

async def download_reference_images(search_keyword: str, save_dir: str, count: int = 5) -> list:
    """
    从闲鱼搜索同类商品，下载它们的图片作为参考/使用
    """
    try:
        _, _, page = await get_browser()
        search_url = f"https://www.goofish.com/search?q={search_keyword}"
        await page.goto(search_url, wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(3)

        # 提取商品图片
        images = await page.evaluate("""
            () => {
                const imgs = document.querySelectorAll('img[src*="alicdn"]');
                const urls = new Set();
                imgs.forEach(img => {
                    const src = img.src || img.getAttribute('data-src') || '';
                    if (src && (src.includes('bao/uploaded') || src.includes('imgextra'))) {
                        // 取高清原图
                        urls.add(src.replace(/_\\d+x\\d+.*?\\./, '_Q90.'));
                    }
                });
                return [...urls].slice(0, 20);
            }
        """)

        os.makedirs(save_dir, exist_ok=True)
        downloaded = []

        import urllib.request
        for i, url in enumerate(images[:count]):
            try:
                ext = '.webp' if 'webp' in url else '.jpg'
                filename = f'ref_{i+1}{ext}'
                filepath = os.path.join(save_dir, filename)
                urllib.request.urlretrieve(url, filepath)
                downloaded.append(filepath)
                add_log('下载图片', f'已下载: {filename}')
            except Exception as e:
                add_log('图片下载失败', str(e), 'warning')

        return downloaded

    except Exception as e:
        add_log('搜索图片失败', str(e), 'error')
        return []


# ============ 发布商品 ============

async def publish_product_quick(product_data: dict) -> dict:
    """
    快速发布商品到闲鱼（使用已验证的 evaluate 方式填表）
    product_data: { title, description, price, original_price, image_path }
    """
    try:
        _, _, page = await get_browser()
        await page.goto(PUBLISH_URL, wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(2)

        # 填入所有信息
        result = await page.evaluate("""
            async (data) => {
                const r = {};

                // 1. 填描述
                const desc = document.querySelector('[contenteditable="true"]');
                if (desc) {
                    desc.focus();
                    desc.innerHTML = '';
                    document.execCommand('insertText', false, data.title + '\\n\\n' + data.description);
                    r.desc = 'ok';
                }

                // 2. 填价格
                const inputs = document.querySelectorAll('input[type="text"]');
                const vis = [];
                inputs.forEach(el => {
                    if (el.getBoundingClientRect().width > 50) vis.push(el);
                });
                if (vis.length >= 2) {
                    const s = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                    s.call(vis[0], String(data.price));
                    vis[0].dispatchEvent(new Event('input', {bubbles:true}));
                    vis[0].dispatchEvent(new Event('change', {bubbles:true}));
                    s.call(vis[1], String(data.original_price));
                    vis[1].dispatchEvent(new Event('input', {bubbles:true}));
                    vis[1].dispatchEvent(new Event('change', {bubbles:true}));
                    r.prices = [vis[0].value, vis[1].value];
                }

                // 3. 选无需邮寄
                const els = [...document.querySelectorAll('*')];
                const noShip = els.find(e => e.textContent?.trim() === '无需邮寄' && e.offsetParent);
                if (noShip) { noShip.click(); r.shipping = 'no'; }

                // 4. 触发文件选择器
                await new Promise(res => setTimeout(res, 300));
                const file = document.querySelector('input[type="file"]');
                if (file) {
                    file.style.position = 'static';
                    file.style.opacity = '1';
                    file.click();
                    r.fileTriggered = true;
                }
                return r;
            }
        """, {
            'title': product_data.get('title', ''),
            'description': product_data.get('description', ''),
            'price': product_data.get('price', 2.90),
            'original_price': product_data.get('original_price', 29.00),
        })

        # 上传图片
        image_path = product_data.get('image_path', '')
        if image_path and os.path.exists(image_path):
            file_input = await page.query_selector('input[type="file"]')
            if file_input:
                await file_input.set_input_files(image_path)
                await asyncio.sleep(3)
                result['image'] = 'uploaded'

        add_log('发布准备完成', '表单已填好，请手动点击发布按钮', 'success')
        return {'success': True, 'message': '商品信息已填好，请点击发布', 'url': page.url}

    except Exception as e:
        add_log('发布异常', str(e), 'error')
        return {'success': False, 'error': str(e)}


async def test_connection():
    try:
        _, _, page = await get_browser()
        await page.goto(GOOFISH_URL, wait_until='domcontentloaded', timeout=30000)
        title = await page.title()
        return {'success': True, 'title': title, 'url': page.url}
    except Exception as e:
        return {'success': False, 'error': str(e)}
