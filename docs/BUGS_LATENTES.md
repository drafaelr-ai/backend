# Bugs latentes — Obraly

Bugs identificados durante o refactor das Fases 1–6 que **não foram corrigidos**
(regra do refactor: não misturar correção funcional com extração estrutural).

Reservados para uma sessão de hotfix consolidado.

---

## Frontend

### Bypasses de `fetchWithAuth`

Componentes que chamam APIs sem autenticação (via `window.open` ou `fetch()` direto em vez de `fetchWithAuth`). Causam erros 401 silenciosos em sessões expiradas.

| # | Componente | Ponto | Status |
|---|---|---|---|
| 1 | `VisualizarNotaFiscalModal` | `handleDownload` — `window.open(url)` sem auth | Pendente |
| 2 | `ModalOrcamentos` | `handleDownloadAnexo` — `window.open(url)` sem auth | Pendente |
| 3 | `CadastrarBoletoModal` | Hotfix parcial aplicado na Fase 2 | Verificar |
| 4 | `NotificacoesDropdown` | Hotfix aplicado na Fase 3 | OK |
| 5 | `GestaoBoletos` | Hotfix aplicado na Fase 3 | OK |
| 6 | `DashboardObra` | Hotfix aplicado na Fase 3.5 | OK |
| 7 | `DiarioObras` | Hotfix aplicado na Fase 3.5 | OK |
| 8 | `CronogramaObra` | Hotfix aplicado na Fase 3.5 | OK |
| 9 | `AgendaDemandas` | Hotfix aplicado na Fase 3.5 | OK |
| 10 | `OrcamentoEngenharia` | Hotfix aplicado na Fase 3.5 | OK |

**Pendentes de correção:** itens 1 e 2 (e verificar item 3).

---

### Notify pattern inconsistente

| # | Componente | Bug | Fix sugerido |
|---|---|---|---|
| 1 | `CadastrarBoletoModal.cadastrarTodosBoletos` | Usa `notify.error()` para mensagem de **sucesso** (cor vermelha confunde o usuário) | Mudar para `notify.success()` |
| 2 | `EditarParcelasModal.showToast` | Toast inline via DOM manipulation em vez de usar `notify()` | Refatorar para `notify.*` |
| 3 | `InserirPagamentoModal` | Toast via `useState` + JSX em vez de `notify()` | Refatorar para `notify.*` |

---

### UI defers

| # | Item | Situação |
|---|---|---|
| 1 | `DiarioObras` weather select | `<select>` com 30+ emojis em `<option>` — funciona, mas feio. Custom dropdown seria melhor |
| 2 | `AppAdmin.js` emojis | Dezenas de emojis inline. Vai para a Fase 8 |
| 3 | `BiModule.js` emojis | Refactor visual futuro |
| 4 | 5 sub-páginas como modal overlay | Caixa, relatórios, orcamentos, pagamento, usuários renderizados como `<Modal>` em vez de embedded page. Fase 6.5 futura |

---

## Backend

### Endpoints faltantes / incompletos

| # | Endpoint | Situação |
|---|---|---|
| 1 | `PATCH /obras/<id>/arquivar` | Não existe. Botão "Arquivar" no Dashboard está desabilitado com label "Em breve" |
| 2 | `POST /admin/import-obra` | Existe no backend, mas não está conectado ao fluxo do módulo principal |

### Status enum de obra

`obra.status` não tem enum definido formalmente. Valores em uso: `ativa`, `finalizada`. Definir formalmente: `ativa | finalizada | arquivada | cancelada` e garantir que todos os endpoints e filtros de frontend usem os mesmos valores.

---

## Módulo Patrimonial (para a Fase 8)

A lista de bugs do módulo patrimonial ainda não foi auditada. A Fase 8 começa com auditoria completa de `AppAdmin.js` e `app_admin.py`.

**Item crítico pré-Fase 8:** verificar se o bug de login 500 no admin ainda está presente.

---

## Superlink

| # | ID | Item | Situação |
|---|---|---|---|
| 1 | B-01 | **Extração de boleto por PDF-imagem falha silenciosamente** | `extrair_dados_boleto_pdf_admin` retorna `None` quando o PDF é scan/imagem sem camada de texto (pdfplumber não extrai). O campo `codigo_barras` fica vazio no modal Admin e o usuário digita manualmente. Comportamento intencional (fallback documentado), mas poderia ser melhorado com OCR (tesseract) ou um aviso explícito "PDF não reconhecido — insira manualmente". |
| 2 | B-02 | **Superlink Main não pré-preenche `pix_chave`** | No módulo Main, `pagamento_futuro` e `pagamento_parcelado` têm coluna `codigo_barras` mas não têm `pix_chave` em todos os endpoints. O modal deixa o campo vazio para preenchimento manual. Corrigir no endpoint `/historico-unificado` expondo `pix` de cada tipo de pagamento. |
