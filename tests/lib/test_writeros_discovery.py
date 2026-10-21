import json

from lib.look_ingest import discover_writeros_package, writeros_package_for

PID = "00000000-1111-2222-3333-444444444444"


def world(tmp_path, monkeypatch, *, link_root=None, packages=1, field=None):
    story = tmp_path / "story"; (story / "wayfinder").mkdir(parents=True)
    library = tmp_path / "library"; library.mkdir()
    monkeypatch.setenv("WRITEROS_LIBRARY", str(library))
    links = {"version": 1, "links": {PID: {"root": str(link_root or story / "wayfinder"), "beatSheet": None}}}
    (library / ".writeros-story-drive-links.json").write_text(json.dumps(links))
    for n in range(packages):
        pkg = library / f"Test Film {n} (00000000).writeros"; pkg.mkdir()
        (pkg / "project.json").write_text(json.dumps({"projectId": PID, "title": "Test Film"}))
    other = library / "Other (99999999).writeros"; other.mkdir()
    (other / "project.json").write_text(json.dumps({"projectId": "other"}))
    film = tmp_path / "film"; film.mkdir()
    yaml_text = f"wayfinder_root: {story}\n" + (f"writeros_package: {field}\n" if field else "")
    (film / "project.yaml").write_text(yaml_text)
    return film, library


def test_finds_the_package_writeros_linked_to_the_story_drive_folder(tmp_path, monkeypatch):
    film, library = world(tmp_path, monkeypatch)
    assert writeros_package_for(film) == (library / "Test Film 0 (00000000).writeros").resolve()


def test_no_link_to_this_folder_means_no_package(tmp_path, monkeypatch):
    film, _ = world(tmp_path, monkeypatch, link_root=tmp_path / "elsewhere" / "wayfinder")
    assert discover_writeros_package(film) is None


def test_two_matching_packages_is_ambiguous(tmp_path, monkeypatch):
    film, _ = world(tmp_path, monkeypatch, packages=2)
    assert discover_writeros_package(film) is None


def test_the_settings_field_wins(tmp_path, monkeypatch):
    other = tmp_path / "Chosen (12345678).writeros"; other.mkdir()
    film, _ = world(tmp_path, monkeypatch, field=other)
    assert writeros_package_for(film) == other.resolve()


def test_film_without_story_drive_folder_finds_nothing(tmp_path, monkeypatch):
    film, _ = world(tmp_path, monkeypatch)
    (film / "project.yaml").write_text("title: x\n")
    assert writeros_package_for(film) is None
