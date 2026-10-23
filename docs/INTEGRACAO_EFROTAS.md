# Integração e-Frotas

O Obraly trata a aba **Frota → Multas** como o centro operacional da
integração: recebe alertas novos, mostra os prazos e permite trazer infrações
pendentes que já existiam antes da ativação.

## 1. Cadastre os veículos antes da conexão

Em **Frota → Veículos**, cadastre cada veículo com a mesma placa registrada no
e-Frotas. A placa é normalizada automaticamente (`ABC-1D23` vira `ABC1D23`).
Depois, em **Frota → Condutores**, escolha o campo **Veículo atribuído**. A
troca é atômica e pede confirmação quando o veículo já pertence a outra pessoa.

O Obraly mantém o condutor responsável em branco para revisão, inclusive em
eventos novos: o callback pode chegar depois de uma troca de motorista. O
evento avisa gestores e, se houver conta vinculada, o condutor atual; a pessoa
responsável é definida conscientemente em **Gerenciar infração**.

## 2. Configure o webhook para infrações novas

Cadastre no gerenciamento do e-Frotas:

```text
POST https://obraly-api.fly.dev/integracoes/efrotas/webhook
Content-Type: application/json
X-Obraly-Webhook-Token: <EFROTAS_WEBHOOK_TOKEN>
```

Na API, defina um segredo longo e exclusivo:

```bash
fly secrets set EFROTAS_WEBHOOK_TOKEN='gere-um-segredo-longo-e-unico' \
  --config fly.obraly.toml --app obraly-api
```

Se o cabeçalho definido no e-Frotas tiver outro nome, ajuste
`EFROTAS_WEBHOOK_HEADER`. Habilite os eventos **1 (autuação), 2
(penalidade), 10 (alteração) e 75 (cancelamento)**. O receptor responde rápido,
guarda `idRastreamento` e a chave oficial `órgão + AIT + código da infração`,
portanto reenvios não duplicam multas nem alertas.

Os tipos 1 e 2 fazem *upsert* da mesma multa; o tipo 10 atualiza prazo, valor,
situação ou pagamento conforme a alteração recebida; o 75 preserva a multa e a
marca como cancelada. Não use exclusão para registros importados: a trilha
oficial fica disponível para auditoria.

## 3. Configure a carga do histórico

O botão **Atualizar infrações** só aparece habilitado quando a API possui a
configuração abaixo. Nunca exponha esses valores no navegador.

```dotenv
EFROTAS_API_BASE_URL=https://efrotas.estaleiro.serpro.gov.br/efrotas/api
EFROTAS_CLIENT_CERT_PATH=/run/secrets/efrotas-client.crt
EFROTAS_CLIENT_KEY_PATH=/run/secrets/efrotas-client.key
EFROTAS_TIMEOUT_SECONDS=20
```

Em produção, o e-Frotas exige certificado ICP-Brasil de pessoa jurídica
habilitado no Credencia (mTLS). Em homologação pode-se usar
`EFROTAS_API_TOKEN` como Bearer token no lugar do par de certificado/chave.

No painel, selecione até **cinco veículos** e um período de até **um ano**. A
API consulta cada placa pelo endpoint oficial de infrações; é idempotente e
não dispara um alerta individual para dados antigos. Para uma frota ou período
maior, faça lotes sucessivos: assim a tela recebe sempre um resultado auditável
antes do limite de resposta do servidor.

Importante: essa consulta oficial retorna **somente infrações não pagas**. Para
montar um histórico completo de quitadas/canceladas, use o cadastro manual ou
uma futura importação CSV mantendo o AIT/origem documentados.

## 4. Revise exceções em vez de perder eventos

Se um callback chegar antes de o veículo ser cadastrado, ele aparece em
**Multas → Revisar exceções**. Cadastre/corrija o veículo e clique em
**Reprocessar**. O evento pendente é reaproveitado sem criar duplicidade.

## Dados esperados

O parser aceita os campos retornados pelo e-Frotas, como `placa`,
`codigoOrgaoAutuador`, `numeroAutoInfracao`, `codigoInfracao`,
`dataAutuacao` e `valorOriginal` (também aceita o legado
`valorIntegralInfracao`). Para penalidade, lê
`dataVencimentoNotificacaoPenalidade`; callbacks de alteração podem trazer a
infração em `infracao`/`dados`, mas `tipoEvento` e `idRastreamento` são sempre
lidos da raiz.

Referências oficiais: [consultas e eventos do e-Frotas](https://centraldeajuda.serpro.gov.br/efrotas/consultaseeventos/), [FAQ](https://centraldeajuda.serpro.gov.br/efrotas/faq/) e [certificado digital](https://centraldeajuda.serpro.gov.br/efrotas/certificadodig/).
