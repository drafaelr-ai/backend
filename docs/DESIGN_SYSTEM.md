# Design System v2.0 — Obraly

Instalado na Fase 6. Fonte: `frontend-fase6/src/styles/tokens.css`.

---

## Tokens CSS

Todos os valores visuais devem usar variáveis CSS. **Nenhum hex hardcoded.**

### Surfaces

```css
var(--surface-page)      /* fundo da página */
var(--surface-card)      /* fundo de cards */
var(--surface-muted)     /* hover, sub-sections */
var(--surface-subtle)    /* fundo ainda mais sutil */
```

### Text

```css
var(--text-primary)      /* texto principal */
var(--text-secondary)    /* texto secundário */
var(--text-muted)        /* labels, captions, placeholders */
var(--text-on-primary)   /* texto sobre fundo primário (botões) */
```

### Borders

```css
var(--border-default)    /* borda padrão */
var(--border-subtle)     /* borda mais sutil */
```

### Brand

```css
var(--brand-primary)     /* cor principal da marca */
```

### Status

```css
var(--status-success)       var(--status-success-bg)
var(--status-warning)       var(--status-warning-bg)
var(--status-danger)        var(--status-danger-bg)
var(--status-info)          var(--status-info-bg)
```

### Misc

```css
var(--shadow-card)       /* sombra de cards */
var(--shadow-modal)      /* sombra de modais */
var(--radius-card)       /* border-radius de cards */
```

---

## Tipografia

| Nível | Tamanho | Peso |
|---|---|---|
| Hero / h1 | 24–32px | 700 |
| h2 | 20px | 600 |
| h3 | 16px | 600 |
| Body | 14px | 400 |
| Caption / small | 12px | 400 |
| Badge / tiny | 11px | 500, uppercase, letter-spacing |

**Valores monetários:** mínimo 14–15px, `font-weight: 600`, `font-variant-numeric: tabular-nums`.
Lição da Fase 6-E: densidade não deve ser alcançada com fonte pequena.

**Fonte:** Inter (Google Fonts CDN), carregada no `index.html`.

---

## Ícones

- **Sistema:** Tabler Icons
- **Carregamento:** CDN `https://cdn.jsdelivr.net/npm/@tabler/icons-webfont`
- **Uso:** `<i className="ti ti-home" aria-hidden="true" />`
- Nunca usar emojis como ícones de UI. Emojis ficam restritos a strings textuais contextuais.

---

## Classes utility

### Form fields

```css
.m-field        /* wrapper de campo */
.m-label        /* label */
.m-input        /* input / textarea / select estilizado */
.m-row          /* row de campos lado a lado */
```

### Botões

```css
.m-btn-primary    /* botão de ação principal */
.m-btn-secondary  /* botão secundário */
.m-btn-cancel     /* botão de cancelar / terciário */
```

### Badges

```css
.m-badge-success
.m-badge-warning
.m-badge-danger
.m-badge-info
```

### Section card

```css
.m-section-card           /* wrapper de seção */
.m-section-card-header    /* header com título + ações */
```

---

## Componentes reutilizáveis

### Modal wrapper

`src/components/Modal/Modal.jsx` — substitui os 26 modais inline que existiam no App.js original.

Props:
| Prop | Tipo | Descrição |
|---|---|---|
| `isOpen` | bool | Controla visibilidade |
| `onClose` | fn | Callback de fechamento |
| `title` | string | Título do header |
| `width` | `'default'` \| `'small'` \| `'large'` \| `'xlarge'` | Largura |
| `footer` | ReactNode | Slot para botões de ação |

Comportamentos inclusos:
- Fecha com `Esc`
- Fecha com click fora
- Focus trap (acessibilidade)
- Scroll lock no `<body>`

### StatCard / StatCardCompact / AlertStatCard

`src/screens/Dashboard/components/` — reutilizáveis em qualquer dashboard.

### ProgressBar / ActivityItem

Componentes do Dashboard, genéricos o suficiente para reuso.

### DashboardHeader

`src/screens/Dashboard/components/DashboardHeader.jsx` — header com logo, sino de notificações e avatar com dropdown (voltar ao seletor, logout).

---

## Spacing

| Contexto | Valor |
|---|---|
| Padding de card (compact) | 16–20px |
| Padding de card (default) | 20–24px |
| Gap entre cards | 12–16px |
| Padding de linha em tabela | 14px vertical |
| Padding de botão | 8–12px vertical, 14–20px horizontal |

---

## Responsive

- Breakpoint mobile: `max-width: 600px`
- Cards colapsam de grid para stack
- DashboardHeader ajusta padding com margin negativa para sair do padding do container
- Tipografia ajustada em media queries (geralmente -1 a -2px)
