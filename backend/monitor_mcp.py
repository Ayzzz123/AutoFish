# 闲鱼订单监控 - 独立运行脚本
# 使用当前浏览器（MCP Playwright）检测订单并自动发货
# 通过调用Flask API来记录订单和触发发货

import asyncio
import json
import os
import re
import time
from datetime import datetime

# 这个模块设计为在已有浏览器page对象上操作
# 不需要自己启动浏览器
# 供MCP Playwright调用

# 付款关键词
PAYMENT_KEYWORDS = ['已付款', '已拍', '拍下了', '已下单', '已支付', '下单了', '付了', '已买']


async def check_im_for_payment(page) -> list:
    """
    检查闲鱼IM，看是否有买家发送了付款确认消息
    返回需要发货的会话列表：[{peerName, peerUserId, itemId, lastMsg}]
    """
    await page.goto('https://www.goofish.com/im', wait_until='domcontentloaded', timeout=30000)
    await asyncio.sleep(2)

    # 获取所有会话
    conversations = await page.evaluate(f"""
        () => {{
            const results = [];
            const items = document.querySelectorAll('[class*="conversation-item"], [class*="Conversation-item"]');
            items.forEach(item => {{
                const text = item.innerText || '';
                const hasPayment = {str(PAYMENT_KEYWORDS).lower()}.some(kw => text.toLowerCase().includes(kw));
                if (hasPayment) {{
                    // Extract item info
                    const nameMatch = text.match(/^(.+?)\\n/);
                    const peerName = nameMatch ? nameMatch[1] : '';
                    results.push({{
                        peerName: peerName,
                        lastMsg: text.substring(0, 200),
                        hasPayment: true
                    }});
                }}
            }});
            return results;
        }}
    """)

    return conversations


async def auto_deliver_to_buyer(page, product_delivery_content: str) -> bool:
    """
    在当前已打开的IM会话中自动发货
    需要先点击进入对应会话
    """
    # Find and click the conversation
    conversations = await page.query_selector_all('[class*="conversation-item"]')
    for conv in conversations:
        text = await conv.inner_text()
        has_payment = any(kw in text.lower() for kw in PAYMENT_KEYWORDS)
        if has_payment:
            await conv.click()
            await asyncio.sleep(2)

            # Find input and send
            input_el = await page.query_selector('textarea, [contenteditable="true"], [role="textbox"]')
            if input_el:
                await input_el.click()
                await input_el.fill(product_delivery_content)
                await asyncio.sleep(0.5)
                await page.keyboard.press('Enter')
                await asyncio.sleep(1)
                return True

    return False


async def monitor_and_deliver(page, product_map: dict) -> dict:
    """
    一键监控：检查IM → 发现付款消息 → 自动发货
    product_map: {product_keyword: delivery_content}
    返回处理结果
    """
    results = {'checked': False, 'delivered': [], 'errors': []}

    try:
        payment_convs = await check_im_for_payment(page)
        results['checked'] = True

        if payment_convs:
            for conv in payment_convs:
                # 匹配商品
                msg = conv.get('lastMsg', '')
                delivered = False

                for keyword, delivery_content in product_map.items():
                    if keyword in msg:
                        ok = await auto_deliver_to_buyer(page, delivery_content)
                        if ok:
                            results['delivered'].append({
                                'buyer': conv.get('peerName', ''),
                                'keyword': keyword
                            })
                            delivered = True
                            break

                # 如果没有匹配到特定商品，用默认发货内容
                if not delivered and product_map.get('default'):
                    ok = await auto_deliver_to_buyer(page, product_map['default'])
                    if ok:
                        results['delivered'].append({'buyer': conv.get('peerName', ''), 'keyword': 'default'})

    except Exception as e:
        results['errors'].append(str(e))

    return results


async def check_sold_orders_on_page(page) -> list:
    """
    在当前page上检查"我卖出的"订单
    page: MCP Playwright page对象
    返回订单列表
    """
    await page.goto('https://www.goofish.com/bought', wait_until='domcontentloaded', timeout=30000)
    await asyncio.sleep(2)

    # 点击"我卖出的"
    sold_tab = await page.query_selector('text=我卖出的')
    if not sold_tab:
        # 尝试查找
        all_els = await page.query_selector_all('*')
        for el in all_els:
            text = await el.inner_text()
            if text.strip() == '我卖出的':
                sold_tab = el
                break
    if sold_tab:
        await sold_tab.click()
        await asyncio.sleep(2)

    # 提取订单
    orders = await page.evaluate("""
        () => {
            const orders = [];
            const links = document.querySelectorAll('a[href*="order-detail"], a[href*="orderId"]');
            const seen = new Set();
            links.forEach(link => {
                const href = link.getAttribute('href') || '';
                const orderMatch = href.match(/orderId=(\\d+)/);
                const orderId = orderMatch ? orderMatch[1] : '';
                if (!orderId || seen.has(orderId)) return;
                seen.add(orderId);
                const container = link.closest('li, div[class*="item"], div[class*="order"], [class*="card"]') || link.parentElement;
                let title = '', price = 0, buyerName = '', buyerId = '', itemId = '', status = 'unknown';
                if (container) {
                    const text = container.innerText || '';
                    const pm = text.match(/[¥￥]\\s*([\\d.]+)/);
                    if (pm) price = parseFloat(pm[1]);
                    if (text.includes('交易成功')) status = 'completed';
                    else if (text.includes('待发货')) status = 'paid';
                    else if (text.includes('已发货')) status = 'shipped';
                    else if (text.includes('退款')) status = 'refund';
                    else if (text.includes('关闭')) status = 'closed';
                    const titleEl = container.querySelector('[class*="title"], [class*="name"], a[href*="item"]');
                    if (titleEl) title = (titleEl.innerText || '').trim().substring(0, 200);
                    const userLink = container.querySelector('a[href*="userId"]');
                    if (userLink) {
                        buyerName = (userLink.innerText || '').trim();
                        const um = userLink.getAttribute('href').match(/userId=(\\d+)/);
                        if (um) buyerId = um[1];
                    }
                    const imA = container.querySelector('a[href*="/im?"]');
                    if (imA) {
                        const imHref = imA.getAttribute('href') || '';
                        const imMatch = imHref.match(/itemId=(\\d+)/);
                        if (imMatch) itemId = imMatch[1];
                    }
                }
                orders.push({orderId, itemId, title, price, buyerName, buyerId, status});
            });
            return orders;
        }
    """)

    return orders


async def auto_deliver_on_page(page, item_id: str, peer_user_id: str, message: str) -> bool:
    """
    在当前page上自动发送IM消息（发货）
    """
    im_url = f'https://www.goofish.com/im?itemId={item_id}&peerUserId={peer_user_id}'
    await page.goto(im_url, wait_until='domcontentloaded', timeout=30000)
    await asyncio.sleep(3)

    # 找输入框
    msg_input = await page.query_selector('[contenteditable="true"]')
    if not msg_input:
        msg_input = await page.query_selector('textarea')

    if msg_input:
        await msg_input.click()
        await asyncio.sleep(0.3)
        await msg_input.fill('')
        await msg_input.type(message, delay=50)
        await asyncio.sleep(1)

        # 点发送
        btns = await page.query_selector_all('button')
        for btn in btns:
            text = (await btn.inner_text()).strip()
            if text in ['发送', 'Send']:
                await btn.click()
                await asyncio.sleep(1)
                return True

        await page.keyboard.press('Enter')
        await asyncio.sleep(1)
        return True

    return False


async def fetch_reference_images(page, keyword: str) -> list:
    """
    从闲鱼搜索同类商品，获取图片URL列表
    """
    await page.goto(f'https://www.goofish.com/search?q={keyword}', wait_until='domcontentloaded', timeout=30000)
    await asyncio.sleep(3)

    images = await page.evaluate("""
        () => {
            const imgs = document.querySelectorAll('img[src*="alicdn"]');
            const urls = new Set();
            imgs.forEach(img => {
                const src = img.src || img.getAttribute('data-src') || '';
                if (src && (src.includes('bao/uploaded') || src.includes('imgextra'))) {
                    urls.add(src.replace(/_\\d+x\\d+.*?\\./, '_Q90.'));
                }
            });
            return [...urls].slice(0, 10);
        }
    """)

    return images


async def fill_publish_form(page, title: str, description: str, price: float = 2.90, original_price: float = 29.00) -> dict:
    """
    填写闲鱼发布表单（使用已验证的 evaluate 方式）
    """
    await page.goto('https://www.goofish.com/publish', wait_until='domcontentloaded', timeout=30000)
    await asyncio.sleep(2)

    result = await page.evaluate("""
        async (data) => {
            const r = {};
            // 描述
            const desc = document.querySelector('[contenteditable="true"]');
            if (desc) {
                desc.focus(); desc.innerHTML = '';
                document.execCommand('insertText', false, data.title + '\\n\\n' + data.description);
                r.desc = 'ok';
            }
            // 价格
            const inputs = document.querySelectorAll('input[type="text"]');
            const vis = [];
            inputs.forEach(el => { if (el.getBoundingClientRect().width > 50) vis.push(el); });
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
            // 无需邮寄
            const els = [...document.querySelectorAll('*')];
            const noShip = els.find(e => e.textContent?.trim() === '无需邮寄' && e.offsetParent);
            if (noShip) { noShip.click(); r.shipping = 'no'; }
            // 触发文件选择器
            await new Promise(res => setTimeout(res, 300));
            const file = document.querySelector('input[type="file"]');
            if (file) {
                file.style.position = 'static'; file.style.opacity = '1';
                file.click(); r.fileTriggered = true;
            }
            return r;
        }
    """, {'title': title, 'description': description, 'price': price, 'original_price': original_price})

    return result
