# Aplicação do patch consolidado

O patch consolidado é gerado por um commit dedicado na branch `patch-delivery`. Ele contém somente a alteração de `lambda_function.py` e os arquivos novos necessários à integração.

Baixe o `.patch` do commit indicado no README e, na raiz da Lambda original, execute:

```bash
git apply --check payment-receipt-integration.patch
git apply payment-receipt-integration.patch
```

`git apply --check` é obrigatório porque o repositório-fonte original não foi disponibilizado neste trabalho; o contexto de `lambda_function.py` foi reconstruído a partir do vídeo.
