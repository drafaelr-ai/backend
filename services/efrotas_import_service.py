"""Persistência idempotente dos eventos de infração do e-Frotas.

O webhook e a carga retrospectiva usam a mesma regra de negócio. Assim, uma
penalidade recebida depois da autuação atualiza a infração existente, em vez de
criar duas multas para o mesmo AIT.
"""
from datetime import datetime
import logging

from sqlalchemy.exc import IntegrityError

from extensions import db
from models.frota_efrotas_evento import FrotaEFrotasEvento
from models.frota_multa import FrotaMulta
from models.frota_veiculo import FrotaVeiculo
from models.notificacao import Notificacao
from models.user import User
from services import telegram_service
from services.efrotas_service import (
    EventoEFrotasInvalido,
    identificadores_infracao,
    interpretar_evento_infracao,
    normalizar_placa,
)

logger = logging.getLogger(__name__)


def _texto(valor, limite=300):
    texto = str(valor or '').strip()
    return texto[:limite] if texto else None


def _metadados(payload):
    if not isinstance(payload, dict):
        return None, None
    return (
        _texto(payload.get('idRastreamento') or payload.get('id_rastreamento'), 100),
        _texto(payload.get('tipoEvento') or payload.get('tipo_evento'), 50),
    )


def _evento_auditoria(payload, *, id_rastreamento, tipo_evento, status, erro=None, multa_id=None):
    """Atualiza um callback recuperável ou cria uma entrada nova de auditoria."""
    evento = None
    if id_rastreamento:
        evento = FrotaEFrotasEvento.query.filter_by(id_rastreamento=id_rastreamento).first()
    if not evento:
        evento = FrotaEFrotasEvento(id_rastreamento=id_rastreamento, payload={})
        db.session.add(evento)
    evento.tipo_evento = tipo_evento
    evento.status = status
    evento.erro = _texto(erro, 500)
    evento.payload = payload if isinstance(payload, dict) else {'payload_invalido': True}
    evento.multa_id = multa_id
    evento.processado_em = datetime.utcnow() if status == 'processado' else None
    return evento


def _destinatarios(veiculo):
    ids = {
        user_id for (user_id,) in db.session.query(User.id)
        .filter(User.role.in_(('master', 'administrador'))).all()
    }
    condutor = veiculo.condutor_atual
    if condutor and condutor.usuario_id:
        ids.add(condutor.usuario_id)
    return sorted(ids)


def _criar_notificacoes(multa, veiculo):
    prazo = multa.data_limite_defesa or multa.data_vencimento_penalidade
    detalhes = [
        f'Veículo {veiculo.placa}',
        multa.descricao or 'Infração de trânsito',
        f'R$ {float(multa.valor):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.'),
    ]
    if prazo:
        detalhes.append(f'prazo: {prazo.strftime("%d/%m/%Y")}')
    mensagem = ' • '.join(detalhes)[:1000]
    destinatarios = _destinatarios(veiculo)
    for usuario_id in destinatarios:
        db.session.add(Notificacao(
            usuario_destino_id=usuario_id,
            tipo='frota_multa_nova',
            titulo='Nova infração na frota',
            mensagem=mensagem,
            obra_id=veiculo.obra_id,
            item_id=multa.id,
            item_type='frota_multa',
        ))
    return destinatarios, mensagem


def _notificar_apos_commit(destinatarios, mensagem):
    for usuario_id in destinatarios:
        try:
            telegram_service.notificar_usuario(
                usuario_id, 'Nova infração na frota', mensagem, tipo='frota_multa_nova'
            )
        except Exception:
            # O registro interno é a fonte de verdade. Uma preferência/queda do
            # Telegram não pode fazer o webhook falhar ou ser reenviado.
            logger.exception('e-Frotas: não foi possível espelhar alerta no Telegram')


def _codigo_alteracao(payload):
    if not isinstance(payload, dict):
        return None
    fontes = (
        payload,
        payload.get('infracao') if isinstance(payload.get('infracao'), dict) else {},
        payload.get('dados') if isinstance(payload.get('dados'), dict) else {},
    )
    for fonte in fontes:
        valor = fonte.get('codigoAlteracao') or fonte.get('codigo_alteracao')
        if valor is not None and str(valor).strip():
            return str(valor).strip().replace('-', '_')[:60]
    return None


def _descricao_alteracao(payload):
    if not isinstance(payload, dict):
        return None
    for fonte in (payload, payload.get('infracao'), payload.get('dados')):
        if isinstance(fonte, dict):
            texto = fonte.get('descricaoAlteracao') or fonte.get('motivo')
            if texto:
                return _texto(texto)
    return None


def _situacao_por_alteracao(codigo):
    """Mapeia os códigos publicados pelo SERPRO sem misturar com pagamento."""
    if not codigo:
        return {}
    if codigo == '414_1':
        return {'status_pagamento': 'paga'}
    if codigo == '414_2':
        return {'status_pagamento': 'pendente'}
    if codigo in {'416_3_1', '416_7_1', '416_20_1'}:
        return {'situacao_efrotas': 'cancelada'}
    if codigo == '416_6_1':
        return {'situacao_efrotas': 'suspensa'}
    # Nem todo código terminado em _2 reativa a multa: 416_8_2 e 416_21_2
    # anulam um registro de reativação, cujo estado correto vem no payload
    # (indicadorCanceladaSuspensa) ou no histórico já persistido.
    if codigo in {'416_3_2', '416_6_2', '416_7_2', '416_8_1', '416_20_2', '416_21_1'}:
        return {'situacao_efrotas': 'ativa'}
    return {}


def _atribuir(multa, campo, valor):
    if valor is None or getattr(multa, campo) == valor:
        return False
    setattr(multa, campo, valor)
    return True


def _aplicar_dados_oficiais(multa, dados, payload):
    """Atualiza somente informações que vieram da fonte oficial."""
    alterado = False
    for campo in (
        'data_infracao', 'descricao', 'valor', 'pontos', 'codigo_orgao_autuador',
        'numero_auto_infracao', 'codigo_infracao', 'gravidade', 'local_autuacao',
        'municipio_autuacao', 'uf_autuacao', 'data_notificacao_autuacao',
        'data_notificacao_penalidade', 'data_limite_defesa',
        'data_vencimento_penalidade', 'data_pagamento',
    ):
        alterado = _atribuir(multa, campo, dados.get(campo)) or alterado

    tipo = str(dados.get('tipo_evento') or '').strip()
    if tipo == '1':
        alterado = _atribuir(multa, 'fase_efrotas', 'autuacao') or alterado
    elif tipo == '2' or dados.get('data_notificacao_penalidade'):
        alterado = _atribuir(multa, 'fase_efrotas', 'penalidade') or alterado
    if not multa.situacao_efrotas:
        multa.situacao_efrotas = 'ativa'
        alterado = True
    if dados.get('cancelada_suspensa') is True:
        alterado = _atribuir(multa, 'situacao_efrotas', 'cancelada') or alterado
        alterado = _atribuir(
            multa, 'motivo_situacao_efrotas', 'Cancelada/suspensa segundo consulta e-Frotas'
        ) or alterado

    codigo = _codigo_alteracao(payload)
    for campo, valor in _situacao_por_alteracao(codigo).items():
        alterado = _atribuir(multa, campo, valor) or alterado
    if codigo == '414_2' and multa.data_pagamento is not None:
        # O evento cancela o registro indevido de pagamento; manter a data
        # faria a multa aparecer como custo realizado apesar de voltar a
        # pendente.
        multa.data_pagamento = None
        alterado = True
    if codigo:
        motivo = _descricao_alteracao(payload) or f'Alteração e-Frotas {codigo}'
        alterado = _atribuir(multa, 'motivo_situacao_efrotas', motivo) or alterado
    multa.atualizado_efrotas_em = datetime.utcnow()
    return alterado


def _reconciliar_cancelamentos_pendentes(multa, dados):
    """Aplica um evento 75 que chegou antes da infração correspondente.

    O cancelamento físico não volta a aparecer nas consultas do RENAINF. Por
    isso, quando a infração é posteriormente recebida por outro caminho, a
    auditoria pendente precisa ser consumida na mesma transação.
    """
    chave = {
        'codigo_orgao_autuador': dados['codigo_orgao_autuador'],
        'numero_auto_infracao': dados['numero_auto_infracao'],
        'codigo_infracao': dados['codigo_infracao'],
    }
    pendentes = (FrotaEFrotasEvento.query
                 .filter(FrotaEFrotasEvento.tipo_evento == '75',
                         FrotaEFrotasEvento.status == 'pendente')
                 .all())
    aplicado = False
    for evento in pendentes:
        if identificadores_infracao(evento.payload) != chave:
            continue
        multa.situacao_efrotas = 'cancelada'
        multa.motivo_situacao_efrotas = (
            _descricao_alteracao(evento.payload) or 'Cancelada no e-Frotas'
        )
        multa.atualizado_efrotas_em = datetime.utcnow()
        evento.status = 'processado'
        evento.erro = None
        evento.multa_id = multa.id
        evento.processado_em = datetime.utcnow()
        aplicado = True
    return aplicado


def _buscar_multa_oficial(veiculo_id, dados):
    return FrotaMulta.query.filter_by(
        veiculo_id=veiculo_id,
        codigo_orgao_autuador=dados['codigo_orgao_autuador'],
        numero_auto_infracao=dados['numero_auto_infracao'],
        codigo_infracao=dados['codigo_infracao'],
    ).first()


def _processar_cancelamento(payload, *, id_rastreamento, tipo_evento):
    chave = identificadores_infracao(payload)
    if not all(chave.values()):
        _evento_auditoria(
            payload, id_rastreamento=id_rastreamento, tipo_evento=tipo_evento,
            status='erro', erro='Cancelamento sem a chave oficial da infração.',
        )
        db.session.commit()
        return {'situacao': 'erro', 'motivo': 'Cancelamento sem a chave oficial da infração.'}
    multa = FrotaMulta.query.filter_by(**chave).first()
    if not multa:
        _evento_auditoria(
            payload, id_rastreamento=id_rastreamento, tipo_evento=tipo_evento,
            status='pendente', erro='Cancelamento recebido antes da infração correspondente.',
        )
        db.session.commit()
        return {'situacao': 'pendente', 'motivo': 'A infração ainda não existe no Obraly.'}
    multa.situacao_efrotas = 'cancelada'
    multa.motivo_situacao_efrotas = _descricao_alteracao(payload) or 'Cancelada no e-Frotas'
    multa.atualizado_efrotas_em = datetime.utcnow()
    _evento_auditoria(
        payload, id_rastreamento=id_rastreamento, tipo_evento=tipo_evento,
        status='processado', multa_id=multa.id,
    )
    db.session.commit()
    return {'situacao': 'atualizada', 'multa_id': multa.id}


def processar_evento_efrotas(
    payload, *, notificar=False, atribuir_condutor=False, placa_consultada=None,
):
    """Upsert transacional, seguro para retries do webhook e cargas históricas.

    ``atribuir_condutor`` só é apropriado para um callback novo que chegou no
    momento da infração. Backfill deve mantê-lo vazio para evitar apontar uma
    multa antiga ao motorista atualmente escalado. Na consulta histórica, a
    placa requisitada é a referência de veículo: o schema do fornecedor a
    documenta no item, mas não a declara como obrigatória.
    """
    id_rastreamento, tipo_evento = _metadados(payload)
    try:
        anterior = None
        if id_rastreamento:
            anterior = FrotaEFrotasEvento.query.filter_by(id_rastreamento=id_rastreamento).first()
            if anterior and anterior.status == 'processado':
                return {'situacao': 'duplicada', 'multa_id': anterior.multa_id}

        if str(tipo_evento or '') == '75':
            return _processar_cancelamento(
                payload, id_rastreamento=id_rastreamento, tipo_evento=tipo_evento,
            )

        payload_infracao = payload
        if placa_consultada:
            placa_normalizada = normalizar_placa(placa_consultada)
            if not placa_normalizada:
                raise EventoEFrotasInvalido('Placa da consulta histórica inválida.')
            # A placa não é required no schema do InfracaoDTO. Incluímos a
            # placa da própria requisição antes do parser validar os campos;
            # depois ela também prevalece caso a resposta viesse divergente.
            payload_infracao = dict(payload) if isinstance(payload, dict) else payload
            if isinstance(payload_infracao, dict):
                payload_infracao['placa'] = placa_normalizada
        dados = interpretar_evento_infracao(payload_infracao)
        if placa_consultada:
            dados['placa'] = placa_normalizada
        veiculo = FrotaVeiculo.query.filter(
            db.func.upper(FrotaVeiculo.placa) == normalizar_placa(dados['placa'])
        ).first()
        if not veiculo:
            _evento_auditoria(
                payload, id_rastreamento=id_rastreamento, tipo_evento=tipo_evento,
                status='pendente', erro=f"Veículo da placa {dados['placa']} não está cadastrado no Obraly.",
            )
            db.session.commit()
            return {'situacao': 'pendente', 'motivo': 'Veículo não cadastrado.'}

        multa = _buscar_multa_oficial(veiculo.id, dados)
        criada = multa is None
        if criada:
            multa = FrotaMulta(
                veiculo_id=veiculo.id,
                condutor_id=veiculo.condutor_atual_id if atribuir_condutor else None,
                origem='efrotas',
                status_pagamento='pendente',
            )
            db.session.add(multa)
        alterada = _aplicar_dados_oficiais(multa, dados, payload)
        db.session.flush()
        alterada = _reconciliar_cancelamentos_pendentes(multa, dados) or alterada
        # A consulta retrospectiva não traz idRastreamento. Nesse caso a
        # sincronização já é auditada na tabela própria; não criamos uma linha
        # de evento sem chave a cada execução repetida.
        if id_rastreamento or criada:
            _evento_auditoria(
                payload, id_rastreamento=id_rastreamento, tipo_evento=tipo_evento,
                status='processado', multa_id=multa.id,
            )
        destinatarios, mensagem = ([], None)
        if criada and notificar:
            destinatarios, mensagem = _criar_notificacoes(multa, veiculo)
        db.session.commit()
        if destinatarios:
            _notificar_apos_commit(destinatarios, mensagem)
        return {
            'situacao': 'importada' if criada else ('atualizada' if alterada else 'duplicada'),
            'multa_id': multa.id,
        }
    except EventoEFrotasInvalido as exc:
        db.session.rollback()
        try:
            _evento_auditoria(
                payload, id_rastreamento=id_rastreamento, tipo_evento=tipo_evento,
                status='erro', erro=str(exc),
            )
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception('e-Frotas: não foi possível auditar evento inválido')
        return {'situacao': 'erro', 'motivo': str(exc)}
    except IntegrityError:
        db.session.rollback()
        # Dois callbacks iguais podem disputar o mesmo índice único. A multa
        # e o evento que venceram a corrida são a fonte de verdade.
        return {'situacao': 'duplicada'}
    except Exception:
        db.session.rollback()
        logger.exception('e-Frotas: falha ao persistir evento')
        raise
