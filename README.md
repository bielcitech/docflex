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

Structure :

| Dossier | Rôle |
|---|---|
| `docflex/core/` | Logique sans interface : `converters.py` (registre des conversions), `office.py` (LibreOffice) |
| `docflex/ui/` | Interface Qt : une vue par outil, widgets communs (`widgets.py`), thème clair/sombre (`theme.py`) |
| `docflex/utils/` | Chemins, tailles, ouverture de dossiers |

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

Lecteur PDF : défilement continu, zoom (largeur, page entière, 25–400 %, ⌘+molette, pincement),
miniatures et signets dans un panneau latéral, recherche (tous les résultats surlignés, ⌘F, Entrée
pour le suivant), sélection et copie de texte, PDF protégés (mot de passe demandé). « Ouvrir avec
DocFlex » depuis le Finder ou l'Explorateur.

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
image, blanc correcteur (efface vraiment ce qui est dessous). Formulaires : clic sur un champ
(surligné en bleu) pour le remplir. Annuler/rétablir commun avec les annotations (instantanés).

La barre de navigation se replie (poignée ‹ ou ⌃⌘S / Ctrl+Alt+S) ; son état et l'apparence sont
mémorisés d'un lancement à l'autre.

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

Les installeurs ne sont pas signés : au premier lancement, macOS demande un clic droit → Ouvrir,
et Windows SmartScreen affiche « Informations complémentaires → Exécuter quand même ».

## Icône

Dessin vectoriel dans `packaging/icon/build_icons.py` (source SVG écrite dans `packaging/icon/docflex.svg`).
`.venv/bin/python packaging/icon/build_icons.py` régénère l'icône macOS (`.icns`, via `iconutil`),
Windows (`.ico`) et celle de la fenêtre (`docflex/ui/assets/icon.png`).

## Licence

PyMuPDF et pdf2docx sont sous licence AGPL-3.0. Distribuer DocFlex impose soit de publier son code
source sous AGPL, soit d'acquérir une licence commerciale PyMuPDF auprès d'Artifex.
