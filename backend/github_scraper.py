# GitHub 学习资料采集模块
# 从 GitHub 搜索并获取虚拟学习资料

import asyncio
import json
import os
import re
from datetime import datetime

from playwright.async_api import async_playwright

from models import add_log, add_product, get_all_products

# GitHub 搜索关键词配置
DEFAULT_SEARCH_QUERIES = [
    "学习资料",
    "教程合集",
    "面试题",
    "考研资料",
    "英语学习",
    "编程教程",
    "电子书合集",
    "course materials",
    "study notes",
    "exam prep",
    "learning resources",
    "awesome list",
    "cheatsheet",
    "中文教程",
    "期末考试",
    "考研笔记",
    "公考资料",
    "CPA资料",
    "CFA notes",
    "雅思资料",
    "托福资料",
]

# GitHub 搜索排序方式
SORT_OPTIONS = ["stars", "updated", "forks"]


async def search_github_repos(query: str, sort: str = "stars", max_results: int = 20) -> list:
    """
    在 GitHub 搜索仓库
    返回: [{'name': str, 'url': str, 'stars': int, 'description': str, 'language': str, ...}, ...]
    """
    repos = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, channel='msedge')
            context = await browser.new_context(
                viewport={'width': 1280, 'height': 900},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )
            page = await context.new_page()

            search_url = f"https://github.com/search?q={query}&type=repositories&s={sort}&o=desc"
            await page.goto(search_url, wait_until='domcontentloaded', timeout=30000)
            await asyncio.sleep(2)

            # 提取搜索结果
            repo_elements = await page.query_selector_all('[data-testid="results-list"] > div')
            if not repo_elements:
                repo_elements = await page.query_selector_all('.repo-list-item, .Box-row')

            for i, el in enumerate(repo_elements[:max_results]):
                try:
                    # 获取仓库名称和链接
                    link_el = await el.query_selector('a[href*="/"]')
                    if not link_el:
                        continue

                    href = await link_el.get_attribute('href') or ''
                    name = (await link_el.inner_text()).strip()
                    full_url = f"https://github.com{href}" if href.startswith('/') else href

                    # 获取描述
                    desc_el = await el.query_selector('[class*="search-match"], p, .mb-1')
                    description = (await desc_el.inner_text()).strip() if desc_el else ''

                    # 获取星数和语言
                    stars = 0
                    language = ''
                    meta_text = await el.inner_text()
                    star_match = re.search(r'([\d,]+)\s*stars?', meta_text, re.IGNORECASE)
                    if star_match:
                        stars = int(star_match.group(1).replace(',', ''))

                    lang_match = re.search(r'(Python|JavaScript|TypeScript|Java|Go|Rust|C\+\+|HTML|CSS|Markdown|Jupyter Notebook|TeX|PDF)', meta_text)
                    if lang_match:
                        language = lang_match.group(1)

                    repos.append({
                        'name': name,
                        'full_name': href.strip('/') if href else name,
                        'url': full_url,
                        'stars': stars,
                        'description': description[:300],
                        'language': language,
                        'query': query,
                    })
                except Exception:
                    continue

            await browser.close()
            add_log('GitHub搜索', f'关键词 "{query}" 找到 {len(repos)} 个仓库', 'info')
            return repos

    except Exception as e:
        add_log('GitHub搜索失败', str(e), 'error')
        return []


async def get_repo_readme(repo_url: str) -> str:
    """获取仓库README内容"""
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, channel='msedge')
            context = await browser.new_context()
            page = await context.new_page()

            await page.goto(repo_url, wait_until='domcontentloaded', timeout=30000)
            await asyncio.sleep(2)

            # 查找 README 内容区域
            readme_el = await page.query_selector('article.markdown-body, [data-target="readme-toc.content"], .Box-body')
            if readme_el:
                content = await readme_el.inner_text()
            else:
                content = await page.inner_text('body')

            await browser.close()
            return content[:5000]  # 限制长度

    except Exception as e:
        return f"获取README失败: {e}"


async def bulk_search_github(queries: list = None) -> list:
    """批量搜索GitHub仓库"""
    if queries is None:
        queries = DEFAULT_SEARCH_QUERIES

    all_repos = []
    for query in queries[:10]:  # 限制搜索数量，避免被限制
        repos = await search_github_repos(query, sort="stars", max_results=5)
        all_repos.extend(repos)
        await asyncio.sleep(2)  # 避免请求过快

    add_log('批量搜索完成', f'共找到 {len(all_repos)} 个仓库', 'success')
    return all_repos


def create_product_from_repo(repo: dict, price: float = 9.90) -> int:
    """
    从 GitHub 仓库信息创建商品
    repo: 仓库信息字典
    price: 售价
    """
    title = repo.get('name', '未知资源')
    description = f"""【{repo.get('name', '')}】学习资料合集

{repo.get('description', '')}

📚 资源类型: {repo.get('language', '综合')}学习资料
⭐ GitHub星数: {repo.get('stars', 0)}

📦 内容包括：源码、文档、教程等学习资料
📎 来源: {repo.get('url', '')}

购买后自动发送网盘链接，请查看闲鱼聊天消息获取下载地址。"""

    # 网盘链接这里先用GitHub原链接占位，用户可以替换为网盘链接
    delivery_content = f"""🎉 感谢购买！您的学习资料已准备好：

📦 资料名称：{repo.get('name', '')}
📝 内容简介：{repo.get('description', '')}

🔗 GitHub链接：{repo.get('url', '')}

💡 如果链接无法访问，请联系我获取网盘备份链接。
❤️ 确认收货后可凭截图领取额外福利资料一份！

如有任何问题请随时联系，记得给个好评哦~ ⭐"""

    product_id = add_product(
        title=title,
        description=description,
        price=price,
        original_price=price * 10 if price > 0 else 99.00,
        delivery_content=delivery_content,
        category=repo.get('language', ''),
        images=[]
    )

    add_log('创建商品', f'商品 "{title}" ID={product_id}', 'success')
    return product_id


def auto_generate_products_from_github(repos: list, default_price: float = 9.90) -> list:
    """批量从GitHub仓库生成商品"""
    product_ids = []
    for repo in repos:
        pid = create_product_from_repo(repo, default_price)
        product_ids.append(pid)
    return product_ids


# 同步版本的搜索函数（用于在非async环境中调用）
def search_github_sync(query: str, sort: str = "stars", max_results: int = 20) -> list:
    """同步包装器"""
    return asyncio.run(search_github_repos(query, sort, max_results))


def bulk_search_sync(queries: list = None) -> list:
    """同步包装器"""
    if queries is None:
        queries = DEFAULT_SEARCH_QUERIES[:5]
    return asyncio.run(bulk_search_github(queries))
