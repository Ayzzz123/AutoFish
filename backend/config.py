# 闲鱼自动上架+自动发货系统 - 配置文件
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 数据库
DATABASE_PATH = os.path.join(BASE_DIR, "data", "goofish.db")

# 商品图片存储
PRODUCTS_DIR = os.path.join(BASE_DIR, "products")

# 闲鱼页面地址
GOOFISH_URL = "https://www.goofish.com"
PUBLISH_URL = "https://www.goofish.com/publish"
BOUGHT_URL = "https://www.goofish.com/bought"
IM_URL = "https://www.goofish.com/im"
LOGIN_URL = "https://www.goofish.com"

# 浏览器配置
BROWSER_HEADLESS = False  # 保持False可以看到浏览器操作，也避免headless被检测
USER_DATA_DIR = os.path.join(BASE_DIR, "data", "browser_profile")  # 浏览器用户数据（保持登录态）

# 监控配置
MONITOR_INTERVAL_SECONDS = 30  # 订单检查间隔（秒）
AUTO_DELIVERY_ENABLED = True   # 是否启用自动发货

# Flask 配置
FLASK_HOST = "127.0.0.1"
FLASK_PORT = 5000
FLASK_DEBUG = os.getenv('FLASK_DEBUG', '').lower() in ('1', 'true', 'yes')
