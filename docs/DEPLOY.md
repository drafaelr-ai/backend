# Deploy — Obraly

---

## Frontend (Vercel)

Deploy automático via push para a branch configurada.

1. Push para `refactor/*` ou `main`
2. Vercel detecta e deploya automaticamente
3. Domain: `obraly.uk` + `www.obraly.uk`

Smoke manual pós-deploy: abrir `https://obraly.uk` em aba anônima, login, navegar pelas seções principais.

---

## Backend Main (Fly.io)

### Pré-requisitos

- Estar autenticado: `fly auth login`
- **Diretório correto:** o `fly.toml` vive em `backend-fase4/fly-deploy/obraly-api/`, não na raiz do backend.
  Rodar `fly deploy` de fora desse diretório resulta em erro `app does not have a Dockerfile`.

### Procedimento

```powershell
# 1. Abrir janela paralela com rollback ready (NÃO executa ainda):
fly releases rollback --app obraly-api

# 2. Push dos commits:
cd C:\PROJETOS\obraly\backend-fase4
git push origin <branch>

# 3. Deploy:
cd C:\PROJETOS\obraly\backend-fase4\fly-deploy\obraly-api
fly deploy --no-cache --app obraly-api

# Aguarda: "Deployment succeeded" + "machines in good state"
```

### Ritual CORS (obrigatório pós-deploy)

```bash
# 1. OPTIONS preflight
curl -i -X OPTIONS https://obraly-api.fly.dev/login \
  -H "Origin: https://www.obraly.uk" \
  -H "Access-Control-Request-Method: POST"
# Espera: 200 + Access-Control-Allow-Origin header

# 2. GET root
curl -i https://obraly-api.fly.dev/ \
  -H "Origin: https://www.obraly.uk"
# Espera: 200 + CORS headers

# 3. Rota inexistente
curl -i https://obraly-api.fly.dev/rota-inexistente \
  -H "Origin: https://www.obraly.uk"
# Espera: 405 (só aceita OPTIONS) + CORS headers
```

### Curls funcionais recomendados

```bash
# Obras (obras_bp) — 422 esperado (JWT inválido)
curl -i https://obraly-api.fly.dev/obras \
  -H "Authorization: Bearer invalido" \
  -H "Origin: https://www.obraly.uk"

# Orcamento-eng (historicamente sensível — v21)
curl -i https://obraly-api.fly.dev/obras/2/orcamento-eng \
  -H "Authorization: Bearer invalido" \
  -H "Origin: https://www.obraly.uk"
# 422 = OK | 500 = ROLLBACK IMEDIATO
```

### Em caso de erro (500)

```powershell
# Na janela paralela:
fly releases rollback --app obraly-api
# Aguarda 30s — volta pra versão anterior
fly logs --app obraly-api  # investigar causa
```

### Histórico de versões

| Versão | Data | Conteúdo |
|---|---|---|
| v20 | mai/2026 | Fix Dockerfile pipeline (.dockerignore + COPY . .) |
| v21 | mai/2026 | Fix bind parameter ANY() em /orcamento-eng |
| v22 | mai/2026 | Estabilização pós-fix |
| v23 | mai/2026 | Batch 1: notificacoes, bi, diario, auth, admin, sid blueprints |
| v24 | mai/2026 | Batch 1+: completado |
| v25 | mai/2026 | Batch 2: caixa, servicos, boletos, lancamentos |
| **v26** | mai/2026 | **Batch 3: cronograma, orcamento_eng, obras — estado estável atual** |

---

## Backend Admin (Fly.io)

### ATENÇÃO: dados em produção

**SEMPRE antes de qualquer deploy do admin:**

```bash
pg_dump postgresql://[conexão-admin-supabase] \
  > backup_admin_$(date +%Y%m%d_%H%M).sql
```

Salvar o dump em local seguro antes de prosseguir.

### Auditoria de integridade obrigatória

Antes e depois de cada deploy, conferir:
- Contagem total de imóveis: `SELECT COUNT(*) FROM imovel;`
- Contagem total de lançamentos: `SELECT COUNT(*) FROM lancamento_admin;`
- Soma de receitas e despesas

Qualquer divergência nas contagens: **ROLLBACK IMEDIATO**.

### Steps

```powershell
cd C:\PROJETOS\obraly\backend-fase4\fly-deploy\obraly-admin
fly deploy --app obraly-admin-api
```

---

## Lições aprendidas

### Incidente v20 — Dockerfile pipeline

**Causa:** `Dockerfile.obraly` copiava apenas `app.py`. Quando `models/` foi extraído para uma pasta separada, o container subiu sem os models.

**Sintoma:** CORS error em produção (na verdade era `ModuleNotFoundError` no boot — o 500 se disfarçava de CORS).

**Fix:** `COPY . .` no Dockerfile + `.dockerignore` criado para excluir arquivos desnecessários.

**Lição:** Sempre que a estrutura de pastas mudar (nova pasta `models/`, `routes/`, etc.), conferir se o Dockerfile copia tudo que precisa.

---

### Incidente v21 — Bind parameter ANY()

**Causa:** Endpoint `/obras/<id>/orcamento-eng` tinha 4 queries com `WHERE id = ANY(:ids)` sem passar `{"ids": lista_de_ids}` no `session.execute()`.

**Sintoma:** HTTP 500 em qualquer obra que tivesse itens de orçamento.

**Fix:** Adicionado o bind parameter `ids` correto nas 4 queries.

**Lição:** Smoke pós-deploy deve incluir endpoints que usam `ANY()` com listas. O smoke com token inválido (422) não testa o corpo da query — precisa de um token real.

---

### Princípio geral

"Failed to fetch" no frontend = backend crashou ou retornou 500, **não** CORS real.
Verificar com `fly logs --app obraly-api` antes de caçar problema de CORS.
