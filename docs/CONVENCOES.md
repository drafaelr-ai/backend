# Convenções — Obraly

---

## Naming

### Backend (Python)

| Caso | Uso |
|---|---|
| `snake_case` | módulos, funções, variáveis |
| `PascalCase` | classes (models, services se for classe) |
| `UPPER_SNAKE_CASE` | constantes (`ALLOWED_ORIGINS`, `JWT_SECRET_KEY`) |
| `nome_bp` | blueprints (`auth_bp`, `notificacoes_bp`) |
| `nome_service.py` | arquivo de service (`auth_service.py`) |

### Frontend (JavaScript/JSX)

| Caso | Uso |
|---|---|
| `camelCase` | variáveis, funções, hooks |
| `PascalCase` | componentes React |
| `UPPER_SNAKE_CASE` | constantes (`API_URL`, `MAX_FILE_SIZE`) |
| `NomeModal` | componentes de modal (`EditPrioridadeModal`) |
| `NomeCard` | componentes de card (`StatCard`, `ObraCard`) |

---

## Estrutura de arquivos

### Backend

```
1 model por arquivo em models/
1 domínio de service por arquivo em services/
1 blueprint por domínio em routes/
helpers globais em utils.py
configuração em config.py
extensões compartilhadas em extensions.py
```

### Frontend

```
1 screen por pasta em screens/<Nome>/index.jsx
1 componente por arquivo em components/
sub-componentes em components/<Componente>/SubComponente.jsx
estilos compartilhados em styles/tokens.css
utilitários em utils/
```

---

## Ordem de imports

### Backend

```python
# 1. stdlib
import os
from datetime import datetime

# 2. third-party
from flask import Blueprint, request, jsonify
from sqlalchemy import func

# 3. local (extensions → services → models)
from extensions import db
from services import get_current_user, user_has_access_to_obra
from models.obra import Obra
from models.lancamento import Lancamento
```

### Frontend

```jsx
// 1. React
import React, { useState, useEffect, useCallback } from 'react';

// 2. Third-party
import axios from 'axios';

// 3. Internal — screens / components
import Modal from '../Modal/Modal';
import StatCard from '../components/StatCard';

// 4. Utils
import { notify } from '../../utils/notify';
import { formatCurrency } from '../../utils/format';

// 5. Styles
import './ComponentName.css';
```

---

## CSS

- **Sempre** usar `var(--*)` tokens — zero hex hardcoded
- Padding/spacing consistente: 8, 12, 14, 16, 20, 24px
- `border-radius` via `var(--radius-*)`
- `box-shadow` via `var(--shadow-*)`
- Não usar `style={{}}` inline para valores estéticos — usar classes

---

## Modal (criando um novo)

```jsx
import Modal from 'components/Modal/Modal';

function MeuModal({ isOpen, onClose, dados }) {
  function handleSave() { /* ... */ }

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Título do modal"
      width="default"
      footer={
        <>
          <button className="m-btn-cancel" onClick={onClose}>Cancelar</button>
          <button className="m-btn-primary" onClick={handleSave}>Salvar</button>
        </>
      }
    >
      <div className="m-field">
        <label className="m-label">Campo</label>
        <input className="m-input" value={dados.nome} onChange={...} />
      </div>
    </Modal>
  );
}
```

---

## Notificações / toasts

```jsx
import { notify } from 'utils/notify';

notify.success('Salvo com sucesso');
notify.error('Erro ao salvar');
notify.warning('Atenção: verifique os dados');
notify.info('Processando...');

// Para confirmação com callback:
import { confirmDialog } from 'utils/notify';
confirmDialog('Tem certeza?', () => { /* ação */ });
```

Nunca usar `alert()`, `confirm()`, `prompt()`, ou `window.confirm()`.

---

## Logging

### Frontend
```js
import logger from 'utils/logger';

logger.info('Obra carregada', obra.id);
logger.warn('Campo vazio', campo);
logger.error('Erro ao buscar obras', error);
```

Nunca usar `console.log`, `console.warn`, `console.error` diretamente.

### Backend
```python
import logging
logger = logging.getLogger(__name__)

logger.info("Rota acessada")
logger.warning("Campo faltando: %s", campo)
logger.error("Erro ao processar: %s", str(e))
logger.exception("Detalhe do traceback:")  # inclui stack trace
```

Nunca usar `print()` diretamente.

---

## Commits

Padrão de mensagem:

```
<tipo>(<escopo>): <descrição curta em imperativo>

<corpo opcional — detalhe do que mudou e por quê>
```

**Tipos:**

| Tipo | Uso |
|---|---|
| `feat` | nova feature |
| `fix` | correção de bug |
| `refactor` | mudança de estrutura sem alterar comportamento |
| `docs` | documentação |
| `style` | formatação, espaços (zero impacto funcional) |
| `chore` | tarefas auxiliares (config, deps) |

**Escopo:** número da fase (`fase-1`, `fase-4`) ou nome do módulo (`obras`, `cronograma`).

**Exemplo:**
```
refactor(fase-4): extract auth helpers to services/auth_service.py

- get_current_user (142 callers)
- user_has_access_to_obra (128 callers)
- check_permission (56 callers)

app.py reduzido em ~31 linhas.
```

---

## O que evitar

```
❌  Hex hardcoded em CSS/JSX  →  use var(--)
❌  Emojis como ícones de UI  →  use Tabler Icons
❌  alert() / confirm()        →  use notify / confirmDialog
❌  console.log / print()      →  use logger.*
❌  bare except:               →  use except Exception as e:
❌  JWT_SECRET_KEY fallback    →  RuntimeError obrigatório
❌  fetch() direto             →  use fetchWithAuth
❌  window.open(url) para download de arquivo autenticado  →  use fetchWithAuth + blob
❌  inline style complexo      →  use classe CSS
❌  Estado espalhado em 10+ useState  →  considere useReducer
```
