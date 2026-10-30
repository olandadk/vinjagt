const V = {
  types: [["rød","Rød"],["hvid","Hvid"],["rosé","Rosé"],["mousserende","Mousserende"],["dessert","Dessert/hedvin"],["orange","Orange"]],
  kr: n => n == null ? "–" : n.toLocaleString("da-DK", {minimumFractionDigits: n % 1 ? 2 : 0, maximumFractionDigits: 2}) + " kr.",
  esc: s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])),
  typeColor: t => `var(--t-${t || "rød"})`,
  typeName: t => (V.types.find(x => x[0] === t) || [, "Ukendt type"])[1],
  async json(p) { const r = await fetch(p, {cache: "no-cache"}); if (!r.ok) throw new Error(p); return r.json(); },
  when(iso) {
    if (!iso) return "aldrig";
    const d = new Date(iso);
    return d.toLocaleString("da-DK", {day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"});
  },
  async header() {
    const m = await V.json("data/meta.json").catch(() => null);
    if (m) {
      document.getElementById("title").firstChild.textContent = m.title;
      document.getElementById("subtitle").textContent = m.subtitle;
    }
    return m;
  },
};
