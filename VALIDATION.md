# Validação

## Resultado

- `git apply --check payment-receipt-integration.patch`: **PASSOU** em fixture com os pontos de contexto reconstruídos da Lambda original.
- `git apply payment-receipt-integration.patch`: **PASSOU** na mesma fixture.
- `python -m compileall -q src tests`: **PASSOU**.
- `PYTHONPATH=. pytest -q --cov=src.client --cov=src.config --cov-branch --cov-report=term-missing --cov-fail-under=100`: **PASSOU**.
- testes: **30 passed**.
- cobertura dos quatro arquivos novos de produção: **100% statements e 100% branches**.
- chamadas reais à AWS/STS/API durante testes: **NÃO REALIZADAS**.

## Comportamentos validados

- Secrets Manager continua sendo o caminho permanente.
- sem `ARN_SECRET`, o método permanente mantém o comportamento de não chamar AWS.
- fallback temporário seleciona `dev`, `hml` ou `prod`.
- fallback Fernet descriptografa `CLIENT_ID` e `CLIENT_SECRET` apenas quando ainda não existem no processo.
- ciphertext/placeholders inválidos são recusados.
- STS usa `client_credentials` + HTTP Basic Auth.
- STS tem timeout e retry para falhas transitórias.
- consulta usa `GET /comprovantes/v3/comprovantes/{identificador}` sem body.
- Bearer token e headers obrigatórios são enviados.
- GET repete `429/500/502/503/504` e falhas de conexão/timeout.
- HTTP 4xx não transitório não é repetido.
- logs não incluem token, client secret ou API keys.

## Segurança do fallback temporário

O repositório público contém apenas placeholders. Nenhuma credencial extraída da imagem foi adicionada ao código, documentação ou patch.

A chave Fernet deve preferencialmente ser configurada em `TEMP_CREDENTIALS_KEY` no ambiente da Lambda. Colocar a chave e o ciphertext juntos no código reduz o mecanismo a ofuscação e não deve ser commitado.

## Limitação do `git apply`

O código-fonte original da Lambda de update de retorno de tributos não foi fornecido como repositório/arquivo; ele foi reconstruído a partir de vídeo. Portanto, a validade estrutural do patch foi comprovada em uma fixture contendo os três pontos de contexto observados (`import json`, `logger = prepare_logger()` e `execute_with_retries(Dynamodb().update_item, ...)`).

Antes de aplicar no repositório corporativo real, execute obrigatoriamente:

```bash
git apply --check payment-receipt-integration.patch
```

Se o código real tiver diferenças de contexto, o Git recusará o patch sem alterar arquivos; nesse caso, ajuste apenas o hunk de `lambda_function.py`. Os arquivos novos do patch são independentes desse contexto.
