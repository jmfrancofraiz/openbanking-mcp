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

test("topN agrupa en «Resto» y descarta importes no positivos", () => {
  const groups = [9, 8, 7, 6, 5, 4, 3, 2, -1].map((t, i) => ({ group: `G${i}`, total: t }));
  const slices = L.topN(groups, 7);
  assert.equal(slices.length, 7);
  assert.deepEqual(slices.slice(-1)[0], { label: "Resto (2)", total: 5, rest: true, color: L.REST });
  assert.equal(L.topN(groups.slice(0, 3), 7).length, 3);
});

test("presentación: porcentaje, nombre de ingreso y título limpio", () => {
  assert.equal(L.share(50, 1070), 5);
  assert.equal(L.share(-10, 100), 0);
  assert.equal(L.shown("Ingresos - Nómina"), "Nómina");
  assert.equal(L.cleanTitle("Pago en MERCADONA VIGO ES"), "MERCADONA VIGO");
  assert.equal(L.cleanTitle(null), "—");
  assert.equal(L.colorFor("Vivienda"), L.colorFor("Vivienda"));
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

test("accumulatedMonths: N meses, año actual y todo (solo meses cerrados)", () => {
  const closed = MONTHS.slice(0, -1); // 2025-11 … 2026-03
  assert.deepEqual(L.accumulatedMonths("3", closed, "2026"), ["2026-01", "2026-02", "2026-03"]);
  assert.deepEqual(L.accumulatedMonths("ano", closed, "2026"), ["2026-01", "2026-02", "2026-03"]);
  assert.deepEqual(L.accumulatedMonths("12", closed, "2026"), closed);
  assert.deepEqual(L.accumulatedMonths("todo", closed, "2026"), closed);
  assert.deepEqual(L.accumulatedMonths("ano", ["2025-11", "2025-12"], "2026"), []);
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

test("diffGroups y diffSubcategories", () => {
  const A = [{ group: "Alimentación", total: 60, categories: [{ category: "Alimentación - Super", label: "Super", total: 60 }] },
    { group: "Restauración", total: 40, categories: [] }];
  const B = [{ group: "Alimentación", total: 120, categories: [{ category: "Alimentación - Super", label: "Super", total: 120 }] },
    { group: "Ocio", total: 0.2, categories: [] }];
  const d = L.diffGroups(A, B);
  assert.deepEqual(d.map((x) => [x.k, x.d]), [["Alimentación", -60], ["Restauración", 40]]);
  assert.deepEqual(L.diffSubcategories(A[0], B[0]), [{ label: "Super", va: 60, vb: 120, d: -60 }]);
});
