import time
from datetime import datetime

from docflex.core.recent import MAX_RECENT, RecentStore, when


def pdf(tmp_path, name, size=100):
    path = tmp_path / name
    path.write_bytes(b"x" * size)
    return path


def test_touch_order_favorites_and_persistence(tmp_path):
    store = RecentStore(tmp_path / "recent.json")
    a, b, c = pdf(tmp_path, "Alpha.pdf", 300), pdf(tmp_path, "beta.pdf", 100), pdf(tmp_path, "Gamma.pdf", 200)
    for path in (a, b, c):
        store.touch(path, 10)
        time.sleep(0.01)
    assert [e.name for e in store.entries()] == ["Gamma", "beta", "Alpha"]
    assert [e.name for e in store.entries(sort="name")] == ["Alpha", "beta", "Gamma"]
    assert [e.name for e in store.entries(sort="size")] == ["Alpha", "Gamma", "beta"]
    assert [e.name for e in store.entries(query="ALP")] == ["Alpha"]

    store.set_page(b, 7)
    store.toggle_favorite(str(b.resolve()))
    store.save()
    reloaded = RecentStore(tmp_path / "recent.json")
    entry = reloaded.get(b)
    assert entry.last_page == 7 and entry.favorite and entry.size == 100
    assert [e.name for e in reloaded.entries(favorites_only=True)] == ["beta"]

    reloaded.touch(b, 10)  # rouvert : page de reprise et favori conservés
    assert reloaded.get(b).last_page == 7 and reloaded.entries()[0].name == "beta"
    reloaded.touch(b, 5)   # document raccourci depuis : reprise au début
    assert reloaded.get(b).last_page == 0

    reloaded.clear()
    assert [e.name for e in reloaded.entries()] == ["beta"]  # les favoris restent
    reloaded.remove(str(b.resolve()))
    assert reloaded.entries() == []


def test_trim_keeps_favorites_and_missing_files(tmp_path):
    store = RecentStore(tmp_path / "recent.json")
    keep = pdf(tmp_path, "garder.pdf")
    store.touch(keep, 1)
    store.toggle_favorite(str(keep.resolve()))
    for i in range(MAX_RECENT + 5):
        store.touch(pdf(tmp_path, f"doc{i}.pdf"), 1)
    assert len(store.entries()) == MAX_RECENT + 1 and store.get(keep).favorite
    keep.unlink()
    assert not store.get(keep).exists


def test_corrupted_file_starts_empty(tmp_path):
    bad = tmp_path / "recent.json"
    bad.write_text("{pas du json")
    assert RecentStore(bad).entries() == []


def test_relative_dates():
    now = datetime(2026, 11, 3, 15, 0).timestamp()
    assert when(datetime(2026, 11, 3, 9, 5).timestamp(), now) == "Aujourd'hui, 09:05"
    assert when(datetime(2026, 11, 2, 22, 30).timestamp(), now) == "Hier, 22:30"
    assert when(datetime(2026, 10, 28).timestamp(), now) == "28 oct."
    assert when(datetime(2025, 2, 1).timestamp(), now) == "1 févr. 2025"
