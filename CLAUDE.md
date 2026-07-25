# Goofish Auto Seller - 闲鱼自动化卖家系统

## 项目概述

闲鱼虚拟商品全自动卖家系统：GitHub采集资料 → 百度网盘打包 → 发布上架 → 自动发货

## 启动方式

```bash
cd backend && python app.py
# 管理后台: http://127.0.0.1:5000
```

## 技术栈

- 后端：Python Flask + SQLite
- 自动化：Playwright (MCP + Python)
- 前端：Vanilla HTML/CSS/JS

## 核心配置

- 售价：2.90 元
- 库存：9999
- 发货方式：无需邮寄
- 百度网盘提取码：math

## 关键目录

```
goofish/
├── backend/
│   ├── app.py              # Flask API 主服务
│   ├── models.py           # SQLite 数据模型 (含发货状态机)
│   ├── config.py           # 集中配置
│   ├── goofish_bot.py      # 闲鱼浏览器自动化 (Playwright)
│   ├── baidu_pan.py        # 百度网盘打包
│   ├── github_scraper.py   # GitHub 资料采集
│   ├── monitor_mcp.py      # 订单监控+发货
│   └── tests/              # 后端测试 (50个)
├── frontend/
│   ├── index.html          # 管理后台UI
│   └── app.js              # 前端逻辑
├── data/                   # SQLite 数据库
├── .claude/skills/fish.md  # Fish Skill 定义
└── start.bat               # Windows 启动脚本
```

## 发货状态机

订单发货使用5态状态机：
- `pending` → `sending` → `sent` / `failed` / `review`
- 原子抢占 `claim_order_for_delivery` 防并发重复发送
- 自动重试 3 次 (5s/30s/120s)，`unknown` 进 `review` 不重试
- 启动时恢复超过5分钟的 `sending` → `failed`

## 技能

使用 `/fish` 激活闲鱼自动化卖家技能，获取完整操作指南。
