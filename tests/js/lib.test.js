// Tests de las funciones puras del panel: node --test tests/js/
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const L = require(path.join(__dirname, "../../src/finmcp/web/static/lib.js"));
const MONTHS = ["2025-11", "2025-12", "2026-01", "2026-02", "2026-03", "2026-04"];

test("fechas: días del mes, inicio, fin y «a día D»", () => {
  assert.equal(L.daysIn("2026-02"), 28);
  assert.equal(L.daysIn("2024-02"), 29);
  assert.equal(L.monthStart("2026-04"), "2026-04-01");
  assert.equal(L.monthEnd("2026-04"), "2026-04-30");
  assert.equal(L.upTo("2026-02", 31), "2026-02-28");
  assert.equal(L.upTo("2026-03", 10), "2026-03-10");
});

test("etiquetas de mes, día y rango", () => {
  assert.equal(L.monthLabel("2026-09"), "septiembre 2026");
  assert.equal(L.monthShort("2026-09"), "sep 26");
  assert.equal(L.dayLabel("2026-10-20"), "mar 20 oct");
  assert.equal(L.rangeLabel("2026-09-01", "2026-09-30"), "septiembre 2026");
  assert.equal(L.rangeLabel("2026-07-01", "2026-09-30"), "jul – sep 2026");
  assert.equal(L.rangeLabel("2025-10-01", "2026-09-30"), "oct 25 – sep 26");
  assert.equal(L.rangeLabel("2026-10-01", "2026-10-20"), "1–20 oct 2026");
  assert.equal(L.rangeLabel("2026-07-01", "2026-10-20"), "1 jul – 20 oct 2026");
});

test("delta: umbral de «sin cambio» y tono según el tipo", () => {
  const d = L.delta(100, 120, "cost");
  assert.deepEqual([d.diff, d.flat, d.tone, d.pct.toFixed(2)], [-20, false, "better", "-16.67"]);
  assert.equal(L.delta(124, 120, "cost").flat, true); // 4 € < máx(5; 3,6)
  assert.equal(L.delta(1050, 1000, "cost").tone, "worse");
  assert.equal(L.delta(1050, 1000, "gain").tone, "better");
  assert.equal(L.delta(10, 0, "cost").pct, null);
  assert.equal(L.delta(10, null, "cost"), null);
});

test("refPct: % de consumo de la mediana; null sin referencia útil", () => {
  assert.equal(L.refPct(73, 100), 73);
  assert.equal(L.refPct(130, 100), 130); // puede superar el 100 %: se informa, no se recorta
  assert.equal(L.refPct(50, undefined), null); // sin histórico
  assert.equal(L.refPct(50, 0), null); // referencia 0: % no definible
  assert.equal(L.refPct(50, -10), null); // referencia negativa: tampoco
});

test("fadeHex: hex de la paleta a rgba con alpha", () => {
  assert.equal(L.fadeHex("#2f6fdf", 0.22), "rgba(47, 111, 223, 0.22)");
});

test("refFill: relleno 0–100 % con color degradado; null sin pct", () => {
  assert.equal(L.refFill("#2f6fdf", null), null);
  const half = L.refFill("#2f6fdf", 50);
  assert.equal(half, "linear-gradient(to right, rgba(47, 111, 223, 0.22) 0%, "
    + "rgba(47, 111, 223, 0.22) 50%, var(--card) 50%, var(--card) 100%)");
  const full = L.refFill("#2f6fdf", 100);
  assert.equal(full, "linear-gradient(to right, rgba(47, 111, 223, 0.22) 0%, "
    + "rgba(47, 111, 223, 0.22) 100%, var(--card) 100%, var(--card) 100%)");
  assert.match(L.refFill("#2f6fdf", -5), /var\(--card\) 0%, var\(--card\) 100%\)$/);
});

test("refFill: por encima del 100 %, fila sólida con raya de puntos en la marca de la mediana", () => {
  const over = L.refFill("#2f6fdf", 200); // gasto el doble de lo habitual: marca al 50 %
  assert.equal(over, "repeating-linear-gradient(to bottom, rgba(47, 111, 223, 0.85) 0, "
    + "rgba(47, 111, 223, 0.85) 2px, transparent 2px, transparent 5px) 50.00% 0 / 2px 100% no-repeat, "
    + "linear-gradient(to right, rgba(47, 111, 223, 0.22), rgba(47, 111, 223, 0.22))");
  // la raya es del mismo tono que el relleno, solo que más saturada (0.85 frente a 0.22).
  assert.match(over, /rgba\(47, 111, 223, 0\.85\)/);
  assert.doesNotMatch(over, /var\(--card\)/); // el Señor rechazó blanco: ha de ser el mismo color
  // la marca es 100/pct: a más exceso, más cerca del borde izquierdo (menos «sliver» típico).
  assert.match(L.refFill("#2f6fdf", 400), /\) 25\.00% 0 \/ 2px 100% no-repeat/);
  assert.match(L.refFill("#2f6fdf", 101), /\) 99\.01% 0 \/ 2px 100% no-repeat/);
  // nunca sin color de relleno, aunque el exceso sea extremo (434 %, caso real del Señor).
  assert.match(L.refFill("#2f6fdf", 434), /linear-gradient\(to right, rgba\(47, 111, 223, 0\.22\), rgba\(47, 111, 223, 0\.22\)\)$/);
});

test("topN agrupa en «Resto» y descarta importes no positivos", () => {
  const groups = [9, 8, 7, 6, 5, 4, 3, 2, -1].map((t, i) => ({ group: `G${i}`, total: t }));
  const slices = L.topN(groups, 7);
  assert.equal(slices.length, 7);
  assert.deepEqual(slices.slice(-1)[0], { label: "Resto (2)", total: 5, rest: true, color: L.REST });
  assert.equal(L.topN(groups.slice(0, 3), 7).length, 3);
});

test("withAllGroups: añade a 0 € las categorías sin movimientos este periodo (pestaña Mes)", () => {
  const agg = {
    total: 120, count: 2,
    groups: [
      { group: "Alimentación", total: 100, count: 1, categories: [{ category: "Alimentación - Supermercado", label: "Supermercado", total: 100, count: 1 }] },
      { group: "Restauración", total: 20, count: 1, categories: [{ category: "Restauración", label: "General", total: 20, count: 1 }] },
    ],
  };
  const out = L.withAllGroups(agg, ["Alimentación", "Restauración", "Vivienda", "Ocio"]);
  assert.equal(out.total, 120); // no toca el total ni el count del periodo
  assert.equal(out.count, 2);
  assert.equal(out.groups.length, 4);
  const added = out.groups.find((g) => g.group === "Vivienda");
  assert.deepEqual(added, { group: "Vivienda", total: 0, count: 0, categories: [] });
  // las existentes no se duplican ni se alteran
  assert.equal(out.groups.filter((g) => g.group === "Alimentación").length, 1);
  assert.deepEqual(out.groups.find((g) => g.group === "Alimentación"), agg.groups[0]);
});

test("withAllGroups: sin nombres, o con todos ya presentes, devuelve el agg tal cual", () => {
  const agg = { total: 20, count: 1, groups: [{ group: "Ocio", total: 20, count: 1, categories: [] }] };
  assert.equal(L.withAllGroups(agg, []), agg);
  assert.equal(L.withAllGroups(agg, null), agg);
  assert.equal(L.withAllGroups(agg, ["Ocio"]), agg);
});

test("presentación: porcentaje, nombre de ingreso y título limpio", () => {
  assert.equal(L.share(50, 1070), 5);
  assert.equal(L.share(-10, 100), 0);
  assert.equal(L.shown("Ingresos - Nómina"), "Nómina");
  assert.equal(L.cleanTitle("Pago en MERCADONA VIGO ES"), "MERCADONA VIGO");
  assert.equal(L.cleanTitle(null), "—");
  assert.equal(L.colorFor("Vivienda"), L.colorFor("Vivienda"));
});

test("modo oscuro: ciclo del alternador y tema efectivo", () => {
  assert.equal(L.nextTheme(""), "dark");
  assert.equal(L.nextTheme("dark"), "light");
  assert.equal(L.nextTheme("light"), "");
  // Sin forzar, manda el sistema; forzado, manda el alternador por encima del sistema.
  assert.equal(L.isDarkTheme("", true), true);
  assert.equal(L.isDarkTheme("", false), false);
  assert.equal(L.isDarkTheme("dark", false), true);
  assert.equal(L.isDarkTheme("light", true), false);
});

test("parseHash decodifica los segmentos", () => {
  assert.deepEqual(L.parseHash("#/rango/mes/2026-03-01/2026-03-31/gastos/g/Moda%20y%20complementos"),
    ["rango", "mes", "2026-03-01", "2026-03-31", "gastos", "g", "Moda y complementos"]);
  assert.deepEqual(L.parseHash(""), []);
});

test("periodsOf: trimestres naturales con meses disponibles y periodo en curso", () => {
  const q = L.periodsOf("trimestre", MONTHS, "2026-04");
  assert.deepEqual(q.map((x) => x.id), ["2025-T4", "2026-T1", "2026-T2"]);
  assert.deepEqual(q[0].all, ["2025-10", "2025-11", "2025-12"]);
  assert.deepEqual(q[0].avail, ["2025-11", "2025-12"]);
  assert.equal(q[2].partial, true);
  assert.deepEqual(L.periodsOf("ano", MONTHS, "2026-04").map((x) => x.label), ["2025", "2026"]);
  assert.deepEqual(L.periodsOf("semestre", MONTHS, "2026-04").map((x) => x.id), ["2025-S2", "2026-S1"]);
});

test("periodo contra periodo: recorte a la misma fecha (ejemplos de la especificación)", () => {
  const months = [];
  for (let m = 1; m <= 12; m += 1) months.push(L.ymOf(2025, m));
  for (let m = 1; m <= 10; m += 1) months.push(L.ymOf(2026, m));
  const range = (type, a, b) => {
    const pers = L.periodsOf(type, months, "2026-10");
    const A = pers.find((x) => x.id === a), B = pers.find((x) => x.id === b);
    const cut = L.comparisonCut(A, B, "2026-10", 20);
    return [L.periodRange(A, cut), L.periodRange(B, cut)];
  };
  assert.deepEqual(range("mes", "2026-10", "2026-09"),
    [{ from: "2026-10-01", to: "2026-10-20" }, { from: "2026-09-01", to: "2026-09-20" }]);
  assert.deepEqual(range("trimestre", "2026-T4", "2026-T3"),
    [{ from: "2026-10-01", to: "2026-10-20" }, { from: "2026-07-01", to: "2026-07-20" }]);
  assert.deepEqual(range("semestre", "2026-S2", "2026-S1"),
    [{ from: "2026-07-01", to: "2026-10-20" }, { from: "2026-01-01", to: "2026-04-20" }]);
  assert.deepEqual(range("ano", "2026", "2025"),
    [{ from: "2026-01-01", to: "2026-10-20" }, { from: "2025-01-01", to: "2025-10-20" }]);
  // Dos periodos cerrados: completos.
  assert.deepEqual(range("trimestre", "2026-T3", "2026-T2"),
    [{ from: "2026-07-01", to: "2026-09-30" }, { from: "2026-04-01", to: "2026-06-30" }]);
});

test("accumulatedMonths: 3/6/12 son de meses cerrados; año actual y todo añaden el mes en curso", () => {
  const closed = MONTHS.slice(0, -1); // 2025-11 … 2026-03
  const current = "2026-04";
  // 3/6/12: ventana fija de meses cerrados, sin el mes en curso aunque se informe.
  assert.deepEqual(L.accumulatedMonths("3", closed, "2026", current), ["2026-01", "2026-02", "2026-03"]);
  assert.deepEqual(L.accumulatedMonths("12", closed, "2026", current), closed);
  // año actual y todo: meses cerrados + el mes en curso al final (parcial, hasta hoy).
  assert.deepEqual(L.accumulatedMonths("ano", closed, "2026", current), ["2026-01", "2026-02", "2026-03", "2026-04"]);
  assert.deepEqual(L.accumulatedMonths("todo", closed, "2026", current), [...closed, "2026-04"]);
  // año actual con el mes en curso como único de ese año: se muestra igualmente (antes era un hueco vacío).
  assert.deepEqual(L.accumulatedMonths("ano", ["2025-11", "2025-12"], "2026", "2026-01"), ["2026-01"]);
  // sin `current` informado (compatibilidad): ni año actual ni todo lo añaden.
  assert.deepEqual(L.accumulatedMonths("ano", closed, "2026"), ["2026-01", "2026-02", "2026-03"]);
  assert.deepEqual(L.accumulatedMonths("todo", closed, "2026"), closed);
});

test("paceRefs y verdict: ejemplo de la especificación (abril a día 10)", () => {
  const atDay10 = { "2026-03": 120, "2026-04": 100 };
  const { now, refs } = L.paceRefs((m) => atDay10[m] || 0, "2026-04", ["2026-03"]);
  assert.equal(now, 100);
  assert.deepEqual(refs.map((r) => [r.k, r.ms.length, r.v]), [["prev", 1, 120], ["avg3", 1, 120], ["avg6", 1, 120]]);
  assert.equal(refs[0].d.tone, "better");
  assert.equal(Math.round(Math.abs(refs[0].d.pct)), 17);
  assert.equal(L.verdict(refs), "Gasta menos que en las tres referencias.");
});

test("verdict mixto y sin referencias", () => {
  const r = (k, tone) => ({ ...L.PACE_REFS.find((x) => x.k === k), d: { tone } });
  assert.equal(L.verdict([r("prev", "better"), r("avg3", "flat"), r("avg6", "worse")]),
    "Por debajo del mes anterior · por encima de la media de 6 meses · en línea con la media de 3 meses.");
  assert.equal(L.verdict([]), "Sin meses anteriores con los que comparar.");
});

test("trendOf: estable, al alza, a la baja y sin histórico suficiente", () => {
  const nine = Array(9).fill(100);
  assert.equal(L.trendOf([...nine, 105, 100, 95]).text, "estable");
  assert.deepEqual(L.trendOf([...nine, 150, 150, 150]), { ratio: 0.5, text: "al alza (+50 %)", tone: "worse" });
  assert.equal(L.trendOf([...nine, 50, 50, 50]).text, "a la baja (-50 %)");
  assert.equal(L.trendOf([100, 100, 100]).ratio, null);
});

test("trendOf: invertido (ingresos) cambia el tono pero no el texto", () => {
  const nine = Array(9).fill(100);
  const up = L.trendOf([...nine, 150, 150, 150], true);
  assert.deepEqual(up, { ratio: 0.5, text: "al alza (+50 %)", tone: "better" });
  const down = L.trendOf([...nine, 50, 50, 50], true);
  assert.equal(down.tone, "worse");
  assert.equal(L.trendOf([...nine, 105, 100, 95], true).tone, "flat");
});

test("diffGroups y diffSubcategories", () => {
  const A = [{ group: "Alimentación", total: 60, categories: [{ category: "Alimentación - Super", label: "Super", total: 60 }] },
    { group: "Restauración", total: 40, categories: [] }];
  const B = [{ group: "Alimentación", total: 120, categories: [{ category: "Alimentación - Super", label: "Super", total: 120 }] },
    { group: "Ocio", total: 0.2, categories: [] }];
  const d = L.diffGroups(A, B);
  assert.deepEqual(d.map((x) => [x.k, x.d]), [["Alimentación", -60], ["Restauración", 40]]);
  assert.deepEqual(L.diffSubcategories(A[0], B[0]), [{ label: "Super", va: 60, vb: 120, d: -60 }]);
});

/* ---------- Diario ---------- */

test("Diario: cabeceras de día con «Hoy», «Ayer» y el año si no es el actual", () => {
  assert.equal(L.dayHeading("2026-10-21", "2026-10-21"), "Hoy · mié 21 oct");
  assert.equal(L.dayHeading("2026-10-20", "2026-10-21"), "Ayer · mar 20 oct");
  assert.equal(L.dayHeading("2026-10-19", "2026-10-21"), "lun 19 oct");
  assert.equal(L.dayHeading("2025-12-31", "2026-01-01"), "Ayer · mié 31 dic 2025");
  assert.equal(L.dayHeading("2025-12-30", "2026-10-21"), "mar 30 dic 2025");
  assert.equal(L.addDays("2026-03-01", -1), "2026-02-28");
  assert.equal(L.dayLong("2026-10-21"), "miércoles, 21 de octubre de 2026");
});

test("Diario: hora local de España de las fotos de saldo (UTC en la API)", () => {
  assert.deepEqual(L.localStamp("2026-10-21T05:31:01.856017Z"), { date: "2026-10-21", time: "07:31" }); // CEST
  assert.deepEqual(L.localStamp("2026-12-01T23:30:00Z"), { date: "2026-12-02", time: "00:30" });      // CET, cambia de día
  assert.equal(L.whenLabel("2026-10-21T05:31:01Z", "2026-10-21"), "hoy 07:31");
  assert.equal(L.whenLabel("2026-10-20T05:31:13Z", "2026-10-21"), "ayer 07:31");
  assert.equal(L.whenLabel("2026-10-12T20:20:44Z", "2026-10-21"), "12 oct 22:20");
  assert.equal(L.whenLabel("2025-10-12T10:00:00Z", "2026-10-21"), "12 oct 2025 12:00");
});

test("Diario: importe con signo y título del movimiento", () => {
  const nb = (x) => x.replace(" €", "\u00a0€"); // Intl separa la cifra del símbolo con un espacio duro
  assert.equal(L.signedEur(-8.99), nb("−8,99 €"));
  assert.equal(L.signedEur(840.48), nb("+840,48 €"));
  assert.equal(L.signedEur(0), nb("0,00 €"));
  assert.equal(L.signedEur(-0.001), nb("0,00 €"));
  // ING pone la cola del IBAN como «comercio» en recibos y transferencias; BBVA enmascara con «*****».
  assert.equal(L.titleOf({ merchant: "1703280894", description: "Recibo PayPal (Europe) S.a.r.l." }), "Recibo PayPal (Europe) S.a.r.l.");
  assert.equal(L.titleOf({ merchant: "*****", description: "BIZUM // OTROS // ENVIADO: limpieza" }), "BIZUM // OTROS // ENVIADO: limpieza");
  assert.equal(L.titleOf({ merchant: "BAZAR GRAN VIA VIGO ES", description: "Pago en BAZAR GRAN VIA VIGO ES" }), "BAZAR GRAN VIA VIGO");
  assert.equal(L.titleOf({ merchant: null, description: "" }), "—");
});

test("eur/eur0: separador de millar también por debajo de 10.000 (useGrouping:true)", () => {
  // Sin useGrouping:true, el locale es-ES solo agrupa a partir de 10.000 (exige 2+ dígitos en el
  // primer grupo): 1234,56 salía sin punto. El Señor pidió el punto también entre 1.000 y 9.999.
  assert.equal(L.eur.format(1234.56), "1.234,56\u00a0€");
  assert.equal(L.eur.format(9310.35), "9.310,35\u00a0€");
  assert.equal(L.eur.format(999.5), "999,50\u00a0€"); // por debajo de 1.000 no hay nada que agrupar
  assert.equal(L.eur.format(12345.67), "12.345,67\u00a0€"); // ya lo llevaba antes; sigue igual
  assert.equal(L.eur0.format(9999.99), "10.000\u00a0€");
  assert.equal(L.signedEur(1234.56), "+1.234,56\u00a0€");
});

test("Diario: filtros en la ruta ↔ parámetros de la API", () => {
  const f = L.diaryFilters("cuenta=acc1&tipo=cargos&marca=recibos&q=caf%C3%A9+vigo&otro=x&desde=");
  assert.deepEqual(f, { cuenta: "acc1", tipo: "cargos", marca: "recibos", q: "café vigo" });
  assert.equal(L.diaryQuery(f), "cuenta=acc1&tipo=cargos&marca=recibos&q=caf%C3%A9+vigo");
  assert.deepEqual(L.diaryFilters(L.diaryQuery(f)), f); // ida y vuelta
  assert.deepEqual(L.ledgerParams({ ...f, etiqueta: "tomiño", desde: "2026-10-01", hasta: "2026-10-21" }), {
    account: "acc1", type: "cargos", mark: "recibos", tag: "tomiño", from: "2026-10-01", to: "2026-10-21", q: "café vigo",
  });
  assert.deepEqual(L.diaryFilters(""), {});
  assert.equal(L.diaryQuery({}), "");
  // La ruta lleva los filtros en un único segmento codificado: sobrevive a parseHash.
  const hash = `#/diario/${encodeURIComponent(L.diaryQuery(f))}`;
  assert.deepEqual(L.diaryFilters(L.parseHash(hash)[1]), f);
});

test("Diario: varias cuentas a la vez (togglear una no destogglea las demás)", () => {
  assert.deepEqual(L.cuentaIds({}), []);
  assert.deepEqual(L.cuentaIds({ cuenta: "acc1" }), ["acc1"]);
  assert.deepEqual(L.cuentaIds({ cuenta: "acc1,acc3" }), ["acc1", "acc3"]);
  // Añadir una segunda cuenta conserva la primera.
  assert.equal(L.toggleCuenta({ cuenta: "acc1" }, "acc3"), "acc1,acc3");
  // Togglear de nuevo una ya elegida la quita, sin tocar las demás.
  assert.equal(L.toggleCuenta({ cuenta: "acc1,acc3" }, "acc1"), "acc3");
  // Desde vacío, elegir la primera.
  assert.equal(L.toggleCuenta({}, "acc1"), "acc1");
  // El filtro combinado viaja a la API como un único «account» separado por comas.
  assert.deepEqual(L.ledgerParams({ cuenta: "acc1,acc3" }), { account: "acc1,acc3" });
});

test("tirar: resistencia, tope y zonas (mediano recarga la página, largo sincroniza)", () => {
  assert.equal(L.pullDistance(-30), 0);                 // hacia arriba no es tirar
  assert.equal(L.pullDistance(0), 0);
  assert.equal(L.pullDistance(100), 100 * L.PULL.damping);
  assert.equal(L.pullDistance(1000), L.PULL.max);       // tope
  assert.equal(L.pullZone(0), "none");
  assert.equal(L.pullZone(L.PULL.reload - 0.1), "none");
  assert.equal(L.pullZone(L.PULL.reload), "reload");
  assert.equal(L.pullZone(L.PULL.sync - 0.1), "reload");
  assert.equal(L.pullZone(L.PULL.sync), "sync");
  assert.equal(L.pullZone(L.PULL.max), "sync");         // el tope del indicador llega a la zona larga
  assert.equal(L.pullZone(L.pullDistance(1000)), "sync");
  // Distancias de dedo razonables: el mediano no es un roce ni media pantalla, el largo es claramente más largo
  // (margen para no pasar de uno a otro sin querer) y cabe en una pantalla de móvil.
  const finger = (pull) => pull / L.PULL.damping;
  // Tirones largos a propósito (petición del Señor): ~200 px de dedo para recargar y ~370 px para sincronizar.
  assert.ok(finger(L.PULL.reload) > 180 && finger(L.PULL.reload) < 230, `mediano: ${finger(L.PULL.reload)}`);
  assert.ok(finger(L.PULL.sync) > 340 && finger(L.PULL.sync) < 420, `largo: ${finger(L.PULL.sync)}`);
  assert.ok(finger(L.PULL.sync) - finger(L.PULL.reload) >= 120);
  assert.ok(L.PULL.reload < L.PULL.sync && L.PULL.sync <= L.PULL.max && L.PULL.hold <= L.PULL.max);
});

test("tirar para sincronizar: tiempos de espera legibles", () => {
  assert.equal(L.waitLabel(0), "1 s");
  assert.equal(L.waitLabel(45.2), "46 s");
  assert.equal(L.waitLabel(89), "89 s");
  assert.equal(L.waitLabel(90), "2 min");
  assert.equal(L.waitLabel(240), "4 min");
});

test("tirar para sincronizar: mensaje según el resultado", () => {
  const snap = (o) => ({ outcome: "ok", new_transactions: 0, banks: [], error: null, retry_after: 0, ...o });
  assert.deepEqual(L.syncResult(snap({ new_transactions: 3 }), true), { tone: "ok", text: "Sincronizado · 3 movimientos nuevos" });
  assert.equal(L.syncResult(snap({ new_transactions: 1 }), true).text, "Sincronizado · 1 movimiento nuevo");
  assert.equal(L.syncResult(snap({}), true).text, "Sincronizado · sin movimientos nuevos");

  const partial = L.syncResult(snap({ outcome: "partial", new_transactions: 2, error: "el banco ha limitado los accesos (429)",
    banks: [{ bank: "BBVA", status: "ok" }, { bank: "Trade Republic", status: "error" }] }), true);
  assert.equal(partial.tone, "warn");
  assert.equal(partial.text, "Sincronizado con avisos · 2 movimientos nuevos. Trade Republic no ha podido sincronizarse: el banco ha limitado los accesos (429).");
  assert.match(L.syncResult(snap({ outcome: "partial", banks: [{ bank: "A", status: "error" }, { bank: "B", status: "error" }] }), true).text, /A, B no han podido/);

  const error = L.syncResult(snap({ outcome: "error", error: "fallo de red (DNS).", banks: [{ bank: "ING", status: "error" }] }), true);
  assert.deepEqual(error, { tone: "error", text: "No se ha podido sincronizar (ING): fallo de red (DNS). Se muestran los datos que ya había." });
  assert.equal(L.syncResult(snap({ outcome: "error" }), true).text, "No se ha podido sincronizar. Se muestran los datos que ya había.");

  assert.equal(L.syncResult(snap({ outcome: "busy" }), true).tone, "info");
  assert.equal(L.syncResult(snap({ outcome: "timeout" }), true).tone, "warn");
});

test("tirar para sincronizar: si el servidor la frena, se dice cuándo se podrá y no se afirma que se sincronizó", () => {
  const frenada = L.syncResult({ outcome: "ok", retry_after: 240, new_transactions: 5 }, false);
  assert.equal(frenada.tone, "info");
  assert.equal(frenada.text, "Datos recargados. La última sincronización es reciente: podrá volver a sincronizar en 4 min.");
  assert.doesNotMatch(frenada.text, /Sincronizado|nuevo/);  // el resultado de la corrida anterior no se presenta como de ahora
  assert.equal(L.syncResult({ retry_after: 0 }, false).text, "Datos recargados.");
});

test("tirar para sincronizar: solo la lista del Diario (y lo que cae en ella) puede hacerlo", () => {
  const v = (hash) => L.viewOf(L.parseHash(hash));
  // Diario: la lista, con o sin filtros; la ruta vacía y las desconocidas o incompletas también pintan la lista.
  for (const h of ["", "#/", "#/diario", "#/diario/cuenta=acc1&tipo=cargos", "#/desconocida", "#/diario/mov", "#/rango/a/b"]) {
    assert.equal(v(h), "diario", h);
    assert.equal(L.pullEnabled(v(h)), true, h);
  }
  // El resto de vistas, no: pestañas, listas de movimientos de un periodo y detalle de un movimiento.
  const otras = { "#/mes/2026-10/gastos": "mes", "#/mes": "mes", "#/evolucion": "evolucion", "#/evolucion/acumulado": "evolucion",
    "#/tendencias": "tendencias", "#/rango/tendencias/2026-09-01/2026-09-30/gastos/g/Vivienda/mov": "rango",
    "#/diario/mov/abc-123": "movimiento" };
  for (const [h, view] of Object.entries(otras)) {
    assert.equal(v(h), view, h);
    assert.equal(L.pullEnabled(v(h)), false, h);
  }
  assert.equal(L.pullEnabled(""), false); // cargando, error o PIN: nada en pantalla
});

test("saldos: la cifra se parte en dígitos que ruedan y símbolos fijos", () => {
  const nb = "\u00a0"; // Intl separa la cifra del «€» con un espacio duro
  const chars = L.figureChars(`9.380,35${nb}€`);
  assert.equal(chars.map((c) => c.ch).join(""), `9.380,35${nb}€`);                       // no se pierde ni se inventa nada
  assert.deepEqual(chars.filter((c) => c.digit !== null).map((c) => c.digit), [9, 3, 8, 0, 3, 5]);
  assert.deepEqual(chars.filter((c) => c.digit === null).map((c) => c.ch), [".", ",", nb, "€"]);
  assert.equal(L.figureChars(L.eur.format(-0.5)).some((c) => c.ch === "−" || c.ch === "-"), true); // el signo es un símbolo fijo
  assert.deepEqual(L.figureChars(""), []);
});

test("saldos: la cinta de cada dígito da una vuelta completa y acaba en él", () => {
  for (let d = 0; d <= 9; d++) {
    const tape = L.odoTape(d);
    assert.equal(tape.length, L.TAPE_LEN);
    assert.deepEqual(tape.slice(0, 10), [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);                 // una vuelta entera
    assert.equal(tape[tape.length - 1], d);                                                // y la celda de reposo es el dígito
  }
  // Los -90,9091 % de app.css son (TAPE_LEN-1)/TAPE_LEN del alto de la cinta.
  assert.equal(((L.TAPE_LEN - 1) / L.TAPE_LEN * 100).toFixed(4), "90.9091");
});

test("saldos: los dígitos arrancan escalonados, de izquierda a derecha y tarjeta a tarjeta", () => {
  assert.equal(L.rollDelay(0, 0), 0);
  assert.ok(L.rollDelay(1, 0) > L.rollDelay(0, 0));
  assert.ok(L.rollDelay(0, 1) > L.rollDelay(0, 0));
  // Toda la animación (la cifra más larga de la última tarjeta + 1 s de giro) cabe en un par de segundos.
  assert.ok(L.rollDelay(8, 4) + 1100 < 2200, `termina a los ${L.rollDelay(8, 4) + 1100} ms`);
});

test("saldos: el aviso del total dice de cuántas cuentas es y qué salvedades tiene, en una línea", () => {
  const today = "2026-10-23";
  const base = { count: 4, missing: [], disponible: [], outdated: [], oldest: "2026-10-23T05:49:49Z" };
  assert.equal(L.totalNote(base, today), "4 cuentas · más antiguo: hoy 07:49");
  assert.equal(L.totalNote({ ...base, count: 1 }, today), "1 cuenta · más antiguo: hoy 07:49");
  assert.equal(L.totalNote({ ...base, oldest: "2026-10-22T05:30:00Z" }, today), "4 cuentas · más antiguo: ayer 07:30");
  assert.equal(L.totalNote({ ...base, disponible: ["Revolut"] }, today), "4 cuentas · más antiguo: hoy 07:49 · Revolut: saldo disponible");
  const todo = L.totalNote({ ...base, missing: ["ING"], outdated: ["BBVA", "Revolut"], disponible: ["Revolut"] }, today);
  assert.equal(todo, "4 cuentas · más antiguo: hoy 07:49 · sin saldo: ING · desactualizado: BBVA, Revolut · Revolut: saldo disponible");
  // Con todo en orden y una cuenta solo con saldo disponible cabe en una línea de la tarjeta (≈ 55-65 caracteres).
  assert.ok(L.totalNote({ ...base, disponible: ["Revolut"] }, today).length <= 65);
});

