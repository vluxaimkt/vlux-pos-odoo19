(() => {
  "use strict";

  const state = { data: null, tab: "summary", error: null };
  const root = document.getElementById("root");

  const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
  })[char]);

  function money(value) {
    const currency = state.data?.currency || { symbol: "$", position: "before", decimals: 2 };
    const amount = Number(value || 0).toLocaleString("es-MX", {
      minimumFractionDigits: currency.decimals,
      maximumFractionDigits: currency.decimals,
    });
    return currency.position === "after" ? `${amount} ${currency.symbol}` : `${currency.symbol}${amount}`;
  }

  async function jsonRpc(route, params = {}) {
    const response = await fetch(route, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jsonrpc: "2.0", method: "call", params, id: Date.now() }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    if (payload.error) throw new Error(payload.error.message || "Error al consultar Odoo");
    return payload.result;
  }

  async function loadDashboard() {
    try {
      state.error = null;
      state.data = await jsonRpc("/vlux_owner/api/dashboard");
      render();
    } catch (error) {
      state.error = error instanceof Error ? error.message : "No se pudo actualizar";
      render();
    }
  }

  function kpi(label, value, icon) {
    return `<article class="kpi-card"><div class="kpi-icon">${icon}</div><span class="kpi-label">${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></article>`;
  }

  function header() {
    const data = state.data;
    const updated = data?.generated_at ? new Date(data.generated_at).toLocaleTimeString("es-MX", { hour: "2-digit", minute: "2-digit" }) : "—";
    return `
      <header class="topbar"><div><p class="eyebrow">VLUX OWNER</p><h1>¡Buenos días, ${escapeHtml(data.store.user_name)}!</h1><p>${escapeHtml(data.store.company_name)}</p></div><button class="icon-button" aria-label="Notificaciones">●</button></header>
      ${state.error ? `<div class="sync-warning">Sin conexión momentánea. Mostrando la última actualización.</div>` : ""}
      <section class="date-row"><span>${escapeHtml(data.date_label)}</span><small><i></i> Actualizado ${escapeHtml(updated)}</small></section>`;
  }

  function summaryView() {
    const data = state.data;
    const comparison = data.summary.comparison_vs_yesterday_pct;
    const comparisonHtml = comparison === null ? "" : `<p class="${comparison >= 0 ? "positive" : "negative"}">${comparison >= 0 ? "▲" : "▼"} ${Math.abs(comparison).toFixed(1)}% vs ayer a esta hora</p>`;
    const registers = data.sales_by_register.map((item) => `
      <div class="register-row"><div class="register-head"><strong>${escapeHtml(item.name)}</strong><span>${money(item.amount)} · ${Number(item.share_pct).toFixed(1)}%</span></div><div class="progress"><i style="width:${Math.min(Number(item.share_pct), 100)}%"></i></div></div>`).join("");
    return `
      <section class="hero-card"><div><span>VENTAS TOTALES HOY</span><strong>${money(data.summary.sales_today)}</strong>${comparisonHtml}</div><canvas class="sparkline" id="sparkline" width="252" height="180"></canvas></section>
      <section class="kpi-grid">${kpi("Tickets", data.summary.tickets, "T")}${kpi("Productos vendidos", data.summary.units_sold, "P")}${kpi("Ticket promedio", money(data.summary.average_ticket), "$")}${kpi("Cajas con ventas", data.sales_by_register.length, "C")}</section>
      <section class="section-block"><div class="section-title"><div><span>VENTAS POR HORA</span><h2>Ritmo del día</h2></div></div><div class="chart-card"><canvas class="chart-canvas" id="salesChart"></canvas></div></section>
      <section class="section-block"><div class="section-title"><div><span>OPERACIÓN</span><h2>Ventas por caja</h2></div><button data-tab="sales">Ver todas</button></div><div class="list-surface">${registers || '<div class="empty-state">Todavía no hay ventas confirmadas hoy.</div>'}</div></section>`;
  }

  function salesView() {
    const rows = state.data.latest_sales.map((sale) => `<div class="detail-row"><div class="grow"><strong>${escapeHtml(sale.reference)}</strong><small>${escapeHtml(sale.time)} · ${escapeHtml(sale.register)} · ${escapeHtml(sale.cashier)}</small></div><strong>${money(sale.amount)}</strong></div>`).join("");
    return `<section class="page-section"><p class="eyebrow">VENTAS</p><h2>Últimas ventas confirmadas</h2><div class="list-surface">${rows || '<div class="empty-state">Sin ventas confirmadas.</div>'}</div></section>`;
  }

  function productsView() {
    const rows = state.data.top_products.map((product, index) => `<div class="detail-row"><span class="rank">${index + 1}</span><div class="grow"><strong>${escapeHtml(product.name)}</strong><small>${Number(product.qty).toFixed(0)} unidades</small></div><strong>${money(product.amount)}</strong></div>`).join("");
    return `<section class="page-section"><p class="eyebrow">PRODUCTOS</p><h2>Más vendidos hoy</h2><div class="list-surface">${rows || '<div class="empty-state">Aún no hay datos de productos.</div>'}</div></section>`;
  }

  function inventoryView() {
    const rows = state.data.low_stock.map((product) => `<div class="detail-row"><span class="warning-icon">!</span><div class="grow"><strong>${escapeHtml(product.name)}</strong><small>Mínimo configurado: ${escapeHtml(product.threshold)}</small></div><strong>${Number(product.qty_available).toFixed(0)}</strong></div>`).join("");
    return `<section class="page-section"><p class="eyebrow">INVENTARIO</p><h2>Stock bajo</h2><div class="list-surface">${rows || '<div class="empty-state">Todo el inventario está por encima del mínimo.</div>'}</div></section>`;
  }

  function moreView() {
    return `<section class="page-section"><p class="eyebrow">VLUX ECOSYSTEM</p><h2>Tu negocio, conectado</h2><div class="menu-surface"><div><b class="menu-icon">P</b><span><strong>VLUX POS</strong><small>Punto de venta conectado</small></span></div><div><b class="menu-icon">S</b><span><strong>VLUX Mobile Scanner</strong><small>Escaneo móvil para tus cajas</small></span></div><div><b class="menu-icon">O</b><span><strong>VLUX Owner</strong><small>Control del negocio desde el celular</small></span></div></div></section>`;
  }

  function nav() {
    const items = [["summary","⌂","Resumen"],["sales","$","Ventas"],["products","P","Productos"],["inventory","I","Inventario"],["more","•••","Más"]];
    return `<nav class="bottom-nav">${items.map(([id, icon, label]) => `<button data-tab="${id}" class="${state.tab === id ? "active" : ""}"><b>${icon}</b><span>${label}</span></button>`).join("")}</nav>`;
  }

  function drawChart(canvasId, data, compact = false) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || !data?.length) return;
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.floor(rect.width * dpr));
    canvas.height = Math.max(1, Math.floor(rect.height * dpr));
    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);
    const w = rect.width, h = rect.height;
    const pad = compact ? 5 : 22;
    const values = data.map((point) => Number(point.amount || 0));
    const max = Math.max(...values, 1);
    const min = Math.min(...values, 0);
    const range = Math.max(max - min, 1);
    const points = values.map((value, index) => ({ x: pad + (index / Math.max(values.length - 1, 1)) * (w - pad * 2), y: pad + (1 - (value - min) / range) * (h - pad * 2) }));
    if (!compact) {
      ctx.strokeStyle = "rgba(255,255,255,.06)"; ctx.lineWidth = 1;
      for (let i = 0; i < 4; i++) { const y = pad + i * ((h - pad * 2) / 3); ctx.beginPath(); ctx.moveTo(pad, y); ctx.lineTo(w-pad, y); ctx.stroke(); }
    }
    const gradient = ctx.createLinearGradient(0, 0, 0, h); gradient.addColorStop(0, "rgba(157,92,255,.45)"); gradient.addColorStop(1, "rgba(157,92,255,0)");
    ctx.beginPath(); ctx.moveTo(points[0].x, h-pad); points.forEach((p) => ctx.lineTo(p.x,p.y)); ctx.lineTo(points[points.length-1].x,h-pad); ctx.closePath(); ctx.fillStyle = gradient; ctx.fill();
    ctx.beginPath(); points.forEach((p,i) => i ? ctx.lineTo(p.x,p.y) : ctx.moveTo(p.x,p.y)); ctx.strokeStyle="#a970ff"; ctx.lineWidth=compact?3:2.5; ctx.lineJoin="round"; ctx.lineCap="round"; ctx.stroke();
    if (!compact) { ctx.fillStyle="#777286"; ctx.font="11px system-ui"; ctx.textAlign="center"; data.forEach((point,index)=>{ if(index%2===0) ctx.fillText(point.label,points[index].x,h-4); }); }
  }

  function render() {
    if (!state.data) {
      root.innerHTML = state.error ? `<main class="app-shell center-state"><h1>No pudimos cargar VLUX Owner</h1><p>${escapeHtml(state.error)}</p><button id="retry">Reintentar</button><a href="/web/login?redirect=/vlux-owner/">Iniciar sesión en Odoo</a></main>` : `<main class="app-shell center-state"><div class="loader"></div><p>Preparando tu negocio…</p></main>`;
      document.getElementById("retry")?.addEventListener("click", loadDashboard);
      return;
    }
    const view = state.tab === "summary" ? summaryView() : state.tab === "sales" ? salesView() : state.tab === "products" ? productsView() : state.tab === "inventory" ? inventoryView() : moreView();
    root.innerHTML = `<main class="app-shell">${header()}${view}${nav()}</main>`;
    root.querySelectorAll("[data-tab]").forEach((button) => button.addEventListener("click", () => { state.tab = button.dataset.tab; render(); }));
    if (state.tab === "summary") requestAnimationFrame(() => { drawChart("sparkline", state.data.sales_trend, true); drawChart("salesChart", state.data.sales_trend, false); });
  }

  window.addEventListener("resize", () => { if (state.tab === "summary" && state.data) { drawChart("sparkline", state.data.sales_trend, true); drawChart("salesChart", state.data.sales_trend, false); } });
  render();
  loadDashboard();
  window.setInterval(loadDashboard, 30000);

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => navigator.serviceWorker.register("/vlux-owner/sw.js", { scope: "/vlux-owner/" }).catch(console.error));
  }
})();
