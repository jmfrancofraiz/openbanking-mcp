/* Panel finmcp · funciones puras (sin DOM): formato, fechas, periodos y comparaciones.
   En el navegador quedan en window.FinLib; los tests las cargan con require(). */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.FinLib = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
    "septiembre", "octubre", "noviembre", "diciembre"];
  const MS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
  const WD = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"];
  const PALETTE = ["#2f6fdf", "#e8590c", "#2b8a3e", "#c92a2a", "#7048e8", "#a05a2c", "#d6336c",
    "#0c8599", "#a9a20f", "#364fc7", "#5c940d", "#e67700", "#862e9c", "#495057"];
  const REST = "#adb5bd";

  const eur = new Intl.NumberFormat("es-ES", { style: "currency", currency: "EUR" });
  const eur0 = new Intl.NumberFormat("es-ES", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
  const pct = (v) => `${v.toLocaleString("es-ES", { maximumFractionDigits: 1 })} %`;

  /* ---------- fechas (cadenas AAAA-MM y AAAA-MM-DD) ---------- */

  const pad2 = (n) => String(n).padStart(2, "0");
  const ymParts = (ym) => ym.split("-").slice(0, 2).map(Number);
  const ymOf = (y, m) => `${y}-${pad2(m)}`;
  const daysIn = (ym) => { const [y, m] = ymParts(ym); return new Date(Date.UTC(y, m, 0)).getUTCDate(); };
  const monthStart = (ym) => `${ym}-01`;
  const monthEnd = (ym) => `${ym}-${pad2(daysIn(ym))}`;
  // Día D de un mes, o su último día si es más corto («a la misma fecha»).
  const upTo = (ym, day) => `${ym}-${pad2(Math.min(day, daysIn(ym)))}`;
  const monthLabel = (ym) => { const [y, m] = ymParts(ym); return `${MONTHS[m - 1]} ${y}`; };
  const monthName = (ym) => MONTHS[ymParts(ym)[1] - 1];
  const monthShort = (ym) => { const [y, m] = ymParts(ym); return `${MS[m - 1]} ${String(y).slice(2)}`; };
  const monthInitial = (ym) => MS[ymParts(ym)[1] - 1];
  const dayLabel = (iso) => {
    const [y, m, d] = iso.split("-").map(Number);
    return `${WD[new Date(Date.UTC(y, m - 1, d)).getUTCDay()]} ${d} ${MS[m - 1]}`;
  };
  const dayShort = (iso) => `${Number(iso.slice(8, 10))} ${monthInitial(iso.slice(0, 7))}`;
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

  // «septiembre 2026», «jul – sep 2026», «oct 25 – sep 26», «1–20 oct 2026» o «1 jul – 20 oct 2026».
  function rangeLabel(from, to) {
    const fm = from.slice(0, 7), tm = to.slice(0, 7);
    const whole = from.endsWith("-01") && to === monthEnd(tm);
    if (whole && fm === tm) return monthLabel(fm);
    if (whole) {
      return fm.slice(0, 4) === tm.slice(0, 4)
        ? `${monthInitial(fm)} – ${monthInitial(tm)} ${tm.slice(0, 4)}` : `${monthShort(fm)} – ${monthShort(tm)}`;
    }
    if (fm === tm) return `${Number(from.slice(8, 10))}–${Number(to.slice(8, 10))} ${monthInitial(tm)} ${tm.slice(0, 4)}`;
    return `${dayShort(from)} – ${dayShort(to)} ${tm.slice(0, 4)}`;
  }

  /* ---------- presentación ---------- */

  function colorFor(name) {
    let h = 0;
    for (const ch of name) h = (h * 31 + ch.codePointAt(0)) >>> 0;
    return PALETTE[h % PALETTE.length];
  }

  const share = (v, total) => (total > 0 ? Math.max(0, Math.round((v / total) * 100)) : 0);
  const shown = (name) => name.replace(/^Ingresos - /, "");
  // Título legible: sin el prefijo bancario «Pago en», «Compra en»… (el concepto completo sigue en el detalle).
  const cleanTitle = (m) => (m || "—").replace(/^(pago|compra|cargo|recibo)\s+(en|de)\s+/i, "").replace(/\s+ES$/, "");

  // Sectores del donut: las n−1 mayores con importe positivo y «Resto (N)» si hay más de n.
  function topN(groups, n) {
    const pos = groups.filter((g) => g.total > 0);
    const mk = (g) => ({ label: g.group, total: g.total, rest: false, color: colorFor(g.group) });
    if (pos.length <= n) return pos.map(mk);
    const tail = pos.slice(n - 1);
    return [...pos.slice(0, n - 1).map(mk),
      { label: `Resto (${tail.length})`, total: tail.reduce((a, g) => a + g.total, 0), rest: true, color: REST }];
  }

  // Variación frente a una referencia. kind «cost»: subir es peor; «gain»: subir es mejor.
  // Sin cambio si |variación| < máx(5 €, 3 % de la referencia).
  function delta(cur, ref, kind) {
    if (ref === null || ref === undefined) return null;
    const diff = cur - ref;
    const p = Math.abs(ref) > 0 ? (diff / Math.abs(ref)) * 100 : null;
    const flat = Math.abs(diff) < Math.max(5, Math.abs(ref) * 0.03);
    const tone = flat ? "flat" : (diff > 0) === (kind === "cost") ? "worse" : "better";
    return { diff, pct: p, flat, tone };
  }

  function parseHash(hash) {
    return String(hash || "").replace(/^#\/?/, "").split("/").filter(Boolean).map((x) => {
      try { return decodeURIComponent(x); } catch { return x; }
    });
  }

  /* ---------- periodos ---------- */

  // Periodos naturales de un tipo con datos: [{id, label, all, avail, partial}] en orden ascendente.
  function periodsOf(type, months, current) {
    const map = new Map();
    for (const m of months) {
      const [y, mm] = ymParts(m);
      let id, label, first, len;
      if (type === "mes") { id = m; label = monthLabel(m); first = mm; len = 1; }
      else if (type === "trimestre") { const q = Math.ceil(mm / 3); id = `${y}-T${q}`; label = `T${q} ${y}`; first = (q - 1) * 3 + 1; len = 3; }
      else if (type === "semestre") { const s = mm <= 6 ? 1 : 2; id = `${y}-S${s}`; label = `S${s} ${y}`; first = s === 1 ? 1 : 7; len = 6; }
      else { id = String(y); label = String(y); first = 1; len = 12; }
      if (!map.has(id)) {
        const all = Array.from({ length: len }, (_, i) => ymOf(y, first + i));
        map.set(id, { id, label, all, avail: all.filter((x) => months.includes(x)), partial: all.includes(current) });
      }
    }
    return [...map.values()];
  }

  // Si uno de los dos periodos está en curso, ambos se recortan a la misma posición y día.
  function comparisonCut(A, B, current, day) {
    const per = A.partial ? A : B.partial ? B : null;
    return per ? { k: per.all.indexOf(current), day, per } : null;
  }

  // Rango de fechas de un periodo; con `cut`, hasta el día D de su mes k.
  function periodRange(per, cut) {
    if (cut) return { from: monthStart(per.all[0]), to: upTo(per.all[cut.k], cut.day) };
    return { from: monthStart(per.all[0]), to: monthEnd(per.avail[per.avail.length - 1]) };
  }

  // Meses cerrados de un periodo del Acumulado: "3", "6", "12", "ano" (año en curso) o "todo".
  function accumulatedMonths(period, closed, year) {
    if (period === "todo") return closed.slice();
    if (period === "ano") return closed.filter((m) => m.startsWith(`${year}-`));
    return closed.slice(-Number(period));
  }

  /* ---------- comparaciones ---------- */

  const PACE_REFS = [
    { k: "prev", n: 1, de: "del mes anterior", con: "con el mes anterior" },
    { k: "avg3", n: 3, de: "de la media de 3 meses", con: "con la media de 3 meses" },
    { k: "avg6", n: 6, de: "de la media de 6 meses", con: "con la media de 6 meses" },
  ];

  // Gasto del mes en curso a día D frente a mes anterior y medias de 3 y 6 meses, también a día D.
  // valueOf(mes) devuelve el gasto de ese mes del día 1 al D.
  function paceRefs(valueOf, current, closed) {
    const now = valueOf(current);
    const refs = PACE_REFS.map((r) => {
      const ms = closed.slice(-r.n);
      if (!ms.length) return null;
      const v = ms.reduce((acc, m) => acc + valueOf(m), 0) / ms.length;
      return { ...r, ms, v, d: delta(now, v, "cost") };
    }).filter(Boolean);
    return { now, refs };
  }

  const joinY = (xs) => (xs.length <= 1 ? xs.join("") : `${xs.slice(0, -1).join(", ")} y ${xs[xs.length - 1]}`);
  const NUM = { 1: "la referencia", 2: "las dos referencias", 3: "las tres referencias" };

  function verdict(refs) {
    if (!refs.length) return "Sin meses anteriores con los que comparar.";
    const below = refs.filter((r) => r.d.tone === "better");
    const above = refs.filter((r) => r.d.tone === "worse");
    const flat = refs.filter((r) => r.d.tone === "flat");
    if (below.length === refs.length) return `Gasta menos que en ${NUM[refs.length]}.`;
    if (above.length === refs.length) return `Gasta más que en ${NUM[refs.length]}.`;
    const parts = [];
    if (below.length) parts.push(`por debajo ${joinY(below.map((r) => r.de))}`);
    if (above.length) parts.push(`por encima ${joinY(above.map((r) => r.de))}`);
    if (flat.length) parts.push(`en línea ${joinY(flat.map((r) => r.con))}`);
    return `${cap(parts.join(" · "))}.`;
  }

  // Tendencia de una categoría: media de los 3 últimos meses cerrados frente a los anteriores (hasta 9).
  function trendOf(closedVals) {
    const avg = (xs) => (xs.length ? xs.reduce((a, v) => a + v, 0) / xs.length : 0);
    const l3 = avg(closedVals.slice(-3));
    const p9 = avg(closedVals.slice(0, -3));
    const ratio = closedVals.length > 3 && p9 > 0 ? (l3 - p9) / p9 : null;
    if (ratio === null) return { ratio, text: "", tone: "flat" };
    if (Math.abs(ratio) < 0.1) return { ratio, text: "estable", tone: "flat" };
    return ratio > 0
      ? { ratio, text: `al alza (+${Math.round(ratio * 100)} %)`, tone: "worse" }
      : { ratio, text: `a la baja (${Math.round(ratio * 100)} %)`, tone: "better" };
  }

  // «Qué ha cambiado»: diferencia por categoría entre dos periodos, de mayor a menor magnitud.
  function diffGroups(groupsA, groupsB) {
    const gA = new Map(groupsA.map((g) => [g.group, g]));
    const gB = new Map(groupsB.map((g) => [g.group, g]));
    return [...new Set([...gA.keys(), ...gB.keys()])].map((k) => {
      const a = gA.get(k) || null, b = gB.get(k) || null;
      const va = a ? a.total : 0, vb = b ? b.total : 0;
      return { k, a, b, va, vb, d: va - vb };
    }).filter((x) => Math.abs(x.d) >= 0.5).sort((x, y) => Math.abs(y.d) - Math.abs(x.d));
  }

  // Subcategorías de una categoría en los dos periodos, con su diferencia.
  function diffSubcategories(a, b) {
    const cats = new Map();
    (a ? a.categories : []).forEach((c) => cats.set(c.category, { label: c.label, va: c.total, vb: 0 }));
    (b ? b.categories : []).forEach((c) => {
      const e = cats.get(c.category) || { label: c.label, va: 0, vb: 0 };
      e.vb = c.total;
      cats.set(c.category, e);
    });
    return [...cats.values()].map((e) => ({ ...e, d: e.va - e.vb })).sort((x, y) => Math.abs(y.d) - Math.abs(x.d));
  }

  return {
    MONTHS, MS, REST, eur, eur0, pct,
    ymOf, daysIn, monthStart, monthEnd, upTo, monthLabel, monthName, monthShort, monthInitial,
    dayLabel, dayShort, cap, rangeLabel,
    colorFor, share, shown, cleanTitle, topN, delta, parseHash,
    periodsOf, comparisonCut, periodRange, accumulatedMonths,
    PACE_REFS, paceRefs, verdict, trendOf, diffGroups, diffSubcategories,
  };
});
