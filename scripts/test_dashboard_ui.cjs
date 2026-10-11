#!/usr/bin/env node
"use strict";

// Run with: node scripts/test_dashboard_ui.cjs
// Exercise the dashboard's real state/usage functions in an isolated VM.
// Rendering is outside this check; no browser, server or profile is modified.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const assert = require("node:assert/strict");

const source = fs.readFileSync(path.join(__dirname, "../nexo_kit/assets/dashboard/app.js"), "utf8");
const boundary = source.indexOf("  function badge(");
assert.ok(boundary > 0, "The state helpers must precede the DOM-rendering boundary.");
const now = Date.UTC(2026, 9, 10, 12, 0, 0);
class TestDate extends Date { static now() { return now; } }
const sandbox = {
  URLSearchParams,
  location: {hash: "", pathname: "/", search: ""},
  sessionStorage: {getItem: () => null, setItem: () => {}},
  document: {},
  Date: TestDate,
};
vm.runInNewContext(source.slice(0, boundary) + `
  globalThis.checks = {model, declaredInUse, declaredEntity, recentEvents, recentEntity, usageLabel, matchesEntity, agents, originLabel, scopeLabel, statusLabel};
})();`, sandbox, {filename: "dashboard-state-functions.js"});
const {model, declaredInUse, declaredEntity, recentEvents, recentEntity, usageLabel, matchesEntity, agents, originLabel, scopeLabel, statusLabel} = sandbox.checks;
const session = {id: "s1", project: "/projects/a", status: "recent", last_seen: new Date(now).toISOString()};
const start = {id: "start", timestamp: new Date(now - 600000).toISOString(), project: session.project, session: "s1", kind: "skill", name: "same-name", path: "/projects/a/.agents/skills/same-name/SKILL.md", source: "agent-report", status: "started"};
const skillA = {id: start.path, name: start.name, path: start.path, scope: "project", project_id: "/projects/a"};
const skillB = {...skillA, id: "/projects/b/.agents/skills/same-name/SKILL.md", path: "/projects/b/.agents/skills/same-name/SKILL.md", project_id: "/projects/b"};
function state(reports, sessions = [session], events = []) {
  model.project = "";
  model.query = "";
  model.data = {skills: [skillA, skillB], activity: {latest_reports: reports, sessions, events}};
}
let passed = 0;
function check(name, fn) {
  try { fn(); passed++; process.stdout.write(`ok ${passed} - ${name}\n`); }
  catch (error) { process.stderr.write(`FAIL - ${name}\n`); throw error; }
}

check("a ten-minute-old start remains declared while its session is recent", () => {
  state([start]);
  assert.equal(declaredInUse().length, 1);
  assert.equal(usageLabel(skillA, "skill"), "En uso · declarado");
});
for (const status of ["stale", "stopped"]) check(`${status} sessions have no declared ongoing use`, () => {
  state([start], [{...session, status}]);
  assert.equal(declaredInUse().length, 0);
});
check("a later completion overrides an old start in the event window", () => {
  state([{...start, status: "completed", timestamp: new Date(now).toISOString()}], [session], [start]);
  assert.equal(declaredInUse().length, 0);
});
check("a report without a session is accepted for 120 seconds only", () => {
  state([{...start, session: null, timestamp: new Date(now - 60000).toISOString()}], []);
  assert.equal(declaredInUse().length, 1);
  state([{...start, session: null, timestamp: new Date(now - 120000).toISOString()}], []);
  assert.equal(declaredInUse().length, 0);
});
check("a completion in another session does not close this session's start", () => {
  state([start, {...start, session: "s2", status: "completed", timestamp: new Date(now).toISOString()}]);
  assert.equal(declaredInUse().length, 1);
});
check("project filtering applies to reports outside the event window", () => {
  state([start]); model.project = "/projects/b";
  assert.equal(declaredInUse().length, 0);
});
check("reading instructions and running generic tools do not count as recent skill use", () => {
  state([], [session], [{...start, source: "hook", status: "loaded", timestamp: new Date(now).toISOString()}, {...start, kind: "tool", source: "hook", status: "executed", timestamp: new Date(now).toISOString()}]);
  assert.equal(recentEvents().length, 0);
});
check("a native role spawn is recent observed use without claiming ongoing execution", () => {
  const role = {id:"/profile/agents/nexo-reviewer.toml", name:"nexo-reviewer", path:"/profile/agents/nexo-reviewer.toml", scope:"personal"};
  const spawned = {...start, kind:"agent", name:role.name, path:role.path, source:"hook:PostToolUse:spawn_agent", status:"spawned", timestamp:new Date(now).toISOString()};
  state([], [session], [spawned]);
  model.data.agents = [role];
  assert.equal(recentEvents().length, 1);
  assert.equal(usageLabel(role, "agent"), "Uso reciente");
  assert.equal(statusLabel(spawned.status, spawned.source), "Delegación observada");
  assert.equal(declaredInUse().length, 0);
  for (const status of ["referenced", "failed"]) {
    model.data.activity.events = [{...spawned, status}];
    assert.equal(recentEvents().length, 0);
  }
  model.data.activity.events = [{...spawned, timestamp:new Date(now - 120000).toISOString()}];
  assert.equal(recentEvents().length, 0);
});
check("the latest-report ledger preserves starts beyond 200 noisy tool events", () => {
  state([start], [session], Array.from({length: 200}, (_, i) => ({...start, id: String(i), kind: "tool", source: "hook", status: "executed", timestamp: new Date(now - i).toISOString()})));
  assert.equal(declaredInUse().length, 1);
});
check("same-name skills in different projects match only the explicit path", () => {
  state([start]);
  assert.equal(declaredEntity(skillA, "skill"), true);
  assert.equal(declaredEntity(skillB, "skill"), false);
  const recent = {...start, source: "hook", status: "executed", timestamp: new Date(now).toISOString()};
  state([], [session], [recent]);
  assert.equal(recentEntity(skillA, "skill"), true);
  assert.equal(recentEntity(skillB, "skill"), false);
});
check("a mismatching explicit path never falls back to a matching name", () => {
  assert.equal(matchesEntity({...start, path: "/elsewhere/SKILL.md"}, skillA, "skill"), false);
});
check("a script in the skill directory belongs only to that skill", () => {
  const event = {...start, path: "/projects/a/.agents/skills/same-name/scripts/check.py"};
  assert.equal(matchesEntity(event, skillA, "skill"), true);
  assert.equal(matchesEntity(event, skillB, "skill"), false);
  assert.equal(matchesEntity({...event, path: "/projects/a/.agents/skills/same-name-other/check.py"}, skillA, "skill"), false);
  assert.equal(matchesEntity({...event, path: "/projects/a/.agents/skills/same-name/../other/check.py"}, skillA, "skill"), false);
});
check("pathless scoped skills require the same project", () => {
  const event = {...start, path: null};
  assert.equal(matchesEntity(event, skillA, "skill"), true);
  assert.equal(matchesEntity(event, skillB, "skill"), false);
  assert.equal(matchesEntity(event, {...skillB, scope: "candidate"}, "skill"), false);
  assert.equal(matchesEntity({...event, project: null}, skillA, "skill"), false);
});
check("pathless personal skills can be used from any project", () => {
  assert.equal(matchesEntity({...start, path: null, project: "/elsewhere"}, {...skillA, scope: "personal", project_id: null}, "skill"), true);
});
check("Windows path separators and case do not split an identity", () => {
  const windowsSkill = {...skillA, path: "C:\\Projects\\A\\Skills\\same-name\\SKILL.md"};
  assert.equal(matchesEntity({...start, path: "c:/projects/a/skills/same-name/SKILL.md"}, windowsSkill, "skill"), true);
  assert.equal(matchesEntity({...start, path: "C:\\PROJECTS\\A\\Skills\\same-name\\scripts\\check.ps1"}, windowsSkill, "skill"), true);
});
check("agent roles sharing a directory do not inherit each other's script events", () => {
  const agent = {name: "reviewer", path: "/profile/agents/reviewer.toml"};
  assert.equal(matchesEntity({...start, kind: "agent", name: "reviewer", path: agent.path}, agent, "agent"), true);
  assert.equal(matchesEntity({...start, kind: "agent", name: "reviewer", path: "/profile/agents/builder.toml"}, agent, "agent"), false);
});
check("project roles honor project filtering and retain personal roles", () => {
  state([]);
  model.data.agents = [{name:"nexo-reviewer", path:"/personal/nexo-reviewer.toml", scope:"personal"}, {name:"nexo-reviewer", path:"/a/nexo-reviewer.toml", scope:"project", project_id:"/projects/a"}, {name:"nexo-reviewer", path:"/b/nexo-reviewer.toml", scope:"project", project_id:"/projects/b"}];
  model.project = "/projects/a";
  assert.equal(agents().length, 2);
  assert.ok(agents().every((agent) => agent.project_id !== "/projects/b"));
});
check("ambiguous pathless role reports do not attribute use to either same-name installation", () => {
  const event = {...start, kind:"agent", name:"nexo-reviewer", path:null};
  assert.ok(model.data.agents.every((agent) => !matchesEntity(event, agent, "agent")));
  const local = model.data.agents[1];
  assert.equal(matchesEntity({...event, path:local.path}, local, "agent"), true);
  assert.equal(matchesEntity({...event, path:local.path}, model.data.agents[0], "agent"), false);
});
check("installed extensions are not presented as Nexo base capabilities", () => {
  assert.equal(originLabel({origin:"installed"}), "Extensión instalada");
  assert.equal(originLabel({origin:"core"}), "Base de Nexo");
  assert.equal(originLabel({origin:"learned"}), "Aprendida");
  assert.equal(scopeLabel({scope:"project",project_id:"/projects/a"}), "Proyecto: a");
});
process.stdout.write(`\n${passed} dashboard behavior checks passed.\n`);
