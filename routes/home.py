"""Blueprint /home — agregados para a tela inicial (seletor de módulos) e
para a home do módulo Obras. Todas as rotas exigem JWT.

/home/alertas junta pendências de pagamento (vencidas ou vencendo em breve)
das fontes do banco MAIN (módulo Obras) e do banco ADMIN (patrimônio, leitura
read-only via admin_read_service), respeitando os módulos permitidos do
usuário e o scoping por obra. Erros de validação sempre 400, nunca 422.
"""
import io
import logging
from datetime import date, datetime, timedelta

from flask import Blueprint, jsonify, make_response, request
from flask_jwt_extended import jwt_required

from calendar import monthrange

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from extensions import db
from models.obra import Obra
from models.lancamento import Lancamento
from models.boleto import Boleto
from models.parcela_individual import ParcelaIndividual
from models.pagamento_parcelado import PagamentoParcelado
from models.pagamento_futuro import PagamentoFuturo
from models.pagamento_servico import PagamentoServico
from models.servico import Servico
from services import admin_read_service
from services import get_current_user, user_tem_modulo
from utils import formatar_real

logger = logging.getLogger(__name__)

home_bp = Blueprint('home', __name__, url_prefix='/home')

DIAS_A_VENCER_DEFAULT = 3


def _obras_visiveis(user):
    """Map {id: nome} das obras ATIVAS que o usuário enxerga (nem arquivada,
    nem concluída — uma obra concluída para de gerar alerta de pendência,
    mesmo que ainda tenha algo em aberto no financeiro)."""
    query = Obra.query.filter(Obra.arquivada.isnot(True), Obra.concluida.isnot(True))
    if user.role not in ('master', 'administrador'):
        ids = [o.id for o in user.obras_permitidas]
        query = query.filter(Obra.id.in_(ids))
    return {o.id: o.nome for o in query.all()}


def _situacao(venc, hoje):
    if venc < hoje:
        return 'vencido'
    if venc == hoje:
        return 'vence_hoje'
    return 'a_vencer'


def _item(modulo, origem, descricao, valor, venc, hoje, origem_id=None, tipo=None):
    return {
        'modulo': modulo,
        'origem': origem,
        'origem_id': origem_id,
        'descricao': descricao,
        'valor': round(float(valor or 0), 2),
        'data_vencimento': venc.isoformat(),
        'situacao': _situacao(venc, hoje),
        'dias': (venc - hoje).days,
        # tipo orienta o frontend a abrir direto na aba certa da obra em vez
        # da home genérica: 'lancamento'|'parcela'|'boleto'|'pagamento_futuro'
        'tipo': tipo,
    }


def _pendencias_obras(user, corte, hoje):
    obras = _obras_visiveis(user)
    if not obras:
        return []
    ids = list(obras.keys())
    itens = []

    # Lançamentos a pagar
    lancs = (Lancamento.query
             .filter(Lancamento.obra_id.in_(ids),
                     Lancamento.status == 'A Pagar',
                     Lancamento.data_vencimento.isnot(None),
                     Lancamento.data_vencimento <= corte)
             .all())
    for l in lancs:
        restante = max((l.valor_total or 0) - (l.valor_pago or 0), 0) or (l.valor_total or 0)
        desc = l.descricao + (f' — {l.fornecedor}' if l.fornecedor else '')
        itens.append(_item('obras', obras.get(l.obra_id), desc, restante,
                           l.data_vencimento, hoje, l.obra_id, tipo='lancamento'))

    # Parcelas de pagamento parcelado (inclui parcelas de boleto a vencer)
    parcelas = (db.session.query(ParcelaIndividual, PagamentoParcelado)
                .join(PagamentoParcelado,
                      ParcelaIndividual.pagamento_parcelado_id == PagamentoParcelado.id)
                .filter(PagamentoParcelado.obra_id.in_(ids),
                        ParcelaIndividual.status.in_(['Previsto', 'Pendente']),
                        ParcelaIndividual.data_vencimento <= corte)
                .all())
    for p, pp in parcelas:
        desc = f'{pp.descricao} — parcela {p.numero_parcela}/{pp.numero_parcelas}'
        itens.append(_item('obras', obras.get(pp.obra_id), desc, p.valor_parcela,
                           p.data_vencimento, hoje, pp.obra_id, tipo='parcela'))

    # Boletos (Vencido explícito, ou Pendente com vencimento no corte)
    boletos = (Boleto.query
               .filter(Boleto.obra_id.in_(ids),
                       Boleto.status != 'Pago',
                       Boleto.data_vencimento <= corte)
               .all())
    for b in boletos:
        desc = 'Boleto ' + (b.descricao or b.beneficiario or 's/ descrição')
        itens.append(_item('obras', obras.get(b.obra_id), desc, b.valor,
                           b.data_vencimento, hoje, b.obra_id, tipo='boleto'))

    # Pagamentos futuros (cronograma)
    futuros = (PagamentoFuturo.query
               .filter(PagamentoFuturo.obra_id.in_(ids),
                       PagamentoFuturo.status == 'Previsto',
                       PagamentoFuturo.data_vencimento <= corte)
               .all())
    for f in futuros:
        desc = f.descricao + (f' — {f.fornecedor}' if f.fornecedor else '')
        itens.append(_item('obras', obras.get(f.obra_id), desc, f.valor,
                           f.data_vencimento, hoje, f.obra_id, tipo='pagamento_futuro'))

    return itens


@home_bp.route('/alertas', methods=['GET'])
@jwt_required()
def alertas():
    """Pendências vencidas/a vencer dos módulos que o usuário acessa.

    ?dias=N controla a janela de "a vencer" (default 3, máx 60)."""
    try:
        user = get_current_user()
        if not user:
            return jsonify({"erro": "Usuário não encontrado"}), 401
        try:
            dias = min(max(int(request.args.get('dias', DIAS_A_VENCER_DEFAULT)), 0), 60)
        except (TypeError, ValueError):
            return jsonify({"erro": "dias inválido"}), 400

        hoje = date.today()
        corte = hoje + timedelta(days=dias)
        pendencias = []
        aviso_admin = None

        if user_tem_modulo(user, 'obras'):
            pendencias.extend(_pendencias_obras(user, corte, hoje))

        if user_tem_modulo(user, 'admin'):
            itens_admin, aviso_admin = admin_read_service.listar_pendencias(corte)
            for it in itens_admin:
                venc = it['data_vencimento']
                pendencias.append(_item('admin', it['imovel_nome'], it['descricao'],
                                        it['valor'], venc, hoje, it['imovel_id']))

        # vencidos primeiro (mais antigos no topo), depois por vencimento
        _ordenar = lambda lst: sorted(lst, key=lambda x: (x['situacao'] != 'vencido', x['data_vencimento']))

        def _resumo(mod):
            do_mod = [p for p in pendencias if p['modulo'] == mod]
            vencidos = [p for p in do_mod if p['situacao'] == 'vencido']
            return {
                'qtd': len(do_mod),
                'vencidos': len(vencidos),
                'valor_total': round(sum(p['valor'] for p in do_mod), 2),
                'valor_vencido': round(sum(p['valor'] for p in vencidos), 2),
            }

        resumo = {'obras': _resumo('obras'), 'admin': _resumo('admin')}

        # SEM corte de quantidade: qualquer limite aqui (ex.: [:30] combinado,
        # ou até um corte por módulo) já causou contagem errada no frontend —
        # o card/badge de vencidos soma o array que a API devolve, então um
        # array truncado = número errado mostrado pro usuário. resumo já é
        # a contagem correta (calculada antes de qualquer corte); a lista
        # completa é a fonte de verdade tanto pra ela quanto pro detalhe.
        pendencias = _ordenar(pendencias)

        return jsonify({
            'pendencias': pendencias,
            'resumo': resumo,
            'aviso_admin': aviso_admin,
            'dias': dias,
        }), 200
    except Exception as e:
        logger.exception("Erro em GET /home/alertas")
        return jsonify({"erro": "Erro ao montar alertas", "detalhe": str(e)}), 500


@home_bp.route('/pendencias/export-pdf', methods=['GET'])
@jwt_required()
def export_pendencias_pdf():
    """PDF das pendências de Obras, agrupado por obra.

    ?escopo=vencidas (só o que já venceu) ou todas (vencidas + a vencer até
    o fim do mês corrente) — default 'todas'. Reusa _pendencias_obras, a
    mesma fonte de /home/alertas, então o total bate com o que já aparece
    no dashboard."""
    try:
        escopo = request.args.get('escopo', 'todas')
        if escopo not in ('vencidas', 'todas'):
            return jsonify({"erro": "escopo inválido (use vencidas ou todas)"}), 400

        user = get_current_user()
        if not user:
            return jsonify({"erro": "Usuário não encontrado"}), 401
        if not user_tem_modulo(user, 'obras'):
            return jsonify({"erro": "Acesso negado: você não tem permissão para o módulo Obras."}), 403

        hoje = date.today()
        if escopo == 'todas':
            corte = date(hoje.year, hoje.month, monthrange(hoje.year, hoje.month)[1])
        else:
            corte = hoje

        itens = _pendencias_obras(user, corte, hoje)
        if escopo == 'vencidas':
            itens = [i for i in itens if i['situacao'] == 'vencido']
        itens.sort(key=lambda x: (x['origem'] or '', x['data_vencimento']))

        if not itens:
            return jsonify({"mensagem": "Nenhuma pendência encontrada para esse filtro"}), 200

        por_obra = {}
        for it in itens:
            por_obra.setdefault(it['origem'] or 'Sem obra', []).append(it)
        total_geral = sum(i['valor'] for i in itens)

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=2 * cm, bottomMargin=2 * cm,
                                leftMargin=2 * cm, rightMargin=2 * cm)
        elements = []
        styles = getSampleStyleSheet()

        titulo = ('Relatório de Pendências Vencidas' if escopo == 'vencidas'
                  else 'Relatório de Pendências (Vencidas + a Vencer no Mês)')
        elements.append(Paragraph(
            f"<b>{titulo}</b><br/><br/>Obras: {len(por_obra)} — Total geral: {formatar_real(total_geral)}",
            styles['Title']))
        elements.append(Spacer(1, 0.8 * cm))

        _SIT_LABEL = {'vencido': lambda it: f"Vencido há {abs(it['dias'])}d",
                      'vence_hoje': lambda it: 'Vence hoje',
                      'a_vencer': lambda it: f"Vence em {it['dias']}d"}

        obras_nomes = list(por_obra.keys())
        for idx, obra_nome in enumerate(obras_nomes):
            obra_itens = por_obra[obra_nome]
            total_obra = sum(i['valor'] for i in obra_itens)
            elements.append(Paragraph(
                f"<b>Obra: {obra_nome}</b> | Total: {formatar_real(total_obra)}", styles['Heading2']))
            elements.append(Spacer(1, 0.3 * cm))

            data = [['Vencimento', 'Situação', 'Descrição', 'Valor']]
            for it in obra_itens:
                venc = it['data_vencimento']
                venc_br = f"{venc[8:10]}/{venc[5:7]}/{venc[0:4]}"
                sit_fn = _SIT_LABEL.get(it['situacao'])
                data.append([
                    venc_br,
                    sit_fn(it) if sit_fn else it['situacao'],
                    (it['descricao'] or '')[:50],
                    formatar_real(it['valor']),
                ])
            data.append(['', '', 'SUBTOTAL', formatar_real(total_obra)])

            table = Table(data, colWidths=[2.5 * cm, 2.8 * cm, 8.2 * cm, 3 * cm])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0061FC')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
                ('TOPPADDING', (0, 0), (-1, 0), 10),
                ('BACKGROUND', (0, 1), (-1, -2), colors.white),
                ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
                ('ALIGN', (0, 1), (-1, -1), 'LEFT'),
                ('ALIGN', (3, 1), (3, -1), 'RIGHT'),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 8),
                ('GRID', (0, 0), (-1, -1), 1, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#f8f9fa')]),
                ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#0061FC')),
                ('TEXTCOLOR', (0, -1), (-1, -1), colors.whitesmoke),
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, -1), (-1, -1), 10),
                ('ALIGN', (2, -1), (3, -1), 'RIGHT'),
            ]))
            elements.append(table)
            if idx < len(obras_nomes) - 1:
                elements.append(Spacer(1, 0.8 * cm))

        elements.append(Spacer(1, 1 * cm))
        elements.append(Paragraph(f"<b>TOTAL GERAL: {formatar_real(total_geral)}</b>", styles['Heading1']))
        elements.append(Spacer(1, 0.5 * cm))
        elements.append(Paragraph(f"Gerado em: {datetime.now().strftime('%d/%m/%Y às %H:%M')}", styles['Normal']))

        doc.build(elements)
        buffer.seek(0)
        pdf_data = buffer.read()
        buffer.close()

        response = make_response(pdf_data)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = (
            f'attachment; filename=pendencias_{escopo}_{hoje.isoformat()}.pdf')
        return response
    except Exception as e:
        logger.exception("Erro em GET /home/pendencias/export-pdf")
        return jsonify({"erro": "Erro ao gerar PDF", "detalhe": str(e)}), 500


def _classe_gasto(tipo):
    """Classifica o tipo do lançamento nas 5 categorias reais que existem em
    produção: Mão de Obra, Material, Equipamentos, Serviço, Despesa. Cada uma
    vira seu próprio total — nenhuma é forçada dentro de MO/Material (Serviço
    é misto mão-de-obra/logística e Despesa é majoritariamente material
    lançado errado na digitação; auditoria real mostrou que "adivinhar" um
    destino único distorce os totais em vez de corrigi-los)."""
    if tipo == 'Mão de Obra':
        return 'mo'
    if tipo == 'Material':
        return 'material'
    if tipo == 'Equipamentos':
        return 'equipamento'
    if tipo == 'Serviço':
        return 'servico'
    if tipo == 'Despesa':
        return 'despesa'
    return 'outros'


@home_bp.route('/obras', methods=['GET'])
@jwt_required()
def home_obras():
    """Agregado da home do módulo Obras (?competencia=YYYY-MM, default mês atual).

    MO e material são TOTAIS ACUMULADOS da obra (pedido do usuário: "total
    gasto"); saídas e previsão a pagar são do mês. Fontes: lançamentos pagos,
    pagamentos de serviço e parcelas pagas (mesmas do /bi/historico-mensal).
    Previsão a pagar = tudo em aberto com vencimento até o fim do mês."""
    try:
        user = get_current_user()
        if not user:
            return jsonify({"erro": "Usuário não encontrado"}), 401
        if not user_tem_modulo(user, 'obras'):
            return jsonify({"erro": "Acesso negado: você não tem permissão para o módulo Obras."}), 403

        competencia = request.args.get('competencia') or date.today().strftime('%Y-%m')
        try:
            ano, mes = int(competencia[:4]), int(competencia[5:7])
            inicio = date(ano, mes, 1)
            fim = date(ano, mes, monthrange(ano, mes)[1])
        except Exception:
            return jsonify({"erro": "competencia inválida (use YYYY-MM)"}), 400

        obras_map = _obras_visiveis(user)
        ids = list(obras_map.keys())
        hoje = date.today()

        _ZERO_CATS = {'mo_total': 0.0, 'material_total': 0.0, 'equipamento_total': 0.0,
                      'servico_total': 0.0, 'despesa_total': 0.0, 'boleto_total': 0.0}
        por_obra = {oid: {**_ZERO_CATS, 'vencidos_qtd': 0, 'vencidos_valor': 0.0} for oid in ids}
        totais = dict(_ZERO_CATS)
        saidas_mes = 0.0
        _CAMPO_POR_CLASSE = {'mo': 'mo_total', 'material': 'material_total',
                             'equipamento': 'equipamento_total', 'servico': 'servico_total',
                             'despesa': 'despesa_total', 'boleto': 'boleto_total'}

        def _acumula(obra_id, classe, valor, data_ref):
            nonlocal saidas_mes
            if data_ref and inicio <= data_ref <= fim:
                saidas_mes += valor
            campo = _CAMPO_POR_CLASSE.get(classe)
            if campo:
                totais[campo] += valor
                por_obra[obra_id][campo] += valor

        if ids:
            # Lançamentos pagos (todos; data_ref = data ou vencimento — regra do BI).
            # Exclui os lançamentos-ESPELHO criados ao pagar parcela (padrão
            # "<desc> (Parcela X/Y)" em sid.marcar_parcela_paga) — a parcela é a
            # fonte canônica; somar os dois duplicaria as saídas.
            lancs = (Lancamento.query
                     .filter(Lancamento.obra_id.in_(ids), Lancamento.status == 'Pago',
                             ~Lancamento.descricao.like('%(Parcela %'))
                     .all())
            for l in lancs:
                _acumula(l.obra_id, _classe_gasto(l.tipo),
                         l.valor_pago or l.valor_total or 0,
                         l.data or l.data_vencimento)

            # Pagamentos de serviço
            pagtos = (db.session.query(PagamentoServico, Servico.obra_id)
                      .join(Servico, PagamentoServico.servico_id == Servico.id)
                      .filter(Servico.obra_id.in_(ids),
                              PagamentoServico.valor_pago > 0)
                      .all())
            for ps, obra_id in pagtos:
                classe = ('mo' if ps.tipo_pagamento == 'mao_de_obra'
                          else 'equipamento' if ps.tipo_pagamento == 'equipamento'
                          else 'material')
                _acumula(obra_id, classe, ps.valor_pago or 0, ps.data)

            # Parcelas pagas (split pelo segmento do parcelamento)
            parcelas_pagas = (db.session.query(ParcelaIndividual, PagamentoParcelado)
                              .join(PagamentoParcelado,
                                    ParcelaIndividual.pagamento_parcelado_id == PagamentoParcelado.id)
                              .filter(PagamentoParcelado.obra_id.in_(ids),
                                      ParcelaIndividual.status == 'Pago')
                              .all())
            for p, pp in parcelas_pagas:
                segmento = pp.segmento or 'Material'
                classe = ('mo' if segmento == 'Mão de Obra'
                          else 'equipamento' if segmento == 'Equipamento'
                          else 'material')
                _acumula(pp.obra_id, classe, p.valor_parcela or 0,
                         p.data_pagamento or p.data_vencimento)

            # Boletos pagos — o modelo não tem um campo "tipo" (Mão de
            # Obra/Material/...) pra classificar, então viram sua própria
            # categoria em vez de forçar um destino adivinhado.
            boletos_pagos = (Boleto.query
                             .filter(Boleto.obra_id.in_(ids), Boleto.status == 'Pago')
                             .all())
            for b in boletos_pagos:
                _acumula(b.obra_id, 'boleto', b.valor or 0,
                         b.data_pagamento or b.data_vencimento)

        # Previsão a pagar: em aberto com vencimento até o fim do mês
        # (reusa as mesmas fontes do /home/alertas com corte = fim do mês)
        pendencias = _pendencias_obras(user, fim, hoje) if ids else []
        pendencias.sort(key=lambda x: (x['situacao'] != 'vencido', x['data_vencimento']))
        previsao_total = round(sum(p['valor'] for p in pendencias), 2)
        for p in pendencias:
            if p['situacao'] == 'vencido' and p['origem_id'] in por_obra:
                por_obra[p['origem_id']]['vencidos_qtd'] += 1
                por_obra[p['origem_id']]['vencidos_valor'] += p['valor']

        obras_out = []
        for oid, nome in obras_map.items():
            d = por_obra[oid]
            obras_out.append({
                'id': oid,
                'nome': nome,
                'mo_total': round(d['mo_total'], 2),
                'material_total': round(d['material_total'], 2),
                'equipamento_total': round(d['equipamento_total'], 2),
                'servico_total': round(d['servico_total'], 2),
                'despesa_total': round(d['despesa_total'], 2),
                'boleto_total': round(d['boleto_total'], 2),
                'vencidos_qtd': d['vencidos_qtd'],
                'vencidos_valor': round(d['vencidos_valor'], 2),
            })

        return jsonify({
            'competencia': competencia,
            'kpis': {
                'mo_total': round(totais['mo_total'], 2),
                'material_total': round(totais['material_total'], 2),
                'equipamento_total': round(totais['equipamento_total'], 2),
                'servico_total': round(totais['servico_total'], 2),
                'despesa_total': round(totais['despesa_total'], 2),
                'boleto_total': round(totais['boleto_total'], 2),
                'saidas_mes': round(saidas_mes, 2),
                'previsao_pagar': {'total': previsao_total, 'qtd': len(pendencias),
                                   'ate': fim.isoformat(), 'itens': pendencias},
            },
            'obras': obras_out,
        }), 200
    except Exception as e:
        logger.exception("Erro em GET /home/obras")
        return jsonify({"erro": "Erro ao montar home de obras", "detalhe": str(e)}), 500
