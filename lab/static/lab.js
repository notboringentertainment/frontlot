const app = document.querySelector("#app");
const back = document.querySelector("#back");
let shows = [];
let show = null;
let selected = null;
let draft = null;
let adding = null;
let busy = "";
let message = "";
let limitMessage = "";
let limitDraft = null;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "class") node.className = value;
    else if (value === true) node.setAttribute(key, "");
    else if (value !== false && value != null) node.setAttribute(key, value);
  }
  node.append(...children.flat().filter(child => child != null));
  return node;
}

function button(label, action, style = "quiet", disabled = Boolean(busy)) {
  return el("button", {type: "button", class: style, onclick: action, disabled}, label);
}

async function request(path, method = "GET", data) {
  const options = {method};
  if (data instanceof File) {
    options.body = data;
  } else if (data !== undefined) {
    options.headers = {"Content-Type": "application/json"};
    options.body = JSON.stringify(data);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new Error("The lab lost its connection. Try opening it again.");
  }
  if (!response.ok) {
    const failure = await response.json().catch(() => ({}));
    const error = new Error(typeof failure.detail === "string"
      ? failure.detail : "The lab couldn’t save that change. Check what you entered and try again.");
    error.status = response.status;
    throw error;
  }
  return response.status === 204 ? null : response.json();
}

function projectPath() {
  return "/api/shows/" + show.id;
}

function entryPath() {
  return projectPath() + "/entries/" + selected;
}

function select(entry) {
  selected = entry?.id ?? null;
  draft = entry ? {name: entry.name, description: entry.description} : null;
  adding = null;
  message = "";
  limitMessage = "";
  render();
}

async function run(status, work) {
  if (busy) return;
  busy = status;
  message = "";
  render();
  try {
    await work();
  } catch (error) {
    message = error.message;
  } finally {
    busy = "";
    render();
  }
}

async function readCanon(again = false) {
  const previous = show.entries.find(item => item.id === selected);
  show = await request(projectPath() + "/canon" + (again ? "?again=true" : ""), "POST");
  if (selected) {
    const entry = show.entries.find(item => item.id === selected);
    if (!entry) select(null);
    else if (draft.description === previous?.description) draft.description = entry.description;
  }
}

async function enter(project) {
  await run("Opening the show…", async () => {
    show = await request("/api/shows/" + project.id);
    busy = "Reading canon…";
    render();
    await readCanon();
  });
}

async function home() {
  if (busy) return;
  show = null;
  limitDraft = null;
  select(null);
  await run("Finding shows…", async () => { shows = await request("/api/shows"); });
}

const unavailable = {
  "no-canon": "No canon in WriterOS yet",
  pending: "WriterOS is preparing the canon",
  failed: "WriterOS couldn’t prepare the canon",
  missing: "No kit from WriterOS yet",
  unreadable: "The lab couldn’t open this kit",
};

function showList() {
  return el("section", {class: "show-list"},
    el("h1", {}, "Your shows"),
    el("div", {class: "show-rows"}, shows.map(project => {
      const available = project.status === "current";
      return el("button", {
        class: "show-row", disabled: !available || Boolean(busy), onclick: () => enter(project),
      },
      el("span", {class: "show-name"}, project.title),
      el("span", {class: "show-caption"}, available ? "Open show" : unavailable[project.status]));
    })),
    !shows.length && !busy && !message ? el("p", {class: "muted"}, "No shows yet. Publish a kit from WriterOS to get started.") : null,
    feedback(busy || message));
}

function dollars(amount) {
  const number = Number(amount);
  return new Intl.NumberFormat("en-US", {style: "currency", currency: "USD"}).format(number);
}

function budget() {
  const input = el("input", {type: "number", inputmode: "decimal", min: 0, step: "0.01", required: true,
    id: "limit", value: limitDraft ?? show.limit,
    oninput: event => { limitDraft = event.target.value; }});
  const total = el("p", {class: "total"});
  const notice = feedback(limitMessage);
  const project = projectPath();
  function showTotal() {
    total.textContent = dollars(show.spent) + (show.estimated ? " spent (estimated)" : " spent");
  }
  showTotal();
  const form = el("form", {class: "budget-form", onsubmit: async event => {
    event.preventDefault();
    const amount = input.value;
    try {
      const saved = await request(project, "PATCH", {limit: amount});
      if (show && project === projectPath()) {
        show.limit = saved.limit;
        if (limitDraft === amount) limitDraft = null;
        Object.assign(show, {spent: saved.spent, estimated: saved.estimated});
        limitMessage = "";
        showTotal();
        notice.textContent = "";
      }
    } catch (error) {
      if (show && project === projectPath()) notice.textContent = error.message;
    }
  }},
  el("label", {for: "limit"}, "Spending limit"),
  el("span", {class: "money-field"}, el("span", {"aria-hidden": "true"}, "$"), input),
  el("button", {class: "quiet", type: "submit"}, "Set limit"));
  return el("aside", {class: "budget"}, total, form, notice);
}

function feedback(text, className = "feedback") {
  return el("p", {class: className, role: "status"}, text || "");
}

function group(kind) {
  const people = kind === "person";
  const entries = show.entries.filter(entry => entry.kind === kind);
  return el("section", {class: "entry-group"},
    el("div", {class: "group-heading"}, el("h2", {}, people ? "People" : "Places"),
      button(people ? "Add person" : "Add place", () => {
        adding = adding === kind ? null : kind;
        render();
        document.querySelector("#new-name")?.focus();
      })),
    adding === kind ? addForm(kind) : null,
    entries.length ? el("ul", {class: "entries"}, entries.map(entry => {
      const main = entry.pictures.find(picture => picture.main);
      const row = button(entry.name, () => select(entry),
        entry.id === selected ? "entry-button active" : "entry-button");
      if (entry.id === selected) row.setAttribute("aria-current", "true");
      if (main) row.prepend(el("img", {src: main.url, alt: ""}));
      return el("li", {}, row);
    })) : el("p", {class: "muted"}, people ? "No people yet." : "No places yet."));
}

function addForm(kind) {
  const name = el("input", {id: "new-name", name: "name", required: true});
  return el("form", {class: "add-form", onsubmit: event => {
    event.preventDefault();
    const value = name.value.trim();
    if (!value) {
      name.focus();
      return;
    }
    run("Adding…", async () => {
      const entry = await request(projectPath() + "/entries", "POST", {kind, name: value});
      show.entries.push(entry);
      select(entry);
    });
  }}, el("label", {for: "new-name"}, "Name"), name,
  el("button", {type: "submit", class: "primary", disabled: Boolean(busy)}, "Add"),
  button("Cancel", () => { adding = null; render(); }));
}

function replaceEntry(entry) {
  const index = show.entries.findIndex(item => item.id === entry.id);
  show.entries[index] = entry;
}

async function saveEdits() {
  const entry = await request(entryPath(), "PATCH", draft);
  replaceEntry(entry);
  draft = {name: entry.name, description: entry.description};
}

async function make() {
  limitMessage = "";
  await run("Making pictures…", async () => {
    await saveEdits();
    try {
      const entry = await request(entryPath() + "/make", "POST", {description: draft.description});
      replaceEntry(entry);
      Object.assign(show, {spent: entry.spent, estimated: entry.estimated, limit: entry.limit});
    } catch (error) {
      if (error.status === 409) {
        limitMessage = error.message;
      } else {
        show = await request(projectPath());
        throw error;
      }
    }
  });
}

function pictureCard(picture, index, entry) {
  function change(fields, status) {
    run(status, async () => {
      const saved = await request(picture.url, "PATCH", fields);
      if (saved.main) entry.pictures.forEach(item => { item.main = false; });
      Object.assign(picture, saved);
    });
  }
  return el("figure", {class: "picture" + (picture.main ? " main-picture" : "")},
    el("a", {href: picture.url, target: "_blank", rel: "noopener", class: "still"},
      el("img", {src: picture.url, alt: entry.name + ", picture " + (index + 1), loading: "lazy"})),
    el("figcaption", {},
      button(picture.kept ? "Unkeep" : "Keep", () => change({kept: !picture.kept}, "Saving…"),
        picture.kept ? "quiet kept" : "quiet"),
      picture.main ? el("span", {class: "main-label"}, "Main look")
        : picture.kept ? button("Set as main look", () => change({main: true}, "Saving…")) : null));
}

function editor(entry) {
  const name = el("input", {id: "entry-name", value: draft.name, required: true,
    oninput: event => { draft.name = event.target.value; }});
  const description = el("textarea", {id: "description", rows: 3,
    oninput: event => { draft.description = event.target.value; }}, draft.description);
  const file = el("input", {type: "file", accept: "image/*", hidden: true, onchange: event => {
    const picture = event.target.files[0];
    if (picture) run("Importing picture…", async () => {
      entry.pictures.push(await request(entryPath() + "/import", "POST", picture));
    });
  }});
  const form = el("form", {class: "description-form", onsubmit: event => {
    event.preventDefault();
    run("Saving…", saveEdits);
  }},
  el("div", {class: "editor-fields"},
    el("label", {for: "entry-name"}, "Name"), name,
    el("label", {for: "description"}, "Visual description"), description),
  entry.source ? el("p", {class: "source"}, entry.source) : null,
  entry.quotes.length ? el("details", {class: "quotes"}, el("summary", {}, "Read the lines from canon"),
    entry.quotes.map(quote => el("blockquote", {}, quote))) : null,
  el("div", {class: "actions"},
    button("Make pictures", make, "primary"),
    el("button", {type: "submit", class: "quiet", disabled: Boolean(busy)}, "Save changes"),
    entry.pictures.some(picture => picture.main) ? el("span", {class: "muted"}, "New pictures use the main look.") : null));
  return el("article", {class: "editor"},
    el("div", {class: "editor-heading"}, el("h2", {}, entry.name),
      button("Remove entry", () => run("Removing…", async () => {
        await request(entryPath(), "DELETE");
        show.entries = show.entries.filter(item => item.id !== entry.id);
        select(null);
      }), "quiet subdued")),
    form,
    el("div", {class: "gallery-heading"},
      el("h3", {}, "Pictures"),
      el("div", {class: "actions"},
        button("Import a picture", () => file.click()), file,
        button("Show in Finder", () => run("Opening Finder…", () => request(entryPath() + "/finder", "POST"))))),
    feedback(busy || message),
    entry.pictures.length ? el("div", {class: "gallery " + entry.kind},
      entry.pictures.map((picture, index) => pictureCard(picture, index, entry)).reverse())
      : !busy ? el("p", {class: "muted empty-pictures"}, "Make pictures or bring in one you already have.") : null);
}

function workspace() {
  const entry = show.entries.find(item => item.id === selected);
  return el("div", {class: "workspace"},
    el("div", {class: "show-heading"},
      el("div", {class: "show-title"}, el("h1", {}, show.title),
        button("Read canon again", () => run("Reading canon…", () => readCanon(true)))),
      budget()),
    feedback(show.notice || (!entry ? busy || message : ""), "canon-notice"),
    el("div", {class: entry ? "work-columns" : "overview"},
      el("div", {class: "directory"},
        entry ? button("People and places", () => select(null), "quiet directory-back") : null,
        group("person"), group("place")),
      entry ? editor(entry) : null));
}

function render() {
  back.hidden = !show;
  back.disabled = Boolean(busy);
  document.querySelector("#home").disabled = Boolean(busy);
  app.replaceChildren(show ? workspace() : showList());
}

document.querySelector("#home").addEventListener("click", home);
back.addEventListener("click", home);
home();
