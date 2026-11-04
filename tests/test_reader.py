import pymupdf
import pytest

from docflex.core.reader import (
    Target, detect_headings, link_at, outline, page_links, page_words, search, selection_text, word_at,
)


@pytest.fixture
def doc():
    d = pymupdf.open()
    page = d.new_page()
    page.insert_text((72, 100), "Premier paragraphe du texte", fontsize=14)
    page.insert_text((72, 130), "Seconde ligne ici", fontsize=14)
    rotated = d.new_page()
    rotated.insert_text((72, 100), "Texte tourné", fontsize=14)
    rotated.set_rotation(90)
    yield d
    d.close()


def test_words_selection_and_lines(doc):
    words = page_words(doc[0])
    assert [w.text for w in words] == ["Premier", "paragraphe", "du", "texte", "Seconde", "ligne", "ici"]
    assert selection_text(words, 5, 2) == "du texte\nSeconde ligne"  # ordre indifférent, lignes conservées
    assert word_at(words, words[1].rect.tl + (2, 2)) == 1           # sous le pointeur
    assert word_at(words, pymupdf.Point(words[6].rect.x1 + 10, words[6].rect.y0 + 5)) == 6  # juste à côté
    assert word_at(words, pymupdf.Point(500, 700)) is None          # loin de tout


def test_rotated_page_uses_displayed_coordinates(doc):
    page = doc[1]
    words = page_words(page)
    assert page.rect.width > page.rect.height  # affichée en paysage
    for word in words:
        assert word.rect in page.rect          # à l'intérieur de la page affichée
        assert word.rect.height > word.rect.width  # texte vertical à l'écran
    pix = page.get_pixmap(dpi=72)
    box = words[0].rect
    inked = [pix.pixel(int(x), int(y))[0] < 128 for x in range(int(box.x0), int(box.x1))
             for y in range(int(box.y0), int(box.y1))]
    assert any(inked)  # le cadre du mot recouvre bien son encre dans le rendu


def test_search_all_pages(doc):
    hits = search(doc, "TEXTE")
    assert [page for page, _ in hits] == [0, 1]
    assert hits[1][1] in doc[1].rect
    assert search(doc, "  ") == [] and search(doc, "absent") == []


def linked_doc() -> pymupdf.Document:
    d = pymupdf.open()
    for _ in range(3):
        d.new_page()
    d[0].insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(50, 100, 150, 120), "page": 2,
                      "to": pymupdf.Point(0, 300)})
    d[0].insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(50, 200, 150, 220), "uri": "https://example.com"})
    d.set_toc([[1, "Annexe", 3, {"kind": pymupdf.LINK_GOTO, "page": 2, "to": pymupdf.Point(0, 300)}],
               [2, "Haut de page", 2]])
    return pymupdf.open("pdf", d.tobytes())


def test_links_and_outline_targets():
    d = linked_doc()
    links = page_links(d[0])
    assert link_at(links, (60, 110)).target == Target(2, 300)
    assert link_at(links, (60, 210)).uri == "https://example.com" and link_at(links, (10, 10)) is None
    assert link_at(links, (60, 110)).description == "Aller à la page 3"
    entries = outline(d)
    assert [(e.level, e.title, e.target.page) for e in entries] == [(1, "Annexe", 2), (2, "Haut de page", 1)]
    assert entries[0].target == Target(2, 300)
    d[2].set_rotation(90)  # destination dans une page tournée : convertie dans le repère affiché
    assert outline(d)[0].target.page == 2 and outline(d)[0].target.y != 300


def word_like_pdf() -> pymupdf.Document:
    """Comme un document Word imprimé en PDF : titres en gros ou en gras, aucun signet."""
    d = pymupdf.open()
    body = "Le lecteur affiche ce texte de démonstration pour remplir la page."
    for chapter in (1, 2):
        page = d.new_page()
        y = 72
        page.insert_text((72, y), f"Chapitre {chapter}", fontsize=20, fontname="hebo")
        for section in (1, 2):
            y += 40
            page.insert_text((72, y), f"Section {chapter}.{section}", fontsize=14, fontname="hebo")
            for _ in range(8):
                y += 18
                page.insert_text((72, y), body, fontsize=11)
        page.insert_text((72, 820), "Rapport confidentiel", fontsize=14)  # pied de page répété
    return d


def test_detect_headings_from_formatting():
    d = word_like_pdf()
    found = [(e.level, e.title, e.target.page) for e in detect_headings(d)]
    assert found == [(1, "Chapitre 1", 0), (2, "Section 1.1", 0), (2, "Section 1.2", 0),
                     (1, "Chapitre 2", 1), (2, "Section 2.1", 1), (2, "Section 2.2", 1)]
    assert outline(d) == []
    plain = pymupdf.open()
    plain.new_page().insert_text((72, 72), "Rien que du texte ordinaire.", fontsize=11)
    assert detect_headings(plain) == []
