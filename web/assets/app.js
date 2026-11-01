(async () => {
  const $ = id => document.getElementById(id);
  const PAGE = 60;
  const DEFAULTS = {q: "", sort: "s", country: "", shop: "", min: "", max: "", minq: "", types: [], basis: "1",
                    own: true, rated: false, cheapest: false, multi: false, fresh: false, favs: false};
  const st = {...DEFAULTS, types: new Set(), shown: PAGE};
  const CHIPS = ["own", "rated", "cheapest", "multi", "fresh", "favs"];

  const [meta, deals, shops] = await Promise.all([
    V.header(), V.json("data/deals.json").catch(() => []), V.json("data/retailers.json").catch(() => []),
  ]);
  const shopName = Object.fromEntries(shops.map(s => [s.id, s.name]));
  const shopById = Object.fromEntries(shops.map(s => [s.id, s]));
  if (meta) {
    $("meta").textContent = `Opdateret ${V.when(meta.generated)} · ${deals.length.toLocaleString("da-DK")} tilbud ud af ` +
      `${(meta.stats.vine || 0).toLocaleString("da-DK")} vine fra ${meta.shops} forhandlere`;
  } else $("meta").textContent = "Ingen data endnu – kør scanneren først.";

  // søgning uden hensyn til accenter: "chateau" finder "Château", "rose" finder "Rosé"
  const fold = s => String(s ?? "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
  const built = meta ? Date.parse(meta.generated) : Date.now();
  for (const d of deals) {
    d._s = fold([d.n, d.pr, d.c, shopName[d.r], d.y, d.t, V.typeName(d.t)].join(" "));
    d._new = !!d.sd && built - Date.parse(d.sd) < 36 * 3600e3;  // tilbuddet startede inden for det seneste døgn
  }

  // favoritter gemmes kun i denne browser
  const FAV_KEY = "vinjagt:favoritter";
  let favs = new Set();
  try { favs = new Set(JSON.parse(localStorage.getItem(FAV_KEY) || "[]")); } catch (e) { /* privat vindue o.l. */ }
  const saveFavs = () => { try { localStorage.setItem(FAV_KEY, JSON.stringify([...favs])); } catch (e) { /* ignoreres */ } };

  // filtre
  const countries = [...new Set(deals.map(d => d.c).filter(Boolean))].sort((a, b) => a.localeCompare(b, "da"));
  $("country").insertAdjacentHTML("beforeend", countries.map(c => `<option>${V.esc(c)}</option>`).join(""));
  const shopIds = [...new Set(deals.map(d => d.r))].sort((a, b) => (shopName[a] || a).localeCompare(shopName[b] || b, "da"));
  $("shop").insertAdjacentHTML("beforeend", shopIds.map(s => `<option value="${V.esc(s)}">${V.esc(shopName[s] || s)}</option>`).join(""));
  $("types").innerHTML = V.types.map(([k, n]) =>
    `<button class="chip" data-t="${k}"><span class="dot" style="background:${V.typeColor(k)}"></span>${n} <small></small></button>`).join("");

  // tilstand i URL'en, så et filter kan deles/bogmærkes
  const url = new URLSearchParams(location.hash.slice(1));
  for (const k of ["q", "sort", "country", "shop", "min", "max", "minq", "basis"]) if (url.get(k)) st[k] = url.get(k);
  for (const k of CHIPS) if (k !== "own") st[k] = url.get(k) === "1";
  st.own = url.get("own") !== "0";
  (url.get("types") || "").split(",").filter(Boolean).forEach(t => st.types.add(t));

  const active = () => ["q", "country", "shop", "min", "max", "minq"].filter(k => st[k]).length + st.types.size +
    CHIPS.filter(k => st[k] !== DEFAULTS[k]).length + (st.sort !== DEFAULTS.sort) + (st.basis !== DEFAULTS.basis);
  const sync = () => {
    for (const k of ["q", "sort", "country", "shop", "min", "max", "minq"]) $(k).value = st[k];
    document.querySelectorAll("#types .chip").forEach(b => b.classList.toggle("on", st.types.has(b.dataset.t)));
    document.querySelectorAll("#basis button").forEach(b => b.classList.toggle("on", b.dataset.v === st.basis));
    for (const k of CHIPS) $(k).classList.toggle("on", st[k]);
    $("favn").textContent = favs.size || "";
    const n = active();
    $("reset").hidden = !n;
    $("ftoggle").textContent = n ? `Filtre (${n})` : "Filtre";
    $("ftoggle").classList.toggle("active", n > 0);
  };
  const save = () => {
    const p = new URLSearchParams();
    for (const k of ["q", "country", "shop", "min", "max", "minq"]) if (st[k]) p.set(k, st[k]);
    if (st.sort !== "s") p.set("sort", st.sort);
    if (st.basis !== "1") p.set("basis", st.basis);
    for (const k of CHIPS) if (k !== "own" && st[k]) p.set(k, "1");
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
    fs: (a, b) => (b.sd || "").localeCompare(a.sd || "") || (b.s - a.s),
  };

  // alle filtre undtagen vintype – så knapperne kan vise, hvor mange hver type giver med de øvrige filtre
  function matches(d, words, min, max, minq) {
    return (!st.country || d.c === st.country) &&
      (!st.shop || d.r === st.shop) &&
      (!min || price(d) >= min) &&
      (!max || price(d) <= max) &&
      (!minq || (d.q != null && d.q >= minq)) &&
      (!st.own || d.of) &&
      (!st.rated || d.q != null) &&
      (!st.cheapest || d.fl.includes("billigst i DK")) &&
      (!st.multi || d.mn > 0) &&
      (!st.fresh || d._new) &&
      (!st.favs || favs.has(d.u)) &&
      words.every(w => d._s.includes(w));
  }

  function filtered() {
    const words = fold(st.q).split(/\s+/).filter(Boolean);
    const min = parseFloat(st.min), max = parseFloat(st.max), minq = parseFloat(st.minq);
    const base = deals.filter(d => matches(d, words, min, max, minq));
    const counts = {};
    for (const d of base) counts[d.t] = (counts[d.t] || 0) + 1;
    document.querySelectorAll("#types .chip").forEach(b => { b.querySelector("small").textContent = counts[b.dataset.t] || 0; });
    const rows = base.filter(d => !st.types.size || st.types.has(d.t)).sort(sorters[st.sort] || sorters.s);
    // samme vin på tilbud hos flere forhandlere -> ét kort (det bedste efter den valgte sortering)
    const groups = new Map();
    for (const d of rows) {
      const g = groups.get(d.g);
      if (g) g.also.push(d); else groups.set(d.g, {d, also: []});
    }
    return {groups: [...groups.values()], offers: rows.length};
  }

  const scoreColor = s => s >= 70 ? "var(--ok)" : s >= 50 ? "var(--gold)" : "var(--muted)";

  // prisudvikling: laveste pris pr. uge de sidste 13 uger
  function spark(d) {
    const pts = (d.h || []).map((p, i) => [i, p]).filter(([, p]) => p != null);
    if (pts.length < 3) return "";
    const ps = pts.map(([, p]) => p), lo = Math.min(...ps), hi = Math.max(...ps), W = 120, H = 26;
    const x = i => 2 + i * (W - 4) / 12, y = p => hi === lo ? H / 2 : 3 + (hi - p) * (H - 6) / (hi - lo);
    const line = pts.map(([i, p]) => `${x(i).toFixed(1)},${y(p).toFixed(1)}`).join(" ");
    const [li, lp] = pts[pts.length - 1];
    const title = `Laveste pris pr. uge de sidste 13 uger: ${V.kr(lo)} – ${V.kr(hi)}`;
    return `<svg class="spark" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${title}"><title>${title}</title>
      <polyline points="${line}" fill="none" stroke="var(--muted)" stroke-width="1.5" stroke-linejoin="round"/>
      <circle cx="${x(li).toFixed(1)}" cy="${y(lp).toFixed(1)}" r="2.5" fill="var(--wine)"/></svg>`;
  }

  function shipping(r) {
    const s = shopById[r] || {};
    if (s.free_shipping_over != null) return `fri fragt over ${V.kr(s.free_shipping_over)}`;
    if (s.shipping != null) return `fragt ${V.kr(s.shipping)}`;
    return "";
  }

  function card({d, also}) {
    const main = price(d), alt = st.basis === "6" ? d.p1 : d.p6;
    const altLabel = st.basis === "6" ? "ved 1 fl." : "ved 6 fl.";
    const rt = d.rt.map(r => {
      const label = `${V.esc(r.s)} ${V.esc(r.v)}${r.n && !r.own ? ` <small>(${r.n.toLocaleString("da-DK")})</small>` : ""}`;
      const title = r.own ? `Oplyst af forhandleren${r.n > 1 ? ` (${r.n} forhandlere)` : ""}` : `Ekstern kilde${r.m ? `, match ${Math.round(r.m * 100)} %` : ""}${r.yr ? "" : ", alle årgange"}`;
      return `<span class="tag" title="${title}">${r.u ? `<a href="${V.esc(r.u)}" target="_blank" rel="noopener">${label}</a>` : label}${r.own ? " *" : ""}</span>`;
    }).join("");
    const fxTag = d.fx ? `<span class="tag warn" title="Udenlandsk webshop: pris omregnet til kroner, uden dansk vinafgift, moms-forskel og fragt">${V.esc(d.fx)}-shop · omregnet, uden dansk afgift</span>` : "";
    const lowest = d.lo && (d.h || []).filter(p => p != null).length >= 4 && d.p1 <= d.lo * 1.005;
    const flags = (d._new ? `<span class="tag new" title="Tilbuddet startede ${V.esc(d.sd)}">ny</span>` : "") + fxTag +
      (d.of ? `<span class="tag good" title="Forhandleren har selv sat varen ned">forhandlertilbud</span>` : "") +
      (lowest ? `<span class="tag good" title="Ingen lavere pris hos forhandleren de sidste 90 dage">laveste pris i 90 dage</span>` : "") +
      d.fl.map(f => `<span class="tag ${f.includes("ikke") ? "warn" : "good"}">${V.esc(f)}</span>`).join("");
    const was = d.bp ? `<div class="was">${d.bk === "vejledende" ? "Vejl." : "Før"} <s>${V.kr(d.bp)}</s></div>` : "";
    const mkt = d.m1 ? `<div class="was">Andre (${d.mn}): ${V.kr(d.m1)}</div>` : "";
    const lo = d.lo && !lowest && d.lo < d.p1 ? `<div class="was" title="Laveste pris hos forhandleren de sidste 90 dage">Laveste 90 dage: ${V.kr(d.lo)}</div>` : "";
    const ship = shipping(d.r);
    const img = d.img ? `<img class="thumb" src="${V.esc(d.img)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">` : "";
    const alsoHtml = also.length ? `<div class="also">Også på tilbud hos ${also.map(a =>
      `<a href="${V.esc(a.u)}" target="_blank" rel="noopener">${V.esc(shopName[a.r] || a.r)}</a> ${V.kr(price(a))}`).join(", ")}</div>` : "";
    const fav = favs.has(d.u);
    return `<article class="card">
      <div class="score" style="background:${scoreColor(d.s)}" title="Samlet tilbudsscore 0–100">${d.s}<small>score</small></div>
      <div class="main">${img}<div>
        <h3 class="name"><a href="${V.esc(d.u)}" target="_blank" rel="noopener">${V.esc(d.n)}</a>
          <button class="fav${fav ? " on" : ""}" data-u="${V.esc(d.u)}" aria-pressed="${fav}" title="${fav ? "Fjern fra favoritter" : "Gem som favorit"}">${fav ? "♥" : "♡"}</button></h3>
        <div class="sub"><span class="tdot" style="background:${V.typeColor(d.t)}"></span>${V.typeName(d.t)}
          ${d.y ? `· ${d.y}` : ""} ${d.c ? `· ${V.esc(d.c)}` : ""} ${d.pr ? `· ${V.esc(d.pr)}` : ""}
          · <b>${V.esc(shopName[d.r] || d.r)}</b>${ship ? ` · ${ship}` : ""}${d.vol && d.vol !== 75 ? ` · ${d.vol} cl (pris omregnet til 75 cl)` : ""}</div>
        <div class="tags">${d.q != null ? `<span class="tag q" title="Samlet kvalitet på 100-point skala">${d.q} p</span>` : ""}
          ${d.v != null ? `<span class="tag ${d.v >= 2 ? "good" : ""}" title="Point over/under hvad vine til samme pris typisk får">${d.v > 0 ? "+" : ""}${d.v} p for prisen</span>` : ""}
          ${rt}${flags}</div>
        ${alsoHtml}
      </div></div>
      <div class="price"><div class="big">${V.kr(main)} <small>/fl.</small></div>
        <div class="alt">${V.kr(alt)} ${altLabel}</div>${was}${mkt}${lo}${spark(d)}</div>
      <div class="deal">${d.d > 0 ? `<span class="badge${d.d < 10 ? " small" : ""}">−${Math.round(d.d)} %</span>` : ""}
        <div class="ref">${d.ref ? `ift. ${V.esc(d.ref)}` : "god værdi for pengene"}</div>
        ${d.ch && d.ch.p < d.p1 ? `<div class="ref">Billigere hos <a href="${V.esc(d.ch.u)}" target="_blank" rel="noopener">${V.esc(shopName[d.ch.r] || d.ch.r)}</a>: ${V.kr(d.ch.p)}</div>` : ""}
        <a class="go" href="${V.esc(d.u)}" target="_blank" rel="noopener">Til butik →</a></div>
    </article>`;
  }

  function render() {
    save(); sync();
    const {groups, offers} = filtered();
    $("count").textContent = groups.length === offers
      ? `${offers.toLocaleString("da-DK")} tilbud`
      : `${groups.length.toLocaleString("da-DK")} vine · ${offers.toLocaleString("da-DK")} tilbud`;
    const empty = st.favs && !favs.size ? "Du har ingen favoritter endnu – tryk ♡ ved en vin for at gemme den."
      : "Ingen tilbud matcher filtrene.";
    $("list").innerHTML = groups.length ? groups.slice(0, st.shown).map(card).join("") : `<div class="empty">${empty}</div>`;
    $("more").hidden = groups.length <= st.shown;
  }

  const reset = () => { st.shown = PAGE; render(); };
  let t;
  $("q").addEventListener("input", e => { st.q = e.target.value; clearTimeout(t); t = setTimeout(reset, 150); });
  for (const k of ["sort", "country", "shop", "minq"]) $(k).addEventListener("change", e => { st[k] = e.target.value; reset(); });
  for (const k of ["min", "max"]) $(k).addEventListener("input", e => { st[k] = e.target.value; clearTimeout(t); t = setTimeout(reset, 250); });
  $("types").addEventListener("click", e => {
    const b = e.target.closest(".chip"); if (!b) return;
    st.types.has(b.dataset.t) ? st.types.delete(b.dataset.t) : st.types.add(b.dataset.t); reset();
  });
  $("basis").addEventListener("click", e => { const b = e.target.closest("button"); if (!b) return; st.basis = b.dataset.v; reset(); });
  for (const k of CHIPS) $(k).addEventListener("click", () => { st[k] = !st[k]; reset(); });
  $("reset").addEventListener("click", () => { Object.assign(st, {...DEFAULTS, types: new Set()}); reset(); });
  $("ftoggle").addEventListener("click", () => {
    const open = $("filters").classList.toggle("open");
    $("ftoggle").setAttribute("aria-expanded", open);
  });
  $("list").addEventListener("click", e => {
    const b = e.target.closest(".fav"); if (!b) return;
    favs.has(b.dataset.u) ? favs.delete(b.dataset.u) : favs.add(b.dataset.u);
    saveFavs(); render();
  });
  $("more").addEventListener("click", () => { st.shown += PAGE; render(); });
  render();
})();
