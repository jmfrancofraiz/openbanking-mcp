"use strict";
/* Panel finmcp · interfaz (variante B «Relato del mes»).
   Diario (pestaña de apertura): último saldo de cada cuenta y lista plana de todos los movimientos, sin agregaciones.
   Mes: ingresos y gastos del mes seleccionado, sin comparaciones (solo minigráficas de 6 meses).
   Evolución: «Mes a mes», «Acumulado» y «Periodo contra periodo». Tendencias: una tarjeta por categoría
   (de gasto o de ingreso, a elección).
   Los importes llegan calculados de la API de solo lectura (/api/*); aquí solo se presentan.
   Única acción: tirar hacia abajo desde arriba del todo de la lista del Diario pide sincronizar con los bancos y recarga los datos. */
(() => {
  const L = window.FinLib;
  const { eur, eur0 } = L;
  const app = document.getElementById("app");
  const SVGNS = "http://www.w3.org/2000/svg";
  const INC = "#147a70", EXP = "#c0663b", VARC = "#e8b89c";
  // En el dispositivo solo se guardan preferencias, nunca cifras.
  const PREF = { split: "finmcp.split", trend: "finmcp.trend" };
  const THEME_KEY = "finmcp.theme";

  // Colores de gráficos SVG dibujados a mano: no siguen la cascada CSS (son atributos `fill`/`stroke`,
  // no `style=`), así que se leen del DOM ya pintado en cada trazo — así reflejan el tema vigente
  // (claro/oscuro) sin tener que tocar estas funciones cuando cambia.
  const cssVar = (name, fallback) => {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name);
    return v && v.trim() ? v.trim() : fallback;
  };
  const ACC_PERIODS = [["3", "3 meses"], ["6", "6 meses"], ["12", "1 año"], ["ano", "Año actual"], ["todo", "Todo"]];
  const PERIOD_TYPES = [["mes", "Mes"], ["trimestre", "Trimestre"], ["semestre", "Semestre"], ["ano", "Año"]];

  let MONTHS = [];   // meses con datos, ascendentes (AAAA-MM)
  let CLOSED = [];   // los mismos sin el mes en curso
  let CUR = "";      // mes en curso
  let TODAY = "";    // hoy (AAAA-MM-DD, fecha del servidor)
  let CUT = { day: 1, days: 30 };
  let COVERAGE = []; // [{bank, first_month}]
  let PIN = false;   // ¿el panel está protegido con PIN?
  let SYNC = false;  // ¿puede el panel lanzar una sincronización (tirar hacia abajo)?

  // La API responde 401 {auth: "pin"} si falta la sesión: se muestra la pantalla del PIN.
  class PinRequired extends Error {}

  /* ---------- DOM ---------- */

  function el(tag, props = {}, children = []) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "onclick") n.addEventListener("click", v);
      else if (k === "style") Object.assign(n.style, v); // CSSOM: permitido por la CSP
      else if (k === "attrs") for (const [a, x] of Object.entries(v)) n.setAttribute(a, x);
      else n[k] = v;
    }
    if (props.attrs && props.attrs.role === "button") {
      n.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); n.click(); } });
    }
    n.append(...[].concat(children).flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false && c !== ""));
    return n;
  }

  function svg(tag, attrs = {}, children = []) {
    const n = document.createElementNS(SVGNS, tag);
    for (const [a, v] of Object.entries(attrs)) n.setAttribute(a, v);
    n.append(...children);
    return n;
  }

  // Sin «−0 €»: los importes que redondean a cero se muestran como 0.
  const nz = (v) => (Math.abs(v) < 0.005 ? 0 : v);
  const amt = (v, fmt = eur0) => el("span", { className: "amt", textContent: fmt.format(nz(v)) });
  const word = (tipo) => (tipo === "ingresos" ? "ingresos" : "gastos");
  const tipoOf = (x) => (x === "ingresos" ? "ingresos" : "gastos");
  const H = (...segs) => `#/${segs.map((x) => encodeURIComponent(x)).join("/")}`;
  const spacer = () => el("span", { className: "spacer" });
  const stepMonth = (ym, d) => MONTHS[MONTHS.indexOf(ym) + d] || null;
  const sum = (xs) => xs.reduce((a, v) => a + v, 0);

  /* ---------- datos (API de solo lectura, caché en memoria durante la visita) ---------- */

  const cache = new Map();
  function api(path, params = {}) {
    const qs = new URLSearchParams(params).toString();
    const url = `/api/${path}${qs ? `?${qs}` : ""}`;
    if (!cache.has(url)) {
      const p = fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin" }).then(async (r) => {
        const body = await r.json().catch(() => ({}));
        if (r.status === 401 && body.auth === "pin") throw new PinRequired(body.error);
        if (!r.ok) throw new Error(body.error || `Error ${r.status}`);
        return body;
      });
      p.catch(() => cache.delete(url));
      cache.set(url, p);
    }
    return cache.get(url);
  }
  // Lo ya cargado de la lista del Diario ({key, rows, next, closing, balance}): al volver de un
  // movimiento se repinta tal cual, sin perder lo desplazado.
  let diary = null;
  const resetData = () => { cache.clear(); diary = null; };
  const aggsOf = (b) => ({ ingresos: b.income, gastos: b.expense });
  const breakdownOf = (from, to) => api("breakdown", { from, to });
  const monthlyRows = (day) => api("monthly", day ? { day } : {}).then((b) => b.months);
  const seriesOf = (from, to) => api("series", { from, to });
  const txOf = (from, to, tipo, kind, key) =>
    api("transactions", { from, to, type: tipo, [kind === "c" ? "category" : "group"]: key }).then((b) => b.transactions);

  /* ---------- navegación ---------- */

  // Cambiar de mes, periodo o subpestaña sustituye la entrada del historial; entrar en un detalle la añade.
  function go(h, { replace = false, keep = false } = {}) {
    const state = history.state || {};
    const depth = state.depth || 0;
    if (replace || location.hash === h) {
      history.replaceState({ depth }, "", h);
    } else {
      history.replaceState({ ...state, depth, scroll: window.scrollY }, "");
      history.pushState({ depth: depth + 1 }, "", h);
    }
    render({ keep });
  }
  const back = () => {
    if (history.state && history.state.depth > 0) { history.back(); return; }
    go(L.parseHash(location.hash)[0] === "diario" ? "#/diario" : H("mes", CUR, "gastos"), { replace: true });
  };

  function swipe(node, onStep) {
    let s = null;
    node.addEventListener("touchstart", (e) => {
      s = e.target.closest("input, select, [data-noswipe]")
        ? null : { x: e.touches[0].clientX, y: e.touches[0].clientY, t: Date.now() };
    }, { passive: true });
    node.addEventListener("touchend", (e) => {
      if (!s) return;
      const dx = e.changedTouches[0].clientX - s.x;
      const dy = e.changedTouches[0].clientY - s.y;
      const ok = Date.now() - s.t < 600 && Math.abs(dx) > 60 && Math.abs(dy) < 40;
      s = null;
      if (ok) onStep(dx < 0 ? 1 : -1);
    });
  }

  /* ---------- componentes ---------- */

  function nav(active) {
    const item = (key, label, d, target) => el("button", {
      className: key === active ? "on" : "", type: "button",
      attrs: { "aria-current": key === active ? "page" : "false" },
      onclick: () => go(target, { replace: true }),
    }, [svg("svg", { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 2, "aria-hidden": "true" }, [svg("path", { d })]),
      el("span", { textContent: label })]);
    return el("nav", { className: "nav" }, [
      item("diario", "Diario", "M9 6h12M9 12h12M9 18h12M3.5 5.5h1v1h-1zM3.5 11.5h1v1h-1zM3.5 17.5h1v1h-1z", "#/diario"),
      item("mes", "Mes", "M12 3a9 9 0 1 0 9 9h-9zM15 3.5A9 9 0 0 1 20.5 9H15z", H("mes", CUR, "gastos")),
      item("evolucion", "Evolución", "M5 20V11M11 20V5M17 20v-7M3 20h18", "#/evolucion"),
      item("tendencias", "Tendencias", "M3 16l5-5 4 4 8-8M3 20h18", "#/tendencias"),
    ]);
  }

  function subtabs(options, current, onChange, cls = "tabs2") {
    return el("div", { className: `seg ${cls}`, attrs: { role: "tablist" } }, options.map(([k, label, total]) =>
      el("button", {
        type: "button", className: k === current ? "on" : "",
        attrs: { role: "tab", "aria-selected": String(k === current) },
        onclick: () => { if (k !== current) onChange(k); },
      }, [el("span", { textContent: label }), total === undefined ? null : el("small", { className: "amt", textContent: eur0.format(total) })])));
  }

  function chipRow(options, current, onChange, label) {
    return el("div", { className: "refrow" }, [label ? el("span", { className: "muted", textContent: label }) : null,
      ...options.map(([k, l]) => el("button", { type: "button", className: `chip${k === current ? " on" : ""}`, textContent: l,
        onclick: () => { if (k !== current) onChange(k); } }))]);
  }

  /* ---------- modo oscuro ---------- */

  const getThemeOverride = () => { try { return localStorage.getItem(THEME_KEY) || ""; } catch { return ""; } };
  const darkMQ = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  const isDark = () => L.isDarkTheme(getThemeOverride(), darkMQ && darkMQ.matches);

  function paintThemeColor() {
    const meta = document.getElementById("theme-color-meta");
    if (meta) meta.setAttribute("content", isDark() ? "#1c1a16" : "#f7f4ee");
  }
  // Aplica el tema al <html> (o lo deja en automático) y refresca el color de la barra del navegador.
  function applyTheme(t) {
    if (t === "dark" || t === "light") document.documentElement.setAttribute("data-theme", t);
    else document.documentElement.removeAttribute("data-theme");
    paintThemeColor();
  }
  applyTheme(getThemeOverride());
  // Si el sistema cambia de tema (p. ej. se hace de noche) y no hay un forzado propio, seguirlo.
  if (darkMQ) darkMQ.addEventListener("change", () => { if (!getThemeOverride()) { paintThemeColor(); render({ keep: true }); } });

  // Icono ☀/☾: muestra el modo EFECTIVO actual (a qué modo pasará si se toca lo indica el aria-label).
  function themeIcon() {
    const common = { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 2,
      "stroke-linecap": "round", "aria-hidden": "true" };
    return isDark()
      ? svg("svg", common, [svg("circle", { cx: 12, cy: 12, r: 4.2 }),
          svg("path", { d: "M12 2.5v2.3M12 19.2v2.3M4.4 4.4l1.6 1.6M18 18l1.6 1.6M2.5 12h2.3M19.2 12h2.3M4.4 19.6l1.6-1.6M18 6l1.6-1.6" })])
      : svg("svg", common, [svg("path", { d: "M20.2 14.7A8.6 8.6 0 1 1 9.3 3.8a7 7 0 0 0 10.9 10.9Z" })]);
  }
  function cycleTheme() {
    const next = L.nextTheme(getThemeOverride());
    try { if (next) localStorage.setItem(THEME_KEY, next); else localStorage.removeItem(THEME_KEY); } catch { /* sin almacenamiento: queda en automático */ }
    applyTheme(next);
    render({ keep: true }); // reconstruye la vista: repinta los gráficos SVG dibujados a mano con el nuevo tema
  }
  function themeBtn() {
    const cur = getThemeOverride();
    const label = cur === "dark" ? "oscuro (forzado)" : cur === "light" ? "claro (forzado)" : "automático, según el sistema";
    return el("button", { className: "ib", type: "button",
      attrs: { "aria-label": `Modo de color: ${label}. Tocar para cambiar.` }, onclick: cycleTheme }, [themeIcon()]);
  }

  const topbar = (title) => el("header", { className: "top" }, [spacer(), el("div", { className: "ttl" }, el("div", { className: "mon", textContent: title })), themeBtn()]);

  function detailBar(title, subtitle) {
    return el("header", { className: "top" }, [
      el("button", { className: "ib", type: "button", textContent: "‹", attrs: { "aria-label": "Volver" }, onclick: back }),
      el("div", { className: "ttl" }, [el("div", { className: "dtitle", textContent: title }), el("div", { className: "muted", textContent: subtitle })]),
      spacer(),
    ]);
  }

  // Barra de mes: «‹ sep» · mes (toque para elegir) · «nov ›». También se cambia deslizando.
  function monthBar(ym, tipo) {
    const prev = stepMonth(ym, -1), next = stepMonth(ym, 1);
    const to = (m) => H("mes", m, tipo);
    const sel = el("select", { className: "msel", attrs: { "aria-label": "Elegir mes" } },
      [...MONTHS].reverse().map((m) => el("option", { value: m, textContent: L.monthLabel(m) + (m === CUR ? " (en curso)" : "") })));
    sel.value = ym;
    sel.addEventListener("change", () => go(to(sel.value), { replace: true }));
    return el("header", { className: "mbar" }, [
      el("button", { className: "mstep", type: "button", disabled: !prev, textContent: prev ? `‹ ${L.monthInitial(prev)}` : "‹",
        attrs: { "aria-label": "Mes anterior" }, onclick: () => { if (prev) go(to(prev), { replace: true }); } }),
      el("div", { className: "mtitle" }, [
        el("span", { className: "mon", textContent: L.monthLabel(ym) }),
        ym === CUR ? el("span", { className: "partial", textContent: `en curso · día ${CUT.day} de ${CUT.days}` }) : null,
        sel,
      ]),
      el("button", { className: "mstep", type: "button", disabled: !next, textContent: next ? `${L.monthInitial(next)} ›` : "›",
        attrs: { "aria-label": "Mes siguiente" }, onclick: () => { if (next) go(to(next), { replace: true }); } }),
    ]);
  }

  // Aviso de cobertura si el periodo empieza antes de que haya datos de alguna cuenta.
  function coverageNote(...froms) {
    const earliest = [...froms].sort()[0].slice(0, 7);
    const late = COVERAGE.filter((c) => c.first_month > earliest);
    if (!late.length) return null;
    return el("p", { textContent: `Cobertura: ${late.map((c) => `${c.bank} desde ${L.monthLabel(c.first_month)}`).join(", ")}. Antes no hay movimientos de esas cuentas.` });
  }

  function arc(c, r0, r1, a0, a1) {
    const large = a1 - a0 > Math.PI ? 1 : 0;
    const p = (r, a) => `${(c + r * Math.sin(a)).toFixed(2)} ${(c - r * Math.cos(a)).toFixed(2)}`;
    return `M${p(r1, a0)} A${r1} ${r1} 0 ${large} 1 ${p(r1, a1)} L${p(r0, a1)} A${r0} ${r0} 0 ${large} 0 ${p(r0, a0)} Z`;
  }

  // Donut SVG con sectores tocables; `selected` resalta un sector y atenúa el resto.
  function donut(slices, { size = 250, thickness = 36, onTap = null, selected = -1 } = {}) {
    const pad = 8;
    const c = size / 2 + pad;
    const r1 = size / 2;
    const r0 = r1 - thickness;
    const total = slices.reduce((a, s) => a + s.total, 0) || 1;
    const root = svg("svg", { viewBox: `0 0 ${size + 2 * pad} ${size + 2 * pad}`, width: size + 2 * pad, height: size + 2 * pad, role: "img", "aria-label": "Reparto por categoría" });
    let a = 0;
    slices.forEach((s, i) => {
      const span = Math.min((s.total / total) * Math.PI * 2, Math.PI * 2 - 0.0001);
      const gap = slices.length > 1 ? 0.012 : 0;
      const grow = i === selected ? 7 : 0;
      const cls = "slice" + (selected >= 0 && i !== selected ? " dim" : "");
      const path = svg("path", { d: arc(c, r0, r1 + grow, a + gap, a + span - gap), fill: s.color, class: cls });
      if (onTap) path.addEventListener("click", (e) => { e.stopPropagation(); onTap(i); });
      root.append(path);
      a += span;
    });
    return root;
  }

  // Minigráfica (línea + punto final). Con lastPartial, el último tramo es discontinuo y el punto hueco.
  function spark(values, { w = 58, h = 18, color = "currentColor", lastPartial = false } = {}) {
    const max = Math.max(...values, 0);
    const min = Math.min(...values, 0);
    const span = max - min || 1;
    const root = svg("svg", { viewBox: `0 0 ${w} ${h}`, width: w, height: h, "aria-hidden": "true", class: "spark" });
    const x = (i) => (values.length === 1 ? w / 2 : 3 + (i / (values.length - 1)) * (w - 6));
    const y = (v) => h - 3 - ((v - min) / span) * (h - 6);
    const n = values.length;
    const partial = lastPartial && n > 1;
    const solid = partial ? values.slice(0, -1) : values;
    const pts = solid.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
    root.append(svg("polyline", { points: pts, fill: "none", stroke: color, "stroke-width": 1.8, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    if (partial) {
      root.append(svg("line", { x1: x(n - 2), y1: y(values[n - 2]), x2: x(n - 1), y2: y(values[n - 1]), stroke: color,
        "stroke-width": 1.6, "stroke-dasharray": "2.5 2.5", "stroke-linecap": "round", class: "partial-seg" }));
    }
    root.append(svg("circle", { cx: x(n - 1), cy: y(values[n - 1]), r: 2.6, fill: partial ? cssVar("--card", "#fffdf9") : color,
      stroke: color, "stroke-width": partial ? 1.4 : 0 }));
    return root;
  }

  function catHref(ctx, tipo, g) {
    return g.categories.length > 1
      ? H("rango", ctx.origin, ctx.from, ctx.to, tipo, "g", g.group)
      : H("rango", ctx.origin, ctx.from, ctx.to, tipo, "c", g.categories[0].category);
  }

  function sliceCard(g, total, tipo, href) {
    const top = g.categories.slice(0, 3);
    return el("div", { className: "card" }, [
      el("div", { className: "dhead" }, [el("b", { textContent: L.shown(g.group) }), el("span", { className: "v amt", textContent: eur0.format(g.total) })]),
      el("div", { className: "dline", textContent: `${L.share(g.total, total)} % de los ${word(tipo)} · ${g.count} movimientos` }),
      top.length > 1 ? el("div", { className: "subcats" }, top.map((c) => el("div", { className: "r" }, [el("span", { textContent: c.label }), amt(c.total)]))) : null,
      el("button", { className: "link", type: "button", textContent: "Ver detalle ›", onclick: () => go(href) }),
    ]);
  }

  function sparkOf(sp, group) {
    const vals = sp.values[group] || sp.months.map(() => 0);
    return spark(vals, { color: L.colorFor(group), lastPartial: sp.partial });
  }

  // Donut + tarjeta del sector tocado + lista de categorías (Mes y Acumulado).
  function categoryBlock(agg, tipo, ctx) {
    if (!agg.groups.length) return [el("p", { className: "muted empty", textContent: `Sin ${word(tipo)} en este periodo.` })];
    const slices = L.topN(agg.groups, 7);
    let sel = -1;
    let all = false;
    const center = el("div", { className: "c" });
    const detail = el("div", { className: "detail" });
    const donutBox = el("div", { className: "donut" });
    const list = el("div", { className: "cards" });
    const more = el("button", { className: "more", type: "button" });
    const paintDonut = () => {
      donutBox.replaceChildren(donut(slices, { selected: sel, onTap: (i) => { sel = sel === i ? -1 : i; paintDonut(); } }), center);
      if (sel < 0) {
        center.replaceChildren(el("span", { className: "lbl", textContent: L.cap(word(tipo)) }),
          el("b", { className: "amt", textContent: eur0.format(agg.total) }), el("span", { className: "hint", textContent: "toque un sector" }));
        detail.replaceChildren();
        return;
      }
      const s = slices[sel];
      center.replaceChildren(el("span", { className: "lbl", textContent: s.rest ? s.label : L.shown(s.label) }),
        el("b", { className: "amt", textContent: eur0.format(s.total) }), el("span", { className: "hint", textContent: `${L.share(s.total, agg.total)} %` }));
      const g = s.rest ? null : agg.groups.find((x) => x.group === s.label);
      detail.replaceChildren(g ? sliceCard(g, agg.total, tipo, catHref(ctx, tipo, g))
        : el("div", { className: "card" }, el("div", { className: "dline", textContent: "Agrupa las categorías menores; están en la lista de abajo." })));
    };
    const card = (g) => el("button", { className: "gcard", type: "button", onclick: () => go(catHref(ctx, tipo, g)) }, [
      el("span", { className: "dot", style: { background: L.colorFor(g.group) } }),
      el("span", { className: "n", textContent: L.shown(g.group) }),
      el("span", { className: "s", textContent: [`${L.share(g.total, agg.total)} %`, `${g.count} mov.`,
        ctx.months > 1 ? `${eur0.format(g.total / ctx.months)}/mes` : null].filter(Boolean).join(" · ") }),
      el("span", { className: `v${g.total < 0 ? " neg" : ""}` }, [amt(g.total), ctx.spark ? sparkOf(ctx.spark, g.group) : null]),
    ]);
    const paintList = () => {
      list.replaceChildren(...(all ? agg.groups : agg.groups.slice(0, 8)).map(card));
      more.textContent = all ? "Ver menos" : `Ver las ${agg.groups.length - 8} categorías restantes`;
      more.classList.toggle("hidden", agg.groups.length <= 8);
    };
    more.addEventListener("click", () => { all = !all; paintList(); });
    paintDonut();
    paintList();
    const sp = ctx.spark;
    return [
      el("h2", { textContent: tipo === "ingresos" ? "¿De dónde ha venido?" : "¿En qué se ha ido?" }),
      donutBox, detail,
      el("h2", { textContent: `${L.cap(word(tipo))} por categoría` }),
      sp ? el("p", { className: "muted lead2", textContent: `Minigráfica: últimos ${sp.months.length} meses `
        + `(${L.monthInitial(sp.months[0])}–${L.monthInitial(sp.months[sp.months.length - 1])})`
        + (sp.partial ? `; tramo discontinuo: mes en curso, a día ${CUT.day}.` : ".") }) : null,
      list, more,
    ];
  }

  function txList(rows, { byDay = true, showCat = false, income = false } = {}) {
    const out = [];
    let last = null;
    for (const t of rows) {
      if (byDay && t.date !== last) {
        out.push(el("div", { className: "day", textContent: L.dayLabel(t.date) }));
        last = t.date;
      }
      const meta = [byDay ? null : L.dayLabel(t.date), t.bank, showCat || income ? t.category : null, ...t.tags.map((x) => `#${x}`),
        t.is_bill ? "recibo" : null, t.amount > 0 && !income ? "devolución/abono" : null].filter(Boolean).join(" · ");
      const det = el("div", { className: "tx-det hidden" }, [
        el("p", { textContent: t.description || "—" }),
        el("p", { className: "muted", textContent: `${t.category} · id ${t.id}` }),
      ]);
      out.push(el("div", { className: "tx", attrs: { role: "button", tabindex: "0" }, onclick: () => det.classList.toggle("hidden") }, [
        el("div", { className: "tx-main" }, [el("div", { className: "tx-title", textContent: L.cleanTitle(t.merchant || t.description) }),
          el("div", { className: "tx-meta", textContent: meta })]),
        el("div", { className: `tx-amt amt${t.amount > 0 ? " pos" : ""}`, textContent: (t.amount > 0 ? "+" : "−") + eur.format(Math.abs(t.amount)) }),
        det,
      ]));
    }
    return out;
  }

  // Movimientos: buscador + orden + lista por días + resumen fijo con el total de lo filtrado.
  // Se abre siempre con la fecha descendente (lo más reciente primero), como el Diario.
  function txPanel(rows, tipo, showCat) {
    let sort = "desc";
    const income = tipo === "ingresos";
    const input = el("input", { type: "search", className: "filter", placeholder: "Buscar comercio, concepto o etiqueta",
      attrs: { "aria-label": "Buscar movimientos" } });
    const chips = el("div", { className: "chips" });
    const list = el("div", { className: "tx-list" });
    const total = el("div", { className: "tx-sum" });
    const paint = () => {
      const q = input.value.trim().toLowerCase();
      let r = rows.filter((t) => !q || [t.merchant, t.description, t.category, ...t.tags].join(" ").toLowerCase().includes(q));
      if (sort === "desc") r = [...r].reverse();
      if (sort === "amount") r = [...r].sort((a, b) => Math.abs(b.amount) - Math.abs(a.amount));
      const net = sum(r.map((t) => t.amount));
      total.replaceChildren(el("span", { textContent: `${r.length} mov.` }),
        el("span", { className: "amt", textContent: eur.format(nz(income ? net : -net)) }));
      list.replaceChildren(...txList(r, { byDay: sort !== "amount", showCat, income }));
      chips.replaceChildren(...[["desc", "Fecha ↓"], ["asc", "Fecha ↑"], ["amount", "Importe"]].map(([v, l]) =>
        el("button", { type: "button", className: `chip${v === sort ? " on" : ""}`, textContent: l, onclick: () => { sort = v; paint(); } })));
    };
    input.addEventListener("input", paint);
    paint();
    return [input, chips, list, total];
  }

  /* ---------- Diario: el día a día, sin agregaciones ---------- */
  // #/diario[/<filtros>] · #/diario/mov/<id>. Los filtros viajan en la ruta (L.diaryFilters).

  const TIPOS = [["todos", "Todos"], ["cargos", "Cargos"], ["abonos", "Abonos"]];
  // Una marca a la vez; tocar la marca activa la quita.
  const MARCAS = [["sin-categoria", "Sin categoría"], ["recibos", "Recibos"], ["no-computables", "No computables"], ["manuales", "Manuales"]];
  let diaryExtra = false; // ¿desplegado «Más filtros»?
  const diaryHash = (f) => { const qs = L.diaryQuery(f); return qs ? H("diario", qs) : "#/diario"; };
  const ledgerPage = (f, cursor) => api("ledger", { ...L.ledgerParams(f), ...(cursor ? { cursor } : {}) });
  const tailOf = (a) => (a.iban_tail ? `··${a.iban_tail}` : a.name);
  const dshort = (iso) => L.dayShort(iso) + (iso.slice(0, 4) !== TODAY.slice(0, 4) ? ` ${iso.slice(0, 4)}` : "");

  function marksOf(t) {
    return [t.uncategorized && ["sin categoría", "warn"], t.is_bill && ["recibo", ""], t.neutral && ["no computa", ""],
      t.manual && ["manual", ""]].filter(Boolean).map(([b, c]) => el("span", { className: `badge${c ? ` ${c}` : ""}`, textContent: b }));
  }

  function freshText(a) {
    const b = a.balance;
    if (!b) return "sin datos de saldo";
    const when = L.whenLabel(b.at, TODAY);
    if (a.manual) return `manual · ${when}`; // sin sincronización: la fecha dice de cuándo es el saldo
    if (a.state === "stale") return `sin actualizar desde ${when}`;
    if (a.state === "warn") return `falló el último sync · saldo de ${when}`;
    return when;
  }

  // Cifra que rueda como un cuentakilómetros: cada dígito es una cinta 0…9,d (L.odoTape) que, mientras la rejilla de
  // saldos lleva la clase `roll`, recorre una vuelta y se detiene en su cifra; sin ella la cinta está en reposo, ya en el
  // valor final (así una tarjeta que no rueda, o un navegador sin animaciones, muestra siempre la cifra correcta).
  // Para los lectores de pantalla la cifra es una imagen con su texto; las cintas se ocultan. `order`: posición de la
  // tarjeta, para escalonar el arranque.
  function odometer(text, order) {
    let n = 0; // dígito en curso: los separadores no cuentan para el escalonado
    return el("span", { className: "odo", attrs: { role: "img", "aria-label": text } }, L.figureChars(text).map(({ ch, digit }) => {
      if (digit === null) return el("span", { className: "odo-ch", attrs: { "aria-hidden": "true" }, textContent: ch });
      const tape = el("span", { className: "odo-tape", style: { animationDelay: `${L.rollDelay(n++, order)}ms` } },
        L.odoTape(digit).map((d) => el("span", { textContent: String(d) })));
      return el("span", { className: "odo-ch odo-col", attrs: { "aria-hidden": "true" } }, tape);
    }));
  }

  // Un bloque único con todos los saldos (`.saldos`): arriba el total, a todo el ancho, y debajo las cuentas en una rejilla de
  // dos columnas (`.bgrid`), todo separado por líneas finas (sin tarjetas sueltas). Cada celda de cuenta es un botón que filtra
  // por esa cuenta; el total no lo es.
  //
  // Celda de cuenta, en tres líneas: banco, cifra (con «saldo disponible» a su lado si el banco no da el contable) y, pequeña,
  // la terminación del IBAN (o el nombre de la cuenta) con la frescura del saldo.
  function balanceCell(a, on, onTap, order) {
    const b = a.balance;
    return el("button", { type: "button", className: `bcell ${a.state}${on ? " on" : ""}`, attrs: { "aria-pressed": String(on) }, onclick: onTap }, [
      el("span", { className: "bk" }, [el("i", { className: `led ${a.state}`, attrs: { "aria-hidden": "true" } }), el("span", { textContent: a.bank })]),
      el("span", { className: "bvrow" }, [
        el("span", { className: "bv amt" }, b ? odometer(eur.format(b.value), order) : "—"),
        b && b.kind === "disponible" ? el("span", { className: "bkind", textContent: "saldo disponible" }) : null,
      ]),
      el("span", { className: "bt", textContent: `${tailOf(a)} · ${freshText(a)}` }),
    ]);
  }

  // Fila del total: la suma de los últimos saldos de todas las cuentas (la calcula el servidor: `total` de /api/accounts).
  // La cifra, grande y a la izquierda, sin rótulo (el nombre solo lo oyen los lectores de pantalla, por `aria-label`); debajo,
  // pequeña, la nota precedida del semáforo, que es el peor de todas las cuentas.
  function totalRow(t) {
    return el("div", { className: `btotal ${t.state}`, attrs: { role: "group", "aria-label": "Total de todas las cuentas" } }, [
      el("span", { className: "bv amt" }, odometer(eur.format(t.value), 0)),
      el("span", { className: "bt" }, [
        el("i", { className: `led ${t.state}`, attrs: { "aria-hidden": "true" } }),
        el("span", { textContent: L.totalNote(t, TODAY) }),
      ]),
    ]);
  }

  // `total` solo con dos o más cuentas (con una repetiría su propia celda).
  function saldosBlock(accounts, total, sel, onPick) {
    return el("div", { className: "saldos", attrs: { role: "group", "aria-label": "Saldos por cuenta" } }, [
      total && accounts.length > 1 ? totalRow(total) : null,
      el("div", { className: "bgrid" }, accounts.map((a, i) => balanceCell(a, sel === a, () => onPick(a), i + 1))),
    ]);
  }

  // Bancos con la sincronización fallida o desactualizada: motivo y fecha del saldo mostrado.
  function syncAlerts(accounts) {
    const lines = accounts.filter((a) => a.state !== "ok").map((a) => {
      const s = a.sync;
      const fail = s && s.status === "error"
        ? `el último intento de sincronización (${L.whenLabel(s.at, TODAY)}) falló${s.error ? `: ${s.error}` : ""}` : null;
      const old = a.balance ? `el saldo mostrado es de ${L.whenLabel(a.balance.at, TODAY)}` : "no hay ninguna foto de saldo";
      return el("p", { className: a.state, textContent: `${a.bank}: ${[fail, old].filter(Boolean).join("; ")}.` });
    });
    return lines.length ? el("div", { className: "alerts" }, lines) : null;
  }

  // Explica el saldo al cierre de cada día (calculado) y su contraste con las fotos reales del banco.
  function closingNote(a, bal) {
    // Cuenta manual: el saldo de partida lo anotó el Señor a mano, no es una foto del banco.
    const src = a.manual ? "el saldo anotado a mano" : "la foto del banco";
    if (!bal) return el("div", { className: "acc-note" }, el("p", { textContent: `${a.bank}: ${a.manual ? "no tiene ningún saldo anotado" : "no hay foto de saldo del banco"}, así que no se puede calcular el saldo de cada día.` }));
    const c = bal.check;
    const ps = [el("p", { textContent: `La cifra junto a cada día es el saldo al cierre, calculado hacia atrás desde ${src} de ${L.whenLabel(bal.anchor.at, TODAY)} `
      + `(${eur.format(bal.anchor.value)}${bal.anchor.kind === "disponible" ? ", saldo disponible" : ""}). ${a.manual ? "Es un cálculo, no un saldo anotado." : "No es un dato del banco."}` })];
    const snaps = (n) => (a.manual ? (n === 1 ? "el saldo anotado" : `los ${n} saldos anotados`) : (n === 1 ? "la foto del banco" : `las ${n} fotos del banco`));
    if (c.bad) {
      const b = c.last_bad;
      ps.push(el("p", { className: "warnv", textContent: `⚠ No cuadra con ${c.bad} de ${snaps(c.snapshots)}. ${a.manual ? "El más reciente" : "La más reciente"}, de ${L.whenLabel(b.at, TODAY)}: ${a.manual ? "el saldo anotado era" : "el banco daba"} ${eur.format(b.bank_value)}; `
        + `el cálculo da ${eur.format(b.calc_prev)} al cierre del día anterior y ${eur.format(b.calc_day)} al cierre de ese día. Falta o sobra algún movimiento: los saldos hasta el ${dshort(b.date)} (en rojo) pueden estar desviados.` }));
    } else if (c.ok) {
      ps.push(el("p", { textContent: `Cuadra con ${snaps(c.ok)} desde el ${dshort(c.since)}`
        + `${c.unknown ? ` (${c.unknown} sin poder comprobar)` : ""}; antes no hay ${a.manual ? "saldos con los que" : "fotos con las que"} comprobarlo.` }));
    }
    return el("div", { className: "acc-note" }, ps);
  }

  function ledgerRow(t, showBank) {
    const cat = t.uncategorized && t.category === "Sin categoría" ? null : t.category;
    const meta = [showBank ? t.bank : null, cat, ...t.tags.map((x) => `#${x}`)].filter(Boolean).join(" · ");
    return el("div", { className: "tx", attrs: { role: "button", tabindex: "0" }, onclick: () => go(H("diario", "mov", t.id)) }, [
      el("div", { className: "tx-main" }, [
        el("div", { className: "tx-title", textContent: L.titleOf(t) }),
        el("div", { className: "tx-meta" }, [meta ? el("span", { textContent: meta }) : null, ...marksOf(t)]),
      ]),
      el("div", { className: `tx-amt amt${t.amount > 0 ? " pos" : ""}`, textContent: L.signedEur(t.amount) }),
    ]);
  }

  async function diaryView(seg) {
    const [accRes, tagRes] = await Promise.all([api("accounts"), api("tags")]);
    const accounts = accRes.accounts;
    let cur = L.diaryFilters(seg);
    const sel = cur.cuenta ? accounts.find((a) => a.id === cur.cuenta) || null : null;
    if (!sel) delete cur.cuenta;
    const fetchFirst = async (f) => {
      const page = await ledgerPage(f);
      // Copias: las páginas siguientes se añaden a `rows`, y la respuesta original sigue en la caché.
      return { key: L.diaryQuery(f), rows: [...page.transactions], next: page.next, closing: { ...(page.closing || {}) }, balance: page.balance };
    };
    if (!diary || diary.key !== L.diaryQuery(cur)) diary = await fetchFirst(cur);

    const setF = (patch) => {
      const nf = { ...cur, ...patch };
      if (nf.desde && nf.hasta && nf.desde > nf.hasta) {
        if ("desde" in patch) nf.hasta = ""; else nf.desde = "";
      }
      go(diaryHash(nf), { replace: true, keep: true });
    };

    // Lista: cabecera por día (con el saldo al cierre si hay una cuenta elegida) y carga progresiva.
    const listBox = el("div", { className: "tx-list" });
    const moreBtn = el("button", { className: "more", type: "button", textContent: "Cargar más" });
    const sentinel = el("div", { className: "sentinel", attrs: { "aria-hidden": "true" } });
    let lastDay = null;
    const dayHead = (d) => {
      const cl = sel ? diary.closing[d] : undefined;
      const upTo = sel && diary.balance ? diary.balance.check.suspect_through : null;
      return el("div", { className: "day dayb" }, [el("span", { textContent: L.dayHeading(d, TODAY) }),
        cl === undefined ? null : el("span", { className: `cl amt${upTo && d <= upTo ? " suspect" : ""}`, textContent: `saldo ${eur.format(cl)}` })]);
    };
    const append = (rows) => {
      const out = [];
      for (const t of rows) {
        if (t.date !== lastDay) { out.push(dayHead(t.date)); lastDay = t.date; }
        out.push(ledgerRow(t, !sel));
      }
      listBox.append(...out);
    };
    const paintMore = () => {
      moreBtn.classList.toggle("hidden", !diary.next);
      moreBtn.disabled = false;
      moreBtn.textContent = "Cargar más";
    };
    const paintList = () => {
      lastDay = null;
      listBox.replaceChildren(diary.rows.length ? "" : el("p", { className: "muted empty", textContent: "No hay movimientos con estos filtros." }));
      append(diary.rows);
      paintMore();
    };
    let loading = false;
    const loadMore = async () => {
      if (loading || !diary.next || !listBox.isConnected) return;
      const d = diary;
      loading = true;
      moreBtn.disabled = true;
      moreBtn.textContent = "Cargando…";
      try {
        const page = await ledgerPage(cur, d.next);
        if (d !== diary) return;
        d.rows.push(...page.transactions);
        d.next = page.next;
        Object.assign(d.closing, page.closing || {});
        append(page.transactions);
        paintMore();
      } catch (err) {
        if (err instanceof PinRequired) { render(); return; }
        moreBtn.disabled = false;
        moreBtn.textContent = "No se ha podido cargar · Reintentar";
      } finally {
        loading = false;
      }
    };
    moreBtn.addEventListener("click", loadMore);
    if ("IntersectionObserver" in window) {
      const io = new IntersectionObserver((entries) => {
        if (!sentinel.isConnected) { io.disconnect(); return; }
        if (entries.some((e) => e.isIntersecting)) loadMore();
      }, { rootMargin: "900px 0px" });
      io.observe(sentinel);
    }

    // Buscador: filtra sin repintar la pantalla (no se pierde el foco) y deja el texto en la ruta.
    const search = el("input", { type: "search", className: "filter", value: cur.q || "",
      placeholder: "Buscar: comercio, concepto, importe…", attrs: { "aria-label": "Buscar por comercio, concepto, categoría, etiqueta o importe", enterkeyhint: "search" } });
    let timer = null, gen = 0;
    search.addEventListener("keydown", (e) => { if (e.key === "Enter") search.blur(); });
    search.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const q = search.value.trim();
        if ((cur.q || "") === q) return;
        cur = { ...cur, q };
        if (!q) delete cur.q;
        history.replaceState(history.state, "", diaryHash(cur));
        const my = ++gen;
        listBox.classList.add("busy");
        try {
          const d = await fetchFirst(cur);
          if (my !== gen) return;
          diary = d;
          paintList();
        } catch (err) {
          if (err instanceof PinRequired) { render(); return; }
          listBox.replaceChildren(el("p", { className: "muted empty", textContent: `No se ha podido buscar: ${err.message}` }));
        } finally {
          if (my === gen) listBox.classList.remove("busy");
        }
      }, 350);
    });

    // Más filtros: etiqueta y fechas.
    const first = accounts.map((a) => a.first).filter(Boolean).sort()[0] || "";
    const tagSel = el("select", { attrs: { "aria-label": "Etiqueta" } }, [el("option", { value: "", textContent: "Todas" }),
      ...tagRes.tags.map((t) => el("option", { value: t.name, textContent: `#${t.name} (${t.count})` })),
      cur.etiqueta && !tagRes.tags.some((t) => t.name === cur.etiqueta) ? el("option", { value: cur.etiqueta, textContent: `#${cur.etiqueta}` }) : null]);
    tagSel.value = cur.etiqueta || "";
    tagSel.addEventListener("change", () => setF({ etiqueta: tagSel.value }));
    const dateIn = (key, label) => {
      const n = el("input", { type: "date", value: cur[key] || "", attrs: { "aria-label": label, ...(first ? { min: first } : {}), max: TODAY } });
      n.addEventListener("change", () => setF({ [key]: n.value }));
      return n;
    };
    const extraN = ["etiqueta", "desde", "hasta"].filter((k) => cur[k]).length;
    const xf = el("div", { className: `xf${extraN || diaryExtra ? "" : " hidden"}` }, [
      el("label", {}, [el("span", { textContent: "Etiqueta" }), tagSel]),
      el("label", {}, [el("span", { textContent: "Desde" }), dateIn("desde", "Desde")]),
      el("label", {}, [el("span", { textContent: "Hasta" }), dateIn("hasta", "Hasta")]),
    ]);
    const anyFilter = Object.keys(cur).length > 0;

    paintList();
    return [
      topbar("Diario"),
      el("main", {}, [
        el("h2", { className: "h2first", textContent: "Saldos" }),
        saldosBlock(accounts, accRes.total, sel, (a) => setF({ cuenta: sel === a ? "" : a.id })),
        syncAlerts(accounts),
        el("h2", { textContent: sel ? `Movimientos · ${sel.bank} ${tailOf(sel)}` : "Movimientos" }),
        sel ? closingNote(sel, diary.balance) : null,
        search,
        subtabs(TIPOS, cur.tipo || "todos", (k) => setF({ tipo: k === "todos" ? "" : k }), "small"),
        el("div", { className: "chips" }, MARCAS.map(([k, l]) => el("button", { type: "button", className: `chip${cur.marca === k ? " on" : ""}`,
          textContent: cur.marca === k ? `${l} ✕` : l, attrs: { "aria-pressed": String(cur.marca === k) },
          onclick: () => setF({ marca: cur.marca === k ? "" : k }) }))),
        el("div", { className: "chips" }, [
          el("button", { type: "button", className: `chip${extraN ? " on" : ""}`, textContent: `Más filtros${extraN ? ` (${extraN})` : ""} ${diaryExtra || extraN ? "▴" : "▾"}`,
            attrs: { "aria-expanded": String(Boolean(diaryExtra || extraN)) },
            onclick: (e) => { diaryExtra = xf.classList.toggle("hidden") === false; e.currentTarget.textContent = `Más filtros${extraN ? ` (${extraN})` : ""} ${diaryExtra ? "▴" : "▾"}`; } }),
          sel ? el("button", { type: "button", className: "chip on", textContent: `${sel.bank} ${tailOf(sel)} ✕`, attrs: { "aria-label": "Ver todas las cuentas" },
            onclick: () => setF({ cuenta: "" }) }) : null,
          anyFilter ? el("button", { type: "button", className: "chip", textContent: "Quitar filtros", onclick: () => go("#/diario", { replace: true, keep: true }) }) : null,
        ]),
        xf,
        listBox, moreBtn, sentinel,
        el("footer", { className: "note" }, [
          el("p", { textContent: "Todos los movimientos de todas las cuentas, también los que no computan como ingreso ni gasto (traspasos entre cuentas propias, liquidaciones de tarjeta…)." }),
          el("p", { textContent: "El banco solo da la fecha de cada movimiento, no la hora: dentro de un mismo día el orden no es el real." }),
          el("p", { textContent: "Toque una cuenta para ver solo sus movimientos y su saldo al cierre de cada día." }),
          PIN ? el("button", { className: "lockbtn", type: "button", textContent: "Bloquear el panel", onclick: lockPanel }) : null,
        ]),
      ]),
      nav("diario"),
    ];
  }

  // Copia al portapapeles; en http (red de casa) no hay API de portapapeles y se usa execCommand.
  function copyButton(text, label) {
    const b = el("button", { type: "button", className: "chip", textContent: label });
    b.addEventListener("click", async () => {
      let ok = false;
      try {
        if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); ok = true; }
      } catch { ok = false; }
      if (!ok) {
        const ta = el("textarea", { value: text, readOnly: true, className: "offscreen", attrs: { "aria-hidden": "true" } });
        document.body.append(ta);
        ta.select();
        try { ok = document.execCommand("copy"); } catch { ok = false; }
        ta.remove();
      }
      b.textContent = ok ? "Copiado ✓" : "No se ha podido copiar";
      setTimeout(() => { b.textContent = label; }, 1800);
    });
    return b;
  }

  async function movementView(id) {
    const t = await api("movement", { id });
    const a = t.account_info;
    const kv = (pairs) => el("dl", { className: "kv" }, pairs.filter(([, v]) => v !== null && v !== undefined && v !== "")
      .flatMap(([k, v]) => [el("dt", { textContent: k }), el("dd", {}, v)]));
    const marks = marksOf(t);
    return [
      detailBar(L.titleOf(t), `${L.dayHeading(t.date, TODAY)} · ${a.bank}${a.iban_tail ? ` ··${a.iban_tail}` : ""}`),
      el("main", {}, [
        el("div", { className: "ghead" }, [
          el("span", { className: `big amt${t.amount > 0 ? " posv" : ""}`, textContent: L.signedEur(t.amount) }),
          marks.length ? el("div", { className: "tx-meta" }, marks) : null,
        ]),
        el("h2", { textContent: "Movimiento" }),
        el("div", { className: "card" }, kv([
          ["Concepto", t.description || "—"],
          ["Comercio", t.merchant && /\p{L}/u.test(t.merchant) ? t.merchant : null], // la cola del IBAN de ING no es un comercio
          ["Fecha", L.cap(L.dayLong(t.date))],
          ["Cuenta", `${a.bank}${a.iban_tail ? ` ··${a.iban_tail}` : ""}${a.name ? ` · ${a.name}` : ""}`],
          ["Categoría", t.category],
          ["Etiquetas", t.tags.map((x) => `#${x}`).join(" ")],
          ["Recibo", t.is_bill ? "sí" : "no"],
          ["Computa", t.neutral ? "no: no cuenta como ingreso ni como gasto" : "sí"],
          ["Categoría a mano", t.manual ? "sí: las reglas no la cambian" : null],
        ])),
        el("h2", { textContent: "Datos del banco" }),
        el("div", { className: "card" }, t.source === "feed"
          ? (t.bank_data.length ? kv(t.bank_data.map((x) => [x.label, x.value])) : el("p", { className: "muted", textContent: "El banco no envió más datos." }))
          : el("p", { className: "muted", textContent: t.source === "csv"
            ? "Importado de un extracto descargado del banco: no hay datos originales de la conexión bancaria."
            : "Sin datos originales del banco." })),
        t.raw ? el("details", { className: "raw" }, [el("summary", { textContent: "Ver el dato original completo" }),
          el("pre", { textContent: JSON.stringify(t.raw, null, 2) })]) : null,
        el("h2", { textContent: "Identificador" }),
        el("div", { className: "card idrow" }, [el("code", { textContent: t.id }), copyButton(t.id, "Copiar id")]),
      ]),
      nav("diario"),
    ];
  }

  /* ---------- Mes ---------- */

  async function monthView(ym, tipo) {
    const from = L.monthStart(ym), to = L.monthEnd(ym);
    const i = MONTHS.indexOf(ym);
    const sm = MONTHS.slice(Math.max(0, i - 5), i + 1);
    const [b, ser] = await Promise.all([breakdownOf(from, to), sm.length > 1 ? seriesOf(sm[0], ym) : null]);
    const aggs = aggsOf(b);
    const sp = ser ? { months: ser.months, values: tipo === "ingresos" ? ser.income : ser.expense, partial: ym === CUR } : null;
    return [
      monthBar(ym, tipo),
      el("main", {}, [
        subtabs([["ingresos", "Ingresos", aggs.ingresos.total], ["gastos", "Gastos", aggs.gastos.total]], tipo,
          (t) => go(H("mes", ym, t), { replace: true })),
        ...categoryBlock(aggs[tipo], tipo, { origin: "mes", from, to, months: 1, spark: sp }),
        el("footer", { className: "note" }, [
          ym === CUR ? el("p", { textContent: `Mes en curso: movimientos hasta el día ${CUT.day}.` }) : null,
          tipo === "gastos" && b.refunds > 0 ? el("p", {}, ["Las devoluciones (", amt(b.refunds), ") se han descontado del gasto de su categoría."]) : null,
          coverageNote(from),
          el("p", { textContent: "Cambie de mes con las flechas, tocando el nombre del mes o deslizando ⇆." }),
          PIN ? el("button", { className: "lockbtn", type: "button", textContent: "Bloquear el panel", onclick: lockPanel }) : null,
        ]),
      ]),
      nav("mes"),
    ];
  }

  /* ---------- detalle de un periodo cualquiera: categoría → subcategorías → movimientos ---------- */
  // #/rango/<origen>/<desde>/<hasta>/<tipo>/g/<categoría>[/mov]  ·  #/rango/<origen>/<desde>/<hasta>/<tipo>/c/<subcategoría>

  async function rangeView(p) {
    const [, origin, from, to, tipo0, kind, key, extra] = p;
    const tipo = tipoOf(tipo0);
    const label = L.rangeLabel(from, to);
    const navKey = ["mes", "evolucion", "tendencias"].includes(origin) ? origin : "mes";
    if (kind === "g" && extra !== "mov") {
      const agg = aggsOf(await breakdownOf(from, to))[tipo];
      const g = agg.groups.find((x) => x.group === key);
      if (!g) {
        return [detailBar(L.shown(key), label), el("main", {}, el("p", { className: "muted empty", textContent: "Sin movimientos en este periodo." })), nav(navKey)];
      }
      return [
        detailBar(L.shown(g.group), label),
        el("main", {}, [
          el("div", { className: "ghead" }, [el("span", { className: "big amt", textContent: eur.format(g.total) }),
            el("div", { className: "muted", textContent: `${g.count} movimientos · ${L.share(g.total, agg.total)} % de los ${word(tipo)}` })]),
          el("h2", { textContent: "Por subcategoría" }),
          el("div", { className: "cards" }, g.categories.map((c) => el("button", { className: "gcard", type: "button",
            onclick: () => go(H("rango", origin, from, to, tipo, "c", c.category)) }, [
            el("span", { className: "dot", style: { background: L.colorFor(c.category) } }),
            el("span", { className: "n", textContent: c.label }),
            el("span", { className: "s", textContent: `${L.share(c.total, g.total)} % · ${c.count} mov.` }),
            el("span", { className: `v${c.total < 0 ? " neg" : ""}` }, amt(c.total)),
          ]))),
          el("button", { className: "link", type: "button", textContent: `Ver los ${g.count} movimientos`,
            onclick: () => go(H("rango", origin, from, to, tipo, "g", g.group, "mov")) }),
        ]),
        nav(navKey),
      ];
    }
    const rows = await txOf(from, to, tipo, kind, key);
    const net = sum(rows.map((t) => t.amount));
    const first = rows[0];
    const isSub = kind === "c" && first && first.group !== key;
    return [
      detailBar(isSub ? first.label : L.shown(key), isSub ? `${L.shown(first.group)} · ${label}` : label),
      el("main", {}, [
        el("div", { className: "ghead" }, [el("span", { className: "big amt", textContent: eur.format(nz(tipo === "ingresos" ? net : -net)) }),
          el("div", { className: "muted", textContent: `${rows.length} movimientos` })]),
        ...txPanel(rows, tipo, kind === "g"),
      ]),
      nav(navKey),
    ];
  }

  /* ---------- Evolución › Mes a mes ---------- */

  function trendPill(d) {
    if (d.flat) return el("span", { className: "trend flat", textContent: "≈ igual" });
    const size = d.pct === null ? eur0.format(Math.abs(d.diff)) : `${Math.abs(d.pct).toFixed(0)} %`;
    return el("span", { className: `trend ${d.tone}`, textContent: `${d.diff > 0 ? "▲" : "▼"} ${size}` });
  }

  async function paceView() {
    const day = CUT.day;
    const [full, cut] = await Promise.all([monthlyRows(), monthlyRows(day)]);
    const fullBy = new Map(full.map((r) => [r.month, r]));
    const cutBy = new Map(cut.map((r) => [r.month, r]));
    const { now, refs } = L.paceRefs((m) => (cutBy.get(m) ? cutBy.get(m).expense : 0), CUR, CLOSED);
    const refLabel = (r) => (r.n === 1 ? `${L.cap(L.monthInitial(r.ms[0]))} a día ${day}` : `Media ${r.ms.length} meses`);
    const hero = el("section", { className: "hero" }, [
      el("div", { className: "lead", textContent: `Gastos de ${L.monthName(CUR)} · a día ${day} de ${CUT.days}` }),
      el("div", { className: "num amt", textContent: eur0.format(now) }),
      el("div", { className: "sub", textContent: L.verdict(refs) }),
      refs.length ? el("div", { className: "hstats" }, refs.map((r) => el("div", { className: "hstat" }, [
        el("span", { className: "t", textContent: refLabel(r) }),
        el("span", { className: "v amt", textContent: eur0.format(r.v) }),
        trendPill(r.d),
        el("span", { className: "d" }, r.d.flat ? "prácticamente igual" : [amt(Math.abs(r.d.diff)), r.d.diff > 0 ? " más" : " menos"]),
      ]))) : null,
    ]);

    // Lista de meses (más reciente primero): el ahorro manda.
    const rows = [...MONTHS].reverse().map((m) => (m === CUR ? { ...cutBy.get(m), partial: true } : { ...fullBy.get(m), partial: false }))
      .filter((r) => r.month);
    const closedRows = rows.filter((r) => !r.partial);
    const maxNet = Math.max(...closedRows.map((r) => r.net), 1);
    const avgNet = closedRows.length ? sum(closedRows.map((r) => r.net)) / closedRows.length : 0;
    const monthRow = (r) => el("button", { className: `mrow2${r.partial ? " inprog" : ""}`, type: "button", onclick: () => go(H("mes", r.month, "gastos")) }, [
      el("span", { className: "ml" }, [
        el("span", { className: "n" }, [L.cap(L.monthName(r.month)), r.partial ? el("span", { className: "tag", textContent: "en curso" }) : null]),
        el("span", { className: "s" }, ["Ingresos ", amt(r.income), " · Gastos ", amt(r.expense)]),
      ]),
      el("span", { className: "mr" }, [
        el("span", { className: `sv amt${r.net < 0 ? " loss" : ""}`, textContent: eur0.format(r.net) }),
        el("span", { className: "s", textContent: r.partial ? `provisional · a día ${day}`
          : r.income > 0 ? `${Math.round((r.net / r.income) * 100)} % ahorrado` : "" }),
      ]),
      !r.partial && r.net > 0 ? el("span", { className: "sbar" }, el("i", { style: { width: `${(r.net / maxNet) * 100}%` } })) : null,
    ]);
    const list = [];
    let year = null;
    for (const r of rows) {
      const y = r.month.slice(0, 4);
      if (y !== year) {
        year = y;
        const yr = closedRows.filter((x) => x.month.startsWith(y));
        const span = yr.length === 12 ? "año completo" : yr.length ? `${L.monthInitial(yr[yr.length - 1].month)}–${L.monthInitial(yr[0].month)}` : "";
        list.push(el("div", { className: "yhead" }, [el("b", { textContent: y }),
          yr.length ? el("span", {}, ["Ahorro ", amt(sum(yr.map((x) => x.net))), ` · ${span}`]) : null]));
      }
      list.push(monthRow(r));
    }
    const refSpan = (r) => (r.n === 1 ? L.monthLabel(r.ms[0]) : `${L.monthInitial(r.ms[0])}–${L.monthShort(r.ms[r.ms.length - 1])}`);
    return [
      hero,
      el("h2", { textContent: "Ahorro mes a mes" }),
      el("p", { className: "muted lead2" }, ["Ahorro medio: ", amt(avgNet), ` al mes en ${closedRows.length} meses cerrados. Toque un mes para ver su detalle.`]),
      ...list,
      el("footer", { className: "note" }, [
        refs.length ? el("p", { textContent: `Comparaciones con el gasto acumulado del día 1 al ${day} de cada mes: ${refs.map((r) => `${r.n === 1 ? "mes anterior" : `media ${r.ms.length} meses`} = ${refSpan(r)}`).join("; ")}.` }) : null,
        el("p", { textContent: "Importes netos de devoluciones. El ahorro del mes en curso es provisional: los ingresos suelen entrar a final de mes." }),
        coverageNote(L.monthStart(MONTHS[0])),
      ]),
    ];
  }

  /* ---------- Evolución › Acumulado ---------- */

  function barsChart(rows, { split, selected, onSelect }) {
    const colW = rows.length <= 13 ? Math.max(27, Math.min(64, Math.floor(320 / rows.length))) : 30;
    const h = 200, padT = 12, padB = 26, bw = Math.min(20, Math.round(colW * 0.37));
    const w = Math.max(330, rows.length * colW + 12);
    const vals = rows.flatMap((r) => [r.income, r.expense, r.net]);
    const max = Math.max(...vals, 1);
    const min = Math.min(...vals, 0);
    const y = (v) => padT + ((max - v) / (max - min)) * (h - padT - padB);
    const root = svg("svg", { viewBox: `0 0 ${w} ${h}`, width: w, height: h, class: "bars", role: "img", "aria-label": "Ingresos, gastos y ahorro por mes" });
    const ink = cssVar("--fg", "#22201c");
    root.append(svg("line", { x1: 0, x2: w, y1: y(0), y2: y(0), stroke: cssVar("--axis", "#d8cfc0") }));
    const x0 = (w - rows.length * colW) / 2;
    const bar = (x, v, fill) => svg("rect", { x, y: Math.min(y(v), y(0)), width: bw, height: Math.max(1, Math.abs(y(v) - y(0))), rx: 2, fill });
    rows.forEach((r, i) => {
      const cx = x0 + i * colW;
      const g = svg("g", { opacity: selected >= 0 && i !== selected ? 0.35 : 1 });
      g.append(bar(cx + 3, r.income, INC));
      if (split) g.append(bar(cx + 4 + bw, r.expense, VARC), bar(cx + 4 + bw, Math.max(0, Math.min(r.bills, r.expense)), EXP));
      else g.append(bar(cx + 4 + bw, r.expense, EXP));
      g.append(svg("text", { x: cx + colW / 2, y: h - 12, "text-anchor": "middle", "font-size": 9.5, fill: ink, opacity: 0.75 }, [L.monthInitial(r.month)]));
      if (i === 0 || r.month.endsWith("-01")) {
        g.append(svg("text", { x: cx + colW / 2, y: h - 2, "text-anchor": "middle", "font-size": 8, fill: ink, opacity: 0.5 }, [r.month.slice(0, 4)]));
      }
      root.append(g);
    });
    const pts = rows.map((r, i) => ({ x: x0 + i * colW + colW / 2, y: y(r.net) }));
    if (pts.length > 1) root.append(svg("polyline", { points: pts.map((pt) => `${pt.x.toFixed(1)},${pt.y.toFixed(1)}`).join(" "), fill: "none", stroke: ink, "stroke-width": 1.6, "stroke-linejoin": "round" }));
    pts.forEach((pt) => root.append(svg("circle", { cx: pt.x, cy: pt.y, r: 2.8, fill: ink })));
    rows.forEach((r, i) => {
      const hit = svg("rect", { x: x0 + i * colW, y: 0, width: colW, height: h, fill: "transparent", class: "hit" });
      hit.addEventListener("click", () => onSelect(i));
      root.append(hit);
    });
    return root;
  }

  function monthCard(r) {
    return el("div", { className: "card" }, [
      el("div", { className: "dhead" }, [el("b", { textContent: L.monthLabel(r.month) }), el("span", { className: `v amt${r.net < 0 ? " negv" : ""}`, textContent: eur0.format(r.net) })]),
      el("div", { className: "dline" }, ["Ingresos ", amt(r.income), " · Gastos ", amt(r.expense), " (recibos ", amt(Math.min(r.bills, r.expense)), ")",
        r.income > 0 ? ` · tasa ${L.pct((r.net / r.income) * 100)}` : ""]),
      el("button", { className: "link", type: "button", textContent: "Ver mes ›", onclick: () => go(H("mes", r.month, "gastos")) }),
    ]);
  }

  async function accumulatedView(p) {
    const period = ACC_PERIODS.some(([k]) => k === p[2]) ? p[2] : "3";
    const tipo = tipoOf(p[3]);
    const year = CUR.slice(0, 4);
    const nav2 = (per, t, keep = false) => go(H("evolucion", "acumulado", per, t), { replace: true, keep });
    const periods = chipRow(ACC_PERIODS, period, (k) => nav2(k, tipo), "Periodo");
    const months = L.accumulatedMonths(period, CLOSED, year);
    const n = months.length;
    if (!n) {
      return [periods, el("p", { className: "muted empty", textContent: period === "ano"
        ? `Aún no hay meses cerrados en ${year}: ${L.monthLabel(CUR)} está en curso.` : "Aún no hay meses cerrados." })];
    }
    const from = L.monthStart(months[0]), to = L.monthEnd(months[n - 1]);
    const [full, b] = await Promise.all([monthlyRows(), breakdownOf(from, to)]);
    const by = new Map(full.map((r) => [r.month, r]));
    const rows = months.map((m) => by.get(m)).filter(Boolean);
    const aggs = aggsOf(b);
    const inc = b.income.total, exp = b.expense.total, net = b.net, bills = b.bills;

    let split = localStorage.getItem(PREF.split) === "1";
    let selIdx = -1;
    const chartBox = el("div", { className: "chart-scroll", attrs: { "data-noswipe": "1" } });
    const legend = el("div", { className: "legend2" });
    const selBox = el("div", { className: "detail" });
    const paintChart = () => {
      chartBox.replaceChildren(barsChart(rows, { split, selected: selIdx, onSelect: (i) => { selIdx = selIdx === i ? -1 : i; paintChart(); } }));
      selBox.replaceChildren(selIdx >= 0 ? monthCard(rows[selIdx]) : el("p", { className: "hint2", textContent: "Toque un mes para ver sus cifras." }));
      legend.replaceChildren(...[
        el("span", {}, [el("i", { style: { background: INC } }), "Ingresos"]),
        el("span", {}, [el("i", { style: { background: EXP } }), split ? "Recibos" : "Gastos"]),
        split ? el("span", {}, [el("i", { style: { background: VARC } }), "Variable"]) : null,
        el("span", { textContent: "● Ahorro" }),
        el("button", { type: "button", className: `switch${split ? " on" : ""}`, textContent: split ? "Recibos ✓" : "Separar recibos",
          attrs: { "aria-pressed": String(split) },
          onclick: () => { split = !split; localStorage.setItem(PREF.split, split ? "1" : "0"); paintChart(); } }),
      ].filter(Boolean));
    };
    paintChart();
    requestAnimationFrame(() => { chartBox.scrollLeft = chartBox.scrollWidth; });
    const stat = (t, v) => el("div", { className: "hstat" }, [el("span", { className: "t", textContent: t }), el("span", { className: "v amt", textContent: eur0.format(v) })]);
    const billsPct = exp > 0 ? Math.max(0, Math.min(100, (bills / exp) * 100)) : 0;
    return [
      periods,
      el("section", { className: "hero" }, [
        el("div", { className: "lead", textContent: `${period === "ano" ? "Año actual" : "Acumulado"} · ${L.rangeLabel(from, to)}` }),
        el("div", { className: "sub", textContent: `${n} ${n === 1 ? "mes cerrado" : "meses cerrados"}${inc > 0 ? ` · tasa de ahorro ${L.pct((net / inc) * 100)}` : ""}` }),
        el("div", { className: "hstats" }, [stat("Ingresos", inc), stat("Gastos", exp), stat("Ahorro", net)]),
        el("div", { className: "sub avg" }, ["Al mes: ingresos ", amt(inc / n), " · gastos ", amt(exp / n), " · ahorro ", amt(net / n)]),
      ]),
      el("h2", { textContent: "Ingresos y gastos por mes" }),
      el("div", { className: "chart-card" }, [legend, chartBox]),
      selBox,
      el("h2", { textContent: "Acumulado por categoría" }),
      subtabs([["ingresos", "Ingresos", aggs.ingresos.total], ["gastos", "Gastos", aggs.gastos.total]], tipo, (t) => nav2(period, t, true)),
      ...categoryBlock(aggs[tipo], tipo, { origin: "evolucion", from, to, months: n }).slice(1),
      tipo === "gastos" ? el("h2", { textContent: "Fijo frente a variable" }) : null,
      tipo === "gastos" ? el("div", { className: "card" }, [
        el("div", {}, ["El ", el("b", { textContent: `${Math.round(billsPct)} %` }), " del gasto fueron recibos: ", amt(bills / n),
          " al mes. El resto, ", amt((exp - bills) / n), " al mes, fue gasto variable."]),
        el("div", { className: "stack" }, [el("span", { style: { width: `${billsPct}%` } }), el("span", { style: { width: `${100 - billsPct}%` } })]),
        el("div", { className: "dline", textContent: "Recibos: cargos marcados como recibo (domiciliaciones, suscripciones…)." }),
      ]) : null,
      el("footer", { className: "note" }, [
        el("p", { textContent: `Solo meses cerrados: ${L.monthLabel(CUR)} está en curso y no se incluye.` }),
        el("p", { textContent: "Importes netos de devoluciones." }),
        coverageNote(from),
      ]),
    ];
  }

  /* ---------- Evolución › Periodo contra periodo ---------- */

  async function periodView(p) {
    const type = PERIOD_TYPES.some(([k]) => k === p[2]) ? p[2] : "mes";
    const tipo = tipoOf(p[5]);
    const pers = L.periodsOf(type, MONTHS, CUR);
    const A = pers.find((x) => x.id === p[3]) || pers[pers.length - 1];
    const iA = pers.indexOf(A);
    const B = pers.find((x) => x.id === p[4] && x.id !== A.id) || pers[iA - 1] || pers[iA + 1];
    const nav2 = (t, a, b, tp, keep = false) => go(H("evolucion", "periodo", t, a, b, tp), { replace: true, keep });
    const typeTabs = subtabs(PERIOD_TYPES, type, (k) => nav2(k, "-", "-", tipo), "small");
    if (!B) return [typeTabs, el("p", { className: "muted empty", textContent: "No hay otro periodo con el que comparar." })];

    const optText = (x) => x.label + (type === "trimestre" || type === "semestre"
      ? ` · ${L.monthInitial(x.all[0])}–${L.monthInitial(x.all[x.all.length - 1])}` : "") + (x.partial ? " (en curso)" : "");
    const mkSel = (value, exclude, onChange, aria) => {
      const s = el("select", { attrs: { "aria-label": aria } }, [...pers].reverse().filter((x) => x.id !== exclude)
        .map((x) => el("option", { value: x.id, textContent: optText(x) })));
      s.value = value;
      s.addEventListener("change", () => onChange(s.value));
      return s;
    };
    const short = (x) => (type === "mes" ? L.monthShort(x.id) : x.label);

    const cut = L.comparisonCut(A, B, CUR, CUT.day);
    const rA = L.periodRange(A, cut), rB = L.periodRange(B, cut);
    const [bA, bB] = await Promise.all([breakdownOf(rA.from, rA.to), breakdownOf(rB.from, rB.to)]);
    const agg = { A: aggsOf(bA), B: aggsOf(bB) };
    const nets = { A: bA.net, B: bB.net };
    const tot = (side, k) => (k === "ahorro" ? nets[side] : agg[side][k].total);
    const dExp = tot("A", "gastos") - tot("B", "gastos");
    const hstat = (t, key, kind) => {
      const va = tot("A", key), vb = tot("B", key);
      const d = L.delta(va, vb, kind);
      return el("div", { className: "hstat" }, [el("span", { className: "t", textContent: t }), el("span", { className: "v amt", textContent: eur0.format(va) }),
        d.flat ? el("span", { className: "d", textContent: "≈ igual" })
          : el("span", { className: "d" }, [d.diff > 0 ? "▲ " : "▼ ", amt(Math.abs(d.diff)), d.pct === null ? "" : ` · ${Math.abs(d.pct).toFixed(0)} %`]),
        el("span", { className: "d" }, ["vs ", amt(vb)])]);
    };

    // Qué ha cambiado, por categoría (desplegable a subcategorías y a los movimientos de cada periodo).
    const kind = tipo === "gastos" ? "cost" : "gain";
    const diffs = L.diffGroups(agg.A[tipo].groups, agg.B[tipo].groups);
    const maxAbs = Math.max(...diffs.map((x) => Math.abs(x.d)), 1);
    const open = new Set();
    let all = false;
    const listBox = el("div");
    const more = el("button", { className: "more", type: "button" });
    const toneOf = (va, vb) => L.delta(va, vb, kind).tone;
    const drow = (x) => {
      const isOpen = open.has(x.k);
      const up = x.d > 0;
      const tone = toneOf(x.va, x.vb);
      const wPct = (Math.abs(x.d) / maxAbs) * 50;
      const kids = [
        el("div", { className: "top2" }, [el("span", { textContent: L.shown(x.k) }), el("span", { className: tone }, [up ? "+" : "−", amt(Math.abs(x.d))])]),
        el("div", { className: "track" }, el("i", { style: { left: up ? "50%" : `${50 - wPct}%`, width: `${wPct}%`,
          background: tone === "worse" ? "var(--bad)" : tone === "better" ? "var(--good)" : "var(--tick)" } })),
        el("div", { className: "s" }, [amt(x.va), ` en ${short(A)} frente a `, amt(x.vb), ` en ${short(B)}`, isOpen ? "" : " · detalle ▾"]),
      ];
      if (isOpen) {
        const subs = L.diffSubcategories(x.a, x.b);
        const showSubs = subs.length > 1 || (subs[0] && subs[0].label !== "General");
        kids.push(el("div", { className: "subcats" }, [
          ...(showSubs ? subs.map((s) => el("div", { className: "r" }, [el("span", { textContent: s.label }),
            el("span", { className: toneOf(s.va, s.vb) }, [s.d >= 0 ? "+" : "−", amt(Math.abs(s.d)), el("span", { className: "muted" }, ["  (", amt(s.va), " / ", amt(s.vb), ")"])])])) : []),
          el("div", { className: "btns" }, [
            x.a ? el("button", { className: "link", type: "button", textContent: `Movimientos ${short(A)} ›`,
              onclick: (e) => { e.stopPropagation(); go(H("rango", "evolucion", rA.from, rA.to, tipo, "g", x.k, "mov")); } }) : null,
            x.b ? el("button", { className: "link", type: "button", textContent: `Movimientos ${short(B)} ›`,
              onclick: (e) => { e.stopPropagation(); go(H("rango", "evolucion", rB.from, rB.to, tipo, "g", x.k, "mov")); } }) : null,
          ]),
        ]));
      }
      return el("div", { className: "drow", attrs: { role: "button", tabindex: "0", "aria-expanded": String(isOpen) },
        onclick: () => { if (isOpen) open.delete(x.k); else open.add(x.k); paint(); } }, kids);
    };
    const paint = () => {
      listBox.replaceChildren(...(diffs.length ? (all ? diffs : diffs.slice(0, 10)).map(drow) : [el("p", { className: "muted", textContent: "Sin cambios." })]));
      more.textContent = all ? "Ver menos" : `Ver las ${diffs.length - 10} categorías restantes`;
      more.classList.toggle("hidden", diffs.length <= 10);
    };
    more.addEventListener("click", () => { all = !all; paint(); });
    paint();

    return [
      typeTabs,
      el("div", { className: "pp" }, [
        el("label", { className: "ppl" }, [el("span", { className: "muted", textContent: "Periodo" }), mkSel(A.id, null, (v) => nav2(type, v, B.id, tipo), "Periodo")]),
        el("label", { className: "ppl" }, [el("span", { className: "muted", textContent: "frente a" }), mkSel(B.id, A.id, (v) => nav2(type, A.id, v, tipo), "Comparar con")]),
      ]),
      el("section", { className: "hero" }, [
        el("div", { className: "lead", textContent: `${A.label} frente a ${B.label}` }),
        el("div", { className: "num amt", textContent: `${dExp >= 0 ? "+" : "−"}${eur0.format(Math.abs(dExp))}` }),
        el("div", { className: "sub", textContent: L.delta(tot("A", "gastos"), tot("B", "gastos"), "cost").flat ? "gasto prácticamente igual" : dExp > 0 ? "más de gasto" : "menos de gasto" }),
        cut ? el("div", { className: "sub same", textContent: `A la misma fecha: ${L.rangeLabel(rA.from, rA.to)} frente a ${L.rangeLabel(rB.from, rB.to)}` }) : null,
        el("div", { className: "hstats" }, [hstat("Ingresos", "ingresos", "gain"), hstat("Gastos", "gastos", "cost"), hstat("Ahorro", "ahorro", "gain")]),
      ]),
      el("h2", { textContent: "Qué ha cambiado, por categoría" }),
      subtabs([["ingresos", "Ingresos"], ["gastos", "Gastos"]], tipo, (t) => nav2(type, A.id, B.id, t, true)),
      listBox, more,
      el("footer", { className: "note" }, [
        el("p", { textContent: cut ? `${cut.per.label} está en curso: ambos periodos se comparan hasta la misma fecha.` : "Periodos completos." }),
        el("p", { textContent: "Importes netos de devoluciones. Toque una categoría para ver sus subcategorías y movimientos." }),
        coverageNote(rA.from, rB.from),
      ]),
    ];
  }

  async function evolutionView(p) {
    const mode = ["acumulado", "periodo"].includes(p[1]) ? p[1] : "meses";
    const body = mode === "periodo" ? await periodView(p) : mode === "acumulado" ? await accumulatedView(p) : await paceView();
    return [
      topbar("Evolución"),
      el("main", {}, [
        subtabs([["meses", "Mes a mes"], ["acumulado", "Acumulado"], ["periodo", "Periodo contra periodo"]], mode,
          (k) => go(H("evolucion", k), { replace: true }), "tabs2 tabs3"),
        ...body,
      ]),
      nav("evolucion"),
    ];
  }

  /* ---------- Tendencias ---------- */

  let trendSel = null;
  try { trendSel = JSON.parse(localStorage.getItem(PREF.trend) || "null"); } catch { trendSel = null; }
  const trendPick = {};

  function miniBars(vals, months, { color, avg, selected, onSelect }) {
    const w = 330, h = 96, padT = 8, padB = 16, n = vals.length;
    const colW = (w - 8) / n, bw = Math.min(16, colW - 6);
    const max = Math.max(...vals, avg, 1), min = Math.min(...vals, 0);
    const y = (v) => padT + ((max - v) / (max - min)) * (h - padT - padB);
    const id = `h${Math.random().toString(36).slice(2, 8)}`;
    const root = svg("svg", { viewBox: `0 0 ${w} ${h}`, class: "mini", "aria-hidden": "true" });
    const ink = cssVar("--fg", "#22201c");
    root.append(svg("defs", {}, [svg("pattern", { id, width: 5, height: 5, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" },
      [svg("line", { x1: 0, y1: 0, x2: 0, y2: 5, stroke: color, "stroke-width": 2 })])]));
    vals.forEach((v, i) => {
      const cx = 4 + i * colW + colW / 2;
      const partial = months[i] === CUR;
      root.append(svg("rect", { x: cx - bw / 2, y: Math.min(y(v), y(0)), width: bw, height: Math.max(1.5, Math.abs(y(v) - y(0))), rx: 2,
        fill: partial ? `url(#${id})` : color, stroke: partial ? color : "none", opacity: selected !== undefined && selected !== i ? 0.35 : 1 }));
      root.append(svg("text", { x: cx, y: h - 3, "text-anchor": "middle", "font-size": 9, fill: ink, opacity: 0.65 }, [L.monthInitial(months[i]).slice(0, 1)]));
    });
    root.append(svg("line", { x1: 4, x2: w - 4, y1: y(avg), y2: y(avg), stroke: ink, "stroke-dasharray": "4 3", "stroke-width": 1, opacity: 0.6 }));
    vals.forEach((v, i) => {
      const hit = svg("rect", { x: 4 + i * colW, y: 0, width: colW, height: h, fill: "transparent", class: "hit" });
      hit.addEventListener("click", () => onSelect(i));
      root.append(hit);
    });
    return root;
  }

  function openPicker(options, totals, nMonths, kindOf, onPick) {
    const close = () => sheet.remove();
    const card = (g) => el("button", { className: "gcard", type: "button", onclick: () => { close(); onPick(g); } }, [
      el("span", { className: "dot", style: { background: L.colorFor(g) } }), el("span", { className: "n", textContent: g }),
      el("span", { className: "s" }, [`${nMonths} meses: `, amt(totals[g])]),
      el("span", { className: "v" }, [amt(totals[g] / Math.max(1, nMonths)), el("span", { className: "s", textContent: "al mes" })]),
    ]);
    const section = (title, opts) => (opts.length
      ? [el("h2", { textContent: title }), el("div", { className: "cards" }, opts.map(card))] : []);
    const expenseOpts = options.filter((g) => kindOf(g) === "gastos");
    const incomeOpts = options.filter((g) => kindOf(g) === "ingresos");
    const sheet = el("div", { className: "backdrop", onclick: (e) => { if (e.target === e.currentTarget) close(); } }, el("div", { className: "sheet", attrs: { role: "dialog", "aria-label": "Añadir categoría" } }, [
      el("div", { className: "grab" }),
      el("h2", { textContent: "Añadir categoría" }),
      options.length ? el("div", {}, [...section("Gastos", expenseOpts), ...section("Ingresos", incomeOpts)])
        : el("p", { className: "muted", textContent: "No quedan más categorías." }),
    ]));
    document.body.append(sheet);
  }

  async function trendsView() {
    const last12 = CLOSED.slice(-12);
    const months = [...last12, CUR];
    const ser = await seriesOf(months[0], CUR);
    const idx = new Map(ser.months.map((m, i) => [m, i]));
    const kindOf = (g) => (g in ser.income ? "ingresos" : "gastos");
    const seriesFor = (g) => (kindOf(g) === "ingresos" ? ser.income : ser.expense);
    const gv = (g, m) => { const s = seriesFor(g)[g]; return (s ? s[idx.get(m)] : 0) || 0; };
    const allGroups = [...new Set([...Object.keys(ser.income), ...Object.keys(ser.expense)])];
    const tot12 = Object.fromEntries(allGroups.map((g) => [g, sum(last12.map((m) => gv(g, m)))]));
    const ranked = allGroups.filter((g) => tot12[g] > 0).sort((a, b) => tot12[b] - tot12[a]);
    // Por defecto, las 5 categorías de gasto de mayor importe (como siempre); el ingreso se añade a mano.
    if (!Array.isArray(trendSel)) trendSel = ranked.filter((g) => kindOf(g) === "gastos").slice(0, 5);
    trendSel = trendSel.filter((g) => allGroups.includes(g));
    const save = () => localStorage.setItem(PREF.trend, JSON.stringify(trendSel));
    const card = (g) => {
      const kind = kindOf(g);
      const vals = months.map((m) => gv(g, m));
      const closedVals = vals.slice(0, -1);
      const avg = closedVals.length ? sum(closedVals) / closedVals.length : 0;
      const tr = L.trendOf(closedVals, kind === "ingresos");
      const maxI = closedVals.length ? closedVals.indexOf(Math.max(...closedVals)) : -1;
      const pick = trendPick[g];
      const selLine = el("div", { className: "tsel" });
      if (pick !== undefined) {
        const m = months[pick];
        selLine.append(el("span", {}, [L.monthLabel(m) + (m === CUR ? ` (a día ${CUT.day})` : "") + ": ", amt(vals[pick])]),
          Math.abs(vals[pick]) >= 0.005 ? el("button", { type: "button", textContent: "Ver movimientos ›",
            onclick: () => go(H("rango", "tendencias", L.monthStart(m), L.monthEnd(m), kind, "g", g, "mov")) }) : "");
      } else {
        selLine.append(el("span", { className: "muted", textContent: "Toque una barra para ver ese mes." }));
      }
      return el("div", { className: "tcard" }, [
        el("div", { className: "h" }, [el("b", { textContent: g }), el("span", { className: `t ${tr.tone}`, textContent: tr.text })]),
        closedVals.length ? el("div", { className: "s" }, ["Media ", amt(avg), "/mes · último mes ", amt(closedVals[closedVals.length - 1]),
          " · máx. ", amt(closedVals[maxI]), ` (${L.monthInitial(months[maxI])})`]) : null,
        el("div", { attrs: { "data-noswipe": "1" } }, miniBars(vals, months, { color: L.colorFor(g), avg, selected: pick,
          onSelect: (i) => { trendPick[g] = trendPick[g] === i ? undefined : i; render({ keep: true }); } })),
        selLine,
      ]);
    };
    return [
      topbar("Tendencias"),
      el("main", {}, [
        el("div", { className: "selrow" }, [
          ...trendSel.map((g) => el("button", { type: "button", className: "chip on", attrs: { "aria-label": `Quitar ${g}` },
            onclick: () => { trendSel = trendSel.filter((x) => x !== g); save(); render({ keep: true }); } },
          [el("span", { className: "dot2", style: { background: L.colorFor(g) } }), `${g} ✕`])),
          el("button", { type: "button", className: "chip", textContent: "＋ Añadir",
            onclick: () => openPicker(ranked.filter((g) => !trendSel.includes(g)), tot12, last12.length, kindOf,
              (g) => { trendSel = [...trendSel, g]; save(); render({ keep: true }); }) }),
        ]),
        trendSel.length ? null : el("p", { className: "muted empty", textContent: "Añada una categoría para ver su evolución." }),
        ...trendSel.map(card),
        el("footer", { className: "note" }, [el("p", { textContent: `Últimos ${last12.length} meses cerrados y ${L.monthLabel(CUR)} a día ${CUT.day} (rayado). Cada tarjeta tiene su propia escala; la línea discontinua es la media de los meses cerrados.` })]),
      ]),
      nav("tendencias"),
    ];
  }

  /* ---------- PIN ---------- */

  const waitText = L.waitLabel;

  function pinView() {
    const input = el("input", { type: "password", className: "pin-in", inputMode: "numeric", autocomplete: "off",
      maxLength: 12, required: true, attrs: { pattern: "[0-9]*", "aria-label": "PIN" } });
    const msg = el("p", { className: "pin-msg", attrs: { role: "alert" } });
    const btn = el("button", { className: "link", type: "submit", textContent: "Entrar" });
    const form = el("form", { className: "pin", attrs: { novalidate: "" } }, [
      el("img", { src: "/static/favicon.svg", width: 52, height: 52, alt: "" }),
      el("h1", { textContent: "Finanzas" }),
      el("p", { className: "muted", textContent: "Introduzca el PIN para ver el panel." }),
      input, btn, msg,
    ]);
    form.addEventListener("submit", async (e) => {
      e.preventDefault(); // envío por fetch: el formulario nunca navega
      if (!input.value) { input.focus(); return; }
      btn.disabled = true;
      msg.textContent = "";
      try {
        const r = await fetch("/api/login", { method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pin: input.value }) });
        if (r.ok) { resetData(); boot(); return; }
        const body = await r.json().catch(() => ({}));
        const wait = body.retry_after || 0;
        msg.textContent = r.status === 429 ? `Demasiados intentos fallidos. Inténtelo de nuevo en ${waitText(wait)}.`
          : wait ? `PIN incorrecto. Demasiados intentos: espere ${waitText(wait)}.` : body.error || `Error ${r.status}`;
      } catch {
        msg.textContent = "No se ha podido conectar con el panel.";
      }
      input.value = "";
      btn.disabled = false;
      input.focus();
    });
    setTimeout(() => input.focus(), 60);
    return [form];
  }

  async function lockPanel() {
    await fetch("/api/logout", { method: "POST", credentials: "same-origin" }).catch(() => null);
    resetData();
    app.replaceChildren(...pinView());
    window.scrollTo(0, 0);
  }

  /* ---------- tirar hacia abajo para sincronizar ---------- */

  // Al soltar pasado el umbral se pide al servidor una sincronización con los bancos (POST /api/sync), se espera a que
  // termine y se recargan los datos. El servidor la frena si ya hay una en marcha o es muy reciente (L.syncResult lo cuenta).
  const POLL_MS = 1500, POLL_MAX_MS = 330000; // el servidor cancela a los 300 s
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function syncCall(method) {
    const post = method === "POST";
    const r = await fetch("/api/sync", { method, credentials: "same-origin", cache: "no-store",
      headers: post ? { Accept: "application/json", "Content-Type": "application/json" } : { Accept: "application/json" },
      body: post ? "{}" : undefined });
    const body = await r.json().catch(() => ({}));
    if (r.status === 401 && body.auth === "pin") throw new PinRequired(body.error);
    if (!r.ok) throw new Error(body.error || `Error ${r.status}`);
    return body;
  }

  // Vista que hay realmente en pantalla (L.viewOf) o «» mientras carga, o si es un error o el PIN. Solo la lista del
  // Diario puede sincronizar; solo entonces el panel hace suyo el gesto (`ptr-on`) y el navegador pierde el suyo: en las
  // demás pestañas conserva su recarga nativa.
  let pullView = "";
  function setPullView(kind) {
    pullView = kind;
    document.documentElement.classList.toggle("ptr-on", SYNC && L.pullEnabled(kind));
  }

  function setupPull() {
    let syncing = false;
    let t = null; // gesto en curso: {x, y, active, pull}
    let toastTimer = 0;
    const txt = el("span", { className: "ptr-txt" });
    const ptr = el("div", { className: "ptr", attrs: { "aria-hidden": "true" } }, [el("i", { className: "ptr-ico" }), txt]);
    const toast = el("div", { className: "toast hidden", attrs: { role: "status", "aria-live": "polite" }, onclick: () => toast.classList.add("hidden") });
    document.body.append(ptr, toast);

    // `mode`: pull (tirando, aún corto) · reload (al soltar recarga la página) · sync (al soltar sincroniza) · busy (trabajando).
    // `drag`: sigue al dedo, sin animación.
    const HINT = {
      pull: "Tire para recargar",
      reload: "Recargar · siga para sincronizar",
      sync: "Suelte para sincronizar",
    };
    function paint(pull, mode, drag, label) {
      ptr.className = `ptr ${mode}${drag ? " drag" : ""}`;
      ptr.style.transform = `translate(-50%, ${Math.round(pull - 60)}px)`;
      ptr.style.opacity = String(Math.min(1, pull / 32));
      txt.textContent = label || HINT[mode] || HINT.pull;
    }
    function showToast({ tone, text }) {
      clearTimeout(toastTimer);
      toast.className = `toast ${tone}`;
      toast.textContent = text;
      toastTimer = setTimeout(() => toast.classList.add("hidden"), tone === "ok" || tone === "info" ? 5000 : 9000);
    }
    function finish() {
      syncing = false;
      app.removeAttribute("aria-busy");
      paint(0, "pull", false);
    }

    async function run() {
      syncing = true;
      app.setAttribute("aria-busy", "true");
      toast.classList.add("hidden");
      paint(L.PULL.hold, "busy", false, "Sincronizando…");
      let result;
      try {
        let snap = await syncCall("POST");
        const ran = snap.started === true || snap.state === "running"; // la lanzó este gesto o se unió a una en marcha
        const t0 = Date.now();
        while (snap.state === "running") {
          if (Date.now() - t0 > POLL_MAX_MS) throw new Error("la sincronización no termina");
          await sleep(POLL_MS);
          snap = await syncCall("GET");
        }
        result = L.syncResult(snap, ran);
      } catch (err) {
        if (err instanceof PinRequired) { // la sesión caducó: pantalla del PIN
          resetData();
          app.replaceChildren(...pinView());
          window.scrollTo(0, 0);
          finish();
          return;
        }
        const why = err instanceof TypeError ? "no hay conexión con el panel" : (err && err.message) || String(err);
        result = { tone: "error", text: `No se ha podido sincronizar: ${why}. Se muestran los datos que ya había.` };
      }
      paint(L.PULL.hold, "busy", false, "Actualizando…");
      resetData();
      await boot({ refreshing: true }); // recarga la vista actual con datos frescos (boot gestiona sus propios errores)
      finish();
      showToast(result);
    }

    // Tirón mediano: recarga completa de la página (la nativa del navegador). Revalida también el JS y el CSS, y conserva la ruta.
    function reloadPage() {
      syncing = true; // ignora nuevos gestos hasta que la página se cierre
      toast.classList.add("hidden");
      paint(L.PULL.hold, "busy", false, "Recargando…");
      location.reload();
    }

    const cancel = () => { const s = t; t = null; if (s && s.active) paint(0, "pull", false); };

    document.addEventListener("touchstart", (e) => {
      t = null;
      const off = !SYNC || !L.pullEnabled(pullView) || syncing || document.querySelector(".backdrop, .pin");
      if (off || e.touches.length !== 1 || window.scrollY > 0 || e.target.closest("input, textarea")) return;
      t = { x: e.touches[0].clientX, y: e.touches[0].clientY, active: false, pull: 0 };
    }, { passive: true });

    document.addEventListener("touchmove", (e) => {
      if (!t) return;
      if (e.touches.length !== 1 || window.scrollY > 0) { cancel(); return; }
      const dx = e.touches[0].clientX - t.x, dy = e.touches[0].clientY - t.y;
      if (!t.active) {
        if (Math.abs(dx) < L.PULL.slop && Math.abs(dy) < L.PULL.slop) return;
        if (dy <= 0 || Math.abs(dx) > Math.abs(dy)) { t = null; return; } // hacia arriba o de lado: no es tirar
        t.active = true;
      }
      if (e.cancelable) e.preventDefault(); // sin rebote de la página ni recarga del navegador
      t.pull = L.pullDistance(dy);
      const zone = L.pullZone(t.pull);
      paint(t.pull, zone === "none" ? "pull" : zone, true);
    }, { passive: false });

    // Lo que se hace lo decide dónde está el dedo al soltar: se puede pasar de largo y volver atrás para elegir el mediano.
    const release = (fire) => {
      const s = t;
      t = null;
      if (!s || !s.active) return;
      const zone = fire ? L.pullZone(s.pull) : "none";
      if (zone === "sync") run();
      else if (zone === "reload") reloadPage();
      else paint(0, "pull", false);
    };
    document.addEventListener("touchend", () => release(true), { passive: true });
    document.addEventListener("touchcancel", () => release(false), { passive: true });
  }

  /* ---------- arranque ---------- */

  function errorView(err) {
    return [el("div", { className: "error" }, [
      el("p", { textContent: "No se han podido cargar los datos." }),
      el("p", { className: "muted", textContent: (err && err.message) || String(err) }),
      el("button", { className: "link", type: "button", textContent: "Reintentar", onclick: () => { resetData(); render(); } }),
    ]), CUR ? nav(L.parseHash(location.hash)[0] || "diario") : null].filter(Boolean);
  }

  // Las cifras de los saldos ruedan una sola vez por carga de la página, la primera vez que se pintan: al filtrar, elegir una
  // cuenta, volver del detalle o recargar los datos tras sincronizar no vuelven a girar. Con «reducir movimiento» no ruedan.
  let rolled = false;
  function startRoll() {
    if (rolled) return;
    const block = app.querySelector(".saldos");
    if (!block) return; // otra pestaña, la pantalla del PIN o un error: aún no se han pintado los saldos
    rolled = true;
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    block.classList.add("roll");
  }

  let seq = 0;
  async function render({ keep = false, scroll = null } = {}) {
    const my = ++seq;
    const y = window.scrollY;
    document.querySelectorAll(".backdrop").forEach((n) => n.remove());
    const p = L.parseHash(location.hash);
    const top = p[0] || "diario";
    const kind = L.viewOf(p);
    if (kind !== pullView) setPullView(""); // al salir de la lista del Diario el gesto se apaga ya, sin esperar a la carga
    const slow = setTimeout(() => { if (my === seq) app.classList.add("loading"); }, 150);
    let view, shown = "";
    try {
      if (kind === "mes") view = await monthView(MONTHS.includes(p[1]) ? p[1] : CUR, tipoOf(p[2]));
      else if (kind === "evolucion") view = await evolutionView(p);
      else if (kind === "tendencias") view = await trendsView();
      else if (kind === "rango") view = await rangeView(p);
      else if (kind === "movimiento") view = await movementView(p[2]);
      else view = await diaryView(top === "diario" ? p[1] : "");
      shown = kind;
    } catch (err) {
      view = err instanceof PinRequired ? pinView() : errorView(err); // pantalla de error o de PIN: no se tira
    } finally {
      clearTimeout(slow);
    }
    if (my !== seq) return;
    app.classList.remove("loading");
    app.replaceChildren(...view);
    startRoll();
    setPullView(shown);
    window.scrollTo(0, scroll !== null ? scroll : keep ? y : 0);
  }

  let started = false;
  async function boot({ refreshing = false } = {}) {
    let meta;
    try {
      meta = await api("meta");
    } catch (err) {
      if (err instanceof PinRequired) { app.replaceChildren(...pinView()); return; }
      app.replaceChildren(...errorView(err));
      app.querySelector(".error .link").onclick = () => { resetData(); boot(); };
      return;
    }
    MONTHS = meta.months;
    CUR = meta.current_month;
    TODAY = meta.today;
    CLOSED = MONTHS.filter((m) => m !== CUR);
    CUT = { day: meta.cut_day, days: meta.days_in_month };
    COVERAGE = meta.coverage;
    PIN = Boolean(meta.pin);
    SYNC = Boolean(meta.sync);
    if (!started) {
      started = true;
      setupPull();
      if ("scrollRestoration" in history) history.scrollRestoration = "manual";
      if (!history.state) history.replaceState({ depth: 0 }, "");
      window.addEventListener("popstate", (e) => render({ scroll: e.state && typeof e.state.scroll === "number" ? e.state.scroll : 0 }));
      swipe(app, (d) => {
        const p = L.parseHash(location.hash);
        if (p[0] !== "mes" || document.querySelector(".backdrop")) return;
        const ym = MONTHS.includes(p[1]) ? p[1] : CUR;
        const m = stepMonth(ym, d);
        if (m) go(H("mes", m, tipoOf(p[2])), { replace: true });
      });
      // De vuelta al panel tras un rato en segundo plano (p. ej. al día siguiente), datos frescos.
      let hiddenAt = 0;
      document.addEventListener("visibilitychange", () => {
        if (document.hidden) { hiddenAt = Date.now(); return; }
        if (hiddenAt && Date.now() - hiddenAt > 10 * 60 * 1000) { hiddenAt = 0; resetData(); boot({ refreshing: true }); }
      });
    }
    render({ keep: refreshing });
  }

  boot();
})();
