# Aplicação do patch

## Pré-requisito

Coloque `apply_patch.sh` na raiz do projeto-alvo, no mesmo nível de `lambda_function.py` e `src/`. O projeto deve estar em um repositório Git sem alterações locais pendentes.

## Aplicação recomendada

```bash
chmod +x apply_patch.sh
./apply_patch.sh
```

Sem argumentos, o script baixa o patch canônico do commit dedicado e executa automaticamente:

```text
1. valida estrutura e estado do repositório;
2. baixa payment-receipt-integration.patch;
3. git apply --check;
4. git apply;
5. git diff --check;
6. valida sintaxe Python em memória, sem gerar __pycache__.
```

Também é possível passar um patch local explicitamente:

```bash
./apply_patch.sh ./payment-receipt-integration.patch
```

Se houver arquivos não rastreados além do próprio script/patch, ou alterações rastreadas/staged, o script aborta antes da aplicação.

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
