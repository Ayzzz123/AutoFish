# 百度网盘自动化打包模块
# 基于实操验证的DOM交互方式

import asyncio
import os
import re
import time

BAIDU_PAN_URL = "https://pan.baidu.com/disk/main?from=homeFlow#/index?category=all"
XIANYU_FOLDER = "闲鱼"


async def get_baidu_page(context=None):
    """获取百度网盘页面，在现有context中打开"""
    from goofish_bot import get_browser
    _, _, page = await get_browser()
    await page.goto(BAIDU_PAN_URL, wait_until='domcontentloaded', timeout=30000)
    await asyncio.sleep(2)
    return page


async def ensure_xianyu_folder(page) -> bool:
    """确保 闲鱼 文件夹存在，不存在则创建"""
    # 刷新到根目录
    await page.goto(BAIDU_PAN_URL, wait_until='domcontentloaded', timeout=30000)
    await asyncio.sleep(2)

    has_folder = await page.evaluate("""
        () => {
            const spans = document.querySelectorAll('span');
            for (const s of spans) {
                if (s.textContent.trim() === '闲鱼') return true;
            }
            return false;
        }
    """)

    if has_folder:
        print("[百度网盘] 闲鱼文件夹已存在")
        return True

    # 创建闲鱼文件夹
    await page.evaluate("""
        async () => {
            const btns = document.querySelectorAll('button');
            for (const btn of btns) {
                if (btn.textContent.includes('新建文件夹') && btn.offsetParent) {
                    btn.click();
                    await new Promise(r => setTimeout(r, 1000));
                    break;
                }
            }
        }
    """)
    await asyncio.sleep(1)
    await page.keyboard.type('闲鱼', delay=80)
    await asyncio.sleep(0.3)
    await page.keyboard.press('Tab')
    await asyncio.sleep(2)

    print("[百度网盘] 已创建闲鱼文件夹")
    return True


async def enter_xianyu_folder(page) -> bool:
    """进入闲鱼文件夹"""
    await page.goto(BAIDU_PAN_URL, wait_until='domcontentloaded', timeout=30000)
    await asyncio.sleep(2)

    result = await page.evaluate("""
        async () => {
            const spans = document.querySelectorAll('span');
            for (const span of spans) {
                if (span.textContent.trim() === '闲鱼' && span.offsetParent) {
                    const row = span.closest('tr, [class*="row"]');
                    if (row) {
                        row.scrollIntoView({ block: 'center' });
                        await new Promise(r => setTimeout(r, 300));
                        row.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
                        await new Promise(r => setTimeout(r, 2000));
                        return true;
                    }
                }
            }
            return false;
        }
    """)

    return result


async def create_product_folder(page, folder_name: str) -> bool:
    """在当前位置创建商品文件夹"""
    # Click "新建文件夹"
    await page.evaluate("""
        () => {
            const btns = document.querySelectorAll('button');
            for (const btn of btns) {
                if (btn.textContent.includes('新建文件夹') && btn.offsetParent) {
                    btn.click();
                    return;
                }
            }
        }
    """)
    await asyncio.sleep(1.2)

    # Type name and confirm with Tab
    await page.keyboard.type(folder_name, delay=60)
    await asyncio.sleep(0.3)
    await page.keyboard.press('Tab')
    await asyncio.sleep(2)

    # Verify
    exists = await page.evaluate(f"""
        () => document.body.innerText.includes('{folder_name}')
    """)
    print(f"[百度网盘] 文件夹 '{folder_name}' {'创建成功' if exists else '创建可能失败'}")
    return exists


async def upload_files_to_current_folder(page, file_paths: list) -> bool:
    """上传文件到当前文件夹"""
    if not file_paths:
        return True

    for filepath in file_paths:
        if not os.path.exists(filepath):
            print(f"[百度网盘] 文件不存在: {filepath}")
            continue

    # Trigger file input
    await page.evaluate("""
        () => {
            const fileInput = document.querySelector('input[type="file"][multiple]');
            if (fileInput) {
                fileInput.style.position = 'static';
                fileInput.style.opacity = '1';
                fileInput.style.width = '1px';
                fileInput.style.height = '1px';
                fileInput.click();
            }
        }
    """)
    await asyncio.sleep(0.5)

    # Upload via file chooser
    try:
        file_input = await page.query_selector('input[type="file"]')
        if file_input:
            await file_input.set_input_files(file_paths)
            await asyncio.sleep(3)
            print(f"[百度网盘] 已上传 {len(file_paths)} 个文件")
            return True
    except Exception as e:
        print(f"[百度网盘] 上传失败: {e}")

    return False


async def select_files_in_current(page, keywords: list) -> bool:
    """选中包含关键词的文件"""
    await page.evaluate("""
        (keywords) => {
            const checkboxes = document.querySelectorAll('input[type="checkbox"]');
            for (const cb of checkboxes) {
                const row = cb.closest('tr');
                if (row) {
                    const text = row.innerText;
                    for (const kw of keywords) {
                        if (text.includes(kw)) {
                            if (!cb.checked) cb.click();
                            break;
                        }
                    }
                }
            }
        }
    """, keywords)
    await asyncio.sleep(0.5)
    return True


async def create_share_link(page, extraction_code: str = "math") -> dict:
    """
    为当前选中的文件创建分享链接
    返回: { link, code, success }
    """
    # Click "分享" button
    await page.evaluate("""
        () => {
            const btns = document.querySelectorAll('button');
            for (const btn of btns) {
                if (btn.textContent.trim() === '分享' && btn.offsetParent) {
                    btn.click();
                    return;
                }
            }
        }
    """)
    await asyncio.sleep(2)

    # Select "永久有效"
    await page.evaluate("""
        () => {
            const labels = document.querySelectorAll('*');
            for (const el of labels) {
                if (el.textContent?.trim() === '永久有效' && el.offsetParent) {
                    el.click();
                    return;
                }
            }
        }
    """)
    await asyncio.sleep(0.5)

    # Select "自定义" extraction code
    await page.evaluate("""
        () => {
            const labels = document.querySelectorAll('*');
            for (const el of labels) {
                if (el.textContent?.trim() === '自定义' && el.offsetParent) {
                    el.click();
                    return;
                }
            }
        }
    """)
    await asyncio.sleep(0.5)

    # Fill extraction code
    await page.evaluate(f"""
        () => {{
            const inputs = document.querySelectorAll('input');
            for (const inp of inputs) {{
                if (inp.placeholder && inp.placeholder.includes('字母') && inp.offsetParent) {{
                    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                    setter.call(inp, '{extraction_code}');
                    inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    return;
                }}
            }}
        }}
    """)
    await asyncio.sleep(0.5)

    # Check "分享链接自动填充提取码"
    await page.evaluate("""
        () => {
            const labels = document.querySelectorAll('*');
            for (const el of labels) {
                if (el.textContent?.includes('分享链接自动填充提取码') && el.offsetParent) {
                    el.click();
                    return;
                }
            }
        }
    """)
    await asyncio.sleep(0.3)

    # Click "复制链接" button
    await page.evaluate("""
        () => {
            const btns = document.querySelectorAll('button');
            for (const btn of btns) {
                if (btn.innerText?.trim() === '复制链接' && btn.offsetParent) {
                    btn.click();
                    return;
                }
            }
        }
    """)
    await asyncio.sleep(2)

    # Extract the share link
    share_link = await page.evaluate("""
        () => {
            const body = document.body.innerText;
            const match = body.match(/https?:\\/\\/pan\\.baidu\\.com\\/s\\/[a-zA-Z0-9_-]+/);
            return match ? match[1] : null;
        }
    """)

    if share_link:
        print(f"[百度网盘] 分享链接: {share_link}")
        print(f"[百度网盘] 提取码: {extraction_code}")
        return {"link": share_link, "code": extraction_code, "success": True}
    else:
        print("[百度网盘] 获取分享链接失败")
        return {"link": None, "code": extraction_code, "success": False}


async def package_product_to_baidu(product_data: dict) -> dict:
    """
    一键打包商品到百度网盘

    product_data = {
        'title': str,           # 商品标题（作为文件夹名）
        'files': [str],         # 要上传的文件路径列表
        'extraction_code': str, # 提取码，默认 'math'
    }

    返回: { link, code, success }
    """
    title = product_data.get('title', '商品资料')
    file_paths = product_data.get('files', [])
    extraction_code = product_data.get('extraction_code', 'math')

    print(f"\n[百度网盘] ===== 开始打包: {title} =====")

    # 安全截取文件夹名（网盘限制）
    folder_name = title[:50].replace('/', '_').replace('\\', '_')

    # 1. 获取页面
    from goofish_bot import get_browser
    _, _, page = await get_browser()

    # 2. 确保闲鱼文件夹存在
    await ensure_xianyu_folder(page)

    # 3. 进入闲鱼文件夹
    await enter_xianyu_folder(page)
    await asyncio.sleep(1)

    # 4. 创建商品文件夹
    await create_product_folder(page, folder_name)

    # 5. 进入商品文件夹
    await page.evaluate(f"""
        async () => {{
            const spans = document.querySelectorAll('span');
            for (const span of spans) {{
                if (span.textContent.trim() === '{folder_name}' && span.offsetParent) {{
                    const row = span.closest('tr');
                    if (row) {{
                        row.scrollIntoView({{ block: 'center' }});
                        await new Promise(r => setTimeout(r, 300));
                        row.dispatchEvent(new MouseEvent('dblclick', {{ bubbles: true }}));
                        await new Promise(r => setTimeout(r, 2000));
                        return;
                    }}
                }}
            }}
        }}
    """)
    await asyncio.sleep(2)

    # 6. 上传文件
    if file_paths:
        await upload_files_to_current_folder(page, file_paths)
        await asyncio.sleep(2)

    # 7. 回到上级（闲鱼文件夹），选中商品文件夹
    # 简单方式：直接在当前位置分享上传的文件
    await page.evaluate("""
        async () => {
            // Click 返回上一级
            const links = document.querySelectorAll('*');
            for (const el of links) {
                if (el.textContent?.trim() === '返回上一级' && el.offsetParent) {
                    el.click();
                    await new Promise(r => setTimeout(r, 2000));
                    return;
                }
            }
        }
    """)
    await asyncio.sleep(1.5)

    # 8. 选中商品文件夹
    await page.evaluate(f"""
        () => {{
            const checkboxes = document.querySelectorAll('input[type="checkbox"]');
            for (const cb of checkboxes) {{
                const row = cb.closest('tr');
                if (row && row.innerText.includes('{folder_name}')) {{
                    if (!cb.checked) cb.click();
                    return;
                }}
            }}
        }}
    """)
    await asyncio.sleep(0.5)

    # 9. 创建分享链接
    result = await create_share_link(page, extraction_code)

    print(f"[百度网盘] ===== 打包完成: {title} =====\n")
    return result


async def package_and_get_link(product_data: dict) -> str:
    """
    打包商品并返回格式化的发货内容
    """
    result = await package_product_to_baidu(product_data)

    if not result['success']:
        return "打包失败，请手动创建百度网盘分享链接"

    title = product_data.get('title', '学习资料')
    link = result['link']
    code = result['code']

    return f"""感谢购买！

资料名称：【{title}】

百度网盘：{link}
提取码：{code}

确认收货后凭截图可领取额外福利资料一份！
有任何问题随时联系~"""
