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
  const WDL = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"];
  const addDays = (iso, n) => {
    const [y, m, d] = iso.split("-").map(Number);
    const t = new Date(Date.UTC(y, m - 1, d + n));
    return `${t.getUTCFullYear()}-${pad2(t.getUTCMonth() + 1)}-${pad2(t.getUTCDate())}`;
  };
  const otherYear = (iso, today) => (iso.slice(0, 4) !== today.slice(0, 4) ? ` ${iso.slice(0, 4)}` : "");

  // Cabecera de día del Diario: «Hoy · mié 21 oct», «Ayer · mar 20 oct», «lun 19 oct» o, de otro año, «mié 31 dic 2025».
  function dayHeading(iso, today) {
    const base = dayLabel(iso) + otherYear(iso, today);
    if (iso === today) return `Hoy · ${base}`;
    if (iso === addDays(today, -1)) return `Ayer · ${base}`;
    return base;
  }

  // «miércoles, 21 de octubre de 2026».
  function dayLong(iso) {
    const [y, m, d] = iso.split("-").map(Number);
    return `${WDL[new Date(Date.UTC(y, m - 1, d)).getUTCDay()]}, ${d} de ${MONTHS[m - 1]} de ${y}`;
  }

  // Fecha y hora en España de una marca UTC de la API («…Z»).
  const MADRID = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Madrid", year: "numeric", month: "2-digit",
    day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  function localStamp(isoUtc) {
    const p = Object.fromEntries(MADRID.formatToParts(new Date(isoUtc)).map((x) => [x.type, x.value]));
    return { date: `${p.year}-${p.month}-${p.day}`, time: `${p.hour}:${p.minute}` };
  }

  // «hoy 07:31», «ayer 07:31», «20 oct 07:31» o, de otro año, «20 oct 2025 07:31».
  function whenLabel(isoUtc, today) {
    const { date, time } = localStamp(isoUtc);
    if (date === today) return `hoy ${time}`;
    if (date === addDays(today, -1)) return `ayer ${time}`;
    return `${dayShort(date)}${otherYear(date, today)} ${time}`;
  }

  /* ---------- tirar hacia abajo para sincronizar ---------- */

  // Distancias en px. `reload` y `sync`: lo que hay que tirar (ya con resistencia) para que al soltar recargue la página
  // (tirón mediano) o sincronice (tirón largo). `damping`: el dedo recorre 1/damping px por cada px que baja el indicador.
  // `max`: tope del indicador. `hold`: dónde queda mientras trabaja.
  const PULL = { reload: 56, sync: 104, max: 120, damping: 0.55, slop: 8, hold: 64 };
  const pullDistance = (dy) => (dy > 0 ? Math.min(PULL.max, dy * PULL.damping) : 0);
  // Qué haría soltar ahora: «none» (tirón corto), «reload» (mediano: recarga la página) o «sync» (largo: sincroniza).
  const pullZone = (pull) => (pull >= PULL.sync ? "sync" : pull >= PULL.reload ? "reload" : "none");

  // «45 s» o, desde 90 s, «3 min».
  const waitLabel = (s) => { const n = Math.max(1, Math.ceil(s)); return n >= 90 ? `${Math.ceil(n / 60)} min` : `${n} s`; };

  // Mensaje y tono de lo ocurrido tras tirar. `snap`: respuesta de /api/sync; `ran`: ¿se sincronizó con los bancos
  // (la lanzó este gesto o se unió a una en marcha)? Si no, el servidor la frenó por reciente y solo se recargaron los datos.
  function syncResult(snap, ran) {
    const n = snap.new_transactions || 0;
    const fresh = n ? `${n} ${n === 1 ? "movimiento nuevo" : "movimientos nuevos"}` : "sin movimientos nuevos";
    const failed = (snap.banks || []).filter((b) => b.status !== "ok").map((b) => b.bank);
    const why = snap.error ? snap.error.replace(/\.$/, "") : "";
    if (!ran) {
      return { tone: "info", text: snap.retry_after > 0
        ? `Datos recargados. La última sincronización es reciente: podrá volver a sincronizar en ${waitLabel(snap.retry_after)}.`
        : "Datos recargados." };
    }
    switch (snap.outcome) {
      case "ok": return { tone: "ok", text: `Sincronizado · ${fresh}` };
      case "partial": return { tone: "warn",
        text: `Sincronizado con avisos · ${fresh}. ${failed.join(", ")} no ${failed.length === 1 ? "ha podido" : "han podido"} sincronizarse${why ? `: ${why}` : ""}.` };
      case "busy": return { tone: "info", text: "Ya había otra sincronización en curso; se muestran los datos actuales." };
      case "timeout": return { tone: "warn", text: "La sincronización tardó demasiado y se canceló; se muestran los datos actuales." };
      default: return { tone: "error",
        text: `No se ha podido sincronizar${failed.length ? ` (${failed.join(", ")})` : ""}${why ? `: ${why}` : ""}. Se muestran los datos que ya había.` };
    }
  }

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

  // Modo oscuro: "" (automático, según el sistema) -> "dark" -> "light" -> "" al tocar el alternador.
  const THEME_ORDER = ["", "dark", "light"];
  const nextTheme = (current) => THEME_ORDER[(THEME_ORDER.indexOf(current) + 1) % THEME_ORDER.length];
  // Tema efectivo: el forzado por el usuario si lo hay; si no, el del sistema.
  const isDarkTheme = (override, systemPrefersDark) => (override ? override === "dark" : Boolean(systemPrefersDark));

  function colorFor(name) {
    let h = 0;
    for (const ch of name) h = (h * 31 + ch.codePointAt(0)) >>> 0;
    return PALETTE[h % PALETTE.length];
  }

  const share = (v, total) => (total > 0 ? Math.max(0, Math.round((v / total) * 100)) : 0);
  const shown = (name) => name.replace(/^Ingresos - /, "");
  // Título legible: sin el prefijo bancario «Pago en», «Compra en»… (el concepto completo sigue en el detalle).
  const cleanTitle = (m) => (m || "—").replace(/^(pago|compra|cargo|recibo)\s+(en|de)\s+/i, "").replace(/\s+ES$/, "");
  // Título de un movimiento: el comercio si es un nombre (tiene alguna letra); si no —la cola del
  // IBAN que ING pone en recibos y transferencias, o los «*****» de BBVA—, el concepto.
  const titleOf = (t) => cleanTitle(t.merchant && /\p{L}/u.test(t.merchant) ? t.merchant : (t.description || t.merchant));
  // Importe con signo: «+12,30 €» los abonos, «−12,30 €» los cargos y «0,00 €» sin signo.
  const signedEur = (v) => {
    const x = Math.abs(v) < 0.005 ? 0 : v;
    return (x > 0 ? "+" : x < 0 ? "−" : "") + eur.format(Math.abs(x));
  };

  /* ---------- filtros del Diario (en la ruta: #/diario/<cuenta=…&tipo=…>) ---------- */

  const DIARY_KEYS = ["cuenta", "tipo", "marca", "etiqueta", "desde", "hasta", "q"];
  const DIARY_API = { cuenta: "account", tipo: "type", marca: "mark", etiqueta: "tag", desde: "from", hasta: "to", q: "q" };
  function diaryFilters(seg) {
    const sp = new URLSearchParams(seg || "");
    const f = {};
    for (const k of DIARY_KEYS) {
      const v = (sp.get(k) || "").trim();
      if (v) f[k] = v;
    }
    return f;
  }
  const diaryQuery = (f) => new URLSearchParams(DIARY_KEYS.filter((k) => f[k]).map((k) => [k, f[k]])).toString();
  const ledgerParams = (f) => Object.fromEntries(DIARY_KEYS.filter((k) => f[k]).map((k) => [DIARY_API[k], f[k]]));

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

  // Vista que pinta una ruta (ya con parseHash): «mes», «evolucion», «tendencias», «rango» (movimientos de un periodo),
  // «movimiento» (detalle del Diario) o «diario» (la lista, que además es la de las rutas desconocidas o incompletas).
  function viewOf(p) {
    const top = p[0] || "diario";
    if (top === "mes" || top === "evolucion" || top === "tendencias") return top;
    if (top === "rango" && p.length >= 7) return "rango";
    if (top === "diario" && p[1] === "mov" && p[2]) return "movimiento";
    return "diario";
  }

  // Solo la lista del Diario puede lanzar una sincronización tirando hacia abajo.
  const pullEnabled = (view) => view === "diario";

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
    dayLabel, dayShort, cap, rangeLabel, addDays, dayHeading, dayLong, localStamp, whenLabel,
    colorFor, share, shown, cleanTitle, titleOf, signedEur, topN, delta, parseHash,
    DIARY_KEYS, diaryFilters, diaryQuery, ledgerParams,
    PULL, pullDistance, pullZone, waitLabel, syncResult, viewOf, pullEnabled,
    periodsOf, comparisonCut, periodRange, accumulatedMonths,
    PACE_REFS, paceRefs, verdict, trendOf, diffGroups, diffSubcategories,
    nextTheme, isDarkTheme,
  };
});
