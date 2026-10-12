/* Městský chatbot – chování stránky.
 *
 * Bez knihoven a bez sestavování. Části:
 *   1. dotaz → POST /api/ask → odpověď se značkami [n], zdroje jako úzké řádky (podrobnosti po rozkliknutí)
 *   2. „Zobrazit v předpisu“ → GET /api/doc/{id} → znění dokumentu se zvýrazněným odstavcem (panel vpravo
 *      vyjede vedle vlákna, dá se zasunout a vrátit)
 *   3. historie dotazů a poznámky – jen v prohlížeči (localStorage), nikam se neposílají
 *   4. kopírování a stahování odpovědí, poznámek a znění předpisu (textové soubory)
 *   5. stav indexu, světlý a tmavý režim, postranní panel, klávesy (Enter odešle, Esc zavře, „/“ do pole)
 *   6. přihlášení společným heslem (jen když ho server vyžaduje) a varování před odesláním dotazu,
 *      který vypadá na osobní údaj (pravidla v osobni-udaje.js)
 *
 * Bezpečnost: vše, co přijde ze serveru, se vkládá jako prostý text (textContent). Nikde se
 * nepoužívá innerHTML – odpověď modelu ani text dokumentu tak nemohou do stránky vložit kód.
 *
 * Název, organizace a texty jsou v souboru nastaveni.js (vzniká z chatbot.toml při sestavení indexu).
 */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  // Nastavení instance: čí chatbot to je a jak se představuje (nastaveni.js ← chatbot.toml).
  const CFG = Object.assign({ nazev: "Chatbot", organizace: "", popis: "", upozorneni: "", kontakt: "", odkaz: "", navod: "",
                              barva: "#1d4f91", barva_tmava: "#0f1b2a" }, window.NASTAVENI || {});
  const TITLE = CFG.organizace ? `${CFG.nazev} – ${CFG.organizace}` : CFG.nazev;
  const SVG = "http://www.w3.org/2000/svg";
  const MAX_CHARS = 500;            // stejný limit hlídá i server
  const SUGGESTED = 3;              // kolik návrhů otázek se nabídne najednou
  const HISTORY_MAX = 40;           // kolik vláken se drží v historii prohlížeče
  const NOTE_CLAMP = 220;           // od kolika znaků se odpověď v poznámce sbalí

  // ------------------------------------------------------------------ pomocníci
  /** Vytvoří prvek s třídou a textem (text vždy jako prostý text). */
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  /** Ikona ze sady symbolů v index.html (<use href="#i-…">). */
  function icon(name, className) {
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("class", className ? `ico ${className}` : "ico");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS(SVG, "use");
    use.setAttribute("href", `#i-${name}`);
    svg.append(use);
    return svg;
  }

  /** Tlačítko s ikonou a popiskem (popisek na úzké obrazovce schová CSS, zůstane bublina). */
  function tool(name, label, onClick, extraClass) {
    const button = el("button", extraClass ? `tool ${extraClass}` : "tool");
    button.type = "button";
    button.dataset.tip = label;
    button.setAttribute("aria-label", label);
    button.append(icon(name), el("span", "", label));
    button.addEventListener("click", onClick);
    return button;
  }

  /** „2026-06-01“ → „1. 6. 2026“; jiný text vrátí beze změny. */
  function czDate(value) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || "");
    return m ? `${Number(m[3])}. ${Number(m[2])}. ${m[1]}` : (value || "");
  }
  /** Čas z ISO zápisu → „10. 10. 2026 21:30“. */
  function czDateTime(iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getDate()}. ${d.getMonth() + 1}. ${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  const today = () => new Date().toISOString().slice(0, 10);
  /** „1 předpis“, „3 předpisy“, „12 předpisů“. */
  function plural(count, one, few, many) {
    return `${count} ${count === 1 ? one : (count >= 2 && count <= 4 ? few : many)}`;
  }
  /** Odkaz ven (na originál dokumentu). Jen adresy http a https – nic jiného se do odkazu nedostane. */
  function externalLink(url, label) {
    if (!/^https?:\/\//i.test(String(url || ""))) return null;
    const link = el("a", "link link--out", label);
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    return link;
  }
  const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 8);

  /** Název souboru z textu: bez diakritiky, jen písmena a číslice. */
  function slug(text, max = 48) {
    return String(text || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase()
      .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, max) || "soubor";
  }

  // úložiště prohlížeče: když není k dispozici (soukromé okno, zákaz), stránka běží dál bez něj
  const store = {
    get(key, fallback) {
      try { const raw = localStorage.getItem(key); return raw === null ? fallback : JSON.parse(raw); } catch { return fallback; }
    },
    set(key, value) {
      try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch { return false; }
    },
    raw(key) { try { return localStorage.getItem(key); } catch { return null; } },
    setRaw(key, value) { try { localStorage.setItem(key, value); } catch { /* bez úložiště */ } },
  };

  /** Stáhne text jako soubor (UTF-8 se značkou, aby ho poznal i starší Poznámkový blok a Excel). */
  function download(filename, text) {
    const blob = new Blob(["\uFEFF" + text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = el("a");
    a.href = url;
    a.download = filename;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // starší prohlížeč nebo stránka bez https: náhradní cesta přes skryté pole
      const area = el("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.className = "sr-only";
      document.body.append(area);
      area.select();
      let ok = false;
      try { ok = document.execCommand("copy"); } catch { ok = false; }
      area.remove();
      return ok;
    }
  }

  // ------------------------------------------------------------------ hláška dole
  let toastTimer = null;
  function toast(message, iconName = "check") {
    const box = $("toast");
    box.replaceChildren(icon(iconName), el("span", "", message));
    box.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { box.hidden = true; }, 2400);
  }

  // ------------------------------------------------------------------ režim (světlý / tmavý)
  const root = document.documentElement;
  const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");
  function isDark() {
    return root.dataset.rezim ? root.dataset.rezim === "tmavy" : darkQuery.matches;
  }
  function reflectMode() {
    const dark = isDark();
    root.classList.toggle("dark", dark);
    $("rezim").querySelector(".rezim__text").textContent = dark ? "Světlý režim" : "Tmavý režim";
    document.querySelector('meta[name="theme-color"]').content = dark ? CFG.barva_tmava : CFG.barva;
  }
  const storedMode = store.raw("rezim");
  if (storedMode === "tmavy" || storedMode === "svetly") root.dataset.rezim = storedMode;
  $("rezim").classList.add("rezim");
  $("rezim").addEventListener("click", () => {
    const next = isDark() ? "svetly" : "tmavy";
    root.dataset.rezim = next;
    store.setRaw("rezim", next);
    reflectMode();
  });
  darkQuery.addEventListener?.("change", reflectMode);
  reflectMode();

  // ------------------------------------------------------------------ značka prohlížeče
  // Náhodná značka uložená v prohlížeči. Server podle ní (spolu s adresou) pozná, komu dát pauzu po
  // opakovaných pokusech o obcházení – lidé z jedné sítě mají společnou adresu a pauza nemá postihnout ostatní.
  // Není to účet ani sledování: nikam se neukládá a s dotazy se nespojuje.
  function clientId() {
    const fresh = () => Array.from(crypto.getRandomValues(new Uint8Array(12)), (b) => b.toString(16).padStart(2, "0")).join("");
    let id = store.raw("znacka");
    if (!/^[a-f0-9]{24}$/.test(id || "")) {
      id = fresh();
      store.setRaw("znacka", id);
    }
    return id;
  }
  const CLIENT_ID = clientId();

  // ------------------------------------------------------------------ stav indexu
  let indexDate = "";
  async function loadStatus() {
    const state = $("stav");
    const banner = $("pruh");
    try {
      const status = await (await fetch("/api/status")).json();
      $("odhlasit").hidden = !status.password;                  // odhlášení má smysl jen se zapnutým heslem
      setLocked(Boolean(status.locked));
      if (!status.ok) {
        state.textContent = "Chatbot teď nemá načtená data.";
        banner.textContent = status.error || "Chatbot teď nemá načtená data.";
        banner.classList.add("banner--error");
        banner.hidden = false;
        return;
      }
      indexDate = czDate(status.built);
      state.textContent = `Stav k ${indexDate} · ${plural(status.docs, "předpis", "předpisy", "předpisů")}`;
      if (status.mode === "zkusebni") {
        banner.textContent = "Zkušební režim: model není připojen. Stránka zatím ukazuje jen místa, která hledání našlo.";
        banner.hidden = false;
      }
    } catch {
      state.textContent = "Stav se nepodařilo načíst.";
    }
  }

  // ------------------------------------------------------------------ přihlášení heslem
  // Server (chatbot/api/access.py) při zapnutém heslu odpovídá jen přihlášenému prohlížeči. Stránka se to
  // dozví ze stavu (`locked`) nebo z odpovědi 401 a překryje se přihlašovací obrazovkou. Heslo se posílá
  // jen na /api/login a nikam se neukládá; přihlášení drží cookie, kterou skript nepřečte.
  function setLocked(locked) {
    const gate = $("zamek");
    if (gate.hidden === !locked) return;
    gate.hidden = !locked;
    $("app").inert = locked;                                    // pod přihlášením nejde nic ovládat ani klávesnicí
    $("panel-bocni").inert = locked;
    if (locked) { $("heslo").value = ""; $("heslo").focus(); }
    else { $("heslo-chyba").hidden = true; $("otazka").focus(); }
  }
  /** Odpověď 401 = přihlášení chybí nebo vypršelo: ukázat přihlašovací obrazovku. Vrací true, když to tak je. */
  function lockedOut(response) {
    if (response.status !== 401) return false;
    setLocked(true);
    return true;
  }
  $("prihlaseni").addEventListener("submit", async (event) => {
    event.preventDefault();
    const problem = $("heslo-chyba");
    const button = $("vstoupit");
    button.disabled = true;
    let message = "";
    try {
      const response = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: $("heslo").value }),
      });
      if (!response.ok) message = (await response.json().catch(() => ({}))).detail || "Přihlášení se nepovedlo.";
    } catch {
      message = "Spojení se nepodařilo. Zkontrolujte připojení a zkuste to znovu.";
    }
    button.disabled = false;
    if (message) {
      problem.textContent = message;
      problem.hidden = false;
      $("heslo").select();
      return;
    }
    $("heslo").value = "";
    setLocked(false);
    loadStatus();
  });
  $("odhlasit").addEventListener("click", async () => {
    try { await fetch("/api/logout", { method: "POST" }); } catch { /* bez spojení se zamkne aspoň stránka */ }
    closePanel();
    setLocked(true);
  });

  // ------------------------------------------------------------------ postranní panel
  const wide = window.matchMedia("(min-width: 901px)");
  let sideAuto = false;                 // postranní panel schoval panel vpravo (ne uživatel) – po jeho zavření se vrátí
  function openSide() {
    sideAuto = false;
    if (wide.matches) {
      document.body.classList.remove("side-closed");
      store.setRaw("bocni", "otevreny");
    } else {
      document.body.classList.add("side-open");
      $("zaclona").hidden = false;
    }
  }
  function closeSide() {
    sideAuto = false;
    if (wide.matches) {
      document.body.classList.add("side-closed");
      store.setRaw("bocni", "zavreny");
    } else {
      document.body.classList.remove("side-open");
      $("zaclona").hidden = true;
    }
  }
  if (store.raw("bocni") === "zavreny") document.body.classList.add("side-closed");
  $("otevrit-bocni").addEventListener("click", openSide);
  $("zavrit-bocni").addEventListener("click", closeSide);
  $("zaclona").addEventListener("click", closeSide);
  wide.addEventListener?.("change", () => { document.body.classList.remove("side-open"); $("zaclona").hidden = true; });

  // ------------------------------------------------------------------ historie (jen v prohlížeči)
  // Vlákno = otázky a odpovědi tak, jak přišly ze serveru. Ukládá se po každé odpovědi; nejstarší vlákna se
  // odstraní, když jich je přes HISTORY_MAX nebo když dojde místo v úložišti.
  let conversations = store.get("historie", []);
  if (!Array.isArray(conversations)) conversations = [];
  let currentId = null;

  function saveConversations() {
    while (conversations.length > HISTORY_MAX) conversations.pop();
    if (!store.set("historie", conversations) && conversations.length > 1) {
      conversations.pop();                      // došlo místo: zkusit bez nejstaršího vlákna
      store.set("historie", conversations);
    }
    renderHistory();
  }

  function remember(question, body) {
    let conv = conversations.find((c) => c.id === currentId);
    if (!conv) {
      conv = { id: uid(), created: new Date().toISOString(), title: question.slice(0, 90), items: [] };
      conversations.unshift(conv);
      currentId = conv.id;
    }
    conv.updated = new Date().toISOString();
    conv.items.push({ q: question, a: body });
    saveConversations();
  }

  function renderHistory() {
    const list = $("historie");
    list.replaceChildren();
    conversations.forEach((conv) => {
      const item = el("li");
      const open = el("button", "hist__item", conv.title || "Dotaz");
      open.type = "button";
      open.title = conv.title || "";
      if (conv.id === currentId) open.classList.add("is-active");
      open.addEventListener("click", () => { loadConversation(conv.id); if (!wide.matches) closeSide(); });
      const remove = el("button", "hist__del");
      remove.type = "button";
      remove.setAttribute("aria-label", "Smazat z historie");
      remove.append(icon("trash"));
      remove.addEventListener("click", (event) => {
        event.stopPropagation();
        conversations = conversations.filter((c) => c.id !== conv.id);
        if (conv.id === currentId) startNew();
        saveConversations();
        toast("Smazáno z historie", "trash");
      });
      item.append(open, remove);
      list.append(item);
    });
    $("historie-prazdna").hidden = conversations.length > 0;
    $("smazat-historii").hidden = conversations.length === 0;
  }
  $("smazat-historii").addEventListener("click", () => {
    if (!window.confirm("Vymazat celou historii dotazů z tohoto prohlížeče?")) return;
    conversations = [];
    startNew();
    saveConversations();
  });

  function loadConversation(id) {
    const conv = conversations.find((c) => c.id === id);
    if (!conv) return;
    currentId = id;
    thread.replaceChildren();
    $("uvod").hidden = true;
    conv.items.forEach((item) => {
      thread.append(questionNode(item.q));
      thread.append(answerNode(item.a, item.q));
    });
    renderHistory();
    scrollToBottom(false);
    input.focus();
  }

  function startNew() {
    currentId = null;
    thread.replaceChildren();
    $("uvod").hidden = false;
    renderHistory();
    showSuggestions();
    input.focus();
  }
  $("novy").addEventListener("click", () => { startNew(); if (!wide.matches) closeSide(); });
  $("novy-bar").addEventListener("click", startNew);

  // ------------------------------------------------------------------ poznámky (jen v prohlížeči)
  let notes = store.get("poznamky", []);
  if (!Array.isArray(notes)) notes = [];
  const noteTimers = new Map();

  function saveNotes() {
    store.set("poznamky", notes);
    const count = $("pocet-poznamek");
    count.textContent = String(notes.length);
    count.hidden = notes.length === 0;
  }
  function noteKey(question, answer) { return `${question}\n${answer}`; }
  function findNote(question, answer) {
    const key = noteKey(question, answer);
    return notes.find((n) => n.kind === "odpoved" && noteKey(n.question, n.answer) === key);
  }

  /** Uloží odpověď do poznámek (i se zdroji – kliknutím se dají otevřít ve znění). */
  function addAnswerNote(question, body) {
    const note = {
      id: uid(), kind: "odpoved", created: new Date().toISOString(), question, answer: body.answer || "",
      sources: (body.sources || []).map((s) => ({ n: s.n, doc_id: s.doc_id, doc_label: s.doc_label, label: s.label, anchor: s.anchor })),
      text: "",
    };
    notes.unshift(note);
    saveNotes();
    renderNotes();
    return note;
  }
  function addFreeNote() {
    const note = { id: uid(), kind: "vlastni", created: new Date().toISOString(), question: "", answer: "", sources: [], text: "" };
    notes.unshift(note);
    saveNotes();
    renderNotes();
    openPanel("notes");
    $("poznamky-seznam").querySelector(".ncard__title")?.focus();
  }
  function deleteNote(id) {
    notes = notes.filter((n) => n.id !== id);
    saveNotes();
    renderNotes();
    thread.querySelectorAll(".tool--note.is-on").forEach(syncNoteButton);
    toast("Poznámka smazána", "trash");
  }
  function updateNote(id, patch) {
    const note = notes.find((n) => n.id === id);
    if (!note) return;
    Object.assign(note, patch);
    clearTimeout(noteTimers.get(id));
    noteTimers.set(id, setTimeout(() => { saveNotes(); flashSaved(id); }, 400));   // ukládá se chvíli po dopsání
  }
  function flashSaved(id) {
    const mark = $("poznamky-seznam").querySelector(`[data-saved="${id}"]`);
    if (!mark) return;
    mark.textContent = "Uloženo";
    setTimeout(() => { mark.textContent = ""; }, 1600);
  }

  function noteText(note, withHeader) {
    const lines = [];
    if (withHeader) lines.push(TITLE, "");
    lines.push((note.kind === "vlastni" ? "Poznámka: " : "Dotaz: ") + (note.question || "(bez názvu)"));
    lines.push("Uloženo: " + czDateTime(note.created));
    if (note.answer) lines.push("", "Odpověď:", note.answer);
    if (note.sources.length) {
      lines.push("", "Zdroje:");
      note.sources.forEach((s) => lines.push(`${s.n ? `[${s.n}] ` : ""}${s.doc_label} – ${s.label}`));
    }
    if (note.text) lines.push("", "Moje poznámka:", note.text);
    if (withHeader) lines.push("", "Závazné je znění předpisu, ne odpověď chatbota.");
    return lines.join("\n");
  }

  function renderNotes() {
    const box = $("poznamky-seznam");
    box.replaceChildren();
    notes.forEach((note) => box.append(noteCard(note)));
    $("poznamky-prazdne").hidden = notes.length > 0;
  }

  function noteCard(note) {
    const card = el("article", "ncard");
    const head = el("div", "ncard__head");
    if (note.kind === "vlastni") {
      const title = el("input", "ncard__title");
      title.type = "text";
      title.placeholder = "Název poznámky";
      title.maxLength = 120;
      title.value = note.question;
      title.setAttribute("aria-label", "Název poznámky");
      title.addEventListener("input", () => updateNote(note.id, { question: title.value }));
      head.append(title);
    } else {
      head.append(el("div", "ncard__q", note.question));
    }
    head.append(el("span", "ncard__date", czDateTime(note.created)));
    card.append(head);

    if (note.answer) {
      const answer = el("p", "ncard__a", note.answer);
      card.append(answer);
      if (note.answer.length > NOTE_CLAMP) {
        answer.classList.add("ncard__a--clamp");
        const more = el("button", "link", "Celá odpověď");
        more.type = "button";
        more.setAttribute("aria-expanded", "false");
        more.addEventListener("click", () => {
          const open = answer.classList.toggle("ncard__a--clamp") === false;
          more.textContent = open ? "Sbalit" : "Celá odpověď";
          more.setAttribute("aria-expanded", String(open));
        });
        card.append(more);
      }
    }
    if (note.sources.length) {
      const chips = el("div", "ncard__srcs");
      note.sources.forEach((s) => {
        const chip = el("button", "ncard__src", `${s.n ? `[${s.n}] ` : ""}${s.doc_label} · ${s.label}`);
        chip.type = "button";
        chip.title = "Zobrazit v předpisu";
        chip.addEventListener("click", () => openDoc(s.doc_id, s.anchor, chip));
        chips.append(chip);
      });
      card.append(chips);
    }

    const text = el("textarea", "ncard__text");
    text.rows = 2;
    text.placeholder = note.kind === "vlastni" ? "Text poznámky…" : "Vaše poznámka k této odpovědi…";
    text.value = note.text;
    text.setAttribute("aria-label", "Text poznámky");
    text.addEventListener("input", () => updateNote(note.id, { text: text.value }));
    card.append(text);

    const row = el("div", "ncard__row");
    row.append(tool("download", "Stáhnout", () => download(`poznamka-${slug(note.question || "vlastni")}.txt`, noteText(note, true))));
    row.append(tool("copy", "Kopírovat", async () => toast((await copyText(noteText(note, false))) ? "Zkopírováno" : "Kopírování se nepovedlo")));
    if (note.question && note.kind === "odpoved") {
      row.append(tool("chat", "Zeptat se znovu", () => { input.value = note.question; autosize(); updateCounter(); input.focus(); closePanel(); }));
    }
    row.append(tool("trash", "Smazat", () => deleteNote(note.id)));
    const saved = el("span", "ncard__saved");
    saved.dataset.saved = note.id;
    row.append(saved);
    card.append(row);
    return card;
  }
  $("otevrit-poznamky").addEventListener("click", () => { openPanel("notes"); if (!wide.matches) closeSide(); });
  $("zavrit-poznamky").addEventListener("click", closePanel);
  $("poznamka-nova").addEventListener("click", addFreeNote);
  $("poznamky-stahnout").addEventListener("click", () => {
    if (!notes.length) { toast("Zatím nemáte žádné poznámky", "info"); return; }
    const parts = [`Moje poznámky – ${CFG.nazev}`, `Staženo: ${czDateTime(new Date().toISOString())}`, ""];
    notes.forEach((note, i) => parts.push(`${"─".repeat(40)}`, `${i + 1}) ${noteText(note, false)}`, ""));
    parts.push("Závazné je znění předpisu, ne odpověď chatbota.");
    download(`moje-poznamky-${today()}.txt`, parts.join("\n"));
  });

  // ------------------------------------------------------------------ vlákno
  const thread = $("vlakno");
  const scroller = $("rolovani");

  function scrollToBottom(smooth = true) {
    scroller.scrollTo({ top: scroller.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  }
  scroller.addEventListener("scroll", () => {
    const away = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
    $("dolu").hidden = away < 240;
  }, { passive: true });
  $("dolu").addEventListener("click", () => scrollToBottom());

  function questionNode(text) {
    const wrap = el("div", "msg msg--q");
    wrap.append(el("p", "q", text));
    const tools = el("div", "tools");
    tools.append(tool("edit", "Upravit a poslat znovu", () => { input.value = text; autosize(); updateCounter(); input.focus(); }));
    wrap.append(tools);
    return wrap;
  }

  function assistantNode(content) {
    const wrap = el("div", "msg msg--a");
    const body = el("div", "a");
    body.append(content);
    wrap.append(makeAvatar(), body);
    return wrap;
  }
  function makeAvatar() { return icon("logo", "avatar"); }

  function notice(text, kind, iconName) {
    const box = el("div", kind ? `notice notice--${kind}` : "notice");
    box.append(icon(iconName || "info"), el("p", "", text));
    return box;
  }

  function errorNode(message) {
    const node = assistantNode(notice(message, "error", "info"));
    node.setAttribute("role", "alert");
    return node;
  }

  /** Text odpovědi → odstavce a seznamy; značky [n] jsou tlačítka, která rozbalí zdroj. */
  function answerText(text, sourceCards, found) {
    const box = el("div", "a__text");
    const lines = String(text || "").split("\n");
    let list = null;
    let paragraph = null;
    const flush = () => { list = null; paragraph = null; };
    lines.forEach((raw) => {
      const line = raw.trim();
      if (!line) { flush(); return; }
      const bullet = /^[-•*]\s+(.*)$/.exec(line);
      const numbered = /^\d{1,2}[.)]\s+(.*)$/.exec(line);
      if (bullet || numbered) {
        const type = bullet ? "ul" : "ol";
        if (!list || list.tagName.toLowerCase() !== type) {
          list = el(type, `a__list a__list--${type}`);
          box.append(list);
          paragraph = null;
        }
        const item = el("li");
        inline(item, (bullet || numbered)[1], sourceCards, found);
        list.append(item);
        return;
      }
      if (!paragraph) {
        paragraph = el("p", "a__p");
        box.append(paragraph);
        list = null;
      } else {
        paragraph.append("\n");
      }
      inline(paragraph, line, sourceCards, found);
    });
    return box;
  }

  function inline(target, text, sourceCards, found) {
    let group = null;                                           // sousední značky [1][2] drží v jedné skupině
    text.split(/(\[\d+\])/).forEach((piece) => {
      const match = /^\[(\d+)\]$/.exec(piece);
      if (!match || !found) {
        if (piece) { target.append(piece); if (piece.trim()) group = null; }
        return;
      }
      const n = Number(match[1]);
      const mark = el("button", "cite", String(n));
      mark.type = "button";
      mark.setAttribute("aria-label", `Zdroj ${n}`);
      mark.title = `Zobrazit zdroj ${n}`;
      mark.addEventListener("click", () => revealSource(sourceCards.get(n)));
      if (!group) { group = el("span", "cites"); target.append(group); }
      group.append(mark);
    });
  }

  function revealSource(card) {
    if (!card) return;
    card.open = true;
    card.scrollIntoView({ behavior: "smooth", block: "center" });
    card.classList.add("src--flash");
    setTimeout(() => card.classList.remove("src--flash"), 1600);
    card.querySelector("summary")?.focus({ preventScroll: true });
  }

  /** Odpověď jako text ke zkopírování nebo stažení: dotaz, odpověď, zdroje. */
  function answerExport(question, body, withHeader) {
    const lines = [];
    if (withHeader) lines.push(TITLE, "");
    lines.push("Dotaz: " + question, "");
    lines.push("Odpověď:", body.answer || "");
    if (body.sources && body.sources.length) {
      lines.push("", "Zdroje:");
      body.sources.forEach((s) => lines.push(`[${s.n}] ${s.doc_label} – ${s.label}`, s.text, ""));
    }
    if (withHeader) {
      lines.push("Staženo: " + czDateTime(new Date().toISOString()) + (indexDate ? ` · stav předpisů k ${indexDate}` : ""));
      lines.push("Závazné je znění předpisu, ne odpověď chatbota.");
    }
    return lines.join("\n").replace(/\n{3,}/g, "\n\n");
  }

  function syncNoteButton(button) {
    const saved = Boolean(findNote(button.dataset.q, button.dataset.a));
    button.classList.toggle("is-on", saved);
    button.querySelector("span").textContent = saved ? "V poznámkách" : "Uložit do poznámek";
    button.dataset.tip = saved ? "Otevřít poznámky" : "Uložit do poznámek";
    button.setAttribute("aria-label", button.dataset.tip);
  }

  /** Odpověď: text se značkami [n], úzké řádky zdrojů, „mohlo by souviset“ a nástroje. */
  function answerNode(body, question) {
    const content = el("div");
    const sourceCards = new Map();                              // číslo značky → řádek zdroje
    const blocked = Boolean(body.blocked);
    // bez modelu (zkušební režim nebo výpadek) se odpověď nesestavuje – ukážou se jen nalezená místa
    const demo = (body.mode === "zkusebni" || body.mode === "bez-modelu") && !body.found && !blocked;

    if (body.found) {
      content.append(answerText(body.answer, sourceCards, true));
    } else if (blocked) {
      content.append(notice(body.answer, "", "chat"));          // nerozumím / mimo účel: bez zdrojů
    } else if (demo) {
      content.append(notice(body.answer, "", "info"));
    } else {
      content.append(notice(body.answer, "warn", "info"));      // nenašel jsem
    }

    if (body.found && body.sources && body.sources.length) {
      const box = el("section", "sources");
      box.append(sectionHead("Zdroje", "Odstavce předpisů, ze kterých odpověď vychází. Kliknutím na řádek zobrazíte text, závazné je znění předpisu."));
      body.sources.forEach((source) => {
        const row = sourceRow(source, true);
        sourceCards.set(source.n, row);
        box.append(row);
      });
      content.append(box);
    }

    const related = (!blocked && body.related) ? body.related : [];
    if (related.length) {
      if (demo) {
        const box = el("section", "sources");
        box.append(sectionHead("Nalezená místa", "Místa, která hledání našlo jako nejbližší. Bez modelu se odpověď nesestavuje."));
        related.forEach((source) => box.append(sourceRow(source, false)));
        content.append(box);
      } else {
        const details = el("details", "related");
        const summary = el("summary");
        summary.append(icon("chevron"), el("span", "", `Mohlo by souviset (${related.length})`));
        details.append(summary);
        details.append(el("p", "related__hint", body.found
          ? "Další místa, která hledání našlo. Nemusí se dotazu týkat."
          : "Nejbližší místa, která hledání našlo. Na dotaz nemusí odpovídat – zkuste ho upřesnit."));
        const list = el("div", "related__list");
        related.forEach((source) => list.append(sourceRow(source, false)));
        details.append(list);
        content.append(details);
      }
    }

    if (body.found) {
      const tools = el("div", "tools");
      tools.append(tool("copy", "Kopírovat", async () => {
        toast((await copyText(answerExport(question, body, false))) ? "Odpověď zkopírována" : "Kopírování se nepovedlo");
      }));
      tools.append(tool("download", "Stáhnout", () => download(`odpoved-${slug(question)}.txt`, answerExport(question, body, true))));
      const save = tool("bookmark", "Uložit do poznámek", () => {
        if (findNote(question, body.answer)) { openPanel("notes"); return; }
        addAnswerNote(question, body);
        syncNoteButton(save);
        toast("Uloženo do poznámek");
      }, "tool--note");
      save.dataset.q = question;
      save.dataset.a = body.answer || "";
      syncNoteButton(save);
      tools.append(save);
      content.append(tools);
    }
    if (!blocked && !demo) content.append(rateRow(question, body));
    return assistantNode(content);
  }

  // ------------------------------------------------------------------ hodnocení odpovědi (palce)
  // Palec nahoru / dolů jde na server (/api/feedback) i s dotazem, odpovědí a citovanými úseky – bez jména
  // a adresy. Správce chatbota podle toho ladí zadání pro model a zkušební otázky. Co kdo hodnotil, si stránka pamatuje
  // v prohlížeči, aby se palec ukázal zvolený i po návratu z historie.
  const REASONS_FOUND = [["spatny-predpis", "Odkazuje na špatný předpis"], ["neuplne", "Odpověď je neúplná"],
                         ["nesrozumitelne", "Je nesrozumitelná"], ["jine", "Něco jiného"]];
  const REASONS_NOT_FOUND = [["melo-se-najit", "V předpisech to je, mělo se to najít"], ["jine", "Něco jiného"]];
  let votes = store.get("hlasy", {});
  if (!votes || typeof votes !== "object") votes = {};

  async function sendFeedback(payload) {
    try {
      const response = await fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Client-Id": CLIENT_ID },
        body: JSON.stringify(payload),
      });
      const body = await response.json().catch(() => ({}));
      lockedOut(response);
      return response.ok ? null : (body.detail || "Hodnocení se nepodařilo odeslat.");
    } catch {
      return "Hodnocení se nepodařilo odeslat. Zkontrolujte připojení.";
    }
  }

  function rateButton(iconName, label) {
    const button = el("button", "rate__btn");
    button.type = "button";
    button.setAttribute("aria-label", label);
    button.dataset.tip = label;
    button.append(icon(iconName));
    return button;
  }

  /** Výběr důvodu: jeden čip zvolený, druhé kliknutí ho zruší. Vrací prvek a funkci, která vrátí zvolený kód. */
  function reasonChips(reasons) {
    const box = el("div", "rate__reasons");
    let chosen = "";
    reasons.forEach(([code, label]) => {
      const chip = el("button", "rchip", label);
      chip.type = "button";
      chip.setAttribute("aria-pressed", "false");
      chip.addEventListener("click", () => {
        chosen = chosen === code ? "" : code;
        box.querySelectorAll(".rchip").forEach((c) => {
          const on = c === chip && Boolean(chosen);
          c.classList.toggle("is-on", on);
          c.setAttribute("aria-pressed", String(on));
        });
      });
      box.append(chip);
    });
    return { box, chosen: () => chosen };
  }

  /** Formulář k palci dolů: důvod, poznámka, odeslat / zrušit. `onSend(reason, comment)` vrací true, když se uložilo. */
  function downForm(reasons, onSend, onCancel) {
    const form = el("div", "rate__form");
    form.append(el("p", "rate__q", "Co bylo špatně?"));
    const chips = reasonChips(reasons);
    form.append(chips.box);
    const text = el("textarea", "rate__text");
    text.rows = 2;
    text.maxLength = 300;
    text.placeholder = "Doplňte, co by vám pomohlo (nepovinné)…";
    text.setAttribute("aria-label", "Poznámka k hodnocení");
    form.append(text);
    const actions = el("div", "rate__row");
    const send = el("button", "btn", "Odeslat");
    send.type = "button";
    send.addEventListener("click", async () => {
      send.disabled = true;
      if (!(await onSend(chips.chosen(), text.value.trim()))) send.disabled = false;
    });
    const cancel = el("button", "btn btn--ghost", "Zrušit");
    cancel.type = "button";
    cancel.addEventListener("click", onCancel);
    actions.append(send, cancel, el("span", "rate__note", "Odešle se dotaz, odpověď a hodnocení – bez vašeho jména."));
    form.append(actions);
    return form;
  }

  function rateRow(question, body) {
    const row = el("div", "rate");
    const key = noteKey(question, body.answer || "");
    const up = rateButton("thumb-up", "Pomohla");
    const down = rateButton("thumb-down", "Nepomohla");
    const done = el("span", "rate__done", "Děkujeme za zpětnou vazbu.");
    row.append(el("span", "rate__label", "Pomohla vám odpověď?"), up, down, done);

    const reflect = () => {
      const vote = votes[key];
      up.classList.toggle("is-on", vote === "up");
      down.classList.toggle("is-on", vote === "down");
      down.classList.toggle("is-on--down", vote === "down");
      up.setAttribute("aria-pressed", String(vote === "up"));
      down.setAttribute("aria-pressed", String(vote === "down"));
      done.hidden = !vote;
    };
    reflect();

    const record = async (vote, reason, comment) => {
      const problem = await sendFeedback({
        vote, reason: reason || "", comment: comment || "", question, answer: body.answer || "", found: Boolean(body.found),
        sources: (body.sources || []).map((s) => `${s.doc_id}:${s.anchor}`),
      });
      if (problem) { toast(problem, "info"); return false; }
      votes[key] = vote;
      store.set("hlasy", votes);
      reflect();
      toast("Děkujeme za zpětnou vazbu");
      return true;
    };

    let form = null;
    const closeForm = () => { form?.remove(); form = null; };
    up.addEventListener("click", () => { closeForm(); if (votes[key] !== "up") record("up"); });
    down.addEventListener("click", () => {
      if (form) { closeForm(); return; }
      form = downForm(body.found ? REASONS_FOUND : REASONS_NOT_FOUND,
                      async (reason, comment) => { const ok = await record("down", reason, comment); if (ok) closeForm(); return ok; },
                      closeForm);
      row.append(form);
      form.querySelector(".rchip")?.focus();
    });
    return row;
  }

  /** Nadpis oddílu s vysvětlivkou, která se ukáže po najetí myší nebo zaměření klávesnicí. */
  function sectionHead(title, tip) {
    const head = el("h3", "sources__head", title);
    const info = el("span", "info tip-left", "i");
    info.dataset.tip = tip;
    info.tabIndex = 0;
    info.setAttribute("role", "note");
    info.setAttribute("aria-label", tip);
    head.append(info);
    return head;
  }

  /** Úzký řádek zdroje: číslo, předpis, místo. Text a tlačítka až po rozkliknutí. */
  function sourceRow(source, numbered) {
    const row = el("details", numbered ? "src" : "src src--thin");
    const summary = el("summary");
    if (numbered && source.n) summary.append(el("span", "src__n", String(source.n)));
    const names = el("span", "src__names");
    names.append(el("span", "src__doc", source.doc_label), el("span", "src__where", source.label));
    summary.append(names);
    const chevron = el("span", "src__chev");
    chevron.append(icon("chevron"));
    summary.append(chevron);
    row.append(summary);

    const body = el("div", "src__body");
    body.append(el("p", "src__text", source.text));
    const actions = el("div", "src__row");
    const open = el("button", "link");
    open.type = "button";
    open.append(icon("doc"), el("span", "", "Zobrazit v předpisu"));
    open.addEventListener("click", () => openDoc(source.doc_id, source.anchor, open));
    actions.append(open);
    actions.append(tool("copy", "Kopírovat text", async () => {
      toast((await copyText(`${source.doc_label} – ${source.label}\n${source.text}`)) ? "Zkopírováno" : "Kopírování se nepovedlo");
    }));
    const original = externalLink(source.url, "Originál");
    if (original) actions.append(original);
    if (source.note) {
      // upozornění k dokumentu (například že ho změnila novela): značka s vysvětlením po najetí myší
      const badge = el("span", "badge tip-left", "pozor");
      badge.dataset.tip = source.note;
      badge.tabIndex = 0;
      badge.setAttribute("role", "note");
      badge.setAttribute("aria-label", source.note);
      actions.append(badge);
    }
    body.append(actions);
    row.append(body);
    return row;
  }

  // ------------------------------------------------------------------ dotaz
  const form = $("formular");
  const input = $("otazka");
  const sendBtn = $("odeslat");
  const counter = $("pocet");
  let busy = false;
  let controller = null;

  function autosize() {
    input.style.height = "auto";
    // Prázdné pole: výšku určí nápověda (placeholder), která se na užší obrazovce zalomí na víc řádků –
    // prohlížeč ji do výšky pole sám nepočítá, proto se na okamžik změří jako text.
    const empty = !input.value;
    if (empty) input.value = input.placeholder;
    const height = input.scrollHeight;
    if (empty) input.value = "";
    input.style.height = `${Math.min(height, 200)}px`;
  }
  window.addEventListener("resize", autosize);
  function updateCounter() {
    const length = input.value.length;
    counter.hidden = length < MAX_CHARS * 0.8;                  // počítadlo se ukáže až u konce limitu
    counter.textContent = `${length} / ${MAX_CHARS}`;
    counter.classList.toggle("ask__count--over", length >= MAX_CHARS);
    sendBtn.disabled = !busy && !input.value.trim();
  }
  function setBusy(value) {
    busy = value;
    sendBtn.classList.toggle("is-busy", value);
    sendBtn.setAttribute("aria-label", value ? "Zastavit" : "Odeslat dotaz");
    sendBtn.dataset.tip = value ? "Zastavit" : "Odeslat (Enter)";
    updateCounter();
  }
  input.addEventListener("input", () => { autosize(); updateCounter(); });
  input.addEventListener("keydown", (event) => {
    // Enter odešle, Shift+Enter udělá nový řádek
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      submit();
    }
  });
  function submit() {
    if (typeof form.requestSubmit === "function") form.requestSubmit();
    else form.dispatchEvent(new Event("submit", { cancelable: true }));
  }

  // ------------------------------------------------------------------ návrhy otázek
  // Otázky sepsané správcem (otazky.json ← chatbot.toml, oddíl [otazky]). Stránka z nich náhodně
  // nabídne pár kusů – při načtení, po každé odpovědi a po kliknutí na „jiné otázky“.
  // Záměrně to nejsou dotazy jiných uživatelů: ty by se daly zneužít.
  let palette = [];
  function showSuggestions() {
    const list = $("priklady");
    const pool = palette.slice();
    list.replaceChildren();
    for (let i = 0; i < SUGGESTED && pool.length; i += 1) {
      const [question] = pool.splice(Math.floor(Math.random() * pool.length), 1);    // bez opakování
      const item = el("li");
      const chip = el("button", "chip", question);
      chip.type = "button";
      item.append(chip);
      list.append(item);
    }
    $("navrhy").hidden = list.children.length === 0;
  }
  async function loadPalette() {
    try {
      const data = await (await fetch("otazky.json")).json();
      if (Array.isArray(data)) palette = data.filter((q) => typeof q === "string");
    } catch { /* bez palety se návrhy prostě neukážou */ }
    showSuggestions();
  }
  $("jine").addEventListener("click", showSuggestions);
  $("priklady").addEventListener("click", (event) => {
    const chip = event.target.closest(".chip");
    if (!chip) return;
    input.value = chip.textContent;
    autosize();
    updateCounter();
    submit();
  });

  // čekání: text se po chvíli mění, aby bylo vidět, že se něco děje
  const WAIT_STAGES = [[0, "Hledám v předpisech…"], [2200, "Čtu nalezené odstavce…"], [5000, "Sestavuji odpověď…"], [10000, "Ještě chvilku…"]];
  function waitingNode() {
    const box = el("div", "wait");
    const dots = el("span", "dots");
    dots.append(el("i"), el("i"), el("i"));
    const label = el("span", "", WAIT_STAGES[0][1]);
    box.append(dots, label);
    const timers = WAIT_STAGES.slice(1).map(([ms, text]) => setTimeout(() => { label.textContent = text; }, ms));
    const node = assistantNode(box);
    node.stopTimers = () => timers.forEach(clearTimeout);
    return node;
  }

  // ------------------------------------------------------------------ varování: osobní údaj v dotazu
  // Před odesláním se dotaz projde pravidly z osobni-udaje.js (jen v prohlížeči). Při nálezu se zobrazí
  // varování: „Zpět k úpravě“ nechá dotaz v poli, „Přesto odeslat“ ho pošle. Bez nálezu se nic neptá.
  const warning = $("varovani");
  let warningAnswer = null;             // funkce, která rozhodne čekající varování (true = odeslat)
  function settleWarning(send) {
    if (warning.open) warning.close();
    const answer = warningAnswer;
    warningAnswer = null;
    answer?.(send);
  }
  $("varovani-zpet").addEventListener("click", () => settleWarning(false));
  $("varovani-odeslat").addEventListener("click", () => settleWarning(true));
  // Esc = zpět. Obslouží se už „cancel“ (přichází hned po stisku); „close“ je pojistka pro jiné zavření.
  warning.addEventListener("cancel", (event) => { event.preventDefault(); settleWarning(false); });
  warning.addEventListener("close", () => settleWarning(false));
  /** Vrátí true, když se dotaz smí odeslat (nic podezřelého, nebo to uživatel potvrdil). */
  function personalDataCheck(question) {
    let kinds = [];
    try { kinds = window.osobniUdaje ? window.osobniUdaje.najdi(question) : []; } catch { kinds = []; }
    if (!kinds.length || typeof warning.showModal !== "function") return Promise.resolve(true);
    $("varovani-text").textContent = `Zdá se, že dotaz může obsahovat osobní nebo citlivý údaj (${kinds.join(", ")}). `
      + "Zkontrolujte ho prosím, než ho odešlete.";
    return new Promise((resolve) => {
      warningAnswer = resolve;
      warning.showModal();
      $("varovani-zpet").focus();
    });
  }

  let checking = false;                 // čeká se na rozhodnutí u varování
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) { controller?.abort(); return; }                  // tlačítko během čekání = zastavit
    const question = input.value.trim();
    if (!question || checking) return;
    checking = true;
    const send = await personalDataCheck(question);
    checking = false;
    if (!send) { input.focus(); return; }                       // zpět: dotaz zůstal v poli k úpravě
    setBusy(true);
    $("uvod").hidden = true;

    thread.append(questionNode(question));
    const waiting = waitingNode();
    thread.append(waiting);
    scrollToBottom();
    input.value = "";
    autosize();
    updateCounter();

    controller = new AbortController();
    let card;
    try {
      const response = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Client-Id": CLIENT_ID },
        body: JSON.stringify({ question }),
        signal: controller.signal,
      });
      const body = await response.json().catch(() => ({}));
      if (response.ok) {
        card = answerNode(body, question);
        remember(question, body);
      } else if (lockedOut(response)) {
        // přihlášení vypršelo: dotaz vrátit do pole, po přihlášení ho stačí odeslat znovu
        card = assistantNode(notice("Přihlášení vypršelo. Po zadání hesla dotaz odešlete znovu.", "", "info"));
        input.value = question;
        autosize();
      } else {
        card = errorNode(body.detail || "Něco se nepovedlo, zkuste to prosím znovu.");
      }
    } catch (error) {
      card = error && error.name === "AbortError"
        ? assistantNode(notice("Hledání jste zastavili. Dotaz můžete upravit a poslat znovu.", "", "info"))
        : errorNode("Spojení se nepodařilo. Zkontrolujte připojení a zkuste to znovu.");
    }
    waiting.stopTimers();
    waiting.replaceWith(card);
    showSuggestions();                                          // po každé odpovědi jiné návrhy
    setBusy(false);
    controller = null;
    card.scrollIntoView({ behavior: "smooth", block: "nearest" });
    input.focus();
  });

  // ------------------------------------------------------------------ panel vpravo (znění / poznámky)
  // Panel vyjede zprava vedle vlákna – vlákno zůstává vidět. Aby na něj bylo místo, schová se při tom
  // postranní panel vlevo (a vrátí se, až panel vpravo zmizí). Panel jde zasunout (obsah zůstane, vrátí ho
  // tlačítko v horní liště), nebo zavřít křížkem.
  const app = $("app");
  const panels = { doc: $("predpis"), notes: $("poznamky") };
  const PANEL_NAMES = { doc: "Znění předpisu", notes: "Moje poznámky" };
  const calm = window.matchMedia("(prefers-reduced-motion: reduce)");
  let activePanel = null;               // "doc" | "notes" | null – který panel je otevřený (i zasunutý)
  let tucked = false;                   // panel je zasunutý vpravo
  let panelShown = false;               // panel je právě vidět
  let panelSeq = 0;                     // pořadí otevření: doběhlá animace zasunutí nesmí schovat nově otevřený panel
  let returnFocus = null;               // kam se vrátí kurzor po zavření

  /** Promítne stav (activePanel, tucked) do stránky: viditelnost panelů, rozvržení, tlačítko v liště, levý panel. */
  function reflectPanel() {
    const shown = Boolean(activePanel) && !tucked;
    Object.entries(panels).forEach(([key, node]) => {
      node.classList.remove("panel--leaving");
      node.hidden = !(shown && key === activePanel);
    });
    app.classList.toggle("app--panel", shown);
    document.body.classList.toggle("panel-open", shown);
    $("vysunout").hidden = !(activePanel && tucked);
    if (activePanel) {
      $("vysunout-text").textContent = PANEL_NAMES[activePanel];
      $("vysunout").setAttribute("aria-label", `Vysunout: ${PANEL_NAMES[activePanel]}`);
    }
    // levý panel: schovat jen ve chvíli, kdy panel vpravo vyjíždí; když ho pak uživatel sám otevře, nechat ho být
    if (shown && !panelShown && wide.matches && !document.body.classList.contains("side-closed")) {
      document.body.classList.add("side-closed");
      sideAuto = true;
    } else if (!shown && sideAuto) {
      document.body.classList.remove("side-closed");
      sideAuto = false;
    }
    panelShown = shown;
  }

  /** Nechá viditelný panel odjet doprava a pak zavolá `done` (bez animace hned). */
  function slideOut(done) {
    const node = panelShown ? panels[activePanel] : null;
    if (!node || calm.matches) { done(); return; }
    const seq = panelSeq;
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      node.classList.remove("panel--leaving");
      if (seq === panelSeq) done();                             // mezitím se neotevřelo nic jiného
    };
    node.classList.add("panel--leaving");
    node.addEventListener("animationend", finish, { once: true });
    setTimeout(finish, 320);                                    // pojistka, kdyby událost nepřišla
  }

  function openPanel(name) {
    panelSeq += 1;
    activePanel = name;
    tucked = false;
    reflectPanel();
    if (name === "notes") renderNotes();
  }
  /** Zasune panel doprava; obsah zůstane a tlačítko v liště ho vrátí. */
  function tuckPanel() {
    if (!panelShown) return;
    slideOut(() => {
      tucked = true;
      reflectPanel();
      $("vysunout").focus({ preventScroll: true });
    });
  }
  function untuckPanel() {
    if (!activePanel) return;
    openPanel(activePanel);
    if (activePanel === "doc") highlight(currentAnchor);        // znovu najet na citovaný odstavec
    panels[activePanel].focus({ preventScroll: true });
  }
  function closePanel() {
    if (!activePanel) return;
    slideOut(() => {
      activePanel = null;
      tucked = false;
      reflectPanel();
      if (returnFocus && document.contains(returnFocus)) returnFocus.focus();
      returnFocus = null;
    });
  }
  $("zavrit").addEventListener("click", closePanel);
  $("zasunout").addEventListener("click", tuckPanel);
  $("zasunout-poznamky").addEventListener("click", tuckPanel);
  $("vysunout").addEventListener("click", untuckPanel);

  // ------------------------------------------------------------------ znění předpisu
  const docCache = new Map();           // číslo dokumentu → odpověď /api/doc
  let currentDoc = null;
  let currentAnchor = "";

  async function openDoc(docId, anchor, trigger) {
    returnFocus = trigger || null;
    let doc = docCache.get(docId);
    if (!doc) {
      try {
        const response = await fetch(`/api/doc/${encodeURIComponent(docId)}`);
        if (lockedOut(response)) return;
        if (!response.ok) throw new Error("doc");
        doc = await response.json();
        docCache.set(docId, doc);
      } catch {
        toast("Znění se nepodařilo načíst", "info");
        return;
      }
    }
    currentDoc = doc;
    currentAnchor = anchor || "";
    renderDoc(doc);
    openPanel("doc");
    highlight(anchor);
    $("zavrit").focus({ preventScroll: true });
  }

  /** Zvýrazní blok s kotvou zdroje. Dělený úsek má kotvu „b12-2“ – blok je „b12“. */
  function highlight(anchor) {
    panels.doc.querySelectorAll(".d-hit").forEach((node) => node.classList.remove("d-hit"));
    const target = document.getElementById(`blk-${String(anchor || "").split("-")[0]}`);
    if (!target) return;
    target.classList.add("d-hit");
    // Roluje se jen tělo panelu. scrollIntoView by během vyjíždění (panel je ještě posunutý mimo obrazovku)
    // odroloval do strany i celou stránku a vlákno by zmizelo.
    const box = $("predpis-text");
    const top = target.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop;
    box.scrollTop = Math.max(0, top - Math.max(0, (box.clientHeight - target.offsetHeight) / 2));
  }

  function renderDoc(doc) {
    $("predpis-nazev").textContent = doc.label;

    const meta = $("predpis-udaje");
    meta.replaceChildren();
    if (doc.meta.ucinnost_od) {
      const pair = el("div");
      pair.append(el("dt", "", "Účinnost od:"), el("dd", "", czDate(doc.meta.ucinnost_od)));
      meta.append(pair);
    }
    const original = externalLink(doc.meta.zdroj, "Otevřít originál");
    if (original) {
      const pair = el("div");
      const value = el("dd");
      value.append(original);
      pair.append(el("dt", "", "Zdroj:"), value);
      meta.append(pair);
    }

    const notes = $("predpis-poznamky");
    notes.replaceChildren();
    if (doc.meta.poznamka) notes.append(el("p", "note", doc.meta.poznamka));
    if (doc.flags.includes("prepis")) {
      notes.append(el("p", "note", "Zobrazený text je přepis originálu. Závazný je originál."));
    }
    if (["obrazky", "popisky", "priloha-bez-textu"].some((flag) => doc.flags.includes(flag))) {
      notes.append(el("p", "note", "Předpis obsahuje obrázky nebo mapy, které tu nejsou vidět. Najdete je v originálu."));
    }

    const body = $("predpis-text");
    body.replaceChildren();
    doc.blocks.forEach((block) => {
      let node;
      switch (block.kind) {
        case "title": node = el("h3", "d-title", block.text); break;
        case "priloha": node = el("h3", "d-priloha", block.text); break;
        case "part": node = el("h4", "d-part", block.text); break;
        case "article":
          node = el("h4", "d-article");
          if (block.number) node.append(el("span", "d-article__n", articleMark(block.number)));
          node.append(block.text);
          break;
        case "para":
          // číslo odstavce je to z dokumentu (stejné je v citaci); odstavec jen s písmeny číslo nemá
          node = el("div", block.number ? "d-para" : "d-para d-para--plain");
          node.append(el("span", "d-para__n", block.number ? `(${block.number})` : ""), paragraphContent(block));
          break;
        case "table": node = el("div", "d-table"); node.append(renderTable(block.rows)); break;
        case "note": notes.append(el("p", "note", block.text)); return;      // upozornění patří nahoru, ne do textu
        default: node = el("p", "d-text", block.text);
      }
      node.id = `blk-${block.anchor}`;
      body.append(node);
    });
    body.scrollTop = 0;
  }

  function paragraphContent(block) {
    const content = el("div");
    content.append(el("div", "", block.text));
    if (block.items.length) content.append(renderItems(block.items));
    if (block.rows.length) content.append(renderTable(block.rows));
    return content;
  }

  /** Pododstavce se značkou z originálu („a)“, „1.“, „–“); položka bez značky je pokračování textu. */
  function renderItems(items) {
    const list = el("ol", "d-sub");
    items.forEach((item) => {
      const row = el("li");
      const content = el("div");
      content.append(el("div", "", item.text));
      if (item.children.length) content.append(renderItems(item.children));
      row.append(el("span", "d-sub__m", item.marker || ""), content);
      list.append(row);
    });
    return list;
  }

  /** Označení článku: „Čl. 3“, u paragrafu „§ 3“. */
  function articleMark(number) {
    return String(number).startsWith("§") ? String(number) : `Čl. ${number}`;
  }

  function renderTable(rows) {
    const wrap = el("div", "d-wrap");
    const table = el("table", "d-grid");
    rows.forEach((cells, rowIndex) => {
      const tr = el("tr");
      cells.forEach((cell) => tr.append(el(rowIndex === 0 && rows.length > 1 ? "th" : "td", "", cell)));
      table.append(tr);
    });
    wrap.append(table);
    return wrap;
  }

  /** Znění předpisu jako prostý text (ke stažení): stejná struktura jako na obrazovce. */
  function docText(doc) {
    const lines = [doc.label, ""];
    if (doc.meta.ucinnost_od) lines.push(`Účinnost od: ${czDate(doc.meta.ucinnost_od)}`);
    if (doc.meta.zdroj) lines.push(`Originál: ${doc.meta.zdroj}`);
    if (doc.meta.poznamka) lines.push(`Upozornění: ${doc.meta.poznamka}`);
    lines.push("");
    const items = (list, level) => list.forEach((item) => {
      lines.push(`${"    ".repeat(level + 1)}${item.marker ? item.marker + " " : ""}${item.text}`);
      if (item.children.length) items(item.children, level + 1);
    });
    const table = (rows) => rows.forEach((cells) => lines.push("    " + cells.join(" | ")));
    doc.blocks.forEach((block) => {
      switch (block.kind) {
        case "title": case "priloha": lines.push("", block.text.toUpperCase(), ""); break;
        case "part": lines.push("", block.text, ""); break;
        case "article": lines.push("", `${block.number ? articleMark(block.number) + " " : ""}${block.text}`); break;
        case "para":
          lines.push(`${block.number ? `(${block.number}) ` : ""}${block.text}`);
          if (block.items.length) items(block.items, 0);
          if (block.rows.length) table(block.rows);
          break;
        case "table": table(block.rows); break;
        case "note": lines.push(`[Upozornění: ${block.text}]`); break;
        default: lines.push(block.text);
      }
    });
    lines.push("", `Staženo z chatbota ${CFG.nazev} ${czDateTime(new Date().toISOString())}` + (indexDate ? ` · stav k ${indexDate}` : ""),
               "Závazné je znění předpisu v originálu, ne tento výpis.");
    return lines.join("\n");
  }
  $("predpis-stahnout").addEventListener("click", () => {
    if (currentDoc) download(`predpis-${slug(currentDoc.label, 60)}.txt`, docText(currentDoc));
  });
  $("predpis-tisk").addEventListener("click", () => window.print());
  $("predpis-kopirovat-odkaz").addEventListener("click", async () => {
    if (!currentDoc) return;
    const link = `${window.location.origin}${window.location.pathname}#p=${encodeURIComponent(currentDoc.id)}`
      + (currentAnchor ? `&b=${encodeURIComponent(currentAnchor)}` : "");
    toast((await copyText(link)) ? "Odkaz zkopírován" : "Kopírování se nepovedlo", "link");
  });

  /** Odkaz na předpis v adrese (#p=1001&b=b12, z tlačítka „Kopírovat odkaz“) otevře znění rovnou po načtení.
   *  Kotva se z adresy hned odstraní, aby obnovení stránky znění neotvíralo znovu. */
  function openFromHash() {
    if (!window.location.hash) return;
    const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    history.replaceState(null, "", window.location.pathname + window.location.search);
    const id = Number(params.get("p"));
    if (Number.isInteger(id) && id > 0) openDoc(id, params.get("b") || "", null);
  }

  // ------------------------------------------------------------------ klávesy
  document.addEventListener("keydown", (event) => {
    if (!$("zamek").hidden) return;                            // pod přihlášením zkratky neplatí
    if (event.key === "Escape") {
      if (warning.open) return;                                 // Esc tu patří varování (= zpět k úpravě)
      if (panelShown) { closePanel(); return; }
      if (document.body.classList.contains("side-open")) closeSide();
      return;
    }
    const typing = /^(input|textarea)$/i.test(document.activeElement?.tagName || "");
    if (event.key === "/" && !typing && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      input.focus();
    }
  });

  // ------------------------------------------------------------------ texty z nastavení
  /** Dosadí do stránky název, organizaci a texty z nastaveni.js. Prázdný údaj svůj prvek schová. */
  function applySettings() {
    document.title = TITLE;
    document.querySelectorAll("[data-nastaveni]").forEach((node) => {
      const value = String(CFG[node.dataset.nastaveni] || "").trim();
      node.textContent = value;
      if (node.dataset.skryt === "prazdne") node.hidden = !value;
    });
    const links = $("odkazy");
    const where = externalLink(CFG.odkaz, "Kde jsou předpisy zveřejněné");
    if (where) links.append(where);
    if (CFG.kontakt) links.append(el("span", "side__contact", `Nevíte si rady? ${CFG.kontakt}`));
    const howto = externalLink(CFG.navod, "Chcete takového chatbota? Návod");
    if (howto) links.append(howto);
    links.hidden = links.children.length === 0;
  }

  // ------------------------------------------------------------------ start
  applySettings();
  renderHistory();
  saveNotes();
  renderNotes();
  loadStatus();
  loadPalette();
  autosize();
  updateCounter();
  openFromHash();
})();
