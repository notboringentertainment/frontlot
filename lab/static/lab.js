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
let audioStatus = {};
let creditsPoll = null;
let audioFeedback = false;
let voiceOpen = false;
let libraryVoices = null;
let librarySample = null;
let soundDrafts = freshSoundDrafts();
let shotDraft = freshShot();
let footageMessage = "";
let footagePoll = null;
const descriptionRequired = "Write a description before saving or making pictures.";

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
  draft = entry ? {name: entry.name, description: entry.description, scene: "",
    voice: entry.voice_description, line: ""} : null;
  voiceOpen = false;
  libraryVoices = librarySample = null;
  audioFeedback = false;
  adding = null;
  message = "";
  limitMessage = "";
  render();
}

async function run(status, work, audio = false) {
  if (busy) return;
  clearTimeout(footagePoll);
  footagePoll = null;
  busy = status;
  audioFeedback = audio;
  message = "";
  render();
  try {
    await work();
  } catch (error) {
    message = error.message;
  } finally {
    busy = "";
    render();
    pollFootage();
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
    sessionStorage.setItem("lab-show", project.id);
    busy = "Reading canon…";
    render();
    await Promise.all([readCanon(), refreshCredits()]);
  });
}

async function home(restore = false) {
  if (busy) return;
  stopCreditsPoll();
  const lastShow = restore === true ? sessionStorage.getItem("lab-show") : null;
  sessionStorage.removeItem("lab-show");
  shotDraft = freshShot();
  footageMessage = "";
  show = null;
  limitDraft = null;
  audioStatus = {};
  soundDrafts = freshSoundDrafts();
  select(null);
  await run("Finding shows…", async () => { shows = await request("/api/shows"); });
  const project = shows.find(item => item.id === lastShow && item.status === "current");
  if (project) await enter(project);
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
    feedback(busy || message, Boolean(message)));
}

function dollars(amount) {
  const number = Number(amount);
  return new Intl.NumberFormat("en-US", {style: "currency", currency: "USD"}).format(number);
}

function spendingText() {
  return dollars(show.spent) + (show.estimated ? " spent (estimated)" : " spent");
}

function budget() {
  const input = el("input", {type: "number", inputmode: "decimal", min: 0, step: "0.01", required: true,
    id: "limit", value: limitDraft ?? show.limit,
    oninput: event => { limitDraft = event.target.value; }});
  const total = el("p", {class: "total"});
  const notice = feedback(limitMessage, true);
  const project = projectPath();
  total.textContent = spendingText();
  const form = el("form", {class: "budget-form", onsubmit: async event => {
    event.preventDefault();
    const amount = input.value;
    try {
      const saved = await request(project, "PATCH", {limit: amount});
      if (show && project === projectPath()) {
        show.limit = saved.limit;
        if (limitDraft === amount) {
          limitDraft = null;
          document.querySelector("#limit").value = saved.limit;
        }
        limitMessage = "";
        notice.textContent = "";
      }
    } catch (error) {
      if (show && project === projectPath()) notice.textContent = error.message;
    }
  }},
  el("label", {for: "limit"}, "Spending limit"),
  el("span", {class: "money-field"}, el("span", {"aria-hidden": "true"}, "$"), input),
  el("button", {class: "quiet", type: "submit"}, "Set limit"));
  return el("aside", {class: "budget"}, total, form, notice,
    el("p", {class: "credits muted"}, creditsText()),
    feedback(audioStatus.notice));
}

function feedback(text, error = false) {
  return el("p", {class: error ? "feedback error" : "feedback", role: "status"}, text || "");
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
  const description = el("textarea", {id: "new-description", rows: 2, "aria-describedby": "new-description-help",
    oninput: () => {
      submit.disabled = !description.value.trim();
      help.hidden = !submit.disabled;
    }});
  const submit = el("button", {type: "submit", class: "primary", disabled: true}, "Add");
  const help = el("p", {id: "new-description-help", class: "muted"}, descriptionRequired);
  return el("form", {class: "add-form", onsubmit: event => {
    event.preventDefault();
    const value = name.value.trim();
    if (!value) {
      name.focus();
      return;
    }
    const words = description.value.trim();
    if (!words) return;
    run("Adding…", async () => {
      const entry = await request(projectPath() + "/entries", "POST", {kind, name: value, description: words});
      show.entries.push(entry);
      select(entry);
    });
  }}, el("label", {for: "new-name"}, "Name"), name,
  el("label", {for: "new-description"}, kind === "person" ? "Who this person is" : "What this place is"),
  description, help, submit,
  button("Cancel", () => { adding = null; render(); }));
}

function replaceEntry(entry) {
  const index = show.entries.findIndex(item => item.id === entry.id);
  show.entries[index] = entry;
}

async function saveEdits() {
  const {name, description} = draft;
  const entry = await request(entryPath(), "PATCH", {name, description});
  replaceEntry(entry);
  Object.assign(draft, {name: entry.name, description: entry.description});
}

async function make() {
  limitMessage = "";
  await run("Making pictures…", async () => {
    await saveEdits();
    try {
      const {description, scene} = draft;
      const entry = await request(entryPath() + "/make", "POST", {description, scene});
      draft.scene = "";
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
      picture.scene ? el("p", {class: "picture-caption"}, picture.scene) : null,
      button(picture.kept ? "Unkeep" : "Keep", () => change({kept: !picture.kept}, "Saving…"),
        picture.kept ? "quiet kept" : "quiet"),
      picture.main ? el("span", {class: "main-label"}, "Main look")
        : picture.kept ? button("Set as main look", () => change({main: true}, "Saving…")) : null));
}

function editor(entry) {
  const editingDisabled = Boolean(busy);
  const missingDescription = !draft.description.trim();
  const name = el("input", {id: "entry-name", value: draft.name, required: true, disabled: editingDisabled,
    oninput: event => { draft.name = event.target.value; }});
  const description = el("textarea", {id: "description", rows: 3, disabled: editingDisabled,
    "aria-describedby": "description-help", oninput: event => {
      draft.description = event.target.value;
      const empty = !draft.description.trim();
      makeButton.disabled = saveButton.disabled = empty;
      help.hidden = !empty;
    }}, draft.description);
  const scene = el("textarea", {id: "scene", rows: 2, disabled: editingDisabled,
    oninput: event => { draft.scene = event.target.value; }}, draft.scene);
  const makeButton = button("Make pictures", make, "primary", editingDisabled || missingDescription);
  const saveButton = el("button", {type: "submit", class: "quiet",
    disabled: editingDisabled || missingDescription}, "Save changes");
  const help = el("p", {id: "description-help", class: "description-help muted", hidden: !missingDescription},
    descriptionRequired);
  const file = el("input", {type: "file", accept: "image/*", hidden: true, onchange: event => {
    const picture = event.target.files[0];
    if (picture) run("Importing picture…", async () => {
      entry.pictures.push(await request(entryPath() + "/import", "POST", picture));
    });
  }});
  const form = el("form", {class: "description-form", onsubmit: event => {
    event.preventDefault();
    if (draft.description.trim()) run("Saving…", saveEdits);
  }},
  el("div", {class: "editor-fields"},
    el("label", {for: "entry-name"}, "Name"), name,
    el("label", {for: "description"}, entry.kind === "person" ? "Who this person is" : "What this place is"), description,
    el("label", {for: "scene"}, "This picture (optional)"), scene),
  help,
  entry.source ? el("p", {class: "source"}, entry.source) : null,
  entry.quotes.length ? el("details", {class: "quotes"}, el("summary", {}, "Read the lines from canon"),
    entry.quotes.map(quote => el("blockquote", {}, quote))) : null,
  el("div", {class: "actions"},
    makeButton, saveButton,
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
    !audioFeedback ? feedback(busy || message, Boolean(message)) : null,
    entry.pictures.length ? el("div", {class: "gallery " + entry.kind},
      entry.pictures.map((picture, index) => pictureCard(picture, index, entry)).reverse())
      : !busy ? el("p", {class: "muted empty-pictures"}, "Make pictures or bring in one you already have.") : null,
    entry.kind === "person" ? voiceSection(entry) : null);
}

function freshSoundDrafts() {
  return {effects: {text: "", seconds: ""}, music: {text: "", seconds: "10"}};
}

function creditsArePartial() {
  return audioStatus.credits_partial ?? show.credits_partial;
}

function creditsText() {
  const used = audioStatus.credits_used ?? show.credits_used;
  const remaining = audioStatus.remaining;
  const partial = creditsArePartial();
  const prefix = partial ? "At least " : "";
  const count = partial && used === 0 ? "Counting ElevenLabs credits…"
    : prefix + used.toLocaleString() + " ElevenLabs credits used";
  return count + (remaining == null ? "" : " · " + remaining.toLocaleString() + " left on the account");
}

function stopCreditsPoll() {
  clearTimeout(creditsPoll?.timer);
  creditsPoll = null;
}

function pollCredits(path, poll, deadline) {
  if (!creditsArePartial()) return;
  poll.timer = setTimeout(async () => {
    if (Date.now() > deadline) return;
    const status = await request(path).catch(() => null);
    if (creditsPoll !== poll) return;
    if (status) {
      audioStatus = status;
      app.querySelector(".credits").textContent = creditsText();
    }
    pollCredits(path, poll, deadline);
  }, 10000);
}

async function refreshCredits() {
  stopCreditsPoll();
  const poll = creditsPoll = {timer: null};
  const path = projectPath() + "/audio/credits";
  try {
    const status = await request(path);
    if (creditsPoll === poll) audioStatus = status;
  } finally {
    if (creditsPoll === poll) pollCredits(path, poll, Date.now() + 180000);
  }
}

async function audioRun(status, work, charged = false) {
  let play;
  await run(status, async () => {
    try {
      play = await work();
    } finally {
      if (charged) await refreshCredits();
    }
  }, true);
  if (play) {
    const player = [...document.querySelectorAll("audio")].find(node => node.getAttribute("src") === play);
    player?.play().catch(() => {});
  }
}

function audioNotice() {
  return audioFeedback ? feedback(busy || (message === audioStatus.notice ? "" : message), Boolean(message)) : null;
}

function player(clip, label) {
  return el("audio", {src: clip.url, controls: true, preload: "none", "aria-label": label});
}

function audioCards(clips) {
  return el("div", {class: "audio-grid"}, [...clips].reverse().map(clip =>
    el("figure", {class: "audio-card"},
      el("p", {class: "picture-caption"}, clip.title),
      player(clip, clip.title),
      button(clip.kept ? "Unkeep" : "Keep", () => audioRun("Saving…", async () => {
        Object.assign(clip, await request(clip.url, "PATCH", {kept: !clip.kept}));
      }), clip.kept ? "quiet kept" : "quiet"))));
}

function importAudio(path, clips) {
  const file = el("input", {type: "file", accept: "audio/*,.m4a,.aif,.aiff,.opus", hidden: true, onchange: event => {
    const audio = event.target.files[0];
    if (audio) audioRun("Importing audio…", async () => {
      clips.push(await request(path + "/import?filename=" + encodeURIComponent(audio.name), "POST", audio));
    });
  }});
  return [button("Import audio", () => file.click()), file];
}

function audioFinder() {
  return button("Show in Finder", () => audioRun("Opening Finder…", () =>
    request(projectPath() + "/audio/finder", "POST")));
}

function audioText(id, label, value, change) {
  return [el("label", {for: id}, label),
    el("textarea", {id, rows: 2, disabled: Boolean(busy), oninput: event => change(event.target.value)}, value)];
}

function voiceSection(entry) {
  if (!voiceOpen) {
    return el("section", {class: "audio-section"},
      button("Voice", () => {
        voiceOpen = true;
        if (entry.voice_description) {
          render();
        } else {
          audioRun("Drafting a voice from canon…", async () => {
            const saved = await request(entryPath() + "/voice/draft", "POST");
            entry.voice_description = draft.voice = saved.description;
            entry.voice_drafted = saved.drafted;
          });
        }
      }, "quiet voice-button"),
      entry.voice ? el("span", {class: "muted"}, entry.voice.name + " is this person’s voice.") : null);
  }

  function pick(fields) {
    audioRun("Saving the voice…", async () => {
      entry.voice = await request(entryPath() + "/voice/pick", "POST", fields);
    });
  }

  const takesPath = entryPath() + "/takes";
  return el("section", {class: "audio-section", id: "voice-section"},
    el("div", {class: "gallery-heading"}, el("h3", {}, "Voice"),
      el("div", {class: "actions"}, ...importAudio(takesPath, entry.takes), audioFinder())),
    audioNotice(),
    entry.voice ? el("div", {class: "audio-form"},
      el("p", {}, el("strong", {}, entry.voice.name), " is this person’s voice."),
      ...audioText("dialogue", "Line of dialogue", draft.line, value => { draft.line = value; }),
      button("Read the line", () => audioRun("Reading the line…", async () => {
        const clip = await request(takesPath, "POST", {text: draft.line});
        entry.takes.push(clip);
        return clip.url;
      }, true), "primary")) : null,
    audioCards(entry.takes),
    el("div", {class: "audio-form"},
      ...audioText("voice-description", "Describe the voice", draft.voice, value => {
        draft.voice = value;
        document.querySelector("#voice-source").hidden = value.trim() !== entry.voice_description || !entry.voice_drafted;
      }),
      el("p", {id: "voice-source", class: "muted", hidden: !entry.voice_drafted ||
        draft.voice.trim() !== entry.voice_description}, "Drafted from canon"),
      el("div", {class: "actions"},
        button("Make voice previews", () => audioRun("Making voice previews…", async () => {
          entry.voice_previews = await request(entryPath() + "/voice/design", "POST", {text: draft.voice});
          if (entry.voice_description !== draft.voice.trim()) entry.voice_drafted = false;
          entry.voice_description = draft.voice.trim();
        }, true), "primary"),
        button(libraryVoices ? "Hide library voices" : "Browse library voices", () => {
          if (libraryVoices) {
            libraryVoices = librarySample = null;
            render();
          } else {
            audioRun("Finding library voices…", async () => {
              libraryVoices = await request(projectPath() + "/audio/voices");
            });
          }
        })),
      entry.voice_previews.length ? el("div", {class: "audio-grid"},
        entry.voice_previews.map((preview, index) => el("figure", {class: "audio-card"},
          el("p", {class: "picture-caption"}, "Preview " + (index + 1)),
          player(preview, "Voice preview " + (index + 1)),
          button("Use this voice", () => pick({preview: preview.id}))))) : null,
      libraryVoices ? libraryList(pick) : null));
}

function libraryList(pick) {
  return el("div", {class: "library-voices"},
    el("h3", {}, "Library voices"),
    libraryVoices.length ? libraryVoices.map(voice => el("div", {class: "library-voice"},
      el("span", {}, voice.name),
      librarySample?.voice === voice.id ? player(librarySample, voice.name) :
        button(voice.sample ? "Hear sample" : "Make sample", () => audioRun("Opening the sample…", async () => {
          librarySample = {...await request(entryPath() + "/voice/listen", "POST", {voice_id: voice.id}), voice: voice.id};
          return librarySample.url;
        }, !voice.sample)),
      button("Use this voice", () => pick({voice_id: voice.id})))) : el("p", {class: "muted"}, "No library voices yet."));
}

function soundSection() {
  return el("section", {class: "audio-section sound-section"},
    el("div", {class: "gallery-heading"}, el("h2", {}, "Sound"), audioFinder()),
    audioNotice(),
    el("div", {class: "sound-columns"}, soundForm("effects"), soundForm("music")));
}

function soundForm(kind) {
  const music = kind === "music";
  const path = projectPath() + "/sound/" + kind;
  const clips = show.sound[kind] ??= [];
  const fields = soundDrafts[kind];
  const length = el("input", {type: "number", id: kind + "-seconds", min: music ? 3 : 0.5, max: music ? 600 : 30,
    step: "any", value: fields.seconds, required: music, disabled: Boolean(busy),
    oninput: event => { fields.seconds = event.target.value; }});
  const make = button(music ? "Make music" : "Make sound effect", () => {
    if (!length.reportValidity()) return;
    audioRun(music ? "Making music…" : "Making a sound effect…", async () => {
      const clip = await request(path, "POST", {text: fields.text, seconds: fields.seconds ? Number(fields.seconds) : null});
      clips.push(clip);
      return clip.url;
    }, true);
  }, "primary");
  return el("div", {class: "audio-form"},
    el("h3", {}, music ? "Music" : "Sound effects"),
    ...audioText(kind + "-description", music ? "Describe the music" : "Describe the sound", fields.text,
      value => { fields.text = value; }),
    el("div", {class: "actions"}, el("label", {for: length.id}, music ? "Seconds" : "Seconds (optional)"), length),
    el("div", {class: "actions"}, make,
      ...importAudio(path, clips)),
    music ? el("p", {class: "muted music-note"},
      "Music from ElevenLabs is not cleared for a piece shown to buyers unless ElevenLabs confirms it in writing.") : null,
    audioCards(clips));
}

function freshShot() {
  return {description: "", seconds: "5", picture: null, chooserOpen: false};
}

function footageAction(status, work) {
  return run(status, async () => {
    footageMessage = "";
    try {
      await work();
    } catch (error) {
      footageMessage = error.message;
    }
  });
}

function footageStatus() {
  return footageMessage || show.footage.notice ||
    (show.footage.pending ? "Making your footage. You can keep working while it’s being made." : "");
}

function pollFootage() {
  if (footagePoll || !show?.footage.pending) return;
  const path = projectPath() + "/footage/check";
  const timer = footagePoll = setTimeout(async () => {
    const saved = await request(path, "POST").catch(() => null);
    if (footagePoll !== timer) return;
    footagePoll = null;
    if (saved) {
      const {footage, spent, estimated} = saved;
      const changed = JSON.stringify(show.footage.shots) !== JSON.stringify(footage.shots);
      Object.assign(show, {footage, spent, estimated});
      app.querySelector(".total").textContent = spendingText();
      const status = app.querySelector("#footage-status");
      if (status) status.textContent = footageStatus();
      if (changed) app.querySelector("#footage-shots")?.replaceWith(footageShots());
    }
    pollFootage();
  }, 3000);
}

function footageShots() {
  return el("div", {id: "footage-shots"}, show.footage.shots.length ?
    [...show.footage.shots].reverse().map(shot => el("section", {class: "shot"},
      el("h3", {}, shot.title),
      el("div", {class: "footage-grid"}, shot.clips.map((clip, index) =>
        el("figure", {class: "audio-card"},
          el("video", {src: clip.url, controls: true, playsinline: true, preload: "metadata",
            "aria-label": shot.title + " Take " + (index + 1)}),
          el("figcaption", {},
            el("p", {class: "picture-caption"}, "Take " + (index + 1), clip.from ? " · From: " + clip.from : ""),
            button(clip.kept ? "Unkeep" : "Keep", () => footageAction("Saving…", async () => {
              Object.assign(clip, await request(clip.url, "PATCH", {kept: !clip.kept}));
            }), clip.kept ? "quiet kept" : "quiet")))))))
    : el("p", {class: "muted"}, "Make a clip or bring in one you already have."));
}

function footageSection() {
  const path = projectPath() + "/footage";
  const pictures = show.entries.flatMap(entry => entry.pictures.filter(picture => picture.kept)
    .map(picture => ({...picture, name: entry.name}))).sort((a, b) => Number(b.main) - Number(a.main));
  const startingPicture = pictures.find(picture => picture.id === shotDraft.picture);
  const price = el("span", {class: "muted"}, clipPrice());
  function clipPrice() {
    return "About " + dollars(show.footage.prices[shotDraft.seconds]) + " a clip";
  }

  const length = el("select", {id: "shot-length", disabled: Boolean(busy), onchange: event => {
    shotDraft.seconds = event.target.value;
    price.textContent = clipPrice();
  }}, Object.keys(show.footage.prices).map(seconds =>
    el("option", {value: seconds, selected: seconds === shotDraft.seconds}, seconds + " seconds")));

  const chooser = el("div", {class: "starting-pictures", hidden: !shotDraft.chooserOpen},
    button("Words alone", () => choose(null), startingPicture ? "quiet" : "quiet kept"),
    pictures.map(picture => el("button", {type: "button", class: "quiet" + (picture === startingPicture ? " kept" : ""),
      disabled: Boolean(busy), onclick: () => choose(picture.id)},
      el("img", {src: picture.url, alt: ""}), el("span", {}, picture.name),
      picture.main ? el("small", {class: "muted"}, "Main look") : null)));
  const chooseButton = button(startingPicture ? "Starting picture: " + startingPicture.name : "Choose a starting picture",
    () => {
      shotDraft.chooserOpen = !shotDraft.chooserOpen;
      chooser.hidden = !shotDraft.chooserOpen;
      chooseButton.setAttribute("aria-expanded", String(shotDraft.chooserOpen));
    });
  chooseButton.setAttribute("aria-expanded", String(shotDraft.chooserOpen));
  function choose(picture) {
    shotDraft.picture = picture;
    shotDraft.chooserOpen = false;
    render();
    app.querySelector(".starting-picture button").focus();
  }

  const file = el("input", {type: "file", accept: ".mp4,.mov,.webm", hidden: true, onchange: event => {
    const clip = event.target.files[0];
    if (clip) footageAction("Importing a clip…", async () => {
      await request(path + "/import?filename=" + encodeURIComponent(clip.name), "POST", clip);
      show.footage = (await request(projectPath())).footage;
    });
  }});
  const makeClip = button("Make a clip", () =>
    footageAction("Starting your clip…", async () => {
      const saved = await request(path, "POST", {description: shotDraft.description, seconds: Number(shotDraft.seconds),
        picture: startingPicture?.id ?? null});
      Object.assign(show, saved);
    }), "primary", Boolean(busy) || !shotDraft.description.trim());

  const status = feedback(footageStatus(), Boolean(footageMessage));
  status.id = "footage-status";
  return el("section", {class: "audio-section", id: "footage"},
    el("div", {class: "gallery-heading"}, el("h2", {}, "Footage"),
      el("div", {class: "actions"},
        button("Import a clip", () => file.click()), file,
        button("Show in Finder", () => footageAction("Opening Finder…", () => request(path + "/finder", "POST"))))),
    el("div", {class: "audio-form shot-form"},
      pictures.length ? el("div", {class: "starting-picture"}, chooseButton,
        startingPicture ? el("img", {src: startingPicture.url, alt: ""}) : null, chooser) : null,
      ...audioText("shot-description", "What happens in the shot", shotDraft.description, value => {
        shotDraft.description = value;
        makeClip.disabled = !value.trim();
      }),
      el("div", {class: "actions"}, el("label", {for: length.id}, "Length"), length),
      el("div", {class: "actions"}, makeClip, price),
      status),
    footageShots());
}

function workspace() {
  const entry = show.entries.find(item => item.id === selected);
  return el("div", {class: "workspace"},
    el("div", {class: "show-heading"},
      el("div", {class: "show-title"}, el("h1", {}, show.title),
        button("Read canon again", () => run("Reading canon…", () => readCanon(true)))),
      budget()),
    show.notice ? el("p", {class: "canon-notice error", role: "status"}, show.notice) : null,
    !entry && !audioFeedback ? feedback(busy || message, Boolean(message)) : null,
    el("div", {class: entry ? "work-columns" : "overview"},
      el("div", {class: "directory"},
        entry ? button("People and places", () => select(null), "quiet directory-back") : null,
        group("person"), group("place")),
      entry ? editor(entry) : null),
    !entry ? soundSection() : null,
    !entry ? footageSection() : null);
}

function render() {
  back.hidden = !show;
  back.disabled = Boolean(busy);
  document.querySelector("#home").disabled = Boolean(busy);
  app.replaceChildren(show ? workspace() : showList());
}

document.querySelector("#home").addEventListener("click", home);
back.addEventListener("click", home);
home(true);
