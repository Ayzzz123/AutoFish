// 闲鱼自动化管理系统 v1.0

const API = '/api';
let currentPage = 'dashboard';
let monitorRunning = false;

// ============ UTILS ============
function toast(msg, type = 'info') {
  const icons = { success: '[OK]', error: '[ERR]', info: '[i]', warning: '[!]' };
  const c = document.getElementById('toastContainer');
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.innerHTML = `<span class="toast-icon">${icons[type] || '[i]'}</span>${msg}`;
  c.appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity 0.3s'; setTimeout(() => el.remove(), 300); }, 3500);
}
function fmt(t) { return t ? new Date(t).toLocaleString('zh-CN') : '-'; }
function price(p) { return (parseFloat(p) || 0).toFixed(2); }
function esc(s) { return s ? String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;') : ''; }
function tag(s) {
  const m = { draft: ['tag-draft','草稿'], listed: ['tag-listed','已上架'], sold_out: ['tag-closed','已售罄'], removed: ['tag-closed','已下架'], pending: ['tag-draft','待付款'], paid: ['tag-paid','待发货'], shipped: ['tag-shipped','待收货'], completed: ['tag-completed','已完成'], closed: ['tag-closed','已关闭'], refund: ['tag-closed','退款中'] };
  const t = m[s] || ['tag-draft', s];
  return `<span class="tag ${t[0]}">${t[1]}</span>`;
}
async function call(url, opts = {}) {
  try {
    const res = await fetch(API + url, { headers: { 'Content-Type': 'application/json' }, ...opts, body: opts.body ? JSON.stringify(opts.body) : undefined });
    if (res.status === 204 || res.headers.get('content-length') === '0') return { success: true };
    return await res.json();
  } catch (e) { toast('请求失败: ' + e.message, 'error'); return { success: false, error: e.message }; }
}

// ============ NAVIGATION ============
function navigateTo(page) {
  currentPage = page;
  document.querySelectorAll('.sidebar-item').forEach(el => el.classList.remove('active'));
  const navItem = document.querySelector(`[data-page="${page}"]`);
  if (navItem) navItem.classList.add('active');

  const titles = { dashboard: '工作台', products: '商品管理', publish: '发布上架', orders: '订单管理' };
  document.getElementById('pageTitle').textContent = titles[page] || page;

  const container = document.getElementById('pageContent');
  switch (page) {
    case 'dashboard': renderDashboard(container); break;
    case 'products': renderProducts(container); break;
    case 'publish': renderPublish(container); break;
    case 'orders': renderOrders(container); break;
  }
}

document.querySelectorAll('.sidebar-item').forEach(item => {
  item.addEventListener('click', () => navigateTo(item.dataset.page));
});

// ============ DASHBOARD ============
async function renderDashboard(container) {
  const info = await call('/system/info');
  const d = info.success ? info.data : { products_count: 0, orders_count: 0, pending_delivery: 0 };

  container.innerHTML = `
    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-icon products">P</div>
        <div><div class="stat-value">${d.products_count}</div><div class="stat-label">商品总数</div></div>
      </div>
      <div class="stat-card">
        <div class="stat-icon orders">O</div>
        <div><div class="stat-value">${d.orders_count}</div><div class="stat-label">订单总数</div></div>
      </div>
      <div class="stat-card">
        <div class="stat-icon pending">!</div>
        <div><div class="stat-value">${d.pending_delivery}</div><div class="stat-label">待发货订单</div></div>
      </div>
      <div class="stat-card">
        <div class="stat-icon delivered">OK</div>
        <div><div class="stat-value">${d.monitor_running ? 'ON' : 'OFF'}</div><div class="stat-label">监控状态</div></div>
      </div>
    </div>

    <div class="quick-actions">
      <div class="action-card" onclick="openProductModal()">
        <div class="ac-icon" style="background:#fff5f0;color:#ff5000;">+</div>
        <div><div class="ac-label">添加商品</div><div class="ac-desc">手动创建新商品</div></div>
      </div>
      <div class="action-card" onclick="navigateTo('publish')">
        <div class="ac-icon" style="background:#e8f5e9;color:#2e7d32;">&gt;</div>
        <div><div class="ac-label">发布上架</div><div class="ac-desc">发布商品到闲鱼</div></div>
      </div>
      <div class="action-card" onclick="navigateTo('orders')">
        <div class="ac-icon" style="background:#fff8e1;color:#e65100;">#</div>
        <div><div class="ac-label">订单管理</div><div class="ac-desc">查看和处理订单</div></div>
      </div>
    </div>

    <div class="panel">
        <div class="panel-header"><h2>最近订单</h2></div>
        <div class="panel-body" id="dashOrders">加载中...</div>
      </div>
  `;

  // 加载最近订单
  const ordersRes = await call('/orders');
  const oDiv = document.getElementById('dashOrders');
  if (ordersRes.success && ordersRes.data.length > 0) {
    oDiv.innerHTML = ordersRes.data.slice(0, 5).map(o => `
      <div style="display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid #f3f4f6;font-size:13px;">
        <span>#${o.id} ${esc(o.buyer_name) || '未知买家'}</span>
        <span style="font-weight:600;color:var(--primary);">${price(o.amount)}</span>
        <span>${tag(o.status)}</span>
        <span class="${deliveryStateClass(o.delivery_status)}" style="font-size:11px;">${deliveryStateLabel(o.delivery_status)}</span>
      </div>`).join('');
  } else {
    oDiv.innerHTML = '<div class="empty"><p>暂无订单</p></div>';
  }

  updateSidebarStats();
}

// ============ PRODUCTS ============
async function renderProducts(container) {
  const res = await call('/products');
  const products = res.success ? res.data : [];

  container.innerHTML = `
    <div class="panel">
      <div class="panel-header">
        <h2>商品列表 (${products.length})</h2>
        <div style="display:flex;gap:8px;">
          <button class="btn btn-primary" onclick="openProductModal()">+ 添加商品</button>
          <button class="btn btn-ghost" onclick="renderProducts(document.getElementById('pageContent'))">刷新</button>
        </div>
      </div>
      <div class="panel-body flat">
        ${products.length === 0 ? '<div class="empty"><div class="empty-icon">---</div><p>还没有商品</p><p class="empty-hint">使用 GitHub 采集或手动添加商品</p></div>' : `
        <div class="table-wrap">
          <table>
            <thead><tr><th>ID</th><th>标题</th><th>价格</th><th>状态</th><th>闲鱼ID</th><th>分类</th><th>时间</th><th>操作</th></tr></thead>
            <tbody>${products.map(p => `
              <tr>
                <td>${p.id}</td>
                <td><span class="cell-title" title="${esc(p.description)}">${esc(p.title)}</span></td>
                <td class="cell-price">${price(p.price)}</td>
                <td>${tag(p.status)}</td>
                <td>${p.goofish_item_id || '-'}</td>
                <td>${esc(p.category) || '-'}</td>
                <td style="font-size:12px;color:var(--text-secondary);">${fmt(p.created_at)}</td>
                <td class="cell-actions">
                  <button class="btn btn-ghost btn-sm" onclick="editProduct(${p.id})">编辑</button>
                  <button class="btn btn-primary btn-sm" onclick="navigateTo('publish');setTimeout(()=>document.getElementById('publishSelect').value=${p.id},200)">发布</button>
                  <button class="btn btn-info btn-sm" onclick="packageToBaidu(${p.id})">打包</button>
                  <button class="btn btn-danger btn-sm" onclick="deleteProduct(${p.id})">删除</button>
                </td>
              </tr>`).join('')}</tbody>
          </table>
        </div>`}
      </div>
    </div>
  `;

  updateSidebarStats();
}

// ============ PUBLISH ============
async function renderPublish(container) {
  const res = await call('/products');
  const products = res.success ? res.data.filter(p => p.status === 'draft' || p.status === 'listed') : [];

  container.innerHTML = `
    <div class="alert alert-warning">
      <strong>注意：</strong>系统会自动填写商品信息到闲鱼发布页面，最终需要你手动点击发布按钮并完成人机验证。
    </div>
    <div class="panel">
      <div class="panel-header"><h2>发布商品到闲鱼</h2></div>
      <div class="panel-body">
        <div class="form-row">
          <div class="form-group">
            <label class="form-label">选择商品</label>
            <select class="form-select" id="publishSelect">
              <option value="">请选择要发布的商品...</option>
              ${products.map(p => `<option value="${p.id}">[${p.id}] ${esc(p.title)} - ${price(p.price)} 元</option>`).join('')}
            </select>
          </div>
          <div style="display:flex;align-items:flex-end;">
            <button class="btn btn-primary btn-lg" onclick="doPublish()">发布到闲鱼</button>
          </div>
        </div>
        <div id="publishResult"></div>
      </div>
    </div>

    <div class="panel" style="margin-top:16px;">
      <div class="panel-header"><h2>已上架商品</h2></div>
      <div class="panel-body flat">
        ${products.filter(p => p.status === 'listed').length === 0
          ? '<div class="empty"><p>暂无已上架商品</p></div>'
          : `<div class="table-wrap"><table><thead><tr><th>ID</th><th>标题</th><th>价格</th><th>闲鱼ID</th><th>操作</th></tr></thead>
          <tbody>${products.filter(p => p.status === 'listed').map(p => `
            <tr><td>${p.id}</td><td class="cell-title">${esc(p.title)}</td>
            <td class="cell-price">${price(p.price)}</td>
            <td>${p.goofish_item_id || '-'}</td>
            <td><button class="btn btn-sm btn-ghost" onclick="navigateTo('orders')">查看订单</button></td></tr>
          `).join('')}</tbody></table></div>`}
      </div>
    </div>
  `;
}

async function doPublish() {
  const productId = document.getElementById('publishSelect')?.value;
  if (!productId) return toast('请选择商品', 'warning');

  // 检查登录
  const statusRes = await call('/goofish/status');
  if (!statusRes.logged_in) {
    if (confirm('尚未登录闲鱼。是否打开浏览器进行登录？')) {
      await call('/goofish/login', { method: 'POST', body: { timeout: 5 } });
      toast('浏览器已打开，请登录后重试', 'info');
    }
    return;
  }

  const resultDiv = document.getElementById('publishResult');
  resultDiv.innerHTML = '<div class="loading-wrap"><div class="spinner"></div>正在打开闲鱼发布页面...</div>';

  const res = await call('/goofish/publish', { method: 'POST', body: { product_id: parseInt(productId) } });

  if (res.success) {
    resultDiv.innerHTML = `
      <div class="alert alert-success">
        <strong>商品信息已填写完成！</strong><br>
        请在浏览器中检查信息并点击<strong>"发布"</strong>按钮完成发布。
      </div>`;
  } else {
    resultDiv.innerHTML = `<div class="alert alert-warning">发布失败：${esc(res.error || '未知错误')}</div>`;
  }
}

// ============ ORDERS ============

function deliveryStateLabel(ds) {
  const m = { pending: '待发送', sending: '发送中', sent: '已发送', failed: '发送失败', review: '需人工检查' };
  return m[ds] || ds || '待发送';
}
function deliveryStateClass(ds) {
  return `delivery-${ds || 'pending'}`;
}
function canDeliver(o) {
  return o.status === 'paid' && !o.delivery_sent && (o.delivery_status === 'pending' || o.delivery_status === 'failed' || !o.delivery_status);
}
function deliveryBtnText(o) {
  return o.delivery_status === 'failed' ? '重试发货' : '发货';
}

async function renderOrders(container) {
  const res = await call('/orders');
  const orders = res.success ? res.data : [];

  container.innerHTML = `
    <div class="panel">
      <div class="panel-header">
        <h2>订单列表 (${orders.length})</h2>
        <div style="display:flex;gap:8px;">
          <button class="btn btn-success btn-sm" id="btnMonStart" onclick="toggleMonitor()" ${monitorRunning ? 'style="display:none;"' : ''}>启动监控</button>
          <button class="btn btn-danger btn-sm" id="btnMonStop" onclick="toggleMonitor()" ${!monitorRunning ? 'style="display:none;"' : ''}>停止监控</button>
          <button class="btn btn-ghost btn-sm" onclick="renderOrders(document.getElementById('pageContent'))">刷新</button>
        </div>
      </div>
      <div class="panel-body flat">
        ${orders.length === 0 ? '<div class="empty"><div class="empty-icon">---</div><p>暂无订单</p><p class="empty-hint">启动监控后将自动检测新订单并自动发货</p></div>' : `
        <div class="table-wrap"><table>
          <thead><tr><th>ID</th><th>商品ID</th><th>买家</th><th>金额</th><th>订单状态</th><th>发货状态</th><th>时间</th><th>操作</th></tr></thead>
          <tbody>${orders.map(o => `
            <tr>
              <td>${o.id}</td><td>${o.product_id || '-'}</td>
              <td>${esc(o.buyer_name) || '-'}</td>
              <td class="cell-price">${price(o.amount)}</td>
              <td>${tag(o.status)}</td>
              <td><span class="${deliveryStateClass(o.delivery_status)}">${deliveryStateLabel(o.delivery_status)}</span>${o.delivery_attempts > 0 ? ` <small style="color:var(--text-secondary);">(${o.delivery_attempts}次)</small>` : ''}</td>
              <td style="font-size:12px;">${fmt(o.detected_at)}</td>
              <td class="cell-actions">
                ${canDeliver(o) ? `<button class="btn btn-success btn-sm" onclick="doManualDeliver(${o.id})">${deliveryBtnText(o)}</button>` : ''}
                <button class="btn btn-ghost btn-sm" onclick="showOrderDetail(${o.id})">详情</button>
              </td>
            </tr>`).join('')}</tbody>
        </table></div>`}
      </div>
    </div>
  `;

  updateSidebarStats();
}

async function showOrderDetail(orderId) {
  const res = await call('/orders');
  if (!res.success) return;
  const o = res.data.find(x => x.id === orderId);
  if (!o) return;

  const detailHtml = `
    <div class="order-detail-grid">
      <div><dt>订单ID</dt><dd>#${o.id}</dd></div>
      <div><dt>闲鱼订单号</dt><dd>${esc(o.goofish_order_id) || '-'}</dd></div>
      <div><dt>买家</dt><dd>${esc(o.buyer_name) || '-'}</dd></div>
      <div><dt>金额</dt><dd>${price(o.amount)}</dd></div>
      <div><dt>订单状态</dt><dd>${tag(o.status)}</dd></div>
      <div><dt>发货状态</dt><dd><span class="${deliveryStateClass(o.delivery_status)}">${deliveryStateLabel(o.delivery_status)}</span></dd></div>
      <div><dt>尝试次数</dt><dd>${o.delivery_attempts || 0} 次</dd></div>
      <div><dt>最近尝试</dt><dd>${fmt(o.last_delivery_attempt_at)}</dd></div>
      <div><dt>检测时间</dt><dd>${fmt(o.detected_at)}</dd></div>
      <div><dt>发货时间</dt><dd>${fmt(o.sent_at)}</dd></div>
    </div>
    ${o.delivery_error ? `<div class="delivery-error-box"><strong>错误原因：</strong>${esc(o.delivery_error)}</div>` : ''}
    <div style="margin-top:12px;padding:12px;background:#fafafa;border-radius:6px;font-size:12px;word-break:break-word;">
      <strong>发货内容：</strong><br>${esc(o.delivery_content || '无')}
    </div>
  `;

  // 复用现有 modal
  document.getElementById('productModalTitle').textContent = '订单详情 #' + o.id;
  document.getElementById('editProductId').value = '';
  document.querySelector('#productModal .modal-body').innerHTML = detailHtml;
  document.querySelector('#productModal .modal-footer').innerHTML = canDeliver(o)
    ? `<button class="btn btn-ghost" onclick="closeProductModal()">关闭</button>
       <button class="btn btn-success btn-lg" onclick="closeProductModal();doManualDeliver(${o.id})">${deliveryBtnText(o)}</button>`
    : `<button class="btn btn-ghost" onclick="closeProductModal()">关闭</button>`;
  document.getElementById('productModal').classList.add('show');
}

async function doManualDeliver(orderId) {
  // 获取订单信息以判断按钮文案
  const res = await call('/orders');
  const order = res.success ? res.data.find(x => x.id === orderId) : null;
  const btnText = order ? deliveryBtnText(order) : '发货';
  const confirmMsg = btnText === '重试发货'
    ? '确定重新尝试发送？将通过闲鱼聊天向买家发送资料。'
    : '确定手动发货？将通过闲鱼聊天向买家发送百度网盘链接。';

  if (!confirm(confirmMsg)) return;
  const result = await call(`/orders/${orderId}/deliver`, { method: 'POST' });
  if (result.success) { toast('发货成功', 'success'); navigateTo('orders'); }
  else { toast('发货失败: ' + (result.error || '未知错误'), 'error'); }
}

async function toggleMonitor() {
  if (monitorRunning) {
    const res = await call('/monitor/stop', { method: 'POST' });
    if (res.success) { monitorRunning = false; toast('监控已停止', 'info'); }
  } else {
    const res = await call('/monitor/start', { method: 'POST' });
    if (res.success) { monitorRunning = true; toast('订单监控已启动', 'success'); }
  }
  updateSidebarStats();
  navigateTo(currentPage);
}


// ============ PRODUCT MODAL ============
function openProductModal(id) {
  const modal = document.getElementById('productModal');
  document.getElementById('editProductId').value = '';
  document.getElementById('editTitle').value = '';
  document.getElementById('editDescription').value = '';
  document.getElementById('editPrice').value = '2.90';
  document.getElementById('editOriginalPrice').value = '99.00';
  document.getElementById('editDeliveryContent').value = '';
  document.getElementById('editCategory').value = '';
  document.getElementById('productModalTitle').textContent = '添加商品';

  if (id) {
    document.getElementById('productModalTitle').textContent = '编辑商品';
    loadProductForEdit(id);
  }
  modal.classList.add('show');
}

function closeProductModal() { document.getElementById('productModal').classList.remove('show'); }

async function loadProductForEdit(id) {
  const res = await call(`/products/${id}`);
  if (res.success) {
    const p = res.data;
    document.getElementById('editProductId').value = p.id;
    document.getElementById('editTitle').value = p.title;
    document.getElementById('editDescription').value = p.description;
    document.getElementById('editPrice').value = p.price;
    document.getElementById('editOriginalPrice').value = p.original_price;
    document.getElementById('editDeliveryContent').value = p.delivery_content;
    document.getElementById('editCategory').value = p.category;
  }
}

function editProduct(id) { openProductModal(id); }

async function saveProduct() {
  const id = document.getElementById('editProductId').value;
  const data = {
    title: document.getElementById('editTitle').value.trim(),
    description: document.getElementById('editDescription').value.trim(),
    price: parseFloat(document.getElementById('editPrice').value) || 0,
    original_price: parseFloat(document.getElementById('editOriginalPrice').value) || 0,
    delivery_content: document.getElementById('editDeliveryContent').value.trim(),
    category: document.getElementById('editCategory').value.trim(),
  };
  if (!data.title || !data.delivery_content) return toast('标题和发货内容不能为空', 'error');
  if (data.price <= 0) return toast('价格必须大于0', 'error');

  const res = id
    ? await call(`/products/${id}`, { method: 'PUT', body: data })
    : await call('/products', { method: 'POST', body: data });

  if (res.success) {
    toast(id ? '商品已更新' : '商品已创建', 'success');
    closeProductModal();
    updateSidebarStats();
    if (currentPage === 'products' || currentPage === 'dashboard') navigateTo(currentPage);
  } else {
    toast(res.error || '操作失败', 'error');
  }
}

async function packageToBaidu(productId) {
  toast('正在打包到百度网盘...', 'info');
  const code = prompt('提取码（默认math）：', 'math') || 'math';
  const res = await call('/baidu/package', {
    method: 'POST',
    body: { product_id: productId, extraction_code: code }
  });
  if (res.success) {
    toast(`打包完成！链接: ${res.data.link}`, 'success');
    alert(`百度网盘打包完成！\n\n链接: ${res.data.link}\n提取码: ${res.data.code}\n\n发货内容已自动更新。`);
  } else {
    toast('打包失败: ' + (res.error || '未知错误'), 'error');
  }
}

async function deleteProduct(id) {
  if (!confirm('确定要删除此商品？此操作不可恢复。')) return;
  const res = await call(`/products/${id}`, { method: 'DELETE' });
  if (res.success) { toast('已删除', 'success'); updateSidebarStats(); navigateTo(currentPage); }
  else toast('删除失败', 'error');
}

// ============ SIDEBAR STATS ============
async function updateSidebarStats() {
  const res = await call('/system/info');
  if (!res.success) return;
  const d = res.data;

  const pc = document.getElementById('navProductCount');
  const np = document.getElementById('navPendingCount');
  if (pc) pc.textContent = d.products_count;
  if (np) np.textContent = d.pending_delivery;

  const sd = document.getElementById('statusDot');
  const st = document.getElementById('statusText');
  if (d.monitor_running) {
    if (sd) sd.className = 'dot on';
    if (st) st.textContent = '监控运行中';
    monitorRunning = true;
  } else {
    if (sd) sd.className = 'dot off';
    if (st) st.textContent = '监控未启动';
    monitorRunning = false;
  }
}

async function updateLoginStatus() {
  const res = await call('/goofish/status');
  const dot = document.getElementById('loginDot');
  const txt = document.getElementById('loginText');
  if (res.logged_in) {
    if (dot) dot.className = 'dot on';
    if (txt) txt.textContent = '已登录闲鱼';
  } else {
    if (dot) dot.className = 'dot off';
    if (txt) txt.textContent = '未登录闲鱼';
  }
}

// ============ INIT ============
document.addEventListener('DOMContentLoaded', () => {
  navigateTo('dashboard');
  updateLoginStatus();
  setInterval(updateSidebarStats, 15000);
  setInterval(updateLoginStatus, 300000);
});

document.getElementById('productModal').addEventListener('click', function(e) {
  if (e.target === this) closeProductModal();
});

document.addEventListener('keydown', e => { if (e.key === 'Escape') closeProductModal(); });
