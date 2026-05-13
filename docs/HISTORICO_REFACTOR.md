# Histórico do Refactor — Obraly

Refactor estrutural realizado em maio/2026 (~4 dias de trabalho).

**Ponto de partida:** monolitos enormes sem estrutura.
**Ponto de chegada:** arquitetura modular, design system, factory pattern.

---

## Métricas globais

| Item | Antes | Depois | Redução |
|---|---|---|---|
| `app.py` (backend) | 19.516 linhas | 165 linhas | -99% |
| `App.js` (frontend) | 13.054 linhas | 85 linhas | -99.4% |
| Modais inline | 28 distintos | 1 wrapper unificado | — |
| `alert()` / `confirm()` | 199 ocorrências | 0 | -100% |
| `console.*` diretos | 177 ocorrências | 0 | -100% |
| `print()` diretos | 749 ocorrências | 0 | -100% |
| `bare except` | 45 ocorrências | 0 | -100% |
| Hex hardcoded | ~200 | 0 (ou catalogados) | -100% |
| Rotas inline em app.py | 186 | 0 | extraídas |
| Models inline em app.py | 25 | 0 | extraídos |
| Services inline | 7 | 0 | extraídos |

---

## Fase 0 — Críticos

**Problema:** vulnerabilidades de segurança e arquivos mortos.

- JWT_SECRET_KEY com fallback inseguro `"default-secret"` → substituído por `RuntimeError` obrigatório
- `app_com_cronograma.py`, `CronogramaObra_EXEMPLO.jsx` e outros órfãos → deletados
- `config.js` criado no frontend (consolidação de constantes)

---

## Fase 1 — Utilidades

**Frontend:**
- `utils/notify.jsx` → substitui 199 `alert()` / `confirm()` / `window.confirm()`
- `utils/logger.js` → substitui 177 `console.log/warn/error`
- `utils/format.js` → funções de formatação monetária e data

**Backend:**
- `logging_setup.py` → setup centralizado de logging
- 749 `print()` → `logger.*`
- 45 `bare except:` → `except Exception as e:`

---

## Fase 2 — 28 Modais (frontend)

**Resultado:** App.js de 13.054 → 6.667 linhas (-49%).

- 28 modais extraídos de App.js para `src/components/modals/`
- Cada modal: próprio `.jsx` + `.css` (quando necessário)
- Hotfix durante extração: `CadastrarBoletoModal` usava `fetch()` direto → substituído por `fetchWithAuth`

---

## Fase 3 — Telas (frontend)

**Resultado:** App.js de 6.667 → **85 linhas** (-99.4% do original).

Sub-lotes A-E: telas extraídas para `src/screens/`:
- `Dashboard/` (era `ObraDetalhe` — renomeado na Fase 6)
- `ModuleSelector/`
- `auth/LoginScreen.jsx`
- E outros

**Incidentes durante extração:**
- Encoding Win-1252 → UTF-8: 134 linhas com `�` (caracteres corrompidos) detectadas e corrigidas
- `fetchWithAuth` bypass em 5 componentes extras detectado e corrigido

---

## Fase 4 — Backend

**Resultado:** app.py de 19.516 → **165 linhas** (-99%).

### Sub-lote A — Estrutura
- `extensions.py` criado: `db`, `jwt`, `cors`, `limiter` compartilhados
- `config.py` criado: configuração por ambiente
- Pastas `models/`, `services/`, `routes/` criadas

### Sub-lote B — Models
- 25 SQLAlchemy models extraídos para `models/`
- Cada model em seu próprio arquivo

### Sub-lote C — Factory pattern
- `create_app(config_class=Config)` implementado
- 3 camadas CORS preservadas (flask-cors + after_request + catch-all OPTIONS)
- Deploy v20

### Sub-lote D — Services + utils
- 7 helpers extraídos para `services/`
  - `auth_service.py`: `get_current_user`, `user_has_access_to_obra`, `check_permission`
  - `notificacao_service.py`: `criar_notificacao`, `notificar_masters`, `notificar_operadores_obra`, `notificar_administradores`
- `utils.py` criado: `formatar_real`
- Fix `bare except` restantes

### Sub-lote E — Blueprints (3 batches)

**Batch 1 + 1+** (deploy v23, v24):
- `notificacoes_bp` (7 rotas)
- `bi_bp` (3 rotas)
- `diario_bp` (8 rotas)
- `auth_bp` (6 rotas)
- `admin_bp` (10 rotas)
- `sid_bp` (10 rotas, `url_prefix=/sid`)

**Batch 2** (deploy v25):
- `caixa_bp` (5 rotas, `url_prefix=/obras/<id>/caixa`)
- `servicos_bp` (11 rotas)
- `boletos_bp` (9 rotas) — inclui `extrair_dados_boleto_pdf()` movida do app.py
- `lancamentos_bp` (9 rotas)

**Batch 3** (deploy v26):
- `cronograma_bp` (30 rotas)
- `orcamento_eng_bp` (14 rotas, `url_prefix=/obras/<int:obra_id>/orcamento-eng`)
- `obras_bp` (40 rotas) — residual de todos os extractions

### Sub-lote F — Cleanup

- 55 imports órfãos removidos de `app.py`
- `run_auto_migration()` extraído para `auto_migration.py` (472 linhas)
- Docstrings adicionados (`app.py`, `create_app()`, `auto_migration.py`)
- `NOTAS_DEPLOY.md` criado
- app.py final: **165 linhas**

**Total:** 42 commits na branch `refactor/fase-4-backend`, deploy estável em v26.

---

## Fase 6 — Design System Frontend

### Sub-lote A — Tokens
- `tokens.css` instalado em `src/styles/`
- Fonte Inter carregada (Google Fonts CDN)
- Tabler Icons carregados (CDN)
- ~200 hex hardcoded migrados para `var(--*)` tokens

### Sub-lote B — Login
- LoginScreen redesenhado: split-screen v2.0
- Marca alinhada, tipografia calibrada

### Sub-lote C — NavBar + Notificações
- `WindowsNavBar` redesenhado com tokens
- `NotificacoesDropdown` atualizado
- `DashboardHeader` criado (header do dashboard com avatar dropdown)

### Sub-lote D — 26 Modais → wrapper unificado
- `Modal.jsx` wrapper criado com 5 variantes (default, small, large, xlarge, confirm)
- `useModalKeyboard.js` para Esc + focus trap
- 26 modais migrados para o wrapper
- Build: 287.01 kB → ~290 kB (mínimo overhead apesar dos 26 modais)

### Sub-lote E — Dashboard novo + ObraDetalhe
- **Dashboard novo** criado: listagem panorâmica de obras com KPIs
  - Componentes: StatCard, StatCardCompact, AlertStatCard, ProgressBar, ActivityItem, ObraCardActions
- **ObraDetalhe** (renome do "Dashboard" anterior): visão de uma obra específica
- Emoji cleanup: todos removidos do UI, substituídos por ícones Tabler
- Tipografia recalibrada: valores monetários mínimo 14px weight 600 tabular-nums

### Sub-lote F — Section card + cleanup final
- `.m-section-card` + `.m-section-card-header` padronizados
- Classes utilitárias consolidadas (`.m-field`, `.m-label`, `.m-input`, `.m-btn-*`, `.m-badge-*`)
- Cleanup de emojis restantes
- Cleanup de hex hardcoded remanescentes

---

## Incidentes em produção (5 — todos recuperados < 1 min de downtime)

| # | Fase | Incidente | Resolução |
|---|---|---|---|
| 1 | 4-C | CORS quebrado pós-deploy v20 — era `ModuleNotFoundError` (models/ não copiado) | Rollback v19 → fix `.dockerignore` + `COPY . .` → v20 OK |
| 2 | 4-D | HTTP 500 em `/orcamento-eng` pós-deploy v21 — `ANY(:ids)` sem bind | Fix em 5 min → v22 |
| 3 | 3 | Encoding corrompido no Dashboard pós-extração — 134 linhas com `�` | Detecção + correção manual Win-1252 → UTF-8 |
| 4 | 4 | `cwd reset` repetido no terminal (PROJETOS/sicop em vez de PROJETOS/obraly) | Workaround `Set-Location` explícito em cada comando |
| 5 | múltiplas | `Failed to fetch` no frontend = sintoma de backend crashado, não CORS real | Diagnóstico via `fly logs` → rollback quando necessário |
