import pymupdf
import pytest

from docflex.core.annotations import (
    AnnotSpec, annot_at, create, describe, markup_quads, page_specs, recreate, remove, supported_annots,
)
from docflex.core.reader import page_words

RED, BLUE = (0.9, 0.1, 0.1), (0.1, 0.3, 0.9)


def text_page(rotation=0):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Une phrase importante à surligner", fontsize=16)
    page.insert_text((72, 130), "et sa suite sur la ligne suivante", fontsize=16)
    page.set_rotation(rotation)
    return doc, page


def specs_for(page):
    words = page_words(page)
    quads = markup_quads([w.rect for w in words[1:9]])
    w = page.rect.width
    return [
        AnnotSpec("highlight", rect=(0, 0, 0, 0), color=(1, 0.85, 0.1), points=quads),
        AnnotSpec("underline", rect=(0, 0, 0, 0), color=BLUE, points=quads[:1]),
        AnnotSpec("strikeout", rect=(0, 0, 0, 0), color=RED, points=quads[1:]),
        AnnotSpec("note", rect=(w - 80, 200, w - 60, 220), color=(1, 0.85, 0.1), text="À vérifier"),
        AnnotSpec("text", rect=(80, 300, 330, 340), color=BLUE, text="Zone de texte", fontsize=14),
        AnnotSpec("rect", rect=(60, 380, 260, 460), color=RED, width=3, opacity=0.7, text="Commentaire de forme"),
        AnnotSpec("ellipse", rect=(300, 380, 420, 460), color=BLUE, width=2),
        AnnotSpec("arrow", rect=(0, 0, 0, 0), color=RED, width=2, points=(((60, 500), (250, 560)),)),
        AnnotSpec("line", rect=(0, 0, 0, 0), color=BLUE, width=1, points=(((300, 500), (400, 500)),)),
        AnnotSpec("ink", rect=(0, 0, 0, 0), color=BLUE, width=3,
                  points=(((80, 600), (120, 640), (160, 600)), ((200, 600), (240, 650)))),
    ]


def close(a, b, tol=1.5):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


@pytest.mark.parametrize("rotation", [0, 90])
def test_create_and_capture_round_trip(rotation):
    doc, page = text_page(rotation)
    wanted = specs_for(page)
    for spec in wanted:
        create(page, spec)
    got = page_specs(page)
    assert [s.kind for s in got] == [s.kind for s in wanted]
    for want, have in zip(wanted, got):
        assert close(want.color, have.color, 0.02), want.kind
        assert want.text == have.text
        if want.kind in ("rect", "ellipse", "text"):
            assert close(want.rect, have.rect), (want.kind, want.rect, have.rect)
        if want.kind in ("arrow", "line", "ink", "highlight"):
            for a_stroke, b_stroke in zip(want.points, have.points):
                for a, b in zip(a_stroke, b_stroke):
                    assert close(a, b, 2), (want.kind, a, b)
        if want.kind == "text":
            assert have.fontsize == 14
        if want.kind in ("rect", "ellipse", "arrow", "line", "ink"):
            assert have.width == pytest.approx(want.width)
    assert got[5].opacity == pytest.approx(0.7, abs=0.01)


def test_highlight_is_drawn_over_the_words_on_rotated_page():
    doc, page = text_page(90)
    words = page_words(page)
    create(page, AnnotSpec("highlight", rect=(0, 0, 0, 0), color=(1, 1, 0), points=markup_quads([words[1].rect])))
    pix = page.get_pixmap(dpi=72)
    r = words[1].rect
    yellow = sum(1 for x in range(int(r.x0), int(r.x1)) for y in range(int(r.y0), int(r.y1))
                 if pix.pixel(x, y)[2] < 80 and pix.pixel(x, y)[0] > 200)
    assert yellow > r.width * r.height * 0.3  # le jaune recouvre bien le mot affiché


def test_recreate_touches_only_one_annotation():
    doc, page = text_page()
    page.add_polygon_annot([(50, 50), (100, 50), (75, 90)])  # type non géré par DocFlex
    other = create(page, AnnotSpec("note", rect=(10, 10, 30, 30), text="Ne pas toucher"))
    shape = create(page, AnnotSpec("rect", rect=(100, 300, 200, 400), color=RED))
    other_xref = other.xref
    (_, spec), = [(a, s) for a, s in supported_annots(page) if s.kind == "rect"]
    xref = shape.xref
    for _ in range(5):  # déplacements successifs : la forme ne grossit pas
        spec = spec.moved(10, 4)
        xref = recreate(page, xref, spec)
    rects = [s.rect for s in page_specs(page) if s.kind == "rect"]
    assert len(rects) == 1 and close(rects[0], (150, 320, 250, 420), 0.5)
    assert page.load_annot(other_xref).info["content"] == "Ne pas toucher"  # même objet, intact
    assert "Polygon" in [a.type[1] for a in page.annots()]
    remove(page, xref)
    assert [s.kind for s in page_specs(page)] == ["note"]


def test_hit_testing_and_description():
    doc, page = text_page()
    for spec in specs_for(page):
        create(page, spec)
    specs = page_specs(page)
    assert specs[annot_at(page, (100, 420))].kind == "rect"
    assert specs[annot_at(page, (360, 420))].kind == "ellipse"
    assert annot_at(page, (500, 780)) is None
    assert describe(page, specs[0]) == "Surlignage — « phrase importante à surligner et sa suite sur »"
    assert describe(page, specs[3]) == "Note — « À vérifier »"
    assert not specs[0].movable and specs[5].movable


def test_annotations_survive_save(tmp_path):
    doc, page = text_page()
    create(page, AnnotSpec("text", rect=(80, 300, 330, 340), color=BLUE, text="Bonjour", fontsize=14))
    out = tmp_path / "a.pdf"
    doc.save(out)
    with pymupdf.open(out) as reopened:
        spec = page_specs(reopened[0])[0]
        assert spec.kind == "text" and spec.text == "Bonjour" and spec.fontsize == 14


def test_history_add_modify_delete_undo_redo():
    from docflex.core.annotations import Change, History, xref_at

    doc, page = text_page()
    page.add_polygon_annot([(50, 50), (100, 50), (75, 90)])  # jamais touchée
    history = History()
    shape = AnnotSpec("rect", rect=(100, 300, 200, 400), color=RED)
    history.do(doc, Change(0, None, shape))
    moved = shape.moved(30, 0)
    history.do(doc, Change(0, shape, moved, xref_at(doc[0], 0)))
    recolored = AnnotSpec("rect", rect=moved.rect, color=BLUE)
    history.do(doc, Change(0, moved, recolored, xref_at(doc[0], 0)))
    history.do(doc, Change(0, recolored, None, xref_at(doc[0], 0)))  # suppression
    assert page_specs(doc[0]) == []

    history.undo(doc)
    assert close(page_specs(doc[0])[0].color, BLUE, 0.02)
    history.undo(doc)
    history.undo(doc)
    assert close(page_specs(doc[0])[0].rect, (100, 300, 200, 400), 0.5)
    history.undo(doc)
    assert page_specs(doc[0]) == [] and history.undo(doc) is None
    for _ in range(4):
        history.redo(doc)
    assert page_specs(doc[0]) == [] and history.redo(doc) is None
    history.undo(doc)
    history.do(doc, Change(0, None, AnnotSpec("note", rect=(10, 10, 30, 30), text="n")))
    assert history.redo_stack == []  # une nouvelle action efface « rétablir »
    assert "Polygon" in [a.type[1] for a in doc[0].annots()]


def test_fit_textbox_grows_to_show_all_text():
    from docflex.core.annotations import fit_textbox

    small = (50, 50, 250, 70)
    assert fit_textbox((50, 50, 250, 90), "Court", 12) == (50, 50, 250, 90)  # assez haute : inchangée
    long_text = "Une remarque assez longue pour devoir passer à la ligne plusieurs fois dans la zone."
    x0, y0, x1, y1 = fit_textbox(small, long_text + "\nDeuxième paragraphe", 12)
    assert (x0, y0, x1) == (50, 50, 250) and y1 - y0 >= 4 * 12 * 1.35
    doc, page = text_page()
    create(page, AnnotSpec("text", rect=(x0, y0, x1, y1), text=long_text + "\nDeuxième paragraphe", fontsize=12))
    pix = page.get_pixmap(dpi=72, clip=pymupdf.Rect(x0, y1 - 18, x1, y1))
    assert any(pix.pixel(x, y)[0] < 100 for x in range(pix.width) for y in range(pix.height))  # dernière ligne visible


def test_history_mixes_annotations_and_content_snapshots():
    from docflex.core.annotations import Change, History, Snapshot
    from docflex.core.editing import replace_line, text_lines

    state = {}
    doc, page = text_page()

    def restore(data):
        state["doc"] = pymupdf.open("pdf", data)

    history = History(restore)
    history.do(doc, Change(0, None, AnnotSpec("rect", rect=(100, 300, 200, 400), color=RED)))
    before = doc.tobytes()
    replace_line(doc[0], text_lines(doc[0])[0], "Texte corrigé")
    history.record(Snapshot(0, before, doc.tobytes()))
    history.do(doc, Change(0, None, AnnotSpec("note", rect=(10, 10, 30, 30), text="n")))

    history.undo(doc)                      # la note
    history.undo(doc)                      # la correction : document restauré
    restored = state["doc"]
    assert "Une phrase importante" in restored[0].get_text()
    assert [s.kind for s in page_specs(restored[0])] == ["rect"]
    history.undo(restored)                 # le rectangle, dans le document restauré
    assert page_specs(restored[0]) == []
    history.redo(restored)
    history.redo(restored)                 # la correction revient
    assert "Texte corrigé" in state["doc"][0].get_text()
    assert [s.kind for s in page_specs(state["doc"][0])] == ["rect"]
