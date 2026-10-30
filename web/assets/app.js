(async () => {
  const $ = id => document.getElementById(id);
  const PAGE = 60;
  const st = {q: "", sort: "s", country: "", shop: "", max: "", types: new Set(), basis: "1", own: true, rated: false, cheapest: false, multi: false, shown: PAGE};

  const [meta, deals, shops] = await Promise.all([
    V.header(), V.json("data/deals.json").catch(() => []), V.json("data/retailers.json").catch(() => []),
  ]);
  const shopName = Object.fromEntries(shops.map(s => [s.id, s.name]));
  if (meta) {
    $("meta").textContent = `Opdateret ${V.when(meta.generated)} · ${deals.length.toLocaleString("da-DK")} tilbud ud af ` +
      `${(meta.stats.vine || 0).toLocaleString("da-DK")} vine fra ${meta.shops} forhandlere`;
  } else $("meta").textContent = "Ingen data endnu – kør scanneren først.";

  // søgetekst forberedes én gang
  for (const d of deals) d._s = [d.n, d.pr, d.c, shopName[d.r], d.y, d.t].join(" ").toLowerCase();

  // filtre
  const countries = [...new Set(deals.map(d => d.c).filter(Boolean))].sort((a, b) => a.localeCompare(b, "da"));
  $("country").insertAdjacentHTML("beforeend", countries.map(c => `<option>${V.esc(c)}</option>`).join(""));
  const shopIds = [...new Set(deals.map(d => d.r))].sort((a, b) => (shopName[a] || a).localeCompare(shopName[b] || b, "da"));
  $("shop").insertAdjacentHTML("beforeend", shopIds.map(s => `<option value="${V.esc(s)}">${V.esc(shopName[s] || s)}</option>`).join(""));
  $("types").innerHTML = V.types.map(([k, n]) =>
    `<button class="chip" data-t="${k}"><span class="dot" style="background:${V.typeColor(k)}"></span>${n} <small>${deals.filter(d => d.t === k).length}</small></button>`).join("");

  // tilstand i URL'en, så et filter kan deles/bogmærkes
  const url = new URLSearchParams(location.hash.slice(1));
  for (const k of ["q", "sort", "country", "shop", "max", "basis"]) if (url.get(k)) st[k] = url.get(k);
  for (const k of ["rated", "cheapest", "multi"]) st[k] = url.get(k) === "1";
  st.own = url.get("own") !== "0";
  (url.get("types") || "").split(",").filter(Boolean).forEach(t => st.types.add(t));
  const sync = () => {
    $("q").value = st.q; $("sort").value = st.sort; $("country").value = st.country; $("shop").value = st.shop; $("max").value = st.max;
    document.querySelectorAll("#types .chip").forEach(b => b.classList.toggle("on", st.types.has(b.dataset.t)));
    document.querySelectorAll("#basis button").forEach(b => b.classList.toggle("on", b.dataset.v === st.basis));
    for (const k of ["own", "rated", "cheapest", "multi"]) $(k).classList.toggle("on", st[k]);
  };
  const save = () => {
    const p = new URLSearchParams();
    for (const k of ["q", "country", "shop", "max"]) if (st[k]) p.set(k, st[k]);
    if (st.sort !== "s") p.set("sort", st.sort);
    if (st.basis !== "1") p.set("basis", st.basis);
    for (const k of ["rated", "cheapest", "multi"]) if (st[k]) p.set(k, "1");
    if (st.types.size) p.set("types", [...st.types].join(","));
    if (!st.own) p.set("own", "0");
    history.replaceState(null, "", p.toString() ? "#" + p : location.pathname);
  };

  const price = d => st.basis === "6" ? d.p6 : d.p1;
  const sorters = {
    s: (a, b) => b.s - a.s,
    d: (a, b) => (b.de - a.de) || (b.s - a.s),
    p: (a, b) => price(a) - price(b),
    "-p": (a, b) => price(b) - price(a),
    q: (a, b) => ((b.q ?? -1) - (a.q ?? -1)) || (b.s - a.s),
    v: (a, b) => ((b.v ?? -99) - (a.v ?? -99)) || (b.s - a.s),
    n: (a, b) => a.n.localeCompare(b.n, "da"),
    fs: (a, b) => (b.fs || 0) - (a.fs || 0),
  };

  function filtered() {
    const words = st.q.toLowerCase().split(/\s+/).filter(Boolean);
    const max = parseFloat(st.max);
    return deals.filter(d =>
      (!st.types.size || st.types.has(d.t)) &&
      (!st.country || d.c === st.country) &&
      (!st.shop || d.r === st.shop) &&
      (!max || price(d) <= max) &&
      (!st.own || d.of) &&
      (!st.rated || d.q != null) &&
      (!st.cheapest || d.fl.includes("billigst i DK")) &&
      (!st.multi || d.mn > 0) &&
      words.every(w => d._s.includes(w))
    ).sort(sorters[st.sort] || sorters.s);
  }

  const scoreColor = s => s >= 70 ? "var(--ok)" : s >= 50 ? "var(--gold)" : "var(--muted)";

  function card(d) {
    const main = price(d), alt = st.basis === "6" ? d.p1 : d.p6;
    const altLabel = st.basis === "6" ? "ved 1 fl." : "ved 6 fl.";
    const rt = d.rt.map(r => {
      const label = `${V.esc(r.s)} ${V.esc(r.v)}${r.n && !r.own ? ` <small>(${r.n.toLocaleString("da-DK")})</small>` : ""}`;
      const title = r.own ? `Oplyst af forhandleren${r.n > 1 ? ` (${r.n} forhandlere)` : ""}` : `Ekstern kilde${r.m ? `, match ${Math.round(r.m * 100)} %` : ""}${r.yr ? "" : ", alle årgange"}`;
      return `<span class="tag" title="${title}">${r.u ? `<a href="${V.esc(r.u)}" target="_blank" rel="noopener">${label}</a>` : label}${r.own ? " *" : ""}</span>`;
    }).join("");
    const flags = (d.of ? `<span class="tag good" title="Forhandleren har selv sat varen ned">forhandlertilbud</span>` : "") + d.fl.map(f => `<span class="tag ${f.includes("ikke") ? "warn" : "good"}">${V.esc(f)}</span>`).join("");
    const was = d.bp ? `<div class="was">${d.bk === "vejledende" ? "Vejl." : "Før"} <s>${V.kr(d.bp)}</s></div>` : "";
    const mkt = d.m1 ? `<div class="was">Andre (${d.mn}): ${V.kr(d.m1)}</div>` : "";
    return `<article class="card">
      <div class="score" style="background:${scoreColor(d.s)}" title="Samlet tilbudsscore 0–100">${d.s}<small>score</small></div>
      <div>
        <h3 class="name"><a href="${V.esc(d.u)}" target="_blank" rel="noopener">${V.esc(d.n)}</a></h3>
        <div class="sub"><span class="tdot" style="background:${V.typeColor(d.t)}"></span>${V.typeName(d.t)}
          ${d.y ? `· ${d.y}` : ""} ${d.c ? `· ${V.esc(d.c)}` : ""} ${d.pr ? `· ${V.esc(d.pr)}` : ""}
          · <b>${V.esc(shopName[d.r] || d.r)}</b>${d.vol && d.vol !== 75 ? ` · ${d.vol} cl (pris omregnet til 75 cl)` : ""}</div>
        <div class="tags">${d.q != null ? `<span class="tag q" title="Samlet kvalitet på 100-point skala">${d.q} p</span>` : ""}
          ${d.v != null ? `<span class="tag ${d.v >= 2 ? "good" : ""}" title="Point over/under hvad vine til samme pris typisk får">${d.v > 0 ? "+" : ""}${d.v} p for prisen</span>` : ""}
          ${rt}${flags}</div>
      </div>
      <div class="price"><div class="big">${V.kr(main)} <small>/fl.</small></div>
        <div class="alt">${V.kr(alt)} ${altLabel}</div>${was}${mkt}</div>
      <div class="deal">${d.d > 0 ? `<span class="badge${d.d < 10 ? " small" : ""}">−${Math.round(d.d)} %</span>` : ""}
        <div class="ref">${d.ref ? `ift. ${V.esc(d.ref)}` : "god værdi for pengene"}</div>
        ${d.ch && d.ch.p < d.p1 ? `<div class="ref">Billigere hos <a href="${V.esc(d.ch.u)}" target="_blank" rel="noopener">${V.esc(shopName[d.ch.r] || d.ch.r)}</a>: ${V.kr(d.ch.p)}</div>` : ""}
        <a class="go" href="${V.esc(d.u)}" target="_blank" rel="noopener">Til butik →</a></div>
    </article>`;
  }

  function render() {
    save();
    const rows = filtered();
    $("count").textContent = `${rows.length.toLocaleString("da-DK")} tilbud`;
    $("list").innerHTML = rows.length ? rows.slice(0, st.shown).map(card).join("") : `<div class="empty">Ingen tilbud matcher filtrene.</div>`;
    $("more").hidden = rows.length <= st.shown;
  }

  const reset = () => { st.shown = PAGE; render(); };
  let t;
  $("q").addEventListener("input", e => { st.q = e.target.value; clearTimeout(t); t = setTimeout(reset, 150); });
  for (const k of ["sort", "country", "shop"]) $(k).addEventListener("change", e => { st[k] = e.target.value; reset(); });
  $("max").addEventListener("input", e => { st.max = e.target.value; clearTimeout(t); t = setTimeout(reset, 250); });
  $("types").addEventListener("click", e => {
    const b = e.target.closest(".chip"); if (!b) return;
    st.types.has(b.dataset.t) ? st.types.delete(b.dataset.t) : st.types.add(b.dataset.t); sync(); reset();
  });
  $("basis").addEventListener("click", e => { const b = e.target.closest("button"); if (!b) return; st.basis = b.dataset.v; sync(); reset(); });
  for (const k of ["own", "rated", "cheapest", "multi"]) $(k).addEventListener("click", () => { st[k] = !st[k]; sync(); reset(); });
  $("more").addEventListener("click", () => { st.shown += PAGE; render(); });
  sync(); render();
})();
