# Validação

## Escopo

A implementação foi construída por **engenharia reversa de um projeto de referência**, sem acesso ao repositório-fonte original completo. O objetivo foi reproduzir somente o mecanismo necessário para autenticação STS, consulta HTTP e integração mínima com o fluxo existente.

## Validações executadas

- `git apply --check`: passou em fixture que reproduz os pontos de contexto observados.
- `git apply`: aplicou integralmente na mesma fixture.
- validação de sintaxe Python em memória: passou.
- `pytest` com branch coverage: **23 passed**, com 100% nos arquivos permanentes novos.
- `git diff --check`: passou.
- chamadas reais externas durante testes: não realizadas.

## Comportamentos permanentes cobertos

- Secrets Manager continua sendo o caminho principal de credenciais;
- STS usa `client_credentials` com HTTP Basic Auth;
- chamadas STS possuem timeout e retry para falhas transitórias;
- consulta de comprovante usa `GET` sem body;
- Bearer token e headers necessários são enviados;
- falhas HTTP transitórias usam retry com backoff e jitter;
- erros 4xx não transitórios não são repetidos;
- logs não exibem token, secret ou API keys.

## Fallback temporário

O fallback de credenciais é exclusivamente de **produção** e não contém seleção de ambiente. Ele existe somente para permitir um deploy manual antes do provisionamento do secret definitivo.

O bloco está claramente delimitado em `src/config/credentials.py` e marcado com `# pragma: no cover`, pois deve ser removido quando a infraestrutura permanente estiver disponível. Não foram criados testes unitários específicos para esse trecho transitório.

## Limitação do patch

Como o código-fonte original completo não fez parte desta análise, o contexto de `lambda_function.py` foi reconstruído. Por isso o script de aplicação executa obrigatoriamente `git apply --check` antes de modificar qualquer arquivo.

Se o check falhar, o script encerra sem aplicar o patch. Nesse caso, deve-se ajustar somente o hunk de contexto de `lambda_function.py` ao código real, preservando os novos arquivos.
