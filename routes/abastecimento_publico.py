"""Rota pública do abastecimento — a página que o motorista abre pelo link.

SEM JWT por design: o motorista não tem conta no sistema. O controle de acesso
é o token do link (24 bytes de `secrets.token_urlsafe`) somado à validade
curta, ao rate limit e ao teto de tentativas de leitura do comprovante.

Fica em blueprint próprio, fora de `frota_bp`, justamente para não depender de
uma exceção dentro do `before_request` que protege todo o módulo Frota — um
bypass mal escrito ali abriria a Frota inteira.

O que a página NUNCA expõe: ids internos, custos de outros abastecimentos,
dados de outros veículos. Só o veículo do link e o que o próprio motorista
digitou.
"""
import logging
from datetime import datetime, date

from flask import Blueprint, jsonify, request

from extensions import db, limiter
from models.frota_abastecimento_solicitacao import FrotaAbastecimentoSolicitacao
from models.frota_veiculo import FrotaVeiculo
from services import storage_service, abastecimento_service
from services import recibo_abastecimento_service

logger = logging.getLogger(__name__)

abastecimento_publico_bp = Blueprint(
    'abastecimento_publico', __name__, url_prefix='/abastecimento',
)

BUCKET_FROTA = 'frota-arquivos'

# Cada leitura de comprovante custa uma chamada paga. O teto por solicitação
# limita o estrago caso um link vaze; o rate limit por IP cobre o resto.
_MAX_OCR_TENTATIVAS = 6
_KM_MAX = 9_999_999
# Folga sobre o teto de 8 MB do serviço de leitura: o corte aqui é pelo header,
# antes de materializar o corpo do request.
_MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _to_num(valor):
    if valor is None or valor == '':
        return None
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        return float(valor)
    s = str(valor).strip().replace('R$', '').strip()
    if ',' in s and '.' in s:
        s = s.replace('.', '').replace(',', '.')
    elif ',' in s:
        s = s.replace(',', '.')
    try:
        return float(s)
    except Exception:
        return None


def _to_int(valor):
    if valor is None or valor == '':
        return None
    try:
        return int(float(str(valor).replace('.', '').replace(',', '.')))
    except (TypeError, ValueError):
        return None


def _parse_date(valor):
    if not valor:
        return None
    if isinstance(valor, date):
        return valor
    try:
        return datetime.fromisoformat(str(valor)[:10]).date()
    except Exception:
        return None


def _buscar(token):
    """(solicitação, erro_response). Erro já pronto para `return`."""
    sol = FrotaAbastecimentoSolicitacao.query.filter_by(token=token).first()
    if not sol:
        return None, (jsonify({"erro": "Link não encontrado."}), 404)
    return sol, None


def _bloqueio_de_envio(sol):
    """Motivo pelo qual o link não aceita mais envio, ou None."""
    if sol.status == 'concluida':
        return "Este abastecimento já foi registrado."
    if sol.status == 'cancelada':
        return "Este link foi cancelado pelo responsável."
    if sol.is_expirado():
        return "Este link expirou. Peça um novo ao responsável."
    return None


@abastecimento_publico_bp.route('/<token>', methods=['GET'])
@limiter.limit("60 per hour")
def obter_abastecimento_publico(token):
    """Dados do veículo e da autorização para montar o formulário."""
    try:
        sol, erro = _buscar(token)
        if erro:
            return erro
        return jsonify(sol.to_dict_publico()), 200
    except Exception:
        logger.exception("Erro em GET /abastecimento/<token>")
        return jsonify({"erro": "Erro ao carregar o link."}), 500


@abastecimento_publico_bp.route('/<token>/comprovante', methods=['POST'])
@limiter.limit("20 per hour")
def ler_comprovante(token):
    """Sobe o comprovante e devolve os dados reconhecidos no cupom.

    Não grava o abastecimento — o motorista confere e corrige antes de enviar.
    O arquivo já fica no Storage aqui: se a leitura falhar, o comprovante não
    se perde e o envio segue com os campos preenchidos à mão.
    """
    try:
        sol, erro = _buscar(token)
        if erro:
            return erro
        bloqueio = _bloqueio_de_envio(sol)
        if bloqueio:
            return jsonify({"erro": bloqueio}), 400
        if sol.ocr_tentativas >= _MAX_OCR_TENTATIVAS:
            return jsonify({
                "erro": "Limite de leituras deste comprovante atingido. "
                        "Preencha os valores manualmente e envie.",
            }), 429

        # Recusa o upload gigante pelo header, antes de gastar disco/memória
        # com o corpo — a rota é pública e o cupom é foto de celular.
        if (request.content_length or 0) > _MAX_UPLOAD_BYTES:
            return jsonify({
                "erro": "Arquivo muito grande (máximo 10 MB). "
                        "Tire a foto com resolução menor.",
            }), 413

        arquivo = request.files.get('arquivo') or request.files.get('file')
        if not arquivo:
            return jsonify({"erro": "Anexe a foto ou o PDF do comprovante."}), 400

        try:
            dados_bytes = recibo_abastecimento_service.ler_bytes(arquivo)
            tipo = recibo_abastecimento_service.media_type(arquivo)
            comprimido, media_final = recibo_abastecimento_service.comprimir_imagem(
                dados_bytes, tipo,
            )
        except Exception:
            logger.exception("Abastecimento público: falha ao ler o upload")
            return jsonify({"erro": "Não foi possível ler o arquivo enviado."}), 400

        # Sobe primeiro: o comprovante é a prova da despesa e não pode
        # depender do sucesso da leitura automática.
        comprovante_url, upload_falhou = None, False
        try:
            comprovante_url = storage_service.upload_arquivo(
                _Upload(comprimido, media_final, getattr(arquivo, 'filename', 'comprovante')),
                f'abastecimentos/{sol.id}', bucket=BUCKET_FROTA,
            )
        except Exception as e:
            upload_falhou = True
            logger.exception("Abastecimento público: upload do comprovante falhou: %s", e)

        sol.ocr_tentativas = (sol.ocr_tentativas or 0) + 1
        if comprovante_url:
            sol.comprovante_url = comprovante_url

        reconhecido, aviso = {}, None
        try:
            reconhecido = recibo_abastecimento_service.extrair_dados_recibo(
                _Upload(comprimido, media_final, getattr(arquivo, 'filename', 'comprovante')),
            )
            sol.ocr_status = 'ok'
            sol.ocr_dados = reconhecido
        except ValueError as e:
            sol.ocr_status = 'falhou'
            db.session.commit()
            return jsonify({"erro": str(e)}), 400
        except Exception as e:
            sol.ocr_status = 'falhou'
            logger.exception("Abastecimento público: leitura do comprovante falhou: %s", e)
            aviso = ("Não conseguimos ler o comprovante automaticamente. "
                     "Confira e preencha os valores à mão.")

        # Coerência: cupom borrado erra dígito. Se litros × preço não bate com
        # o total, o motorista revisa em vez de enviar número errado.
        litros = reconhecido.get('litros')
        preco = reconhecido.get('preco_litro')
        total = reconhecido.get('valor_total')
        if litros and preco and total:
            if abs(litros * preco - total) > max(0.5, total * 0.02):
                aviso = ("Os valores lidos não fecham (litros × preço ≠ total). "
                         "Confira antes de enviar.")
        elif litros and preco and not total:
            reconhecido['valor_total'] = round(litros * preco, 2)
        elif total and litros and not preco:
            reconhecido['preco_litro'] = round(total / litros, 3)

        db.session.commit()
        out = {
            'dados': reconhecido,
            'comprovante_recebido': bool(comprovante_url),
            'ocr_status': sol.ocr_status,
            'tentativas_restantes': max(0, _MAX_OCR_TENTATIVAS - sol.ocr_tentativas),
        }
        if upload_falhou:
            out['aviso'] = ("O comprovante não pôde ser anexado. "
                            "Você ainda pode enviar os dados do abastecimento.")
        elif aviso:
            out['aviso'] = aviso
        return jsonify(out), 200
    except Exception:
        db.session.rollback()
        logger.exception("Erro em POST /abastecimento/<token>/comprovante")
        return jsonify({"erro": "Erro ao processar o comprovante."}), 500


@abastecimento_publico_bp.route('/<token>', methods=['POST'])
@limiter.limit("20 per hour")
def enviar_abastecimento(token):
    """Envio final do motorista — cria o abastecimento e fecha o link."""
    try:
        sol, erro = _buscar(token)
        if erro:
            return erro
        bloqueio = _bloqueio_de_envio(sol)
        if bloqueio:
            return jsonify({"erro": bloqueio}), 400

        veiculo = db.session.get(FrotaVeiculo, sol.veiculo_id)
        if not veiculo:
            return jsonify({"erro": "Veículo não encontrado."}), 400

        dados = request.get_json(silent=True) or {}
        km = _to_int(dados.get('km'))
        if not km or km <= 0 or km > _KM_MAX:
            return jsonify({"erro": "Informe o KM atual do painel."}), 400
        if veiculo.km_atual and km < veiculo.km_atual:
            return jsonify({
                "erro": f"KM informado ({km}) é menor que o último registrado "
                        f"({veiculo.km_atual}). Confira o painel.",
            }), 400

        litros = _to_num(dados.get('litros'))
        if not litros or litros <= 0 or litros > 2000:
            return jsonify({"erro": "Informe quantos litros foram abastecidos."}), 400

        valor_total = _to_num(dados.get('valor_total'))
        preco_litro = _to_num(dados.get('preco_litro'))
        if (not valor_total or valor_total <= 0) and preco_litro and preco_litro > 0:
            valor_total = round(litros * preco_litro, 2)
        if not valor_total or valor_total <= 0:
            return jsonify({"erro": "Informe o valor total do abastecimento."}), 400
        if not preco_litro or preco_litro <= 0:
            preco_litro = round(valor_total / litros, 3)
        if sol.limite_valor is not None and valor_total > float(sol.limite_valor):
            return jsonify({
                "erro": f"Valor acima do limite autorizado "
                        f"(R$ {float(sol.limite_valor):.2f}). Fale com o responsável.",
            }), 400

        data_abast = _parse_date(dados.get('data')) or date.today()
        if data_abast > date.today():
            return jsonify({"erro": "A data do abastecimento não pode ser futura."}), 400

        try:
            sol.km = km
            sol.litros = litros
            sol.preco_litro = preco_litro
            sol.valor_total = valor_total
            sol.posto = (dados.get('posto') or '').strip()[:160] or None
            sol.data_abastecimento = data_abast
            sol.combustivel = (dados.get('combustivel') or sol.combustivel or None)
            sol.observacao_motorista = (dados.get('observacao') or '').strip()[:300] or None

            abast = abastecimento_service.registrar_abastecimento(
                veiculo,
                {
                    'data': data_abast,
                    'valor': valor_total,
                    'litros': litros,
                    'km': km,
                    'preco_litro': preco_litro,
                    'combustivel': sol.combustivel,
                    'posto': sol.posto,
                    'condutor_id': sol.condutor_id,
                    'observacao': sol.observacao_motorista,
                },
                origem='superlink',
                solicitacao_id=sol.id,
                comprovante_url=sol.comprovante_url,
            )
            db.session.flush()  # garante abast.id antes do commit

            sol.abastecimento_id = abast.id
            sol.status = 'concluida'
            sol.enviado_em = datetime.utcnow()
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.exception("Abastecimento público: erro ao gravar: %s", e)
            return jsonify({"erro": "Erro ao registrar o abastecimento."}), 500

        return jsonify({
            'ok': True,
            'mensagem': 'Abastecimento registrado. Obrigado!',
            'resumo': {
                'veiculo_placa': veiculo.placa,
                'km': km,
                'litros': litros,
                'preco_litro': preco_litro,
                'valor_total': valor_total,
                'posto': sol.posto,
                'data': data_abast.isoformat(),
                'comprovante_recebido': bool(sol.comprovante_url),
            },
        }), 201
    except Exception:
        db.session.rollback()
        logger.exception("Erro em POST /abastecimento/<token>")
        return jsonify({"erro": "Erro ao registrar o abastecimento."}), 500


class _Upload:
    """Adapta bytes já lidos à interface que o storage_service espera
    (`read`/`seek`/`filename`/`mimetype`), para não reler o FileStorage
    original depois da compressão."""

    def __init__(self, dados, mimetype, filename):
        self._dados = dados
        self.mimetype = mimetype
        self.filename = filename or 'comprovante'
        if mimetype == 'image/jpeg' and not self.filename.lower().endswith(('.jpg', '.jpeg')):
            self.filename = f'{self.filename.rsplit(".", 1)[0]}.jpg'

    def read(self):
        return self._dados

    def seek(self, *_args):
        return None
