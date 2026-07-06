"""Lógica de negócio do RH que cruza models: piso vigente e dashboard (com rateio)."""
import logging
from datetime import date

from sqlalchemy import func

from extensions import db
from models.convencao_coletiva import ConvencaoColetiva
from models.convencao_valor import ConvencaoValor
from models.funcionario import Funcionario
from models.pagamento_salario import PagamentoSalario
from models.encargo import Encargo
from models.obra import Obra

logger = logging.getLogger(__name__)

_ENCARGO_LABEL = {
    'fgts': 'FGTS',
    'inss_darf': 'INSS · DARF',
    'esocial_dae': 'eSocial · DAE',
    'outro': 'Outro',
}


def piso_vigente(categoria_id, uf):
    """Piso da CCT `confirmada` vigente do estado para a categoria.

    Prefere a convenção vigente hoje; se nenhuma estiver vigente, cai para a
    confirmada mais recente daquele estado/categoria. Retorna float ou None.
    """
    if not categoria_id or not uf:
        return None

    base = (
        db.session.query(ConvencaoValor.piso_salarial, ConvencaoColetiva.vigencia_inicio,
                         ConvencaoColetiva.vigencia_fim)
        .join(ConvencaoColetiva, ConvencaoValor.convencao_id == ConvencaoColetiva.id)
        .filter(
            ConvencaoValor.categoria_id == categoria_id,
            func.upper(ConvencaoColetiva.uf) == uf.upper(),
            ConvencaoColetiva.status == 'confirmada',
        )
    )
    hoje = date.today()
    vigente = (
        base.filter(ConvencaoColetiva.vigencia_inicio <= hoje,
                    ConvencaoColetiva.vigencia_fim >= hoje)
        .order_by(ConvencaoColetiva.vigencia_inicio.desc())
        .first()
    )
    row = vigente or base.order_by(ConvencaoColetiva.vigencia_fim.desc()).first()
    return float(row[0]) if row and row[0] is not None else None


def piso_vigente_batch(pares):
    """Versão em lote de `piso_vigente` p/ evitar N+1 em listagens de
    funcionários: recebe um iterável de (categoria_id, uf) e resolve cada
    combinação única uma única vez (várias linhas costumam repetir a mesma
    categoria/UF). Retorna {(categoria_id, uf): piso}."""
    unicos = {(cid, uf) for cid, uf in pares if cid and uf}
    return {par: piso_vigente(par[0], par[1]) for par in unicos}


def piso_vigente_funcionario(funcionario):
    """Piso vigente para um funcionário: UF vem da obra vinculada (se houver)."""
    try:
        uf = funcionario.obra.uf if funcionario.obra else None
    except Exception:
        uf = None
    if not uf:
        return None
    return piso_vigente(funcionario.categoria_id, uf)


def _somar_por_obra(competencia):
    """Soma de salários (tipo 'salario') por obra_id snapshot na competência."""
    rows = (
        db.session.query(PagamentoSalario.obra_id, func.coalesce(func.sum(PagamentoSalario.valor), 0))
        .filter(PagamentoSalario.competencia == competencia,
                PagamentoSalario.tipo == 'salario')
        .group_by(PagamentoSalario.obra_id)
        .all()
    )
    return {obra_id: float(total) for obra_id, total in rows}


def dashboard(competencia):
    """Payload do dashboard do RH para a competência 'YYYY-MM' (ver §6 do spec)."""
    # --- Folha (salários) ---
    folha_total = float(
        db.session.query(func.coalesce(func.sum(PagamentoSalario.valor), 0))
        .filter(PagamentoSalario.competencia == competencia,
                PagamentoSalario.tipo == 'salario')
        .scalar() or 0
    )

    # --- Encargos ---
    encargos_total = float(
        db.session.query(func.coalesce(func.sum(Encargo.valor), 0))
        .filter(Encargo.competencia == competencia)
        .scalar() or 0
    )
    custo_total = folha_total + encargos_total
    pct_encargos = round(encargos_total / folha_total * 100) if folha_total else None

    # --- MO por obra (salários snapshot + encargos diretos + rateio do Geral) ---
    salarios_por_obra = _somar_por_obra(competencia)

    encargos_diretos = {}
    encargos_geral = 0.0
    enc_rows = (
        db.session.query(Encargo.obra_id, func.coalesce(func.sum(Encargo.valor), 0))
        .filter(Encargo.competencia == competencia)
        .group_by(Encargo.obra_id)
        .all()
    )
    for obra_id, total in enc_rows:
        if obra_id is None:
            encargos_geral += float(total)
        else:
            encargos_diretos[obra_id] = float(total)

    # Conjunto de obras que aparecem (salários ou encargos diretos), incl. None (Sem obra)
    obra_ids = set(salarios_por_obra) | set(encargos_diretos)

    # Nomes das obras
    nomes = {}
    ids_reais = [oid for oid in obra_ids if oid is not None]
    if ids_reais:
        for obra in Obra.query.filter(Obra.id.in_(ids_reais)).all():
            nomes[obra.id] = obra.nome

    mo_por_obra = []
    for oid in obra_ids:
        salarios = salarios_por_obra.get(oid, 0.0)
        enc_direto = encargos_diretos.get(oid, 0.0)
        rateio = 0.0
        estimado = False
        # Rateio proporcional só quando há folha p/ ratear (evita divisão por zero)
        if encargos_geral and folha_total > 0 and salarios > 0:
            rateio = encargos_geral * (salarios / folha_total)
            estimado = True
        encargos_obra = enc_direto + rateio
        mo_por_obra.append({
            'obra_id': oid,
            'obra': nomes.get(oid, 'Sem obra') if oid is not None else 'Sem obra',
            'salarios': round(salarios, 2),
            'encargos': round(encargos_obra, 2),
            'total': round(salarios + encargos_obra, 2),
            'encargo_estimado': estimado,
        })

    # Sem folha p/ ratear: encargos "Geral" não somem — vão num balde único "Geral".
    if encargos_geral and folha_total == 0:
        mo_por_obra.append({
            'obra_id': None,
            'obra': 'Geral',
            'salarios': 0.0,
            'encargos': round(encargos_geral, 2),
            'total': round(encargos_geral, 2),
            'encargo_estimado': False,
        })

    mo_por_obra.sort(key=lambda x: x['total'], reverse=True)

    # --- Folha por segmento (categoria): total via pagamentos, qtd = ativos ---
    seg_rows = (
        db.session.query(Funcionario.categoria_id, func.coalesce(func.sum(PagamentoSalario.valor), 0))
        .join(PagamentoSalario, PagamentoSalario.funcionario_id == Funcionario.id)
        .filter(PagamentoSalario.competencia == competencia,
                PagamentoSalario.tipo == 'salario')
        .group_by(Funcionario.categoria_id)
        .all()
    )
    from models.categoria_mo import CategoriaMO
    cat_nomes = {c.id: c.nome for c in CategoriaMO.query.all()}
    qtd_ativos = dict(
        db.session.query(Funcionario.categoria_id, func.count(Funcionario.id))
        .filter(Funcionario.status == 'ativo')
        .group_by(Funcionario.categoria_id)
        .all()
    )
    folha_por_segmento = [
        {
            'categoria': cat_nomes.get(cat_id, '—'),
            'qtd': int(qtd_ativos.get(cat_id, 0)),
            'total': round(float(total), 2),
        }
        for cat_id, total in seg_rows
    ]
    folha_por_segmento.sort(key=lambda x: x['total'], reverse=True)

    # --- Encargos por tipo ---
    tipo_rows = (
        db.session.query(Encargo.tipo, func.coalesce(func.sum(Encargo.valor), 0))
        .filter(Encargo.competencia == competencia)
        .group_by(Encargo.tipo)
        .all()
    )
    encargos_por_tipo = [
        {
            'tipo': tipo,
            'label': _ENCARGO_LABEL.get(tipo, (tipo or '—').title()),
            'total': round(float(total), 2),
        }
        for tipo, total in tipo_rows
    ]
    encargos_por_tipo.sort(key=lambda x: x['total'], reverse=True)

    return {
        'folha_total': round(folha_total, 2),
        'encargos_total': round(encargos_total, 2),
        'custo_total': round(custo_total, 2),
        'pct_encargos': pct_encargos,
        'mo_por_obra': mo_por_obra,
        'folha_por_segmento': folha_por_segmento,
        'encargos_por_tipo': encargos_por_tipo,
    }
