# Données de langue Tesseract

`fra.traineddata` et `eng.traineddata` proviennent du projet Tesseract OCR
(https://github.com/tesseract-ocr/tessdata_fast), sous licence Apache 2.0.
Le moteur OCR lui-même est intégré à PyMuPDF.

Pour ajouter une langue : déposer son fichier `<code>.traineddata` ici et
l'ajouter à `OCR_LANGUAGES` dans `docflex/core/pdf_tools.py`.
