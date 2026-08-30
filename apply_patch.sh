#!/usr/bin/env bash
# Baixa e aplica de forma segura o patch canônico na raiz do projeto-alvo.
#
# Uso esperado:
#   1. coloque apenas este script na raiz do projeto;
#   2. execute: chmod +x apply_patch.sh && ./apply_patch.sh
#
# Opcionalmente, informe um patch local já baixado:
#   ./apply_patch.sh ./meu-patch.patch
#
# O script falha antes de alterar arquivos se o contexto do patch não for
# compatível ou se houver alterações locais não commitadas.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CANONICAL_PATCH_URL="https://github.com/sylviohmartins/aws-lambda-payment-receipt-integration/commit/68288c391c671926aec85352499c0f3dc0e15227.patch"
PATCH_FILE="${1:-payment-receipt-integration.patch}"

fail() {
  printf 'ERRO: %s\n' "$*" >&2
  exit 1
}

command -v git >/dev/null 2>&1 || fail "git não encontrado no PATH."
command -v python >/dev/null 2>&1 || fail "python não encontrado no PATH."

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || fail "execute o script dentro de um repositório Git."
[[ -f "lambda_function.py" ]] || fail "lambda_function.py não encontrado na raiz do projeto."
[[ -d "src" ]] || fail "diretório src/ não encontrado na raiz do projeto."

if ! git diff --quiet || ! git diff --cached --quiet; then
  fail "há alterações rastreadas/staged. Commit ou stash antes de aplicar o patch."
fi

SCRIPT_NAME="$(basename -- "$0")"
PATCH_NAME="${PATCH_FILE#./}"
UNEXPECTED_UNTRACKED="$(
  git ls-files --others --exclude-standard \
    | grep -v -F -x "$SCRIPT_NAME" \
    | grep -v -F -x "$PATCH_NAME" \
    || true
)"
if [[ -n "$UNEXPECTED_UNTRACKED" ]]; then
  printf '%s\n' "$UNEXPECTED_UNTRACKED" >&2
  fail "há arquivos não rastreados além do script e do patch."
fi

if [[ $# -eq 0 ]]; then
  command -v curl >/dev/null 2>&1 || fail "curl não encontrado no PATH."
  printf '[0/4] Baixando patch canônico...\n'
  curl --fail --silent --show-error --location "$CANONICAL_PATCH_URL" --output "$PATCH_FILE"
fi

[[ -f "$PATCH_FILE" ]] || fail "patch não encontrado: $PATCH_FILE"

printf '[1/4] Validando patch...\n'
git apply --check "$PATCH_FILE"

printf '[2/4] Aplicando patch...\n'
git apply "$PATCH_FILE"

printf '[3/4] Validando whitespace e sintaxe Python...\n'
git diff --check
python - <<'PYCODE'
from pathlib import Path

files = [Path("lambda_function.py"), *Path("src").rglob("*.py")]
for path in files:
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
PYCODE

printf '[4/4] Concluído. Revise o diff antes do deploy:\n\n'
git status --short
printf '\nComandos recomendados:\n'
printf '  git diff --check\n'
printf '  git diff\n'
printf '\nIMPORTANTE: preencha apenas na cópia local os placeholders temporários de PROD\n'
printf 'e empacote requirements-temporary.txt antes do deploy manual.\n'
