"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const icons = {
    grid: ["M3 3h7v7H3z", "M14 3h7v7h-7z", "M3 14h7v7H3z", "M14 14h7v7h-7z"],
    skill: ["m12 3 8 4.5v9L12 21l-8-4.5v-9Z", "m4 7.5 8 4.5 8-4.5", "M12 12v9", "m8 5 8 4.5"],
    agent: ["M5 8h14v12H5z", "M12 8V4", "M10 4h4", "M8 12v2", "M16 12v2", "M9 17h6", "M2 11v6", "M22 11v6"],
    learn: ["M12 21v-8", "M12 15C5 15 3 11 3 5c6 0 9 3 9 8", "M12 12c0-6 4-9 9-9 0 6-3 10-9 10"],
    activity: ["M3 12h4l3-8 4 16 3-8h4"],
    folder: ["M3 6h6l2 2h10v12H3z", "M3 6V4h7l2 2h8v2"],
    search: ["M20 20l-5-5", "M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0"],
    arrow: ["M5 12h14", "m14 7 5 5-5 5"],
    check: ["m5 12 4 4L19 6"],
    clock: ["M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0", "M12 7v5l3 2"],
    plan: ["M5 4h14v17H5z", "M9 2v4", "M15 2v4", "M8 11h8", "M8 15h5"],
    code: ["m8 7-5 5 5 5", "m16 7 5 5-5 5", "m14 4-4 16"],
    review: ["M20 11a8 8 0 1 1-5-7", "m10 10 3 3 8-9"],
    memory: ["M5 3h14v18H5z", "M8 7h8", "M8 11h8", "M8 15h5"],
    debug: ["M8 8h8v9a4 4 0 0 1-8 0Z", "M10 8V5h4v3", "M4 11h4", "M16 11h4", "M4 17h4", "M16 17h4", "m6 4 3 4", "m18 4-3 4"],
    workflow: ["M3 3h6v6H3z", "M15 15h6v6h-6z", "M15 3h6v6h-6z", "M9 6h6", "M18 9v6"],
  };
  const views = {
    overview: {name: "Resumen", title: "Una vista de conjunto.", description: "Explora tus capacidades y sigue cómo aprende Nexo.", icon: "grid"},
    skills: {name: "Skills", title: "Capacidades que te acompañan.", description: "Consulta las instrucciones y el origen de cada skill disponible.", icon: "skill"},
    agents: {name: "Agentes", title: "Un equipo con cada rol claro.", description: "Explora las instrucciones de tus agentes y su uso observado.", icon: "agent"},
    learning: {name: "Aprendizaje", title: "La experiencia se convierte en método.", description: "Sigue las repeticiones, candidatas y validaciones de cada proyecto.", icon: "learn"},
    activity: {name: "Actividad", title: "El pulso de tu espacio.", description: "Actividad observada y uso declarado, con su proyecto, estado y fuente.", icon: "activity"},
  };
  const model = {data: null, view: "overview", project: "", query: "", skillFilter: "all", activityFilter: "all", paused: false, pending: false, online: false, lastRefresh: null, error: "", detail: null};
  const fragment = new URLSearchParams(location.hash.slice(1));
  let token = fragment.get("token") || "";
  try { token ||= sessionStorage.getItem("nexo-dashboard-token") || ""; if (token) sessionStorage.setItem("nexo-dashboard-token", token); } catch (_) { /* The URL token also works without browser storage. */ }
  if (fragment.has("token")) history.replaceState(null, "", location.pathname + location.search);

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2).toLowerCase(), value);
      else if (key === "text") node.textContent = value;
      else node.setAttribute(key, value === true ? "" : String(value));
    }
    for (const child of children.flat(Infinity)) {
      if (child == null || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }
  function icon(name) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    for (const [key, value] of Object.entries({viewBox:"0 0 24 24", fill:"none", stroke:"currentColor", "stroke-width":"1.5", "stroke-linecap":"round", "stroke-linejoin":"round", "aria-hidden":"true"})) node.setAttribute(key, value);
    for (const d of icons[name] || icons.skill) { const path = document.createElementNS(node.namespaceURI, "path"); path.setAttribute("d", d); node.append(path); }
    return node;
  }
  const array = (value) => Array.isArray(value) ? value : [];
  const str = (value) => value == null ? "" : String(value);
  const numeric = (value) => Number.isFinite(Number(value)) ? Number(value) : 0;
  const matches = (...values) => !model.query || values.map(str).join(" ").toLocaleLowerCase("es").includes(model.query.toLocaleLowerCase("es"));
  function timestamp(value) { const number = Number(value); return Number.isFinite(number) && value != null && value !== "" ? (number < 1e12 ? number * 1000 : number) : Date.parse(value); }
  function relativeTime(value) {
    const time = timestamp(value);
    if (!Number.isFinite(time)) return "Sin fecha";
    const seconds = Math.max(0, (Date.now() - time) / 1000);
    if (seconds < 60) return "Ahora";
    if (seconds < 3600) return `Hace ${Math.floor(seconds / 60)} min`;
    if (seconds < 86400) return `Hace ${Math.floor(seconds / 3600)} h`;
    return new Intl.DateTimeFormat("es", {day:"numeric", month:"short"}).format(time);
  }
  function absoluteTime(value) { const time = timestamp(value); return Number.isFinite(time) ? new Intl.DateTimeFormat("es", {dateStyle:"medium", timeStyle:"medium"}).format(time) : "Sin fecha registrada"; }
  function projects() { return array(model.data?.projects).filter((p) => !model.project || p.id === model.project); }
  function projectName(id) { return array(model.data?.projects).find((p) => p.id === id || p.path === id)?.name || str(id).split(/[\\/]/).filter(Boolean).pop() || "Sin proyecto"; }
  function skills(filter = true) { return array(model.data?.skills).filter((s) => (!model.project || !s.project_id || s.project_id === model.project) && (!filter || matches(s.name, s.description, s.path))); }
  function agents(filter = true) { return array(model.data?.agents).filter((a) => (!model.project || !a.project_id || a.project_id === model.project) && (!filter || matches(a.name, a.description, a.path))); }
  function patterns(filter = true) { return projects().flatMap((p) => array(p.learning?.patterns).map((pattern) => ({...pattern, project: p.id, projectName: p.name, threshold: numeric(p.learning?.threshold) || 3}))).filter((p) => !filter || matches(p.key, p.candidate?.name, p.projectName)); }
  function events(filter = true) { return array(model.data?.activity?.events).filter((e) => (!model.project || e.project === model.project) && (!filter || matches(e.name, e.path, e.status, e.kind, e.source, projectName(e.project)))).sort((a,b) => (timestamp(b.timestamp) || 0) - (timestamp(a.timestamp) || 0)); }
  function recentEvents() {
    const sessions = array(model.data?.activity?.sessions);
    return events(false).filter((event) => {
      const age = Date.now() - timestamp(event.timestamp);
      const session = sessions.find((s) => s.id === event.session && (!s.project || !event.project || s.project === event.project));
      const observedUse = ["executed", "started", "completed"].includes(event.status) || (event.kind === "agent" && event.status === "spawned");
      return age >= 0 && age < 120000 && ["skill", "agent"].includes(event.kind) && observedUse && (session ? session.status !== "stopped" : event.source === "agent-report");
    });
  }
  function declaredInUse() {
    const sessions = array(model.data?.activity?.sessions);
    const latest = new Map();
    const reports = [...array(model.data?.activity?.latest_reports).filter((e) => !model.project || e.project === model.project), ...events(false)].sort((a,b) => (timestamp(b.timestamp) || 0) - (timestamp(a.timestamp) || 0));
    for (const event of reports) {
      if (event.source !== "agent-report" || !["skill", "agent"].includes(event.kind)) continue;
      const key = JSON.stringify([event.project || "", event.session || "", event.kind, event.name]);
      if (!latest.has(key)) latest.set(key, event);
    }
    return [...latest.values()].filter((event) => {
      if (event.status !== "started") return false;
      const session = sessions.find((s) => s.id === event.session && (!s.project || !event.project || s.project === event.project));
      if (session) return session.status === "recent";
      const age = Date.now() - timestamp(event.timestamp);
      return age >= 0 && age < 120000;
    });
  }
  function normalizedPath(value) {
    const path = str(value).trim().replace(/\\/g, "/");
    const windows = /^(?:[a-z]:|\/\/)/i.test(path);
    const prefix = path.startsWith("//") ? "//" : path.startsWith("/") ? "/" : "";
    const parts = [];
    for (const part of path.split("/")) {
      if (!part || part === ".") continue;
      if (part === ".." && parts.length && parts[parts.length - 1] !== ".." && !parts[parts.length - 1].endsWith(":")) parts.pop();
      else if (part !== ".." || (!prefix && !windows)) parts.push(part);
    }
    const normalized = prefix + parts.join("/");
    return windows ? normalized.toLowerCase() : normalized;
  }
  function matchesEntity(event, entity, kind) {
    if (event.kind !== kind) return false;
    if (event.path) {
      const eventPath = normalizedPath(event.path), entityPath = normalizedPath(entity.path);
      if (!entityPath) return false;
      if (eventPath === entityPath) return true;
      if (kind !== "skill" || !/\/skill\.md$/i.test(entityPath)) return false;
      const skillDirectory = entityPath.slice(0, entityPath.lastIndexOf("/") + 1);
      return eventPath.startsWith(skillDirectory) && !/\/skill\.md$/i.test(eventPath);
    }
    if (event.name !== entity.name) return false;
    if ((entity.project_id || ["project", "candidate"].includes(entity.scope)) && (!entity.project_id || normalizedPath(entity.project_id) !== normalizedPath(event.project))) return false;
    // A pathless report cannot identify which same-name installation was used.
    // Keep the event visible without decorating two different instruction sets.
    const applicable = array(kind === "agent" ? model.data?.agents : model.data?.skills).filter((item) => item.name === entity.name && item.installed !== false && item.scope !== "candidate" && (!item.project_id || normalizedPath(item.project_id) === normalizedPath(event.project)));
    if (new Set(applicable.map((item) => normalizedPath(item.path || item.id))).size > 1) return false;
    return true;
  }
  function originLabel(entity) { return ({core:"Base de Nexo", installed:"Extensión instalada", learned:"Aprendida", candidate:"Candidata de aprendizaje"})[entity.origin] || "Origen no registrado"; }
  function scopeLabel(entity) { return entity.scope === "project" ? `Proyecto: ${projectName(entity.project_id)}` : entity.scope === "candidate" ? "Candidata" : "Personal"; }
  function declaredEntity(entity, kind) { return declaredInUse().some((e) => matchesEntity(e, entity, kind)); }
  function recentEntity(entity, kind) { return recentEvents().some((e) => matchesEntity(e, entity, kind)); }
  function recentLabel(entity, kind) { const found = recentEvents().filter((e) => matchesEntity(e, entity, kind)); return found.length && found.every((e) => e.source === "agent-report") ? "Uso declarado" : "Uso reciente"; }
  function usageLabel(entity, kind) { return declaredEntity(entity, kind) ? "En uso · declarado" : recentEntity(entity, kind) ? recentLabel(entity, kind) : ""; }
  function statusLabel(status, source) {
    if (source === "agent-report") return `Uso declarado · ${({started:"inicio", completed:"finalización", executed:"ejecución", failed:"fallo"})[status] || str(status)}`;
    return ({loaded:"Instrucciones leídas", executed:"Ejecución observada", spawned:"Delegación observada", referenced:"Referencia observada", started:"Inicio observado", completed:"Finalización observada", failed:"Fallo observado"})[status] || str(status) || "Evento observado";
  }
  function badge(text, type = "") { return el("span", {class: `badge ${type}`}, text); }
  function skillIcon(skill) {
    const name = str(skill.name).toLowerCase();
    for (const [match, symbol] of [["plan","plan"],["build","code"],["review","review"],["verify","review"],["debug","debug"],["memory","memory"],["orchestrate","workflow"],["learn","learn"],["resume","clock"]]) if (name.includes(match)) return symbol;
    return "skill";
  }
  function empty(title, message, symbol = "skill") { return el("div", {class:"empty-state"}, el("div", {class:"entity-icon"}, icon(symbol)), el("h3", {}, title), el("p", {}, message)); }
  function sectionHeading(title, subtitle, action, view) { return el("div", {class:"section-heading"}, el("div", {}, el("h2", {}, title), subtitle ? el("p", {class:"section-subtitle"}, subtitle) : null), action ? el("button", {class:"section-link", onClick:() => changeView(view)}, action, icon("arrow")) : null); }
  function entityCard(entity, kind = "skill", compact = false) {
    const isAgent = kind === "agent";
    const learned = entity.origin === "learned";
    const symbol = isAgent ? "agent" : skillIcon(entity);
    const usage = usageLabel(entity, kind);
    if (compact) return el("button", {class:"agent-compact", "data-focus-key":`agent-${entity.id}`, onClick:() => showEntity(entity, kind)}, el("span", {class:"entity-icon agent"}, icon(symbol)), el("span", {}, el("strong", {}, entity.name), el("small", {}, usage || "Instrucciones del rol")));
    return el("button", {class:"entity-card", "data-focus-key":`${kind}-${entity.id}`, onClick:() => showEntity(entity, kind)},
      el("div", {class:"entity-top"}, el("span", {class:`entity-icon ${isAgent ? "agent" : learned ? "learned" : ""}`}, icon(symbol)), usage ? badge(usage, "teal") : entity.scope === "candidate" ? badge("Candidata", "amber") : learned ? badge("Aprendida", "amber") : badge(isAgent ? "AGENTE" : "SKILL")),
      el("h3", {}, entity.name), el("p", {}, entity.description || "Consulta las instrucciones completas de esta capacidad."),
      el("div", {class:"entity-footer"}, el("span", {}, entity.scope === "project" ? projectName(entity.project_id) : entity.scope === "candidate" ? "Pendiente de instalación" : isAgent ? "Rol personal" : "Disponible en tus proyectos"), icon("arrow")));
  }
  function statCard(label, value, foot, symbol, view, positive = false) { return el("button", {class:"stat-card", "data-focus-key":`stat-${view}`, onClick:() => changeView(view)}, el("div", {class:"stat-top"}, el("span", {}, label), el("span", {class:"stat-icon"}, icon(symbol))), el("div", {class:"stat-value"}, value), el("div", {class:"stat-foot"}, positive ? icon("check") : null, el("span", {class:positive ? "positive" : ""}, foot))); }
  function activityRow(event, full = false) {
    const available = event.kind === "skill" ? skills(false).find((item) => matchesEntity(event, item, "skill")) : event.kind === "agent" ? agents(false).find((item) => matchesEntity(event, item, "agent")) : null;
    const row = el(available ? "button" : "div", {class:"activity-row", ...(available ? {onClick:() => showEntity(available, event.kind === "agent" ? "agent" : "skill")} : {})}, el("span", {class:"activity-symbol"}, icon(({agent:"agent", skill:"skill", tool:"code", hook:"workflow"})[event.kind] || "activity")), el("span", {class:"activity-info"}, el("strong", {}, event.name || "Evento sin nombre"), el("p", {}, statusLabel(event.status, event.source), " · ", projectName(event.project), full && event.source ? ` · Fuente: ${event.source}` : "")), el("time", {class:"activity-time", title:absoluteTime(event.timestamp)}, relativeTime(event.timestamp)));
    if (available) { row.classList.add("activity-button"); row.setAttribute("data-focus-key", `event-${event.id}`); }
    return row;
  }
  function warningPanel() {
    const warnings = [...array(model.data?.warnings), ...projects().flatMap((p) => array(p.errors).map((e) => `${p.name}: ${typeof e === "string" ? e : JSON.stringify(e)}`))];
    if (!warnings.length) return null;
    return el("aside", {class:"warnings"}, el("h3", {}, "Algunos datos requieren atención"), el("ul", {}, warnings.map((w) => el("li", {}, typeof w === "string" ? w : JSON.stringify(w)))));
  }
  function overview() {
    const allSkills = skills(false), installed = allSkills.filter((s) => s.installed), learned = installed.filter((s) => s.origin === "learned"), shownSkills = skills().filter((s) => s.installed).sort((a,b) => Number(declaredEntity(b,"skill")) - Number(declaredEntity(a,"skill"))).slice(0,6), shownAgents = agents().sort((a,b) => Number(declaredEntity(b,"agent")) - Number(declaredEntity(a,"agent"))).slice(0,4), allPatterns = patterns(false), recent = recentEvents().filter((e) => e.source !== "agent-report");
    const recentCount = new Set(recent.map((e) => `${e.kind}:${e.path || e.name}`)).size;
    const declaredCount = new Set(declaredInUse().map((e) => `${e.kind}:${e.path || e.name}`)).size;
    const capabilityEvents = events().filter((e) => ["skill", "agent"].includes(e.kind));
    const overviewEvents = (capabilityEvents.length ? capabilityEvents : events()).slice(0,4);
    const totalSuccesses = allPatterns.reduce((sum,p) => sum + numeric(p.consecutive_successes), 0);
    const content = el("div", {},
      el("div", {class:"stats-grid"}, statCard("Skills instaladas", installed.length, `${installed.filter((s) => s.scope === "personal").length} personales · ${installed.filter((s) => s.scope === "project").length} de proyecto`, "skill", "skills"), statCard("Agentes configurados", agents(false).length, "Un rol para cada etapa", "agent", "agents"), statCard("Skills aprendidas", learned.length, learned.length ? `${learned.filter((s) => s.verified_installation).length} instalaciones verificadas` : "El siguiente paso nace de la práctica", "learn", "learning", learned.length > 0), statCard("En uso · declarado", declaredCount, `Uso reciente: ${recentCount} · Últimos 2 min`, "activity", "activity")),
      el("div", {class:"overview-columns"},
        el("section", {}, sectionHeading("Tu biblioteca de skills", "Pequeños métodos. Un trabajo mejor conectado.", "Ver todas", "skills"), el("div", {class:"skill-grid"}, shownSkills.length ? shownSkills.map((s) => entityCard(s)) : empty(model.query ? "Sin coincidencias" : "Tu biblioteca está por descubrir", model.query ? "Prueba con otro nombre o descripción." : "Las skills instaladas aparecerán aquí al conectar sus archivos."))),
        el("aside", {class:"overview-sidebar"},
          el("section", {class:"side-panel"}, el("div", {class:"section-heading"}, el("h2", {}, "Actividad reciente"), el("span", {class:"live-label"}, el("span", {class:"status-dot"}), "REGISTRADA")), el("div", {class:"activity-list"}, overviewEvents.length ? overviewEvents.map((e) => activityRow(e)) : empty("Sin actividad registrada", "Los eventos observados aparecerán aquí con su fuente.", "activity")), events().length ? el("button", {class:"section-link", onClick:() => changeView("activity")}, "Ver actividad", icon("arrow")) : null),
          el("section", {class:"learning-panel"}, el("div", {class:"section-heading"}, el("h2", {}, "Nexo aprende contigo"), icon("learn")), el("p", {}, "Cada procedimiento comprobado acerca una nueva capacidad a tu biblioteca."), el("div", {class:"learning-number"}, allPatterns.length, el("span", {class:"learning-unit"}, allPatterns.length === 1 ? "patrón registrado" : "patrones registrados")), el("p", {}, `${totalSuccesses} ${totalSuccesses === 1 ? "éxito consecutivo registrado" : "éxitos consecutivos registrados"}`), learningSteps(), el("button", {class:"section-link", onClick:() => changeView("learning")}, "Explorar aprendizaje", icon("arrow"))))),
      el("section", {class:"overview-agents"}, sectionHeading("Tu equipo de agentes", null, "Conocer los roles", "agents"), el("div", {class:"agent-strip"}, shownAgents.length ? shownAgents.map((a) => entityCard(a, "agent", true)) : empty("Sin agentes configurados", "Los roles disponibles se mostrarán cuando se encuentren sus instrucciones.", "agent"))),
      projectSection(), warningPanel());
    return content;
  }
  function learningSteps() { return el("div", {class:"learning-steps", "aria-label":"Proceso: repetir, preparar, validar e instalar"}, ["1","2","3","4"].flatMap((n,i) => [i ? el("span", {class:"step-line"}) : null, el("span", {class:"learning-step", title:["Repetir","Preparar candidata","Validar","Instalar"][i]}, n)])); }
  function projectSection() {
    const list = projects().filter((p) => matches(p.name, p.path));
    return el("section", {class:"projects-section"}, el("div", {class:"section-heading"}, el("h2", {}, "Proyectos conectados"), el("button", {class:"section-link", onClick:showProjectForm}, "Añadir proyecto", icon("arrow"))), el("div", {class:"project-list"}, list.length ? list.map((p) => el("button", {class:"project-row", onClick:() => { model.project = p.id; render(); }}, icon("folder"), el("span", {class:"project-copy"}, el("strong", {}, p.name), el("small", {}, p.path)), badge(array(p.errors).length ? "Revisar datos" : p.initialized ? "Nexo disponible" : "Sin inicializar", array(p.errors).length ? "amber" : p.initialized ? "teal" : ""))) : empty("Conecta tu primer proyecto", "Añade una carpeta para consultar su aprendizaje y su actividad local.", "folder")));
  }
  function chips(items, selected, setter) { return el("div", {class:"filter-chips", role:"group", "aria-label":"Filtrar resultados"}, items.map(([id,label]) => el("button", {class:`filter-chip ${selected === id ? "selected" : ""}`, "data-focus-key":`filter-${id}`, "aria-pressed":selected === id ? "true" : "false", onClick:() => { setter(id); render(); }}, label))); }
  function skillView() {
    const shown = skills().filter((s) => model.skillFilter === "all" || (model.skillFilter === "learned" ? s.origin === "learned" : model.skillFilter === "candidate" ? s.scope === "candidate" : s.scope === model.skillFilter));
    return el("section", {}, el("div", {class:"toolbar"}, chips([["all","Todas"],["personal","Personales"],["project","De proyecto"],["learned","Aprendidas"],["candidate","Candidatas"]], model.skillFilter, (v) => model.skillFilter = v), el("span", {class:"result-count"}, `${shown.length} ${shown.length === 1 ? "skill" : "skills"}`)), el("div", {class:"skill-grid"}, shown.length ? shown.map((s) => entityCard(s)) : empty("No hay skills en esta selección", "Cambia el filtro o la búsqueda para explorar tu biblioteca.")), warningPanel());
  }
  function agentView() { const shown = agents(); return el("section", {}, el("div", {class:"toolbar"}, el("p", {class:"result-count"}, "Roles personales y de los proyectos seleccionados"), el("span", {class:"result-count"}, `${shown.length} ${shown.length === 1 ? "agente" : "agentes"}`)), el("div", {class:"skill-grid"}, shown.length ? shown.map((a) => entityCard(a,"agent")) : empty("No se han encontrado agentes", model.query ? "Prueba con otra búsqueda." : "Los agentes aparecerán al encontrar sus archivos de configuración.", "agent"))); }
  function patternCard(pattern) {
    const count = numeric(pattern.consecutive_successes), candidate = pattern.candidate, validation = pattern.validation, installs = array(pattern.installations), installed = installs.some((i) => i.verified_installation);
    const stages = [["Repetición", `${count}/${pattern.threshold} éxitos`, count >= pattern.threshold], ["Candidata", candidate ? candidate.current ? "Vigente" : "Registrada" : "Pendiente", !!candidate?.current], ["Validación", validation ? validation.current ? "Registrada" : "Revisar vigencia" : "Pendiente", !!validation?.current], ["Instalación", installed ? "Comprobada" : installs.length ? "Registrada" : "Pendiente", installed]];
    return el("button", {class:"pattern-card", "data-focus-key":`pattern-${pattern.project}-${pattern.key}`, onClick:() => showPattern(pattern)}, el("div", {class:"pattern-header"}, el("h3", {}, candidate?.name || pattern.key), badge(installed ? "Instalada" : pattern.eligible ? "Puede preparar candidata" : "En aprendizaje", installed ? "teal" : "amber")), el("p", {class:"pattern-project"}, pattern.projectName), el("div", {class:"pipeline"}, stages.map(([name,value,reached]) => el("div", {class:`pipeline-stage ${reached ? "reached" : ""}`}, el("strong", {}, name), el("span", {}, value)))), el("div", {class:"pattern-foot"}, el("span", {}, `${numeric(pattern.observations)} ${numeric(pattern.observations) === 1 ? "observación" : "observaciones"} · ${learningStatus(pattern.last_status)}`), icon("arrow")));
  }
  function learningStatus(status) { return ({passed:"Último resultado: éxito", success:"Último resultado: éxito", succeeded:"Último resultado: éxito", failure:"Último resultado: fallo", failed:"Último resultado: fallo"})[status] || (status ? `Último resultado: ${status}` : "Sin resultado registrado"); }
  function learningView() {
    const shown = patterns();
    return el("section", {}, el("div", {class:"learning-intro"}, el("div", {}, el("h2", {}, "Aprender lleva evidencia."), el("p", {}, "Tres peticiones distintas resueltas con éxito consecutivo permiten preparar una candidata. Un caso nuevo, una revisión y una validación deben preceder a su instalación.")), learningSteps()), el("div", {class:"toolbar"}, el("span", {class:"result-count"}, model.project ? projectName(model.project) : "Aprendizaje separado por proyecto"), el("span", {class:"result-count"}, `${shown.length} ${shown.length === 1 ? "patrón" : "patrones"}`)), el("div", {class:"learning-grid"}, shown.length ? shown.map(patternCard) : empty("El aprendizaje empieza con la práctica", "Aquí aparecerán los procedimientos registrados en los proyectos conectados. Todavía no hay patrones para esta selección.", "learn")), el("p", {class:"info-note"}, "Una validación registrada conserva el resultado de Nexo; el panel no vuelve a ejecutar sus pruebas. La instalación comprobada indica que el archivo coincide con su registro. Puedes abrir cada patrón para consultar sus datos y su vigencia."), warningPanel());
  }
  function activityView() {
    const shown = events().filter((e) => model.activityFilter === "all" || e.kind === model.activityFilter);
    const inUse = declaredInUse().filter((e) => (model.activityFilter === "all" || e.kind === model.activityFilter) && matches(e.name, e.path, projectName(e.project)));
    return el("section", {},
      el("div", {class:"toolbar"}, chips([["all","Todos los eventos"],["skill","Skills"],["agent","Agentes"]], model.activityFilter, (v) => model.activityFilter = v), el("span", {class:"result-count"}, `${shown.length} ${shown.length === 1 ? "evento" : "eventos"}`)),
      inUse.length ? el("section", {class:"side-panel"}, sectionHeading("En uso · declarado", "Inicios registrados sin cierre posterior en una sesión reciente."), el("div", {class:"activity-list"}, inUse.map((e) => activityRow(e,true)))) : null,
      shown.length ? el("div", {class:"activity-list activity-full"}, shown.map((e) => activityRow(e,true))) : empty("Aún no hay actividad registrada", "La lectura, las referencias y las ejecuciones se mostrarán por separado cuando lleguen eventos locales.", "activity"),
      el("p", {class:"info-note"}, "«En uso · declarado» conserva el último inicio cooperativo mientras su sesión esté reciente y no haya un cierre posterior. Desaparece cuando la sesión está detenida o inactiva; sin sesión, caduca a los 120 segundos. Depende de que el agente registre el inicio y el cierre, y no garantiza una ejecución viva. «Uso reciente» indica eventos observados en los últimos 120 segundos. La instalación y la lectura de instrucciones no cuentan como ejecución."));
  }
  function changeView(view) { model.view = view; render(); window.scrollTo({top:0, behavior:"instant"}); }
  function renderNavigation() {
    const counts = {skills:skills(false).length, agents:agents(false).length, learning:patterns(false).length};
    $("navigation").replaceChildren(...Object.entries(views).map(([id,view]) => el("button", {class:`nav-button ${model.view === id ? "selected" : ""}`, title:view.name, "aria-label":view.name, "data-focus-key":`nav-${id}`, ...(model.view === id ? {"aria-current":"page"} : {}), onClick:() => changeView(id)}, icon(view.icon), el("span", {class:"nav-label"}, view.name), counts[id] !== undefined ? el("span", {class:"nav-count"}, counts[id]) : null)));
  }
  function render() {
    const focused = document.activeElement?.getAttribute("data-focus-key");
    renderNavigation();
    const view = views[model.view];
    $("current-view").textContent = view.name; $("page-title").textContent = view.title; $("page-description").textContent = view.description;
    document.title = `Nexo · ${view.name}`;
    const choices = JSON.stringify(array(model.data?.projects).map((p) => [p.id,p.name]));
    if ($("project-select").dataset.choices !== choices) { $("project-select").replaceChildren(el("option", {value:""}, "Todos los proyectos"), ...array(model.data?.projects).map((p) => el("option", {value:p.id}, p.name))); $("project-select").dataset.choices = choices; }
    if (model.project && !array(model.data?.projects).some((p) => p.id === model.project)) model.project = "";
    $("project-select").value = model.project;
    if (model.data) { $("content").replaceChildren(({overview, skills:skillView, agents:agentView, learning:learningView, activity:activityView})[model.view]()); $("content").setAttribute("aria-busy", "false"); $("version").textContent = model.data.version ? `v${model.data.version}` : "PANEL LOCAL"; }
    else if (model.error) { $("content").replaceChildren(empty("No se pudo cargar el espacio", "Comprueba que el servidor local de Nexo sigue abierto y vuelve a actualizar.", "activity")); $("content").setAttribute("aria-busy", "false"); }
    if (focused) [...document.querySelectorAll("[data-focus-key]")].find((item) => item.getAttribute("data-focus-key") === focused)?.focus({preventScroll:true});
    renderConnection();
  }
  function renderConnection() {
    const offline = !!model.error;
    $("connection-dot").className = `status-dot ${offline ? "offline" : model.paused ? "paused" : ""}`;
    $("connection-banner").hidden = !offline;
    $("connection-banner").textContent = `${model.error}${model.data ? " Se conservan los últimos datos recibidos." : ""}`;
    const time = model.lastRefresh ? new Intl.DateTimeFormat("es", {hour:"2-digit", minute:"2-digit", second:"2-digit"}).format(model.lastRefresh) : null;
    $("refresh-label").textContent = offline ? `Sin conexión${time ? ` · Último refresco ${time}` : ""}` : model.paused ? `Actualizaciones pausadas${time ? ` · ${time}` : ""}` : time ? `Actualizado a las ${time} · Cada 5 s` : "Conectando con Nexo…";
    $("pause-updates").textContent = model.paused ? "Reanudar actualizaciones" : "Pausar actualizaciones";
    $("pause-updates").setAttribute("aria-pressed", String(model.paused));
    $("refresh-now").disabled = model.pending;
  }
  async function api(url, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    let response;
    try { response = await fetch(url, {...options, signal:controller.signal, cache:"no-store", credentials:"same-origin", headers:{"X-Nexo-Token":token, ...options.headers}}); }
    catch (error) { if (error.name === "AbortError") throw new Error("El servidor local está tardando en responder. Puedes volver a actualizar."); throw error; }
    finally { clearTimeout(timer); }
    let data;
    try { data = await response.json(); } catch (_) { throw new Error("El servidor local devolvió una respuesta no válida."); }
    if (!response.ok) throw new Error(typeof data.error === "string" ? data.error : response.status === 401 || response.status === 403 ? "Acceso local no autorizado. Abre el enlace que genera Nexo con su token." : `No se pudieron cargar los datos (${response.status}).`);
    return data;
  }
  async function refresh() {
    if (model.pending) return;
    model.pending = true; renderConnection();
    try { const data = await api("/api/state"); if (!data || !Array.isArray(data.projects) || !Array.isArray(data.skills) || !Array.isArray(data.agents)) throw new Error("El estado recibido no tiene el formato esperado."); model.data = data; model.online = true; model.error = ""; model.lastRefresh = Date.now(); }
    catch (error) { model.online = false; model.error = error instanceof TypeError ? "No se puede contactar con el servidor local de Nexo." : error.message; }
    finally { model.pending = false; render(); }
  }
  function metadata(rows) { $("detail-metadata").replaceChildren(...rows.filter(([,value]) => value != null && value !== "").map(([label,value]) => el("div", {class:"metadata-row"}, el("span", {class:"metadata-label"}, label), el("span", {class:"metadata-value"}, value)))); }
  function showEntity(entity, kind) {
    model.detail = {name:entity.name, instructions:entity.instructions || "No hay instrucciones disponibles en el archivo.", focusKey:document.activeElement?.getAttribute("data-focus-key") || `${kind}-${entity.id}`};
    $("detail-kind").textContent = kind === "agent" ? "AGENTE · INSTRUCCIONES DEL ROL" : "SKILL · BIBLIOTECA LOCAL";
    $("detail-icon").replaceChildren(el("div", {class:`entity-icon ${kind === "agent" ? "agent" : entity.origin === "learned" ? "learned" : ""}`}, icon(kind === "agent" ? "agent" : skillIcon(entity))));
    $("detail-title").textContent = entity.name; $("detail-description").textContent = entity.description || "Consulta del archivo de instrucciones completo.";
    $("detail-badges").replaceChildren(...[badge(scopeLabel(entity)), badge(originLabel(entity), entity.origin === "learned" ? "amber" : ""), ...(kind === "agent" ? [badge("Rol configurado","blue")] : [entity.installed ? badge("Instalada","teal") : badge("Sin instalar","amber"), entity.verified_installation ? badge("Instalación comprobada","teal") : null])].filter(Boolean), ...(usageLabel(entity,kind) ? [badge(usageLabel(entity,kind),"teal")] : []));
    metadata([["Archivo",entity.path], ["Proyecto",entity.project_id ? projectName(entity.project_id) : "Ámbito personal"], ["Acceso","Consulta y copia de instrucciones"]]);
    $("instructions-label").textContent = "Instrucciones completas"; $("copy-instructions").textContent = "Copiar instrucciones"; $("detail-instructions").textContent = model.detail.instructions;
    if (!$("details-dialog").open) $("details-dialog").showModal();
    $("details-dialog").querySelector(".drawer-body").scrollTop = 0;
  }
  function showPattern(pattern) {
    const instructions = JSON.stringify({key:pattern.key, observations:pattern.observations, consecutive_successes:pattern.consecutive_successes, threshold:pattern.threshold, eligible:pattern.eligible, last_status:pattern.last_status, candidate:pattern.candidate || null, validation:pattern.validation || null, installations:array(pattern.installations)}, null, 2);
    model.detail = {name:pattern.candidate?.name || pattern.key, instructions, focusKey:`pattern-${pattern.project}-${pattern.key}`};
    $("detail-kind").textContent = "APRENDIZAJE · EVIDENCIA REGISTRADA"; $("detail-icon").replaceChildren(el("div", {class:"entity-icon learned"}, icon("learn"))); $("detail-title").textContent = model.detail.name;
    $("detail-description").textContent = "Estado local del procedimiento. La validación registrada no significa que el panel haya vuelto a ejecutar o verificar las pruebas.";
    $("detail-badges").replaceChildren(badge(`${numeric(pattern.consecutive_successes)}/${pattern.threshold} éxitos consecutivos`, "teal"), badge(pattern.eligible ? "Puede preparar candidata" : "En aprendizaje", "amber"));
    metadata([["Proyecto", pattern.projectName], ["Candidata",pattern.candidate?.path || "No registrada"], ["Validación",pattern.validation ? pattern.validation.current ? "Registro vigente; evidencias no reejecutadas" : "Registro pendiente de revisar" : "No registrada"], ["Instalación",array(pattern.installations).some((i) => i.verified_installation) ? "Archivo comprobado contra su registro" : "Sin instalación comprobada"]]);
    $("instructions-label").textContent = "Datos del procedimiento"; $("copy-instructions").textContent = "Copiar datos"; $("detail-instructions").textContent = instructions;
    if (!$("details-dialog").open) $("details-dialog").showModal();
    $("details-dialog").querySelector(".drawer-body").scrollTop = 0;
  }
  let toastTimer;
  function toast(message) { $("toast").textContent = message; $("toast").hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $("toast").hidden = true, 3000); }
  async function copy(text, label) {
    try { await navigator.clipboard.writeText(text); toast(`${label} copiado.`); }
    catch (_) { const area = el("textarea", {"aria-label":"Texto para copiar", class:"clipboard-fallback"}, text); const target = $("details-dialog").open ? $("details-dialog") : document.body; target.append(area); area.select(); const ok = document.execCommand("copy"); area.remove(); toast(ok ? "Copiado al portapapeles." : "No se pudo copiar. Selecciona el texto de las instrucciones."); }
  }
  function showProjectForm() { $("project-error").hidden = true; $("project-path").value = ""; if (!$("project-dialog").open) $("project-dialog").showModal(); $("project-path").focus(); }
  $("search-icon").append(icon("search")); $("project-icon").append(icon("folder"));
  $("search").addEventListener("input", (event) => { model.query = event.target.value.trim(); render(); });
  $("project-select").addEventListener("change", (event) => { model.project = event.target.value; render(); });
  $("refresh-now").addEventListener("click", refresh);
  $("pause-updates").addEventListener("click", () => { model.paused = !model.paused; renderConnection(); if (!model.paused) refresh(); });
  $("add-project").addEventListener("click", showProjectForm);
  document.querySelector(".brand").addEventListener("click", (event) => { event.preventDefault(); changeView("overview"); });
  document.querySelectorAll(".close-dialog").forEach((button) => button.addEventListener("click", () => button.closest("dialog").close()));
  document.querySelectorAll("dialog").forEach((dialog) => dialog.addEventListener("click", (event) => { const bounds = dialog.getBoundingClientRect(); if (event.target === dialog && (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom)) dialog.close(); }));
  $("copy-name").addEventListener("click", () => model.detail && copy(model.detail.name, "Nombre"));
  $("details-dialog").addEventListener("close", () => { if (model.detail?.focusKey) [...$("content").querySelectorAll("[data-focus-key]")].find((item) => item.getAttribute("data-focus-key") === model.detail.focusKey)?.focus({preventScroll:true}); });
  $("copy-instructions").addEventListener("click", () => model.detail && copy(model.detail.instructions, "Texto"));
  $("project-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const path = $("project-path").value.trim(); if (!path) return;
    $("submit-project").disabled = true; $("submit-project").textContent = "Añadiendo…"; $("project-error").hidden = true;
    try { await api("/api/projects", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({path})}); $("project-dialog").close(); toast("Proyecto añadido al panel."); await refresh(); }
    catch (error) { $("project-error").textContent = error.message || "No se pudo añadir el proyecto."; $("project-error").hidden = false; }
    finally { $("submit-project").disabled = false; $("submit-project").textContent = "Añadir proyecto"; }
  });
  document.addEventListener("keydown", (event) => { if (event.key === "/" && !event.ctrlKey && !event.metaKey && !["INPUT","TEXTAREA","SELECT"].includes(document.activeElement?.tagName) && !document.querySelector("dialog[open]")) { event.preventDefault(); $("search").focus(); } });
  render(); refresh(); setInterval(() => { if (!model.paused && !document.hidden) refresh(); }, 5000);
  // Presence is independent of paused data updates and works in the Codex tab.
  // Background browser throttling is tolerated by the server's two-minute TTL.
  const presence = () => api("/api/presence", {method:"POST"}).catch(() => {});
  presence(); setInterval(presence, 30000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden && !model.paused) refresh(); });
})();
