/* ==========================================================================
   GlassBox Pro - Autonomous AI Options Trading Terminal Application Logic
   ========================================================================== */

const API_BASE = window.location.origin;

// State Cache
let currentSymbol = 'SPY';
let currentMarketContext = null;
let currentChartTimeframe = '1M';
let candleBars = [];
let auditSnapshots = [];
let memoryData = null;

// --- INITIALIZATION ---
document.addEventListener('DOMContentLoaded', () => {
  loadMarketData();
  loadTickerTape();
  refreshSystemStatus();
  loadLivePositions();
  loadAuditSnapshots();
  loadSelfImprovingMemory();
  loadCandleChart();
  loadRiskConfig();          // real kernel/config.yaml drives the rule count + modal

  // Background polling intervals
  setInterval(refreshSystemStatus, 3000);
  setInterval(loadLivePositions, 5000);
  setInterval(loadAuditSnapshots, 5000);
  setInterval(loadSelfImprovingMemory, 6000);
  setInterval(loadTickerTape, 15000);

  // Setup interactive chart mouse events
  setupChartHoverTooltip();
});

// --- TAB SWITCHING ---
function switchTab(tabId) {
  document.querySelectorAll('.nav-tab').forEach(btn => btn.classList.remove('active'));
  document.querySelectorAll('.tab-pane').forEach(pane => pane.classList.remove('active'));

  const btn = document.getElementById(`tab-btn-${tabId}`);
  const pane = document.getElementById(`pane-${tabId}`);

  if (btn) btn.classList.add('active');
  if (pane) pane.classList.add('active');

  if (tabId === 'terminal') {
    setTimeout(loadCandleChart, 50);
  } else if (tabId === 'memory') {
    loadSelfImprovingMemory();
  } else if (tabId === 'tradetrap') {
    loadAct2Status();
    loadReconciliationLog();
  } else if (tabId === 'blindfold') {
    document.getElementById('blindfold-symbol-label').textContent = getActiveSymbol();
  } else if (tabId === 'arena') {
    loadRegimePanel();
  }
}

// --- DYNAMIC TICKER HANDLING ---
function getActiveSymbol() {
  const customInput = document.getElementById('custom-ticker-input');
  if (customInput && customInput.value.trim()) {
    return customInput.value.trim().toUpperCase();
  }
  return currentSymbol;
}

function selectQuickTicker(sym) {
  const customInput = document.getElementById('custom-ticker-input');
  if (customInput) customInput.value = sym;
  currentSymbol = sym;

  document.querySelectorAll('.badge-tag').forEach(b => {
    b.classList.toggle('active-tag', b.textContent === sym);
  });

  loadMarketData();
  loadCandleChart();
}

function applyCustomTicker() {
  currentSymbol = getActiveSymbol();
  loadMarketData();
  loadCandleChart();
}

function triggerStudioForCurrentSymbol() {
  switchTab('copilot');
  runAct1Pipeline();
}

// --- TICKER TAPE (real prices, no fabricated placeholders) ---
async function loadTickerTape() {
  try {
    const res = await fetch(`${API_BASE}/api/ticker-tape`);
    if (!res.ok) return;
    const data = await res.json();
    const tape = document.getElementById('ticker-tape');
    if (!tape) return;

    const items = Object.entries(data.quotes || {}).map(([sym, q]) => {
      const hasChange = typeof q.change_pct === 'number';
      const up = hasChange && q.change_pct >= 0;
      const changeStr = hasChange ? `${up ? '+' : ''}${q.change_pct.toFixed(2)}%` : '';
      return `<span class="tape-item"><strong class="tape-sym">${sym}</strong> $${q.price.toFixed(2)} ${hasChange ? `<span class="${up ? 'tape-up' : 'tape-down'}">${changeStr}</span>` : ''}</span>`;
    });
    if (typeof data.vix === 'number') {
      items.push(`<span class="tape-item"><strong class="tape-sym">VIX</strong> ${data.vix.toFixed(2)}</span>`);
    }
    if (items.length > 0) {
      tape.innerHTML = items.join('');
    }
  } catch (e) {
    // Leave existing tape content in place rather than crash the page on a transient failure.
  }
}

// --- PERCEPTION & MARKET DATA ---
async function loadMarketData() {
  const symbol = getActiveSymbol();
  try {
    const res = await fetch(`${API_BASE}/api/market/${symbol}`);
    if (!res.ok) return;
    const ctx = await res.json();
    currentMarketContext = ctx;

    document.getElementById('kpi-price').textContent = `$${ctx.underlying_price.toFixed(2)}`;
    document.getElementById('kpi-iv-rank').textContent = `${ctx.iv_rank.toFixed(1)}%`;
    document.getElementById('kpi-vrp').textContent = `${ctx.vrp >= 0 ? '+' : ''}${ctx.vrp.toFixed(1)} pts`;
    document.getElementById('kpi-vix').textContent = (ctx.vix === null || ctx.vix === undefined) ? 'N/A' : ctx.vix.toFixed(1);

    const chartSym = document.getElementById('chart-symbol-header');
    const chartPrice = document.getElementById('chart-price-header');
    if (chartSym) chartSym.textContent = symbol;
    if (chartPrice) chartPrice.textContent = `$${ctx.underlying_price.toFixed(2)}`;

    // Update Propose Button in Studio
    const act1Btn = document.getElementById('btn-act1-run');
    if (act1Btn) act1Btn.textContent = `▶ Generate & Execute Strategy (${symbol})`;

    // Load options payoff diagram, volatility smile, and options chain table
    loadPayoffAndSmileData(symbol);
    renderOptionsChain(ctx.contracts, ctx.underlying_price);

  } catch (err) {
    console.error('Error fetching market context:', err);
  }
}

// --- INTERACTIVE CANDLESTICK CHART ENGINE WITH BOLLINGER BANDS ---
async function loadCandleChart() {
  const symbol = getActiveSymbol();
  try {
    const res = await fetch(`${API_BASE}/api/chart/${symbol}?timeframe=${currentChartTimeframe}`);
    if (!res.ok) return;
    const data = await res.json();
    candleBars = data.bars || [];
    renderCandleChart(candleBars);
  } catch (err) {
    console.error('Candle chart fetch error:', err);
  }
}

function setChartTimeframe(tf) {
  currentChartTimeframe = tf;
  loadCandleChart();
}

function renderCandleChart(bars) {
  const canvas = document.getElementById('mainCandleCanvas');
  if (!canvas || !bars || bars.length === 0) return;
  const ctx = canvas.getContext('2d');
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  const paddingBottom = 42;
  const chartH = h - paddingBottom;

  const minLow = Math.min(...bars.map(b => b.low)) * 0.992;
  const maxHigh = Math.max(...bars.map(b => b.high)) * 1.008;
  const maxVol = Math.max(...bars.map(b => b.volume)) || 1;

  const getX = i => (i / (bars.length - 1 || 1)) * (w - 65) + 15;
  const getY = p => chartH - ((p - minLow) / (maxHigh - minLow || 1)) * (chartH - 20) - 10;
  const barWidth = Math.max(3, (w - 80) / bars.length * 0.65);

  // Horizontal Grid Lines
  ctx.strokeStyle = 'rgba(255,255,255,0.05)';
  ctx.lineWidth = 1;
  for (let i = 0; i < 4; i++) {
    const yVal = minLow + (i / 3) * (maxHigh - minLow);
    const yPx = getY(yVal);
    ctx.beginPath();
    ctx.moveTo(10, yPx);
    ctx.lineTo(w - 10, yPx);
    ctx.stroke();

    ctx.fillStyle = 'rgba(255,255,255,0.3)';
    ctx.font = '10px monospace';
    ctx.fillText(`$${yVal.toFixed(1)}`, w - 48, yPx + 3);
  }

  // Draw Volume Histogram (Bottom)
  bars.forEach((b, i) => {
    const x = getX(i);
    const volH = (b.volume / maxVol) * (paddingBottom - 12);
    const isUp = b.close >= b.open;
    ctx.fillStyle = isUp ? 'rgba(16, 185, 129, 0.28)' : 'rgba(244, 63, 94, 0.28)';
    ctx.fillRect(x - barWidth / 2, h - volH - 4, barWidth, volH);
  });

  // Calculate 20-period Moving Average & Bollinger Bands (2.0 std dev)
  let maPoints = [];
  let upperBands = [];
  let lowerBands = [];
  for (let i = 0; i < bars.length; i++) {
    const start = Math.max(0, i - 19);
    const sub = bars.slice(start, i + 1);
    const avg = sub.reduce((acc, c) => acc + c.close, 0) / sub.length;
    const variance = sub.reduce((acc, c) => acc + Math.pow(c.close - avg, 2), 0) / sub.length;
    const stdDev = Math.sqrt(variance);

    maPoints.push({ x: getX(i), y: getY(avg) });
    upperBands.push({ x: getX(i), y: getY(avg + 2.0 * stdDev) });
    lowerBands.push({ x: getX(i), y: getY(avg - 2.0 * stdDev) });
  }

  // Draw Bollinger Bands Shaded Envelop
  ctx.fillStyle = 'rgba(168, 85, 247, 0.06)';
  ctx.beginPath();
  upperBands.forEach((pt, i) => {
    if (i === 0) ctx.moveTo(pt.x, pt.y);
    else ctx.lineTo(pt.x, pt.y);
  });
  for (let i = lowerBands.length - 1; i >= 0; i--) {
    ctx.lineTo(lowerBands[i].x, lowerBands[i].y);
  }
  ctx.closePath();
  ctx.fill();

  // Draw Bollinger Bands Dashed Boundary Lines
  ctx.strokeStyle = 'rgba(168, 85, 247, 0.4)';
  ctx.lineWidth = 1;
  ctx.setLineDash([3, 3]);
  ctx.beginPath();
  upperBands.forEach((pt, i) => {
    if (i === 0) ctx.moveTo(pt.x, pt.y);
    else ctx.lineTo(pt.x, pt.y);
  });
  ctx.stroke();

  ctx.beginPath();
  lowerBands.forEach((pt, i) => {
    if (i === 0) ctx.moveTo(pt.x, pt.y);
    else ctx.lineTo(pt.x, pt.y);
  });
  ctx.stroke();
  ctx.setLineDash([]);

  // Draw Candlesticks
  bars.forEach((b, i) => {
    const x = getX(i);
    const isUp = b.close >= b.open;
    const color = isUp ? '#10b981' : '#f43f5e';

    // Wick
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(x, getY(b.high));
    ctx.lineTo(x, getY(b.low));
    ctx.stroke();

    // Body
    const yOpen = getY(b.open);
    const yClose = getY(b.close);
    const bodyTop = Math.min(yOpen, yClose);
    const bodyH = Math.max(2, Math.abs(yOpen - yClose));

    ctx.fillStyle = color;
    ctx.fillRect(x - barWidth / 2, bodyTop, barWidth, bodyH);
  });

  // Draw 20-period Moving Average Line
  ctx.strokeStyle = '#6366f1';
  ctx.lineWidth = 2;
  ctx.beginPath();
  maPoints.forEach((pt, i) => {
    if (i === 0) ctx.moveTo(pt.x, pt.y);
    else ctx.lineTo(pt.x, pt.y);
  });
  ctx.stroke();

  // Current Price Line Tag
  if (bars.length > 0) {
    const lastPrice = bars[bars.length - 1].close;
    const lastY = getY(lastPrice);
    ctx.strokeStyle = '#10b981';
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(10, lastY);
    ctx.lineTo(w - 50, lastY);
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.fillStyle = '#10b981';
    ctx.fillRect(w - 50, lastY - 9, 45, 18);
    ctx.fillStyle = '#000';
    ctx.font = 'bold 10px monospace';
    ctx.fillText(`$${lastPrice.toFixed(1)}`, w - 46, lastY + 4);
  }
}

function setupChartHoverTooltip() {
  const canvas = document.getElementById('mainCandleCanvas');
  const tooltip = document.getElementById('chart-tooltip');
  if (!canvas || !tooltip) return;

  canvas.addEventListener('mousemove', e => {
    if (!candleBars || candleBars.length === 0) return;
    const rect = canvas.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const w = canvas.width;

    const idx = Math.min(candleBars.length - 1, Math.max(0, Math.round(((mouseX - 15) / (w - 65)) * (candleBars.length - 1))));
    const bar = candleBars[idx];
    if (!bar) return;

    tooltip.style.display = 'block';
    tooltip.style.left = `${Math.min(rect.width - 150, mouseX + 15)}px`;
    tooltip.style.top = `${Math.max(10, e.clientY - rect.top - 50)}px`;

    const isUp = bar.close >= bar.open;
    tooltip.innerHTML = `
      <div style="font-weight: 700; color: #fff;">${bar.time}</div>
      <div style="color: ${isUp ? '#10b981' : '#f43f5e'}; font-weight: 700;">Close: $${bar.close.toFixed(2)}</div>
      <div style="color: #94a3b8;">O: $${bar.open.toFixed(2)} | H: $${bar.high.toFixed(2)} | L: $${bar.low.toFixed(2)}</div>
      <div style="color: var(--accent-cyan);">Vol: ${bar.volume.toLocaleString()}</div>
    `;
  });

  canvas.addEventListener('mouseleave', () => {
    tooltip.style.display = 'none';
  });
}

// --- OPTIONS CHAIN TABLE RENDERING ---
function renderOptionsChain(contracts, spotPrice) {
  const tbody = document.getElementById('options-chain-tbody');
  if (!tbody || !contracts) return;

  const calls = contracts.filter(c => c.type === 'call');
  const puts = contracts.filter(c => c.type === 'put');

  const strikesMap = {};
  calls.forEach(c => {
    if (!strikesMap[c.strike]) strikesMap[c.strike] = {};
    strikesMap[c.strike].call = c;
  });
  puts.forEach(p => {
    if (!strikesMap[p.strike]) strikesMap[p.strike] = {};
    strikesMap[p.strike].put = p;
  });

  const sortedStrikes = Object.keys(strikesMap).map(Number).sort((a, b) => a - b);
  tbody.innerHTML = '';

  sortedStrikes.forEach(strike => {
    const call = strikesMap[strike].call || {};
    const put = strikesMap[strike].put || {};
    const isAtm = Math.abs(strike - spotPrice) <= 2.5;

    const tr = document.createElement('tr');
    tr.style.cursor = 'pointer';
    tr.title = `Click to structure defined-risk spread at $${strike} strike`;
    tr.onclick = () => executeSpreadFromStrike(strike);
    if (isAtm) tr.style.background = 'rgba(99, 102, 241, 0.15)';

    tr.innerHTML = `
      <td style="color: #a5b4fc;">${call.delta ? call.delta.toFixed(2) : '-'}</td>
      <td>${call.vega ? call.vega.toFixed(2) : '-'}</td>
      <td>${call.implied_volatility ? call.implied_volatility.toFixed(1) + '%' : '-'}</td>
      <td style="color: #10b981;">$${call.bid ? call.bid.toFixed(2) : '-'}</td>
      <td style="color: #f43f5e;">$${call.ask ? call.ask.toFixed(2) : '-'}</td>
      <td style="background: rgba(255,255,255,0.06); font-weight: 700; color: #fff; text-align: center;">$${strike.toFixed(1)}</td>
      <td style="color: #10b981;">$${put.bid ? put.bid.toFixed(2) : '-'}</td>
      <td style="color: #f43f5e;">$${put.ask ? put.ask.toFixed(2) : '-'}</td>
      <td>${put.implied_volatility ? put.implied_volatility.toFixed(1) + '%' : '-'}</td>
      <td>${put.vega ? put.vega.toFixed(2) : '-'}</td>
      <td style="color: #fda4af;">${put.delta ? put.delta.toFixed(2) : '-'}</td>
    `;
    tbody.appendChild(tr);
  });
}

function executeSpreadFromStrike(strike) {
  switchTab('copilot');
  runAct1Pipeline();
}

// --- OPTIONS PAYOFF & SMILE CANVAS CHARTS ---
async function loadPayoffAndSmileData(symbol) {
  try {
    const res = await fetch(`${API_BASE}/api/market/payoff/${symbol}`);
    if (!res.ok) return;
    const data = await res.json();

    drawPayoffDiagram(data);
    drawSmileCurve(data.volatility_smile);
  } catch (err) {
    console.error('Payoff data error:', err);
  }
}

function drawPayoffDiagram(data) {
  const canvas = document.getElementById('payoffCanvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  const curve = data.payoff_curve || [];
  if (curve.length < 2) return;

  const minP = curve[0].price;
  const maxP = curve[curve.length - 1].price;
  const maxPnl = data.max_profit;
  const minPnl = -data.max_loss;

  const getX = p => ((p - minP) / (maxP - minP || 1)) * (w - 20) + 10;
  const getY = pnl => h / 2 - (pnl / (Math.max(Math.abs(maxPnl), Math.abs(minPnl)) || 1)) * (h / 2 - 12);

  // Zero PnL line
  ctx.strokeStyle = 'rgba(255, 255, 255, 0.15)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(10, h / 2);
  ctx.lineTo(w - 10, h / 2);
  ctx.stroke();

  // Breakeven vertical markers
  const be1X = getX(data.breakeven_lower);
  const be2X = getX(data.breakeven_upper);

  ctx.strokeStyle = 'rgba(6, 182, 212, 0.4)';
  ctx.setLineDash([2, 2]);
  ctx.beginPath();
  ctx.moveTo(be1X, 5); ctx.lineTo(be1X, h - 5);
  ctx.moveTo(be2X, 5); ctx.lineTo(be2X, h - 5);
  ctx.stroke();
  ctx.setLineDash([]);

  // Payoff line
  ctx.strokeStyle = '#10b981';
  ctx.lineWidth = 2.5;
  ctx.beginPath();
  curve.forEach((pt, idx) => {
    const x = getX(pt.price);
    const y = getY(pt.pnl);
    if (idx === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  // Spot marker
  const spotX = getX(data.spot_price);
  ctx.fillStyle = '#6366f1';
  ctx.beginPath();
  ctx.arc(spotX, getY(maxPnl), 3.5, 0, Math.PI * 2);
  ctx.fill();
}

function drawSmileCurve(smilePoints) {
  const canvas = document.getElementById('smileCanvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  if (!smilePoints || smilePoints.length < 2) return;

  const minStrike = smilePoints[0].strike;
  const maxStrike = smilePoints[smilePoints.length - 1].strike;
  const ivs = smilePoints.map(s => s.iv);
  const minIv = Math.min(...ivs) - 2;
  const maxIv = Math.max(...ivs) + 2;

  const getX = strike => ((strike - minStrike) / (maxStrike - minStrike || 1)) * (w - 20) + 10;
  const getY = iv => h - 10 - ((iv - minIv) / (maxIv - minIv || 1)) * (h - 20);

  const grad = ctx.createLinearGradient(0, 0, w, 0);
  grad.addColorStop(0, '#f43f5e');
  grad.addColorStop(0.5, '#6366f1');
  grad.addColorStop(1, '#06b6d4');

  ctx.strokeStyle = grad;
  ctx.lineWidth = 2;
  ctx.beginPath();
  smilePoints.forEach((pt, idx) => {
    const x = getX(pt.strike);
    const y = getY(pt.iv);
    if (idx === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  smilePoints.forEach(pt => {
    ctx.fillStyle = '#fff';
    ctx.beginPath();
    ctx.arc(getX(pt.strike), getY(pt.iv), 2, 0, Math.PI * 2);
    ctx.fill();
  });
}

// --- SELF-IMPROVING MEMORY & REFLECTION ENGINE ---
async function loadSelfImprovingMemory() {
  try {
    const res = await fetch(`${API_BASE}/api/memory`);
    if (!res.ok) return;
    const data = await res.json();
    memoryData = data;

    // Update Header Tab Badge
    const badge = document.getElementById('win-rate-tab-badge');
    if (badge) badge.textContent = `${data.win_rate_pct}%`;

    // Update Memory Tab KPIs
    const winRateEl = document.getElementById('mem-win-rate');
    const netPnlEl = document.getElementById('mem-net-pnl');
    const cyclesEl = document.getElementById('mem-cycles');
    if (winRateEl) winRateEl.textContent = `${data.win_rate_pct}% (${data.win_count}W / ${data.loss_count}L)`;
    if (netPnlEl) {
      netPnlEl.textContent = `${data.total_realized_pnl >= 0 ? '+' : ''}$${data.total_realized_pnl.toFixed(2)}`;
      netPnlEl.className = data.total_realized_pnl >= 0 ? 'kpi-number positive' : 'kpi-number negative';
    }
    if (cyclesEl) cyclesEl.textContent = data.learning_iterations;

    // Update Adaptive Hyperparameters
    const p = data.parameters || {};
    if (document.getElementById('mem-wing-mult')) document.getElementById('mem-wing-mult').textContent = `${p.wing_buffer_multiplier}x`;
    if (document.getElementById('mem-vrp-thresh')) document.getElementById('mem-vrp-thresh').textContent = `${p.vrp_entry_threshold} pts`;
    if (document.getElementById('mem-profit-target')) document.getElementById('mem-profit-target').textContent = `${p.profit_target_pct}%`;
    if (document.getElementById('mem-stop-loss')) document.getElementById('mem-stop-loss').textContent = `${p.stop_loss_multiplier}x`;

    // Update Regime Weights Bars
    const regimeContainer = document.getElementById('mem-regime-bars');
    if (regimeContainer && p.regime_weights) {
      regimeContainer.innerHTML = Object.entries(p.regime_weights).map(([regime, weight]) => {
        const pct = Math.min(100, Math.round((weight / 1.5) * 100));
        return `
          <div>
            <div style="display: flex; justify-content: space-between; margin-bottom: 0.15rem;">
              <span style="color: #cbd5e1;">${regime}</span>
              <span style="color: var(--accent-cyan); font-weight: 700;">${weight}x (${pct}%)</span>
            </div>
            <div style="background: rgba(255,255,255,0.05); border-radius: 4px; height: 6px; overflow: hidden;">
              <div style="background: linear-gradient(90deg, var(--accent-indigo), var(--accent-cyan)); width: ${pct}%; height: 100%;"></div>
            </div>
          </div>
        `;
      }).join('');
    }

    // Update Meta-Cognitive Reflections Feed
    const feed = document.getElementById('mem-reflection-feed');
    if (feed && data.recent_trades) {
      if (data.recent_trades.length === 0) {
        feed.innerHTML = `<div style="text-align: center; color: var(--text-muted); font-size: 0.8rem; padding: 2rem 0;">No closed trades reflected yet. Execute and harvest trades to populate.</div>`;
      } else {
        feed.innerHTML = data.recent_trades.map(t => {
          const isWin = t.status === 'WIN';
          return `
            <div style="background: rgba(255,255,255,0.03); border: 1px solid var(--border-card); border-radius: 8px; padding: 0.85rem;">
              <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.35rem;">
                <span style="font-weight: 700; color: #fff; font-size: 0.85rem;">
                  ${t.underlying} — ${t.structure.toUpperCase()} (${t.trade_id})
                </span>
                <span class="${isWin ? 'check-pass' : 'check-fail'}">
                  ${isWin ? '+' : ''}$${t.realized_pnl.toFixed(2)} (${t.status})
                </span>
              </div>
              <div style="font-size: 0.72rem; color: var(--text-muted); margin-bottom: 0.4rem;">
                Entry VRP: +${t.entry_vrp} pts | IV Rank: ${t.entry_iv_rank}% | Entry Spot: $${t.entry_price}
              </div>
              <div style="background: rgba(99, 102, 241, 0.08); border-left: 2px solid var(--accent-indigo); padding: 0.45rem 0.65rem; border-radius: 4px; margin-bottom: 0.4rem;">
                <strong style="color: #a5b4fc; font-size: 0.72rem;">🧠 Gemini Meta-Reflection:</strong>
                <p style="font-size: 0.78rem; color: #e2e8f0; margin-top: 0.15rem;">${t.reflection || 'Optimal theta convergence observed.'}</p>
              </div>
              ${t.lessons_learned && t.lessons_learned.length > 0 ? `
                <div style="font-size: 0.72rem; color: var(--accent-emerald);">
                  <strong>Learned Strategy Heuristic:</strong>
                  <ul style="padding-left: 1rem; margin-top: 0.15rem;">
                    ${t.lessons_learned.map(l => `<li>${l}</li>`).join('')}
                  </ul>
                </div>
              ` : ''}
            </div>
          `;
        }).join('');
      }
    }

  } catch (err) {
    console.error('Self-improving memory load error:', err);
  }
}

async function simulateReflection(symbol, pnl) {
  try {
    const res = await fetch(`${API_BASE}/api/memory/reflect`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        trade_id: `${symbol}-${Date.now().toString().slice(-4)}`,
        exit_price: symbol === 'NVDA' ? 128.50 : 652.10,
        realized_pnl: pnl
      })
    });
    const data = await res.json();
    // Label the reflection with whoever actually produced it - a canned template must never be
    // presented as model insight.
    const src = data.trade?.reflection_source || 'unknown';
    const srcLabel = src === 'template_fallback' ? 'deterministic template (no LLM reachable)' : src;
    toast(data.trade?.reflection || 'Reflection recorded.', {
      type: pnl >= 0 ? 'success' : 'warn',
      title: `Learned from simulated ${pnl >= 0 ? 'WIN' : 'LOSS'} on ${symbol} - via ${srcLabel}`,
      timeout: 10000,
    });
    await loadSelfImprovingMemory();
  } catch (err) {
    toastError(err.message || err, 'Reflection simulation failed');
  }
}

async function harvestProfitablePositions() {
  try {
    const res = await fetch(`${API_BASE}/api/positions/harvest`, { method: 'POST' });
    const data = await res.json();
    toastOk(`${data.harvested_count} position(s) closed at target and fed to the reflection engine.`, 'Harvest complete');
    await loadLivePositions();
    await loadSelfImprovingMemory();
    await refreshSystemStatus();
  } catch (err) {
    toastError(err.message || err, 'Harvest failed');
  }
}

// --- SYSTEM STATUS & ACCOUNT ---
async function refreshSystemStatus() {
  try {
    const res = await fetch(`${API_BASE}/api/status`);
    if (!res.ok) return;
    const data = await res.json();

    if (data.account) {
      document.getElementById('header-buying-power').textContent = `$${data.account.buying_power.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
      document.getElementById('header-portfolio-val').textContent = `$${data.account.portfolio_value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
      updateRiskGauges(data.account);
    }

    const statusPill = document.getElementById('system-status-pill');
    const statusText = document.getElementById('system-status-text');
    if (data.kill_switch.is_halted) {
      statusPill.className = 'status-pill halted';
      statusText.textContent = 'HALTED (KILL SWITCH)';
    } else {
      statusPill.className = 'status-pill';
      statusText.textContent = 'HEALTHY';
    }

    renderProviderChip(data.llm);

    const autoBtn = document.getElementById('btn-auto-trader');
    if (autoBtn && data.autonomous_trader) {
      if (data.autonomous_trader.is_running) {
        autoBtn.textContent = `🟢 Auto-Trading: ON (${data.autonomous_trader.trades_executed} trades)`;
        autoBtn.style.background = 'rgba(6, 182, 212, 0.2)';
        autoBtn.style.color = '#fff';
      } else {
        autoBtn.textContent = '🤖 Auto-Trading: OFF';
        autoBtn.style.background = 'transparent';
        autoBtn.style.color = 'var(--accent-cyan)';
      }
    }
  } catch (err) {
    console.error('Status refresh error:', err);
  }
}

function updateRiskGauges(account) {
  const delta = account.portfolio_delta || 0.0;
  const deltaPct = Math.min(100, Math.round((Math.abs(delta) / 250.0) * 100));
  const deltaEl = document.getElementById('gauge-delta-val');
  const deltaBar = document.getElementById('gauge-delta-bar');
  if (deltaEl) deltaEl.textContent = `${delta >= 0 ? '+' : ''}${delta.toFixed(1)} Δ`;
  if (deltaBar) deltaBar.setAttribute('stroke-dasharray', `${deltaPct}, 100`);

  const vega = account.portfolio_vega || 0.0;
  const vegaPct = Math.min(100, Math.round((Math.abs(vega) / 250.0) * 100));
  const vegaEl = document.getElementById('gauge-vega-val');
  const vegaBar = document.getElementById('gauge-vega-bar');
  if (vegaEl) vegaEl.textContent = `${vega.toFixed(1)} ν`;
  if (vegaBar) vegaBar.setAttribute('stroke-dasharray', `${vegaPct}, 100`);

  const bp = account.buying_power || 1.0;
  const pv = account.portfolio_value || 1.0;
  const cushionPct = Math.min(100, Math.round((bp / pv) * 100));
  const cushionEl = document.getElementById('gauge-cushion-val');
  const cushionBar = document.getElementById('gauge-cushion-bar');
  if (cushionEl) cushionEl.textContent = `${cushionPct}%`;
  if (cushionBar) cushionBar.setAttribute('stroke-dasharray', `${cushionPct}, 100`);
}

async function toggleAutoTrading() {
  try {
    await fetch(`${API_BASE}/api/autonomous/toggle`, { method: 'POST' });
    await refreshSystemStatus();
  } catch (err) {
    toastError(err.message || err, 'Could not toggle autonomous trading');
  }
}

// --- LIVE BROKER POSITIONS & ORDERS ---
async function loadLivePositions() {
  try {
    const res = await fetch(`${API_BASE}/api/positions`);
    if (!res.ok) return;
    const data = await res.json();
    const positions = data.positions || [];

    const badge = document.getElementById('pos-count-badge');
    if (badge) badge.textContent = positions.length;

    const tbody = document.getElementById('live-positions-tbody');
    if (!tbody) return;

    if (positions.length === 0) {
      tbody.innerHTML = `<tr><td colspan="8" style="text-align: center; color: var(--text-muted); padding: 1.5rem;">No open positions held on Alpaca Paper. Run a trade in the Terminal tab.</td></tr>`;
      return;
    }

    tbody.innerHTML = positions.map(p => `
      <tr>
        <td style="font-weight: 700; color: #fff;">${p.symbol}</td>
        <td><span class="card-badge badge-indigo">${p.asset_class.toUpperCase()}</span></td>
        <td style="font-family: var(--font-mono); color: ${p.qty > 0 ? '#10b981' : '#f43f5e'}; font-weight: 700;">${p.qty > 0 ? '+' : ''}${p.qty}</td>
        <td>$${p.current_price.toFixed(2)}</td>
        <td>$${p.market_value.toFixed(2)}</td>
        <td>$${p.cost_basis.toFixed(2)}</td>
        <td style="color: ${p.unrealized_pl >= 0 ? '#10b981' : '#f43f5e'}; font-weight: 700;">
          ${p.unrealized_pl >= 0 ? '+' : ''}$${p.unrealized_pl.toFixed(2)}
        </td>
        <td>
          <button class="btn btn-danger" style="padding: 0.15rem 0.45rem; font-size: 0.7rem;" onclick="closeLivePosition('${p.symbol}')">
            ⚡ Close
          </button>
        </td>
      </tr>
    `).join('');

    // Load recent orders
    const ordersRes = await fetch(`${API_BASE}/api/orders`);
    if (ordersRes.ok) {
      const ordData = await ordersRes.json();
      const ordersTbody = document.getElementById('live-orders-tbody');
      if (ordersTbody && ordData.orders) {
        if (ordData.orders.length === 0) {
          ordersTbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 1rem;">No recent broker orders recorded.</td></tr>`;
        } else {
          ordersTbody.innerHTML = ordData.orders.map(o => `
            <tr>
              <td style="font-family: var(--font-mono); font-size: 0.72rem; color: var(--accent-cyan);">${o.id.substring(0, 12)}...</td>
              <td style="font-weight: 700; color: #fff;">${o.symbol}</td>
              <td>${o.qty}</td>
              <td><span style="color: ${o.side.includes('buy') ? '#10b981' : '#f43f5e'}; font-weight: 700;">${o.side.toUpperCase()}</span></td>
              <td>${o.type}</td>
              <td><span class="check-pass">${o.status.toUpperCase()}</span></td>
              <td style="font-size: 0.72rem; color: var(--text-muted);">${o.submitted_at ? new Date(o.submitted_at).toLocaleTimeString() : '-'}</td>
            </tr>
          `).join('');
        }
      }
    }

  } catch (err) {
    console.error('Error fetching live positions:', err);
  }
}

async function closeLivePosition(symbol) {
  if (!confirm(`Are you sure you want to close position for ${symbol} at market on Alpaca?`)) return;
  try {
    const res = await fetch(`${API_BASE}/api/positions/${encodeURIComponent(symbol)}`, { method: 'DELETE' });
    const data = await res.json();
    toastOk(`Close order submitted for ${symbol}.`, 'Position closing');
    await loadLivePositions();
    await loadSelfImprovingMemory();
    await refreshSystemStatus();
  } catch (err) {
    toastError(err.message || err, 'Could not close position');
  }
}

// --- AI COPILOT CHAT TERMINAL ---
async function sendCopilotMessage() {
  const input = document.getElementById('copilot-user-input');
  const message = input.value.trim();
  if (!message) return;

  const history = document.getElementById('copilot-chat-history');
  
  // User message
  const userMsg = document.createElement('div');
  userMsg.className = 'chat-msg user';
  userMsg.innerHTML = `<strong>You:</strong><p style="margin-top: 0.2rem;">${message}</p>`;
  history.appendChild(userMsg);
  input.value = '';
  history.scrollTop = history.scrollHeight;

  // Bot loading
  const botMsg = document.createElement('div');
  botMsg.className = 'chat-msg bot';
  botMsg.innerHTML = `<strong>GlassBox AI Copilot:</strong><p style="margin-top: 0.2rem;">Analyzing volatility surface, self-improving memory priors, and risk constraints...</p>`;
  history.appendChild(botMsg);
  history.scrollTop = history.scrollHeight;

  try {
    const res = await fetch(`${API_BASE}/api/agent/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, symbol: getActiveSymbol() })
    });
    const data = await res.json();
    botMsg.innerHTML = `<strong>GlassBox AI Copilot:</strong><p style="margin-top: 0.2rem; white-space: pre-wrap;">${data.reply}</p>`;
    history.scrollTop = history.scrollHeight;
  } catch (err) {
    botMsg.innerHTML = `<strong>GlassBox AI Copilot:</strong><p style="margin-top: 0.2rem; color: var(--accent-rose);">Copilot error: ${err}</p>`;
  }
}

// --- STRATEGY EXECUTION PIPELINE ---
async function runAct1Pipeline() {
  const symbol = getActiveSymbol();
  const runBtn = document.getElementById('btn-act1-run');
  if (runBtn) {
    runBtn.disabled = true;
    runBtn.textContent = '⏳ Processing...';
  }

  try {
    // Arena-gated path: regime -> strategy tournament -> evidence-backed proposal -> risk kernel.
    const dryRun = document.getElementById('studio-dry-run')?.checked ?? false;
    const data = await apiFetch('/api/pipeline/arena_run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol, dry_run: dryRun })
    });

    renderAct1Results(data);
    renderStudioArenaContext(data);
    await loadAuditSnapshots();
    await refreshSystemStatus();
    await loadLivePositions();
    await loadSelfImprovingMemory();
    loadPayoffAndSmileData(symbol);
  } catch (err) {
    toastError(err.message || err, 'Pipeline failed');
  } finally {
    if (runBtn) {
      runBtn.disabled = false;
      runBtn.textContent = `▶ Generate & Execute Strategy (${symbol})`;
    }
  }
}

async function runGuaranteedRejection() {
  const btn = document.getElementById('btn-act1-reject');
  if (btn) {
    btn.disabled = true;
    btn.textContent = '⏳ Testing Rejection...';
  }

  try {
    const res = await fetch(`${API_BASE}/api/pipeline/guaranteed_rejection`, {
      method: 'POST'
    });
    const data = await res.json();
    renderAct1Results(data);
    await loadAuditSnapshots();
    await refreshSystemStatus();
  } catch (err) {
    toastError(err.message || err, 'Rejection scenario failed');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = '⚠️ Test Guaranteed Rejection (Vega Breach)';
    }
  }
}

function renderAct1Results(data) {
  document.getElementById('intent-json').textContent = JSON.stringify(data.intent, null, 2);
  document.getElementById('intent-rationale').textContent = data.intent.rationale || 'No rationale';

  const statusText = document.getElementById('decision-status-text');
  const orderPill = document.getElementById('alpaca-order-pill');
  const rejectionBox = document.getElementById('rejection-reasons-box');
  const rejectionText = document.getElementById('rejection-reasons-text');

  if (data.decision.decision === 'APPROVED') {
    // order_id is only ever set when the executor actually submitted an order to Alpaca (see
    // kernel/executor.py). It's null both for real submission failures and for the scripted
    // guaranteed-rejection scenario (which never calls the executor at all) - never claim a
    // fill happened when order_id is missing, that would be fabricating a broker confirmation.
    if (data.order_id) {
      statusText.textContent = 'APPROVED (SUBMITTED TO ALPACA)';
      orderPill.textContent = data.order_id;
    } else if (data.dry_run) {
      statusText.textContent = 'APPROVED (DRY RUN - NOT SUBMITTED)';
      orderPill.textContent = 'DRY RUN';
    } else {
      statusText.textContent = 'APPROVED (NOT SUBMITTED)';
      orderPill.textContent = 'NONE (NOT SUBMITTED)';
    }
    statusText.style.color = 'var(--accent-emerald)';
    rejectionBox.style.display = 'none';
  } else {
    statusText.textContent = 'REJECTED (BLOCKED BY KERNEL)';
    statusText.style.color = 'var(--accent-rose)';
    orderPill.textContent = 'NONE (EXECUTION BLOCKED)';
    rejectionBox.style.display = 'block';
    rejectionText.textContent = data.decision.reasons?.join(', ') || 'Risk limits exceeded';
  }

  const tbody = document.getElementById('risk-checks-body');
  if (tbody && data.decision.kernel_checks) {
    tbody.innerHTML = data.decision.kernel_checks.map(c => `
      <tr>
        <td style="font-weight: 500;">${c.check}</td>
        <td style="font-family: var(--font-mono); color: #fff;">${typeof c.value === 'number' ? c.value.toFixed(2) : c.value}</td>
        <td style="font-family: var(--font-mono); color: var(--text-muted);">${typeof c.limit === 'number' ? c.limit.toFixed(2) : c.limit}</td>
        <td><span class="${c.pass ? 'check-pass' : 'check-fail'}">${c.pass ? 'PASS' : 'FAIL'}</span></td>
      </tr>
    `).join('');
  }
}

// --- SHA-256 AUDIT TRAIL ---
async function loadAuditSnapshots() {
  try {
    const res = await fetch(`${API_BASE}/api/audit/snapshots`);
    if (!res.ok) return;
    const data = await res.json();
    // The endpoint returns a bare JSON array, not {snapshots: [...]}.
    auditSnapshots = Array.isArray(data) ? data : (data.snapshots || []);

    const container = document.getElementById('audit-list-container');
    if (!container) return;

    if (auditSnapshots.length === 0) {
      container.innerHTML = `<div style="text-align: center; color: var(--text-muted); font-size: 0.8rem; padding: 2rem 0;">No audit records generated yet. Run a trade in the Terminal or Strategy Studio.</div>`;
      return;
    }

    container.innerHTML = '';
    auditSnapshots.forEach(s => {
      const intent = s.intent_data || {};
      const isApproved = s.kernel_decision === 'APPROVED';
      const card = document.createElement('div');
      card.style.background = 'rgba(255,255,255,0.03)';
      card.style.border = '1px solid var(--border-card)';
      card.style.borderRadius = '8px';
      card.style.padding = '0.85rem';
      card.style.marginBottom = '0.65rem';

      card.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.35rem;">
          <span style="font-weight: 700; color: #fff; font-size: 0.85rem;">
            ${intent.underlying || '-'} — ${(intent.structure || '').toUpperCase()} (x${intent.size ?? '-'})
          </span>
          <span class="${isApproved ? 'check-pass' : 'check-fail'}">${s.kernel_decision}</span>
        </div>
        <div style="font-family: var(--font-mono); font-size: 0.72rem; color: var(--accent-cyan); margin-bottom: 0.4rem; word-break: break-all;">
          ${s.hash}
        </div>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span style="font-size: 0.72rem; color: var(--text-muted);">
            ${new Date(s.captured_at).toLocaleTimeString()}
          </span>
          <button class="btn btn-secondary" style="padding: 0.2rem 0.55rem; font-size: 0.72rem;" onclick="verifySnapshot('${s.snapshot_id}')">
            🔍 Verify Trade
          </button>
        </div>
      `;
      container.appendChild(card);
    });

  } catch (err) {
    console.error('Audit trail load error:', err);
  }
}

async function verifySnapshot(idVal) {
  const modal = document.getElementById('replay-modal');
  const body = document.getElementById('replay-modal-body');
  body.innerHTML = '<div style="text-align: center; padding: 2rem; color: var(--text-muted);">Recomputing SHA-256 hash & cross-checking numeric claims...</div>';
  modal.style.display = 'flex';

  try {
    const res = await fetch(`${API_BASE}/api/audit/verify/${idVal}`, { method: 'POST' });
    const data = await res.json();

    const claimsHtml = data.rationale_claims.map(c => `
      <div style="margin-bottom: 0.35rem; font-size: 0.82rem;">
        <span style="color: ${c.supported ? 'var(--accent-emerald)' : 'var(--accent-rose)'}; font-weight: 700;">
          ${c.supported ? '✓ SUPPORTED' : '✗ DISCREPANCY'}
        </span>: ${c.claim} (Actual in Snapshot: ${c.actual})
      </div>
    `).join('');

    body.innerHTML = `
      <div style="margin-bottom: 1rem;">
        <div style="font-size: 0.8rem; color: var(--text-muted);">Snapshot ID: <strong style="color: #fff;">${data.snapshot_id}</strong></div>
        <div style="font-size: 0.8rem; color: var(--text-muted);">Order ID: <strong style="color: var(--accent-cyan);">${data.order_id}</strong></div>
      </div>

      <div style="background: rgba(255,255,255,0.03); border: 1px solid var(--border-card); border-radius: 8px; padding: 0.85rem; margin-bottom: 0.75rem;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.35rem;">
          <strong style="font-size: 0.85rem;">1. Cryptographic Hash Integrity Check</strong>
          <span class="${data.integrity ? 'check-pass' : 'check-fail'}">
            ${data.integrity ? '✓ VERIFIED (MATCH)' : '✗ FAILED (TAMPER DETECTED)'}
          </span>
        </div>
        <div style="font-family: var(--font-mono); font-size: 0.7rem; color: var(--accent-cyan);">Stored:     ${data.stored_hash}</div>
        <div style="font-family: var(--font-mono); font-size: 0.7rem; color: #a5b4fc;">Recomputed: ${data.recomputed_hash}</div>
      </div>

      <div style="background: rgba(255,255,255,0.03); border: 1px solid var(--border-card); border-radius: 8px; padding: 0.85rem; margin-bottom: 1rem;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.35rem;">
          <strong style="font-size: 0.85rem;">2. Rationale Numeric Claims Supported</strong>
          <span class="${data.condition_supported ? 'check-pass' : 'check-fail'}">
            ${data.condition_supported ? '✓ SUPPORTED' : '✗ DISCREPANCY'}
          </span>
        </div>
        <div>${claimsHtml}</div>
      </div>
    `;

  } catch (err) {
    body.innerHTML = `<div style="color: var(--accent-rose);">Verification error: ${err}</div>`;
  }
}

function closeReplayModal() {
  document.getElementById('replay-modal').style.display = 'none';
}

function closeConfigModal() {
  document.getElementById('config-modal').style.display = 'none';
}

// --- QUANT BACKTEST TAB ---
async function executeBacktestTabRun() {
  const symbol = document.getElementById('backtest-tab-ticker-input').value.trim().toUpperCase() || 'SPY';
  const days = parseInt(document.getElementById('backtest-tab-days-select').value) || 180;
  const container = document.getElementById('backtest-tab-results-container');

  try {
    const res = await fetch(`${API_BASE}/api/backtest/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol, days, strategy: 'iron_condor', trials: 12 })
    });
    const data = await res.json();

    container.style.display = 'block';
    document.getElementById('tab-bt-win-rate').textContent = `${data.win_rate_pct}%`;
    document.getElementById('tab-bt-total-ret').textContent = `${data.total_return_pct >= 0 ? '+' : ''}${data.total_return_pct}%`;
    document.getElementById('tab-bt-max-dd').textContent = `-${data.max_drawdown_pct}%`;
    document.getElementById('tab-bt-dsr').textContent = `${data.deflated_sharpe_ratio} (${(data.deflated_sharpe_ratio * 100).toFixed(1)}%)`;
    document.getElementById('tab-bt-verdict').textContent = data.overfitting_risk_verdict;
    document.getElementById('backtest-tab-json-preview').textContent = JSON.stringify(data, null, 2);

    drawBacktestTabEquityCurve(data.equity_curve);
  } catch (err) {
    toastError(err.message || err, 'Backtest failed');
  }
}

function drawBacktestTabEquityCurve(equityCurve) {
  const canvas = document.getElementById('backtestTabEquityCanvas');
  if (!canvas || !equityCurve || equityCurve.length < 2) return;
  const ctx = canvas.getContext('2d');
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  const minEq = Math.min(...equityCurve) * 0.98;
  const maxEq = Math.max(...equityCurve) * 1.02;

  const getX = idx => (idx / (equityCurve.length - 1)) * (w - 20) + 10;
  const getY = val => h - 15 - ((val - minEq) / (maxEq - minEq || 1)) * (h - 30);

  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, 'rgba(16, 185, 129, 0.35)');
  grad.addColorStop(1, 'rgba(16, 185, 129, 0.0)');

  ctx.fillStyle = grad;
  ctx.beginPath();
  ctx.moveTo(getX(0), h - 10);
  equityCurve.forEach((val, idx) => {
    ctx.lineTo(getX(idx), getY(val));
  });
  ctx.lineTo(getX(equityCurve.length - 1), h - 10);
  ctx.closePath();
  ctx.fill();

  ctx.strokeStyle = '#10b981';
  ctx.lineWidth = 2.5;
  ctx.beginPath();
  equityCurve.forEach((val, idx) => {
    const x = getX(idx);
    const y = getY(val);
    if (idx === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

// --- ACT 2: TRADETRAP DEFENSE (FAULT INJECTOR + RECONCILIATION) ---
async function injectPhantomFault() {
  try {
    await fetch(`${API_BASE}/api/act2/inject_fault`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol: 'AAPL', qty: 9 })
    });
    await loadAct2Status();
  } catch (err) {
    toastError(err.message || err, 'Fault injection failed');
  }
}

async function clearPhantomFault() {
  try {
    await fetch(`${API_BASE}/api/act2/clear_fault`, { method: 'POST' });
    await loadAct2Status();
  } catch (err) {
    toastError(err.message || err, 'Could not clear fault');
  }
}

async function triggerReconcileNow() {
  try {
    await fetch(`${API_BASE}/api/act2/reconcile_now`, { method: 'POST' });
    await loadAct2Status();
    await loadReconciliationLog();
    await refreshSystemStatus();
  } catch (err) {
    toastError(err.message || err, 'Reconciliation failed');
  }
}

async function resetKillSwitchAction() {
  try {
    await fetch(`${API_BASE}/api/act2/reset_killswitch`, { method: 'POST' });
    await loadAct2Status();
    await refreshSystemStatus();
  } catch (err) {
    toastError(err.message || err, 'Could not reset kill switch');
  }
}

async function loadAct2Status() {
  try {
    const res = await fetch(`${API_BASE}/api/status`);
    if (!res.ok) return;
    const data = await res.json();

    const fi = data.fault_injector || {};
    const faultBadge = document.getElementById('act2-fault-badge');
    if (faultBadge) {
      faultBadge.textContent = fi.active ? 'ACTIVE' : 'INACTIVE';
      faultBadge.className = `card-badge ${fi.active ? 'badge-rose' : 'badge-emerald'}`;
    }
    const faultActive = document.getElementById('act2-fault-active');
    if (faultActive) faultActive.textContent = fi.active ? 'true' : 'false';
    const faultPositions = document.getElementById('act2-fault-positions');
    if (faultPositions) faultPositions.textContent = JSON.stringify(fi.corrupted_positions || {});
    const faultDesc = document.getElementById('act2-fault-desc');
    if (faultDesc) faultDesc.textContent = fi.description || '';

    const ks = data.kill_switch || {};
    const ksBadge = document.getElementById('act2-killswitch-badge');
    if (ksBadge) {
      ksBadge.textContent = ks.is_halted ? 'HALTED' : 'NORMAL';
      ksBadge.className = `card-badge ${ks.is_halted ? 'badge-rose' : 'badge-emerald'}`;
    }
    const haltReason = document.getElementById('act2-halt-reason');
    if (haltReason) haltReason.textContent = ks.halt_reason || '-';
    const haltedAt = document.getElementById('act2-halted-at');
    if (haltedAt) haltedAt.textContent = ks.halted_at ? new Date(ks.halted_at).toLocaleString() : '-';
    const triggerSource = document.getElementById('act2-trigger-source');
    if (triggerSource) triggerSource.textContent = ks.trigger_source || '-';
  } catch (err) {
    console.error('Act2 status load error:', err);
  }
}

async function loadReconciliationLog() {
  const container = document.getElementById('act2-reconciliation-log');
  if (!container) return;
  try {
    const res = await fetch(`${API_BASE}/api/act2/reconciliation_log`);
    if (!res.ok) return;
    const events = await res.json();

    if (!events || events.length === 0) {
      container.innerHTML = '<div style="text-align: center; color: var(--text-muted); font-size: 0.8rem; padding: 2rem 0;">No reconciliation events yet. Click "Reconcile Now" or wait for the 30s background loop.</div>';
      return;
    }

    container.innerHTML = events.map(e => `
      <div style="background: rgba(0,0,0,0.25); border: 1px solid ${e.match ? 'var(--border-card)' : 'var(--accent-rose)'}; border-radius: 6px; padding: 0.7rem;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.35rem;">
          <span style="font-size: 0.72rem; color: var(--text-muted);">${new Date(e.checked_at).toLocaleTimeString()}</span>
          <span class="${e.match ? 'check-pass' : 'check-fail'}">${e.match ? 'MATCH' : 'MISMATCH'}</span>
        </div>
        <div style="font-size: 0.75rem; color: var(--text-secondary); margin-bottom: 0.3rem;">
          Action: <strong style="color: #fff;">${e.action_taken}</strong>${e.fault_injected ? ' <span style="color: var(--accent-rose);">(fault injected)</span>' : ''}
        </div>
        <div style="font-family: var(--font-mono); font-size: 0.68rem; color: var(--text-muted);">Believed: ${JSON.stringify(e.believed_positions)}</div>
        <div style="font-family: var(--font-mono); font-size: 0.68rem; color: var(--text-muted);">Actual: ${JSON.stringify(e.actual_positions)}</div>
      </div>
    `).join('');
  } catch (err) {
    console.error('Reconciliation log load error:', err);
  }
}

// --- ACT 3: BLINDFOLD VRP EXPERIMENT ---
async function runBlindfoldSingle() {
  const symbol = getActiveSymbol();
  const label = document.getElementById('blindfold-symbol-label');
  if (label) label.textContent = symbol;

  const btn = document.getElementById('btn-blindfold-single');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Running normal + anonymized reasoning...'; }

  const container = document.getElementById('blindfold-single-result');
  container.style.display = 'block';
  container.innerHTML = '<div class="glass-panel" style="text-align: center; color: var(--text-muted); padding: 1.5rem;">Running normal + anonymized reasoning comparison...</div>';

  try {
    const res = await fetch(`${API_BASE}/api/act3/blindfold`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol })
    });
    const data = await res.json();
    renderBlindfoldSingle(data);
  } catch (err) {
    container.innerHTML = `<div class="glass-panel" style="color: var(--accent-rose); padding: 1.25rem;">Blindfold comparison error: ${err}</div>`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = `▶ Run Single Comparison (${symbol})`; }
  }
}

function renderBlindfoldSingle(data) {
  const container = document.getElementById('blindfold-single-result');
  container.innerHTML = `
    <div class="glass-panel" style="padding: 1.25rem;">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem;">
        <h4 style="font-size: 0.95rem; font-weight: 700; color: #fff;">Comparison: ${data.underlying} vs. ${data.pseudonym}</h4>
        <span class="card-badge ${data.material_match ? 'badge-emerald' : 'badge-rose'}">${data.material_match ? '✓ MATERIAL MATCH' : '✗ MATERIAL MISMATCH'}</span>
      </div>
      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.85rem;">
        <div style="background: rgba(6,182,212,0.06); border: 1px solid var(--border-card); border-radius: 8px; padding: 0.85rem;">
          <div style="font-size: 0.7rem; color: var(--accent-cyan); font-weight: 700; text-transform: uppercase; margin-bottom: 0.5rem;">Normal (${data.underlying})</div>
          <div style="font-size: 0.85rem; margin-bottom: 0.3rem;">Structure: <strong style="color: #fff;">${data.normal_structure}</strong></div>
          <div style="font-size: 0.85rem; margin-bottom: 0.5rem;">Conviction: <strong style="color: #fff;">${data.normal_conviction}</strong></div>
          <div style="font-size: 0.75rem; color: var(--text-muted);">${data.normal_rationale}</div>
        </div>
        <div style="background: rgba(168,85,247,0.06); border: 1px solid var(--border-card); border-radius: 8px; padding: 0.85rem;">
          <div style="font-size: 0.7rem; color: #c4b5fd; font-weight: 700; text-transform: uppercase; margin-bottom: 0.5rem;">Anonymized (${data.pseudonym})</div>
          <div style="font-size: 0.85rem; margin-bottom: 0.3rem;">Structure: <strong style="color: #fff;">${data.anonymized_structure}</strong></div>
          <div style="font-size: 0.85rem; margin-bottom: 0.5rem;">Conviction: <strong style="color: #fff;">${data.anonymized_conviction}</strong></div>
          <div style="font-size: 0.75rem; color: var(--text-muted);">${data.anonymized_rationale}</div>
        </div>
      </div>
      <div style="display: flex; gap: 1rem; margin-top: 0.85rem; font-size: 0.78rem; flex-wrap: wrap;">
        <span>Structure Match: <span class="${data.structure_match ? 'check-pass' : 'check-fail'}">${data.structure_match ? 'PASS' : 'FAIL'}</span></span>
        <span>Conviction Match (±0.10): <span class="${data.conviction_match ? 'check-pass' : 'check-fail'}">${data.conviction_match ? 'PASS' : 'FAIL'}</span></span>
        <span>Regime Tags Match: <span class="${data.tags_match ? 'check-pass' : 'check-fail'}">${data.tags_match ? 'PASS' : 'FAIL'}</span></span>
      </div>
    </div>
  `;
}

async function runBlindfoldBatch() {
  const btn = document.getElementById('btn-blindfold-batch');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Running 5-asset batch (this calls the LLM 10x)...'; }

  const container = document.getElementById('blindfold-batch-result');
  container.style.display = 'block';
  container.innerHTML = '<div class="glass-panel" style="text-align: center; color: var(--text-muted); padding: 1.5rem;">Running multi-asset blindfold batch (SPY, QQQ, AAPL, NVDA, TSLA)...</div>';

  try {
    const res = await fetch(`${API_BASE}/api/act3/blindfold_batch`, { method: 'POST' });
    const data = await res.json();
    renderBlindfoldBatch(data);
  } catch (err) {
    container.innerHTML = `<div class="glass-panel" style="color: var(--accent-rose); padding: 1.25rem;">Batch experiment error: ${err}</div>`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '🧮 Run Batch Experiment (5 Assets)'; }
  }
}

function renderBlindfoldBatch(data) {
  const container = document.getElementById('blindfold-batch-result');
  const rows = (data.details || []).map(d => `
    <tr>
      <td>${d.underlying} &rarr; ${d.pseudonym}</td>
      <td>${d.normal_structure}</td>
      <td>${d.anonymized_structure}</td>
      <td style="font-family: var(--font-mono);">${d.normal_conviction}</td>
      <td style="font-family: var(--font-mono);">${d.anonymized_conviction}</td>
      <td><span class="${d.material_match ? 'check-pass' : 'check-fail'}">${d.material_match ? 'MATCH' : 'MISMATCH'}</span></td>
    </tr>
  `).join('');

  container.innerHTML = `
    <div class="glass-panel" style="padding: 1.25rem;">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem;">
        <h4 style="font-size: 0.95rem; font-weight: 700; color: #fff;">Batch Agreement Rate</h4>
        <span class="card-badge ${data.agreement_rate_pct >= 80 ? 'badge-emerald' : 'badge-rose'}">${data.verdict}</span>
      </div>
      <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.65rem; margin-bottom: 1rem;">
        <div class="kpi-box">
          <div class="kpi-title">Agreement Rate</div>
          <div class="kpi-number ${data.agreement_rate_pct >= 80 ? 'positive' : 'negative'}">${data.agreement_rate_pct}%</div>
        </div>
        <div class="kpi-box">
          <div class="kpi-title">Matched Samples</div>
          <div class="kpi-number highlight">${data.matched_samples} / ${data.total_samples}</div>
        </div>
        <div class="kpi-box">
          <div class="kpi-title">Total Samples</div>
          <div class="kpi-number highlight">${data.total_samples}</div>
        </div>
      </div>
      <div style="overflow-x: auto;">
        <table class="options-chain-table" style="font-size: 0.8rem;">
          <thead>
            <tr><th>Asset &rarr; Pseudonym</th><th>Normal Structure</th><th>Anon. Structure</th><th>Normal Conv.</th><th>Anon. Conv.</th><th>Verdict</th></tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </div>
  `;
}

// --- STRATEGY ARENA: REGIME ENGINE + TOURNAMENT ---
const REGIME_COLORS = {
  HIGH_VOL_RANGE: 'var(--accent-emerald)',
  HIGH_VOL_TREND: 'var(--accent-cyan)',
  LOW_VOL_TREND: 'var(--accent-cyan)',
  LOW_VOL_RANGE: 'var(--text-muted)',
  VOL_EXPANSION: 'var(--accent-rose)',
  EVENT_RISK: 'var(--accent-rose)',
};

const SIGNAL_LABELS = {
  iv_rank: 'IV Rank (52w proxy)',
  realized_vol: 'Realized Vol 30d',
  vrp: 'VRP (IV - RV)',
  vix: 'VIX (CBOE)',
  trend_20d_pct: 'Trend, 20 sessions',
  price_vs_ema20_pct: 'Spot vs EMA(20)',
  rv_short: 'Realized Vol 10d',
  rv_long: 'Realized Vol 60d',
  rv_expansion_ratio: 'Vol expansion (10d/60d)',
  earnings_days: 'Days to earnings',
  is_earnings_blackout: 'Earnings blackout',
};

const SIGNAL_UNITS = {
  iv_rank: '%', realized_vol: '%', vrp: ' pts', trend_20d_pct: '%',
  price_vs_ema20_pct: '%', rv_short: '%', rv_long: '%',
};

function renderRegimePanel(regime) {
  const badge = document.getElementById('arena-regime-badge');
  badge.textContent = regime.regime;
  badge.className = 'card-badge ' +
    ((regime.regime === 'EVENT_RISK' || regime.regime === 'VOL_EXPANSION') ? 'badge-rose' : 'badge-emerald');

  const label = document.getElementById('arena-regime-label');
  label.textContent = regime.label;
  label.style.color = REGIME_COLORS[regime.regime] || 'var(--accent-cyan)';

  document.getElementById('arena-regime-desc').textContent = regime.description;
  document.getElementById('arena-regime-conf').textContent = regime.confidence_pct + '%';
  document.getElementById('arena-regime-complete').textContent =
    regime.data_complete ? '' : 'some signals unavailable - confidence reduced';

  document.getElementById('arena-regime-tags').innerHTML = (regime.tags || [])
    .map(t => '<span class="badge-tag" style="cursor: default; font-size: 0.65rem;">' + t + '</span>')
    .join('');

  const rows = Object.entries(regime.signals || {}).map(([key, val]) => {
    const lbl = SIGNAL_LABELS[key] || key;
    let shown;
    if (val === null || val === undefined) {
      shown = '<span style="color: var(--accent-rose);">UNAVAILABLE</span>';
    } else if (typeof val === 'boolean') {
      shown = val ? '<span style="color: var(--accent-rose);">yes</span>' : 'no';
    } else if (typeof val === 'number') {
      shown = val + (SIGNAL_UNITS[key] || '');
    } else {
      shown = val;
    }
    return '<tr><td style="color: var(--text-muted); padding: 0.18rem 0;">' + lbl +
           '</td><td style="text-align: right; color: #fff;">' + shown + '</td></tr>';
  }).join('');
  document.getElementById('arena-regime-signals').innerHTML = '<tbody>' + rows + '</tbody>';
}

async function loadRegimePanel() {
  try {
    const symbol = getActiveSymbol();
    const res = await fetch(`${API_BASE}/api/regime/${symbol}`);
    if (!res.ok) return;
    renderRegimePanel(await res.json());
  } catch (err) {
    console.error('Regime load error:', err);
  }
}

async function runStrategyArena() {
  const btn = document.getElementById('btn-arena-run');
  const symbol = getActiveSymbol();
  const lookback = parseInt(document.getElementById('arena-lookback-select').value) || 365;
  if (btn) { btn.disabled = true; btn.textContent = 'Replaying every variant on real history...'; }

  const tbody = document.getElementById('arena-leaderboard-body');
  tbody.innerHTML = '<tr><td colspan="10" style="text-align:center; color: var(--text-muted); padding: 1.5rem;">' +
    'Replaying ' + symbol + ' history for every strategy variant...</td></tr>';

  try {
    const res = await fetch(`${API_BASE}/api/arena/score`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol, lookback_days: lookback, refresh: true })
    });
    const data = await res.json();
    renderRegimePanel(data.regime);
    renderArenaLeaderboard(data);
  } catch (err) {
    tbody.innerHTML = '<tr><td colspan="10" style="color: var(--accent-rose); padding: 1rem;">Arena error: ' + err + '</td></tr>';
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Run Tournament'; }
  }
}

function renderArenaLeaderboard(data) {
  const tbody = document.getElementById('arena-leaderboard-body');
  const scores = data.scores || [];

  if (!scores.length) {
    tbody.innerHTML = '<tr><td colspan="10" style="text-align:center; color: var(--text-muted); padding: 1.5rem;">No variants scored.</td></tr>';
    return;
  }

  tbody.innerHTML = scores.map((s, idx) => {
    const isChampion = s.variant_id === data.champion_id;
    const rowBg = isChampion ? 'background: rgba(16,185,129,0.10);' : (s.eligible ? '' : 'opacity: 0.55;');
    const expColor = s.expectancy_pct >= 0 ? 'var(--accent-emerald)' : 'var(--accent-rose)';
    const noteHtml = s.note ? '<div style="font-size:0.66rem; color: var(--text-muted);">' + s.note + '</div>' : '';
    return '<tr style="' + rowBg + '">' +
      '<td style="font-family: var(--font-mono);">' + (isChampion ? '&#128081;' : (idx + 1)) + '</td>' +
      '<td style="font-weight: 500;">' + s.name + noteHtml + '</td>' +
      '<td><span class="' + (s.eligible ? 'check-pass' : 'check-fail') + '">' +
        (s.eligible ? 'ELIGIBLE' : 'EXCLUDED') + '</span></td>' +
      '<td style="font-family: var(--font-mono);">' + s.trades + '</td>' +
      '<td style="font-family: var(--font-mono);">' + s.win_rate_pct + '%</td>' +
      '<td style="font-family: var(--font-mono); color: ' + expColor + ';">' +
        (s.expectancy_pct >= 0 ? '+' : '') + s.expectancy_pct + '%</td>' +
      '<td style="font-family: var(--font-mono); color: var(--accent-rose);">-' + s.max_drawdown_pct + '%</td>' +
      '<td style="font-family: var(--font-mono);">' + s.sharpe + '</td>' +
      '<td style="font-family: var(--font-mono);">' + s.deflated_sharpe + '</td>' +
      '<td style="font-family: var(--font-mono); font-weight: 700; color: ' +
        (isChampion ? 'var(--accent-emerald)' : '#fff') + ';">' + s.score + '</td>' +
      '</tr>';
  }).join('');

  const champBox = document.getElementById('arena-champion-box');
  champBox.style.display = 'block';
  const champ = scores.find(s => s.variant_id === data.champion_id);
  if (champ) {
    const breakdown = Object.entries(champ.score_breakdown || {})
      .filter(([k]) => k !== 'sample_confidence')
      .map(([k, v]) => '<span style="margin-right: 0.9rem;"><span style="color: var(--text-muted);">' +
        k.replace(/_/g, ' ') + '</span> <strong style="color:#fff; font-family: var(--font-mono);">' + v + '</strong></span>')
      .join('');
    champBox.innerHTML =
      '<div style="background: rgba(16,185,129,0.08); border: 1px solid var(--accent-emerald); border-left: 4px solid var(--accent-emerald); border-radius: 6px; padding: 0.85rem;">' +
        '<div style="display:flex; justify-content: space-between; align-items:center; margin-bottom: 0.3rem;">' +
          '<strong style="color: #fff;">&#128081; Champion: ' + champ.name + '</strong>' +
          '<span class="card-badge badge-emerald">EARNED THE RIGHT TO TRADE</span>' +
        '</div>' +
        '<div style="font-size: 0.78rem; color: var(--text-secondary); line-height: 1.5;">' + data.champion_rationale + '</div>' +
        '<div style="font-size: 0.72rem; margin-top: 0.5rem;">Score contribution: ' + breakdown + '</div>' +
      '</div>';
  } else {
    champBox.innerHTML =
      '<div style="background: rgba(244,63,94,0.08); border-left: 4px solid var(--accent-rose); border-radius: 6px; padding: 0.85rem;">' +
        '<strong style="color: #fff;">No champion - standing down</strong>' +
        '<div style="font-size: 0.78rem; color: var(--text-secondary); margin-top: 0.25rem;">' + data.champion_rationale + '</div>' +
      '</div>';
  }

  document.getElementById('arena-methodology').textContent = data.methodology || '';
  const biasBox = document.getElementById('arena-bias');
  if (data.known_bias) {
    biasBox.style.display = 'block';
    biasBox.innerHTML =
      '<div style="background: rgba(245,158,11,0.08); border-left: 3px solid #f59e0b; border-radius: 6px; padding: 0.7rem; font-size: 0.72rem; color: var(--text-secondary); line-height: 1.5;">' +
      '<strong style="color: #fbbf24;">Disclosed methodological bias:</strong> ' + data.known_bias + '</div>';
  } else {
    biasBox.style.display = 'none';
  }
}

// --- JUDGE DEMO: FULL STORY TIMELINE ---
const VERDICT_STYLES = {
  INFO:     { color: 'var(--accent-cyan)',    bg: 'rgba(6,182,212,0.08)',   icon: '&#9679;' },
  PASS:     { color: 'var(--accent-emerald)', bg: 'rgba(16,185,129,0.08)',  icon: '&#10003;' },
  REJECTED: { color: 'var(--accent-rose)',    bg: 'rgba(244,63,94,0.08)',   icon: '&#10005;' },
  HALTED:   { color: 'var(--accent-rose)',    bg: 'rgba(244,63,94,0.14)',   icon: '&#9940;' },
  LEARNED:  { color: '#c4b5fd',               bg: 'rgba(168,85,247,0.08)',  icon: '&#129504;' },
};

async function runJudgeDemo() {
  const btn = document.getElementById('btn-judge-demo');
  const execute = document.getElementById('judge-demo-execute').checked;
  const symbol = getActiveSymbol();
  const timeline = document.getElementById('judge-demo-timeline');
  const summaryBox = document.getElementById('judge-demo-summary');

  if (execute && !confirm(
    'This will place a REAL multi-leg options order on your Alpaca paper account for ' + symbol +
    ' if the Risk Kernel approves it.\n\nPaper money, but a real order. Continue?')) {
    return;
  }

  if (btn) { btn.disabled = true; btn.textContent = 'Running the full story...'; }
  summaryBox.style.display = 'none';
  timeline.innerHTML = '<div class="glass-panel" style="padding: 1.5rem; text-align: center; color: var(--text-muted);">' +
    'Running perception, regime, arena, reasoning, kernel, audit, rejection, TradeTrap, learning...</div>';

  try {
    const res = await fetch(`${API_BASE}/api/demo/story`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol, execute })
    });
    const data = await res.json();
    renderJudgeDemo(data);
    await refreshSystemStatus();
    await loadAuditSnapshots();
  } catch (err) {
    timeline.innerHTML = '<div class="glass-panel" style="padding: 1.25rem; color: var(--accent-rose);">Demo error: ' + err + '</div>';
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Run Full Story'; }
  }
}

function renderJudgeDemo(data) {
  const s = data.summary || {};
  const summaryBox = document.getElementById('judge-demo-summary');
  summaryBox.style.display = 'block';
  summaryBox.innerHTML =
    '<div class="glass-panel" style="padding: 1rem;">' +
      '<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 0.65rem;">' +
        '<div class="kpi-box"><div class="kpi-title">Regime</div><div class="kpi-number highlight" style="font-size: 0.95rem;">' + (s.regime || '-') + '</div></div>' +
        '<div class="kpi-box"><div class="kpi-title">Arena Champion</div><div class="kpi-number highlight" style="font-size: 0.8rem;">' + (s.champion || 'none') + '</div></div>' +
        '<div class="kpi-box"><div class="kpi-title">Kernel Decision</div><div class="kpi-number ' +
          (s.kernel_decision === 'APPROVED' ? 'positive' : 'negative') + '" style="font-size: 0.95rem;">' + (s.kernel_decision || '-') + '</div></div>' +
        '<div class="kpi-box"><div class="kpi-title">Broker Order</div><div class="kpi-number highlight" style="font-size: 0.78rem; font-family: var(--font-mono);">' +
          (s.order_id || 'not submitted') + '</div></div>' +
        '<div class="kpi-box"><div class="kpi-title">Hash Verified</div><div class="kpi-number ' +
          (s.hash_verified ? 'positive' : 'negative') + '" style="font-size: 0.95rem;">' + (s.hash_verified ? 'VERIFIED' : 'FAILED') + '</div></div>' +
      '</div>' +
    '</div>';

  const steps = data.steps || [];
  document.getElementById('judge-demo-timeline').innerHTML =
    '<div class="glass-panel" style="padding: 1.25rem;">' +
      '<div style="display: flex; flex-direction: column; gap: 0.6rem;">' +
        steps.map(st => {
          const v = VERDICT_STYLES[st.verdict] || VERDICT_STYLES.INFO;
          return '<div style="display: flex; gap: 0.75rem; align-items: flex-start; background: ' + v.bg +
            '; border-left: 3px solid ' + v.color + '; border-radius: 6px; padding: 0.7rem 0.85rem;">' +
            '<div style="font-family: var(--font-mono); font-size: 0.78rem; color: ' + v.color +
              '; font-weight: 700; min-width: 2.4rem;">' + v.icon + ' ' + st.step + '</div>' +
            '<div style="flex: 1;">' +
              '<div style="display: flex; justify-content: space-between; align-items: baseline; gap: 0.5rem;">' +
                '<strong style="font-size: 0.86rem; color: #fff;">' + st.title + '</strong>' +
                '<span style="font-size: 0.66rem; font-weight: 700; color: ' + v.color + ';">' + st.verdict + '</span>' +
              '</div>' +
              '<div style="font-size: 0.78rem; color: var(--text-secondary); margin-top: 0.2rem; line-height: 1.5;">' + st.detail + '</div>' +
              renderDemoStepExtra(st) +
            '</div>' +
          '</div>';
        }).join('') +
      '</div>' +
    '</div>';
}

function renderDemoStepExtra(st) {
  const d = st.data || {};
  if (d.leaderboard) {
    return '<div style="margin-top: 0.45rem; font-family: var(--font-mono); font-size: 0.68rem; color: var(--text-muted);">' +
      d.leaderboard.map(l => (l.eligible ? '&#9679;' : '&#9675;') + ' ' + l.name + ' - score ' + l.score +
        ', ' + l.trades + ' cycles, win ' + l.win_rate_pct + '%').join('<br>') + '</div>';
  }
  if (d.decision && d.decision.kernel_checks) {
    const failed = d.decision.kernel_checks.filter(c => !c.pass);
    const shown = failed.length ? failed : d.decision.kernel_checks.slice(0, 4);
    return '<div style="margin-top: 0.45rem; font-family: var(--font-mono); font-size: 0.68rem; color: var(--text-muted);">' +
      shown.map(c => (c.pass ? '&#10003;' : '&#10005;') + ' ' + c.check + ': ' + c.value + ' vs limit ' + c.limit).join('<br>') +
      '</div>';
  }
  if (d.hash) {
    return '<div style="margin-top: 0.35rem; font-family: var(--font-mono); font-size: 0.66rem; color: var(--accent-cyan); word-break: break-all;">' + d.hash + '</div>';
  }
  return '';
}

/* ==========================================================================
   PRODUCTION LAYER
   Toasts, resilient fetch, LLM provider health, real config loading.
   ========================================================================== */

// --- Toast notifications (non-blocking replacement for alert()) -----------
function ensureToastHost() {
  let host = document.getElementById('toast-host');
  if (!host) {
    host = document.createElement('div');
    host.id = 'toast-host';
    host.setAttribute('role', 'status');
    host.setAttribute('aria-live', 'polite');
    document.body.appendChild(host);
  }
  return host;
}

function toast(message, { type = 'info', title = '', timeout = 6000 } = {}) {
  const host = ensureToastHost();
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;

  const body = document.createElement('div');
  body.className = 'toast-body';
  if (title) {
    const t = document.createElement('div');
    t.className = 'toast-title';
    t.textContent = title;
    body.appendChild(t);
  }
  const m = document.createElement('div');
  m.className = 'toast-msg';
  m.textContent = message;             // textContent: never inject markup from an error string
  body.appendChild(m);

  const close = document.createElement('button');
  close.className = 'toast-close';
  close.setAttribute('aria-label', 'Dismiss notification');
  close.textContent = '×';
  close.onclick = () => dismissToast(el);

  el.appendChild(body);
  el.appendChild(close);
  host.appendChild(el);

  if (timeout) setTimeout(() => dismissToast(el), timeout);
  return el;
}

function dismissToast(el) {
  if (!el || !el.parentNode) return;
  el.classList.add('leaving');
  setTimeout(() => el.remove(), 200);
}

const toastError = (msg, title = 'Something went wrong') => toast(String(msg), { type: 'error', title, timeout: 9000 });
const toastOk = (msg, title = '') => toast(msg, { type: 'success', title });
const toastWarn = (msg, title = '') => toast(msg, { type: 'warn', title, timeout: 8000 });

// --- Resilient fetch -------------------------------------------------------
/**
 * fetch + JSON with a timeout and a real error message.
 * Surfaces the server's own detail when it sends one, instead of a bare "500".
 */
async function apiFetch(path, options = {}, { timeoutMs = 120000 } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${API_BASE}${path}`, { ...options, signal: controller.signal });
    let payload = null;
    const text = await res.text();
    try { payload = text ? JSON.parse(text) : null; } catch { payload = null; }

    if (!res.ok) {
      const detail = (payload && (payload.detail || payload.message)) || text.slice(0, 200) || res.statusText;
      throw new Error(`${res.status} ${detail}`);
    }
    return payload;
  } catch (err) {
    if (err.name === 'AbortError') throw new Error(`Request timed out after ${Math.round(timeoutMs / 1000)}s: ${path}`);
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

// --- LLM provider health chip ---------------------------------------------
const PROVIDER_CHIP = {
  LIVE:           { cls: 'provider-live',     icon: '●', label: p => `AI: ${p || 'live'}` },
  READY:          { cls: 'provider-ready',    icon: '○', label: () => 'AI: ready' },
  DEGRADED:       { cls: 'provider-degraded', icon: '△', label: () => 'AI: deterministic' },
  NOT_CONFIGURED: { cls: 'provider-off',      icon: '○', label: () => 'AI: not configured' },
};

function renderProviderChip(llm) {
  if (!llm) return;
  let chip = document.getElementById('llm-provider-chip');
  if (!chip) {
    chip = document.createElement('span');
    chip.id = 'llm-provider-chip';
    const anchor = document.querySelector('.broker-badge');
    if (anchor && anchor.parentNode) anchor.parentNode.insertBefore(chip, anchor.nextSibling);
    else return;
  }
  const spec = PROVIDER_CHIP[llm.mode] || PROVIDER_CHIP.NOT_CONFIGURED;
  chip.className = `provider-chip ${spec.cls}`;
  chip.textContent = `${spec.icon} ${spec.label(llm.active_provider)}`;

  const gem = llm.gemini || {}, orr = llm.openrouter || {};
  chip.title =
    `${llm.detail}\n` +
    `Gemini (${gem.model}): ${gem.configured ? 'configured' : 'not configured'}` +
    `${gem.last_error ? ' - ' + gem.last_error : ''}\n` +
    `OpenRouter (${orr.model}): ${orr.configured ? 'configured' : 'not configured'}` +
    `${orr.last_error ? ' - ' + orr.last_error : ''}\n` +
    `Fallback: ${llm.fallback}`;
}

// --- Real risk config (kernel/config.yaml, not a pasted copy) -------------
let cachedRiskConfig = null;

async function loadRiskConfig() {
  try {
    const data = await apiFetch('/api/config');
    cachedRiskConfig = data;
    const pre = document.getElementById('config-yaml-display');
    if (pre) pre.textContent = data.raw;

    // The heading reports rules the kernel ACTUALLY enforces, not the number of config keys -
    // one declared limit (min_daily_volume) is deliberately unenforced for lack of free data.
    const countEl = document.getElementById('kernel-rule-count');
    if (countEl && typeof data.enforced_count === 'number') {
      countEl.textContent = data.enforced_count;
      countEl.title = `Enforced: ${(data.enforced_checks || []).join(', ')}` +
        ((data.declared_but_unenforced || []).length
          ? `\nDeclared but not enforced: ${data.declared_but_unenforced.join(', ')}`
          : '');
    }
    return data;
  } catch (err) {
    const pre = document.getElementById('config-yaml-display');
    if (pre) pre.textContent = `Could not load kernel/config.yaml: ${err.message}`;
  }
  return null;
}

async function openConfigModal() {
  document.getElementById('config-modal').style.display = 'flex';
  await loadRiskConfig();
}

// --- Global safety nets ----------------------------------------------------
window.addEventListener('unhandledrejection', (e) => {
  console.error('Unhandled promise rejection:', e.reason);
});

// Keyboard: Escape closes any open modal.
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    document.querySelectorAll('.modal-overlay').forEach(m => {
      if (m.style.display === 'flex') m.style.display = 'none';
    });
  }
});

/**
 * Shows the regime + arena evidence that shaped an arena-gated pipeline run, so the Strategy
 * Studio makes it visible that the proposal was constrained by measured evidence rather than
 * being a free-form model guess.
 */
function renderStudioArenaContext(data) {
  const box = document.getElementById('studio-arena-context');
  if (!box) return;
  const regime = data.regime, arena = data.arena;
  if (!regime || !arena) { box.style.display = 'none'; return; }

  const eligible = (arena.scores || []).filter(s => s.eligible);
  box.style.display = 'block';
  box.innerHTML =
    '<div style="background: rgba(99,102,241,0.07); border-left: 3px solid var(--accent-indigo); border-radius: 6px; padding: 0.7rem 0.85rem;">' +
      '<div style="display:flex; justify-content:space-between; align-items:center; gap:0.5rem; flex-wrap:wrap;">' +
        '<strong style="font-size:0.82rem; color:#fff;">Regime: ' + regime.label + '</strong>' +
        '<span class="card-badge badge-indigo">' + (arena.champion_name || 'NO CHAMPION') + '</span>' +
      '</div>' +
      '<div style="font-size:0.73rem; color:var(--text-muted); margin-top:0.3rem;">' +
        eligible.length + ' of ' + (arena.scores || []).length + ' strategies eligible here. ' +
        (arena.champion_rationale || '') +
      '</div>' +
    '</div>';
}
