import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class Kit:
    id: str
    title: str
    status: str
    generation: Path | None = None

    def public(self):
        return {"id": self.id, "title": self.title, "status": self.status}

    def sources(self):
        canon = (self.generation / "canon.md").read_text(encoding="utf-8")
        outline = self.optional("outline.json", {})
        looks = self.optional("looks.json", {}).get("looks", [])
        return canon, outline, looks

    def optional(self, name, default):
        try:
            return read_json(self.generation / name)
        except FileNotFoundError:
            return default


def find_kits(root: Path):
    kits = []
    for folder in root.glob("*.writeros"):
        if not folder.is_dir():
            continue
        title = re.sub(r" \([^()]+\)$", "", folder.stem)
        fallback_id = hashlib.sha256(folder.name.encode()).hexdigest()[:20]
        exports = folder / "memory/exports/studio-kit"
        try:
            state = read_json(exports / "state.json")
            status = state["status"]
            if status not in ("current", "pending", "failed", "no-canon"):
                raise ValueError
        except FileNotFoundError:
            kits.append(Kit(fallback_id, title, "missing"))
            continue
        except (OSError, ValueError, KeyError, TypeError):
            kits.append(Kit(fallback_id, title, "unreadable"))
            continue

        name = state.get("generation")
        project_id = fallback_id
        generation = None
        if status != "no-canon" and isinstance(name, str) and re.fullmatch(r"gen-\d+-[0-9a-f]{12}", name):
            generation = exports / name
            try:
                meta = read_json(generation / "kit.json")
                project_id = str(UUID(meta["project_id"]))
                title = meta["project_title"]
                if not isinstance(title, str) or not title.strip():
                    raise ValueError
            except FileNotFoundError:
                if status == "current":
                    status = "missing"
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                if status == "current":
                    status = "unreadable"
                title = re.sub(r" \([^()]+\)$", "", folder.stem)
        elif status == "current":
            status = "unreadable"
        kits.append(Kit(project_id, title, status, generation))
    return sorted(kits, key=lambda kit: kit.title.casefold())
