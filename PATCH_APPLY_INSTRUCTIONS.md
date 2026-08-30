# Aplicação do patch consolidado

O patch consolidado é gerado pelo commit dedicado `ea111b11c3383af5b6beab74728eb2cae824856d`, disponível na branch `patch-delivery`. Ele contém somente:

- alteração de `lambda_function.py`;
- dependência temporária `cryptography`;
- quatro arquivos novos de produção;
- quatro arquivos de testes unitários.

Na raiz da Lambda original:

```bash
curl -L \
  https://github.com/sylviohmartins/aws-lambda-payment-receipt-integration/commit/ea111b11c3383af5b6beab74728eb2cae824856d.patch \
  -o payment-receipt-integration.patch

git apply --check payment-receipt-integration.patch
git apply payment-receipt-integration.patch
```

`git apply --check` é obrigatório porque o repositório-fonte original não foi disponibilizado neste trabalho; o contexto de `lambda_function.py` foi reconstruído a partir do vídeo.

Se o check falhar, não aplique parcialmente. O caso mais provável é diferença de contexto somente no hunk de `lambda_function.py`; os arquivos novos permanecem independentes.
