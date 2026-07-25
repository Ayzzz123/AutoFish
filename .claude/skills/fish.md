---
name: fish
description: 闲鱼全自动卖家系统 — GitHub采集资料 → 百度网盘打包 → 发布上架 → 自动发货
---

# 闲鱼自动化卖家系统 (Fish)

一键完成：GitHub找资料 → 百度网盘打包 → 发布闲鱼 → 监控订单 → 自动发网盘链接

## 前置条件

1. 启动后端服务：`cd backend && python app.py`
2. 管理后台：http://127.0.0.1:5000
3. 闲鱼已登录（通过 MCP Playwright 浏览器）

## 核心配置

- 售价：统一 2.90 元
- 原价：统一 29.00 元
- 库存：9999
- 发货方式：无需邮寄（虚拟商品）
- 百度网盘提取码：math
- 分享有效期：永久有效

## 完整流程

### 第一步：GitHub 采集资料

```
搜索关键词 → 找到仓库 → 一键打包成商品
```

API：`POST /api/github/search` → `POST /api/github/create-products`

### 第二步：百度网盘打包

```
创建闲鱼/商品文件夹 → 上传资料文件 → 生成永久分享链接
```

API：`POST /api/baidu/package`
- 自动创建 `闲鱼/商品名/` 文件夹
- 自动上传封面图和资料说明
- 自动生成永久有效分享链接（提取码 math）
- 自动更新商品发货内容

### 第三步：获取商品图片

从闲鱼搜索同类商品，下载真实卖家图片使用。

### 第四步：发布到闲鱼

```
自动填表（描述/价格/无需邮寄/图片）→ 手动点击发布
```

API：`POST /api/goofish/publish`

### 第五步：订单监控 & 自动发货

```
定时检查已卖出 → 发现新订单 → 自动发百度网盘链接
```

API：`POST /api/monitor/report-orders` → `GET /api/monitor/pending-deliveries`

## 百度网盘自动化操作技术要点

以下技术来自实操验证：

1. **创建文件夹**：点击"新建文件夹" → `keyboard.type()` → `keyboard.press('Tab')` 确认（**不能用 Enter**，会触发搜索）
2. **进入文件夹**：`scrollIntoView()` + `dblclick` 事件（Playwright locator 会因 viewport 问题失败）
3. **上传文件**：触发 `input[type="file"]` 的 `click()` → 使用 file chooser
4. **创建分享**：选中文件 → 点"分享" → 永久有效 → 自定义提取码 → 点"复制链接"

## 商品图片策略

- 在闲鱼搜索同类商品 → 下载真实卖家的商品图片
- 图片 URL 模式：`https://img.alicdn.com/bao/uploaded/...`
- API：`POST /api/goofish/download-images`

## 自动化发货原理

买家在闲鱼下单后：
1. 订单出现在"我卖出的"页面
2. 监控检测到新订单（状态=已付款）
3. 系统打开闲鱼IM：`https://www.goofish.com/im?itemId={商品ID}&peerUserId={买家ID}`
4. 自动填入发货消息（含百度网盘链接和提取码）
5. 点击发送 → 买家在聊天中收到下载链接

## 常用命令

```bash
# 启动系统
cd backend && python app.py

# 启动订单监控（轻量模式，30秒轮询）
curl -X POST http://127.0.0.1:5000/api/monitor/start

# 查看待发货订单
curl http://127.0.0.1:5000/api/monitor/pending-deliveries

# 一键打包商品到百度网盘
curl -X POST http://127.0.0.1:5000/api/baidu/package \
  -H "Content-Type: application/json" \
  -d '{"product_id": 1, "extraction_code": "math"}'
```
