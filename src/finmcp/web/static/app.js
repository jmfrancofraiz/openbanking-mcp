"use strict";
/* Panel finmcp · interfaz (variante B «Relato del mes»).
   Mes: ingresos y gastos del mes seleccionado, sin comparaciones (solo minigráficas de 6 meses).
   Evolución: «Mes a mes», «Acumulado» y «Periodo contra periodo». Tendencias: una tarjeta por categoría.
   Los importes llegan calculados de la API de solo lectura (/api/*); aquí solo se presentan. */
(() => {
  const L = window.FinLib;
  const { eur, eur0 } = L;
  const app = document.getElementById("app");
  const SVGNS = "http://www.w3.org/2000/svg";
  const INC = "#147a70", EXP = "#c0663b", VARC = "#e8b89c", INK = "#22201c";
  // En el dispositivo solo se guardan preferencias, nunca cifras.
  const PREF = { split: "finmcp.split", trend: "finmcp.trend" };
  const ACC_PERIODS = [["3", "3 meses"], ["6", "6 meses"], ["12", "1 año"], ["ano", "Año actual"], ["todo", "Todo"]];
  const PERIOD_TYPES = [["mes", "Mes"], ["trimestre", "Trimestre"], ["semestre", "Semestre"], ["ano", "Año"]];

  let MONTHS = [];   // meses con datos, ascendentes (AAAA-MM)
  let CLOSED = [];   // los mismos sin el mes en curso
  let CUR = "";      // mes en curso
  let CUT = { day: 1, days: 30 };
  let COVERAGE = []; // [{bank, first_month}]
  let PIN = false;   // ¿el panel está protegido con PIN?

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
  const back = () => (history.state && history.state.depth > 0 ? history.back() : go(H("mes", CUR, "gastos"), { replace: true }));

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

  const topbar = (title) => el("header", { className: "top" }, [spacer(), el("div", { className: "ttl" }, el("div", { className: "mon", textContent: title })), spacer()]);

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
    root.append(svg("circle", { cx: x(n - 1), cy: y(values[n - 1]), r: 2.6, fill: partial ? "#fffdf9" : color,
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
  function txPanel(rows, tipo, showCat) {
    let sort = "asc";
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
      chips.replaceChildren(...[["asc", "Fecha ↑"], ["desc", "Fecha ↓"], ["amount", "Importe"]].map(([v, l]) =>
        el("button", { type: "button", className: `chip${v === sort ? " on" : ""}`, textContent: l, onclick: () => { sort = v; paint(); } })));
    };
    input.addEventListener("input", paint);
    paint();
    return [input, chips, list, total];
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
    root.append(svg("line", { x1: 0, x2: w, y1: y(0), y2: y(0), stroke: "#d8cfc0" }));
    const x0 = (w - rows.length * colW) / 2;
    const bar = (x, v, fill) => svg("rect", { x, y: Math.min(y(v), y(0)), width: bw, height: Math.max(1, Math.abs(y(v) - y(0))), rx: 2, fill });
    rows.forEach((r, i) => {
      const cx = x0 + i * colW;
      const g = svg("g", { opacity: selected >= 0 && i !== selected ? 0.35 : 1 });
      g.append(bar(cx + 3, r.income, INC));
      if (split) g.append(bar(cx + 4 + bw, r.expense, VARC), bar(cx + 4 + bw, Math.max(0, Math.min(r.bills, r.expense)), EXP));
      else g.append(bar(cx + 4 + bw, r.expense, EXP));
      g.append(svg("text", { x: cx + colW / 2, y: h - 12, "text-anchor": "middle", "font-size": 9.5, fill: INK, opacity: 0.75 }, [L.monthInitial(r.month)]));
      if (i === 0 || r.month.endsWith("-01")) {
        g.append(svg("text", { x: cx + colW / 2, y: h - 2, "text-anchor": "middle", "font-size": 8, fill: INK, opacity: 0.5 }, [r.month.slice(0, 4)]));
      }
      root.append(g);
    });
    const pts = rows.map((r, i) => ({ x: x0 + i * colW + colW / 2, y: y(r.net) }));
    if (pts.length > 1) root.append(svg("polyline", { points: pts.map((pt) => `${pt.x.toFixed(1)},${pt.y.toFixed(1)}`).join(" "), fill: "none", stroke: INK, "stroke-width": 1.6, "stroke-linejoin": "round" }));
    pts.forEach((pt) => root.append(svg("circle", { cx: pt.x, cy: pt.y, r: 2.8, fill: INK })));
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
          background: tone === "worse" ? "#b3261e" : tone === "better" ? "#2f7d32" : "#b9ad9a" } })),
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
    root.append(svg("defs", {}, [svg("pattern", { id, width: 5, height: 5, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" },
      [svg("line", { x1: 0, y1: 0, x2: 0, y2: 5, stroke: color, "stroke-width": 2 })])]));
    vals.forEach((v, i) => {
      const cx = 4 + i * colW + colW / 2;
      const partial = months[i] === CUR;
      root.append(svg("rect", { x: cx - bw / 2, y: Math.min(y(v), y(0)), width: bw, height: Math.max(1.5, Math.abs(y(v) - y(0))), rx: 2,
        fill: partial ? `url(#${id})` : color, stroke: partial ? color : "none", opacity: selected !== undefined && selected !== i ? 0.35 : 1 }));
      root.append(svg("text", { x: cx, y: h - 3, "text-anchor": "middle", "font-size": 9, fill: INK, opacity: 0.65 }, [L.monthInitial(months[i]).slice(0, 1)]));
    });
    root.append(svg("line", { x1: 4, x2: w - 4, y1: y(avg), y2: y(avg), stroke: INK, "stroke-dasharray": "4 3", "stroke-width": 1, opacity: 0.6 }));
    vals.forEach((v, i) => {
      const hit = svg("rect", { x: 4 + i * colW, y: 0, width: colW, height: h, fill: "transparent", class: "hit" });
      hit.addEventListener("click", () => onSelect(i));
      root.append(hit);
    });
    return root;
  }

  function openPicker(options, totals, nMonths, onPick) {
    const close = () => sheet.remove();
    const sheet = el("div", { className: "backdrop", onclick: (e) => { if (e.target === e.currentTarget) close(); } }, el("div", { className: "sheet", attrs: { role: "dialog", "aria-label": "Añadir categoría" } }, [
      el("div", { className: "grab" }),
      el("h2", { textContent: "Añadir categoría" }),
      options.length ? el("div", { className: "cards" }, options.map((g) => el("button", { className: "gcard", type: "button", onclick: () => { close(); onPick(g); } }, [
        el("span", { className: "dot", style: { background: L.colorFor(g) } }), el("span", { className: "n", textContent: g }),
        el("span", { className: "s" }, [`${nMonths} meses: `, amt(totals[g])]),
        el("span", { className: "v" }, [amt(totals[g] / Math.max(1, nMonths)), el("span", { className: "s", textContent: "al mes" })]),
      ]))) : el("p", { className: "muted", textContent: "No quedan más categorías con gasto." }),
    ]));
    document.body.append(sheet);
  }

  async function trendsView() {
    const last12 = CLOSED.slice(-12);
    const months = [...last12, CUR];
    const ser = await seriesOf(months[0], CUR);
    const idx = new Map(ser.months.map((m, i) => [m, i]));
    const gv = (g, m) => (ser.expense[g] ? ser.expense[g][idx.get(m)] : 0) || 0;
    const tot12 = Object.fromEntries(Object.keys(ser.expense).map((g) => [g, sum(last12.map((m) => gv(g, m)))]));
    const ranked = Object.keys(tot12).filter((g) => tot12[g] > 0).sort((a, b) => tot12[b] - tot12[a]);
    if (!Array.isArray(trendSel)) trendSel = ranked.slice(0, 5);
    trendSel = trendSel.filter((g) => g in ser.expense);
    const save = () => localStorage.setItem(PREF.trend, JSON.stringify(trendSel));
    const card = (g) => {
      const vals = months.map((m) => gv(g, m));
      const closedVals = vals.slice(0, -1);
      const avg = closedVals.length ? sum(closedVals) / closedVals.length : 0;
      const tr = L.trendOf(closedVals);
      const maxI = closedVals.length ? closedVals.indexOf(Math.max(...closedVals)) : -1;
      const pick = trendPick[g];
      const selLine = el("div", { className: "tsel" });
      if (pick !== undefined) {
        const m = months[pick];
        selLine.append(el("span", {}, [L.monthLabel(m) + (m === CUR ? ` (a día ${CUT.day})` : "") + ": ", amt(vals[pick])]),
          Math.abs(vals[pick]) >= 0.005 ? el("button", { type: "button", textContent: "Ver movimientos ›",
            onclick: () => go(H("rango", "tendencias", L.monthStart(m), L.monthEnd(m), "gastos", "g", g, "mov")) }) : "");
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
            onclick: () => openPicker(ranked.filter((g) => !trendSel.includes(g)), tot12, last12.length,
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

  const waitText = (s) => (s >= 90 ? `${Math.ceil(s / 60)} min` : `${s} s`);

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
        if (r.ok) { cache.clear(); boot(); return; }
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
    cache.clear();
    app.replaceChildren(...pinView());
    window.scrollTo(0, 0);
  }

  /* ---------- arranque ---------- */

  function errorView(err) {
    return [el("div", { className: "error" }, [
      el("p", { textContent: "No se han podido cargar los datos." }),
      el("p", { className: "muted", textContent: (err && err.message) || String(err) }),
      el("button", { className: "link", type: "button", textContent: "Reintentar", onclick: () => { cache.clear(); render(); } }),
    ]), CUR ? nav(L.parseHash(location.hash)[0] || "mes") : null].filter(Boolean);
  }

  let seq = 0;
  async function render({ keep = false, scroll = null } = {}) {
    const my = ++seq;
    const y = window.scrollY;
    document.querySelectorAll(".backdrop").forEach((n) => n.remove());
    const p = L.parseHash(location.hash);
    const top = p[0] || "mes";
    const slow = setTimeout(() => { if (my === seq) app.classList.add("loading"); }, 150);
    let view;
    try {
      if (top === "evolucion") view = await evolutionView(p);
      else if (top === "tendencias") view = await trendsView();
      else if (top === "rango" && p.length >= 7) view = await rangeView(p);
      else view = await monthView(MONTHS.includes(p[1]) ? p[1] : CUR, tipoOf(p[2]));
    } catch (err) {
      view = err instanceof PinRequired ? pinView() : errorView(err);
    } finally {
      clearTimeout(slow);
    }
    if (my !== seq) return;
    app.classList.remove("loading");
    app.replaceChildren(...view);
    window.scrollTo(0, scroll !== null ? scroll : keep ? y : 0);
  }

  let started = false;
  async function boot() {
    let meta;
    try {
      meta = await api("meta");
    } catch (err) {
      if (err instanceof PinRequired) { app.replaceChildren(...pinView()); return; }
      app.replaceChildren(...errorView(err));
      app.querySelector(".error .link").onclick = () => { cache.clear(); boot(); };
      return;
    }
    MONTHS = meta.months;
    CUR = meta.current_month;
    CLOSED = MONTHS.filter((m) => m !== CUR);
    CUT = { day: meta.cut_day, days: meta.days_in_month };
    COVERAGE = meta.coverage;
    PIN = Boolean(meta.pin);
    if (!started) {
      started = true;
      if ("scrollRestoration" in history) history.scrollRestoration = "manual";
      if (!history.state) history.replaceState({ depth: 0 }, "");
      window.addEventListener("popstate", (e) => render({ scroll: e.state && typeof e.state.scroll === "number" ? e.state.scroll : 0 }));
      swipe(app, (d) => {
        const p = L.parseHash(location.hash);
        if ((p[0] || "mes") !== "mes" || document.querySelector(".backdrop")) return;
        const ym = MONTHS.includes(p[1]) ? p[1] : CUR;
        const m = stepMonth(ym, d);
        if (m) go(H("mes", m, tipoOf(p[2])), { replace: true });
      });
    }
    render();
  }

  boot();
})();
