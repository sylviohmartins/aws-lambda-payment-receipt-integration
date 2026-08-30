# Aplicação do patch

## Pré-requisito

Coloque na raiz do projeto-alvo, no mesmo nível de `lambda_function.py` e `src/`:

```text
apply_patch.sh
payment-receipt-integration.patch
```

O projeto deve estar em um repositório Git sem alterações locais pendentes.

## Aplicação recomendada

```bash
chmod +x apply_patch.sh
./apply_patch.sh
```

O script executa automaticamente, nesta ordem:

```text
1. valida estrutura e estado do repositório;
2. git apply --check payment-receipt-integration.patch;
3. git apply payment-receipt-integration.patch;
4. git diff --check;
5. valida sintaxe Python em memória, sem gerar __pycache__.
```

Se houver arquivos não rastreados além do próprio script e do patch, ou alterações rastreadas/staged, o script aborta antes da aplicação.

## Depois de aplicar

Revise:

```bash
git diff --check
git diff
```

Antes do deploy manual temporário, substitua **somente na cópia local** os placeholders do fallback de produção e empacote a dependência indicada em `requirements-temporary.txt`.

Quando o Secrets Manager estiver disponível, remova integralmente o bloco marcado como `TEMPORÁRIO / PRODUÇÃO`, sua chamada no bootstrap e a dependência temporária.

## Observação

Este patch foi produzido por **engenharia reversa de um projeto de referência**. Como o repositório-fonte original completo não estava disponível, `git apply --check` é obrigatório para garantir compatibilidade com o contexto real antes de qualquer alteração.
