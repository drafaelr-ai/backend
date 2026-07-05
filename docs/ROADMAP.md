# Roadmap — Obraly

Atualizado em 05/06/2026.

---

## Estado atual

Fases 1–4, 6 e Superlink concluídas:

| Fase | Status | Conteúdo |
|---|---|---|
| 1 | ✅ | Utilidades (notify, logger, format) |
| 2 | ✅ | 28 modais extraídos (frontend) |
| 3 | ✅ | Telas extraídas — App.js → 85 linhas |
| 4 | ✅ | Backend completo — app.py → 165 linhas, 13 blueprints, v26 |
| 5 | ⏳ | Performance (não iniciada) |
| 6 | ✅ | Design System v2.0 + Dashboard novo |
| 7 | ⏳ | Acessibilidade (não iniciada) |
| 8 | ⏳ | Módulo Patrimonial — **PRIORIDADE** |
| 9 | ⏳ | Cronograma Operacional Integrado |
| — | ✅ | **Superlink de Pagamento** (fora de fase, entregue 05/06/2026) |
| — | ✅ | **Módulo Pessoal / RH** (fora de fase, entregue 07/2026) |

---

## Curto prazo

### Fase 5 — Performance

- `useCallback` / `React.memo` em componentes pesados (OrcamentoEngenharia, CronogramaObra, DiarioObras)
- `useReducer` no Dashboard (substituir os múltiplos `useState` por estado consolidado)
- Paginação em listas com > 100 itens (obras, lançamentos, boletos)
- Virtualização onde for necessário (react-window)
- Lighthouse audit → score alvo ≥ 90 em Performance

### Fase 7 — Acessibilidade

- `alt` em todas as imagens
- `aria-label` em ícones sem texto visível
- Focus management em abertura/fechamento de modais
- Navegação por teclado em menus dropdown
- Lighthouse a11y audit → score alvo ≥ 90

---

## Médio prazo

### Fase 8 — Módulo Patrimonial

**Diretiva do usuário:** aplicar aos mesmos moldes das Fases 1–4 + 6 do módulo principal.

#### Garantias de integridade de dados (CRÍTICO)

O módulo patrimonial tem dados reais de clientes em produção. Qualquer operação precisa de:

1. `pg_dump` completo **antes** de qualquer deploy
2. Auditoria de contagens pré/pós deploy (imóveis, lançamentos, receitas/despesas)
3. Zero migrations que renomeiem, dropem ou alterem colunas existentes
4. Refactor apenas visual + UX — schema preservado
5. Rollback `fly releases rollback` imediato se qualquer divergência

#### Roteiro proposto

| Sub-lote | Conteúdo |
|---|---|
| 8.0 | Auditoria de `AppAdmin.js` + `app_admin.py` (linhas, bugs, features) |
| 8.1 | Utilidades: logger, notify, format no módulo admin |
| 8.2 | Design System: tokens + modais (reusa o wrapper do main) |
| 8.3 | Telas: extração de `AppAdmin.js` → `screens/admin/` |
| 8.4 | Backend: extensions, models, services, routes (factory pattern) |
| 8.5 | Performance |
| 8.7 | Acessibilidade |

#### Features confirmadas pelo usuário

- **Superlink de pagamento** por imóvel — ✅ entregue (botão em ModalLancamentosDashboard)
- **Boletos contextuais** por imóvel
- **Period selector** no dashboard (acumulado vs. mês atual)
- **Imóvel cards clicáveis** no dashboard (navegação direta para detalhe)

#### Pré-requisitos antes de iniciar

- Resolver status de login (verificar se o bug 500 no admin ainda existe)
- Confirmar enum de status do imóvel: `ativa | finalizada | arquivada | cancelada`
- Fazer backup completo do banco admin

---

### Fase 9 — Cronograma Operacional Integrado

**Tese:** o cronograma se auto-atualiza a partir do financeiro. Mantém o tripé:

| Dimensão | Fonte | Atualização |
|---|---|---|
| **Físico** | Input manual do mestre | Manual |
| **Executado** | Medição da etapa | Manual (com sugestão AI futura) |
| **Pago** | Lançamentos financeiros | Automático |

A **variância Pago × Executado** é a métrica-chave. A estrutura já existe — falta reformular a apresentação.

#### Sub-lotes propostos

| Sub-lote | Conteúdo |
|---|---|
| 9.0 | Auditoria do `CronogramaObra.js` atual (~1.875 linhas) |
| 9.1 | Reformulação visual: 3 barras verticais com cores + banner de variância com CTA |
| 9.2 | Curva S do projeto inteiro (progresso acumulado ao longo do tempo) |
| 9.3 | Mobile-first weekly view (Last Planner-light — reduz fricção de atualização) |
| 9.4 | AI sugestões: "Pago R$X em categoria Y, marcar etapa Z como N%?" |

**Combinação recomendada:** 9.3 (weekly view mobile) + 9.4 (AI sugestões) — maior redução de fricção.

---

## Superlink de Pagamento — ✅ Entregue (05/06/2026)

Substituiu o compartilhamento individual por WhatsApp por um link permanente de 7 dias.

| Componente | Entregue |
|---|---|
| Migration idempotente (`superlink` table, JSONB `itens`) | ✅ |
| `Superlink` model Main + `SuperlinkAdmin` model Admin | ✅ |
| `POST /superlink` + `GET /superlink/<token>` (público) | ✅ |
| `POST /admin/superlink` + `GET /admin/superlink/<token>` (público) | ✅ |
| Smoke 7/7 PASS + deploy obraly-api v40 + obraly-admin-api | ✅ |
| Página pública `/pagar/<token>` — PIX + boleto, 410 expired, 404 notfound | ✅ |
| `GerarSuperlinkModal` (Main) em `HistoricoPagamentosCard` | ✅ |
| `GerarSuperlinkAdminModal` (Admin) em `ModalLancamentosDashboard` | ✅ |

**Snapshot:** cada link congela descrição, valor, contexto, forma e chave no momento da geração. A rota pública nunca lê models internos.

**Boleto / extração PDF:** `extrair_dados_boleto_pdf_admin` reutilizada no Admin. Quando o PDF é imagem (sem texto extraível), o campo `codigo_barras` fica vazio para digitação manual — comportamento documentado em B-01 de BUGS_LATENTES.

---

## Módulo Pessoal / RH — ✅ Entregue (07/2026)

Módulo centralizado (fora de qualquer obra) no backend Main. **Registra**
funcionários, CCTs, pagamentos de salário e encargos — não calcula férias/13º/rescisão.

| Componente | Entregue |
|---|---|
| 6 models + migration aditiva/idempotente (+ `obra.uf` nullable) | ✅ |
| Services: storage (Supabase), parser CCT (pdfplumber + Anthropic), rh_service (dashboard c/ rateio) | ✅ |
| `rh_bp` (`/rh`) — 27 rotas, todas `@jwt_required()`; smoke multi-role 0 FAIL | ✅ |
| Deploy backend `obraly-api` + secrets (`ANTHROPIC_API_KEY`, `SUPABASE_*`) + bucket `rh-arquivos` | ✅ |
| Frontend: 5 telas + 3 modais + card no ModuleSelector, fiéis ao preview | ✅ |
| Hotfix pré-smoke: guard ÷0 no dashboard + degradação graciosa parser/storage | ✅ |

**Decisões:** UF do piso vem de `obra.uf` (`funcionario.obra → obra.uf → piso_vigente`).
Parser degrada com 200+aviso (nunca 422, que o `fetchWithAuth` trata como logout).
`obra_id` do pagamento é snapshot (custo histórico por obra permanece correto).

---

## Não priorizado (decisão consciente)

| Item | Motivo |
|---|---|
| Migração para outro framework | Custo/benefício não justifica agora |
| DiarioObras weather custom dropdown | `<select>` com emojis funciona — refactor visual puro |
| Gantt complexo com dependências | Fora do escopo do produto atual |
| CPM / caminho crítico | Idem |
| Refactor visual do BiModule | Baixa prioridade |
| 5 sub-páginas que são modal overlay | Fase 6.5 futura (caixa, relatórios, orcamentos, pagamento, usuários) |

---

## Diretivas registradas do usuário

1. **Patrimonial é prioridade** — antes de cronograma e performance
2. **Cuidado máximo com dados do patrimonial** — backup + auditoria sempre
3. **Cronograma:** manter o tripé Pago vs Executado, reformular o formato de apresentação
4. **Módulo admin aos mesmos moldes do main** — não inventar nova arquitetura
