#!/usr/bin/env bash
# Construit dist/DocFlex.app puis l'image disque dist/DocFlex-<version>-macos-<arch>.dmg.
# À lancer depuis la racine du projet, environnement virtuel présent dans .venv.
#
# Signature et notarisation Apple (automatiques si possible) :
#   - identité : DOCFLEX_SIGN_IDENTITY, sinon le premier certificat « Developer ID Application » du trousseau ;
#   - notarisation : profil notarytool DOCFLEX_NOTARY_PROFILE (par défaut « docflex-notary »), créé une fois avec
#       xcrun notarytool store-credentials docflex-notary --apple-id <e-mail> --team-id <ÉQUIPE>
#   Sans certificat Developer ID, le DMG est produit non signé (avertissement de macOS au premier lancement).
set -euo pipefail

cd "$(dirname "$0")/../.."
PY="${PYTHON:-.venv/bin/python}"
VERSION="$("$PY" -c 'from docflex import __version__; print(__version__)')"
ARCH="$(uname -m)"
DMG="dist/DocFlex-${VERSION}-macos-${ARCH}.dmg"
APP="dist/DocFlex.app"
PROFILE="${DOCFLEX_NOTARY_PROFILE:-docflex-notary}"

if [[ -z "${DOCFLEX_SIGN_IDENTITY:-}" ]]; then
    DOCFLEX_SIGN_IDENTITY="$(security find-identity -v -p codesigning \
        | sed -n 's/.*"\(Developer ID Application:[^"]*\)".*/\1/p' | head -1)"
fi
export DOCFLEX_SIGN_IDENTITY
if [[ -n "$DOCFLEX_SIGN_IDENTITY" ]]; then
    echo "Signature : $DOCFLEX_SIGN_IDENTITY"
else
    echo "Aucun certificat « Developer ID Application » : application NON signée."
fi

"$PY" -m PyInstaller docflex.spec --noconfirm --log-level WARN

# Vérifie l'application empaquetée (et signée, avec le hardened runtime) avant de la distribuer.
CHECK="$(mktemp -d)"
if ! "$APP/Contents/MacOS/DocFlex" --selftest "$CHECK"; then
    cat "$CHECK/selftest.txt"
    echo "Autotest en échec : DMG non créé." >&2
    exit 1
fi
cat "$CHECK/selftest.txt"
rm -rf "$CHECK"

if [[ -n "$DOCFLEX_SIGN_IDENTITY" ]]; then
    codesign --verify --deep --strict --verbose=1 "$APP"
fi

# Notarisation : uniquement avec un certificat Developer ID et un profil notarytool enregistré.
NOTARIZE=0
if [[ "$DOCFLEX_SIGN_IDENTITY" == "Developer ID Application:"* ]]; then
    if xcrun notarytool history --keychain-profile "$PROFILE" >/dev/null 2>&1; then
        NOTARIZE=1
    else
        echo "Profil de notarisation « $PROFILE » introuvable : DMG signé mais non notarisé."
        echo "  Créez-le une fois : xcrun notarytool store-credentials $PROFILE --apple-id <e-mail> --team-id <ÉQUIPE>"
    fi
fi

notarize() {  # notarize <fichier> : envoie à Apple, attend le verdict, affiche le journal en cas de refus
    local file="$1" result status id
    echo "Notarisation de $(basename "$file") (quelques minutes)…"
    result="$(xcrun notarytool submit "$file" --keychain-profile "$PROFILE" --wait --output-format json)"
    status="$("$PY" -c 'import json,sys; print(json.loads(sys.argv[1]).get("status", ""))' "$result")"
    id="$("$PY" -c 'import json,sys; print(json.loads(sys.argv[1]).get("id", ""))' "$result")"
    if [[ "$status" != "Accepted" ]]; then
        echo "Notarisation refusée ($status). Journal d'Apple :" >&2
        xcrun notarytool log "$id" --keychain-profile "$PROFILE" >&2 || true
        exit 1
    fi
}

if [[ "$NOTARIZE" == 1 ]]; then
    # 1. L'application elle-même : la validation y est agrafée, utile pour un premier lancement hors ligne.
    ZIP="$(mktemp -d)/DocFlex.zip"
    ditto -c -k --keepParent "$APP" "$ZIP"
    notarize "$ZIP"
    xcrun stapler staple "$APP"
fi

STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
hdiutil create -volname "DocFlex" -srcfolder "$STAGE" -fs HFS+ -format UDZO -ov "$DMG" >/dev/null
rm -rf "$STAGE"

if [[ -n "$DOCFLEX_SIGN_IDENTITY" ]]; then
    codesign --sign "$DOCFLEX_SIGN_IDENTITY" --timestamp "$DMG"
fi
if [[ "$NOTARIZE" == 1 ]]; then
    # 2. L'image disque.
    notarize "$DMG"
    xcrun stapler staple "$DMG"
    spctl --assess --type open --context context:primary-signature --verbose "$DMG"
    spctl --assess --type execute --verbose "$APP"
    echo "Signé et notarisé par Apple : aucun avertissement à l'ouverture."
fi
echo "Créé : $DMG ($(du -h "$DMG" | cut -f1))"
