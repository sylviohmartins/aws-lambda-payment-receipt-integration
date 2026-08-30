# Validação

## Resultado local

- `python -m compileall -q .`: **PASSOU**
- `PYTHONPATH=. pytest -q`: **PASSOU — 9 testes**
- STS `client_credentials` + HTTP Basic Auth: **PASSOU**
- retry STS em timeout transitório: **PASSOU**
- limite de 3 tentativas STS: **PASSOU**
- Secrets Manager com `botocore.config.Config` e retry `standard`: **PASSOU com mock**
- GET `/comprovantes/v3/comprovantes/{identificador}` sem body: **PASSOU**
- Bearer token e headers informados: **PASSOU**
- retry GET para HTTP 503: **PASSOU**
- HTTP 400 não é repetido: **PASSOU**
- resposta HTTP 204/sem conteúdo: **PASSOU**
- bloqueio de headers `<FAKE_...>` antes de qualquer request: **PASSOU**
- chamadas externas reais durante os testes: **NÃO REALIZADAS**

## Restrições

O repositório-fonte original da Lambda de update de retorno de tributos não foi disponibilizado como código. Portanto, não é possível executar a suíte original nem provar um `git diff` contra os bytes do projeto real. `lambda_function.patch` foi elaborado sobre o fluxo reconstruído visualmente.
