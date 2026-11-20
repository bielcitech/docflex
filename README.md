# DocFlex

Outil de bureau hors ligne pour convertir et manipuler des documents (PDF, images, Excel, CSV, Word…).
Interface PySide6 (Qt), moteur PyMuPDF. Fonctionne sur macOS et Windows.

## Développement

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt     # Windows : .venv\Scripts\pip
.venv/bin/python main.py                          # lancer l'application
.venv/bin/python -m pytest -q                     # moteur + interface (sans fenêtre visible)
```

Les tests (plus de 200) pilotent la vraie interface avec pytest-qt, sans fenêtre visible. `conftest.py`
isole les réglages et l'historique dans un dossier temporaire (`DOCFLEX_SETTINGS_FILE`,
`DOCFLEX_DATA_DIR`) : lancer les tests ne touche ni aux documents récents ni aux préférences.

Structure :

| Dossier | Rôle |
|---|---|
| `docflex/core/` | Logique sans interface, testable seule : `converters.py` (registre des conversions), `office.py` (LibreOffice), `split.py`, `organize.py`, `compress.py`, `pdf_tools.py` et `pdf_extras.py` (outils PDF), `reader.py` (mots, recherche, liens, table des matières), `annotations.py` (annotations et historique), `editing.py` (texte, images, champs de formulaire), `forms.py` (calculs des formulaires), `recent.py` (documents récents), `tasks.py` (progression, rapports) |
| `docflex/ui/` | Interface Qt : une vue par menu (`*_view.py`) ; lecteur en onglets (`reader_view.py`, un `document_tab.py` par document, rendu dans `reader_canvas.py`) ; barres flottantes (`floating.py`), saisie dans la page (`text_editor.py`, `field_editor.py`), impression (`printing.py`) ; widgets communs (`widgets.py`), thème clair/sombre (`theme.py`) |
| `docflex/utils/` | Chemins, tailles, ouverture de dossiers |
| `packaging/` | Installeurs macOS et Windows, icône |
| `video/` | Vidéo de démonstration (Remotion + ElevenLabs) : voir `video/README.md` |

Ajouter une conversion : écrire une fonction `(src, dest, opts, progress) -> list[Path]`
dans `converters.py` et l'inscrire dans `CONVERSIONS`. L'interface la propose automatiquement.

## Conversions disponibles

| Source | Cibles |
|---|---|
| PDF | PNG, JPG, DOCX, TXT |
| Images (PNG, JPG, BMP, GIF, TIFF, WebP) | PDF (un ou plusieurs, format image ou A4), autres formats d'image |
| XLSX | CSV (séparateur, décimales, encodage, une ou toutes les feuilles) |
| CSV | XLSX (détection du séparateur et de l'encodage) |
| EPUB, XPS, FB2, MOBI, CBZ, SVG, TXT | PDF |
| DOC/DOCX/ODT/RTF, XLS/XLSX/ODS, PPT/PPTX/ODP | PDF et formats bureautiques équivalents — **nécessite LibreOffice** |

## Lire et annoter

Accueil : documents récents et favoris en cartes (miniature, page de reprise, taille) ou en liste,
recherche, tri, menu « ⋯ » (ouvrir, afficher dans le dossier, favori, retirer). Un document rouvert
reprend à la dernière page lue. Historique et miniatures restent sur la machine (dossier de données
de l'application).

Lecteur PDF : un onglet par document (rouvrir un document déjà ouvert ramène à son onglet ; le
point « • » signale des modifications non enregistrées), défilement continu, zoom (largeur, page
entière, 25–400 %, ⌘+molette, pincement), sélection et copie de texte, PDF protégés (mot de passe
demandé). « Ouvrir avec DocFlex » depuis le Finder ou l'Explorateur ; plusieurs PDF à la fois avec ⌘O.

Barres d'outils flottantes, posées sur le document qui défile dessous : en haut la navigation, le
zoom, la recherche, l'impression et l'enregistrement ; en bas les outils du mode Annoter ou
Modifier. Elles glissent hors champ pour lire tranquille (bouton en haut à droite ou ⌃⌘T /
Ctrl+Alt+T, réglage mémorisé). La recherche sort en tiroir sous la barre du haut (⌘F, Entrée pour
le résultat suivant, tous les résultats surlignés). Les messages s'affichent en bulle au-dessus de
la barre du bas puis s'effacent. Si la fenêtre est trop étroite, le panneau latéral s'efface pour
laisser la barre du bas entière.

Panneau latéral (onglets en icônes) : miniatures des pages, table des matières, liste des
annotations. La table des matières reprend les signets du PDF (un document Word exporté avec
« Créer des signets à l'aide des titres », ou converti par DocFlex) et mène à la position exacte de
chaque titre. Sans signets (Word « imprimé » en PDF), les titres sont devinés d'après la mise en
forme — corps du texte, tailles plus grandes, gras isolé — à la première ouverture de l'onglet.
Les liens des pages sont cliquables : vers une page du document, ou vers le navigateur et la
messagerie pour une adresse externe.

Impression (⌘P, icône de la barre du haut ou menu ⋯) : dialogue du système (aperçu, imprimante,
copies, recto verso), tout le document, une plage ou la page courante. On imprime ce qui est
affiché, annotations, champs remplis et modifications non enregistrées compris ; chaque page garde
son orientation, réduite si elle dépasse la zone imprimable, jamais agrandie (rendu à 300 ppp au
plus).

Annotations (vraies annotations PDF, lisibles dans Acrobat, Foxit, Aperçu…) : surligner, souligner,
barrer (texte sélectionné ou glissé), notes, zones de texte, rectangle, ellipse, ligne, flèche, dessin
à main levée ; couleur (6 teintes ou au choix), épaisseur, opacité, taille du texte. Sélection,
déplacement, recoloration, commentaire (double-clic), suppression ; annuler/rétablir illimité (200
étapes) ; liste des annotations. Enregistrement incrémental (⌘S) ou « Enregistrer sous… ». Les
annotations venant d'autres logiciels ne sont jamais modifiées à l'insu de l'utilisateur.
Édition du contenu (mode « Modifier »), saisie directement dans la page, comme une machine à
écrire : modifier une ligne de texte (clic sur la ligne, curseur à l'endroit cliqué ; mise en forme
d'origine gardée — position, police, taille, couleur, gras, italique — sauf si on la change dans la
barre, pour cette ligne seulement ; police d'origine si elle contient les caractères, sinon police
standard proche — corrections ponctuelles, pas de recomposition de paragraphes), ajouter du texte
(clic, puis frappe ; police, taille, couleur, gras et italique de la barre, visibles pendant la
saisie, au zoom courant ; Entrée passe à la ligne, ⌘ Entrée ou un clic ailleurs valide, Échap
annule), déplacer du texte (avec « Modifier le texte », glisser une ligne déplace son paragraphe :
réécrit à la nouvelle place, chaque fragment avec sa police, sa taille et sa couleur), ajouter une
image, blanc correcteur (efface vraiment ce qui est dessous). Annuler/rétablir commun avec les
annotations (instantanés).

Formulaires : la saisie se fait directement dans le champ (surligné en bleu), au zoom courant.
Tab / ⇧Tab passent au champ suivant ou précédent dans l'ordre de lecture, Entrée valide, Échap
abandonne ; Espace ou un clic cochent une case ; les listes s'ouvrent sur place ; un clic ailleurs
et ⌘S gardent la saisie en cours. Boutons radio en groupe (une seule option cochée, même sur
plusieurs pages). Calculs automatiques recalculés à chaque saisie, pour les formes que produit
Acrobat sans programmation : somme, moyenne, produit, minimum, maximum (`AFSimple_Calculate`) et
notation simplifiée (`Prix * Quantité`) ; nombres saisis à la française ou à l'anglaise ;
résultat mis en forme (décimales, séparateurs, devise, pourcentage) ; calculs en chaîne. Les
champs calculés passent en lecture seule et Tab les saute ; les scripts JavaScript écrits à la
main sont ignorés (le champ garde sa valeur).
Créer un formulaire (mode Modifier) : champ texte, case à cocher, bouton radio, liste déroulante ;
on trace le champ, ou un clic le crée à une taille usuelle. Un nom unique est proposé (deux champs
de même nom partageraient leur valeur). Clic droit sur un champ : remplir, renommer, calcul
automatique (opération, champs sources, décimales ; écrit au format d'Acrobat, qui le reconnaît),
supprimer.

La barre de navigation se replie (poignée ‹ ou ⌃⌘S / Ctrl+Alt+S) ; son état et l'apparence sont
mémorisés d'un lancement à l'autre.

Raccourcis du lecteur (⌘ = Ctrl sous Windows) :

| Raccourci | Action |
|---|---|
| ⌘O | Ouvrir un ou plusieurs PDF |
| ⌘W | Fermer l'onglet |
| ⌃Tab / ⌃⇧Tab (Windows : Ctrl+Tab / Ctrl+Maj+Tab) | Onglet suivant / précédent |
| ⌃⌘T (Windows : Ctrl+Alt+T) | Afficher / masquer les barres d'outils |
| ⌘F, puis Entrée | Rechercher, puis résultat suivant |
| ⌘G, ⇧⌘G (Windows : F3, Maj+F3) | Résultat suivant / précédent |
| ⌘+, ⌘−, ⌘0 | Zoom avant, arrière, ajuster à la largeur |
| ⌘C | Copier le texte sélectionné |
| ⌘S | Enregistrer |
| ⌘P | Imprimer |
| ⌘Z, ⇧⌘Z (Windows : Ctrl+Z, Ctrl+Y) | Annuler, rétablir |
| Suppr, ⌫ | Supprimer l'annotation sélectionnée |
| Échap | Revenir à l'outil Sélection, abandonner une saisie |
| Tab, ⇧Tab | Champ de formulaire suivant / précédent |

## Découper un PDF

Par plages (`1-3, 4-6, 7-` : un fichier par plage), extraction (`1, 3, 5-8` : un seul fichier, dans
l'ordre saisi), une page par fichier, toutes les N pages, ou par signets de premier niveau.
L'aperçu liste les fichiers avant de lancer. Plusieurs fichiers produits sont rangés dans
`<nom> (découpé)/`.

## Organiser les pages

Grille de miniatures : glisser-déposer pour réordonner, rotation (↺ ↻), recadrage manuel (double-clic
sur une page), rognage automatique des marges blanches, duplication, page blanche, suppression,
inversion de l'ordre, ajout des pages d'autres PDF (glissés dans la grille). Annuler/Rétablir illimité
(200 étapes). Le recadrage passe par la CropBox : rien n'est détruit. Les signets suivent leurs pages.

## Outils PDF

Une grille de cartes donne accès aux douze outils.


| Outil | Ce qu'il fait | Résultat |
|---|---|---|
| Fusionner | Assemble plusieurs PDF dans l'ordre choisi (▲▼, tri par nom), un signet par fichier | fichier choisi à l'enregistrement |
| Filigrane | Texte (diagonale ou horizontale, couleur) ou image/logo (taille, 5 positions) ; intensité au choix ; toutes les pages ou une sélection | `nom_filigrane.pdf` |
| Numéroter | `1`, `Page 1`, `Page 1 sur 12`, `1 / 12` ; 5 positions ; premier numéro ; couverture non numérotée | `nom_numéroté.pdf` |
| Mot de passe | Protège (AES-256, droits d'impression/copie/modification) ou retire la protection | `nom_protégé.pdf` / `nom_déverrouillé.pdf` |
| OCR | Ajoute une couche de texte invisible aux PDF numérisés : sélection et recherche possibles, aspect inchangé | `nom_ocr.pdf` |
| Images | Extrait chaque image une seule fois ; JPEG copiés sans perte, le reste en PNG (transparence comprise) | dossier `nom_images/` |
| Métadonnées | Modifie titre, auteur, sujet, mots-clés… ; en lot, seuls les champs remplis s'appliquent ; effacement complet (XMP compris) avant partage | `nom_métadonnées.pdf` |
| Signer | Appose l'image d'une signature (fond rendu transparent) à l'endroit choisi sur l'aperçu, ou en paraphe sur toutes les pages — signature visuelle, pas électronique certifiée | `nom_signé.pdf` |
| Caviarder | Supprime définitivement des mots et, au choix, e-mails, téléphones, IBAN (texte retiré du fichier, pas seulement recouvert) | `nom_caviardé.pdf` |
| Comparer | Surligne les retraits (rouge) et ajouts (vert) entre deux versions, sur tout le document | `nom_différences.pdf` ×2 |
| Pages blanches | Retire les pages vides d'un scan ; une page portant du texte n'est jamais retirée | `nom_sans_pages_blanches.pdf` |
| Aplatir | Intègre formulaires et annotations au contenu des pages | `nom_aplati.pdf` |

L'OCR utilise le moteur Tesseract intégré à PyMuPDF et les données de langue embarquées
(`docflex/resources/tessdata`, français et anglais, licence Apache 2.0) : rien à installer.

## Compresser

PDF, JPG, PNG, WebP, en trois niveaux :

| Niveau | PDF | Images |
|---|---|---|
| Légère | Sans perte : structure nettoyée, polices allégées | JPG sans perte ajoutée, PNG optimisé |
| Recommandée | Images au-delà de 200 ppp ramenées à 150 ppp, JPEG qualité 75 | Plus grand côté ≤ 2560 px |
| Forte | Images au-delà de 120 ppp ramenées à 96 ppp, JPEG qualité 55 | ≤ 1600 px, PNG en 256 couleurs |

Option niveaux de gris pour les PDF. Le résultat s'appelle `<nom>_compressé` ; s'il ne fait pas
gagner au moins 1 %, il n'est pas créé.

LibreOffice (gratuit) n'est pas embarqué : il pèse plus de 500 Mo. DocFlex le détecte au démarrage
et active les conversions bureautiques s'il est installé.

## Installeurs

PyInstaller ne compile pas d'un système à l'autre : chaque installeur se construit sur son OS.

- **macOS** : `./packaging/macos/build_dmg.sh` → `dist/DocFlex-<version>-macos-<arch>.dmg`.
  L'architecture suit celle de la machine (arm64 sur Apple Silicon, x86_64 sur Intel).
- **Windows** : installer [Inno Setup 6](https://jrsoftware.org/isinfo.php), puis
  `powershell -ExecutionPolicy Bypass -File packaging\windows\build_installer.ps1`
  → `dist\DocFlex-<version>-windows-setup.exe`.
- **Les trois d'un coup** : le workflow `.github/workflows/build.yml` (GitHub Actions), lancé à la main
  ou en poussant un tag `v*`.

Chaque script exécute `DocFlex --selftest <dossier>` sur l'application empaquetée avant de produire
l'installeur. Les modules optionnels de PyMuPDF (IA de mise en page, ~100 Mo) sont exclus
de l'installeur ; `DOCFLEX_BLOCK_OPTIONAL=1 pytest` vérifie que rien n'en dépend.

Signature sur macOS : `build_dmg.sh` signe l'application (hardened runtime) avec le premier
certificat « Developer ID Application » du trousseau, ou celui de `DOCFLEX_SIGN_IDENTITY`. Il fait
ensuite notariser et agrafer l'application puis le DMG, avec le profil notarytool
`docflex-notary` (ou `DOCFLEX_NOTARY_PROFILE`), à créer une fois :
`xcrun notarytool store-credentials docflex-notary --apple-id <e-mail> --team-id <ÉQUIPE>`.
Sans certificat, le DMG est produit non signé : au premier lancement, macOS demande un clic droit
→ Ouvrir. L'installeur Windows n'est pas signé : SmartScreen affiche « Informations
complémentaires → Exécuter quand même ».

## Icône

Dessin vectoriel dans `packaging/icon/build_icons.py` (source SVG écrite dans `packaging/icon/docflex.svg`).
`.venv/bin/python packaging/icon/build_icons.py` régénère l'icône macOS (`.icns`, via `iconutil`),
Windows (`.ico`) et celle de la fenêtre (`docflex/ui/assets/icon.png`).

## Licence

DocFlex est un logiciel libre distribué sous licence **GNU AGPL-3.0 ou ultérieure** (fichier
[`LICENSE`](LICENSE)), comme PyMuPDF et pdf2docx qu'il utilise. Vous pouvez l'utiliser, l'étudier, le
modifier et le redistribuer selon ses termes ; toute version modifiée distribuée doit l'être sous la
même licence, avec son code source.

Téléchargement des installeurs : [xenotools.pro/outils/docflex](https://www.xenotools.pro/outils/docflex)
ou les [releases GitHub](https://github.com/bielcitech/docflex/releases).
