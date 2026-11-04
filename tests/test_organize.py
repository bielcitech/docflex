from pathlib import Path

import pymupdf
import pytest

from docflex.core.organize import (
    FULL, PageRef, blank_page, build_pdf, content_box, crop_pages, crop_pages_each, delete_pages, duplicate_pages,
    insert_blank, move_pages, pages_of, render_thumbnail, reverse_pages, rotate_box, rotate_pages,
)
from docflex.core.tasks import Cancelled
from tests.test_split_compress import make_pdf, page_texts


def test_reorder_rotate_delete_duplicate(tmp_path):
    src = make_pdf(tmp_path / "a.pdf", 4)
    p = pages_of(0, 4)
    pages = [p[3], p[0].rotated(90), p[0], p[2].rotated(-90)]  # page 2 supprimée, page 1 dupliquée
    out = build_pdf([src], pages, tmp_path / "out.pdf")
    assert page_texts(out) == ["Page 4", "Page 1", "Page 1", "Page 3"]
    with pymupdf.open(out) as doc:
        assert [page.rotation for page in doc] == [0, 90, 0, 270]


def test_rotation_adds_to_existing_rotation(tmp_path):
    src = tmp_path / "r.pdf"
    with pymupdf.open() as doc:
        doc.new_page().set_rotation(90)
        doc.save(src)
    out = build_pdf([src], [PageRef(0, 0).rotated(90).rotated(90).rotated(90)], tmp_path / "o.pdf")
    with pymupdf.open(out) as doc:
        assert doc[0].rotation == 0  # 90 d'origine + 270


def test_merge_several_sources(tmp_path):
    a = make_pdf(tmp_path / "a.pdf", 2)
    b = make_pdf(tmp_path / "b.pdf", 3)
    out = build_pdf([a, b], [PageRef(1, 2), PageRef(0, 0), PageRef(1, 0)], tmp_path / "o.pdf")
    assert page_texts(out) == ["Page 3", "Page 1", "Page 1"]


def test_bookmarks_follow_their_pages(tmp_path):
    toc = [[1, "Intro", 1], [2, "Détail", 2], [1, "Fin", 4]]
    src = make_pdf(tmp_path / "t.pdf", 4, toc=toc)
    p = pages_of(0, 4)
    out = build_pdf([src], [p[3], p[1], p[0]], tmp_path / "o.pdf")  # page 3 supprimée
    with pymupdf.open(out) as doc:
        assert doc.get_toc() == [[1, "Fin", 1], [2, "Détail", 2], [1, "Intro", 3]]
        assert doc.metadata["author"] == "XenoviaLabs"
    out = build_pdf([src], [p[1], p[0]], tmp_path / "o2.pdf")
    with pymupdf.open(out) as doc:
        # « Détail » (niveau 2) passe en tête : son niveau est ramené à 1 pour rester valide.
        assert doc.get_toc() == [[1, "Détail", 1], [1, "Intro", 2]]


def test_overwrite_source_in_place(tmp_path):
    src = make_pdf(tmp_path / "s.pdf", 3)
    build_pdf([src], list(reversed(pages_of(0, 3))), src)
    assert page_texts(src) == ["Page 3", "Page 2", "Page 1"]
    assert list(tmp_path.iterdir()) == [src]  # pas de fichier temporaire oublié


def test_empty_and_cancel(tmp_path):
    src = make_pdf(tmp_path / "s.pdf", 30)
    with pytest.raises(ValueError, match="aucune page"):
        build_pdf([src], [], tmp_path / "o.pdf")

    def progress(fraction, message):
        if fraction > 0:
            raise Cancelled

    with pytest.raises(Cancelled):
        build_pdf([src], pages_of(0, 30), tmp_path / "o.pdf", progress)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["s.pdf"]


def test_thumbnail_width(tmp_path):
    src = make_pdf(tmp_path / "s.pdf", 1)
    with pymupdf.open(src) as doc:
        pix = render_thumbnail(doc, 0, 100)
    assert pix.width == 100 and pix.n == 3


# ------------------------------------------------- opérations d'édition
P = pages_of(0, 5)  # pages 0 à 4


def idx(pages):
    return [p.index for p in pages]


@pytest.mark.parametrize("rows, target, order, selected", [
    ([0], 3, [1, 2, 0, 3, 4], [2]),          # vers la droite : « avant la page 3 »
    ([4], 0, [4, 0, 1, 2, 3], [0]),          # au tout début
    ([1, 3], 5, [0, 2, 4, 1, 3], [3, 4]),    # en bloc, à la fin, ordre conservé
    ([3, 1], 0, [1, 3, 0, 2, 4], [0, 1]),    # ordre de sélection indifférent
    ([2], 2, [0, 1, 2, 3, 4], [2]),          # déposée sur place : rien ne change
    ([2], 3, [0, 1, 2, 3, 4], [2]),          # juste après elle-même : idem
])
def test_move_pages(rows, target, order, selected):
    pages, sel = move_pages(P, rows, target)
    assert idx(pages) == order and sel == selected


def test_other_edits():
    pages, sel = rotate_pages(P, [1, 3], 90)
    assert [p.rotation for p in pages] == [0, 90, 0, 90, 0] and sel == [1, 3]
    pages, sel = delete_pages(P, [0, 4])
    assert idx(pages) == [1, 2, 3] and sel == []
    pages, sel = duplicate_pages(P, [1, 3])
    assert idx(pages) == [0, 1, 1, 2, 3, 3, 4] and sel == [2, 5]
    pages, sel = reverse_pages(P, [])
    assert idx(pages) == [4, 3, 2, 1, 0]
    pages, sel = reverse_pages(P, [0, 2, 3])
    assert idx(pages) == [3, 1, 2, 0, 4] and sel == [0, 2, 3]


def test_rotation_direction_matches_thumbnail_preview(tmp_path):
    """« Droite » = sens horaire, dans le PDF produit comme dans l'aperçu (QTransform.rotate(90))."""
    src = tmp_path / "bande.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.draw_rect(pymupdf.Rect(0, 0, page.rect.width, 100), color=None, fill=(1, 0, 0))  # bande en haut
        doc.save(src)
    out = build_pdf([src], [PageRef(0, 0).rotated(90)], tmp_path / "o.pdf")
    with pymupdf.open(out) as doc:
        pix = doc[0].get_pixmap(dpi=20)
    assert pix.width > pix.height
    assert pix.pixel(pix.width - 2, pix.height // 2) == (255, 0, 0)  # la bande est passée à droite
    assert pix.pixel(1, pix.height // 2) == (255, 255, 255)


# ------------------------------------------------------- recadrage
RED, GREEN, BLUE, YELLOW = (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)


def quadrant_pdf(path, rotation=0, cropbox=None):
    """Page 400×600 : rouge en haut à gauche, vert en haut à droite, bleu en bas à gauche, jaune en bas à droite."""
    with pymupdf.open() as doc:
        page = doc.new_page(width=400, height=600)
        for rect, color in [((0, 0, 200, 300), RED), ((200, 0, 400, 300), GREEN),
                            ((0, 300, 200, 600), BLUE), ((200, 300, 400, 600), YELLOW)]:
            page.draw_rect(pymupdf.Rect(rect), color=None, fill=[c / 255 for c in color])
        if cropbox:
            page.set_cropbox(pymupdf.Rect(cropbox))
        page.set_rotation(rotation)
        doc.save(path)
    return path


def shown_colors(path) -> set:
    with pymupdf.open(path) as doc:
        pix = doc[0].get_pixmap(dpi=24)
    w, h = pix.width, pix.height
    return {pix.pixel(x, y) for x in (2, w // 2, w - 3) for y in (2, h // 2, h - 3)}


def test_rotate_box_round_trip():
    box = (0.1, 0.2, 0.4, 0.9)
    assert rotate_box(box, 90) == pytest.approx((0.1, 0.1, 0.8, 0.4))
    for degrees in (90, 180, 270):
        assert rotate_box(rotate_box(box, degrees), -degrees) == pytest.approx(box)


def test_crop_simple(tmp_path):
    src = quadrant_pdf(tmp_path / "q.pdf")
    out = build_pdf([src], [PageRef(0, 0).cropped((0.5, 0, 1, 0.5))], tmp_path / "o.pdf")
    assert shown_colors(out) == {GREEN}
    with pymupdf.open(out) as doc:
        assert doc[0].rect.width == pytest.approx(200) and doc[0].rect.height == pytest.approx(300)


def test_crop_on_page_already_rotated_with_offset_cropbox(tmp_path):
    # Cropbox d'origine : seulement la moitié basse (bleu | jaune), puis page tournée de 90°.
    # Affichée : bleu en haut, jaune en bas. On garde la moitié haute affichée → bleu.
    src = quadrant_pdf(tmp_path / "q.pdf", rotation=90, cropbox=(0, 300, 400, 600))
    assert shown_colors(src) == {BLUE, YELLOW}
    out = build_pdf([src], [PageRef(0, 0).cropped((0, 0, 1, 0.5))], tmp_path / "o.pdf")
    assert shown_colors(out) == {BLUE}


def test_crop_drawn_after_user_rotation(tmp_path):
    src = quadrant_pdf(tmp_path / "q.pdf")
    # Tournée à droite, la page montre : bleu | rouge en haut, jaune | vert en bas.
    ref = PageRef(0, 0).rotated(90)
    out = build_pdf([src], [ref.cropped((0.5, 0, 1, 0.5))], tmp_path / "o.pdf")  # quart haut-droit vu à l'écran
    assert shown_colors(out) == {RED}
    assert ref.cropped((0.5, 0, 1, 0.5)).display_crop() == pytest.approx((0.5, 0, 1, 0.5))


def test_full_box_removes_crop_and_blank_ignores_it():
    ref = PageRef(0, 0).cropped((0.1, 0.1, 0.9, 0.9))
    assert ref.crop is not None and ref.cropped(FULL).crop is None
    assert blank_page(100, 200).cropped((0, 0, 0.5, 0.5)).crop is None


def test_crop_edits_and_blank_insert(tmp_path):
    pages = pages_of(0, 3)
    cropped, sel = crop_pages(pages, [0, 2], (0, 0, 0.5, 1))
    assert [p.crop for p in cropped] == [(0, 0, 0.5, 1), None, (0, 0, 0.5, 1)] and sel == [0, 2]
    each, _ = crop_pages_each(pages, {1: (0.2, 0.2, 0.8, 0.8), 2: None})
    assert [p.crop for p in each] == [None, (0.2, 0.2, 0.8, 0.8), None]
    with_blank, sel = insert_blank(pages, [0], (300, 400))
    assert with_blank[1].is_blank and sel == [1]
    assert insert_blank(pages, [], (300, 400))[0][-1].is_blank

    src = make_pdf(tmp_path / "a.pdf", 1)
    out = build_pdf([src], [pages[0], blank_page(300, 400)], tmp_path / "o.pdf")
    with pymupdf.open(out) as doc:
        assert doc.page_count == 2 and tuple(doc[1].rect)[2:] == (300, 400)
        assert doc[1].get_text().strip() == ""


def test_content_box_finds_margins(tmp_path):
    src = tmp_path / "m.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=400, height=600)
        page.draw_rect(pymupdf.Rect(100, 150, 300, 450), color=None, fill=(0, 0, 0))
        doc.new_page()
        doc.save(src)
    with pymupdf.open(src) as doc:
        box = content_box(doc, 0, margin=0)
        assert box == pytest.approx((0.25, 0.25, 0.75, 0.75), abs=0.01)
        assert content_box(doc, 1) is None  # page vide
