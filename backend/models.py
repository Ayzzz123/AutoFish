# 数据库模型
import sqlite3
import os
from datetime import datetime, timedelta
from contextlib import contextmanager
from config import DATABASE_PATH, PRODUCTS_DIR


def init_db():
    """初始化数据库，创建表结构"""
    os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
    os.makedirs(PRODUCTS_DIR, exist_ok=True)

    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    # 商品表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,              -- 商品标题
            description TEXT NOT NULL,         -- 商品描述
            price REAL NOT NULL,               -- 售价
            original_price REAL DEFAULT 0,     -- 原价
            category TEXT DEFAULT '',          -- 分类
            images TEXT DEFAULT '[]',          -- 图片路径列表(JSON)
            delivery_content TEXT NOT NULL,    -- 发货内容（网盘链接/文本）
            goofish_item_id TEXT DEFAULT '',   -- 闲鱼商品ID（发布后回填）
            goofish_url TEXT DEFAULT '',       -- 闲鱼商品链接（发布后回填）
            status TEXT DEFAULT 'draft',       -- draft/listed/sold_out/removed
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 订单表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER,                -- 关联商品ID
            goofish_order_id TEXT DEFAULT '',  -- 闲鱼订单号
            buyer_name TEXT DEFAULT '',        -- 买家昵称
            buyer_user_id TEXT DEFAULT '',     -- 买家用户ID
            item_id TEXT DEFAULT '',           -- 闲鱼商品ID
            amount REAL DEFAULT 0,             -- 成交金额
            status TEXT DEFAULT 'pending',     -- pending/paid/shipped/completed/refund/closed
            delivery_sent INTEGER DEFAULT 0,   -- 是否已发货 (0/1)
            delivery_content TEXT DEFAULT '',  -- 实际发送的内容
            sent_at TIMESTAMP,                 -- 发货时间
            remark TEXT DEFAULT '',            -- 备注
            raw_data TEXT DEFAULT '{}',        -- 原始数据(JSON)
            delivery_status TEXT DEFAULT 'pending',
            delivery_attempts INTEGER DEFAULT 0,
            delivery_error TEXT DEFAULT '',
            delivery_started_at TIMESTAMP,
            last_delivery_attempt_at TIMESTAMP,
            detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 操作日志表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,              -- 操作类型
            detail TEXT DEFAULT '',            -- 详细信息
            level TEXT DEFAULT 'info',         -- info/warning/error/success
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 系统设置表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT DEFAULT ''
        )
    """)

    order_columns = {row[1] for row in cursor.execute("PRAGMA table_info(orders)")}
    order_migrations = (
        ('delivery_status', "ALTER TABLE orders ADD COLUMN delivery_status TEXT DEFAULT 'pending'"),
        ('delivery_attempts', "ALTER TABLE orders ADD COLUMN delivery_attempts INTEGER DEFAULT 0"),
        ('delivery_error', "ALTER TABLE orders ADD COLUMN delivery_error TEXT DEFAULT ''"),
        ('delivery_started_at', "ALTER TABLE orders ADD COLUMN delivery_started_at TIMESTAMP"),
        ('last_delivery_attempt_at', "ALTER TABLE orders ADD COLUMN last_delivery_attempt_at TIMESTAMP"),
    )
    for column, statement in order_migrations:
        if column not in order_columns:
            cursor.execute(statement)

    cursor.execute("""
        UPDATE orders
        SET delivery_status = 'sent'
        WHERE delivery_sent = 1
          AND (delivery_status IS NULL OR delivery_status <> 'sent')
    """)
    cursor.execute("""
        UPDATE orders
        SET delivery_status = 'pending'
        WHERE delivery_sent = 0
          AND (delivery_status IS NULL OR delivery_status = '')
    """)

    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_goofish_order_id_unique
        ON orders(goofish_order_id)
        WHERE goofish_order_id <> ''
    """)

    conn.commit()
    conn.close()


@contextmanager
def get_db():
    """获取数据库连接上下文管理器"""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ========== 商品操作 ==========

def add_product(title, description, price, delivery_content, original_price=0, images=None, category=''):
    """添加商品"""
    import json
    with get_db() as db:
        cursor = db.execute(
            """INSERT INTO products (title, description, price, original_price, delivery_content, images, category)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (title, description, price, original_price, delivery_content,
             json.dumps(images or [], ensure_ascii=False), category)
        )
        return cursor.lastrowid


def update_product(product_id, **kwargs):
    """更新商品"""
    if not kwargs:
        return
    import json
    if 'images' in kwargs and isinstance(kwargs['images'], list):
        kwargs['images'] = json.dumps(kwargs['images'], ensure_ascii=False)

    kwargs['updated_at'] = datetime.now().isoformat()
    fields = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [product_id]
    with get_db() as db:
        db.execute(f"UPDATE products SET {fields} WHERE id = ?", values)


def get_product(product_id):
    """获取单个商品"""
    with get_db() as db:
        return db.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()


def get_all_products(status=None):
    """获取所有商品"""
    with get_db() as db:
        if status:
            return db.execute("SELECT * FROM products WHERE status = ? ORDER BY created_at DESC", (status,)).fetchall()
        return db.execute("SELECT * FROM products ORDER BY created_at DESC").fetchall()


def delete_product(product_id):
    """删除商品"""
    with get_db() as db:
        db.execute("DELETE FROM products WHERE id = ?", (product_id,))


# ========== 订单操作 ==========

def add_order(product_id, goofish_order_id, buyer_name, buyer_user_id, item_id, amount, status='pending', raw_data='{}'):
    """添加新订单"""
    import json
    if isinstance(raw_data, dict):
        raw_data = json.dumps(raw_data, ensure_ascii=False)
    with get_db() as db:
        cursor = db.execute(
            """INSERT INTO orders (product_id, goofish_order_id, buyer_name, buyer_user_id, item_id, amount, status, raw_data)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (product_id, goofish_order_id, buyer_name, buyer_user_id, item_id, amount, status, raw_data)
        )
        return cursor.lastrowid


def update_order(order_id, **kwargs):
    """更新订单"""
    if not kwargs:
        return
    kwargs['updated_at'] = datetime.now().isoformat()
    fields = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [order_id]
    with get_db() as db:
        db.execute(f"UPDATE orders SET {fields} WHERE id = ?", values)


def get_order(order_id):
    """获取单个订单"""
    with get_db() as db:
        return db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()


def get_orders(status=None):
    """获取所有订单"""
    with get_db() as db:
        if status:
            return db.execute("SELECT * FROM orders WHERE status = ? ORDER BY detected_at DESC", (status,)).fetchall()
        return db.execute("SELECT * FROM orders ORDER BY detected_at DESC").fetchall()


def get_pending_delivery_orders():
    """获取待发货订单"""
    with get_db() as db:
        return db.execute(
            """SELECT * FROM orders
               WHERE status = 'paid'
                 AND delivery_sent = 0
                 AND delivery_status IN ('pending', 'failed')
               ORDER BY detected_at ASC"""
        ).fetchall()


def claim_order_for_delivery(order_id):
    now = datetime.now().isoformat()
    with get_db() as db:
        cursor = db.execute(
            """UPDATE orders
               SET delivery_status = 'sending',
                   delivery_attempts = delivery_attempts + 1,
                   delivery_error = '',
                   delivery_started_at = ?,
                   last_delivery_attempt_at = ?,
                   updated_at = ?
               WHERE id = ?
                 AND status = 'paid'
                 AND delivery_sent = 0
                 AND delivery_status IN ('pending', 'failed')""",
            (now, now, now, order_id),
        )
        if cursor.rowcount != 1:
            return None
        return db.execute(
            "SELECT delivery_attempts FROM orders WHERE id = ?",
            (order_id,),
        ).fetchone()['delivery_attempts']


def finish_delivery(order_id, attempt_token, delivery_status, error='', delivery_content=''):
    now = datetime.now().isoformat()
    with get_db() as db:
        if delivery_status == 'sent':
            cursor = db.execute(
                """UPDATE orders
                   SET delivery_status = 'sent',
                       delivery_sent = 1,
                       delivery_content = ?,
                       delivery_error = '',
                       delivery_started_at = NULL,
                       status = 'shipped',
                       sent_at = ?,
                       updated_at = ?
                   WHERE id = ?
                     AND delivery_status = 'sending'
                     AND delivery_attempts = ?""",
                (delivery_content, now, now, order_id, attempt_token),
            )
        else:
            cursor = db.execute(
                """UPDATE orders
                   SET delivery_status = ?,
                       delivery_error = ?,
                       delivery_started_at = NULL,
                       updated_at = ?
                   WHERE id = ?
                     AND delivery_status = 'sending'
                     AND delivery_attempts = ?""",
                (delivery_status, error, now, order_id, attempt_token),
            )
        return cursor.rowcount == 1


def mark_order_delivery_sent(order_id, delivery_content='', order_status=None):
    now = datetime.now().isoformat()
    status = order_status or 'shipped'
    with get_db() as db:
        cursor = db.execute(
            """UPDATE orders
               SET delivery_status = 'sent',
                   delivery_sent = 1,
                   delivery_content = ?,
                   delivery_error = '',
                   delivery_started_at = NULL,
                   status = ?,
                   sent_at = ?,
                   updated_at = ?
               WHERE id = ?""",
            (delivery_content, status, now, now, order_id),
        )
        return cursor.rowcount == 1


def recover_stale_deliveries(now=None, stale_minutes=5):
    now = now or datetime.now()
    cutoff = now - timedelta(minutes=stale_minutes)
    with get_db() as db:
        cursor = db.execute(
            """UPDATE orders
               SET delivery_status = 'failed',
                   delivery_error = '发送任务中断',
                   delivery_started_at = NULL,
                   updated_at = ?
               WHERE delivery_status = 'sending'
                 AND delivery_started_at < ?""",
            (now.isoformat(), cutoff.isoformat()),
        )
        return cursor.rowcount


def order_exists(goofish_order_id):
    """检查订单是否已存在"""
    with get_db() as db:
        result = db.execute("SELECT id FROM orders WHERE goofish_order_id = ?", (goofish_order_id,)).fetchone()
        return result is not None


# ========== 日志操作 ==========

def add_log(action, detail='', level='info'):
    """添加日志"""
    with get_db() as db:
        db.execute("INSERT INTO logs (action, detail, level) VALUES (?, ?, ?)", (action, detail, level))


def get_logs(limit=100):
    """获取最近日志"""
    with get_db() as db:
        return db.execute("SELECT * FROM logs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()


# ========== 设置操作 ==========

def get_setting(key, default=''):
    """获取设置"""
    with get_db() as db:
        row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row['value'] if row else default


def set_setting(key, value):
    """设置配置"""
    with get_db() as db:
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))


# 初始化
init_db()
